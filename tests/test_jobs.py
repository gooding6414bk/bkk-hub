import datetime as dt

import pandas as pd
import pytest

from bkkstock.clients.base import ApiError
from bkkstock.jobs import daily, report
from bkkstock.jobs.common import make_ctx

DAY = dt.date(2026, 11, 6)


@pytest.fixture
def ctx(settings):
    return make_ctx(settings)


def stub_all(monkeypatch, overrides=None):
    calls = []

    def ok(name, n):
        def f(ctx, day):
            calls.append((name, day))
            return n, f"{name} ok"
        return f

    funcs = {name: ok(name, 100) for name, _, _ in daily.STEPS}
    funcs.update(overrides or {})
    monkeypatch.setattr(daily, "STEPS", [(n, funcs[n], m) for n, _, m in daily.STEPS])
    return calls


def test_holiday_is_skipped(ctx, monkeypatch):
    calls = stub_all(monkeypatch)
    res = daily.run(ctx, dt.date(2026, 10, 9))
    assert res["status"] == "skipped" and calls == []


def test_empty_result_is_failure_and_others_continue(ctx, monkeypatch):
    calls = stub_all(monkeypatch, {"investor": lambda c, d: (0, "비어 있음")})
    res = daily.run(ctx, DAY)
    assert res["status"] == "partial"
    assert res["items"]["investor"]["status"] == "fail"
    assert res["items"]["outlook"]["status"] == "ok"      # 다른 항목은 계속 돈다


def test_api_error_recorded_with_recoverable_flag(ctx, monkeypatch):
    def boom(c, d):
        raise ApiError("kis", "X", "실패")
    stub_all(monkeypatch, {"universe": boom})
    res = daily.run(ctx, DAY)
    assert res["items"]["universe"]["status"] == "fail" and res["items"]["universe"]["recoverable"] is False


def test_count_drop_is_warning(ctx, monkeypatch):
    stub_all(monkeypatch)
    daily.run(ctx, DAY)
    stub_all(monkeypatch, {"investor": lambda c, d: (40, "")})
    res = daily.run(ctx, DAY + dt.timedelta(days=3))
    assert res["items"]["investor"]["status"] == "warn"


def test_catch_up_retries_only_failed_recoverable(ctx, monkeypatch):
    stub_all(monkeypatch, {"krx_prices": lambda c, d: (0, ""), "nxt_prices": lambda c, d: (0, "")})
    daily.run(ctx, DAY)
    calls = stub_all(monkeypatch)
    daily.catch_up(ctx, DAY)
    names = {n for n, _ in calls}
    assert names == {"krx_prices", "outlook"}          # nxt_prices는 복구 불가라 다시 안 받는다
    calls2 = stub_all(monkeypatch)
    assert daily.catch_up(ctx, DAY) == {"status": "nothing"} and calls2 == []


def test_report_message_lists_failures(ctx, monkeypatch):
    stub_all(monkeypatch, {"investor": lambda c, d: (0, "")})
    daily.run(ctx, DAY)
    msg = report.build_message(ctx, DAY)
    assert "11/06(금)" in msg and "수급" in msg and "내일 아침 자동 재시도" in msg


def test_report_without_run_is_alarm(ctx):
    assert "기록이 없습니다" in report.build_message(ctx, DAY)


def test_disclosure_first_seen_never_changes(ctx):
    items = [{"rcept_no": "1", "stock_code": "005930", "corp_name": "가", "report_nm": "유상증자결정", "rcept_dt": "20261106"}]
    new1 = daily.upsert_disclosures(ctx, items, DAY)
    t1 = ctx.store.query("SELECT first_seen_at FROM disclosures")[0]["first_seen_at"]
    new2 = daily.upsert_disclosures(ctx, items, DAY)
    t2 = ctx.store.query("SELECT first_seen_at FROM disclosures")[0]["first_seen_at"]
    assert len(new1) == 1 and new2 == [] and t1 == t2


def test_watch_risk_disclosure_alerts_once(ctx):
    ctx.store.upsert_watch("005930", "가", "관심", None, None, "")
    new = daily.upsert_disclosures(ctx, [{"rcept_no": "9", "stock_code": "005930", "corp_name": "가",
                                           "report_nm": "주요사항보고서(유상증자결정)", "rcept_dt": "20261106"},
                                          {"rcept_no": "10", "stock_code": "005930", "corp_name": "가",
                                           "report_nm": "기업설명회(IR)개최", "rcept_dt": "20261106"}], DAY)
    assert daily.alert_watch_disclosures(ctx, new) == 1        # 관심 종목은 위험 공시만
    assert daily.alert_watch_disclosures(ctx, new) == 0        # 중복 알림 없음


def test_short_code():
    assert daily.short_code("KR7005930003") == "005930"
    assert daily.short_code(5930) == "005930"


def test_krx_empty_in_evening_is_pending_not_failure(ctx, monkeypatch):
    stub_all(monkeypatch, {"krx_prices": lambda c, d: (0, "KRX 비어 있음"), "krx_index": lambda c, d: (0, ""),
                           "outlook": lambda c, d: (0, "")})
    res = daily.run(ctx, DAY)
    assert res["items"]["krx_prices"]["status"] == "pending"
    assert res["status"] == "ok"                          # 대기는 실패로 치지 않는다
    msg = report.build_message(ctx, DAY)
    assert msg.startswith("📊") and "시세 아침에" in msg
    calls = stub_all(monkeypatch)
    daily.catch_up(ctx, DAY)                              # 아침에 대기 항목을 다시 받는다
    assert {n for n, _ in calls} == {"krx_prices", "krx_index", "outlook"}


# ---------------- 2026-11 2차 점검 ----------------
def test_backup_due_ignores_weekend():
    from bkkstock.jobs.watchdog import backup_due
    from bkkstock.timeutil import KST
    fri = dt.datetime(2026, 11, 20, 21, 0, tzinfo=KST)
    for now in (dt.datetime(2026, 11, 22, 9, 1, tzinfo=KST), dt.datetime(2026, 11, 23, 20, 59, tzinfo=KST)):
        assert backup_due(now) == fri                         # 일요일 아침·월요일 밤에도 금요일 백업이면 충분
    assert backup_due(dt.datetime(2026, 11, 24, 9, 1, tzinfo=KST)) == dt.datetime(2026, 11, 23, 21, 0, tzinfo=KST)


def test_health_backup_stale_only_when_weekday_backup_missing(ctx, monkeypatch):
    from bkkstock.jobs import watchdog
    from bkkstock.timeutil import KST
    rid = ctx.store.start_run("backup", dt.date(2026, 11, 20))
    ctx.store.finish_run(rid, "ok", {})
    with ctx.store.db() as con:
        con.execute("UPDATE job_runs SET finished_at=? WHERE id=?", ("2026-11-20T21:01:00+09:00", rid))
    monkeypatch.setattr(watchdog, "now_kst", lambda: dt.datetime(2026, 11, 22, 10, 0, tzinfo=KST))   # 일요일
    assert "backup_stale" not in watchdog.health(ctx)[1]["codes"]
    monkeypatch.setattr(watchdog, "now_kst", lambda: dt.datetime(2026, 11, 24, 10, 0, tzinfo=KST))   # 월요일 백업 없음
    assert "backup_stale" in watchdog.health(ctx)[1]["codes"]


def test_deploy_stale_from_update_state(ctx, monkeypatch):
    import time as _t
    from bkkstock.jobs import watchdog
    now = _t.time()
    assert watchdog.deploy_stale({}, now) is False                                  # 설치 전·시험 환경
    assert not watchdog.deploy_stale({"fetch_ok": str(now - 600), "behind_since": "0"}, now)
    assert watchdog.deploy_stale({"fetch_ok": str(now - 90000), "behind_since": "0"}, now)       # 하루 넘게 fetch 실패
    assert watchdog.deploy_stale({"fetch_ok": str(now - 60), "behind_since": str(now - 90000)}, now)   # 하루 넘게 밀림
    (ctx.store.data_dir / "update.state").write_text(f"fetch_ok={now - 90000:.0f}\nbehind_since=0\nresult=fetch_fail\n")
    assert "deploy_stale" in watchdog.health(ctx)[1]["codes"]


def test_nxt_failed_codes_are_retried_and_leftovers_warn(ctx, monkeypatch):
    from bkkstock.jobs.common import Steps
    monkeypatch.setattr(daily.time, "sleep", lambda s: None)
    monkeypatch.setattr(daily, "_universe_codes", lambda c, d: ["000001", "000002", "000003"])
    tries = {}

    def chart(code, start, end, market="J"):
        tries[code] = tries.get(code, 0) + 1
        if code == "000003" or (code == "000002" and tries[code] == 1):
            raise ApiError("kis", "500", "일시 오류")
        return [{"stck_bsop_date": DAY.strftime("%Y%m%d"), "acml_vol": "10", "stck_clpr": "100"}]
    monkeypatch.setattr(ctx.kis, "daily_chart", chart)
    rid = ctx.store.start_run("daily", DAY)
    res = Steps(ctx.store, rid).run("nxt_prices", lambda: daily.collect_nxt_prices(ctx, DAY))
    assert res.count == 2 and res.status == "warn"               # 000002 는 다시 받아 회복, 000003 만 남음
    assert tries == {"000001": 1, "000002": 2, "000003": 2} and "1건 못 받음" in res.message


def test_redact_json_token():
    from bkkstock.redact import redact
    out = redact('{"access_token":"eyJhbGciOiJ.abc.def","expires_in":86400}')
    assert "eyJ" not in out and '"access_token":"•••"' in out
