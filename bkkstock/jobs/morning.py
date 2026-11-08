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
    out["sent"] = send_pending_alerts(ctx)
    return out
