"""하루치 콘텐츠의 상태 기계 — 에이전트와 사람이 같은 명령으로 움직인다.

    python gibalhae/pipeline/daily.py new [--date 2026-10-24] [--topic 3]   오늘 폴더·머리말 틀 만들고 소재를 뽑는다
    python gibalhae/pipeline/daily.py status                                  날짜별 상태 한눈에
    python gibalhae/pipeline/daily.py check 2026-10-24                        금지 표현·길이 검수 (check.py)
    python gibalhae/pipeline/daily.py approve 2026-10-24 [--blog|--shorts]    사람이 승인 → approved
    python gibalhae/pipeline/daily.py reject 2026-10-24 [--blog|--shorts] [--why "..."]
    python gibalhae/pipeline/daily.py build 2026-10-24 [--voice hyunbin]      JSON2Video 로 영상 (approved → built). --engine local 이면 edge-tts+ffmpeg
    python gibalhae/pipeline/daily.py publish 2026-10-24 [--now] [--blog-target naver|wordpress]
    python gibalhae/pipeline/daily.py published 2026-10-24 --blog-url 주소    손으로 붙인 블로그 주소 기록

상태: draft(초안) → approved(승인) → built(제작 완료, 쇼츠만) → published(올림). rejected 는 다시 쓰라는 뜻.
승인 없이는 build · publish 가 돌지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date as _date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import ROOT, build_dir, day_dir, dump_frontmatter, list_days, read_doc, set_status  # noqa: E402

KST = timezone(timedelta(hours=9))
TOPICS = os.path.join(ROOT, "topics.md")


# ------------------------------------------------------------------ 소재 --
def next_topic(explicit: int | None = None) -> tuple[int, dict]:
    """topics.md 의 '쇼츠·블로그 소재 큐' 표에서 아직 안 쓴 첫 줄. 표 형식: | 번호 | 시리즈 | 제목 | 블로그 각도 | 쓴 날 |"""
    with open(TOPICS, encoding="utf-8") as f:
        rows = [ln for ln in f.read().split("\n") if re.match(r"^\|\s*\d+\s*\|", ln)]
    for ln in rows:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        n = int(cells[0])
        used = len(cells) > 4 and cells[4] not in ("", "-")
        if explicit is not None and n == explicit:
            return n, {"series": cells[1], "title": cells[2], "blog_angle": cells[3] if len(cells) > 3 else ""}
        if explicit is None and not used:
            return n, {"series": cells[1], "title": cells[2], "blog_angle": cells[3] if len(cells) > 3 else ""}
    raise SystemExit("topics.md 에 남은 소재가 없다 — 표에 줄을 더 넣는다")


def mark_topic(n: int, date: str) -> None:
    with open(TOPICS, encoding="utf-8") as f:
        lines = f.read().split("\n")
    for i, ln in enumerate(lines):
        if re.match(rf"^\|\s*{n}\s*\|", ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            while len(cells) < 5:
                cells.append("")
            cells[4] = date
            lines[i] = "| " + " | ".join(cells) + " |"
    with open(TOPICS, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def new(date: str | None, topic: int | None) -> str:
    date = date or datetime.now(KST).strftime("%Y-%m-%d")
    d = day_dir(date)
    if os.path.exists(os.path.join(d, "shorts.md")):
        print(f"{d} 는 이미 있다")
        return date
    os.makedirs(d, exist_ok=True)
    n, t = next_topic(topic)
    publish_at = f"{date}T18:00:00+09:00"
    shorts = {
        "date": date, "topic": n, "series": t["series"], "title": t["title"], "status": "draft",
        "titles": [t["title"], "", ""], "tags": [], "voice": "default",
        "youtube": {"title": t["title"], "description": "", "pinned_comment": "", "publish_at": publish_at, "tags": []},
    }
    blog = {
        "date": date, "topic": n, "title": t["title"], "status": "draft", "tags": [], "category": "풍수·택일",
        "images": [], "angle": t["blog_angle"], "publish_at": publish_at,
    }
    with open(os.path.join(d, "shorts.md"), "w", encoding="utf-8") as f:
        f.write(dump_frontmatter(shorts) + "\n<!-- 장면 지시 + 나레이션. 형식은 pipeline/common.py 머리 설명 · 예시는 content/_example -->\n")
    with open(os.path.join(d, "blog.md"), "w", encoding="utf-8") as f:
        f.write(dump_frontmatter(blog) + "\n<!-- 본문 1,500~2,500자. 구조는 brand.md 「블로그 글 구조」 -->\n")
    mark_topic(n, date)
    print(f"{d}  소재 {n}: [{t['series']}] {t['title']}")
    return date


# ------------------------------------------------------------------ 상태 --
def status() -> None:
    days = list_days()
    if not days:
        print("아직 만든 날이 없다 — daily.py new")
        return
    print(f"{'날짜':<12}{'블로그':<12}{'쇼츠':<12}제목")
    for d in days:
        row = []
        title = ""
        for name in ("blog.md", "shorts.md"):
            p = os.path.join(day_dir(d), name)
            if os.path.exists(p):
                meta, _ = read_doc(p)
                row.append(str(meta.get("status", "?")))
                title = title or str(meta.get("title", ""))
            else:
                row.append("-")
        print(f"{d:<12}{row[0]:<12}{row[1]:<12}{title}")


def _targets(argv: list[str]) -> list[str]:
    if "--blog" in argv:
        return ["blog.md"]
    if "--shorts" in argv:
        return ["shorts.md"]
    return ["blog.md", "shorts.md"]


def approve(date: str, argv: list[str]) -> None:
    for name in _targets(argv):
        p = os.path.join(day_dir(date), name)
        if os.path.exists(p):
            meta = set_status(p, "approved", approved_at=datetime.now(KST).isoformat(timespec="minutes"))
            print(f"승인: {name}  {meta.get('title', '')}")


def reject(date: str, argv: list[str]) -> None:
    why = argv[argv.index("--why") + 1] if "--why" in argv else ""
    for name in _targets(argv):
        p = os.path.join(day_dir(date), name)
        if os.path.exists(p):
            set_status(p, "rejected", reject_reason=why)
            print(f"반려: {name}  {why}")


def check(date: str) -> int:
    from pipeline.check import main as check_main
    return check_main([date])


def build(date: str, argv: list[str]) -> str:
    p = os.path.join(day_dir(date), "shorts.md")
    meta, _ = read_doc(p)
    if meta.get("status") not in ("approved", "built"):
        raise SystemExit(f"쇼츠 status 가 {meta.get('status')} — 승인(approved) 뒤에만 만든다")
    if check(date):
        raise SystemExit("검수 실패 — 금지 표현을 고친 뒤 다시")
    engine = argv[argv.index("--engine") + 1] if "--engine" in argv else os.environ.get("SHORTS_ENGINE", "json2video")
    voice = argv[argv.index("--voice") + 1] if "--voice" in argv else (meta.get("voice") if meta.get("voice") not in (None, "", "default") else None)
    if engine == "json2video":
        from pipeline.fetch_background import fetch
        from pipeline.render_json2video import render as j2v
        print("1/2 배경 영상")
        fetch(date, download=False)
        print("2/2 JSON2Video 렌더 (음성·자막·합성)")
        info = j2v(date, voice)
        mp4 = os.path.join(build_dir(date), "shorts.mp4")
        set_status(p, "built", duration=info.get("duration"), voice_used=voice or "default", engine="json2video", j2v_project=info.get("project"))
        return mp4
    from pipeline.graphics import render_day
    from pipeline.render import render
    from pipeline.tts import synthesize
    print("1/3 음성")
    info = synthesize(date, voice)
    print(f"    {info['voice']} · {info['total']:.1f}초")
    print("2/3 그래픽")
    pngs = render_day(date)
    print(f"    {len(pngs)}장")
    print("3/3 영상")
    mp4 = render(date)
    set_status(p, "built", duration=info["total"], voice_used=info["voice"], engine="local")
    print(f"    {mp4} ({os.path.getsize(mp4) / 1e6:.1f} MB)")
    return mp4


def publish(date: str, argv: list[str]) -> dict:
    result: dict = {"date": date}
    d = day_dir(date)
    now = "--now" in argv
    blog_target = argv[argv.index("--blog-target") + 1] if "--blog-target" in argv else os.environ.get("BLOG_TARGET", "naver")
    sp = os.path.join(d, "shorts.md")
    if os.path.exists(sp) and "--blog" not in argv:
        meta, _ = read_doc(sp)
        if meta.get("status") == "built":
            from pipeline.upload_youtube import upload
            result["youtube"] = upload(date, now=now)
        elif meta.get("status") == "approved":
            print("쇼츠: 승인만 되고 제작이 안 됐다 — build 먼저")
        else:
            print(f"쇼츠: status {meta.get('status')} — 건너뜀")
    bp = os.path.join(d, "blog.md")
    if os.path.exists(bp) and "--shorts" not in argv:
        meta, _ = read_doc(bp)
        if meta.get("status") == "approved":
            if check(date):
                raise SystemExit("검수 실패")
            if blog_target == "wordpress":
                from pipeline.publish_blog import publish_wordpress
                result["blog"] = publish_wordpress(date)
            else:
                from pipeline.publish_blog import export
                info = export(date)
                result["blog"] = {"manual": True, "bundle": os.path.dirname(info["html"]), "images": len(info["images"])}
                print(f"블로그: 네이버는 손으로 붙인다 — {result['blog']['bundle']}/CHECKLIST.md")
        else:
            print(f"블로그: status {meta.get('status')} — 건너뜀")
    with open(os.path.join(build_dir(date), "publish.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def published(date: str, argv: list[str]) -> None:
    url = argv[argv.index("--blog-url") + 1] if "--blog-url" in argv else ""
    p = os.path.join(day_dir(date), "blog.md")
    set_status(p, "published", published_url=url, published_at=datetime.now(KST).isoformat(timespec="minutes"))
    print(f"기록: {url}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "new":
        d = rest[rest.index("--date") + 1] if "--date" in rest else None
        t = int(rest[rest.index("--topic") + 1]) if "--topic" in rest else None
        new(d, t)
        return 0
    if cmd == "status":
        status()
        return 0
    if not rest or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest[0]):
        print("날짜(YYYY-MM-DD)가 필요하다")
        return 2
    date = rest[0]
    if cmd == "check":
        return check(date)
    if cmd == "approve":
        approve(date, rest)
    elif cmd == "reject":
        reject(date, rest)
    elif cmd == "build":
        build(date, rest)
    elif cmd == "publish":
        publish(date, rest)
    elif cmd == "published":
        published(date, rest)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    _ = _date
    sys.exit(main(sys.argv[1:]))
