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
        if ctx.settings.has("DART_API_KEY"):
            # 21:40에 돈다: 오늘 장중(08~20시) 공시 조회가 한 번도 성공 못 했거나 19시 전에 멈췄으면 알림
            rows = ctx.store.query("SELECT finished_at FROM job_runs WHERE job='dart_poll' AND status='ok' AND day=? "
                                   "ORDER BY id DESC LIMIT 1", (day.isoformat(),))
            last_ok = dt.datetime.fromisoformat(rows[0]["finished_at"]) if rows and rows[0]["finished_at"] else None
            if last_ok is None or last_ok.hour < 19:
                when = last_ok.strftime("%H:%M") if last_ok else "오늘 한 번도"
                ctx.store.add_alert("감시", f"⚠️ 공시 조회가 {when} 이후 성공하지 못했습니다.",
                                    dedupe_key=f"watchdog:dart:{day}")
                out["dart"] = "stale"
    out["sent"] = send_pending_alerts(ctx)
    return out


HEALTH_CODES = {
    "daily_failed": "직전 거래일(또는 오늘 22시 뒤) 일일 수집이 실패했거나 기록이 없음",
    "dart_stale": "장중 공시 조회가 40분 넘게 성공하지 못함",
    "krx_pending": "10시가 지났는데 직전 거래일 KRX 시세를 아직 못 받음",
    "backup_stale": "직전 평일 21시 백업이 없음 (12시간 여유)",
    "disk_low": "디스크 여유 10% 미만",
    "deploy_stale": "자동 업데이트가 하루 넘게 새 커밋을 못 받았거나 저장소에 연결하지 못함",
}
DEPLOY_STALE_SEC = 86400


def deploy_state(data_dir) -> dict:
    """deploy/update.sh 가 남긴 data/update.state. 없으면 {} (설치 전·시험 환경)."""
    try:
        text = (data_dir / "update.state").read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition("=")
        if k:
            out[k.strip()] = v.strip()
    return out


def deploy_stale(st: dict, now_ts: float) -> bool:
    def num(k):
        try:
            return float(st.get(k) or 0)
        except ValueError:
            return 0.0
    if not st:
        return False
    fetch_ok, behind = num("fetch_ok"), num("behind_since")
    return (fetch_ok and now_ts - fetch_ok > DEPLOY_STALE_SEC) or bool(behind and now_ts - behind > DEPLOY_STALE_SEC)


def backup_due(now: dt.datetime) -> dt.datetime:
    """지금 이미 끝났어야 할 가장 최근 백업 차례(평일 21:00 + 12시간 여유가 지난 것)."""
    slot = now.replace(hour=21, minute=0, second=0, microsecond=0)
    while slot.weekday() >= 5 or slot + dt.timedelta(hours=12) > now:
        slot -= dt.timedelta(days=1)
    return slot


def health(ctx: Ctx) -> tuple[bool, dict]:
    """외부 감시용 판정. 비밀 정보는 넣지 않는다. codes는 HEALTH_CODES 안에서만."""
    import shutil

    from ..timeutil import prev_trading_day
    now = now_kst()
    day = now.date()
    codes: list[str] = []
    last = ctx.store.last_run("daily")
    info = {"time": now.isoformat(timespec="seconds"), "last_daily": last["day"] if last else None,
            "last_daily_status": last["status"] if last else None}
    # 1) 일일 수집: 직전 거래일 것이 없거나 실패 / 오늘 22시 뒤 오늘 것이 없거나 실패
    if last is not None:                 # 설치 직후(기록 0)에는 판정하지 않는다
        prev = prev_trading_day(day)
        pr = ctx.store.last_run("daily", prev)
        if pr is None or pr["status"] in ("fail", "running"):
            codes.append("daily_failed")
        if is_trading_day(day) and now.hour >= 22:
            today = ctx.store.last_run("daily", day)
            if not (today and today["status"] in ("ok", "partial")):
                codes.append("daily_failed")
        # 3) 10시가 지나도 직전 거래일 KRX 시세가 대기
        if now.hour >= 10 and is_trading_day(day):
            st = ctx.store.latest_item_status("daily", prev).get("krx_prices")
            if st in ("pending", "fail"):
                codes.append("krx_pending")
    # 2) 장중 공시 조회 멈춤
    if is_trading_day(day) and ctx.settings.has("DART_API_KEY") and (8, 20) <= (now.hour, now.minute) < (20, 30):
        rows = ctx.store.query("SELECT finished_at FROM job_runs WHERE job='dart_poll' AND status='ok' ORDER BY id DESC LIMIT 1")
        if rows and rows[0]["finished_at"]:
            if now - dt.datetime.fromisoformat(rows[0]["finished_at"]) > dt.timedelta(minutes=40):
                codes.append("dart_stale")
    # 4) 백업
    # 백업은 평일 21:00 일일 보고 때만 돈다 → '36시간'으로 보면 주말마다 거짓 경보(일 09시~월 21시).
    # 12시간 여유를 둔 가장 최근 평일 21:00 차례의 백업이 있는지만 본다.
    b = ctx.store.query("SELECT finished_at FROM job_runs WHERE job='backup' AND status IN ('ok','partial') "
                        "ORDER BY id DESC LIMIT 1")
    if b and b[0]["finished_at"] and dt.datetime.fromisoformat(b[0]["finished_at"]) < backup_due(now) - dt.timedelta(hours=2):
        codes.append("backup_stale")
    # 5) 자동 업데이트가 멈췄는지 (텔레그램이 없어도 밖에서 보이게)
    if deploy_stale(deploy_state(ctx.store.data_dir), now.timestamp()):
        codes.append("deploy_stale")
    # 6) 디스크
    try:
        du = shutil.disk_usage(ctx.store.data_dir)
        if du.free / du.total < 0.10:
            codes.append("disk_low")
    except OSError:
        pass
    codes = sorted(set(codes))
    info["codes"] = codes
    info["why"] = [HEALTH_CODES[c] for c in codes]
    return not codes, info
