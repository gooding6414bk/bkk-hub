"""콘텐츠 제작 관리 대시보드 — 보드 앱(app.py)에 붙는 Flask 블루프린트. 관리자만.

화면 /gibalhae — 탭 3개: 콘텐츠(날짜별 상태·승인·제작·업로드·미리보기·글 고치기) · 연결(API 키 넣기·확인) · 작업(실행 기록).
API  /api/gibalhae/*

어느 Flask 앱에나 붙는다: app.register_blueprint(bp). 템플릿·정적 파일은 이 폴더의 ui/ 에 있다.
호스트가 정할 수 있는 것(app.config):
    GIBALHAE_HOME_URL   "← 보드" 링크 (기본 "/")      GIBALHAE_HOME_LABEL  (기본 "보드")
    GIBALHAE_LOGOUT_URL 로그아웃 폼 주소 (기본 "/logout", "" 이면 숨김)
    GIBALHAE_ALLOW      함수 () -> bool. 관리자만 통과시키는 검사. 없으면 g.user 의 role 이 admin 인지 보고, g.user 도 없으면 통과(호스트가 이미 막았다고 본다)
혼자 띄우려면 gibalhae/app.py (비밀번호 하나짜리 로그인).
작업(제작·업로드 등)은 daily.py 를 별도 프로세스로 돌리고 기록을 instance/gibalhae/jobs/ 에 남긴다 — 화면이 2초마다 읽는다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import common as C  # noqa: E402
from pipeline import keys  # noqa: E402
from pipeline.check import check_file  # noqa: E402

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
bp = Blueprint("gibalhae", __name__, template_folder=UI, static_folder=os.path.join(UI, "static"), static_url_path="/gibalhae/static")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAILY = os.path.join(REPO, "gibalhae", "pipeline", "daily.py")
JOBS_DIR = os.path.join(keys.INSTANCE, "gibalhae", "jobs")
CMDS = {"new", "check", "approve", "reject", "build", "publish", "published"}
SAFE_ARG = re.compile(r"^[\w가-힣 .:/+\-,'\"!?()]{0,200}$")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _date_ok(d: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""))


def _username() -> str:
    u = getattr(g, "user", None)
    if isinstance(u, dict):
        return str(u.get("username") or u.get("name") or "admin")
    return str(u or "admin")


@bp.before_request
def _admin_only():
    if request.endpoint == "gibalhae.static":
        return None
    allow = current_app.config.get("GIBALHAE_ALLOW")
    if callable(allow):
        ok = bool(allow())
    else:
        u = getattr(g, "user", None)
        ok = True if u is None else (isinstance(u, dict) and u.get("role") in (None, "admin"))
    if not ok:
        if request.path.startswith("/api/"):
            return jsonify({"error": "관리자만 쓸 수 있습니다"}), 403
        abort(403)
    return None


# ------------------------------------------------------------------ 화면 --
@bp.get("/gibalhae")
@bp.get("/media")
def page():
    cfg = current_app.config
    return render_template("gibalhae.html", username=_username(), home_url=cfg.get("GIBALHAE_HOME_URL", "/"),
                           home_label=cfg.get("GIBALHAE_HOME_LABEL", "보드"), logout_url=cfg.get("GIBALHAE_LOGOUT_URL", "/logout"))


# ------------------------------------------------------------------ 콘텐츠 --
def _doc(date: str, name: str) -> dict | None:
    p = os.path.join(C.day_dir(date), name)
    if not os.path.exists(p):
        return None
    meta, body = C.read_doc(p)
    return {"meta": meta, "body": body.strip(), "status": meta.get("status", "draft"), "title": meta.get("title", "")}


def _build_info(date: str) -> dict:
    b = os.path.join(C.day_dir(date), "build")
    info = {"mp4": False, "movie_json": False, "j2v": None, "blog_bundle": False, "blog_images": [], "publish": None, "background": None}
    if not os.path.isdir(b):
        return info
    mp4 = os.path.join(b, "shorts.mp4")
    if os.path.exists(mp4):
        info["mp4"] = True
        info["mp4_size"] = os.path.getsize(mp4)
    info["movie_json"] = os.path.exists(os.path.join(b, "movie.json"))
    for key, name in (("j2v", "json2video.json"), ("publish", "publish.json"), ("background", "background.json")):
        p = os.path.join(b, name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    info[key] = json.load(f)
            except ValueError:
                pass
    blog = os.path.join(b, "blog")
    if os.path.isdir(blog):
        info["blog_bundle"] = os.path.exists(os.path.join(blog, "CHECKLIST.md"))
        info["blog_images"] = sorted(f"blog/{x}" for x in os.listdir(blog) if x.endswith(".png"))
    return info


def _day_row(date: str) -> dict:
    s, b = _doc(date, "shorts.md"), _doc(date, "blog.md")
    bi = _build_info(date)
    return {
        "date": date, "title": (s or b or {}).get("title", ""), "series": (s or {}).get("meta", {}).get("series", ""),
        "topic": (s or b or {}).get("meta", {}).get("topic"),
        "shorts": {"status": s["status"], "youtube": (s["meta"].get("youtube") or {}).get("url"), "duration": s["meta"].get("duration"),
                   "voice": s["meta"].get("voice_used") or s["meta"].get("voice")} if s else None,
        "blog": {"status": b["status"], "url": b["meta"].get("published_url")} if b else None,
        "mp4": bi["mp4"], "blog_bundle": bi["blog_bundle"],
    }


@bp.get("/api/gibalhae/overview")
def overview():
    days = [_day_row(d) for d in reversed(C.list_days())]
    credits = None
    for d in days:
        j = _build_info(d["date"]).get("j2v") or {}
        q = (j.get("remaining_quota") or {}).get("time")
        if q is not None:
            credits = q
            break
    with _lock:
        running = sum(1 for j in _jobs.values() if j["status"] == "running")
    kp = keys.public()
    return jsonify({"days": days, "keys": {k: {"set": v["set"], "label": v["label"]} for k, v in kp.items()},
                    "credits": credits, "running": running, "topics": _topics()[:40]})


def _topics() -> list[dict]:
    p = os.path.join(REPO, "gibalhae", "topics.md")
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                if re.match(r"^\|\s*\d+\s*\|", ln):
                    c = [x.strip() for x in ln.strip().strip("|").split("|")]
                    out.append({"n": int(c[0]), "series": c[1], "title": c[2], "angle": c[3] if len(c) > 3 else "",
                                "used": c[4] if len(c) > 4 else ""})
    except OSError:
        pass
    return out


@bp.get("/api/gibalhae/content/<date>")
def content(date: str):
    if not _date_ok(date) or not os.path.isdir(C.day_dir(date)):
        abort(404)
    out = {"date": date, "shorts": _doc(date, "shorts.md"), "blog": _doc(date, "blog.md"), "build": _build_info(date), "checks": {}}
    for name in ("shorts.md", "blog.md"):
        p = os.path.join(C.day_dir(date), name)
        if os.path.exists(p):
            try:
                f, w = check_file(p)
            except Exception as e:                    # 형식 오류도 화면에 보인다
                f, w = [f"{name}: {e}"], []
            out["checks"][name.split(".")[0]] = {"fails": f, "warns": w}
    out["raw"] = {}
    for name in ("shorts.md", "blog.md"):
        p = os.path.join(C.day_dir(date), name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                out["raw"][name.split(".")[0]] = fh.read()
    return jsonify(out)


@bp.put("/api/gibalhae/content/<date>/<kind>")
def content_save(date: str, kind: str):
    """글·대본 통째로 저장(머리말 포함). 올린 것(published)은 고치지 않는다."""
    if not _date_ok(date) or kind not in ("shorts", "blog"):
        abort(404)
    p = os.path.join(C.day_dir(date), f"{kind}.md")
    if not os.path.exists(p):
        abort(404)
    text = (request.get_json(silent=True) or {}).get("text")
    if not isinstance(text, str) or len(text) > 200_000:
        return jsonify({"error": "text 가 필요합니다"}), 400
    meta, _ = C.parse_frontmatter(text)
    if not meta.get("title"):
        return jsonify({"error": "머리말(---)에 title 이 있어야 합니다"}), 400
    cur, _ = C.read_doc(p)
    if cur.get("status") == "published":
        return jsonify({"error": "이미 올린 글은 여기서 고치지 않습니다"}), 400
    with open(p, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    f_, w_ = check_file(p)
    return jsonify({"saved": True, "fails": f_, "warns": w_})


@bp.get("/api/gibalhae/content/<date>/build/<path:name>")
def build_file(date: str, name: str):
    if not _date_ok(date) or ".." in name or not re.fullmatch(r"[\w./\-]+\.(mp4|png|jpg|ass|srt|json|md|txt|html)", name):
        abort(404)
    return send_from_directory(os.path.join(C.day_dir(date), "build"), name, max_age=0)


# ------------------------------------------------------------------ 작업 --
def _run_job(job: dict) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARBI_")} | {"ARBI_INSTANCE": keys.INSTANCE, "PYTHONIOENCODING": "utf-8",
                                                                                 "PYTHONUNBUFFERED": "1"}
    if C.CONTENT != os.path.join(C.ROOT, "content"):
        env["GIBALHAE_CONTENT"] = C.CONTENT
    os.makedirs(JOBS_DIR, exist_ok=True)
    log_path = os.path.join(JOBS_DIR, job["id"] + ".log")
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(job["cmd"][1:]) + "\n")
        log.flush()
        try:
            p = subprocess.Popen(job["cmd"], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            rc = p.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = -1
            log.write("\n30분 안에 끝나지 않아 멈춤\n")
        except Exception as e:
            rc = -2
            log.write(f"\n실행 실패: {e}\n")
    with _lock:
        job.update(status="ok" if rc == 0 else "fail", rc=rc, ended=time.time())


@bp.post("/api/gibalhae/action")
def action():
    d = request.get_json(silent=True) or {}
    cmd, date, args = d.get("cmd"), d.get("date"), d.get("args") or []
    if cmd not in CMDS:
        return jsonify({"error": "모르는 작업"}), 400
    if cmd != "new" and not _date_ok(date):
        return jsonify({"error": "날짜가 필요합니다"}), 400
    if not isinstance(args, list) or any(not isinstance(a, str) or not SAFE_ARG.match(a) for a in args):
        return jsonify({"error": "인자가 이상합니다"}), 400
    with _lock:
        if any(j["status"] == "running" and j["date"] == date and j["name"] == cmd for j in _jobs.values()):
            return jsonify({"error": "같은 작업이 돌고 있습니다"}), 409
    argv = [sys.executable, "-B", DAILY, cmd] + ([date] if cmd != "new" else []) + args
    job = {"id": uuid.uuid4().hex[:10], "name": cmd, "date": date, "args": args, "cmd": argv, "status": "running",
           "started": time.time(), "ended": None, "rc": None, "by": _username()}
    with _lock:
        _jobs[job["id"]] = job
        for old in sorted(_jobs.values(), key=lambda j: j["started"])[:-40]:
            if old["status"] != "running":
                _jobs.pop(old["id"], None)
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return jsonify({"job": _job_json(job)})


def _job_json(j: dict, with_log: bool = False) -> dict:
    out = {k: j[k] for k in ("id", "name", "date", "args", "status", "started", "ended", "rc", "by")}
    if with_log:
        try:
            with open(os.path.join(JOBS_DIR, j["id"] + ".log"), encoding="utf-8", errors="replace") as f:
                out["log"] = f.read()[-20000:]
        except OSError:
            out["log"] = ""
    return out


@bp.get("/api/gibalhae/jobs")
def jobs():
    with _lock:
        js = sorted(_jobs.values(), key=lambda j: j["started"], reverse=True)
    return jsonify({"jobs": [_job_json(j) for j in js]})


@bp.get("/api/gibalhae/jobs/<jid>")
def job(jid: str):
    with _lock:
        j = _jobs.get(jid)
    if not j:
        abort(404)
    return jsonify(_job_json(j, with_log=True))


# ------------------------------------------------------------------ 연결 --
@bp.get("/api/gibalhae/keys")
def keys_get():
    return jsonify({"keys": keys.public(), "file": keys.FILE.replace(REPO + os.sep, "")})


@bp.post("/api/gibalhae/keys")
def keys_save():
    d = request.get_json(silent=True) or {}
    patch = {k: v for k, v in d.items() if k in keys.GUIDE and isinstance(v, str)}
    if not patch:
        return jsonify({"error": "저장할 값이 없습니다"}), 400
    try:
        keys.save(patch)
    except (ValueError, OSError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"saved": sorted(patch), "keys": keys.public()})


@bp.post("/api/gibalhae/keys/<name>/check")
def keys_check(name: str):
    if name not in keys.CHECKABLE:
        return jsonify({"error": "확인할 수 없는 항목"}), 400
    return jsonify(keys.check(name))
