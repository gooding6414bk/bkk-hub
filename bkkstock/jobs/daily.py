"""20:30 일일 수집.

복구 불가(그날 찍지 않으면 끝): 종목 목록·시장조치 상태(universe), NXT 거래 종목(nxt).
나머지는 실패해도 다음 날 아침에 자동으로 다시 받는다(catch_up).
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from ..analysis import outlook
from ..analysis.flags import Flag
from ..clients.base import ApiError
from ..clients.krx import INDEX_COLS, TRADE_COLS
from ..clients.kis import to_float, to_int
from ..master import extract_mst, parse_master, stocks_only
from ..store import normalize_code
from ..timeutil import is_trading_day, iso_kst, now_kst, prev_trading_day
from .common import Ctx, Steps, job_lock, log, send_pending_alerts

def short_code(v) -> str:
    """KRX 응답의 종목코드가 표준코드(KR7005930003)로 와도 6자리 단축코드로 맞춘다."""
    s = str(v or "").strip()
    if len(s) == 12 and s.startswith("KR"):
        return s[3:9]
    return s.zfill(6) if s.isdigit() else s


DEFERRABLE = {"krx_prices", "krx_index", "outlook"}   # KRX는 그날 시세를 다음 영업일 아침에 올린다
RECOVERABLE = {
    "universe": False, "krx_prices": True, "krx_index": True, "nxt_prices": False,
    "investor": True, "disclosures": True, "watch_short_credit": True, "outlook": True,
}


# ---------------- 개별 항목 ----------------
def collect_universe(ctx: Ctx, day: dt.date):
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        z = ctx.kis.download_master(market)
        ctx.store.write_raw_bytes(day, f"kis_master_{market.lower()}", z, ".zip")
        frames.append(parse_master(extract_mst(z), market))
    df = pd.concat(frames, ignore_index=True)
    ctx.store.write_clean("universe", day, df)
    st = stocks_only(df)
    flagged = int((st["halted"].eq("Y") | st["administrative"].eq("Y") | st["market_warning"].isin(["01", "02", "03"])).sum())
    return len(st), f"주권 {len(st):,}종목 (전체 {len(df):,}) · 정지·관리·경보 {flagged}종목"


def collect_krx_prices(ctx: Ctx, day: dt.date):
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        rows = ctx.krx.daily_trades(market, day)
        ctx.store.write_raw(day, f"krx_trades_{market.lower()}", rows)
        if rows:
            df = pd.DataFrame(rows).rename(columns=TRADE_COLS)
            df["market"] = market
            frames.append(df)
    if not frames:
        return 0, "KRX 일별매매정보 비어 있음 (아직 갱신 전일 수 있음, 내일 아침 자동 재시도)"
    df = pd.concat(frames, ignore_index=True)
    df["code"] = df["code"].map(short_code)
    if "date" not in df.columns:
        df["date"] = day.strftime("%Y%m%d")
    ctx.store.write_clean("prices_krx", day, df)
    return len(df), f"코스피·코스닥 {len(df):,}종목"


def collect_krx_index(ctx: Ctx, day: dt.date):
    frames = []
    for fam in ("KOSPI", "KOSDAQ"):
        rows = ctx.krx.index_daily(fam, day)
        ctx.store.write_raw(day, f"krx_index_{fam.lower()}", rows)
        if rows:
            df = pd.DataFrame(rows).rename(columns=INDEX_COLS)
            df["family"] = fam
            frames.append(df)
    if not frames:
        return 0, "지수 비어 있음"
    df = pd.concat(frames, ignore_index=True)
    ctx.store.write_clean("index", day, df)
    return len(df), f"지수 {len(df)}개"


def _universe_codes(ctx: Ctx, day: dt.date) -> list[str]:
    df = ctx.store.read_clean("universe", day)
    if df is None or df.empty:
        prev = ctx.store.latest_clean_day("universe")
        df = ctx.store.read_clean("universe", prev) if prev else None
    if df is None or df.empty:
        return []
    return sorted(stocks_only(df)["code"].astype(str).tolist())


def collect_nxt_prices(ctx: Ctx, day: dt.date):
    """NXT 일봉. 월요일·첫 실행은 전 종목을 훑고, 나머지 날은 전날 NXT 목록만 본다."""
    codes = _universe_codes(ctx, day)
    prev_day = ctx.store.latest_clean_day("nxt_universe")
    prev = ctx.store.read_clean("nxt_universe", prev_day) if prev_day and prev_day < day else None
    full = prev is None or day.weekday() == 0
    targets = codes if full else sorted(set(prev["code"].astype(str)) & set(codes))
    rows, errors, err_samples = [], 0, []
    for code in targets:
        try:
            out = ctx.kis.daily_chart(code, day, day, market="NX")
        except ApiError as e:
            errors += 1
            if len(err_samples) < 3:
                err_samples.append(f"{code}:{e.code}")
            if errors > 50 and errors > len(targets) * 0.5:
                raise
            continue
        for r in out:
            if r.get("stck_bsop_date") == day.strftime("%Y%m%d") and (to_int(r.get("acml_vol")) or 0) > 0:
                rows.append({"date": r["stck_bsop_date"], "code": code, "open": r.get("stck_oprc"),
                             "high": r.get("stck_hgpr"), "low": r.get("stck_lwpr"), "close": r.get("stck_clpr"),
                             "volume": r.get("acml_vol"), "value": r.get("acml_tr_pbmn")})
    ctx.store.write_raw(day, "kis_nxt_daily", rows)
    df = pd.DataFrame(rows)
    if len(df):
        ctx.store.write_clean("prices_nxt", day, df)
        ctx.store.write_clean("nxt_universe", day, df[["code"]].assign(scan="full" if full else "prev"))
    msg = f"NXT 거래 {len(df):,}종목 ({'전 종목' if full else '전날 목록'} {len(targets):,}개 조회)"
    if errors:
        msg += f" · 오류 {errors}건 {', '.join(err_samples)}"
    return len(df), msg


def collect_investor(ctx: Ctx, day: dt.date):
    codes = _universe_codes(ctx, day) if ctx.settings.investor_scope == "all" else ctx.store.watch_codes()
    rows, errors, err_samples = [], 0, []
    want = day.strftime("%Y%m%d")
    for code in codes:
        try:
            out = ctx.kis.investor_daily(code, day, market="UN")
        except ApiError as e:
            errors += 1
            if len(err_samples) < 3:
                err_samples.append(f"{code}:{e.code}")
            if errors > 50 and errors > len(codes) * 0.5:
                raise
            continue
        for r in out:
            if r.get("stck_bsop_date") == want:
                rows.append(dict(r, code=code, date=want))
                break
    ctx.store.write_raw(day, "kis_investor_un", rows)
    if rows:
        ctx.store.write_clean("investor", day, pd.DataFrame(rows))
    msg = f"투자자별 매매(통합) {len(rows):,}종목 / {len(codes):,}"
    if errors:
        msg += f" · 오류 {errors}건 {', '.join(err_samples)}"
    return len(rows), msg


def upsert_disclosures(ctx: Ctx, items: list[dict], day_hint: dt.date) -> list[dict]:
    """새로 본 공시만 돌려준다. 처음 본 시각은 절대 바꾸지 않는다."""
    from ..clients.dart import classify
    new = []
    now = iso_kst()
    with ctx.store.db() as con:
        for it in items:
            rno = it.get("rcept_no")
            if not rno:
                continue
            tags = ",".join(classify(it.get("report_nm", "")))
            stock = (it.get("stock_code") or "").strip()
            cur = con.execute(
                """INSERT OR IGNORE INTO disclosures(rcept_no, day, corp_code, corp_name, stock_code, corp_cls,
                   report_nm, flr_nm, rcept_dt, rm, first_seen_at, tags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rno, (it.get("rcept_dt") or day_hint.strftime("%Y%m%d")), it.get("corp_code"), it.get("corp_name"),
                 stock, it.get("corp_cls"), it.get("report_nm"), it.get("flr_nm"), it.get("rcept_dt"),
                 it.get("rm"), now, tags))
            if cur.rowcount:
                new.append(dict(it, tags=tags, first_seen_at=now, stock_code=stock))
    return new


def collect_disclosures(ctx: Ctx, day: dt.date):
    items = ctx.dart.list_all(day, day)
    ctx.store.write_raw(day, "dart_list_eod", items)
    new = upsert_disclosures(ctx, items, day)
    alert_watch_disclosures(ctx, new)
    return len(items), f"공시 {len(items):,}건 (오늘 처음 본 것 {len(new)}건)"


def alert_watch_disclosures(ctx: Ctx, new_items: list[dict]) -> int:
    from ..clients.dart import RISK_TAGS
    watch = {r["code"]: r for r in ctx.store.watchlist()}
    n = 0
    for it in new_items:
        code = it.get("stock_code")
        if code not in watch:
            continue
        tags = set(filter(None, (it.get("tags") or "").split(",")))
        risky = tags & RISK_TAGS
        icon = "🚨" if risky else "📄"
        kind = watch[code]["kind"]
        if not risky and kind != "보유":
            continue
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}"
        msg = f"{icon} [{kind}] {it.get('corp_name')}({code}) 공시\n{it.get('report_nm')}"
        if risky:
            msg += f"\n분류: {', '.join(sorted(risky))}"
        msg += f"\n{url}"
        if ctx.store.add_alert("공시", msg, code, f"disc:{it['rcept_no']}"):
            n += 1
    return n


def collect_watch_short_credit(ctx: Ctx, day: dt.date):
    codes = ctx.store.watch_codes()
    if not codes:
        return None, "관심 종목 없음"
    short_rows, credit_rows = [], []
    want = day.strftime("%Y%m%d")
    for code in codes:
        for r in ctx.kis.short_daily(code, day - dt.timedelta(days=10), day):
            if r.get("stck_bsop_date") == want:
                short_rows.append(dict(r, code=code, date=want))
        for r in ctx.kis.credit_daily(code, day):
            if r.get("deal_date") == want:
                credit_rows.append(dict(r, code=code, date=want))
    ctx.store.write_raw(day, "kis_watch_short", short_rows)
    ctx.store.write_raw(day, "kis_watch_credit", credit_rows)
    if short_rows:
        ctx.store.write_clean("short", day, pd.DataFrame(short_rows))
    if credit_rows:
        ctx.store.write_clean("credit", day, pd.DataFrame(credit_rows))
    return len(codes), f"관심 {len(codes)}종목 공매도 {len(short_rows)} · 신용 {len(credit_rows)}"


def compute_outlook(ctx: Ctx, day: dt.date):
    if ctx.store.read_clean("prices_krx", day) is None:
        return 0, f"{day} KRX 시세가 아직 없어 점수는 시세를 받은 뒤 계산"
    df = outlook.compute(ctx.store, day)
    if df.empty:
        return 0, "시세 이력이 없어 점수를 못 냈습니다"
    ctx.store.write_clean("outlook", day, df)
    alert_watch_flags(ctx, df, day)
    enough = int(df["enough_history"].sum())
    return len(df), f"{len(df):,}종목 (이력 충분 {enough:,}) · 모델 {outlook.MODEL_VERSION}"


def alert_watch_flags(ctx: Ctx, df: pd.DataFrame, day: dt.date) -> int:
    watch = {r["code"]: r for r in ctx.store.watchlist()}
    n = 0
    for row in df[df["code"].isin(list(watch))].to_dict("records"):
        for f in json.loads(row["flags"] or "[]"):
            if f["level"] not in ("block", "warn"):
                continue
            key = f"flag:{row['code']}:{f['kind']}:{f.get('since') or day.isoformat()}"
            icon = "⛔" if f["level"] == "block" else "⚠️"
            msg = f"{icon} [{watch[row['code']]['kind']}] {row['name']}({row['code']})\n{f['text']}"
            if ctx.store.add_alert("위험", msg, row["code"], key):
                n += 1
    return n


STEPS = [
    ("universe", collect_universe, 100),
    ("krx_prices", collect_krx_prices, 100),
    ("krx_index", collect_krx_index, 1),
    ("nxt_prices", collect_nxt_prices, 1),
    ("investor", collect_investor, 1),
    ("disclosures", collect_disclosures, 1),
    ("watch_short_credit", collect_watch_short_credit, 0),
    ("outlook", compute_outlook, 1),
]


def run(ctx: Ctx, day: dt.date | None = None, force: bool = False, only: list[str] | None = None) -> dict:
    day = day or now_kst().date()
    with job_lock(ctx.store, "daily"):
        if not is_trading_day(day) and not force:
            rid = ctx.store.start_run("daily", day)
            ctx.store.finish_run(rid, "skipped", {"reason": "휴장일"})
            return {"status": "skipped"}
        rid = ctx.store.start_run("daily", day)
        steps = Steps(ctx.store, rid)
        for name, fn, min_count in STEPS:
            if only and name not in only:
                continue
            steps.run(name, lambda fn=fn: fn(ctx, day), recoverable=RECOVERABLE[name], min_count=min_count,
                      compare_prev=name not in ("watch_short_credit",), deferrable=name in DEFERRABLE)
        status = steps.overall()
        ctx.store.finish_run(rid, status, steps.summary())
        send_pending_alerts(ctx)
        log.info("daily %s %s", day, status)
        return {"status": status, "run_id": rid, "items": steps.summary()}


def catch_up(ctx: Ctx, day: dt.date | None = None) -> dict:
    """전 거래일 수집에서 실패한 '복구 가능' 항목만 다시 받는다 (아침 작업에서 호출)."""
    day = day or prev_trading_day(now_kst().date())
    latest = ctx.store.latest_item_status("daily", day)
    if not latest:
        return run(ctx, day, force=True, only=[n for n, r in RECOVERABLE.items() if r])
    retry = [n for n, ok in RECOVERABLE.items() if ok and latest.get(n) in ("fail", "pending", None)]
    if not retry:
        return {"status": "nothing"}
    if "krx_prices" in retry and "outlook" not in retry:
        retry.append("outlook")
    return run(ctx, day, force=True, only=retry)
