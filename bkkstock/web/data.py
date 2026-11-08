"""화면에 보여줄 데이터를 모은다. 무거운 계산은 하지 않고 저장된 결과를 읽는다."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import time
from functools import lru_cache

import pandas as pd

from ..analysis import outlook
from ..analysis.flags import horse_events
from ..analysis.history import investor_history, latest_universe, price_history
from ..clients.base import ApiError
from ..config import mask
from ..jobs.common import Ctx
from ..master import stocks_only
from ..timeutil import is_trading_day, now_kst, trading_days_back
from . import charts
from .apis import APIS

ITEM_LABELS = {
    "universe": "종목·시장조치", "krx_prices": "KRX 시세", "krx_index": "지수", "nxt_prices": "NXT 시세",
    "investor": "투자자 수급", "disclosures": "공시", "watch_short_credit": "공매도·신용", "outlook": "참고 점수",
}
UNRECOVERABLE = ["universe", "nxt_prices"]
JOB_LABELS = {"dart_poll": "공시 조회", "morning": "아침 작업", "backup": "백업", "backfill": "과거 데이터 채우기",
              "report": "일일 보고", "watchdog": "감시"}


def ref_day(ctx: Ctx) -> dt.date | None:
    return ctx.store.latest_clean_day("prices_krx")


def _outlook_df(ctx: Ctx, day: dt.date | None) -> pd.DataFrame:
    if day is None:
        return pd.DataFrame()
    df = ctx.store.read_clean("outlook", day)
    return df if df is not None else pd.DataFrame()


_search_cache: dict = {"t": 0, "df": None}


def universe_index(ctx: Ctx) -> pd.DataFrame:
    if _search_cache["df"] is not None and time.time() - _search_cache["t"] < 600:
        return _search_cache["df"]
    uni, _ = latest_universe(ctx.store, now_kst().date())
    if uni.empty:
        day = ref_day(ctx)
        p = ctx.store.read_clean("prices_krx", day) if day else None
        df = p[["code", "name", "market"]] if p is not None and not p.empty else pd.DataFrame(columns=["code", "name", "market"])
    else:
        df = stocks_only(uni)[["code", "name", "market"]]
    _search_cache.update(t=time.time(), df=df)
    return df


def search(ctx: Ctx, q: str, limit: int = 12) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    df = universe_index(ctx)
    if df.empty:
        return []
    m = df[df["code"].str.startswith(q) | df["name"].str.contains(q, case=False, regex=False)]
    m = m.assign(_exact=(m["name"] == q) | (m["code"] == q)).sort_values(["_exact", "name"], ascending=[False, True])
    return m.head(limit)[["code", "name", "market"]].to_dict("records")


def name_of(ctx: Ctx, code: str) -> str:
    df = universe_index(ctx)
    hit = df[df["code"] == code]
    return str(hit.iloc[0]["name"]) if len(hit) else ""


def _flags(row: dict | None) -> list[dict]:
    if not row:
        return []
    try:
        return json.loads(row.get("flags") or "[]")
    except ValueError:
        return []


def watch_rows(ctx: Ctx) -> list[dict]:
    day = ref_day(ctx)
    out_df = _outlook_df(ctx, day)
    watch = ctx.store.watchlist()
    missing = [w["code"] for w in watch if out_df.empty or w["code"] not in set(out_df["code"])]
    if missing and day:
        extra = _cached_compute(ctx.store.data_dir.as_posix(), day.isoformat(), tuple(sorted(missing)), _mtime(ctx))
        if extra is not None and not extra.empty:
            out_df = pd.concat([out_df, extra], ignore_index=True) if not out_df.empty else extra
    by_code = {r["code"]: r for r in out_df.to_dict("records")} if not out_df.empty else {}
    prices = ctx.store.read_clean("prices_krx", day) if day else None
    pmap = {}
    if prices is not None and not prices.empty:
        for r in prices.to_dict("records"):
            pmap[str(r.get("code"))] = r
    last_disc = {}
    if watch:
        qs = ",".join("?" * len(watch))
        for r in ctx.store.query(f"SELECT * FROM disclosures WHERE stock_code IN ({qs}) ORDER BY rcept_dt DESC, rcept_no DESC",
                                 [w["code"] for w in watch]):
            last_disc.setdefault(r["stock_code"], r)
    rows = []
    for w in watch:
        o = by_code.get(w["code"])
        p = pmap.get(w["code"], {})
        close = _num(p.get("close"))
        chg = _num(p.get("change_pct"))
        pnl = None
        if close and w.get("qty") and w.get("avg_price"):
            pnl = (close - w["avg_price"]) * w["qty"]
        rows.append({
            **w, "close": close, "change_pct": chg, "pnl": pnl,
            "pnl_pct": (close / w["avg_price"] - 1) * 100 if close and w.get("avg_price") else None,
            "score": o.get("score") if o else None, "grade": o.get("grade") if o else "이력 부족",
            "arrow": o.get("arrow") if o else "·", "blocked": bool(o.get("blocked")) if o else False,
            "enough": bool(o.get("enough_history")) if o else False,
            "ret20": o.get("ret20") if o else None, "flags": _flags(o),
            "last_disc": last_disc.get(w["code"]),
        })
    return rows


def _mtime(ctx: Ctx) -> float:
    try:
        return os.path.getmtime(ctx.store.clean_dir / "prices_krx")
    except OSError:
        return 0.0


@lru_cache(maxsize=16)
def _cached_compute(data_dir: str, day: str, codes: tuple, mtime: float):
    from ..store import Store
    return outlook.compute(Store(data_dir), dt.date.fromisoformat(day), list(codes))


def dashboard(ctx: Ctx) -> dict:
    day = ref_day(ctx)
    today = now_kst().date()
    last = ctx.store.last_run("daily")
    latest = ctx.store.latest_item_status("daily", dt.date.fromisoformat(last["day"])) if last else {}
    summary = json.loads(last["summary"] or "{}") if last else {}
    items = [{"item": k, "label": v, "status": latest.get(k), "count": (summary.get(k) or {}).get("count"),
              "message": (summary.get(k) or {}).get("message", "")} for k, v in ITEM_LABELS.items()]
    polls = ctx.store.query("SELECT * FROM job_runs WHERE job='dart_poll' ORDER BY id DESC LIMIT 1")
    out_df = _outlook_df(ctx, day)
    horses, material = [], []
    if not out_df.empty:
        for r in out_df.to_dict("records"):
            for f in _flags(r):
                if f["kind"] == "경주마":
                    horses.append({**r, "flag": f})
                elif f["kind"] == "재료급등" and f["level"] == "warn":
                    material.append({**r, "flag": f})
        horses.sort(key=lambda r: r["flag"].get("since", ""), reverse=True)
        material.sort(key=lambda r: r["flag"].get("since", ""), reverse=True)
    risky = ctx.store.query(
        """SELECT * FROM disclosures WHERE tags != '' AND (tags LIKE '%희석%' OR tags LIKE '%부실%' OR tags LIKE '%매도압력%')
           AND stock_code != '' ORDER BY first_seen_at DESC LIMIT 15""")
    apis = api_cards(ctx)
    return {
        "day": day, "today": today, "trading_today": is_trading_day(today), "last_run": last, "item_status": items,
        "last_poll": polls[0] if polls else None, "watch": watch_rows(ctx), "horses": horses[:15],
        "material": material[:10], "risky_disclosures": risky,
        "alerts": ctx.store.query("SELECT * FROM alerts ORDER BY id DESC LIMIT 8"),
        "api_ok": sum(1 for a in apis if a["state"] == "ok"),
        "api_bad": sum(1 for a in apis if a["state"] == "fail"),
        "api_missing": sum(1 for a in apis if a["state"] == "missing" and not a.get("optional")),
        "n_scored": len(out_df),
    }


def stock_detail(ctx: Ctx, code: str) -> dict:
    day = ref_day(ctx)
    name = name_of(ctx, code)
    out_df = _outlook_df(ctx, day)
    o = None
    if not out_df.empty and code in set(out_df["code"]):
        o = out_df[out_df["code"] == code].iloc[0].to_dict()
    elif day:
        extra = _cached_compute(ctx.store.data_dir.as_posix(), day.isoformat(), (code,), _mtime(ctx))
        if extra is not None and not extra.empty:
            o = extra.iloc[0].to_dict()
    hist = price_history(ctx.store, day, 60) if day else pd.DataFrame()
    h = hist[hist["code"] == code].sort_values("date") if not hist.empty else pd.DataFrame()
    chart = ""
    last_px = {}
    if not h.empty:
        name = name or str(h.iloc[-1].get("name") or "")
        ev = horse_events(h)
        dates = list(h["date"])
        marks = [dates.index(d) for d in ev["date"] if d in dates] if not ev.empty else []
        chart = charts.price_chart([d.isoformat() for d in dates], list(h["close"]), list(h["value"].fillna(0)),
                                   list(pd.to_numeric(h["nxt_value"], errors="coerce").fillna(0)), marks)
        lr = h.iloc[-1]
        last_px = {"close": _num(lr.get("close")), "change_pct": _num(lr.get("change_pct")), "date": lr["date"],
                   "value": _num(lr.get("total_value")), "nxt_share": _share(lr)}
    inv = investor_history(ctx.store, day, 20) if day else pd.DataFrame()
    flows = None
    if not inv.empty:
        iv = inv[inv["code"] == code]
        if len(iv):
            flows = {k: float(iv[c].fillna(0).sum()) for k, c in
                     (("외국인", "frgn_ntby_tr_pbmn"), ("기관", "orgn_ntby_tr_pbmn"), ("개인", "prsn_ntby_tr_pbmn"))}
            flows["days"] = len(iv)
    discs = ctx.store.query("SELECT * FROM disclosures WHERE stock_code=? ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 25", (code,))
    hist_scores = []
    for d in ctx.store.clean_days("outlook")[-40:]:
        df = ctx.store.read_clean("outlook", d)
        if df is not None and code in set(df["code"]):
            hist_scores.append(float(df[df["code"] == code].iloc[0]["score"]))
    comps = []
    if o:
        comps = charts.bars_pm([(outlook.EXPLAIN[k], float(o.get(k) or 0)) for k in
                                ("reversal", "lottery", "lowvol", "flow", "flag_penalty")], 20)
    watch = {w["code"]: w for w in ctx.store.watchlist()}.get(code)
    return {"code": code, "name": name, "day": day, "o": o, "flags": _flags(o), "chart": chart, "px": last_px,
            "flows": flows, "discs": discs, "score_spark": charts.sparkline(hist_scores, 160, 36),
            "n_score_days": len(hist_scores), "comps": comps, "watch": watch, "model": outlook.MODEL_VERSION}


def news(ctx: Ctx, code: str, force: bool = False) -> dict:
    rows = ctx.store.query("SELECT * FROM news_cache WHERE code=?", (code,))
    if rows and not force:
        age = now_kst() - dt.datetime.fromisoformat(rows[0]["fetched_at"])
        if age < dt.timedelta(minutes=30):
            return {"items": json.loads(rows[0]["items"]), "fetched_at": rows[0]["fetched_at"], "source": ctx.news.source}
    if ctx.settings.demo:
        return {"items": [], "error": "샘플 데이터 모드에서는 뉴스를 받지 않습니다 (가상 회사입니다).", "source": ctx.news.source}
    name = name_of(ctx, code) or code
    try:
        items = ctx.news.search(f"{name} 주가" if ctx.news.source == "google_rss" else name, 15)
    except ApiError as e:
        return {"items": [], "error": str(e), "source": ctx.news.source}
    from ..timeutil import iso_kst
    with ctx.store.db() as con:
        con.execute("INSERT OR REPLACE INTO news_cache(code, fetched_at, items) VALUES (?,?,?)",
                    (code, iso_kst(), json.dumps(items, ensure_ascii=False)))
    return {"items": items, "fetched_at": iso_kst(), "source": ctx.news.source}


def api_cards(ctx: Ctx) -> list[dict]:
    st = ctx.store.api_status()
    cards = []
    for a in APIS:
        keys = [{"name": k, "label": lbl, "set": bool(ctx.settings.get(k)), "masked": mask(ctx.settings.get(k))}
                for k, lbl in a["keys"]]
        required = [k for k in keys if "선택" not in k["label"]]
        statuses = [st.get(i) for i in a["status_ids"] if st.get(i)]
        last_ok = max((s.get("last_ok_at") or "" for s in statuses), default="")
        last_fail = max((s.get("last_fail_at") or "" for s in statuses), default="")
        err = ""
        for s in statuses:
            if (s.get("last_fail_at") or "") == last_fail:
                err = s.get("last_error") or ""
        calls = sum((s.get("calls_today") or 0) for s in statuses if s.get("calls_day") == now_kst().date().isoformat())
        if not a.get("public") and required and not all(k["set"] for k in required) and a["id"] != "news":
            state = "missing"
        elif last_fail and last_fail > last_ok:
            state = "fail"
        elif last_ok:
            state = "ok"
        else:
            state = "unknown"
        extra = []
        if a["id"] == "kis":
            ti = ctx.kis.token_info()
            if ti:
                extra.append(f"접근 토큰 만료 {ti['expires_at'][:16].replace('T', ' ')}")
        if a["id"] == "krx":
            note = (st.get("krx") or {}).get("note")
            if note:
                extra.append("인증 방식: " + ("헤더" if note == "auth=header" else "쿼리 파라미터"))
        if a["id"] == "news":
            extra.append("현재 사용: " + ("네이버 검색 API" if ctx.news.source == "naver" else "구글 뉴스 RSS (키 불필요)"))
        cards.append({**a, "key_list": keys, "state": state, "last_ok": last_ok, "last_fail": last_fail,
                      "error": err, "calls_today": calls, "extra": extra})
    return cards


def ops(ctx: Ctx) -> dict:
    runs = ctx.store.query("SELECT * FROM job_runs WHERE job='daily' ORDER BY id DESC LIMIT 40")
    days: dict[str, dict] = {}
    for r in reversed(runs):
        days.setdefault(r["day"], {"day": r["day"], "runs": 0})
        days[r["day"]]["runs"] += 1
        days[r["day"]]["status"] = r["status"]
        days[r["day"]]["finished_at"] = r["finished_at"]
    table = []
    for d in sorted(days, reverse=True)[:12]:
        latest = ctx.store.latest_item_status("daily", dt.date.fromisoformat(d))
        table.append({**days[d], "items": latest})
    streaks = {}
    today = now_kst().date()
    for item in UNRECOVERABLE:
        n = 0
        for d in reversed(trading_days_back(today, 120)):
            if d == today and now_kst().hour < 21:
                continue
            if ctx.store.latest_item_status("daily", d).get(item) == "ok":
                n += 1
            else:
                break
        streaks[item] = n
    du = shutil.disk_usage(ctx.store.data_dir)
    backups = sorted((p.name, p.stat().st_size) for p in ctx.store.backup_dir.iterdir()) if ctx.store.backup_dir.exists() else []
    other = ctx.store.query("SELECT job, MAX(finished_at) AS at, status FROM job_runs WHERE job != 'daily' GROUP BY job")
    for r in other:
        r["label"] = JOB_LABELS.get(r["job"], r["job"])
    first_week = ""
    fw = ctx.store.data_dir / "first_week.txt"
    if fw.exists():
        first_week = fw.read_text(encoding="utf-8")
    return {"table": table, "labels": ITEM_LABELS, "streaks": streaks, "disk": du, "data_size": _dir_size(ctx.store.data_dir),
            "backups": backups[-10:], "other": other, "verify": outlook.verify(ctx.store), "first_week": first_week,
            "remote_backup": bool(ctx.settings.get("BACKUP_CMD"))}


_size_cache = {"t": 0, "v": 0}


def _dir_size(p) -> int:
    if time.time() - _size_cache["t"] < 600:
        return _size_cache["v"]
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    _size_cache.update(t=time.time(), v=total)
    return total


def _num(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _share(row) -> float | None:
    tv = _num(row.get("total_value"))
    nv = _num(row.get("nxt_value"))
    if tv and nv:
        return nv / tv * 100
    return None


def gibalhae_summary() -> dict | None:
    """대시보드의 기발해 카드: 최근 날짜 상태와 할 일."""
    try:
        from pipeline import keys as gkeys
        from pipeline import webui as gweb
        from pipeline import common as gc
    except Exception:  # noqa: BLE001
        return None
    try:
        days = [gweb._day_row(d) for d in list(reversed(gc.list_days()))[:6]]
        kp = gkeys.public()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "days": [], "todo": [], "keys_missing": []}
    todo = []
    for d in days:
        s, b = d.get("shorts") or {}, d.get("blog") or {}
        if s.get("status") == "draft" or b.get("status") == "draft":
            todo.append(f"{d['date']} 승인 대기")
        elif s.get("status") == "approved":
            todo.append(f"{d['date']} 영상 만들기 대기")
        elif s.get("status") == "built" and not s.get("youtube"):
            todo.append(f"{d['date']} 유튜브 올리기 대기")
        if b.get("status") in ("approved", "built") and not b.get("url"):
            todo.append(f"{d['date']} 블로그 올리기 대기")
    required = ("json2video", "youtube_token")       # 쇼츠 제작·올리기에 꼭 필요한 것 (keys.GUIDE 이름)
    missing = [v["label"] for k, v in kp.items() if k in required and not v["set"]]
    return {"days": days, "todo": todo[:6], "keys_missing": missing}
