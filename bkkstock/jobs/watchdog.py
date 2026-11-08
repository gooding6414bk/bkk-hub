"""21:40 감시: 일일 수집이 안 돌았거나, 장중 공시 조회가 오래 멈췄으면 알린다.
서버 전체가 죽으면 이것도 못 알리므로 외부 헬스체크(/healthz)를 같이 쓴다."""

from __future__ import annotations

import datetime as dt

from ..timeutil import is_trading_day, now_kst
from .common import Ctx, send_pending_alerts


def run(ctx: Ctx) -> dict:
    now = now_kst()
    day = now.date()
    out: dict = {}
    if is_trading_day(day):
        last = ctx.store.last_run("daily", day)
        if last is None or last["status"] in ("running", "fail"):
            st = "기록 없음" if last is None else last["status"]
            ctx.store.add_alert("감시", f"🚨 주식 일일 수집 이상: {st}. 서버 로그를 확인하세요.\njournalctl -u bkk-stock@daily -n 50",
                                dedupe_key=f"watchdog:daily:{day}")
            out["daily"] = st
        if 9 <= now.hour < 20 and ctx.settings.has("DART_API_KEY"):
            rows = ctx.store.query("SELECT finished_at FROM job_runs WHERE job='dart_poll' AND status='ok' ORDER BY id DESC LIMIT 1")
            stale = True
            if rows and rows[0]["finished_at"]:
                stale = now - dt.datetime.fromisoformat(rows[0]["finished_at"]) > dt.timedelta(minutes=40)
            if stale:
                ctx.store.add_alert("감시", "⚠️ 공시 조회가 40분 넘게 성공하지 못했습니다.",
                                    dedupe_key=f"watchdog:dart:{day}:{now.hour}")
                out["dart"] = "stale"
    out["sent"] = send_pending_alerts(ctx)
    return out


def health(ctx: Ctx) -> tuple[bool, dict]:
    """외부 감시용 요약. 비밀 정보는 넣지 않는다."""
    now = now_kst()
    day = now.date()
    last = ctx.store.last_run("daily")
    info = {"time": now.isoformat(timespec="seconds"), "last_daily": None, "last_daily_status": None}
    ok = True
    if last:
        info["last_daily"] = last["day"]
        info["last_daily_status"] = last["status"]
    if is_trading_day(day) and now.hour >= 22:
        today = ctx.store.last_run("daily", day)
        ok = bool(today and today["status"] in ("ok", "partial"))
    return ok, info
