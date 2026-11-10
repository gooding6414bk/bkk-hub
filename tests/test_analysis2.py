"""수급·뉴스·내일 예측가·시장 개요."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from bkkstock.analysis import flows, forecast, market, newsread
from bkkstock.timeutil import trading_days_back
from tests.conftest import make_prices

END = dt.date(2026, 11, 6)


# ---------------- 수급 ----------------
def _inv(days, frgn, close=10000.0, unit=1e6):
    rows = []
    for d, f in zip(days, frgn):
        won = f * 1e8
        rows.append({"date": d, "code": "000001", "stck_clpr": close,
                     "frgn_ntby_tr_pbmn": won / unit, "frgn_ntby_qty": won / close,
                     "orgn_ntby_tr_pbmn": -won / 2 / unit, "orgn_ntby_qty": -won / 2 / close,
                     "prsn_ntby_tr_pbmn": -won / 2 / unit, "prsn_ntby_qty": -won / 2 / close,
                     "fund_ntby_tr_pbmn": -won / 4 / unit})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("unit", [1.0, 1e3, 1e6])
def test_unit_factor_detected(unit):
    days = trading_days_back(END, 5)
    inv = _inv(days, [1, 2, -1, 3, 1], unit=unit)
    assert flows.unit_factor(inv) == unit


def test_breakdown_periods_streak_and_ratios():
    days = trading_days_back(END, 25)
    frgn = [-1] * 20 + [1, 2, 1, 1, 1]          # 억 원, 마지막 5일 연속 순매수
    inv = _inv(days, frgn)
    px = pd.DataFrame({"date": days, "close": 10000.0, "total_value": 10e8, "mktcap": 1000e8})
    b = flows.breakdown(inv, px)
    assert b["streak"]["외국인"] == 5 and b["streak"]["기관"] == -5
    p5 = next(p for p in b["periods"] if p["days"] == 5)
    assert p5["groups"]["외국인"]["net"] == pytest.approx(6e8)
    assert p5["groups"]["외국인"]["pct_value"] == pytest.approx(6e8 / 50e8 * 100)
    assert p5["groups"]["외국인"]["pct_mktcap"] == pytest.approx(0.6)
    assert "연기금" in b["inst_names"]
    assert [p["days"] for p in b["periods"]] == [1, 5, 20]       # 60일은 자료 부족이라 없음


# ---------------- 뉴스 ----------------
@pytest.mark.parametrize("title,tone,cat", [
    ("삼성전자, 2분기 최대 실적…영업이익 컨센서스 상회", "pos", "실적 개선"),
    ("가나다 운영자금 위해 유상증자 결정", "neg", "희석·자금조달"),
    ("가나다 수주 소식에 급등했지만 소송 리스크", "mixed", "법적·규제"),
    ("가나다 신제품 공개 행사 열어", "pos", "승인·신사업"),
    ("가나다 주주총회 개최", "neutral", None),
    ("가나다 급락", "neg", "주가 약세"),
])
def test_classify_title(title, tone, cat):
    r = newsread.classify_title(title)
    assert r["tone"] == tone
    if cat:
        assert cat in r["categories"] and r["words"]


def test_dedupe_same_story_from_many_outlets():
    items = [{"title": "가나다전자 대규모 공급계약 체결 - 샘플일보"},
             {"title": "가나다전자, 대규모 공급계약 체결 - 샘플경제"},
             {"title": "가나다전자 목표주가 상향"}]
    u = newsread.dedupe(items)
    assert len(u) == 2 and u[0]["dupes"] == 1


def test_summary_has_ratio_and_sentence():
    items = [{"title": t} for t in ("A 공급계약 체결", "A 최대 실적", "A 유상증자 결정", "A 신제품 출시", "A 주총")]
    sm = newsread.summarize(items)
    assert sm["n"] == 5 and sm["counts"]["pos"] == 3 and sm["counts"]["neg"] == 1
    assert round(sum(sm["ratio"].values())) == 100
    assert "호재 3건(60%)" in sm["summary"] and "가격에 반영" in sm["summary"]
    assert sm["groups"][0]["count"] >= 1


def test_disclosure_summary():
    ds = newsread.disclosure_summary([{"tags": "희석"}, {"tags": "주주환원"}, {"tags": ""}, {"tags": "희석,정정"}])
    assert ds["counts"] == {"neg": 2, "pos": 1, "neutral": 1} and ds["tags"][0] == ("희석", 2)


# ---------------- 내일 예측가 ----------------
def _ov(days_rets: dict) -> pd.DataFrame:
    rows, lv = [], 100.0
    for d in sorted(days_rets):
        lv *= 1 + days_rets[d]
        rows.append({"symbol": "EWY", "date": d, "close": lv})
    return pd.DataFrame(rows)


def test_overnight_window_monday_and_us_holiday():
    fri, sat, mon, tue = dt.date(2026, 11, 6), dt.date(2026, 11, 7), dt.date(2026, 11, 9), dt.date(2026, 11, 10)
    ov = _ov({dt.date(2026, 11, 5): 0.0, fri: 0.02, mon: 0.01})
    done = dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)
    # 한국 월요일 시초가: 금요일(한국 장 마감 뒤) 미국 세션 반영
    assert forecast.overnight(ov, "EWY", fri, mon, done) == pytest.approx(0.02)
    # 한국 화요일: 월요일 미국 세션만 (금요일 것은 이미 월요일에 반영 → 두 번 세지 않음)
    assert forecast.overnight(ov, "EWY", mon, tue, done) == pytest.approx(0.01)
    # 아직 끝나지 않은 미국 세션은 넣지 않고 '대기'(None)로 돌려준다 — 0%로 보이면 안 된다
    before_close = dt.datetime(2026, 11, 9, 18, 0, tzinfo=dt.timezone.utc)
    assert forecast.overnight(ov, "EWY", mon, tue, before_close) is None


def _gap_world(store, beta_true=0.8, gamma_true=0.4, n_days=70, seed=1):
    rng = np.random.default_rng(seed)
    days = trading_days_back(END, n_days)
    codes = [f"{i:06d}" for i in range(1, 41)]
    us = {}
    d0 = days[0] - dt.timedelta(days=7)
    d = d0
    while d <= days[-1]:
        if d.weekday() < 5:
            us[d] = float(rng.normal(0, 0.015))
        d += dt.timedelta(days=1)
    store.write_clean("overseas", days[-1], _ov(us).assign(open=lambda x: x["close"]))
    close = {c: 10000.0 for c in codes}
    nxt_after = {c: 0.0 for c in codes}
    for i, t in enumerate(days):
        prev = days[i - 1] if i else t - dt.timedelta(days=1)
        ewy = float(np.prod([1 + r for dd, r in us.items() if prev <= dd < t]) - 1)
        rows, nrows = [], []
        for c in codes:
            gap = beta_true * ewy + gamma_true * nxt_after[c] + rng.normal(0, 0.002)
            o = close[c] * (1 + gap)
            cl = o * (1 + rng.normal(0, 0.01))
            rows.append({"date": t.strftime("%Y%m%d"), "code": c, "name": c, "open": o, "high": max(o, cl), "low": min(o, cl),
                         "close": cl, "change_pct": (cl / close[c] - 1) * 100, "value": 1e9, "mktcap": 1e11})
            na = rng.normal(0, 0.005)
            nrows.append({"date": t.strftime("%Y%m%d"), "code": c, "close": cl * (1 + na), "value": 2e8, "volume": 1})
            nxt_after[c] = na
            close[c] = cl
        store.write_clean("prices_krx", t, pd.DataFrame(rows))
        store.write_clean("prices_nxt", t, pd.DataFrame(nrows))
    return days, codes


def test_forecast_recovers_beta_gamma_and_beats_baseline(store):
    _gap_world(store)
    ctx = forecast.build_context(store)
    beta, gamma = forecast._pooled(ctx.feats)
    assert beta == pytest.approx(0.8, abs=0.08) and gamma == pytest.approx(0.4, abs=0.1)
    fc = forecast.forecast(ctx, "000001")
    assert fc["target"] == dt.date(2026, 11, 9)                   # 금요일 다음 거래일 = 월요일
    assert fc["lo1"] < fc["center"] < fc["hi1"] and fc["lo2"] < fc["lo1"]
    bt = fc["bt"]
    assert bt["n"] >= 20 and bt["mae_model"] < bt["mae_base"] and bt["hit"] > 70


def test_backtest_has_no_lookahead(store):
    _gap_world(store)
    ctx = forecast.build_context(store)
    a = forecast.backtest_rows(ctx, "000001", days=40)
    # 마지막 날 실제 갭을 엉뚱하게 바꿔도, 그 전 날짜들의 예측은 하나도 바뀌면 안 된다
    ctx2 = forecast.build_context(store)
    last = ctx2.feats["date"].max()
    ctx2.feats.loc[ctx2.feats["date"] == last, "gap"] = 0.3
    b = forecast.backtest_rows(ctx2, "000001", days=40)
    pa = a[a["date"] < last].set_index("date")["pred"]
    pb = b[b["date"] < last].set_index("date")["pred"]
    assert len(pa) >= 20
    pd.testing.assert_series_equal(pa, pb)
    # 반대로 미래를 바꾸면 '그 미래 날짜'의 성적만 달라진다
    assert a[a["date"] == last]["actual_gap"].iloc[0] != b[b["date"] == last]["actual_gap"].iloc[0]


def test_forecast_without_overseas_still_gives_range(store):
    make_prices(store, trading_days_back(END, 30), ["000001", "000002"])
    ctx = forecast.build_context(store)
    fc = forecast.forecast(ctx, "000001")
    assert not fc["us_ready"] and fc["pred_gap"] == pytest.approx(0.0, abs=1e-9) and fc["hi1"] > fc["lo1"]


# ---------------- 시장 개요 ----------------
def test_market_overview_counts(store):
    days = trading_days_back(END, 45)
    make_prices(store, days, ["000001", "000002", "000003"], rets={("000001", days[-1]): 0.3, ("000002", days[-1]): -0.05})
    mk = market.overview(store)
    assert mk["up"] >= 1 and mk["down"] >= 1 and mk["limit_up"] == 1
    assert mk["top_up"][0]["code"] == "000001"
    assert mk["new_high"] >= 1
