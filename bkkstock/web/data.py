"""화면에 보여줄 데이터를 모은다. 무거운 계산은 하지 않고 저장된 결과를 읽는다."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import time
from functools import lru_cache

import pandas as pd

from ..analysis import flows as flowmod
from ..analysis import forecast as fcmod
from ..analysis import market as marketmod
from ..analysis import newsread
from ..analysis import outlook
from ..analysis.flags import horse_events
from ..analysis.history import _to_num, investor_history, latest_universe, price_history
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
            "fc": forecast_for(ctx, w["code"]),
            "value_now": close * w["qty"] if close and w.get("qty") else None,
            "pnl_today": (close * w["qty"] * (chg / (100 + chg))) if close and w.get("qty") and chg is not None else None,
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


@lru_cache(maxsize=4)
def _market_cached(data_dir: str, day: str, mtime: float, ov_mtime: float):
    from ..store import Store
    return marketmod.overview(Store(data_dir), dt.date.fromisoformat(day))


def market_overview(ctx: Ctx) -> dict | None:
    day = ref_day(ctx)
    if not day:
        return None
    try:
        return _market_cached(ctx.store.data_dir.as_posix(), day.isoformat(), _mtime(ctx), _overseas_mtime(ctx))
    except Exception:  # noqa: BLE001
        return None


def check_items(ctx: Ctx, rows: list[dict]) -> list[str]:
    """보유·관심 종목에서 오늘 볼 만한 것을 자동으로 고른다."""
    out = []
    day = ref_day(ctx)
    inv = investor_history(ctx.store, day, 10) if day else pd.DataFrame()
    since = (now_kst().date() - dt.timedelta(days=3)).strftime("%Y%m%d")
    for w in rows:
        nm = w.get("name") or w["code"]
        for f in w.get("flags") or []:
            if f["level"] == "block":
                out.append(f"⛔ {nm}: {f['text']}")
        if w.get("change_pct") is not None and abs(w["change_pct"]) >= 5:
            out.append(f"{'📈' if w['change_pct'] > 0 else '📉'} {nm}: 하루 {w['change_pct']:+.1f}% 움직임")
        if not inv.empty:
            iv = inv[inv["code"] == w["code"]]
            if len(iv):
                br = flowmod.breakdown(iv)
                st = (br or {}).get("streak", {})
                for who in ("외국인", "기관"):
                    k = st.get(who, 0)
                    if abs(k) >= 4:
                        out.append(f"{'🟢' if k > 0 else '🔴'} {nm}: {who} {abs(k)}일 연속 순{'매수' if k > 0 else '매도'}")
        new = ctx.store.query("SELECT report_nm, tags FROM disclosures WHERE stock_code=? AND rcept_dt>=? ORDER BY rcept_dt DESC",
                              (w["code"], since))
        if new:
            risky = [d for d in new if any(t in (d["tags"] or "") for t in ("희석", "부실", "매도압력", "소송"))]
            out.append(f"📄 {nm}: 최근 3일 공시 {len(new)}건" + (f" (위험 분류 {len(risky)}건)" if risky else ""))
        fc = w.get("fc")
        if fc and fc.get("us_ready") and abs(fc.get("pred_gap") or 0) >= 0.01:
            out.append(f"🌙 {nm}: 밤사이 해외 반영 예상 시초가 {fc['pred_gap'] * 100:+.1f}% (참고값)")
    return out[:12]


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
    watch = watch_rows(ctx)
    own = [w for w in watch if w.get("kind") == "보유" and w.get("value_now")]
    totals = None
    if own:
        totals = {"value": sum(w["value_now"] for w in own),
                  "pnl": sum(w["pnl"] for w in own if w.get("pnl") is not None),
                  "pnl_today": sum(w["pnl_today"] for w in own if w.get("pnl_today") is not None)}
        cost = totals["value"] - totals["pnl"]
        totals["pnl_pct"] = totals["pnl"] / cost * 100 if cost else None
    return {
        "market": market_overview(ctx), "totals": totals, "checks": check_items(ctx, watch),
        "day": day, "today": today, "trading_today": is_trading_day(today), "last_run": last, "item_status": items,
        "last_poll": polls[0] if polls else None, "watch": watch, "horses": horses[:15],
        "material": material[:10], "risky_disclosures": risky,
        "alerts": ctx.store.query("SELECT * FROM alerts ORDER BY id DESC LIMIT 8"),
        "api_ok": sum(1 for a in apis if a["state"] == "ok"),
        "api_bad": sum(1 for a in apis if a["state"] == "fail"),
        "api_missing": sum(1 for a in apis if a["state"] == "missing" and not a.get("optional")),
        "n_scored": len(out_df),
    }


def _overseas_mtime(ctx: Ctx) -> float:
    try:
        return os.path.getmtime(ctx.store.clean_dir / "overseas")
    except OSError:
        return 0.0


@lru_cache(maxsize=4)
def _fc_context(data_dir: str, day: str, mtime: float, ov_mtime: float):
    from ..store import Store
    return fcmod.build_context(Store(data_dir), dt.date.fromisoformat(day))


def fc_context(ctx: Ctx):
    day = ref_day(ctx)
    if not day:
        return None
    return _fc_context(ctx.store.data_dir.as_posix(), day.isoformat(), _mtime(ctx), _overseas_mtime(ctx))


@lru_cache(maxsize=64)
def _forecast_cached(data_dir: str, day: str, mtime: float, ov_mtime: float, code: str, hour: int):
    c = _fc_context(data_dir, day, mtime, ov_mtime)
    return fcmod.forecast(c, code) if c else None


def forecast_for(ctx: Ctx, code: str) -> dict | None:
    day = ref_day(ctx)
    if not day:
        return None
    try:
        return _forecast_cached(ctx.store.data_dir.as_posix(), day.isoformat(), _mtime(ctx), _overseas_mtime(ctx),
                                code, now_kst().hour)
    except Exception:  # noqa: BLE001 — 예측가가 실패해도 화면은 뜬다
        return None


def _index_series(ctx: Ctx, days: list, name: str) -> dict:
    out = {}
    for d in days:
        df = ctx.store.read_clean("index", d)
        if df is None or df.empty or "name" not in df:
            continue
        hit = df[df["name"].astype(str) == name]
        if not hit.empty:
            try:
                out[d] = float(str(hit.iloc[0]["close"]).replace(",", ""))
            except (TypeError, ValueError):
                pass
    return out


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
    hist = price_history(ctx.store, day, 120) if day else pd.DataFrame()
    h = hist[hist["code"] == code].sort_values("date") if not hist.empty else pd.DataFrame()
    chart = rel_chart = ""
    last_px = {}
    market = ""
    stats = {k: None for k in ("ret5", "ret20", "ret60", "vol20", "hi", "lo", "from_hi", "days", "value_x")}
    if not h.empty:
        name = name or str(h.iloc[-1].get("name") or "")
        market = str(h.iloc[-1].get("market") or "")
        ev = horse_events(h)
        dates = list(h["date"])
        marks = [dates.index(d) for d in ev["date"] if d in dates] if not ev.empty else []
        num = lambda col: [(_num(v)) for v in h[col]] if col in h else [None] * len(h)  # noqa: E731
        chart = charts.candle_chart([d.isoformat() for d in dates], num("open"), num("high"), num("low"), num("close"),
                                    list(h["value"].fillna(0)), list(pd.to_numeric(h["nxt_value"], errors="coerce").fillna(0)),
                                    marks=marks)
        # 지수 대비 상대 성과 (처음 = 100)
        idx_name = "코스닥" if market.upper().startswith("KOSDAQ") or market == "코스닥" else "코스피"
        idx = _index_series(ctx, dates, idx_name)
        closes = num("close")
        if closes and closes[0]:
            base_i = next((idx[d] for d in dates if d in idx), None)
            s_stock = [c / closes[0] * 100 if c else None for c in closes]
            s_idx = [(idx[d] / base_i * 100) if (d in idx and base_i) else None for d in dates]
            series = [(name or code, s_stock, "s1")]
            if any(v is not None for v in s_idx):
                series.append((idx_name, s_idx, "s2"))
            rel_chart = charts.multi_line(series, [d.isoformat() for d in dates], zero=100, fmt=lambda v: f"{v:,.0f}")
        lr = h.iloc[-1]
        last_px = {"close": _num(lr.get("close")), "change_pct": _num(lr.get("change_pct")), "date": lr["date"],
                   "value": _num(lr.get("total_value")), "nxt_share": _share(lr), "mktcap": _num(lr.get("mktcap"))}
        c_ser = pd.Series(closes, dtype=float)
        rets = c_ser.pct_change(fill_method=None)
        for k in (5, 20, 60):
            if len(c_ser) > k and c_ser.iloc[-k - 1]:
                stats[f"ret{k}"] = (c_ser.iloc[-1] / c_ser.iloc[-k - 1] - 1) * 100
        if len(rets.dropna()) >= 10:
            stats["vol20"] = float(rets.tail(20).std() * 100)
        stats["hi"] = float(c_ser.max())
        stats["lo"] = float(c_ser.min())
        stats["from_hi"] = (c_ser.iloc[-1] / c_ser.max() - 1) * 100
        stats["days"] = len(c_ser)
        v20 = h["total_value"].tail(20).mean() if "total_value" in h else None
        stats["value_x"] = float(h["total_value"].iloc[-1] / v20) if v20 else None
    inv = investor_history(ctx.store, day, 60) if day else pd.DataFrame()
    flow = None
    flow_chart = ""
    if not inv.empty:
        iv = inv[inv["code"] == code]
        if len(iv):
            flow = flowmod.breakdown(iv, h if not h.empty else None)
            if flow and flow.get("cum"):
                series = [(k, [v / 1e8 for v in vals], cls) for (k, vals), cls in zip(flow["cum"].items(), ("s1", "s2", "s3"))]
                flow_chart = charts.multi_line(series, flow["dates"], zero=0, fmt=lambda v: f"{v:,.0f}", unit="억")
    sc = _short_credit(ctx, day, code)
    discs = ctx.store.query("SELECT * FROM disclosures WHERE stock_code=? ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 40", (code,))
    disc_sum = newsread.disclosure_summary(discs)
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
    return {"code": code, "name": name, "day": day, "o": o, "flags": _flags(o), "chart": chart, "rel_chart": rel_chart,
            "px": last_px, "stats": stats, "market": market, "flow": flow, "flow_chart": flow_chart, "sc": sc,
            "discs": discs[:25], "disc_sum": disc_sum, "disc_bar": charts.tone_bar(disc_sum["ratio"]),
            "score_spark": charts.sparkline(hist_scores, 160, 36), "n_score_days": len(hist_scores), "comps": comps,
            "watch": watch, "model": outlook.MODEL_VERSION, "fc": forecast_for(ctx, code), "fc_model": fcmod.MODEL_VERSION}


def _short_credit(ctx: Ctx, day, code: str) -> dict:
    if not day:
        return {}
    days = trading_days_back(day, 60)
    frames = {}
    for t in ("short", "credit"):
        df = ctx.store.read_range(t, days)
        frames[t] = df[df["code"].astype(str) == code] if not df.empty and "code" in df else None
    return flowmod.short_credit(frames["short"], frames["credit"])


def news(ctx: Ctx, code: str, force: bool = False) -> dict:
    rows = ctx.store.query("SELECT * FROM news_cache WHERE code=?", (code,))
    if rows and not force:
        age = now_kst() - dt.datetime.fromisoformat(rows[0]["fetched_at"])
        if age < dt.timedelta(minutes=30):
            return _with_summary({"items": json.loads(rows[0]["items"]), "fetched_at": rows[0]["fetched_at"],
                                  "source": ctx.news.source}, name_of(ctx, code))
    if ctx.settings.demo:
        return _with_summary({"items": _demo_news(name_of(ctx, code) or code), "fetched_at": now_kst().isoformat(timespec="seconds"),
                              "source": "demo"}, name_of(ctx, code))
    name = name_of(ctx, code) or code
    try:
        items = ctx.news.search(f"{name} 주가" if ctx.news.source == "google_rss" else name, 15)
    except ApiError as e:
        return {"items": [], "error": str(e), "source": ctx.news.source}
    from ..timeutil import iso_kst
    with ctx.store.db() as con:
        con.execute("INSERT OR REPLACE INTO news_cache(code, fetched_at, items) VALUES (?,?,?)",
                    (code, iso_kst(), json.dumps(items, ensure_ascii=False)))
    return _with_summary({"items": items, "fetched_at": iso_kst(), "source": ctx.news.source}, name)


def _with_summary(d: dict, subject: str = "") -> dict:
    sm = newsread.summarize(d.get("items") or [], subject)
    d["items"] = sm.pop("items")
    d["summary"] = sm
    d["bar"] = charts.tone_bar(sm["ratio"])
    return d


def _demo_news(name: str) -> list[dict]:
    base = [("{n}, 2분기 최대 실적…영업이익 컨센서스 상회", "샘플경제"), ("{n} 대규모 공급계약 체결", "샘플일보"),
            ("{n} 공급계약 체결 소식에 강세", "샘플투데이"), ("{n}, 운영자금 마련 위해 유상증자 결정", "샘플뉴스"),
            ("증권가 \"{n} 목표주가 상향\"", "샘플리서치"), ("{n} 관련 소송 1심 결과 주목", "샘플법률"),
            ("{n} 신제품 공개 행사", "샘플테크"), ("외국인 순매도에 {n} 약세", "샘플마켓")]
    now = now_kst()
    return [{"title": t.format(n=name), "link": "#", "published": (now - dt.timedelta(hours=i * 7)).isoformat(timespec="minutes"),
             "source": src} for i, (t, src) in enumerate(base)]


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
            "fc_live": fcmod.live_record(ctx.store), "fc_model": fcmod.MODEL_VERSION,
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
