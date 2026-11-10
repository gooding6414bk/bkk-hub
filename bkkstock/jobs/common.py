from __future__ import annotations

import datetime as dt
import fcntl
import logging
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..clients.base import ApiError, NotConfigured
from ..clients.dart import DartClient
from ..clients.datagokr import DataGoKrClient
from ..clients.kis import KisClient
from ..clients.krx import KrxClient
from ..clients.news import NewsClient
from ..clients.overseas import OverseasClient
from ..clients.telegram import Telegram
from ..config import Settings, get_settings
from ..redact import redact
from ..store import Store

log = logging.getLogger("bkkstock")


@dataclass
class Ctx:
    settings: Settings
    store: Store
    kis: KisClient
    krx: KrxClient
    dart: DartClient
    news: NewsClient
    tg: Telegram
    datagokr: DataGoKrClient
    overseas: OverseasClient


def make_ctx(settings: Settings | None = None) -> Ctx:
    s = settings or get_settings()
    store = Store(s.data_dir)
    return Ctx(s, store, KisClient(s, store), KrxClient(s, store), DartClient(s, store),
               NewsClient(s, store), Telegram(s, store), DataGoKrClient(s, store), OverseasClient(s, store))


class AlreadyRunning(Exception):
    pass


@contextmanager
def job_lock(store: Store, name: str):
    lock_dir = store.data_dir / "locks"
    lock_dir.mkdir(exist_ok=True)
    f = open(lock_dir / f"{name}.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise AlreadyRunning(name)
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


@dataclass
class StepResult:
    item: str
    status: str
    count: int | None = None
    recoverable: bool = True
    message: str = ""


@dataclass
class Steps:
    store: Store
    run_id: int
    results: list[StepResult] = field(default_factory=list)

    def run(self, item: str, fn, recoverable: bool = True, min_count: int = 1, compare_prev: bool = True,
            deferrable: bool = False):
        """fn() -> (count, message). 빈 결과(min_count 미만)는 실패, 전일 대비 70% 미만이면 경고.
        deferrable: 아직 안 올라오는 게 정상인 자료(KRX 그날 시세는 다음 영업일 아침)는 '대기'로 두고 아침에 다시 받는다."""
        try:
            count, message = fn()
            status = "ok"
            if count is not None and count < min_count:
                if deferrable:
                    status = "pending"
                    message = (message + " · " if message else "") + "아직 안 올라옴 → 아침 7:30·8:30에 다시 받음"
                else:
                    status = "fail"
                    message = (message + " · " if message else "") + "빈 응답 (실패로 처리)"
            elif compare_prev and count:
                prev = self.store.last_item_count(item, self.run_id)
                if prev and count < prev * 0.7:
                    status = "warn"
                    message = (message + " · " if message else "") + f"전일 {prev:,}건 대비 급감"
        except NotConfigured as e:
            count, status, message = None, "skip", e.message
        except ApiError as e:
            count, status, message = None, "fail", str(e)
        except Exception as e:  # noqa: BLE001 — 한 항목 실패가 나머지를 막지 않게
            log.exception("step %s failed", item)
            count, status, message = None, "fail", f"{type(e).__name__}: {e} | {traceback.format_exc(limit=2)[-300:]}"
        res = StepResult(item, status, count, recoverable, redact(message))
        self.results.append(res)
        self.store.item(self.run_id, item, status, count, recoverable, res.message)
        return res

    def overall(self) -> str:
        sts = [r.status for r in self.results if r.status not in ("skip", "pending")]
        if not sts:
            # 아직 안 올라온 것뿐이면 실패가 아니라 '대기' (아침마다 빨간 '실패'가 뜨던 문제)
            return "pending" if any(r.status == "pending" for r in self.results) else "fail"
        if all(s == "ok" for s in sts):
            return "ok"
        if all(s == "fail" for s in sts):
            return "fail"
        return "partial"

    def summary(self) -> dict:
        return {r.item: {"status": r.status, "count": r.count, "recoverable": r.recoverable, "message": r.message}
                for r in self.results}


ALERT_MAX_AGE_H = 24


def send_pending_alerts(ctx: Ctx) -> int:
    """미발송 알림을 보낸다. 하루 넘게 묵은 것은 하나씩 보내지 않고 '밀린 알림 N건' 한 줄로 요약한다
    (텔레그램을 나중에 켜는 날 몇 주 치가 한꺼번에 쏟아져 진짜 위험을 놓치지 않게)."""
    if not ctx.tg.configured:
        return 0
    n = 0
    now = dt.datetime.now(dt.timezone.utc)
    old, fresh = [], []
    own = {w["code"] for w in ctx.store.watchlist() if w.get("kind") == "보유"}
    for a in ctx.store.unsent_alerts():
        try:
            age = now - dt.datetime.fromisoformat(a["created_at"])
        except (TypeError, ValueError):
            age = dt.timedelta(0)
        limit = 72 if (a.get("code") in own and a.get("kind") in ("공시", "위험")) else ALERT_MAX_AGE_H   # 보유 종목 위험은 3일까지 개별로
        (old if age > dt.timedelta(hours=limit) else fresh).append(a)
    try:
        if old:
            kinds = ", ".join(f"{k} {v}" for k, v in sorted(_count(a["kind"] for a in old).items()))
            ctx.tg.send(f"📦 밀린 알림 {len(old)}건 ({kinds}) — 하루 넘게 지난 것이라 보드 대시보드에서 확인하세요.")
            for a in old:
                ctx.store.mark_sent(a["id"])
            n += 1
        for a in fresh:
            ctx.tg.send(a["message"])
            ctx.store.mark_sent(a["id"])
            n += 1
    except ApiError:
        pass
    return n


def _count(it) -> dict:
    out: dict = {}
    for k in it:
        out[k] = out.get(k, 0) + 1
    return out


def day_or_today(day: dt.date | None) -> dt.date:
    from ..timeutil import today_kst
    return day or today_kst()
