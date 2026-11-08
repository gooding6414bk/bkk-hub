"""대본 → AI 음성(edge-tts) + 자막(srt). 장면마다 따로 만들어 길이를 정확히 재고, 장면 사이에 쉼을 넣어 잇는다.

    python gibalhae/pipeline/tts.py 2026-10-24                 voices.json 의 default 목소리
    python gibalhae/pipeline/tts.py 2026-10-24 --voice sunhi   다른 목소리(비교용)

결과: content/날짜/build/
    narration.mp3        전체 음성
    narration.srt        전체 자막(장면 오프셋 반영)
    timing.json          장면별 시작·끝 초 — render.py 가 읽는다
    scene_NN.mp3/.srt    장면별 원본

인터넷이 필요하다(마이크로소프트 음성 서버). 회사 프록시 뒤라면 HTTPS_PROXY 를 그대로 쓰고,
자체 서명 CA 는 SSL_CERT_FILE 로 넘긴다(aiohttp 는 certifi 만 보므로 아래에서 합쳐 준다).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import build_dir, day_dir, load_voices, parse_scenes, read_doc  # noqa: E402


def _patch_ssl() -> None:
    """edge_tts 는 certifi 묶음만 믿는다. SSL_CERT_FILE 이 있으면 그 CA 도 같이 믿게 한다."""
    extra = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not extra or not os.path.exists(extra):
        return
    import edge_tts.communicate as c  # noqa: WPS433
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    ctx.load_verify_locations(extra)
    c._SSL_CTX = ctx


def srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        a = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        b = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 1000
        out.append((a, b, " ".join(lines[2:]).strip()))
    return out


def write_srt(cues: list[tuple[float, float, str]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, t) in enumerate(cues, 1):
            f.write(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n")


def media_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


async def _speak(text: str, voice: dict, mp3: str, srt: str) -> list[tuple[float, float, str]]:
    """음성 파일과 문장 자막을 쓰고, 어절별 (시작, 끝, 글자) 를 돌려준다 — 자막에서 읽는 단어를 켜는 데 쓴다."""
    import edge_tts
    com = edge_tts.Communicate(text, voice["voice"], rate=voice.get("rate", "+0%"), pitch=voice.get("pitch", "+0Hz"),
                               boundary="WordBoundary", proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    sub = edge_tts.SubMaker()
    words: list[tuple[float, float, str]] = []
    with open(mp3, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                sub.feed(chunk)
                a = chunk["offset"] / 1e7
                words.append((round(a, 3), round(a + chunk["duration"] / 1e7, 3), chunk["text"]))
            elif chunk["type"] == "SentenceBoundary":
                sub.feed(chunk)
    with open(srt, "w", encoding="utf-8") as f:
        f.write(sub.get_srt())
    return words


def synthesize(date: str, voice_key: str | None = None) -> dict:
    _patch_ssl()
    voices = load_voices()
    key = voice_key or voices["default"]
    voice = voices[key]
    gap = float(voice.get("gap", 0.45))
    meta, body = read_doc(os.path.join(day_dir(date), "shorts.md"))
    scenes = parse_scenes(body)
    out = build_dir(date)

    timing, cues, parts = [], [], []
    t = 0.0
    for s in scenes:
        mp3 = os.path.join(out, f"scene_{s.idx:02d}.mp3")
        srt = os.path.join(out, f"scene_{s.idx:02d}.srt")
        words = asyncio.run(_speak(s.text, voice, mp3, srt))
        dur = media_duration(mp3)
        with open(srt, encoding="utf-8") as f:
            local = parse_srt(f.read())
        if not local:                                   # 자막이 안 왔으면 장면 통째로 한 줄
            local = [(0.0, dur, s.text)]
        for a, b, txt in local:
            cues.append((t + a, min(t + b, t + dur), txt))
        timing.append({"idx": s.idx, "kind": s.kind, "args": s.args, "start": round(t, 3), "end": round(t + dur, 3),
                       "text": s.text, "words": [(round(t + a, 3), round(min(t + b, t + dur), 3), w) for a, b, w in words]})
        parts.append((mp3, dur))
        t += dur + gap
    total = t - gap

    # 장면 음성을 쉼(무음)과 함께 잇는다
    concat = os.path.join(out, "concat.txt")
    silence = os.path.join(out, "gap.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{gap:.3f}",
                    "-c:a", "libmp3lame", "-q:a", "9", silence], check=True)
    with open(concat, "w", encoding="utf-8") as f:
        for i, (mp3, _) in enumerate(parts):
            if i:
                f.write(f"file '{silence}'\n")
            f.write(f"file '{mp3}'\n")
    narration = os.path.join(out, "narration.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat,
                    "-c:a", "libmp3lame", "-q:a", "3", narration], check=True)
    write_srt(cues, os.path.join(out, "narration.srt"))
    info = {"voice": key, "gap": gap, "total": round(media_duration(narration), 3), "estimated_total": round(total, 3),
            "scenes": timing}
    with open(os.path.join(out, "timing.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return info


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    voice = None
    if "--voice" in argv:
        voice = argv[argv.index("--voice") + 1]
    info = synthesize(argv[0], voice)
    print(f"음성 {info['voice']} · {info['total']:.1f}초 · 장면 {len(info['scenes'])}개 → {build_dir(argv[0])}/narration.mp3")
    if info["total"] > 59.5:
        print("WARN 60초를 넘는다 — 쇼츠로 안 잡힐 수 있다. 대본을 줄인다")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
