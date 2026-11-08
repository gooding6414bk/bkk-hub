"""대시보드 웹 앱.  python -m bkkstock web

로그인 필수. 키 변경은 HTTPS로 들어왔거나 서버 자신(SSH 터널)에서 접속했을 때만 허용한다.
화면은 저장된 결과를 읽기만 하고, 무거운 수집은 타이머 작업이 한다.
"""

from __future__ import annotations

import hmac
import json
import secrets
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

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
_settings = get_settings()

app = FastAPI(title="bkk-stock", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=_settings.get("DASH_SECRET") or secrets.token_hex(32),
                   session_cookie="bkkstock", max_age=14 * 86400, same_site="lax")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

_ctx: Ctx | None = None
_fails: dict[str, list[float]] = defaultdict(list)
JOBS_FROM_UI = {"daily": ["daily", "--force"], "backfill": ["backfill", "--days", "90"],
                "dart-poll": ["dart-poll"], "first-week-check": ["first-week-check"], "report": ["report"]}


def ctx() -> Ctx:
    global _ctx
    if _ctx is None:
        _ctx = make_ctx(get_settings())
    return _ctx


def reload_ctx() -> None:
    global _ctx
    _ctx = make_ctx(get_settings(reload=True))


# ---------------- 공통 ----------------
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
    """원 → 억 원."""
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


templates.env.filters.update(num=fmt_num, pct=fmt_pct, eok=fmt_eok, bytes=fmt_bytes, ts=short_ts)
templates.env.globals.update(version=__version__)


def logged_in(request: Request) -> bool:
    return bool(request.session.get("auth"))


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, token: str | None) -> None:
    if not token or not hmac.compare_digest(token, request.session.get("csrf") or ""):
        raise HTTPException(403, "CSRF 토큰이 맞지 않습니다. 새로고침 후 다시 시도하세요.")


def secure_context(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and "x-forwarded-for" not in request.headers


def page(request: Request, name: str, **kw):
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    c = ctx()
    return templates.TemplateResponse(request, name, {
        "csrf": csrf_token(request), "demo": c.settings.demo, "nav": name.split(".")[0], **kw})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---------------- 로그인 ----------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    has_pw = bool(ctx().settings.get("DASH_PASSWORD"))
    return templates.TemplateResponse(request, "login.html", {"has_pw": has_pw, "error": None, "demo": ctx().settings.demo})


@app.post("/login")
def login(request: Request, password: str = Form("")):
    ip = request.client.host if request.client else "?"
    now = time.time()
    _fails[ip] = [t for t in _fails[ip] if now - t < 600]
    if len(_fails[ip]) >= 5:
        return templates.TemplateResponse(request, "login.html", {"has_pw": True, "demo": ctx().settings.demo,
                                          "error": "실패가 많아 10분 동안 잠겼습니다."}, status_code=429)
    pw = ctx().settings.get("DASH_PASSWORD")
    if pw and hmac.compare_digest(password.encode(), pw.encode()):
        request.session.clear()
        request.session["auth"] = True
        csrf_token(request)
        return RedirectResponse("/", 303)
    _fails[ip].append(now)
    return templates.TemplateResponse(request, "login.html", {"has_pw": bool(pw), "demo": ctx().settings.demo,
                                      "error": "비밀번호가 맞지 않습니다."}, status_code=401)


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", 303)


@app.get("/healthz")
def healthz():
    ok, info = health(ctx())
    return JSONResponse(info, status_code=200 if ok else 503)


# ---------------- 화면 ----------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    return page(request, "dashboard.html", d=data.dashboard(ctx()))


@app.get("/stocks", response_class=HTMLResponse)
def stocks(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    return page(request, "stocks.html", rows=data.watch_rows(ctx()), day=data.ref_day(ctx()))


@app.get("/stocks/{code}", response_class=HTMLResponse)
def stock(request: Request, code: str):
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    try:
        code = normalize_code(code)
    except ValueError:
        raise HTTPException(404)
    return page(request, "stock.html", s=data.stock_detail(ctx(), code))


@app.get("/apis", response_class=HTMLResponse)
def apis(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    return page(request, "apis.html", cards=data.api_cards(ctx()), secure=secure_context(request))


@app.get("/ops", response_class=HTMLResponse)
def ops(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    return page(request, "ops.html", o=data.ops(ctx()))


# ---------------- 동작 (JSON) ----------------
def _auth_json(request: Request, need_csrf: bool = False):
    if not logged_in(request):
        raise HTTPException(401, "로그인이 필요합니다")
    if need_csrf:
        check_csrf(request, request.headers.get("x-csrf"))


@app.get("/api/search")
def api_search(request: Request, q: str = ""):
    _auth_json(request)
    return data.search(ctx(), q)


@app.post("/api/watch")
async def api_watch_add(request: Request):
    _auth_json(request, True)
    body = await request.json()
    try:
        code = normalize_code(body.get("code", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = (body.get("name") or data.name_of(ctx(), code) or "").strip()[:60]
    kind = body.get("kind") if body.get("kind") in ("보유", "관심") else "관심"

    def num(v):
        try:
            return float(str(v).replace(",", "")) if v not in (None, "") else None
        except ValueError:
            raise HTTPException(400, f"숫자가 아닙니다: {v}")
    ctx().store.upsert_watch(code, name, kind, num(body.get("qty")), num(body.get("avg_price")),
                             (body.get("memo") or "")[:200])
    return {"ok": True, "code": code, "name": name}


@app.delete("/api/watch/{code}")
def api_watch_del(request: Request, code: str):
    _auth_json(request, True)
    ctx().store.remove_watch(code)
    return {"ok": True}


@app.get("/api/stock/{code}/news")
def api_news(request: Request, code: str, force: int = 0):
    _auth_json(request)
    try:
        code = normalize_code(code)
    except ValueError:
        raise HTTPException(404)
    return data.news(ctx(), code, bool(force))


@app.post("/api/apis/{api_id}/test")
def api_test(request: Request, api_id: str):
    _auth_json(request, True)
    if api_id not in BY_ID:
        raise HTTPException(404)
    try:
        msg = run_test(ctx(), api_id)
        return {"ok": True, "message": msg}
    except ApiError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


@app.post("/api/apis/{api_id}/keys")
async def api_keys(request: Request, api_id: str):
    _auth_json(request, True)
    if not secure_context(request):
        raise HTTPException(403, "키 변경은 HTTPS나 SSH 터널(localhost)로 접속했을 때만 할 수 있습니다.")
    a = BY_ID.get(api_id)
    if not a:
        raise HTTPException(404)
    body = await request.json()
    allowed = {k for k, _ in a["keys"]}
    updates = {}
    for k, v in (body.get("values") or {}).items():
        if k not in allowed or k not in KNOWN_KEYS:
            raise HTTPException(400, f"이 API의 키가 아닙니다: {k}")
        v = (v or "").strip()
        if v:
            updates[k] = v
    if not updates:
        raise HTTPException(400, "바꿀 값이 없습니다 (빈 칸은 그대로 둡니다)")
    write_env_values(ctx().settings.env_file, updates)
    with ctx().store.db() as con:
        con.execute("INSERT INTO key_changes(at, api, keys, source) VALUES (?,?,?,?)",
                    (iso_kst(), api_id, ",".join(sorted(updates)), request.client.host if request.client else ""))
    if api_id == "kis":
        ctx().kis.clear_token()
    reload_ctx()
    return {"ok": True, "changed": sorted(updates)}


@app.post("/api/jobs/{job}")
def api_job(request: Request, job: str):
    _auth_json(request, True)
    if job not in JOBS_FROM_UI:
        raise HTTPException(404)
    if ctx().settings.demo:
        return {"ok": False, "message": "샘플 데이터 모드에서는 실행하지 않습니다."}
    log = ctx().store.data_dir / "first_week.txt" if job == "first-week-check" else None
    args = ["nice", "-n", "15", sys.executable, "-m", "bkkstock", *JOBS_FROM_UI[job]]
    out = open(log, "w") if log else subprocess.DEVNULL
    subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                     cwd=Path(__file__).resolve().parents[2])
    return {"ok": True, "message": f"{job} 작업을 백그라운드로 시작했습니다. 몇 분 뒤 새로고침하세요."}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "message": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return RedirectResponse("/login", 303)
    return HTMLResponse(f"<p>{exc.status_code} {exc.detail}</p>", status_code=exc.status_code)
