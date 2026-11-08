import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setenv("BKK_STOCK_ENV", str(settings.env_file))
    monkeypatch.setenv("BKK_STOCK_DATA", str(settings.data_dir))
    from bkkstock.config import get_settings
    get_settings(reload=True)
    import importlib
    from bkkstock.web import app as appmod
    importlib.reload(appmod)
    appmod.reload_ctx()
    return TestClient(appmod.app), appmod


def login(c):
    r = c.post("/login", data={"password": "pw"}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/stocks").text
    return page.split('name="csrf" content="')[1].split('"')[0]


def test_requires_login(client):
    c, _ = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/search?q=a").status_code == 401


def test_wrong_password_and_lockout(client):
    c, _ = client
    for _ in range(5):
        assert c.post("/login", data={"password": "x"}).status_code == 401
    assert c.post("/login", data={"password": "pw"}).status_code == 429


def test_pages_render_empty_state(client):
    c, _ = client
    login(c)
    for url in ("/", "/stocks", "/apis", "/ops", "/stocks/005930"):
        r = c.get(url)
        assert r.status_code == 200, url
    assert "아직 일일 수집 기록이 없습니다" in c.get("/").text


def test_csrf_required(client):
    c, _ = client
    login(c)
    r = c.post("/api/watch", json={"code": "005930"})
    assert r.status_code == 403


def test_watch_add_and_delete(client):
    c, appmod = client
    tok = login(c)
    r = c.post("/api/watch", json={"code": "5930", "name": "삼성전자", "kind": "보유", "qty": "10", "avg_price": "70,000"},
               headers={"X-CSRF": tok})
    assert r.json()["code"] == "005930"
    assert "삼성전자" in c.get("/stocks").text
    assert c.delete("/api/watch/005930", headers={"X-CSRF": tok}).json()["ok"]
    assert appmod.ctx().store.watchlist() == []


def test_key_change_blocked_without_secure_context(client):
    c, appmod = client
    tok = login(c)
    r = c.post("/api/apis/dart/keys", json={"values": {"DART_API_KEY": "abc"}}, headers={"X-CSRF": tok})
    assert r.status_code == 403
    assert "DART_API_KEY" not in appmod.ctx().settings.env_file.read_text()


def test_key_change_allowed_over_https_and_never_echoed(client):
    c, appmod = client
    tok = login(c)
    r = c.post("https://testserver/api/apis/dart/keys", json={"values": {"DART_API_KEY": "secretvalue1234"}},
               headers={"X-CSRF": tok})
    assert r.status_code == 200 and r.json()["changed"] == ["DART_API_KEY"]
    page = c.get("/apis").text
    assert "secretvalue1234" not in page and "1234" in page
    # 다른 API의 키 이름은 못 넣는다
    r = c.post("https://testserver/api/apis/dart/keys", json={"values": {"DASH_PASSWORD": "x"}}, headers={"X-CSRF": tok})
    assert r.status_code == 400


def test_healthz_has_no_secrets(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200 and "pw" not in r.text
