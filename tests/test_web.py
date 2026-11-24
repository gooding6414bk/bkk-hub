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


def login(c, base_url="http://localhost"):
    r = c.post("/login", data={"password": "pw"}, base_url=base_url)
    assert r.status_code == 302
    with c.session_transaction(base_url=base_url) as sess:          # 화면의 <meta name="csrf">와 같은 값 — 이후 요청에 자동으로 붙인다
        c.environ_base["HTTP_X_CSRF"] = sess["csrf"]


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
    for url in ("/", "/stocks", "/apis", "/ops", "/knowledge", "/stocks/005930", "/gibalhae", "/media"):
        r = c.get(url)
        assert r.status_code == 200, url
    home = c.get("/").get_data(as_text=True)
    assert "아직 일일 수집 기록이 없습니다" in home and 'href="/stocks"' in home and ">주식<" in home
    assert 'href="/media"' not in home and "🎬" not in home          # 영상 메뉴는 보드에서 뺐다 (주소는 그대로 열림)
    assert "건우<b>주식</b>" in home and "bkk hub" not in home and "bkk<b>hub" not in home
    for url in ("/", "/stocks", "/apis", "/ops", "/knowledge", "/login"):     # 메인 화면들에 콘텐츠 이름이 드러나지 않는다
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
    login(c, base_url="https://localhost")
    r = c.post("/api/apis/dart/keys", json={"values": {"DART_API_KEY": "secretvalue1234"}},
               base_url="https://localhost", environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 200 and r.get_json()["changed"] == ["DART_API_KEY"]
    page = c.get("/apis", base_url="https://localhost").get_data(as_text=True)
    assert "secretvalue1234" not in page and "1234" in page
    r = c.post("/api/apis/dart/keys", json={"values": {"DASH_PASSWORD": "x"}}, base_url="https://localhost")
    assert r.status_code == 400


def test_ssh_tunnel_counts_as_secure(client):
    c, _ = client
    login(c)
    r = c.post("/api/apis/telegram/keys", json={"values": {"TELEGRAM_CHAT_ID": "123"}},
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200


def test_healthz_has_no_secrets(client, monkeypatch):
    import datetime as dt
    from bkkstock.jobs import watchdog
    from bkkstock.timeutil import KST
    c, _ = client
    monkeypatch.setattr(watchdog, "now_kst", lambda: dt.datetime(2026, 11, 10, 10, 0, tzinfo=KST))   # 시각 고정
    r = c.get("/healthz")
    assert r.status_code == 200 and "pw" not in r.get_data(as_text=True)
    c2, appmod = client
    st = appmod.ctx().store
    rid = st.start_run("daily", dt.date(2026, 11, 9))
    st.finish_run(rid, "ok", {})
    appmod._health_cache.update(t=0.0, v=None)
    monkeypatch.setattr(watchdog, "now_kst", lambda: dt.datetime(2026, 11, 10, 22, 30, tzinfo=KST))
    r = c.get("/healthz")
    assert r.status_code == 503 and r.get_json() == {"ok": False, "codes": ["daily_failed"]}   # 22시 뒤 그날 기록 없음


def test_stock_page_with_data_shows_new_sections(client, monkeypatch):
    import datetime as dt
    import pandas as pd
    from bkkstock.timeutil import trading_days_back
    from tests.conftest import make_prices
    c, appmod = client
    st = appmod.ctx().store
    days = trading_days_back(dt.date(2026, 11, 6), 30)
    make_prices(st, days, ["000001", "000002", "000003"])
    for d in days:
        st.write_clean("investor", d, pd.DataFrame([{"date": d.strftime("%Y%m%d"), "code": "000001", "stck_clpr": 10000,
                                                     "frgn_ntby_tr_pbmn": 100, "frgn_ntby_qty": 10000,
                                                     "orgn_ntby_tr_pbmn": -50, "prsn_ntby_tr_pbmn": -50}]))
    st.upsert_watch("000001", "종목000001", "보유", 10, 9000, "")
    login(c)
    from bkkstock.timeutil import KST
    from bkkstock.web import data as datamod
    # 금요일(11/06) 저녁에 본다 → 예측 대상 11/09(월) 장 전이라 숫자가 보여야 한다
    monkeypatch.setattr(datamod, "now_kst", lambda: dt.datetime(2026, 11, 6, 21, 0, tzinfo=KST))
    page = c.get("/stocks/000001").get_data(as_text=True)
    for word in ("내일 예측가", "종가 보통 범위", "이동평균", "지수 대비 성과", "연속 순매수", "거래대금 대비", "성적표", "참고값",
                 "위험 표시", "수정주가"):
        assert word in page, word
    assert "방향 적중" not in page                     # 기준선 없는 적중률은 보이지 않는다
    # 예측 대상일 장이 이미 열렸으면 지난 계산이라 숫자를 숨긴다
    monkeypatch.setattr(datamod, "now_kst", lambda: dt.datetime(2026, 11, 9, 10, 0, tzinfo=KST))
    assert "장이 이미 시작돼" in c.get("/stocks/000001").get_data(as_text=True)
    home = c.get("/").get_data(as_text=True)
    assert "오늘 볼 것" in home and "보유 평가금액" in home and "편집" in home
    assert "내일 보통 범위" not in home and "많이 오른 종목" not in home and "거래대금 상위" not in home
    news = c.get("/api/stock/000001/news").get_json()
    assert "summary" in news or "error" in news


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


def _live_setup(client, monkeypatch, handler):
    import datetime as dt
    import httpx
    from bkkstock.timeutil import trading_days_back
    from bkkstock.web import data as datamod
    from tests.conftest import make_prices
    c, appmod = client
    cx = appmod.ctx()
    days = trading_days_back(dt.date(2026, 11, 6), 30)
    make_prices(cx.store, days, ["000001", "000002"])
    cx.store.upsert_watch("000001", "종목000001", "보유", 10, 9000, "")
    cx.store.upsert_watch("000002", "종목000002", "관심", None, None, "")
    cx.settings.values.update(KIS_APP_KEY="APPKEY123456", KIS_APP_SECRET="SECRET")
    cx.kis.http = httpx.Client(transport=httpx.MockTransport(handler))
    datamod._live_cache.clear()
    datamod._live_state.update(market="UN", error=None, error_t=0.0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    login(c)
    return c, cx, datamod


def _kis_handler(calls, reject_un=False, fail=False):
    import httpx

    def handler(req: httpx.Request):
        if req.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "T", "access_token_token_expired": "2099-01-01 00:00:00"})
        if fail:
            raise httpx.ConnectError("down")
        mkt = req.url.params["FID_COND_MRKT_DIV_CODE"]
        calls.append((req.url.params["FID_INPUT_ISCD"], mkt))
        assert req.headers["tr_id"] == "FHKST01010100"          # 조회(현재가)만 — 주문 tr_id 아님
        if reject_un and mkt == "UN":
            return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "시장 코드 오류"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {
            "stck_prpr": "12345", "prdy_ctrt": "-2.50", "prdy_vrss": "-316", "stck_oprc": "12600",
            "stck_hgpr": "12700", "stck_lwpr": "12300", "acml_vol": "1000", "acml_tr_pbmn": "12345000000"}})
    return handler


def test_refresh_shows_live_price_and_caches(client, monkeypatch):
    calls = []
    c, cx, datamod = _live_setup(client, monkeypatch, _kis_handler(calls))
    home = c.get("/").get_data(as_text=True)
    assert "12,345" in home and "현재가" in home and "새로고침" in home
    assert sorted(calls) == [("000001", "UN"), ("000002", "UN")]
    # 보유 손익도 현재가 기준: (12345 - 9000) × 10
    assert "33,450" in home
    c.get("/stocks")                                              # 20초 안 → 다시 묻지 않는다
    assert len(calls) == 2
    t0 = datamod.time.time()
    monkeypatch.setattr(datamod.time, "time", lambda: t0 + datamod.LIVE_TTL + 1)
    page = c.get("/stocks/000001").get_data(as_text=True)         # 새로고침 = 새로 조회
    assert len(calls) == 3
    assert "12,345" in page and "KRX+넥스트레이드" in page and "고가 12,700" in page


def test_live_price_falls_back_to_krx_market(client, monkeypatch):
    calls = []
    c, cx, datamod = _live_setup(client, monkeypatch, _kis_handler(calls, reject_un=True))
    page = c.get("/stocks/000001").get_data(as_text=True)
    assert "12,345" in page and "(KRX)" in page
    assert calls == [("000001", "UN"), ("000001", "J")]
    c.get("/stocks/000002")
    assert calls[-1] == ("000002", "J")                           # 한 번 바꾸면 계속 KRX로


def test_live_price_failure_keeps_page_working(client, monkeypatch):
    calls = []
    c, cx, datamod = _live_setup(client, monkeypatch, _kis_handler(calls, fail=True))
    home = c.get("/")
    assert home.status_code == 200
    text = home.get_data(as_text=True)
    assert "현재가 조회 오류" in text and "종가" in text and "APPKEY" not in text and "SECRET" not in text
    assert "현재가를 받지 못해" in c.get("/stocks/000001").get_data(as_text=True)


def test_no_kis_key_shows_close_without_calling(client, monkeypatch):
    calls = []
    c, cx, datamod = _live_setup(client, monkeypatch, _kis_handler(calls))
    cx.settings.values.pop("KIS_APP_KEY")
    assert "키가 없어 종가로" in c.get("/").get_data(as_text=True)
    assert calls == []


def test_knowledge_page(client):
    c, _ = client
    assert c.get("/knowledge").status_code == 302                  # 로그인 필요
    login(c)
    page = c.get("/knowledge").get_data(as_text=True)
    for word in ("IRP", "ISA", "퇴직연금", "노령연금", "증권거래세", "세액공제", "확인 필요", "국민연금공단", "건강보험"):
        assert word in page, word
    assert 'href="/knowledge"' in c.get("/").get_data(as_text=True)
    assert 'href="/knowledge/realestate"' in page and "💰 금융" in page


def test_realestate_knowledge_page(client):
    c, _ = client
    assert c.get("/knowledge/realestate").status_code == 302         # 로그인 필요
    login(c)
    page = c.get("/knowledge/realestate").get_data(as_text=True)
    for word in ("🏠 부동산", "취득세", "재산세", "종합부동산세", "양도소득세", "장기보유특별공제", "LTV", "DSR", "토지거래허가구역",
                 "확인 필요", "위택스", "확인 2026-11-24"):
        assert word in page, word
    assert 'class="on"' in page and "기발해" not in page            # 상단 메뉴는 '지식'이 켜짐


def test_live_price_temporary_error_does_not_switch_market(client, monkeypatch):
    import httpx
    calls = []
    state = {"n": 0}
    base = _kis_handler(calls)

    def handler(req):
        if req.url.path != "/oauth2/tokenP":
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(500, text="server error")        # 잠깐 난 서버 오류
        return base(req)
    c, cx, datamod = _live_setup(client, monkeypatch, handler)
    c.get("/stocks/000001")
    assert datamod._live_state["market"] == "UN"                         # KRX 로 바꾸지 않는다
    datamod._live_state.update(error=None, error_t=0.0)
    datamod._live_cache.clear()
    assert "KRX+넥스트레이드" in c.get("/stocks/000001").get_data(as_text=True)


def test_live_price_switches_back_to_integrated_after_10_minutes(client, monkeypatch):
    calls = []
    c, cx, datamod = _live_setup(client, monkeypatch, _kis_handler(calls, reject_un=True))
    c.get("/stocks/000001")
    assert datamod._live_state["market"] == "J"
    datamod._live_cache.clear()
    datamod._live_state["j_since"] -= datamod.LIVE_RETRY_UN + 1
    c.get("/stocks/000002")
    assert ("000002", "UN") in calls                                     # 10분 뒤 통합 시세를 다시 시도


def test_live_price_concurrent_request_does_not_wait(client, monkeypatch):
    calls = []
    c, cx, datamod = _live_setup(client, monkeypatch, _kis_handler(calls))
    datamod._live_lock.acquire()                                         # 다른 요청이 조회 중이라고 가정
    try:
        r = datamod.live_quotes(cx, ["000001", "000002"])
    finally:
        datamod._live_lock.release()
    assert calls == [] and r["quotes"] == {} and r["error"] is None      # 기다리지 않고 종가로


def test_kis_token_issued_once_under_concurrency(settings, store):
    import threading
    import time as _t
    import httpx
    from bkkstock.clients.base import RateLimiter
    from bkkstock.clients.kis import KisClient
    n = {"token": 0}

    def handler(req):
        if req.url.path == "/oauth2/tokenP":
            n["token"] += 1
            _t.sleep(0.2)
            return httpx.Response(200, json={"access_token": f"T{n['token']}", "access_token_token_expired": "2099-01-01 00:00:00"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "1"}})
    settings.values.update(KIS_APP_KEY="APPKEY123456", KIS_APP_SECRET="SECRET")
    clients = [KisClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(handler)), limiter=RateLimiter(1000))
               for _ in range(5)]
    ts = [threading.Thread(target=k.token) for k in clients]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert n["token"] == 1


def test_indicators_page_and_home_grid(client):
    import datetime as dt
    from tests.test_macro import _seed_macro
    from bkkstock.timeutil import now_kst
    c, appmod = client
    login(c)
    page = c.get("/knowledge/indicators").get_data(as_text=True)
    assert "📊 지표" in page and "ECOS 키" in page and "다가오는 일정" in page and "매수·매도 신호" in page
    _seed_macro(appmod.ctx().store, now_kst().date())
    page = c.get("/knowledge/indicators").get_data(as_text=True)
    assert "국고채 3년" in page and "1년 중" in page and "1개월" in page
    home = c.get("/").get_data(as_text=True)
    assert "시장 한눈에" in home and 'href="/indicators"' in home
    apis = c.get("/apis").get_data(as_text=True)
    assert "한국은행 ECOS" in apis and "ECOS_API_KEY" in apis


def test_move_calculator_saves_only_on_server(client):
    import json
    c, appmod = client
    assert c.get("/knowledge/realestate/move").status_code == 302
    login(c)
    page = c.get("/knowledge/realestate/move").get_data(as_text=True)
    assert "갈아타기" in page and "토지거래허가구역" in page and "결과" not in page.split("체크리스트")[0].split("</form>")[1]
    r = c.post("/knowledge/realestate/move", data={"csrf": c.environ_base["HTTP_X_CSRF"], "note": "우리 집",
               "sell_price": "20", "buy_cost": "9", "hold_years": "7", "live_years": "7", "exempt_ok": "on",
               "target_price": "30", "cash": "2", "want_loan": "on"})
    assert r.status_code == 302
    page = c.get("/knowledge/realestate/move").get_data(as_text=True)
    assert "모자람" in page and "우리 집" in page and "양도세" in page
    saved = json.loads(appmod.ctx().store.meta_get("move_plan"))
    assert saved["sell_price"] == 20 and saved["note"] == "우리 집"
    assert c.post("/knowledge/realestate/move", data={"sell_price": "1"}, headers={"X-CSRF": ""}).status_code == 403


def test_stock_page_has_chips_fin_and_calendar(client, monkeypatch):
    import datetime as dt
    from tests.conftest import make_prices
    from bkkstock.timeutil import trading_days_back
    c, appmod = client
    st = appmod.ctx().store
    make_prices(st, trading_days_back(dt.date(2026, 11, 6), 60), ["000001", "000002"])
    st.upsert_watch("000001", "종목000001", "보유", 10, 9000, "")
    with st.db() as con:
        con.execute("INSERT INTO disclosures(rcept_no, day, corp_code, corp_name, stock_code, report_nm, rcept_dt, first_seen_at, tags) "
                    "VALUES ('9','2026-11-01','001','가','000001','주주총회소집결의',?, '2026-11-01','')", (dt.date.today().strftime("%Y%m%d"),))
    login(c)
    page = c.get("/stocks/000001").get_data(as_text=True)
    for word in ('href="#s-fin"', 'id="s-fin"', 'id="s-div"', 'href="#s-risk"', "60일", "PER"):
        assert word in page, word
    j = c.get("/api/stock/000001/fin").get_json()
    assert "OpenDART" in j["error"]                                           # 키 없음
    cal = c.get("/stocks/calendar").get_data(as_text=True)
    assert "실적 발표 마감" in cal and "주총" in cal and "⭐ 내 종목" in cal
    assert "🗓 일정" in c.get("/stocks").get_data(as_text=True)


def test_indicator_line_uses_kis_fields(client, monkeypatch):
    import httpx
    calls = []
    base = _kis_handler(calls)

    def handler(req):
        r = base(req)
        if req.url.path.endswith("inquire-price"):
            body = r.json()
            body["output"].update(per="12.34", pbr="1.10", w52_hgpr="13000", w52_lwpr="9000", bstp_kor_isnm="전기·전자",
                                  hts_frgn_ehrt="51.2", hts_avls="7000")
            return httpx.Response(200, json=body)
        return r
    c, cx, datamod = _live_setup(client, monkeypatch, handler)
    page = c.get("/stocks/000001").get_data(as_text=True)
    assert "12.3" in page and "1.10" in page and "전기·전자" in page and "51.2%" in page and "52주" in page


def test_prefs_edit_and_sort(client):
    import json
    c, appmod = client
    assert c.get("/api/prefs").status_code == 401
    login(c)
    p = c.get("/api/prefs").get_json()
    assert p["home"][0] == ["risk", True] and p["watch"]["sort"] == "own"
    # 순서 바꾸기·숨기기, 모르는 키·위젯은 버리고 '오늘 볼 것'은 못 숨김
    r = c.put("/api/prefs", json={"home": [["market", True], ["risk", False], ["pnl", False], ["evil", True]],
                                  "watch": {"sort": "today_up", "view": "compact"}, "x": 1}).get_json()
    home = r["prefs"]["home"]
    assert home[0] == ["market", True] and home[1] == ["risk", True] and home[2] == ["pnl", False]
    assert {w for w, _ in home} == {"risk", "pnl", "watch", "events", "market", "ind", "alerts", "mktrisk", "status"}
    assert r["prefs"]["watch"] == {"sort": "own", "view": "compact"}          # 등락률순 같은 모르는 정렬은 무시
    page = c.get("/").get_data(as_text=True)
    assert 'id="w-pnl"' not in page and page.index('id="w-market"') < page.index('id="w-events"')
    c.put("/api/prefs", json={"watch": {"sort": "name"}})
    assert json.loads(appmod.ctx().store.meta_get("ui.prefs"))["watch"]["sort"] == "name"
    assert c.put("/api/prefs", json={"reset": True}).get_json()["prefs"]["watch"]["sort"] == "own"
    assert c.put("/api/prefs", json={}, headers={"X-CSRF": "bad"}).status_code == 403


def test_settings_and_indicators_menu(client):
    c, _ = client
    login(c)
    s = c.get("/settings").get_data(as_text=True)
    assert "API 연결" in s and "홈 화면 편집" in s and "로그아웃" in s and 'href="/ops"' in s
    ind = c.get("/indicators").get_data(as_text=True)
    assert "📊 지표" in ind and "💰 금융" not in ind                    # 지표는 1급 메뉴 (지식 탭 아님)
    home = c.get("/").get_data(as_text=True)
    for href in ('href="/"', 'href="/stocks"', 'href="/indicators"', 'href="/knowledge"', 'href="/settings"'):
        assert href in home, href
    assert "기발해" not in s and "🎬" not in home
