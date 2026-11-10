"""07:30: 토큰 재발급, 밤사이 공시 확인, 어제 실패한 복구 가능 항목 다시 받기."""

from __future__ import annotations

from ..clients.base import ApiError
from ..timeutil import now_kst, prev_trading_day
from . import daily, dart_poll
from .common import Ctx, send_pending_alerts


def run(ctx: Ctx) -> dict:
    """결과를 job_runs에 남긴다(운영 탭·감시가 볼 수 있게). 하나라도 실패하면 상태 partial."""
    today = now_kst().date()
    rid = ctx.store.start_run("morning", today)
    out: dict = {}
    try:
        _run(ctx, today, out)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    bad = [k for k, v in out.items() if isinstance(v, str) and ("Error" in v or "실패" in v or v.startswith("["))]
    status = "ok" if not bad else "partial"
    ctx.store.finish_run(rid, status, {k: (v if isinstance(v, (str, int, float)) else str(v))[:300] for k, v in out.items()},
                         error=", ".join(bad))
    out["status"] = status
    return out


def _run(ctx: Ctx, today, out: dict) -> None:
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
    if ctx.settings.has("DART_API_KEY") and not ctx.store.meta_get("disc_backfill"):
        # 한 번만: 설치 전 130일 공시 채우기 (회피 엔진의 희석·부실 창이 처음 몇 달 비지 않게)
        try:
            from .backfill import backfill_disclosures
            out["disc_backfill"] = backfill_disclosures(ctx, prev_trading_day(today))
        except Exception as e:  # noqa: BLE001
            out["disc_backfill"] = f"실패 {type(e).__name__}"
    try:
        out["overseas"] = collect_overseas(ctx)
    except Exception as e:  # noqa: BLE001
        out["overseas"] = str(e)
    try:
        out["forecast"] = save_forecasts(ctx)
    except Exception as e:  # noqa: BLE001
        out["forecast"] = str(e)
    out["sent"] = send_pending_alerts(ctx)


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
    now = now_kst()
    # 사전 등록: 장이 시작된 뒤(또는 이미 지난 날)를 목표로 한 예측은 저장하지 않는다 — 진짜 장 전 예측을 가리지 않게
    if target < now.date() or (target == now.date() and now.hour >= 9):
        return f"{target} 장이 이미 시작돼 저장하지 않음 (KRX 전날 시세가 아직 없음)"
    if ctx.store.read_clean("forecast", target, columns=["code"]) is not None:
        return f"{target} 예측은 이미 장 전에 저장됨 (처음 것만 인정)"
    df["target"] = df["target"].astype(str)
    df["base_day"] = df["base_day"].astype(str)
    ctx.store.write_clean("forecast", target, df)
    return f"{len(df)}종목 → {target}"
