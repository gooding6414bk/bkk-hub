"""블로그 글 내보내기·올리기.

    python gibalhae/pipeline/publish_blog.py 2026-10-24                      naver (기본): 붙여넣기용 묶음
    python gibalhae/pipeline/publish_blog.py 2026-10-24 --target wordpress   WordPress REST API 로 바로 올림
    python gibalhae/pipeline/publish_blog.py 2026-10-24 --target html        html 파일만

네이버 블로그는 공개 API 가 없고 자동 글쓰기 프로그램은 제재 대상이라, 사람이 5분 붙이는 묶음을 만든다:
    build/blog/post.html      스마트에디터에 그대로 붙일 수 있는 HTML (제목·본문·이미지 자리·태그)
    build/blog/post.txt       본문 글만(붙여넣기용)
    build/blog/NN.png         도면·달력 이미지
    build/blog/CHECKLIST.md   붙이는 순서(제목 → 본문 → 이미지 자리에 사진 → 태그 → 발행)

워드프레스(wordpress.com 포함)는 비밀값 세 개로 바로 올린다 (환경변수):
    WP_URL=https://내블로그.com   WP_USER=아이디   WP_APP_PASSWORD=응용 프로그램 비밀번호 (프로필 → 응용 프로그램 비밀번호)
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import keys  # noqa: E402
from pipeline.common import build_dir, day_dir, read_doc, set_status  # noqa: E402
from pipeline.graphics import render_blog  # noqa: E402

KST = timezone(timedelta(hours=9))


def md_to_html(md: str) -> str:
    """아주 작은 마크다운 → HTML (제목 ##, 문단, 굵게, 목록, 이미지 자리 ![..](N))."""
    out, para, in_list = [], [], False

    def flush():
        nonlocal para
        if para:
            text = " ".join(para)
            out.append(f"<p>{inline(text)}</p>")
            para = []

    def inline(t: str) -> str:
        t = html.escape(t, quote=False)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
        return t

    for ln in md.split("\n"):
        s = ln.rstrip()
        if not s.strip():
            flush()
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            flush()
            lvl = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^!\[(.*?)\]\((\d+)\)", s)
        if m:
            flush()
            out.append(f'<p class="img" data-image="{m.group(2)}">[이미지 {m.group(2)}: {html.escape(m.group(1))}]</p>')
            continue
        if re.match(r"^[-*]\s+", s):
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else '')}</li>")
            continue
        para.append(s.strip())
    flush()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def export(date: str) -> dict:
    path = os.path.join(day_dir(date), "blog.md")
    meta, body = read_doc(path)
    out = os.path.join(build_dir(date), "blog")
    os.makedirs(out, exist_ok=True)
    images = render_blog(date)
    title = str(meta.get("title", "")).strip()
    tags = [str(t) for t in (meta.get("tags") or [])]
    body_html = md_to_html(body.strip())
    doc = (f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>\n"
           f"<h1>{html.escape(title)}</h1>\n{body_html}\n<p class='tags'>{' '.join('#' + t for t in tags)}</p>\n")
    with open(os.path.join(out, "post.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    with open(os.path.join(out, "post.txt"), "w", encoding="utf-8") as f:
        f.write(title + "\n\n" + re.sub(r"^#{1,3}\s+", "", body.strip(), flags=re.M) + "\n\n" + " ".join("#" + t for t in tags) + "\n")
    with open(os.path.join(out, "CHECKLIST.md"), "w", encoding="utf-8") as f:
        f.write(f"# {date} 블로그 붙이기 (5분)\n\n"
                f"1. blog.naver.com 글쓰기 → 제목: `{title}`\n"
                f"2. post.txt 본문을 붙인다 (문단 그대로)\n"
                f"3. `[이미지 N]` 자리마다 `{os.path.basename(out)}/NN.png` 를 넣는다 ({len(images)}장)\n"
                f"4. 태그 {len(tags)}개: {' '.join('#' + t for t in tags)}\n"
                f"5. 카테고리: {meta.get('category', '풍수·택일')} → 발행(전체 공개·검색 허용)\n"
                f"6. 발행된 주소를 `python gibalhae/pipeline/daily.py published {date} --blog-url 주소` 로 적는다\n")
    return {"html": os.path.join(out, "post.html"), "txt": os.path.join(out, "post.txt"), "images": images, "title": title, "tags": tags}


def publish_wordpress(date: str) -> dict:
    url, user, pw = (keys.get("wp_url") or "").rstrip("/"), keys.get("wp_user") or "", keys.get("wp_app_password") or ""
    if not (url and user and pw):
        raise SystemExit("워드프레스 주소·아이디·응용 프로그램 비밀번호가 필요하다 (대시보드 연결 탭 또는 WP_URL · WP_USER · WP_APP_PASSWORD)")
    info = export(date)
    path = os.path.join(day_dir(date), "blog.md")
    meta, _ = read_doc(path)
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    hdr = {"Authorization": f"Basic {auth}", "Content-Type": "application/json", "User-Agent": "gibalhae-pipeline"}

    def call(method: str, ep: str, data=None, raw: bytes | None = None, extra: dict | None = None):
        req = urllib.request.Request(f"{url}/wp-json/wp/v2/{ep}", method=method,
                                     data=raw if raw is not None else (json.dumps(data).encode() if data else None))
        for k, v in {**hdr, **(extra or {})}.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    with open(info["html"], encoding="utf-8") as f:
        content = f.read().split("</h1>", 1)[1]
    # 이미지 올리고 자리 바꿔 넣기
    for i, img in enumerate(info["images"], 1):
        with open(img, "rb") as f:
            media = call("POST", "media", raw=f.read(), extra={"Content-Type": "image/png",
                                                                 "Content-Disposition": f'attachment; filename="{date}-{i:02d}.png"'})
        content = re.sub(rf'<p class="img" data-image="{i}">.*?</p>', f'<figure><img src="{media["source_url"]}" alt=""></figure>', content)
    at = meta.get("publish_at")
    post = {"title": info["title"], "content": content, "status": "future" if at else "publish"}
    if at:
        dt = datetime.fromisoformat(str(at))
        post["date"] = (dt if dt.tzinfo else dt.replace(tzinfo=KST)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        post["date_gmt"] = post.pop("date")
    res = call("POST", "posts", post)
    link = res.get("link", "")
    set_status(path, "published", published_url=link, published_at=datetime.now(KST).isoformat(timespec="minutes"))
    print(f"올림: {link}")
    return {"url": link, "id": res.get("id")}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    date = argv[0]
    target = argv[argv.index("--target") + 1] if "--target" in argv else "naver"
    if target == "wordpress":
        publish_wordpress(date)
    else:
        info = export(date)
        print(f"붙여넣기 묶음: {os.path.dirname(info['html'])}  (이미지 {len(info['images'])}장)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
