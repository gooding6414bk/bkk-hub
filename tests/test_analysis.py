import datetime as dt
import json

import pandas as pd

from bkkstock.analysis import outlook
from bkkstock.analysis.flags import disclosure_flags, horse_flags, universe_flags
from bkkstock.timeutil import trading_days_back
from tests.conftest import make_prices

END = dt.date(2026, 11, 6)


def test_universe_flags():
    fl = universe_flags({"halted": "N", "administrative": "Y", "market_warning": "01", "warning_notice": "Y",
                         "listed_date": "20150101"}, END)
    kinds = {f.kind: f.level for f in fl}
    assert kinds["관리종목"] == "block" and kinds["투자주의"] == "warn" and kinds["경고예고"] == "warn"


def test_new_listing_flag():
    d = trading_days_back(END, 10)[0]
    fl = universe_flags({"listed_date": d.strftime("%Y%m%d")}, END)
    assert any(f.kind == "신규상장" for f in fl)


def _hist(spike_day, ret=0.24):
    days = trading_days_back(END, 40)
    rows, px = [], 1000.0
    for d in days:
        r = ret if d == spike_day else 0.0
        px *= 1 + r
        rows.append({"date": d, "close": px, "value": 1e9 * (8 if d == spike_day else 1), "total_value": 1e9 * (8 if d == spike_day else 1)})
    return pd.DataFrame(rows)


def test_horse_without_material_blocks():
    spike = trading_days_back(END, 5)[0]
    fl = horse_flags(_hist(spike), [], END)
    assert fl and fl[0].level == "block" and fl[0].kind == "경주마"


def test_horse_with_previous_evening_disclosure_is_warn():
    spike = trading_days_back(END, 3)[0]
    prev = trading_days_back(spike, 2)[0]
    disc = [{"rcept_dt": prev.strftime("%Y%m%d"), "report_nm": "단일판매ㆍ공급계약체결", "tags": "수주"}]
    fl = horse_flags(_hist(spike), disc, END)
    assert fl[0].level == "warn" and fl[0].kind == "재료급등"


def test_old_horse_expires():
    spike = trading_days_back(END, 30)[0]
    assert horse_flags(_hist(spike), [], END) == []


def test_dilution_disclosure_window():
    recent = (END - dt.timedelta(days=10)).strftime("%Y%m%d")
    old = (END - dt.timedelta(days=200)).strftime("%Y%m%d")
    fl = disclosure_flags([{"rcept_dt": recent, "report_nm": "유상증자결정", "tags": "희석"},
                           {"rcept_dt": old, "report_nm": "관리종목지정", "tags": "부실"}], END)
    assert [f.kind for f in fl] == ["희석"]


def test_outlook_reversal_and_block(store):
    days = trading_days_back(END, 30)
    codes = [f"{i:06d}" for i in range(1, 61)]
    rets = {}
    for d in days[-20:]:
        rets[("000001", d)] = 0.02      # 꾸준히 많이 오른 종목
        rets[("000002", d)] = -0.01     # 꾸준히 내린 종목
    rets[("000003", days[-3])] = 0.25  # 재료 없는 급등
    make_prices(store, days, codes, rets)
    df = outlook.compute(store, END).set_index("code")
    assert df.loc["000001", "reversal"] < 0 < df.loc["000002", "reversal"]
    assert df.loc["000003", "blocked"] and df.loc["000003", "grade"] == "매수 금지"
    assert df.loc["000003", "lottery"] < 0
    assert all(df["model"] == outlook.MODEL_VERSION)
    flags = json.loads(df.loc["000003", "flags"])
    assert flags[0]["kind"] == "경주마"


def test_outlook_needs_history(store):
    days = trading_days_back(END, 10)
    make_prices(store, days, ["000001", "000002"])
    df = outlook.compute(store, END)
    assert not df["enough_history"].any() and (df["reversal"] == 0).all()


def test_verify_insufficient(store):
    assert outlook.verify(store)["status"] == "insufficient"
