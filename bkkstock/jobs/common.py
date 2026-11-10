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
        res = StepResult(item, status, count, recoverable, message)
        self.results.append(res)
        self.store.item(self.run_id, item, status, count, recoverable, message)
        return res

    def overall(self) -> str:
        sts = [r.status for r in self.results if r.status not in ("skip", "pending")]
        if not sts:
            return "fail"
        if all(s == "ok" for s in sts):
            return "ok"
        if all(s == "fail" for s in sts):
            return "fail"
        return "partial"

    def summary(self) -> dict:
        return {r.item: {"status": r.status, "count": r.count, "recoverable": r.recoverable, "message": r.message}
                for r in self.results}


def send_pending_alerts(ctx: Ctx) -> int:
    if not ctx.tg.configured:
        return 0
    n = 0
    for a in ctx.store.unsent_alerts():
        try:
            ctx.tg.send(a["message"])
            ctx.store.mark_sent(a["id"])
            n += 1
        except ApiError:
            break
    return n


def day_or_today(day: dt.date | None) -> dt.date:
    from ..timeutil import today_kst
    return day or today_kst()
