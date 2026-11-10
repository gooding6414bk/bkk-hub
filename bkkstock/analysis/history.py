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


PRICE_COLS = ["date", "code", "name", "market", *NUM]
LEAN_COLS = ["date", "code", "open", "close", "change_pct", "value"]     # 예측가·검증용 (메모리 절약)


def price_history(store: Store, end: dt.date, n: int = 60, codes: list[str] | None = None,
                  columns: list[str] | None = None) -> pd.DataFrame:
    """KRX 일별 시세 + NXT 거래량 (수정주가). 열: date, code, name, close, volume, value, mktcap, nxt_volume, nxt_value, total_value.
    codes를 주면 그 종목만 읽는다(종목 화면 — 전 종목을 읽으면 요청 하나에 수백 MB). columns로 읽을 열을 줄일 수 있다."""
    days = trading_days_back(end, n)
    krx = store.read_range("prices_krx", days, columns=columns or PRICE_COLS, codes=codes)
    if krx.empty:
        return krx
    for c in NUM:                      # 응답 필드가 빠져도(이름이 다르거나) 계산이 죽지 않게
        if c not in krx.columns:
            krx[c] = pd.NA
    krx = _to_num(krx, NUM)
    krx["date"] = pd.to_datetime(krx["date"].astype(str), format="mixed").dt.date
    nxt = store.read_range("prices_nxt", days, columns=["date", "code", "volume", "value", "close"], codes=codes)
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
    krx = krx.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last").reset_index(drop=True)
    return adjust_prices(krx)


ADJ_TOL = 0.03   # 등락률로 거꾸로 구한 전날 종가가 실제 전날 종가와 3% 넘게 다르면 권리락·액면 변경으로 본다


def adjust_prices(df: pd.DataFrame) -> pd.DataFrame:
    """수정주가. 액면분할·병합, 무상증자·유상증자 권리락 날은 원 종가끼리 나누면 가짜 급등·폭락이 생긴다
    (예: 10:1 병합 → +900% '재료 없는 급등'으로 매수 금지). KRX 등락률(FLUC_RT)은 권리락·액면 변경을 반영한
    기준가 대비라서, 등락률로 구한 전날 종가 ÷ 실제 전날 종가 비율만큼 그날 이전 가격(시·고·저·종)을 맞춘다.
    원래 종가는 raw_close로 남긴다. 거래대금·시총은 그대로 (주식 수와 무관)."""
    if df.empty or "change_pct" not in df.columns:
        return df
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df["raw_close"] = df["close"]
    prev = df.groupby("code")["close"].shift(1)
    implied = df["close"] / (1 + df["change_pct"] / 100)
    ratio = implied / prev
    event = ratio.notna() & ((ratio - 1).abs() > ADJ_TOL) & (ratio > 0.01) & (ratio < 100)
    if not event.any():
        return df
    r = ratio.where(event, 1.0).astype(float)
    after_incl = r.iloc[::-1].groupby(df["code"].iloc[::-1]).cumprod().reindex(df.index)
    factor = after_incl / r                     # 이 날 '뒤'에 있었던 변경들의 누적 비율
    for c in ("open", "high", "low", "close", "nxt_close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce") * factor
    df["adj_factor"] = factor
    return df


def _investor_col(c: str) -> bool:
    return c in ("date", "code", "stck_clpr") or "_ntby_" in c


def investor_history(store: Store, end: dt.date, n: int = 30, codes: list[str] | None = None) -> pd.DataFrame:
    """투자자별 순매수. 응답 열 80여 개 중 순매수 열만 읽는다."""
    days = trading_days_back(end, n)
    inv = store.read_range("investor", days, columns=_investor_col, codes=codes)
    if inv.empty:
        return inv
    inv["date"] = pd.to_datetime(inv["date"].astype(str), format="mixed").dt.date
    inv = _to_num(inv, ["frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn", "prsn_ntby_tr_pbmn",
                        "frgn_ntby_qty", "orgn_ntby_qty", "prsn_ntby_qty"])
    return inv.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last").reset_index(drop=True)


def latest_universe(store: Store, end: dt.date) -> tuple[pd.DataFrame, dt.date | None]:
    """end 이전(포함) 가장 가까운 종목 목록. end보다 '나중' 목록은 쓰지 않는다 — 과거 날짜 점수(백필)에
    오늘 지정된 관리종목·경고가 찍히는 미래 정보 누출을 막는다."""
    for d in reversed(trading_days_back(end, 10)):
        df = store.read_clean("universe", d)
        if df is not None and len(df):
            return df, d
    for day in reversed(store.clean_days("universe")):
        if day <= end:
            df = store.read_clean("universe", day)
            if df is not None and len(df):
                return df, day
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
