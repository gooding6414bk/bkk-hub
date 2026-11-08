"""쇼츠 mp4 → 유튜브 (YouTube Data API v3, 공식). 제목·설명·태그·예약 시각까지 한 번에. 외부 패키지 없이 urllib 로 올린다.

    python gibalhae/pipeline/upload_youtube.py 2026-10-24                      shorts.md 머리말대로 (예약 시각 포함)
    python gibalhae/pipeline/upload_youtube.py 2026-10-24 --now                바로 공개
    python gibalhae/pipeline/upload_youtube.py 2026-10-24 --dry-run            보내지 않고 보낼 내용만 출력

인증(한 번만, 내 PC 에서): youtube_auth.py 참고. 토큰(JSON 한 줄)은 대시보드 「연결」 탭에 붙여넣거나(instance/gibalhae/keys.json)
환경변수 YT_TOKEN_JSON 에 둔다.

업로드되면 shorts.md 머리말 youtube.video_id · youtube.url 을 채우고 status 를 published 로 바꾼다.
하루 업로드 할당량은 기본 6개 정도(1만 유닛 중 업로드 1,600). 하루 1~2편이면 넉넉하다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import keys  # noqa: E402
from pipeline.common import build_dir, day_dir, read_doc, set_status  # noqa: E402

KST = timezone(timedelta(hours=9))
AI_LINE = "이 채널의 목소리는 AI 음성입니다. 내용은 30년 택일·풍수가 도형의 기록입니다."
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"


def payload(meta: dict, now: bool = False) -> dict:
    y = meta.get("youtube") or {}
    title = str(y.get("title") or meta.get("title") or "").strip()
    if len(title) > 100:
        raise SystemExit(f"제목 {len(title)}자 — 100자 안")
    desc = str(y.get("description") or "").strip()
    if "AI" not in desc:
        desc = (desc + "\n\n" + AI_LINE).strip()
    tags = [str(t) for t in (y.get("tags") or meta.get("tags") or [])][:30]
    if "Shorts" not in tags:
        tags.append("Shorts")
    body = {
        "snippet": {"title": title, "description": desc, "tags": tags, "categoryId": str(y.get("category", "22")),
                    "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
        "status": {"privacyStatus": "public" if now else "private", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},     # AI 음성 — 유튜브 '변경·합성 콘텐츠' 표시
    }
    if not now:
        at = y.get("publish_at")
        if at:
            dt = datetime.fromisoformat(str(at))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            body["status"]["privacyStatus"] = "public"
    return body


def _call(url: str, token: str, data: bytes | None = None, method: str = "POST", ctype: str = "application/json", extra: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": ctype, **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def upload(date: str, now: bool = False, dry: bool = False) -> dict:
    path = os.path.join(day_dir(date), "shorts.md")
    meta, _ = read_doc(path)
    if meta.get("status") not in ("built", "approved") and not dry:
        raise SystemExit(f"status 가 {meta.get('status')} — built(제작 완료) 상태만 올린다")
    if (meta.get("youtube") or {}).get("video_id") and not dry:
        raise SystemExit(f"이미 올린 영상: {(meta.get('youtube') or {}).get('url')}")
    mp4 = os.path.join(build_dir(date), "shorts.mp4")
    if not os.path.exists(mp4) and not dry:
        raise SystemExit(f"{mp4} 없음 — build 먼저")
    body = payload(meta, now)
    if dry:
        print(json.dumps(body, ensure_ascii=False, indent=1))
        return body
    tok = keys.youtube_access_token()
    if not tok.get("ok"):
        raise SystemExit("유튜브 인증 실패: " + tok.get("detail", ""))
    token = tok["token"]
    size = os.path.getsize(mp4)
    code, hdr, txt = _call(UPLOAD, token, json.dumps(body).encode(), extra={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
    if code != 200 or not hdr.get("Location"):
        raise SystemExit(f"업로드 시작 실패 {code}: {txt[:400]}")
    with open(mp4, "rb") as f:
        code, hdr, txt = _call(hdr["Location"], token, f.read(), "PUT", "video/mp4")
    if code not in (200, 201):
        raise SystemExit(f"업로드 실패 {code}: {txt[:400]}")
    vid = json.loads(txt)["id"]
    url = f"https://youtube.com/shorts/{vid}"
    y = dict(meta.get("youtube") or {})
    y["video_id"], y["url"] = vid, url
    pinned = str(y.get("pinned_comment") or "").strip()
    if pinned:
        c, _, t = _call("https://www.googleapis.com/youtube/v3/commentThreads?part=snippet", token,
                        json.dumps({"snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": pinned}}}}).encode())
        if c == 200:
            y["pinned_comment_posted"] = True
            print("  고정 댓글 올림(스튜디오에서 '고정' 은 손으로 한 번 눌러야 한다)")
        else:
            print(f"  WARN 댓글 못 올림 {c}: {t[:200]}")
    set_status(path, "published", youtube=y, published_at=datetime.now(KST).isoformat(timespec="minutes"))
    print(f"올림: {url}  ({'예약 ' + body['status'].get('publishAt', '') if 'publishAt' in body['status'] else body['status']['privacyStatus']})")
    return {"video_id": vid, "url": url, **body["status"]}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    upload(argv[0], now="--now" in argv, dry="--dry-run" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
