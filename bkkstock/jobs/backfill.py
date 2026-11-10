"""과거 데이터 채우기. 설치 직후 한 번 돌리면 점수 계산에 필요한 60거래일 이력이 생긴다.

KRX 일별 시세는 날짜 하나로 전 종목이 오므로 하루 2번 호출이면 된다.
관심 종목은 수급(30일)도 같이 채운다. 이미 있는 날은 건너뛴다.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..analysis import outlook
from ..clients.base import ApiError, NotConfigured
from ..timeutil import now_kst, prev_trading_day, trading_days_back
from .common import Ctx, job_lock
from .daily import collect_krx_index, collect_krx_prices


def run(ctx: Ctx, days: int = 90, end: dt.date | None = None, with_outlook: bool = True) -> dict:
    end = end or prev_trading_day(now_kst().date() + dt.timedelta(days=1))
    out = {"prices": 0, "index": 0, "skipped": 0, "errors": []}
    with job_lock(ctx.store, "backfill"):
        have_p = set(ctx.store.clean_days("prices_krx"))
        have_i = set(ctx.store.clean_days("index"))
        for d in trading_days_back(end, days):
            try:
                if d not in have_p:
                    n, _ = collect_krx_prices(ctx, d)
                    out["prices"] += 1 if n else 0
                else:
                    out["skipped"] += 1
                if d not in have_i:
                    n, _ = collect_krx_index(ctx, d)
                    out["index"] += 1 if n else 0
            except NotConfigured as e:
                out["errors"].append(e.message)
                break
            except ApiError as e:
                out["errors"].append(f"{d}: {e}")
                if len(out["errors"]) > 10:
                    break
        out["investor_watch"] = backfill_watch_investor(ctx, end)
        out["short_credit_watch"] = backfill_watch_short_credit(ctx, end)
        try:
            from .morning import collect_overseas
            out["overseas"] = collect_overseas(ctx)
        except Exception as e:  # noqa: BLE001
            out["overseas"] = str(e)
        if with_outlook:
            n_out = 0
            for d in trading_days_back(end, min(days, 40)):
                if d in set(ctx.store.clean_days("outlook")):
                    continue
                df = outlook.compute(ctx.store, d)
                if not df.empty:
                    ctx.store.write_clean("outlook", d, df)
                    n_out += 1
            out["outlook_days"] = n_out
    return out


def backfill_watch_investor(ctx: Ctx, end: dt.date) -> str:
    codes = ctx.store.watch_codes()
    if not codes or not ctx.settings.has("KIS_APP_KEY", "KIS_APP_SECRET"):
        return "건너뜀"
    by_day: dict[str, list[dict]] = {}
    for code in codes:
        try:
            for r in ctx.kis.investor_daily(code, end, market="UN"):
                by_day.setdefault(r["stck_bsop_date"], []).append(dict(r, code=code, date=r["stck_bsop_date"]))
        except ApiError:
            continue
    have = set(ctx.store.clean_days("investor"))
    n = 0
    for ymd, rows in by_day.items():
        d = dt.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        if d in have:
            continue
        ctx.store.write_clean("investor", d, pd.DataFrame(rows))
        n += 1
    return f"관심 {len(codes)}종목 · {n}일치"


def backfill_watch_short_credit(ctx: Ctx, end: dt.date, days: int = 60) -> str:
    """관심 종목 공매도·신용 60일 (차트·표용)."""
    codes = ctx.store.watch_codes()
    if not codes or not ctx.settings.has("KIS_APP_KEY", "KIS_APP_SECRET"):
        return "건너뜀"
    start = trading_days_back(end, days)[0]
    short_by: dict[str, list[dict]] = {}
    credit_by: dict[str, list[dict]] = {}
    for code in codes:
        try:
            for r in ctx.kis.short_daily(code, start, end):
                short_by.setdefault(r["stck_bsop_date"], []).append(dict(r, code=code, date=r["stck_bsop_date"]))
        except ApiError:
            pass
        d = end
        for _ in range(3):           # 한 번에 30행씩
            try:
                rows = ctx.kis.credit_daily(code, d)
            except ApiError:
                break
            if not rows:
                break
            for r in rows:
                credit_by.setdefault(r["deal_date"], []).append(dict(r, code=code, date=r["deal_date"]))
            oldest = min(r["deal_date"] for r in rows)
            d = dt.date(int(oldest[:4]), int(oldest[4:6]), int(oldest[6:])) - dt.timedelta(days=1)
            if d < start:
                break
    n = 0
    for table, by in (("short", short_by), ("credit", credit_by)):
        have = set(ctx.store.clean_days(table))
        for ymd, rows in by.items():
            dd = dt.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
            if dd in have or dd < start:
                continue
            ctx.store.write_clean(table, dd, pd.DataFrame(rows))
            n += 1
    return f"관심 {len(codes)}종목 · {n}일치"
