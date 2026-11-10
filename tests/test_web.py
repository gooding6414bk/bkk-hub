import importlib

import pytest


@pytest.fixture
def client(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("BKK_STOCK_ENV", str(settings.env_file))
    monkeypatch.setenv("BKK_STOCK_DATA", str(settings.data_dir))
    monkeypatch.setenv("ARBI_INSTANCE", str(tmp_path / "instance"))
    monkeypatch.setenv("GIBALHAE_CONTENT", str(tmp_path / "gb-content"))
    from bkkstock.config import get_settings
    get_settings(reload=True)
    from bkkstock.web import app as appmod
    importlib.reload(appmod)
    appmod.reload_ctx()
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def login(c):
    r = c.post("/login", data={"password": "pw"})
    assert r.status_code == 302


def test_requires_login(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["location"]
    assert c.get("/api/search?q=a").status_code == 401
    assert c.get("/gibalhae").status_code == 302                  # 기발해도 같은 로그인


def test_wrong_password_and_lockout(client):
    c, _ = client
    for _ in range(5):
        assert c.post("/login", data={"password": "x"}).status_code == 401
    assert c.post("/login", data={"password": "pw"}).status_code == 429


def test_login_redirects_to_next_but_not_offsite(client):
    c, _ = client
    r = c.post("/login?next=/stocks", data={"password": "pw"})
    assert r.headers["location"].endswith("/stocks")
    c2, _ = client
    r = c.post("/login?next=//evil.example", data={"password": "pw"})
    assert r.headers["location"].endswith("/")


def test_pages_render_empty_state(client):
    c, _ = client
    login(c)
    for url in ("/", "/stocks", "/apis", "/ops", "/stocks/005930", "/gibalhae", "/media"):
        r = c.get(url)
        assert r.status_code == 200, url
    home = c.get("/").get_data(as_text=True)
    assert "아직 일일 수집 기록이 없습니다" in home and 'href="/media"' in home and "📈 주식" in home
    assert "건우<b>주식</b>" in home and "bkk hub" not in home and "bkk<b>hub" not in home
    for url in ("/", "/stocks", "/apis", "/ops", "/login"):     # 메인 화면들에 콘텐츠 이름이 드러나지 않는다
        assert "기발해" not in c.get(url).get_data(as_text=True), url


def test_cross_site_post_blocked(client):
    c, _ = client
    login(c)
    r = c.post("/api/watch", json={"code": "005930"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/gibalhae/keys", json={"json2video": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_watch_add_and_delete(client):
    c, appmod = client
    login(c)
    h = {"Origin": "http://localhost"}
    r = c.post("/api/watch", json={"code": "5930", "name": "삼성전자", "kind": "보유", "qty": "10", "avg_price": "70,000"}, headers=h)
    assert r.get_json()["code"] == "005930"
    assert "삼성전자" in c.get("/stocks").get_data(as_text=True)
    assert c.delete("/api/watch/005930", headers=h).get_json()["ok"]
    assert appmod.ctx().store.watchlist() == []
    assert c.post("/api/watch", json={"code": "5930", "qty": "abc"}, headers=h).status_code == 400


def test_key_change_blocked_without_secure_context(client):
    c, appmod = client
    login(c)
    r = c.post("/api/apis/dart/keys", json={"values": {"DART_API_KEY": "abc"}},
               environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 403
    assert "DART_API_KEY" not in appmod.ctx().settings.env_file.read_text()


def test_key_change_allowed_over_https_and_never_echoed(client):
    c, appmod = client
    login(c)
    r = c.post("/api/apis/dart/keys", json={"values": {"DART_API_KEY": "secretvalue1234"}},
               base_url="https://localhost", environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 200 and r.get_json()["changed"] == ["DART_API_KEY"]
    page = c.get("/apis").get_data(as_text=True)
    assert "secretvalue1234" not in page and "1234" in page
    r = c.post("/api/apis/dart/keys", json={"values": {"DASH_PASSWORD": "x"}}, base_url="https://localhost")
    assert r.status_code == 400


def test_ssh_tunnel_counts_as_secure(client):
    c, _ = client
    login(c)
    r = c.post("/api/apis/telegram/keys", json={"values": {"TELEGRAM_CHAT_ID": "123"}},
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200


def test_healthz_has_no_secrets(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200 and "pw" not in r.get_data(as_text=True)


def test_content_not_shown_on_dashboard(client, tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    from pipeline import common as gc       # 기발해 모듈은 경로를 처음 import 때 정하므로 테스트에서 바꿔 준다
    c, _ = client
    src = Path(__file__).resolve().parents[1] / "gibalhae" / "content" / "_example"
    shutil.copytree(src, tmp_path / "gb-content" / "2026-11-08")
    monkeypatch.setattr(gc, "CONTENT", str(tmp_path / "gb-content"))
    login(c)
    home = c.get("/").get_data(as_text=True)
    assert "침대" not in home and "기발해" not in home and "승인 대기" not in home   # 메인에는 영상 콘텐츠를 두지 않는다
    page = c.get("/media").get_data(as_text=True)
    assert "🎬 영상" in page and "기발해" not in page.split("<main>")[0]


def test_gibalhae_sync_keeps_server_edits_but_follows_untouched_files(tmp_path):
    from bkkstock.gibalhae_sync import sync
    src = tmp_path / "repo"
    (src / "2026-11-08").mkdir(parents=True)
    (src / "2026-11-08" / "blog.md").write_text("status: draft")
    (src / "2026-11-08" / "shorts.md").write_text("status: draft")
    dst = tmp_path / "server"
    assert sorted(sync(src, dst)) == ["2026-11-08/blog.md", "2026-11-08/shorts.md"]
    assert sync(src, dst) == []                                         # 바뀐 것 없음
    # 서버 대시보드에서 블로그를 승인 (서버 편집)
    (dst / "2026-11-08" / "blog.md").write_text("status: approved (서버)")
    # 채팅으로 쇼츠를 승인해서 저장소 쪽 두 파일이 바뀜
    (src / "2026-11-08" / "blog.md").write_text("status: approved (저장소)")
    (src / "2026-11-08" / "shorts.md").write_text("status: approved (저장소)")
    (src / "2026-11-08" / "new.png").write_text("img")
    assert sorted(sync(src, dst)) == ["2026-11-08/new.png", "2026-11-08/shorts.md"]
    assert (dst / "2026-11-08" / "blog.md").read_text() == "status: approved (서버)"      # 서버 편집 유지
    assert (dst / "2026-11-08" / "shorts.md").read_text() == "status: approved (저장소)"  # 손대지 않은 파일은 따라감
