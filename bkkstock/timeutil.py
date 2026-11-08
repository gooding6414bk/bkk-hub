"""한국 시간과 한국거래소 휴장일 처리.

서버는 UTC일 수 있으므로 날짜·시각은 모두 이 모듈을 거쳐서 만든다.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)


def today_kst() -> dt.date:
    return now_kst().date()


def iso_kst(ts: dt.datetime | None = None) -> str:
    ts = ts or now_kst()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KST)
    return ts.astimezone(KST).isoformat(timespec="seconds")


def ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def parse_ymd(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


@lru_cache(maxsize=8)
def _krx_holidays(year: int) -> dict:
    return dict(holidays.financial_holidays("XKRX", years=[year]))


def is_trading_day(d: dt.date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _krx_holidays(d.year)


def holiday_name(d: dt.date) -> str | None:
    if d.weekday() >= 5:
        return "주말"
    return _krx_holidays(d.year).get(d)


def prev_trading_day(d: dt.date) -> dt.date:
    d = d - dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def last_trading_day(d: dt.date | None = None) -> dt.date:
    """d가 거래일이면 d, 아니면 그 이전 마지막 거래일."""
    d = d or today_kst()
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def trading_days_back(end: dt.date, n: int) -> list[dt.date]:
    """end를 포함해 거꾸로 n개 거래일 (오래된 순)."""
    out: list[dt.date] = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))
