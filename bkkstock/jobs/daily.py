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
from ..timeutil import is_trading_day, iso_kst, now_kst, prev_trading_day, trading_days_back
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


def _retry(fn, waits=(30, 120)):
    import time
    for w in (*waits, None):
        try:
            return fn()
        except ApiError:
            if w is None:
                raise
            time.sleep(w)


# ---------------- 개별 항목 ----------------
def collect_universe(ctx: Ctx, day: dt.date):
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        z = _retry(lambda m=market: ctx.kis.download_master(m), waits=(30, 120))   # 복구 불가 1순위 — 그날 꼭 받는다
        ctx.store.write_raw_bytes(day, f"kis_master_{market.lower()}", z, ".zip")
        frames.append(parse_master(extract_mst(z), market))
    df = pd.concat(frames, ignore_index=True)
    ctx.store.write_clean("universe", day, df)
    st = stocks_only(df)
    flagged = int((st["halted"].eq("Y") | st["administrative"].eq("Y") | st["market_warning"].isin(["01", "02", "03"])).sum())
    return len(st), f"주권 {len(st):,}종목 (전체 {len(df):,}) · 정지·관리·경보 {flagged}종목"


KRX_REQUIRED = ("ISU_CD", "TDD_CLSPRC", "ACC_TRDVAL", "FLUC_RT")   # 없으면 점수·경주마 판정이 조용히 망가지는 열


def collect_krx_prices(ctx: Ctx, day: dt.date):
    frames, empty = [], []
    for market in ("KOSPI", "KOSDAQ"):
        rows = ctx.krx.daily_trades(market, day)
        ctx.store.write_raw(day, f"krx_trades_{market.lower()}", rows)
        if rows:
            missing = [c for c in KRX_REQUIRED if c not in rows[0]]
            if missing:   # 응답 필드 이름이 바뀌면 '전 종목 중립·경주마 사라짐'이 조용히 생긴다 → 실패로 (원본은 저장됨)
                raise ApiError("krx", "schema", f"{market} 응답에 필수 열이 없습니다: {', '.join(missing)}")
            df = pd.DataFrame(rows).rename(columns=TRADE_COLS)
            df["market"] = market
            frames.append(df)
        else:
            empty.append(market)
    if not frames or empty:
        # 한 시장만 오면 그날 시세가 반쪽이 된다 → 둘 다 올 때까지 '대기'(아침에 다시 받음)
        return 0, ("KRX 일별매매정보 비어 있음" if not frames else f"{', '.join(empty)} 비어 있음") + " (아직 갱신 전일 수 있음)"
    df = pd.concat(frames, ignore_index=True)
    close = pd.to_numeric(df["close"].astype(str).str.replace(",", ""), errors="coerce")
    if close.isna().mean() > 0.05:
        raise ApiError("krx", "schema", f"종가가 빈 종목이 {close.isna().mean():.0%}입니다 (응답 형식 확인 필요)")
    df["code"] = df["code"].map(short_code)
    if "date" not in df.columns:
        df["date"] = day.strftime("%Y%m%d")
    ctx.store.write_clean("prices_krx", day, df)
    return len(df), f"코스피·코스닥 {len(df):,}종목"


def collect_krx_index(ctx: Ctx, day: dt.date):
    frames, empty = [], []
    for fam in ("KOSPI", "KOSDAQ"):
        rows = ctx.krx.index_daily(fam, day)
        ctx.store.write_raw(day, f"krx_index_{fam.lower()}", rows)
        if rows:
            df = pd.DataFrame(rows).rename(columns=INDEX_COLS)
            df["family"] = fam
            frames.append(df)
        else:
            empty.append(fam)
    if not frames or empty:
        return 0, "지수 비어 있음" if not frames else f"{', '.join(empty)} 지수 비어 있음"
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
    # 마지막 '전 종목' 조회 목록도 합친다 — 하루 거래가 0이었거나 오류 난 종목이 그 주 내내 빠지지 않게
    last_full, last_full_day = None, None
    for d in reversed([x for x in ctx.store.clean_days("nxt_universe") if x < day][-10:]):
        u = ctx.store.read_clean("nxt_universe", d)
        if u is not None and "scan" in u.columns and (u["scan"] == "full").any():
            last_full, last_full_day = u, d
            break
    stale = last_full_day is None or len([x for x in trading_days_back(day, 6) if last_full_day < x < day]) >= 5
    full = prev is None or day.weekday() == 0 or stale
    base = set(prev["code"].astype(str)) if prev is not None else set()
    if last_full is not None:
        base |= set(last_full["code"].astype(str))
    targets = codes if full else sorted(base & set(codes))
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
    have = set(ctx.store.clean_days("investor"))
    fill_days = {d.strftime("%Y%m%d") for d in trading_days_back(day, 20)[:-1] if d not in have}
    fill_rows: dict[str, list] = {}
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
            d = r.get("stck_bsop_date")
            if d == want:
                rows.append(dict(r, code=code, date=want))
            elif d in fill_days:
                fill_rows.setdefault(d, []).append(dict(r, code=code, date=d))
    ctx.store.write_raw(day, "kis_investor_un", rows)
    if rows:
        ctx.store.write_clean("investor", day, pd.DataFrame(rows))
    # 응답에 지난 30일치가 같이 온다 — 최근 20거래일 중 수급 파일이 없는 날은 호출 없이 여기서 채운다
    for d, rr in fill_rows.items():
        if len(rr) >= max(1, len(codes) // 2):
            dd = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
            ctx.store.write_raw(dd, "kis_investor_un_fill", rr)
            ctx.store.write_clean("investor", dd, pd.DataFrame(rr))
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
    # 받은 행 수로 판정한다 (관심 종목 수가 아니라). 신용잔고는 공표가 늦어 그날 0건일 수 있다.
    return len(short_rows), f"관심 {len(codes)}종목 공매도 {len(short_rows)} · 신용 {len(credit_rows)}"


def compute_outlook(ctx: Ctx, day: dt.date):
    from ..clients.dart import CLASSIFY_VERSION, classify
    ctx.store.retag_disclosures(classify, CLASSIFY_VERSION)      # 분류 규칙이 바뀐 뒤 첫 계산이면 다시 매김
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


CATCH_UP_DAYS = 10                     # 최근 10거래일까지 훑는다 (아침 두 번을 다 놓친 날도 다시 받게)
CHEAP_ITEMS = {"krx_prices", "krx_index", "outlook", "disclosures"}   # 하루 몇 번 호출이면 되는 것


def catch_up(ctx: Ctx, day: dt.date | None = None) -> dict:
    """복구 가능 항목 중 실패·대기·경고·기록 없음인 것을 다시 받는다 (아침 작업에서 호출).
    직전 거래일은 모든 복구 가능 항목, 그 전 9거래일은 호출이 싼 항목(KRX 시세·지수·점수·공시)만.
    수급(종목마다 1회 호출, 하루 2천여 회)은 직전 거래일만 — 빠진 날은 매일 오는 응답의 지난 행으로 채운다."""
    day = day or prev_trading_day(now_kst().date())
    days = trading_days_back(day, CATCH_UP_DAYS)
    out: dict = {}
    first_run = ctx.store.last_run("daily") is None
    for d in reversed(days):
        latest = ctx.store.latest_item_status("daily", d)
        if not latest:
            if d != day and (first_run or d < _first_daily_day(ctx)):
                continue                      # 설치 전 날짜는 백필이 맡는다
            items = [n for n, r in RECOVERABLE.items() if r and (d == day or n in CHEAP_ITEMS)]
        else:
            items = [n for n, ok in RECOVERABLE.items() if ok and latest.get(n) in ("fail", "pending", "warn", None)
                     and (d == day or n in CHEAP_ITEMS)]
        if not items:
            continue
        if "krx_prices" in items and "outlook" not in items:
            items.append("outlook")
        out[d.isoformat()] = run(ctx, d, force=True, only=items)
    if not out:
        return {"status": "nothing"}
    sts = {v.get("status") for v in out.values()}
    status = "ok" if sts <= {"ok"} else "pending" if sts <= {"pending", "ok"} else "partial"
    return {"status": status, "days": out}


def _first_daily_day(ctx: Ctx) -> dt.date:
    rows = ctx.store.query("SELECT MIN(day) AS d FROM job_runs WHERE job='daily'")
    return dt.date.fromisoformat(rows[0]["d"]) if rows and rows[0]["d"] else now_kst().date()
