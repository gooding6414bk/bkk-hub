"""통합 보드: 📈 주식 + 🎬 기발해 콘텐츠.  python -m bkkstock web

Flask 단일 프로세스(waitress). 로그인 하나로 두 화면을 다 본다.
- 값을 바꾸는 요청은 같은 사이트에서 온 것만 받는다 (Origin/Referer 검사 + CSRF 토큰).
- 키 변경은 HTTPS로 들어왔거나 서버 자신(SSH 터널)에서 접속했을 때만.
- 접속자 주소는 waitress가 같은 서버의 Caddy(127.0.0.1)가 붙인 X-Forwarded-* 로 정한다 (cli.py web).
- 로그인 잠금: 처음 보는 기기는 주소별 5회·전체 20회(10분), 전에 로그인했던 기기는 그 기기만 10회.
  남이 틀린 비밀번호를 퍼부어도 주인 휴대폰·PC는 막히지 않는다 (OWASP '기기 쿠키' 방식).
- 화면은 저장된 결과를 읽기만 하고, 무거운 수집은 타이머 작업이 한다.
"""

from __future__ import annotations

import hmac
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature, URLSafeTimedSerializer

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
JOB_LOCKS = {"daily": "daily", "backfill": "backfill", "dart-poll": "dart_poll"}
JOB_NAMES = {"daily": "일일 수집", "backfill": "과거 데이터 채우기", "dart-poll": "공시 조회",
             "first-week-check": "전체 API 점검", "report": "일일 보고"}
OPEN_ENDPOINTS = {"login", "healthz", "static"}
DEVICE_COOKIE = "bkkdev"            # HTTPS에서는 __Host-bkkdev (형제 *.sslip.io가 덮어쓰지 못하게)
SESSION_VERSION = 2                 # 올리면 예전 세션 쿠키는 모두 다시 로그인 (2: 쿠키 보안 강화 배포)
LOCK_WINDOW = 600            # 10분
IP_LIMIT = 5                 # 처음 보는 기기: 같은 주소에서 5번
UNKNOWN_LIMIT = 20           # 처음 보는 기기 전체 합쳐 20번 (주소를 바꿔 가며 시도하는 경우)
DEVICE_LIMIT = 10            # 전에 로그인했던 기기: 그 기기에서만 10번

_ctx: Ctx | None = None
_login_lock = threading.Lock()      # 실패 수 확인과 기록을 한 번에 — 동시 요청으로 한도를 넘지 못하게
_health_cache: dict = {"t": 0.0, "v": None}


def ctx() -> Ctx:
    global _ctx
    if _ctx is None:
        _ctx = make_ctx(get_settings())
        try:                     # 공시 분류 규칙이 바뀌었으면 화면에서도 바로 새 분류로 보이게
            from ..clients.dart import CLASSIFY_VERSION, classify
            _ctx.store.retag_disclosures(classify, CLASSIFY_VERSION)
        except Exception:  # noqa: BLE001
            pass
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
    """원 → '1조 7,841억' / '3,420억' (네이버·토스 표기)."""
    if v is None:
        return "–"
    try:
        e = float(v) / 1e8
    except (TypeError, ValueError):
        return "–"
    if e != e:
        return "–"
    sign = "-" if e < 0 else ""
    e = abs(e)
    if e >= 10000:
        jo, eok = divmod(round(e), 10000)
        return f"{sign}{jo:,}조" + (f" {eok:,}억" if eok else "")
    return f"{sign}{e:,.0f}억"


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


def csrf_ok() -> bool:
    sent = request.headers.get("X-CSRF") or request.form.get("csrf") or ""
    want = session.get("csrf") or ""
    return bool(want) and hmac.compare_digest(sent.encode(), want.encode())


class HubSession(SecureCookieSessionInterface):
    """HTTPS로 들어오면 쿠키를 Secure + __Host- 이름으로(평문 http로 새지 않고, 형제 *.sslip.io가 덮어쓰지 못하게).
    SSH 터널(http://127.0.0.1)은 예전처럼 일반 쿠키."""

    def get_cookie_name(self, app):
        return "__Host-bkkhub" if request.is_secure else app.config["SESSION_COOKIE_NAME"]

    def get_cookie_secure(self, app):
        return bool(request.is_secure or app.config["SESSION_COOKIE_SECURE"])


def device_signer(app: Flask) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.secret_key, salt="bkkhub-device")


def device_cookie_name() -> str:
    return "__Host-" + DEVICE_COOKIE if request.is_secure else DEVICE_COOKIE


def known_device(app: Flask) -> str | None:
    """같은 이름 쿠키가 여러 개 와도(다른 주소가 심은 가짜) 서명이 맞는 것이 하나라도 있으면 인정한다."""
    signer = device_signer(app)
    for name in ("__Host-" + DEVICE_COOKIE, DEVICE_COOKIE):
        for tok in request.cookies.getlist(name):
            try:
                dev = signer.loads(tok, max_age=365 * 86400)
            except BadSignature:
                continue
            if isinstance(dev, str) and len(dev) <= 64:
                return dev
    return None


def job_running(lock_path: Path) -> bool:
    """작업 잠금 파일이 잡혀 있으면 이미 돌고 있는 것 (타이머 작업이든 화면에서 누른 작업이든)."""
    import fcntl
    lock_path.parent.mkdir(exist_ok=True)
    with open(lock_path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


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
    app.session_interface = HubSession()
    # 프록시 헤더는 waitress(trusted_proxy=127.0.0.1)가 처리한다. 여기서 또 ProxyFix를 걸지 않는다.
    app.jinja_env.filters.update(num=fmt_num, pct=fmt_pct, eok=fmt_eok, bytes=fmt_bytes, ts=short_ts)
    app.jinja_env.globals.update(version=__version__)
    app.config["HAS_GIBALHAE"] = load_gibalhae(app)

    @app.context_processor
    def inject():
        tok = session.get("csrf") or secrets.token_urlsafe(24)
        session.setdefault("csrf", tok)
        nav = "gibalhae" if request.path.startswith(("/gibalhae", "/media")) else ""
        return {"csrf": tok, "demo": ctx().settings.demo, "csp_nonce": getattr(g, "csp_nonce", ""),
                "has_gibalhae": app.config["HAS_GIBALHAE"], "user": getattr(g, "user", None), "nav": nav,
                "alerts_off": not ctx().tg.configured}

    @app.before_request
    def gate():
        g.csp_nonce = secrets.token_urlsafe(16)
        ep = request.endpoint or ""
        if ep in OPEN_ENDPOINTS:
            if request.method == "POST" and not same_origin():
                abort(403)
            return None
        if not session.get("auth") or session.get("sv") != SESSION_VERSION:
            if request.path.startswith("/api/"):
                return api_error(401, "로그인이 필요합니다")
            return redirect(url_for("login", next=request.path))
        g.user = {"username": "주인", "role": "admin"}
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not same_origin():
                return api_error(403, "다른 사이트에서 온 요청은 받지 않습니다")
            if not csrf_ok():
                return api_error(403, "화면이 오래되었습니다. 새로고침한 뒤 다시 해 주세요.")
        return None

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # 인라인 스크립트는 요청마다 바뀌는 nonce가 있는 것만. 외부에서 불러오는 것은 없다.
        nonce = getattr(g, "csp_nonce", "")
        script_src = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            f"script-src {script_src}; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "font-src 'self' data:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=2592000")
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
        dev = known_device(app)
        st = ctx().store
        pw = ctx().settings.get("DASH_PASSWORD")
        with _login_lock:
            now = time.time()
            since = now - LOCK_WINDOW
            if dev:
                locked = st.login_fail_count(since, device=dev) >= DEVICE_LIMIT
                msg = "이 기기에서 실패가 많아 10분 동안 잠겼습니다."
            else:
                locked = (st.login_fail_count(since, ip=ip) >= IP_LIMIT
                          or st.login_fail_count(since, unknown_devices=True) >= UNKNOWN_LIMIT)
                msg = ("실패가 많아 10분 동안 잠겼습니다. 전에 로그인했던 휴대폰·PC에서는 그대로 들어갈 수 있습니다. "
                       "급하면 서버에서 python -m bkkstock unlock")
            ok = (not locked) and bool(pw) and hmac.compare_digest(request.form.get("password", "").encode(), pw.encode())
            if not locked and not ok:
                st.login_fail(now, ip, dev)
        if locked:
            return render_template("login.html", has_pw=True, error=msg), 429
        if ok:
            session.clear()
            session.permanent = True
            session["auth"] = True
            session["sv"] = SESSION_VERSION
            session["csrf"] = secrets.token_urlsafe(24)
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
                nxt = "/"
            resp = redirect(nxt)
            token = device_signer(app).dumps(dev or secrets.token_urlsafe(12))
            resp.set_cookie(device_cookie_name(), token, max_age=365 * 86400, httponly=True, samesite="Lax",
                            secure=request.is_secure, path="/")
            return resp
        return render_template("login.html", has_pw=bool(pw), error="비밀번호가 맞지 않습니다."), 401

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/healthz")
    def healthz():
        """밖에서 보는 감시(UptimeRobot 등)용. 로그인 없이 열려 있어 ok와 정해진 이유 코드만 준다(30초 캐시)."""
        now = time.time()
        if _health_cache["v"] is None or now - _health_cache["t"] > 30:
            ok, info = health(ctx())
            _health_cache.update(t=now, v=(ok, sorted(info.get("codes") or [])))
        ok, codes = _health_cache["v"]
        body = {"ok": ok} if ok else {"ok": False, "codes": codes}
        return jsonify(body), (200 if ok else 503)

    @app.get("/api/health")
    def api_health():
        ok, info = health(ctx())
        return jsonify(dict(info, ok=ok))

    # ---------- 화면 ----------
    @app.get("/")
    def index():
        return page("dashboard.html", d=data.dashboard(ctx()))   # 영상 콘텐츠 요약은 메인에 두지 않는다(🎬 화면에서만)

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
        try:
            ctx().store.remove_watch(code)
        except ValueError as e:
            return api_error(400, str(e))
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
        lock = JOB_LOCKS.get(job)
        if lock and job_running(ctx().store.data_dir / "locks" / f"{lock}.lock"):
            return jsonify({"ok": False, "message": f"{JOB_NAMES.get(job, job)} 작업이 이미 돌고 있습니다."})
        logs = ctx().store.data_dir / "logs"
        logs.mkdir(exist_ok=True)
        if job == "first-week-check":
            log = ctx().store.data_dir / "first_week.txt"
        else:
            log = logs / f"{job}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        args = ["nice", "-n", "15", sys.executable, "-m", "bkkstock", *JOBS_FROM_UI[job]]
        subprocess.Popen(args, stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True, cwd=REPO)
        return jsonify({"ok": True, "message": f"{JOB_NAMES.get(job, job)} 작업을 시작했습니다. 몇 분 뒤 새로고침하세요."})

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
