"""장면 PNG + 음성 + 자막 → 1080×1920 mp4 (쇼츠).

    python gibalhae/pipeline/render.py 2026-10-24
    python gibalhae/pipeline/render.py 2026-10-24 --no-subs      자막 없이(검수용)
    python gibalhae/pipeline/render.py 2026-10-24 --still        움직임 없이(빠른 확인용)

먼저 tts.py(음성·timing.json)와 graphics.py(scene_NN.png)가 돌아 있어야 한다. daily.py build 가 순서대로 부른다.

보이는 것:
  - 장면마다 카메라가 천천히 들어가거나 나온다(줌 6%), 장면 사이는 0.35초 페이드.
  - 자막은 어절 단위로 **지금 읽는 단어만 붉게** 켜진다(edge-tts 어절 타이밍). 한 줄 14자 안, 두 줄.
  - build/background.mp4 가 있으면 어둡게 깔고 그 위에 그래픽을 얹는다(fetch_background.py 가 Pexels 에서 받아 둔다).
결과: content/날짜/build/shorts.mp4
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import FONTS, build_dir  # noqa: E402

FPS = 30
XFADE = 0.35                   # 장면 전환 페이드(초)
ZOOM = 0.06                    # 장면 안 줌 비율
LINE_CHARS = 14                # 자막 한 줄 글자 수(공백 포함) — 두 줄까지
SUB_TAIL = 0.25                # 마지막 단어 뒤 자막이 남는 시간

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Noto Sans KR,68,&H00FFFFFF,&H00FFFFFF,&H00141413,&H80000000,-1,0,0,0,100,100,0,0,1,5,0,2,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
HI = r"{\c&H0A00C7&\3c&HFFFFFF&\bord4}"        # 읽는 단어: 붉은 글자(#c7000a → BGR) + 흰 테두리
HI_END = r"{\r}"


def _esc(path: str) -> str:
    """ffmpeg 필터 인자용 경로 이스케이프."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ass_time(sec: float) -> str:
    cs = int(round(max(sec, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(t: str) -> str:
    return t.replace("\\", "/").replace("{", "(").replace("}", ")")


def group_words(words: list, scene_end: float) -> list[dict]:
    """어절들을 자막 한 장(두 줄 안)씩 묶는다. 문장 끝(. ? !)에서는 끊는다."""
    cues, cur = [], []

    def flush():
        if cur:
            cues.append({"start": cur[0][0], "end": min(cur[-1][1] + SUB_TAIL, scene_end), "words": list(cur)})
            cur.clear()

    for a, b, w in words:
        text_len = sum(len(x[2]) + 1 for x in cur) + len(w)
        if cur and text_len > LINE_CHARS * 2:
            flush()
        cur.append((a, b, w))
        if w.endswith((".", "?", "!")) :
            flush()
    flush()
    # 자막끼리 겹치지 않게
    for i in range(len(cues) - 1):
        cues[i]["end"] = min(cues[i]["end"], cues[i + 1]["start"])
    return cues


def _layout(words: list[str]) -> list[list[int]]:
    """어절 번호를 두 줄로 나눈다(한 줄 LINE_CHARS 안, 넘치면 균형 있게)."""
    total = sum(len(w) for w in words) + len(words) - 1
    if total <= LINE_CHARS or len(words) == 1:
        return [list(range(len(words)))]
    best, best_diff = None, 1e9
    for cut in range(1, len(words)):
        l1 = sum(len(w) for w in words[:cut]) + cut - 1
        l2 = sum(len(w) for w in words[cut:]) + len(words) - cut - 1
        diff = abs(l1 - l2)
        if diff < best_diff:
            best, best_diff = cut, diff
    return [list(range(best)), list(range(best, len(words)))]


def karaoke_ass(timing: dict, ass: str) -> str:
    """timing.json 의 어절 타이밍 → 읽는 단어가 켜지는 ASS 자막."""
    lines = [ASS_HEAD]
    for s in timing["scenes"]:
        words = s.get("words") or []
        if not words:                                      # 어절 타이밍이 없으면 장면 글 통째로
            lines.append(f"Dialogue: 0,{_ass_time(s['start'])},{_ass_time(s['end'])},Sub,,0,0,0,,{_clean(s['text'])}\n")
            continue
        for cue in group_words(words, s["end"]):
            ws = cue["words"]
            rows = _layout([w for _, _, w in ws])
            for k, (a, b, _) in enumerate(ws):
                a0 = a if k else cue["start"]
                b0 = ws[k + 1][0] if k + 1 < len(ws) else cue["end"]
                if b0 <= a0:
                    continue
                row_txt = []
                for row in rows:
                    row_txt.append(" ".join((HI + _clean(ws[i][2]) + HI_END) if i == k else _clean(ws[i][2]) for i in row))
                text = "\\N".join(row_txt)          # f-문자열 안 역슬래시는 파이썬 3.12부터라 밖으로 뺀다 (서버 3.10 호환)
                lines.append(f"Dialogue: 0,{_ass_time(a0)},{_ass_time(b0)},Sub,,0,0,0,,{text}\n")
    with open(ass, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return ass


def srt_to_ass(srt: str, ass: str) -> str:
    """문장 자막(srt) → 화면 크기에 맞춘 ASS (어절 타이밍이 없을 때의 대안)."""
    from pipeline.tts import parse_srt
    with open(srt, encoding="utf-8") as f:
        cues = parse_srt(f.read())
    with open(ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEAD)
        for a, b, t in cues:
            f.write(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Sub,,0,0,0,,{_clean(t)}\n")
    return ass


def _scene_clip(png: str, out: str, dur: float, idx: int, still: bool) -> str:
    frames = max(int(round(dur * FPS)), 2)
    if still:
        vf = f"scale=1080:1920,fps={FPS},format=yuv420p"
    else:
        # 짝수 장면은 들어가고 홀수 장면은 나온다. 입력을 1.5배로 키워 떨림을 줄인다
        if idx % 2 == 0:
            z = f"1+{ZOOM}*on/{frames}"
        else:
            z = f"1+{ZOOM}-{ZOOM}*on/{frames}"
        vf = (f"scale=1620:2880,zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS},"
              "format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", png, "-vf", vf,
                    "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out], check=True)
    return out


def render(date: str, subs: bool = True, still: bool = False) -> str:
    out = build_dir(date)
    with open(os.path.join(out, "timing.json"), encoding="utf-8") as f:
        timing = json.load(f)
    scenes = timing["scenes"]
    total = float(timing["total"])
    n = len(scenes)

    # 1) 장면 클립 — 장면 i 는 start_i 부터 다음 장면 start 까지 + 전환 겹침
    clips = []
    for i, s in enumerate(scenes):
        png = os.path.join(out, f"scene_{s['idx']:02d}.png")
        if not os.path.exists(png):
            raise FileNotFoundError(f"{png} 없음 — graphics.py 먼저")
        nxt = scenes[i + 1]["start"] if i + 1 < n else total + 0.3
        dur = max(0.6, nxt - s["start"]) + (XFADE if i + 1 < n else 0)
        clips.append(_scene_clip(png, os.path.join(out, f"clip_{i:02d}.mp4"), dur, i, still))

    # 2) 전환(xfade) 으로 잇기
    parts = []
    last = "[0:v]"
    for i in range(1, n):
        offset = scenes[i]["start"]
        lab = f"[x{i}]" if i < n - 1 else "[scene]"
        parts.append(f"{last}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}{lab}")
        last = lab
    if n == 1:
        parts.append("[0:v]copy[scene]")
    inputs = []
    for c in clips:
        inputs += ["-i", c]
    narration = os.path.join(out, "narration.mp3")
    inputs += ["-i", narration]
    a_idx = n

    # 3) 배경 클립이 있으면 어둡게 깔고 그래픽을 얹는다 (그래픽의 어두운 바탕을 뚫는다)
    bg = os.path.join(out, "background.mp4")
    chain = "[scene]"
    if os.path.exists(bg):
        inputs += ["-stream_loop", "-1", "-i", bg]
        parts.append(f"[{a_idx + 1}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                     f"eq=brightness=-0.22:saturation=0.55,fps={FPS}[bg]")
        parts.append("[scene]colorkey=0x1e1b1a:0.10:0.06[fg];[bg][fg]overlay=shortest=1[over]")
        chain = "[over]"

    # 4) 자막
    if subs:
        ass = os.path.join(out, "narration.ass")
        if any(s.get("words") for s in scenes):
            karaoke_ass(timing, ass)
        else:
            srt_to_ass(os.path.join(out, "narration.srt"), ass)
        parts.append(f"{chain}ass='{_esc(ass)}':fontsdir='{_esc(FONTS)}'[v]")
    else:
        parts.append(f"{chain}copy[v]")

    mp4 = os.path.join(out, "shorts.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", f"{a_idx}:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-r", str(FPS), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{total + 0.3:.3f}", mp4]
    subprocess.run(cmd, check=True)
    for c in clips:
        os.remove(c)
    return mp4


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    mp4 = render(argv[0], subs="--no-subs" not in argv, still="--still" in argv)
    size = os.path.getsize(mp4) / 1e6
    print(f"{mp4} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
