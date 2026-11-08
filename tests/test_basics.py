import datetime as dt
import json

import pandas as pd
import pytest

from bkkstock.config import mask, parse_env_text, write_env_values
from bkkstock.store import normalize_code
from bkkstock.timeutil import is_trading_day, prev_trading_day, trading_days_back


def test_env_roundtrip_keeps_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# 주석\nKRX_OPENAPI_KEY=old # 끝 주석\nDART_API_KEY='abc'\n")
    write_env_values(p, {"KRX_OPENAPI_KEY": "new", "TELEGRAM_CHAT_ID": "123"})
    text = p.read_text()
    assert "# 주석" in text
    v = parse_env_text(text)
    assert v["KRX_OPENAPI_KEY"] == "new" and v["DART_API_KEY"] == "abc" and v["TELEGRAM_CHAT_ID"] == "123"
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_env_rejects_unknown_and_newline(tmp_path):
    p = tmp_path / ".env"
    with pytest.raises(ValueError):
        write_env_values(p, {"PATH": "x"})
    with pytest.raises(ValueError):
        write_env_values(p, {"DART_API_KEY": "a\nDASH_PASSWORD=x"})


def test_mask_never_shows_whole_key():
    assert mask("abcdefghijkl").endswith("ijkl") and "abcd" not in mask("abcdefghijkl")
    assert mask("") == ""


@pytest.mark.parametrize("raw,want", [("5930", "005930"), (5930, "005930"), ("A005930", "005930"), ("005930", "005930")])
def test_code_keeps_leading_zero(raw, want):
    assert normalize_code(raw) == want


def test_bad_code_rejected():
    with pytest.raises(ValueError):
        normalize_code("../etc")


def test_krx_holidays_2026():
    assert not is_trading_day(dt.date(2026, 10, 9))     # 한글날
    assert not is_trading_day(dt.date(2026, 12, 31))    # 연말 휴장
    assert not is_trading_day(dt.date(2026, 11, 7))     # 토요일
    assert is_trading_day(dt.date(2026, 11, 6))
    assert prev_trading_day(dt.date(2026, 10, 12)) == dt.date(2026, 10, 8)
    days = trading_days_back(dt.date(2026, 10, 12), 3)
    assert days == [dt.date(2026, 10, 7), dt.date(2026, 10, 8), dt.date(2026, 10, 12)]


def test_raw_never_overwritten(store):
    d = dt.date(2026, 11, 6)
    p1 = store.write_raw(d, "x", {"a": 1})
    p2 = store.write_raw(d, "x", {"a": 2})
    assert p1 != p2 and p1.exists() and p2.exists() and ".v2." in p2.name


def test_clean_reads_latest_version(store):
    d = dt.date(2026, 11, 6)
    store.write_clean("t", d, pd.DataFrame({"code": ["005930"], "v": [1]}))
    store.write_clean("t", d, pd.DataFrame({"code": ["005930"], "v": [2]}))
    for _ in range(9):
        store.write_clean("t", d, pd.DataFrame({"code": ["005930"], "v": [3]}))
    store.write_clean("t", d, pd.DataFrame({"code": ["005930"], "v": [99]}))  # v12: 문자열 정렬이면 v9가 이김
    df = store.read_clean("t", d)
    assert int(df["v"].iloc[0]) == 99
    assert df["code"].iloc[0] == "005930"          # 앞자리 0 유지
    assert "collected_at" in df.columns


def test_latest_item_status_merges_retries(store):
    d = dt.date(2026, 11, 6)
    r1 = store.start_run("daily", d)
    store.item(r1, "krx_prices", "fail")
    store.item(r1, "universe", "ok", 10)
    r2 = store.start_run("daily", d)
    store.item(r2, "krx_prices", "ok", 10)
    assert store.latest_item_status("daily", d) == {"krx_prices": "ok", "universe": "ok"}


def test_alert_dedupe(store):
    assert store.add_alert("x", "m", dedupe_key="k") is not None
    assert store.add_alert("x", "m", dedupe_key="k") is None
