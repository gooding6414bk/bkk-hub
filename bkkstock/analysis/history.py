"""저장된 일별 데이터를 분석용 표로 모은다."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..store import Store
from ..timeutil import trading_days_back

NUM = ["open", "high", "low", "close", "volume", "value", "mktcap", "change_pct", "listed_shares"]


def _to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "", regex=False), errors="coerce")
    return df


def price_history(store: Store, end: dt.date, n: int = 60) -> pd.DataFrame:
    """KRX 일별 시세 + NXT 거래량. 열: date, code, name, close, volume, value, mktcap, nxt_volume, nxt_value, total_value."""
    days = trading_days_back(end, n)
    krx = store.read_range("prices_krx", days)
    if krx.empty:
        return krx
    for c in NUM:                      # 응답 필드가 빠져도(이름이 다르거나) 계산이 죽지 않게
        if c not in krx.columns:
            krx[c] = pd.NA
    krx = _to_num(krx, NUM)
    krx["date"] = pd.to_datetime(krx["date"].astype(str), format="mixed").dt.date
    nxt = store.read_range("prices_nxt", days)
    if not nxt.empty:
        nxt = _to_num(nxt, ["volume", "value"])
        nxt["date"] = pd.to_datetime(nxt["date"].astype(str), format="mixed").dt.date
        if "close" not in nxt.columns:
            nxt["close"] = pd.NA
        nxt = _to_num(nxt, ["close"])
        nxt = nxt.groupby(["date", "code"], as_index=False)[["volume", "value", "close"]].last()
        nxt = nxt.rename(columns={"volume": "nxt_volume", "value": "nxt_value", "close": "nxt_close"})
        krx = krx.merge(nxt, on=["date", "code"], how="left")
    else:
        krx["nxt_volume"] = pd.NA
        krx["nxt_value"] = pd.NA
        krx["nxt_close"] = pd.NA
    krx["total_value"] = krx["value"].fillna(0) + pd.to_numeric(krx["nxt_value"], errors="coerce").fillna(0)
    krx["total_volume"] = krx["volume"].fillna(0) + pd.to_numeric(krx["nxt_volume"], errors="coerce").fillna(0)
    krx = krx.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last")
    return krx.reset_index(drop=True)


def investor_history(store: Store, end: dt.date, n: int = 30) -> pd.DataFrame:
    days = trading_days_back(end, n)
    inv = store.read_range("investor", days)
    if inv.empty:
        return inv
    inv["date"] = pd.to_datetime(inv["date"].astype(str), format="mixed").dt.date
    inv = _to_num(inv, ["frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn", "prsn_ntby_tr_pbmn",
                        "frgn_ntby_qty", "orgn_ntby_qty", "prsn_ntby_qty"])
    return inv.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last").reset_index(drop=True)


def latest_universe(store: Store, end: dt.date) -> tuple[pd.DataFrame, dt.date | None]:
    for d in reversed(trading_days_back(end, 10)):
        df = store.read_clean("universe", d)
        if df is not None and len(df):
            return df, d
    day = store.latest_clean_day("universe")
    if day:
        return store.read_clean("universe", day), day
    return pd.DataFrame(), None


def overseas_latest(store: Store) -> pd.DataFrame:
    """가장 최근에 받은 해외 시세 묶음(1년치)."""
    day = store.latest_clean_day("overseas")
    if not day:
        return pd.DataFrame()
    df = store.read_clean("overseas", day)
    if df is None or df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)
