"""레드팀 재현 테스트 (2026-11 내부 점검) — 실제 waitress를 거친다 (앱의 cli.main(["web"])을 하위 프로세스로 띄움).

저장소 tests/ 로 옮길 때: SNAP 기본값(저장소 루트)만 쓰면 된다.
실행: BKK_SNAPSHOT=<코드 폴더> python -m pytest -q -p no:cacheprovider test_redteam_regress.py
Caddy 흉내: 127.0.0.1에서 접속하며 X-Forwarded-For/Proto 를 Caddy처럼 붙인다.
'믿지 않는 상대'는 127.0.0.2 에서 접속해 흉내 낸다(리눅스).
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import json
import os
import secrets
import socket
import subprocess
import sys
import textwrap
import time
import zlib
from pathlib import Path

import httpx
import pytest

SNAP = Path(os.environ.get("BKK_SNAPSHOT", Path(__file__).resolve().parents[1]))
PW = "pw-" + secrets.token_hex(8)

LAUNCH = textwrap.dedent("""
    import os, sys
    snap, port = sys.argv[1], sys.argv[2]
    sys.path.insert(0, snap); os.chdir(snap)
    from bkkstock import cli
    cli.main(["web", "--host", "127.0.0.1", "--port", port])
""")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(tmp_path):
    secret = secrets.token_hex(32)
    (tmp_path / ".env").write_text(f"DASH_PASSWORD={PW}\nDASH_SECRET={secret}\n")
    for d in ("data", "instance", "content"):
        (tmp_path / d).mkdir()
    env = dict(os.environ, BKK_STOCK_ENV=str(tmp_path / ".env"), BKK_STOCK_DATA=str(tmp_path / "data"),
               BKK_STOCK_DEMO="1", ARBI_INSTANCE=str(tmp_path / "instance"), GIBALHAE_CONTENT=str(tmp_path / "content"))
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", LAUNCH, str(SNAP), str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/login", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("서버가 안 뜸")
    yield {"base": base, "secret": secret, "data": tmp_path / "data"}
    proc.kill()
    proc.wait()


# ---------- 도우미 ----------
def caddy(ip: str, proto: str = "https") -> dict:
    return {"X-Forwarded-For": ip, "X-Forwarded-Proto": proto}


def post_login(base, headers=None, cookie=None, pw=PW, local=None):
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    tr = httpx.HTTPTransport(local_address=local) if local else None
    with httpx.Client(transport=tr) as c:          # 매번 새 클라이언트: 쿠키 자동 보관 없음
        return c.post(base + "/login", data={"password": pw}, headers=h)


def set_cookies(r) -> dict[str, str]:
    out = {}
    for sc in r.headers.get_list("set-cookie"):
        name, _, rest = sc.partition("=")
        out[name] = sc
    return out


def cookie_value(sc_line: str) -> str:
    return sc_line.partition("=")[2].split(";")[0]


def session_cookie(r) -> tuple[str, str]:
    for name, line in set_cookies(r).items():
        if name.endswith("bkkhub"):
            return name, cookie_value(line)
    raise AssertionError("세션 쿠키 없음")


def device_cookie(r) -> tuple[str, str] | None:
    for name, line in set_cookies(r).items():
        if name.endswith("bkkdev"):
            return name, cookie_value(line)
    return None


def csrf_from_session(val: str) -> str:
    """Flask 세션 쿠키는 서명만 하고 암호화하지 않는다 → 페이지를 안 그려도 csrf 를 읽을 수 있다."""
    payload = val.split(".")[1] if val.startswith(".") else val.split(".")[0]
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    if val.startswith("."):
        raw = zlib.decompress(raw)
    return json.loads(raw).get("csrf", "")


def key_change(base, headers, cookie_name, cookie_val):
    """빈 값으로 키 변경 → 403(보안 문맥 아님) / 400(보안 문맥: '바꿀 값이 없습니다')."""
    h = {**headers, "Cookie": f"{cookie_name}={cookie_val}", "X-CSRF": csrf_from_session(cookie_val)}
    return httpx.post(base + "/api/apis/dart/keys", json={"values": {}}, headers=h).status_code


# ---------- 1. 프록시 주소: 인터넷 사용자마다 따로 보이는가 ----------
def test_lockout_is_per_client_behind_caddy(server):
    b = server["base"]
    for _ in range(5):
        assert post_login(b, caddy("203.0.113.10"), pw="wrong").status_code == 401
    assert post_login(b, caddy("198.51.100.7")).status_code == 302      # 주인은 안 잠긴다 (현재 main: 429)


def test_untrusted_peer_cannot_rotate_xff(server):
    b = server["base"]
    try:
        socket.create_connection(("127.0.0.1", int(b.rsplit(":", 1)[1])), source_address=("127.0.0.2", 0)).close()
    except OSError:
        pytest.skip("127.0.0.2 사용 불가")
    for i in range(5):
        post_login(b, {"X-Forwarded-For": f"203.0.113.{100 + i}"}, pw="wrong", local="127.0.0.2")
    # XFF를 무시했다면 6번째는 127.0.0.2 하나의 버킷으로 잠김
    assert post_login(b, {"X-Forwarded-For": "203.0.113.200"}, local="127.0.0.2").status_code == 429


def test_concurrent_burst_respects_ip_limit(server):
    b = server["base"]
    with cf.ThreadPoolExecutor(30) as ex:
        codes = list(ex.map(lambda _: post_login(b, caddy("203.0.113.77"), pw="wrong").status_code, range(30)))
    assert codes.count(401) <= 5


# ---------- 2. 키 변경 보안 문맥 ----------
def test_key_change_closed_when_proxy_says_http(server):
    b = server["base"]
    r = post_login(b)                                               # 터널로 로그인
    name, val = session_cookie(r)
    assert key_change(b, caddy("198.51.100.7", "http"), name, val) == 403          # 현재 main: 400(열림)
    assert key_change(b, {"X-Forwarded-For": "127.0.0.1, 198.51.100.7", "X-Forwarded-Proto": "http"},
                      name, val) == 403                             # XFF에 127.0.0.1 끼워 넣기


def test_key_change_open_via_tunnel_and_https(server):
    b = server["base"]
    r = post_login(b)
    assert key_change(b, {}, *session_cookie(r)) == 400              # SSH 터널은 계속 된다
    r = post_login(b, caddy("198.51.100.8"))
    assert key_change(b, caddy("198.51.100.8"), *session_cookie(r)) == 400


# ---------- 3. 쿠키 ----------
def test_https_cookies_hardened(server):
    b = server["base"]
    r = post_login(b, caddy("198.51.100.9"))
    sc = set_cookies(r)
    sess = next(v for k, v in sc.items() if k.endswith("bkkhub"))
    assert sess.startswith("__Host-bkkhub=") and "Secure" in sess and "Domain" not in sess
    dev = next((v for k, v in sc.items() if k.endswith("bkkdev")), None)
    assert dev and "Secure" in dev
    page = httpx.get(b + "/login", headers=caddy("198.51.100.9"))
    assert "max-age=" in page.headers.get("Strict-Transport-Security", "")


def test_tossed_device_cookie_cannot_lock_owner(server):
    """형제 *.sslip.io 가 Domain=sslip.io; Path=/login 으로 'bkkdev=쓰레기'를 심으면 브라우저는 그것을 먼저 보낸다."""
    b = server["base"]
    r = post_login(b, caddy("198.51.100.20"))
    dev = device_cookie(r)
    assert dev, "기기 쿠키 없음"
    for i in range(30):                                            # 낯선 기기 한도 채우기
        post_login(b, caddy(f"203.0.114.{i}"), pw="wrong")
    tossed = f"bkkdev=garbage; {dev[0]}={dev[1]}"                  # 접두어 없는 이름은 형제 도메인이 심을 수 있다
    assert post_login(b, caddy("198.51.100.20"), cookie=tossed).status_code == 302


def test_duplicate_device_cookie_any_valid_accepted(server):
    """방어 한 겹 더: 같은 이름이 여러 개 와도 그중 유효한 것이 있으면 주인 기기로 본다."""
    b = server["base"]
    r = post_login(b, caddy("198.51.100.21"))
    dev = device_cookie(r)
    for i in range(30):
        post_login(b, caddy(f"203.0.115.{i}"), pw="wrong")
    assert post_login(b, caddy("198.51.100.21"), cookie=f"{dev[0]}=garbage; {dev[0]}={dev[1]}").status_code == 302


def test_pre_fix_session_cookie_rejected(server):
    """수정 전에 Secure 없이 나간(평문으로 샐 수 있었던) 세션 값은 이름만 바꿔도 통하면 안 된다."""
    from flask import Flask
    from flask.sessions import SecureCookieSessionInterface
    a = Flask("old")
    a.secret_key = server["secret"]
    old = SecureCookieSessionInterface().get_signing_serializer(a).dumps({"_permanent": True, "auth": True})
    r = httpx.get(server["base"] + "/stocks", headers={**caddy("192.0.2.50"), "Cookie": f"__Host-bkkhub={old}"})
    assert r.status_code == 302                                    # 지금 수정본: 200 (통과)


# ---------- 4. /healthz 공개 본문 ----------
def test_healthz_public_is_minimal(server):
    r = httpx.get(server["base"] + "/healthz")
    assert r.status_code in (200, 503)
    body = r.json()
    assert set(body) <= {"ok", "codes"}
    assert not any(w in r.text.lower() for w in ("key", "token", "secret", "/home", "ip", "version"))


# ---------- 5. 기발해 동기화: 날짜 폴더 자체가 링크인 경우 ----------
def test_sync_skips_symlinked_day_dir_and_nested_dir(tmp_path):
    sys.path.insert(0, str(SNAP))
    from bkkstock.gibalhae_sync import sync
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.json").write_text('{"k":"FAKE"}')
    src = tmp_path / "src"
    (src / "2026-11-11" / "x").mkdir(parents=True)
    (src / "2026-11-11" / "blog.md").write_text("---\ntitle: t\n---\n")
    (src / "2026-11-12").symlink_to(outside)                       # 날짜 폴더가 통째로 링크
    (src / "2026-11-11" / "build").symlink_to(outside)             # 안쪽 폴더 링크
    copied = sync(src, tmp_path / "dst")
    assert copied == ["2026-11-11/blog.md"]
