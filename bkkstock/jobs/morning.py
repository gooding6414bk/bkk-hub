"""07:30: 토큰 재발급, 밤사이 공시 확인, 어제 실패한 복구 가능 항목 다시 받기."""

from __future__ import annotations

from ..clients.base import ApiError
from ..timeutil import now_kst, prev_trading_day
from . import daily, dart_poll
from .common import Ctx, send_pending_alerts


def run(ctx: Ctx) -> dict:
    today = now_kst().date()
    out: dict = {}
    if ctx.settings.has("KIS_APP_KEY", "KIS_APP_SECRET"):
        try:
            ctx.kis.token(force=False)
            out["kis_token"] = "ok"
        except ApiError as e:
            out["kis_token"] = str(e)
    if ctx.settings.has("DART_API_KEY"):
        prev = prev_trading_day(today)
        for d in sorted({prev, today}):
            try:
                out[f"dart_{d}"] = dart_poll.run(ctx, d)
            except Exception as e:  # noqa: BLE001
                out[f"dart_{d}"] = str(e)
    # 휴일 아침에도 직전 거래일의 실패 항목은 다시 받아 둔다
    try:
        out["catch_up"] = daily.catch_up(ctx, prev_trading_day(today))
    except Exception as e:  # noqa: BLE001
        out["catch_up"] = str(e)
    try:
        out["overseas"] = collect_overseas(ctx)
    except Exception as e:  # noqa: BLE001
        out["overseas"] = str(e)
    try:
        out["forecast"] = save_forecasts(ctx)
    except Exception as e:  # noqa: BLE001
        out["forecast"] = str(e)
    out["sent"] = send_pending_alerts(ctx)
    return out


def collect_overseas(ctx: Ctx) -> str:
    """밤사이 미국장(EWY·반도체·나스닥)과 원/달러 1년치. 내일 예측가와 시장 개요에 쓴다."""
    import pandas as pd
    rows = ctx.overseas.all_history("1y")
    day = now_kst().date()
    ctx.store.write_raw(day, "overseas", rows)
    ctx.store.write_clean("overseas", day, pd.DataFrame(rows))
    return f"{len(rows)}행"


def save_forecasts(ctx: Ctx) -> str:
    """관심·보유 종목의 내일 예측가를 저장 (나중에 실제와 비교해 적중률을 쌓는다)."""
    from ..analysis import forecast as fcmod
    codes = ctx.store.watch_codes()
    if not codes:
        return "관심 종목 없음"
    df = fcmod.forecast_many(ctx.store, codes, with_backtest=True)
    if df.empty:
        return "시세 이력 없음"
    target = df["target"].iloc[0]
    df["target"] = df["target"].astype(str)
    df["base_day"] = df["base_day"].astype(str)
    ctx.store.write_clean("forecast", target, df)
    return f"{len(df)}종목 → {target}"
