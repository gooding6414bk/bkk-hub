"""📊 지표 (2026-11): ECOS·야후 수집, 화면 계산, 일정."""
import datetime as dt

import httpx
import pandas as pd
import pytest

from bkkstock.analysis import macro
from bkkstock.clients.base import ApiError
from bkkstock.clients.ecos import EcosClient
from bkkstock.clients.overseas import OverseasClient
from bkkstock.jobs.common import make_ctx
from bkkstock.redact import redact


def ecos_with(settings, store, handler):
    settings.values["ECOS_API_KEY"] = "ECOSKEY123456"
    return EcosClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ecos_series_parses_daily_and_monthly(settings, store):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if "/M/" in req.url.path:
            rows = [{"TIME": "202609", "DATA_VALUE": "119.9"}, {"TIME": "202610", "DATA_VALUE": "120.05"}]
        else:
            rows = [{"TIME": "20261123", "DATA_VALUE": "3.743"}, {"TIME": "20261124", "DATA_VALUE": "-"}]
        return httpx.Response(200, json={"StatisticSearch": {"list_total_count": 2, "row": rows}})
    e = ecos_with(settings, store, handler)
    assert e.series("kr_3y", dt.date(2026, 11, 1), dt.date(2026, 11, 24)) == [{"period": "2026-11-23", "value": 3.743}]
    assert e.series("kr_cpi", dt.date(2026, 9, 1), dt.date(2026, 10, 31))[-1] == {"period": "2026-10", "value": 120.05}
    assert "/817Y002/D/20261101/20261124/010200000" in seen[0] and "/901Y009/M/202609/202610/0" in seen[1]


def test_ecos_errors_and_no_data(settings, store):
    def handler(req):
        if "722Y001" in req.url.path:
            return httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}})
        return httpx.Response(200, json={"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}})
    e = ecos_with(settings, store, handler)
    assert e.series("kr_base", dt.date(2026, 11, 1), dt.date(2026, 11, 2)) == []
    with pytest.raises(ApiError) as ei:
        e.series("usdkrw", dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    assert "INFO-100" in str(ei.value) and "ECOSKEY" not in str(ei.value)


def test_ecos_key_in_path_is_redacted():
    out = redact("ConnectError for https://ecos.bok.or.kr/api/StatisticSearch/ABCDEF123456/json/kr/1/10/722Y001")
    assert "ABCDEF123456" not in out and "StatisticSearch/•••" in out


def _yahoo(fail=()):
    def handler(req):
        sym = req.url.path.rsplit("/", 1)[-1]
        if sym in fail:
            return httpx.Response(500, text="no")
        ts = [1763337600 + 86400 * i for i in range(3)]
        return httpx.Response(200, json={"chart": {"result": [{"meta": {"gmtoffset": 0}, "timestamp": ts,
                              "indicators": {"quote": [{"open": [1, 2, 3], "close": [4.1, 4.2, 4.3]}]}}]}})
    return handler


def test_overseas_one_symbol_failing_does_not_stop_others(settings, store):
    ov = OverseasClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(_yahoo(fail=("%5ESOX", "^SOX")))))
    rows = ov.all_history("5d")
    assert {r["symbol"] for r in rows} == {"EWY", "^IXIC", "KRW=X"}            # 반도체만 빠지고 예측가용 EWY 는 남음
    mrows, failed = ov.macro_history("5d")
    assert {r["symbol"] for r in mrows} == {"^TNX", "^VIX", "DX-Y.NYB"} and failed == []


def _seed_macro(store, today):
    rows = []
    days = pd.bdate_range(end=today, periods=260)
    for i, d in enumerate(days):
        rows.append({"source": "ecos", "series": "kr_3y", "label": "국고채 3년", "unit": "%", "freq": "D",
                     "period": d.strftime("%Y-%m-%d"), "value": 3.0 + i * 0.002, "fetched_at": "2026-11-24T20:40:00+09:00"})
        rows.append({"source": "ecos", "series": "usdkrw", "label": "원/달러", "unit": "원", "freq": "D",
                     "period": d.strftime("%Y-%m-%d"), "value": 1300 + i, "fetched_at": "2026-11-24T20:40:00+09:00"})
        rows.append({"source": "yahoo", "series": "us_10y", "label": "미국채 10년", "unit": "%", "freq": "D",
                     "period": d.strftime("%Y-%m-%d"), "value": 4.0, "fetched_at": "2026-11-24T20:40:00+09:00"})
    for k, m in enumerate(pd.period_range(end=pd.Period(today, "M") - 1, periods=25, freq="M")):
        rows.append({"source": "ecos", "series": "kr_cpi", "label": "소비자물가지수", "unit": "2020=100", "freq": "M",
                     "period": str(m), "value": 100 * (1.02 ** (k / 12)), "fetched_at": "2026-11-24T20:40:00+09:00"})
    store.write_clean("macro", today, pd.DataFrame(rows))


def test_view_cards_change_position_and_cpi_yoy(store):
    today = dt.date(2026, 11, 24)
    _seed_macro(store, today)
    v = macro.view(store, today)
    cards = {c["name"]: c for g in v["groups"] for c in g["cards"]}
    k3 = cards["kr_3y"]
    assert not k3["empty"] and k3["pos"] == pytest.approx(100) and k3["chg"] > 0 and k3["mode"] == "pp"
    assert cards["usdkrw"]["chg"] == pytest.approx((1559 / 1538 - 1) * 100, rel=0.05)
    assert cards["kr_cpi_yoy"]["value"] == pytest.approx(2.0, abs=0.05)                # 지수 → 1년 전 대비 %
    assert not cards["kr_cpi_yoy"]["stale"] and cards["kr_cpi_yoy"]["shown"] == "2026년 10월"   # 10월분은 11월에 정상
    assert cards["vix"]["empty"] and cards["us_10y"]["pos"] == 50.0                    # 1년 내내 같으면 가운데
    assert v["has_ecos"] and len(v["events"]) == 3


def test_stale_and_jump_flags(store):
    today = dt.date(2026, 11, 24)
    rows = [{"source": "yahoo", "series": "vix", "label": "VIX", "unit": "", "freq": "D", "period": d, "value": val,
             "fetched_at": ""} for d, val in (("2026-10-01", 15.0), ("2026-10-02", 15.2), ("2026-11-02", 30.0))]
    store.write_clean("macro", today, pd.DataFrame(rows))
    c = {c["name"]: c for g in macro.view(store, today)["groups"] for c in g["cards"]}["vix"]
    assert c["stale"] and c["jump"]                     # 3주 넘게 안 바뀜 · 하루 +97%


def test_option_expiry_and_events():
    assert macro.option_expiry(2026, 12) == dt.date(2026, 12, 10)          # 둘째 목요일
    ev = macro.upcoming_events(dt.date(2026, 11, 24), 3)
    assert [e["kind"] for e in ev] == ["금통위", "FOMC", "옵션만기"] and ev[0]["dday"] == 2
    assert "쿼드러플" in ev[2]["desc"]
    assert macro.schedule_gap(dt.date(2026, 11, 24)) is None
    assert "FOMC" in macro.schedule_gap(dt.date(2027, 8, 1))                # 2027 10월 이후 FOMC 를 넣어야 함


def test_collect_without_ecos_key_still_saves_yahoo(settings, store, monkeypatch):
    ctx = make_ctx(settings)
    ctx.overseas.http = httpx.Client(transport=httpx.MockTransport(_yahoo()))
    from bkkstock.jobs import macro as job
    r = job.collect(ctx)
    assert r["status"] == "ok" and r["rows"] == 9 and "ECOS 키 없음" in r["note"]
    df = ctx.store.read_clean("macro", ctx.store.latest_clean_day("macro"))
    assert set(df["series"]) == {"us_10y", "vix", "dxy"}
    assert job.safe_collect(ctx).startswith("ok")
