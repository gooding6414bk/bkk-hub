"""08:00~20:00 10분마다: 새 공시를 받아 처음 본 시각을 기록. 관심 종목 위험 공시는 바로 알림."""

from __future__ import annotations

import datetime as dt

from ..timeutil import now_kst
from .common import Ctx, job_lock, send_pending_alerts
from .daily import alert_watch_disclosures, upsert_disclosures


def run(ctx: Ctx, day: dt.date | None = None) -> dict:
    day = day or now_kst().date()
    with job_lock(ctx.store, "dart_poll"):
        rid = ctx.store.start_run("dart_poll", day)
        try:
            known = {r["rcept_no"] for r in ctx.store.query("SELECT rcept_no FROM disclosures WHERE day=?",
                                                           (day.strftime("%Y%m%d"),))}
            items = ctx.dart.list_all(day, day, stop_at=known or None)
            new = upsert_disclosures(ctx, items, day)
            if new:
                ctx.store.write_raw(day, "dart_poll", new)
            alerts = alert_watch_disclosures(ctx, new)
            ctx.store.finish_run(rid, "ok", {"new": len(new), "alerts": alerts})
        except Exception as e:  # noqa: BLE001
            ctx.store.finish_run(rid, "fail", error=str(e))
            raise
        sent = send_pending_alerts(ctx)
        return {"status": "ok", "new": len(new), "alerts": alerts, "sent": sent}
