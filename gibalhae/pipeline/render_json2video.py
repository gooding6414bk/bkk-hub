"""대본 → JSON2Video 로 렌더 (음성·자막·배경·장면 카드까지 API 하나). 결과 mp4 는 build/shorts.mp4.

    python gibalhae/pipeline/render_json2video.py 2026-10-24                  voices.json 의 json2video.default 목소리
    python gibalhae/pipeline/render_json2video.py 2026-10-24 --voice sunhi    다른 목소리(voices.json json2video 항목 이름)
    python gibalhae/pipeline/render_json2video.py 2026-10-24 --dry-run        보낼 movie.json 만 만들고 보내지 않음
    python gibalhae/pipeline/render_json2video.py 2026-10-24 --project ID     이미 보낸 작업의 결과만 받기

키: 클라우드 세션에서는 프록시가 x-api-key 를 넣어 준다(환경 비밀값). 내 PC 에서는 환경변수 JSON2VIDEO_API_KEY.
배경: build/background.json 에 {"url": "...mp4"} 가 있으면(fetch_background.py --remote) 그 영상을 어둡게 깔고, 없으면 그라데이션.
비용: 1초 1크레딧 (무료 600 = 쇼츠 10편, 워터마크·비상업). 음성(Azure·ElevenLabs)·자막은 0.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import html_scenes as HS  # noqa: E402
from pipeline import keys  # noqa: E402
from pipeline.common import build_dir, day_dir, load_voices, parse_scenes, read_doc  # noqa: E402

API = "https://api.json2video.com/v2/movies"
POLL_EVERY = 6
POLL_MAX = 900


def api_key() -> str:
    # 대시보드 「연결」 탭(instance) → 환경변수 → 클라우드 프록시 주입(값은 자리표시면 된다)
    return keys.get("json2video") or "proxy-injected"


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"x-api-key": api_key(), "Content-Type": "application/json", "User-Agent": "gibalhae-pipeline"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"JSON2Video {e.code}: {e.read().decode(errors='replace')[:600]}")


def voice_spec(key: str | None) -> dict:
    v = load_voices()["json2video"]
    k = key or v["default"]
    return dict(v[k], key=k)


def build_movie(date: str, voice_key: str | None = None) -> dict:
    meta, body = read_doc(os.path.join(day_dir(date), "shorts.md"))
    scenes = parse_scenes(body)
    title = str(meta.get("title", ""))
    v = voice_spec(voice_key)
    bg_path = os.path.join(build_dir(date), "background.json")
    bg_url = None
    if os.path.exists(bg_path):
        with open(bg_path, encoding="utf-8") as f:
            bg_url = json.load(f).get("url")

    out_scenes = []
    for s in scenes:
        els: list[dict] = []
        if bg_url:
            els.append({"type": "video", "src": bg_url, "resize": "cover", "position": "center-center", "muted": True,
                        "duration": -2, "correction": {"brightness": -0.15, "saturation": 0.6},
                        "keyframes": [{"time": 0, "zoom": 0}, {"time": "100%", "zoom": 2 if s.idx % 2 == 0 else -1, "easing": "linear"}]})
            els.append({"type": "html", "html": HS.dim_html(), "duration": -2})
        else:
            els.append({"type": "html", "html": HS.background_html(), "duration": -2})
        els.append({"type": "html", "html": HS.scene_html(s.kind, s.args, title), "duration": -2, "fade-in": 0.35, "wait": 1.2,
                    "comment": f"{s.kind}: {s.args}"})
        voice = {"type": "voice", "text": s.text, "voice": v["voice"], "extra-time": float(v.get("gap", 0.45))}
        if v.get("model"):
            voice["model"] = v["model"]
        if v.get("speed") and float(v["speed"]) != 1:
            voice["speed"] = float(v["speed"])
        els.append(voice)
        sc = {"comment": f"장면 {s.idx + 1} {s.kind}", "duration": -1, "background-color": "#1e1b1a", "elements": els}
        if s.idx:
            sc["transition"] = {"style": "fade", "duration": 0.4}
        out_scenes.append(sc)

    movie = {
        "comment": f"기발해 {date} {title}",
        "resolution": "custom", "width": 1080, "height": 1920, "quality": "high",
        "client-data": {"date": date, "title": title, "voice": v["key"]},
        "elements": [
            {"type": "subtitles", "language": "ko", "model": "default",
             "settings": {"style": "classic", "font-family": "Noto Sans KR", "font-weight": "900", "font-size": 62,
                          "word-color": "#ff3b30", "line-color": "#FFFFFF", "outline-color": "#141413", "outline-width": 7,
                          "shadow-color": "#000000", "shadow-offset": 2, "max-words-per-line": 4,
                          "position": "custom", "x": 540, "y": 1560}},
        ],
        "scenes": out_scenes,
    }
    return movie


def submit(movie: dict) -> str:
    r = _req("POST", API, movie)
    if not r.get("success"):
        raise SystemExit(f"JSON2Video 거부: {r}")
    return r["project"]


def wait(project: str) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < POLL_MAX:
        r = _req("GET", f"{API}?project={project}&format=simple")
        m = r.get("movie") or {}
        st = m.get("status", "?")
        if st != last:
            print(f"  {st} {m.get('message', '')}".rstrip())
            last = st
        if st == "done":
            m["remaining_quota"] = r.get("remaining_quota")
            return m
        if st in ("error", "timeout"):
            raise SystemExit(f"JSON2Video 실패: {m.get('message')}")
        time.sleep(POLL_EVERY)
    raise SystemExit("JSON2Video 15분 안에 끝나지 않음")


def download(url: str, path: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "gibalhae-pipeline"})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return path


def render(date: str, voice_key: str | None = None, dry: bool = False, project: str | None = None) -> dict:
    out = build_dir(date)
    if not project:
        movie = build_movie(date, voice_key)
        with open(os.path.join(out, "movie.json"), "w", encoding="utf-8") as f:
            json.dump(movie, f, ensure_ascii=False, indent=1)
        chars = sum(len(s["elements"][-1]["text"]) for s in movie["scenes"])
        print(f"movie.json: 장면 {len(movie['scenes'])}개 · 나레이션 {chars}자 · 목소리 {movie['client-data']['voice']}")
        if dry:
            return {"dry": True, "path": os.path.join(out, "movie.json")}
        project = submit(movie)
        print(f"보냄: project {project}")
    m = wait(project)
    mp4 = download(m["url"], os.path.join(out, "shorts.mp4"))
    info = {"project": project, "url": m["url"], "ass": m.get("ass"), "thumbnail": m.get("thumbnail"), "duration": m.get("duration"),
            "rendering_time": m.get("rendering_time"), "remaining_quota": m.get("remaining_quota")}
    with open(os.path.join(out, "json2video.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    if m.get("ass"):
        try:
            download(m["ass"], os.path.join(out, "narration.ass"))
        except Exception:
            pass
    print(f"{mp4} · {m.get('duration')}초 · 남은 크레딧 {(m.get('remaining_quota') or {}).get('time')}")
    return info


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    voice = argv[argv.index("--voice") + 1] if "--voice" in argv else None
    project = argv[argv.index("--project") + 1] if "--project" in argv else None
    render(argv[0], voice, dry="--dry-run" in argv, project=project)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
