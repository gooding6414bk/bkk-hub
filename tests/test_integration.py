"""일일 수집 전체를 가짜 API 서버로 끝까지 돌린다 (응답 파싱 경로 확인)."""

import datetime as dt
import io
import json
import zipfile

import httpx
import pytest

from bkkstock.clients.base import RateLimiter
from bkkstock.jobs import daily, report
from bkkstock.jobs.common import make_ctx
from bkkstock.master import KOSDAQ_COLS, KOSDAQ_WIDTHS, KOSPI_COLS, KOSPI_WIDTHS
from tests.test_master import _line

DAY = dt.date(2026, 11, 6)
YMD = "20261106"


def _zip(name, text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(name, text.encode("cp949"))
    return b.getvalue()


KOSPI_ZIP = _zip("kospi_code.mst", "\n".join([
    _line("005930", "삼성전자", KOSPI_COLS, KOSPI_WIDTHS, {"그룹코드": "ST", "상장일자": "19750611"}),
    _line("000660", "SK하이닉스", KOSPI_COLS, KOSPI_WIDTHS, {"그룹코드": "ST", "시장경고": "01"}),
    _line("069500", "KODEX 200", KOSPI_COLS, KOSPI_WIDTHS, {"그룹코드": "EF"}),
]) + "\n")
KOSDAQ_ZIP = _zip("kosdaq_code.mst", _line("086520", "에코프로", KOSDAQ_COLS, KOSDAQ_WIDTHS,
                                            {"증권그룹구분코드": "ST", "관리 종목 여부": "Y"}) + "\n")


def router(req: httpx.Request) -> httpx.Response:
    u = req.url
    if u.host == "new.real.download.dws.co.kr":
        return httpx.Response(200, content=KOSPI_ZIP if "kospi" in u.path else KOSDAQ_ZIP)
    if u.host == "data-dbg.krx.co.kr":
        assert req.headers.get("AUTH_KEY") == "KRXKEY"
        if u.path.endswith("stk_bydd_trd"):
            rows = [{"BAS_DD": YMD, "ISU_CD": "005930", "ISU_NM": "삼성전자", "MKT_NM": "KOSPI", "TDD_CLSPRC": "70,000",
                     "FLUC_RT": "1.20", "ACC_TRDVOL": "10,000,000", "ACC_TRDVAL": "700,000,000,000", "MKTCAP": "400,000,000,000,000"},
                    {"BAS_DD": YMD, "ISU_CD": "KR7000660001", "ISU_NM": "SK하이닉스", "MKT_NM": "KOSPI", "TDD_CLSPRC": "200000",
                     "FLUC_RT": "-0.50", "ACC_TRDVOL": "1000", "ACC_TRDVAL": "200000000", "MKTCAP": "100000000000"}]
        elif u.path.endswith("ksq_bydd_trd"):
            rows = [{"BAS_DD": YMD, "ISU_CD": "086520", "ISU_NM": "에코프로", "MKT_NM": "KOSDAQ", "TDD_CLSPRC": "90000",
                     "FLUC_RT": "3.00", "ACC_TRDVOL": "500", "ACC_TRDVAL": "45000000", "MKTCAP": "9000000000"}]
        else:
            rows = [{"BAS_DD": YMD, "IDX_NM": "코스피", "CLSPRC_IDX": "4,000.00"}]
        return httpx.Response(200, json={"OutBlock_1": rows})
    if u.host == "openapi.koreainvestment.com":
        if u.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "T", "access_token_token_expired": "2099-01-01 00:00:00"})
        p = u.params
        code, mkt = p.get("FID_INPUT_ISCD"), p.get("FID_COND_MRKT_DIV_CODE")
        if u.path.endswith("inquire-daily-itemchartprice"):
            assert mkt == "NX"
            out = [{"stck_bsop_date": YMD, "stck_clpr": "70100", "acml_vol": "3000000", "acml_tr_pbmn": "210000000000"}] if code == "005930" else []
            return httpx.Response(200, json={"rt_cd": "0", "output2": out})
        if u.path.endswith("investor-trade-by-stock-daily"):
            assert mkt == "UN"
            return httpx.Response(200, json={"rt_cd": "0", "output2": [
                {"stck_bsop_date": YMD, "frgn_ntby_tr_pbmn": "1500", "orgn_ntby_tr_pbmn": "-200", "prsn_ntby_tr_pbmn": "-1300"},
                {"stck_bsop_date": "20261105", "frgn_ntby_tr_pbmn": "1", "orgn_ntby_tr_pbmn": "1", "prsn_ntby_tr_pbmn": "-2"}]})
        if u.path.endswith("daily-short-sale"):
            return httpx.Response(200, json={"rt_cd": "0", "output2": [{"stck_bsop_date": YMD, "ssts_cntg_qty": "10"}]})
        if u.path.endswith("daily-credit-balance"):
            return httpx.Response(200, json={"rt_cd": "0", "output": [{"deal_date": YMD, "whol_loan_rmnd_rate": "0.1"}]})
    if u.host == "opendart.fss.or.kr":
        return httpx.Response(200, json={"status": "000", "total_page": 1, "list": [
            {"rcept_no": "20261106000001", "corp_name": "에코프로", "stock_code": "086520", "corp_cls": "K",
             "report_nm": "주요사항보고서(유상증자결정)", "rcept_dt": YMD},
            {"rcept_no": "20261106000002", "corp_name": "삼성전자", "stock_code": "005930", "corp_cls": "Y",
             "report_nm": "기업설명회(IR)개최", "rcept_dt": YMD}]})
    if u.host == "api.telegram.org":
        return httpx.Response(200, json={"ok": True})
    return httpx.Response(404, json={})


@pytest.fixture
def ctx(settings):
    settings.values.update(KIS_APP_KEY="K" * 12, KIS_APP_SECRET="S", KRX_OPENAPI_KEY="KRXKEY", DART_API_KEY="D",
                           TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="9")
    c = make_ctx(settings)
    http = httpx.Client(transport=httpx.MockTransport(router))
    for cl in (c.kis, c.krx, c.dart, c.tg):
        cl.http = http
    c.kis.limiter = RateLimiter(10_000)
    c.tg._last = -10
    return c


def test_full_daily_run(ctx, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    # 운영에서는 전 종목이 100개 미만이면 실패로 본다. 여기서는 3종목짜리 가짜 데이터라 하한을 낮춘다.
    monkeypatch.setattr(daily, "STEPS", [(n, f, min(m, 1)) for n, f, m in daily.STEPS])
    ctx.store.upsert_watch("086520", "에코프로", "보유", 10, 80000, "")
    res = daily.run(ctx, DAY)
    items = res["items"]
    assert res["status"] == "ok", {k: (v["status"], v["message"][:80]) for k, v in items.items() if v["status"] != "ok"}
    assert items["universe"]["count"] == 3                       # ETF 제외 주권 3종목
    assert items["krx_prices"]["count"] == 3
    assert items["nxt_prices"]["count"] == 1
    assert items["investor"]["count"] == 3
    assert items["disclosures"]["count"] == 2

    p = ctx.store.read_clean("prices_krx", DAY).set_index("code")
    assert "000660" in p.index                                    # 표준코드 → 단축코드
    uni = ctx.store.read_clean("universe", DAY).set_index("code")
    assert uni.loc["086520", "administrative"] == "Y" and uni.loc["000660", "market_warning"] == "01"
    inv = ctx.store.read_clean("investor", DAY)
    assert set(inv["date"]) == {YMD}                              # 그날 행만

    from bkkstock.analysis.history import price_history
    h = price_history(ctx.store, DAY, 5).set_index("code")
    assert h.loc["005930", "close"] == 70000                      # 쉼표 숫자 파싱
    assert h.loc["005930", "total_value"] == 700_000_000_000 + 210_000_000_000   # KRX + NXT

    out = ctx.store.read_clean("outlook", DAY).set_index("code")
    eco = json.loads(out.loc["086520", "flags"])
    kinds = {f["kind"] for f in eco}
    assert {"관리종목", "희석"} <= kinds and out.loc["086520", "blocked"]

    alerts = ctx.store.query("SELECT * FROM alerts")
    assert any("유상증자" in a["message"] for a in alerts)
    assert all(a["sent"] == 1 for a in alerts)                   # 텔레그램으로 나감

    msg = report.build_message(ctx, DAY)
    assert msg.startswith("📊") and "종목 3" in msg

    raw = sorted(p.name for p in (ctx.store.raw_dir / DAY.isoformat()).iterdir())
    assert "kis_master_kospi.zip" in raw and "krx_trades_kospi.json.gz" in raw


def test_web_pages_with_real_pipeline_data(ctx, settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    ctx.store.upsert_watch("086520", "에코프로", "보유", 10, 80000, "")
    daily.run(ctx, DAY)
    monkeypatch.setenv("BKK_STOCK_ENV", str(settings.env_file))
    monkeypatch.setenv("BKK_STOCK_DATA", str(settings.data_dir))
    from bkkstock.config import get_settings
    get_settings(reload=True)
    import importlib
    from bkkstock.web import app as appmod
    importlib.reload(appmod)
    appmod.reload_ctx()
    c = appmod.app.test_client()
    c.post("/login", data={"password": "pw"})
    for url in ("/", "/stocks", "/stocks/086520", "/stocks/005930", "/apis", "/ops"):
        r = c.get(url)
        assert r.status_code == 200, url
    page = c.get("/stocks").get_data(as_text=True)
    assert "에코프로" in page and "매수 금지" in page
    assert c.get("/api/search?q=에코").get_json()[0]["code"] == "086520"
