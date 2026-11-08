"""통합 보드: 📈 주식 + 🎬 기발해 콘텐츠.  python -m bkkstock web

Flask 단일 프로세스(waitress). 로그인 하나로 두 화면을 다 본다.
- 값을 바꾸는 요청은 같은 사이트에서 온 것만 받는다 (Origin/Referer 검사, 건우아비 보드와 같은 방식).
- 키 변경은 HTTPS로 들어왔거나 서버 자신(SSH 터널)에서 접속했을 때만.
- 화면은 저장된 결과를 읽기만 하고, 무거운 수집은 타이머 작업이 한다.
"""

from __future__ import annotations

import hmac
import os
import secrets
import subprocess
import sys
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import __version__
from ..clients.base import ApiError
from ..config import KNOWN_KEYS, get_settings, write_env_values
from ..jobs.common import Ctx, make_ctx
from ..jobs.watchdog import health
from ..store import normalize_code
from ..timeutil import iso_kst
from . import data
from .apis import BY_ID, run_test

HERE = Path(__file__).parent
REPO = HERE.parents[1]
JOBS_FROM_UI = {"daily": ["daily", "--force"], "backfill": ["backfill", "--days", "90"],
                "dart-poll": ["dart-poll"], "first-week-check": ["first-week-check"], "report": ["report"]}
OPEN_ENDPOINTS = {"login", "healthz", "static"}

_ctx: Ctx | None = None
_fails: dict[str, list[float]] = defaultdict(list)


def ctx() -> Ctx:
    global _ctx
    if _ctx is None:
        _ctx = make_ctx(get_settings())
    return _ctx


def reload_ctx() -> None:
    global _ctx
    _ctx = make_ctx(get_settings(reload=True))


# ---------------- 표시 형식 ----------------
def fmt_num(v, nd=0):
    if v is None:
        return "–"
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v, nd=1, ratio=False):
    if v is None:
        return "–"
    try:
        x = float(v) * (100 if ratio else 1)
    except (TypeError, ValueError):
        return "–"
    return f"{x:+.{nd}f}%"


def fmt_eok(v):
    if v is None:
        return "–"
    try:
        return f"{float(v) / 1e8:,.0f}억"
    except (TypeError, ValueError):
        return "–"


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:,.0f}{unit}" if unit == "B" else f"{n:,.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def short_ts(s):
    if not s:
        return "–"
    s = str(s).replace("T", " ")
    return s[5:16] if len(s) >= 16 else s


# ---------------- 보안 ----------------
def same_origin() -> bool:
    src = request.headers.get("Origin") or request.headers.get("Referer")
    if not src:
        return True   # 브라우저가 아닌 도구. 세션 쿠키는 SameSite=Lax라 다른 사이트에서는 실리지 않는다
    return urlsplit(src).netloc == request.host


def secure_context() -> bool:
    if request.scheme == "https":
        return True
    return request.remote_addr in ("127.0.0.1", "::1") and "X-Forwarded-For" not in request.headers


def api_error(status: int, message: str):
    return jsonify({"ok": False, "message": message, "error": message}), status


# ---------------- 앱 ----------------
def load_gibalhae(app: Flask) -> bool:
    gdir = REPO / "gibalhae"
    if not (gdir / "pipeline" / "webui.py").exists():
        return False
    # 서버에서는 콘텐츠를 git 밖(instance/gibalhae/content)에 둔다. 자동 업데이트가 승인·수정을 지우지 않게.
    os.environ.setdefault("ARBI_INSTANCE", str(REPO / "instance"))
    if str(gdir) not in sys.path:
        sys.path.insert(0, str(gdir))
    from pipeline.webui import bp  # noqa: E402
    app.register_blueprint(bp)
    return True


def create_app() -> Flask:
    s = get_settings()
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"))
    app.secret_key = s.get("DASH_SECRET") or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_NAME="bkkhub", SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                      PERMANENT_SESSION_LIFETIME=timedelta(days=14), MAX_CONTENT_LENGTH=8 * 1024 * 1024,
                      JSON_AS_ASCII=False)
    app.json.ensure_ascii = False
    # 같은 서버의 리버스 프록시(127.0.0.1)가 붙이는 헤더만 믿는다
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.jinja_env.filters.update(num=fmt_num, pct=fmt_pct, eok=fmt_eok, bytes=fmt_bytes, ts=short_ts)
    app.jinja_env.globals.update(version=__version__)
    app.config["HAS_GIBALHAE"] = load_gibalhae(app)

    @app.context_processor
    def inject():
        tok = session.get("csrf") or secrets.token_urlsafe(24)
        session.setdefault("csrf", tok)
        nav = "gibalhae" if request.path.startswith("/gibalhae") else ""
        return {"csrf": tok, "demo": ctx().settings.demo, "csp_nonce": "", "has_gibalhae": app.config["HAS_GIBALHAE"],
                "user": getattr(g, "user", None), "nav": nav}

    @app.before_request
    def gate():
        ep = request.endpoint or ""
        if ep in OPEN_ENDPOINTS:
            if request.method == "POST" and not same_origin():
                abort(403)
            return None
        if not session.get("auth"):
            if request.path.startswith("/api/"):
                return api_error(401, "로그인이 필요합니다")
            return redirect(url_for("login", next=request.path))
        g.user = {"username": "주인", "role": "admin"}
        if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin():
            return api_error(403, "다른 사이트에서 온 요청은 받지 않습니다")
        return None

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    register_routes(app)
    return app


def page(name: str, **kw):
    return render_template(name, nav=name.split(".")[0], **kw)


def register_routes(app: Flask) -> None:
    # ---------- 로그인 ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        has_pw = bool(ctx().settings.get("DASH_PASSWORD"))
        if request.method == "GET":
            return render_template("login.html", has_pw=has_pw, error=None)
        ip = request.remote_addr or "?"
        now = time.time()
        _fails[ip] = [t for t in _fails[ip] if now - t < 600]
        if len(_fails[ip]) >= 5:
            return render_template("login.html", has_pw=True, error="실패가 많아 10분 동안 잠겼습니다."), 429
        pw = ctx().settings.get("DASH_PASSWORD")
        if pw and hmac.compare_digest(request.form.get("password", "").encode(), pw.encode()):
            session.clear()
            session.permanent = True
            session["auth"] = True
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            return redirect(nxt)
        _fails[ip].append(now)
        return render_template("login.html", has_pw=bool(pw), error="비밀번호가 맞지 않습니다."), 401

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/healthz")
    def healthz():
        ok, info = health(ctx())
        return jsonify(info), (200 if ok else 503)

    # ---------- 화면 ----------
    @app.get("/")
    def index():
        return page("dashboard.html", d=data.dashboard(ctx()), gb=data.gibalhae_summary() if app.config["HAS_GIBALHAE"] else None)

    @app.get("/stocks")
    def stocks():
        return page("stocks.html", rows=data.watch_rows(ctx()), day=data.ref_day(ctx()))

    @app.get("/stocks/<code>")
    def stock(code: str):
        try:
            code = normalize_code(code)
        except ValueError:
            abort(404)
        return page("stock.html", s=data.stock_detail(ctx(), code))

    @app.get("/apis")
    def apis():
        return page("apis.html", cards=data.api_cards(ctx()), secure=secure_context())

    @app.get("/ops")
    def ops():
        return page("ops.html", o=data.ops(ctx()))

    # ---------- 동작 ----------
    @app.get("/api/search")
    def api_search():
        return jsonify(data.search(ctx(), request.args.get("q", "")))

    @app.post("/api/watch")
    def api_watch_add():
        body = request.get_json(silent=True) or {}
        try:
            code = normalize_code(body.get("code", ""))
        except ValueError as e:
            return api_error(400, str(e))
        name = (body.get("name") or data.name_of(ctx(), code) or "").strip()[:60]
        kind = body.get("kind") if body.get("kind") in ("보유", "관심") else "관심"
        try:
            qty = _num(body.get("qty"))
            avg = _num(body.get("avg_price"))
        except ValueError as e:
            return api_error(400, str(e))
        ctx().store.upsert_watch(code, name, kind, qty, avg, (body.get("memo") or "")[:200])
        return jsonify({"ok": True, "code": code, "name": name})

    @app.delete("/api/watch/<code>")
    def api_watch_del(code: str):
        ctx().store.remove_watch(code)
        return jsonify({"ok": True})

    @app.get("/api/stock/<code>/news")
    def api_news(code: str):
        try:
            code = normalize_code(code)
        except ValueError:
            abort(404)
        return jsonify(data.news(ctx(), code, request.args.get("force") == "1"))

    @app.post("/api/apis/<api_id>/test")
    def api_test(api_id: str):
        if api_id not in BY_ID:
            abort(404)
        try:
            return jsonify({"ok": True, "message": run_test(ctx(), api_id)})
        except ApiError as e:
            return jsonify({"ok": False, "message": str(e)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "message": f"{type(e).__name__}: {e}"})

    @app.post("/api/apis/<api_id>/keys")
    def api_keys(api_id: str):
        if not secure_context():
            return api_error(403, "키 변경은 HTTPS나 SSH 터널(localhost)로 접속했을 때만 할 수 있습니다.")
        a = BY_ID.get(api_id)
        if not a:
            abort(404)
        body = request.get_json(silent=True) or {}
        allowed = {k for k, _ in a["keys"]}
        updates = {}
        for k, v in (body.get("values") or {}).items():
            if k not in allowed or k not in KNOWN_KEYS:
                return api_error(400, f"이 API의 키가 아닙니다: {k}")
            v = (v or "").strip()
            if v:
                updates[k] = v
        if not updates:
            return api_error(400, "바꿀 값이 없습니다 (빈 칸은 그대로 둡니다)")
        write_env_values(ctx().settings.env_file, updates)
        with ctx().store.db() as con:
            con.execute("INSERT INTO key_changes(at, api, keys, source) VALUES (?,?,?,?)",
                        (iso_kst(), api_id, ",".join(sorted(updates)), request.remote_addr or ""))
        if api_id == "kis":
            ctx().kis.clear_token()
        reload_ctx()
        return jsonify({"ok": True, "changed": sorted(updates)})

    @app.post("/api/jobs/<job>")
    def api_job(job: str):
        if job not in JOBS_FROM_UI:
            abort(404)
        if ctx().settings.demo:
            return jsonify({"ok": False, "message": "샘플 데이터 모드에서는 실행하지 않습니다."})
        log = ctx().store.data_dir / "first_week.txt" if job == "first-week-check" else None
        args = ["nice", "-n", "15", sys.executable, "-m", "bkkstock", *JOBS_FROM_UI[job]]
        out = open(log, "w") if log else subprocess.DEVNULL
        subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, cwd=REPO)
        return jsonify({"ok": True, "message": f"{job} 작업을 백그라운드로 시작했습니다. 몇 분 뒤 새로고침하세요."})

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return api_error(404, "없습니다")
        return render_template("error.html", code=404, message="페이지가 없습니다."), 404

    @app.errorhandler(403)
    def forbidden(e):
        if request.path.startswith("/api/"):
            return api_error(403, "허용되지 않은 요청입니다")
        return render_template("error.html", code=403, message="허용되지 않은 요청입니다."), 403


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        raise ValueError(f"숫자가 아닙니다: {v}")


app = create_app()
