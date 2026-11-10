"""보안 점검(2026-11 내부 회의)에서 나온 문제의 재발 방지 테스트."""

import importlib
import logging
import socket
import threading
import time

import httpx
import pytest

from tests.test_web import client, login  # noqa: F401  (fixture 재사용)


# ---------- 로그인 잠금: 남이 틀려도 주인 기기는 안 막힌다 ----------
def test_unknown_device_lockout_is_per_ip(client):  # noqa: F811
    c, _ = client
    for _ in range(5):
        assert c.post("/login", data={"password": "x"}, environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 401
    assert c.post("/login", data={"password": "pw"}, environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 429
    # 다른 주소의 사람은 그대로 들어간다
    assert c.post("/login", data={"password": "pw"}, environ_base={"REMOTE_ADDR": "198.51.100.7"}).status_code == 302


def test_known_device_not_locked_by_strangers(client):  # noqa: F811
    c, appmod = client
    login(c)                                   # 이 기기는 기기 쿠키를 받는다
    c.post("/logout")
    stranger = appmod.app.test_client()
    for i in range(30):                        # 여러 주소로 퍼붓기 → 처음 보는 기기 전체가 잠김
        stranger.post("/login", data={"password": "x"}, environ_base={"REMOTE_ADDR": f"203.0.113.{i}"})
    assert stranger.post("/login", data={"password": "pw"}, environ_base={"REMOTE_ADDR": "198.51.100.8"}).status_code == 429
    assert c.post("/login", data={"password": "pw"}).status_code == 302      # 주인 기기는 통과


def test_lockout_survives_restart(client):  # noqa: F811
    c, appmod = client
    for _ in range(5):
        c.post("/login", data={"password": "x"}, environ_base={"REMOTE_ADDR": "203.0.113.10"})
    importlib.reload(appmod)                   # 재시작 흉내 (실패 기록은 sqlite에 있다)
    appmod.reload_ctx()
    c2 = appmod.app.test_client()
    assert c2.post("/login", data={"password": "pw"}, environ_base={"REMOTE_ADDR": "203.0.113.10"}).status_code == 429


# ---------- 쿠키·헤더 ----------
def test_https_cookie_is_secure_host_prefixed(client):  # noqa: F811
    c, _ = client
    r = c.post("/login", data={"password": "pw"}, base_url="https://localhost")
    cookies = r.headers.getlist("Set-Cookie")
    sess = [x for x in cookies if x.startswith("__Host-bkkhub=")]
    assert sess and "Secure" in sess[0] and "HttpOnly" in sess[0] and "Domain" not in sess[0]
    assert any(x.startswith("__Host-bkkdev=") and "Secure" in x for x in cookies)
    page = c.get("/", base_url="https://localhost")
    assert page.status_code == 200 and "max-age=" in page.headers.get("Strict-Transport-Security", "")


def test_csp_and_nonce(client):  # noqa: F811
    c, _ = client
    login(c)
    r = c.get("/media")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    html = r.get_data(as_text=True)
    assert f'<script nonce="{nonce}">' in html                      # 인라인 스크립트는 nonce가 있어야 돈다
    assert "<script>" not in html


def test_csrf_token_required(client):  # noqa: F811
    c, _ = client
    login(c)
    tok = c.environ_base.pop("HTTP_X_CSRF")
    assert c.post("/api/watch", json={"code": "005930"}).status_code == 403
    assert c.post("/api/watch", json={"code": "005930"}, headers={"X-CSRF": tok}).status_code == 200


def test_bad_code_delete_is_400(client):  # noqa: F811
    c, _ = client
    login(c)
    assert c.delete("/api/watch/zz").status_code == 400


# ---------- 키가 로그·오류 문구에 남지 않는다 ----------
def test_redact_hides_keys():
    from bkkstock.redact import redact
    s = redact("GET https://opendart.fss.or.kr/api/list.json?crtfc_key=ABCDEF0123456789&page_no=1 "
               "serviceKey=xyz%2Fabc POST https://api.telegram.org/bot123456:AAE-secret_tok/sendMessage AUTH_KEY=k1")
    for secret in ("ABCDEF0123456789", "xyz%2Fabc", "AAE-secret_tok", "k1"):
        assert secret not in s
    assert "page_no=1" in s


def test_httpx_logs_redacted(capsys):
    from bkkstock.redact import RedactFilter, setup_logging
    root = logging.getLogger()
    old = list(root.handlers)
    for h in old:
        root.removeHandler(h)
    try:
        setup_logging()
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        logging.getLogger("bkkstock").warning("요청 실패 https://x/api?crtfc_key=SECRETKEY123&a=1")
        err = capsys.readouterr().err
        assert "SECRETKEY123" not in err and "crtfc_key=•••" in err
        assert any(isinstance(f, RedactFilter) for h in root.handlers for f in h.filters)
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in old:
            root.addHandler(h)


def test_api_error_message_redacted():
    from bkkstock.clients.base import ApiError
    e = ApiError("dart", "network", "failed for url https://x/list.json?crtfc_key=TOPSECRET99")
    assert "TOPSECRET99" not in str(e) and "TOPSECRET99" not in e.message


# ---------- 뉴스 링크·기발해 동기화 ----------
def test_news_links_only_http():
    from bkkstock.clients.news import _safe_link
    assert _safe_link("javascript:alert(1)") == ""
    assert _safe_link(" https://news.example/a ") == "https://news.example/a"


def test_sync_skips_symlinks(tmp_path):
    from bkkstock.gibalhae_sync import sync
    secret = tmp_path / "secret.env"
    secret.write_text("FAKE_SECRET=abc123")
    src = tmp_path / "src" / "2026-11-10"
    (src / "build").mkdir(parents=True)
    (src / "blog.md").write_text("---\ntitle: t\n---\n본문")
    (src / "build" / "x.txt").symlink_to(secret)
    dst = tmp_path / "dst"
    copied = sync(tmp_path / "src", dst)
    assert "2026-11-10/blog.md" in copied and not (dst / "2026-11-10" / "build" / "x.txt").exists()


# ---------- 실제 waitress를 거쳐 프록시 주소가 제대로 보이는지 ----------
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_waitress_trusts_only_local_proxy_headers():
    from flask import Flask, jsonify, request
    from waitress.server import create_server

    from bkkstock import cli
    probe = Flask("probe")

    @probe.get("/who")
    def who():
        return jsonify(addr=request.remote_addr, scheme=request.scheme)

    seen = {}

    def fake_serve(app, **kw):
        seen.update(kw)

    import waitress
    orig = waitress.serve
    waitress.serve = fake_serve
    try:
        import bkkstock.web.app  # noqa: F401  (cli가 import)
        cli.main(["web", "--port", "1"])
    finally:
        waitress.serve = orig
    assert seen["trusted_proxy"] == "127.0.0.1" and "x-forwarded-for" in seen["trusted_proxy_headers"]
    kw = {k: seen[k] for k in ("trusted_proxy", "trusted_proxy_count", "trusted_proxy_headers",
                               "clear_untrusted_proxy_headers")}
    port = _free_port()
    srv = create_server(probe, host="127.0.0.1", port=port, **kw)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    try:
        for _ in range(50):
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/who", headers={"X-Forwarded-For": "203.0.113.5",
                                                                     "X-Forwarded-Proto": "https"}, timeout=2)
                break
            except httpx.TransportError:
                time.sleep(0.05)
        assert r.json() == {"addr": "203.0.113.5", "scheme": "https"}     # Caddy(127.0.0.1)가 붙인 값을 믿는다
        r = httpx.get(f"http://127.0.0.1:{port}/who", timeout=2)
        assert r.json() == {"addr": "127.0.0.1", "scheme": "http"}       # SSH 터널(헤더 없음)은 로컬 그대로
    finally:
        srv.close()


def test_tossed_garbage_device_cookie_does_not_lock_owner(client):  # noqa: F811
    c, appmod = client
    login(c)
    c.post("/logout")
    stranger = appmod.app.test_client()
    for i in range(25):
        stranger.post("/login", data={"password": "x"}, environ_base={"REMOTE_ADDR": f"203.0.113.{i}"})
    good = c.get_cookie("bkkdev").value
    # 형제 *.sslip.io가 심은 같은 이름의 가짜 쿠키가 앞에 와도 진짜가 하나 있으면 주인 기기로 인정
    r = c.post("/login", data={"password": "pw"}, headers={"Cookie": f"bkkdev=garbage; bkkdev={good}"})
    assert r.status_code == 302


def test_old_session_version_rejected(client):  # noqa: F811
    c, _ = client
    login(c)
    with c.session_transaction() as sess:
        sess["sv"] = 1                       # 보안 강화 전 세션
    assert c.get("/").status_code == 302


def test_healthz_public_body_is_minimal(client, monkeypatch):  # noqa: F811
    c, appmod = client
    appmod._health_cache.update(t=0.0, v=None)
    r = c.get("/healthz")
    assert set(r.get_json()) <= {"ok", "codes"}
    login(c)
    assert "time" in c.get("/api/health").get_json()


def test_unlock_clears_lockout(client, capsys):  # noqa: F811
    c, appmod = client
    for _ in range(5):
        c.post("/login", data={"password": "x"}, environ_base={"REMOTE_ADDR": "203.0.113.50"})
    from bkkstock import cli
    cli.main(["unlock"])
    assert c.post("/login", data={"password": "pw"}, environ_base={"REMOTE_ADDR": "203.0.113.50"}).status_code == 302
