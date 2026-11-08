"""Pexels 무료 영상에서 배경 클립을 받아 build/background.mp4 로 둔다 (얼굴 없는 사물·공간 클립만 고른다).

    python gibalhae/pipeline/fetch_background.py 2026-10-24                 shorts.md 머리말 background: 검색어 (없으면 시리즈별 기본)
    python gibalhae/pipeline/fetch_background.py 2026-10-24 --query "bedroom dark window"

Pexels API 키(무료, https://www.pexels.com/api/)를 환경변수 PEXELS_API_KEY 에 둔다. 키가 없으면 아무것도 하지 않고 0 으로 끝난다
(배경 없이도 영상은 나온다). 라이선스: Pexels 는 상업 이용 가능, 출처 표기 불필요 — 그래도 build/SOURCES.md 에 적어 둔다.
사람이 나오는 클립을 피하려고 검색어에 'no people' 류를 붙이고, 결과 설명에 person/people/woman/man 이 들어간 건 거른다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import keys  # noqa: E402
from pipeline.common import build_dir, day_dir, read_doc  # noqa: E402

DEFAULT_QUERY = {"A": "empty bedroom interior slow", "B": "calendar desk close up", "C": "korean traditional house roof",
                 "D": "paper texture minimal"}
PEOPLE = ("person", "people", "woman", "man", "girl", "boy", "face", "hand", "couple", "family", "portrait")


def fetch(date: str, query: str | None = None, download: bool = True) -> str | None:
    """고른 클립의 주소를 build/background.json 에 적고(JSON2Video 가 직접 받는다), download 면 build/background.mp4 로도 받는다."""
    key = keys.get("pexels")
    if not key:
        print("Pexels 키 없음(대시보드 연결 탭 또는 PEXELS_API_KEY) — 배경 없이 간다")
        return None
    meta, _ = read_doc(os.path.join(day_dir(date), "shorts.md"))
    q = query or str(meta.get("background") or DEFAULT_QUERY.get(str(meta.get("series", "A")), DEFAULT_QUERY["A"]))
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode({"query": q, "orientation": "portrait", "size": "medium", "per_page": 15})
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "gibalhae-pipeline"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    for v in data.get("videos", []):
        blob = (str(v.get("url", "")) + " " + json.dumps(v.get("user", {}), ensure_ascii=False)).lower()
        if any(p in blob for p in PEOPLE):
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 1280]
        if not files:
            continue
        files.sort(key=lambda f: f.get("height") or 0)
        link = files[0]["link"]
        with open(os.path.join(build_dir(date), "background.json"), "w", encoding="utf-8") as f:
            json.dump({"url": link, "page": v.get("url"), "author": v.get("user", {}).get("name", ""), "query": q}, f, ensure_ascii=False, indent=1)
        with open(os.path.join(build_dir(date), "SOURCES.md"), "a", encoding="utf-8") as f:
            f.write(f"- 배경 ← {v.get('url')} (Pexels, {v.get('user', {}).get('name', '')}) 검색어 '{q}'\n")
        print(f"배경: {v.get('url')}")
        if not download:
            return link
        out = os.path.join(build_dir(date), "background.mp4")
        urllib.request.urlretrieve(link, out)
        return out
    print("맞는 클립이 없다 — 배경 없이 간다")
    return None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    q = argv[argv.index("--query") + 1] if "--query" in argv else None
    fetch(argv[0], q, download="--remote" not in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
