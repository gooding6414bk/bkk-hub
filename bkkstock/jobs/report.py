"""21:00 일일 보고 + 백업. 보고가 안 오면 무언가 멈춘 것이다."""

from __future__ import annotations

import datetime as dt
import json

from ..timeutil import is_trading_day, now_kst
from . import backup
from .common import Ctx, send_pending_alerts

LABELS = {
    "universe": "종목", "krx_prices": "시세", "krx_index": "지수", "nxt_prices": "NXT",
    "investor": "수급", "disclosures": "공시", "watch_short_credit": "공매도·신용", "outlook": "점수",
}
WEEK = "월화수목금토일"


def build_message(ctx: Ctx, day: dt.date) -> str:
    last = ctx.store.last_run("daily", day)
    head = f"{day:%m/%d}({WEEK[day.weekday()]})"
    if last is None:
        return f"🚨 주식 수집 {head}: 오늘 일일 수집 기록이 없습니다. 서버나 타이머를 확인하세요."
    latest = ctx.store.latest_item_status("daily", day)
    summary = json.loads(last["summary"] or "{}")
    counts = []
    fails = []
    for item, label in LABELS.items():
        st = latest.get(item)
        info = summary.get(item, {})
        if st == "ok":
            c = info.get("count")
            counts.append(f"{label} {c:,}" if isinstance(c, int) else f"{label} ✓")
        elif st == "skip":
            counts.append(f"{label} 미설정")
        elif st:
            rec = "내일 아침 자동 재시도" if info.get("recoverable", True) else "복구 불가 항목"
            fails.append(f"• {label}: {st} · {(info.get('message') or '')[:120]} ({rec})")
    icon = "📊" if not fails else ("⚠️" if latest.get("universe") == "ok" else "🚨")
    fin = (last.get("finished_at") or "")[11:16]
    lines = [f"{icon} 주식 수집 {head} {'완료 ' + fin if fin else '진행 중'}", " · ".join(counts)]
    if fails:
        lines.append("실패·경고")
        lines.extend(fails)
    watch = ctx.store.watchlist()
    if watch:
        sc = ctx.store.read_clean("outlook", day)
        warn = 0
        if sc is not None and not sc.empty:
            sub = sc[sc["code"].isin([w["code"] for w in watch])]
            warn = int(sub["blocked"].sum())
        lines.append(f"관심·보유 {len(watch)}종목 · 매수 금지 표시 {warn}")
    return "\n".join(lines)


def run(ctx: Ctx, day: dt.date | None = None) -> dict:
    day = day or now_kst().date()
    out: dict = {}
    if is_trading_day(day):
        msg = build_message(ctx, day)
        out["message"] = msg
        if ctx.tg.configured:
            try:
                ctx.tg.send(msg)
                out["sent"] = True
            except Exception as e:  # noqa: BLE001
                out["sent"] = str(e)
    out["alerts"] = send_pending_alerts(ctx)
    out["backup"] = backup.run(ctx, day)
    return out
