"""시장 개요: 지수, 상승·하락 종목 수, 거래대금 상위, 60일 신고가·신저가, 밤사이 해외."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..clients.overseas import SYMBOLS
from ..store import Store
from .history import _to_num, overseas_latest, price_history


def overview(store: Store, day: dt.date | None = None) -> dict | None:
    day = day or store.latest_clean_day("prices_krx")
    if not day:
        return None
    today = store.read_clean("prices_krx", day)
    if today is None or today.empty:
        return None
    today = _to_num(today.copy(), ["close", "change_pct", "value", "mktcap"])
    uni = None
    ud = store.latest_clean_day("universe")
    if ud:
        u = store.read_clean("universe", ud)
        if u is not None and not u.empty:
            uni = set(u.loc[u["group"] == "ST", "code"])
    if uni:
        today = today[today["code"].isin(uni)]
    chg = today["change_pct"].dropna()
    out = {"day": day, "up": int((chg > 0).sum()), "down": int((chg < 0).sum()), "flat": int((chg == 0).sum()),
           "limit_up": int((chg >= 29.5).sum()), "limit_down": int((chg <= -29.5).sum())}
    out["top_value"] = today.sort_values("value", ascending=False).head(6)[["code", "name", "close", "change_pct", "value"]].to_dict("records")
    out["top_up"] = today.sort_values("change_pct", ascending=False).head(5)[["code", "name", "close", "change_pct"]].to_dict("records")
    out["top_down"] = today.sort_values("change_pct").head(5)[["code", "name", "close", "change_pct"]].to_dict("records")
    hist = price_history(store, day, 60)
    if not hist.empty:
        if uni:
            hist = hist[hist["code"].isin(uni)]
        g = hist.groupby("code")["close"]
        n = g.size()
        ok = n[n >= 40].index
        last = g.last()[ok]
        out["new_high"] = int((last >= g.max()[ok]).sum())
        out["new_low"] = int((last <= g.min()[ok]).sum())
        out["hl_days"] = int(n.max())
    idx = store.read_clean("index", day)
    out["index"] = []
    if idx is not None and not idx.empty:
        idx = _to_num(idx.copy(), ["close", "change_pct"])
        for fam, want in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
            sub = idx[idx["family"] == fam] if "family" in idx else idx
            hit = sub[sub["name"] == want] if "name" in sub else sub.iloc[0:0]
            if hit.empty and "name" in sub:
                hit = sub[sub["name"].astype(str).str.fullmatch(f"{want}( 지수)?")]
            if not hit.empty:
                r = hit.iloc[0]
                out["index"].append({"name": want, "close": r.get("close"), "change_pct": r.get("change_pct")})
    ov = overseas_latest(store)
    out["overseas"] = []
    if not ov.empty:
        for sym, label in SYMBOLS.items():
            s = ov[ov["symbol"] == sym].sort_values("date")
            if len(s) >= 2:
                a, b = s.iloc[-2], s.iloc[-1]
                out["overseas"].append({"symbol": sym, "label": label, "date": b["date"], "close": float(b["close"]),
                                        "change_pct": (float(b["close"]) / float(a["close"]) - 1) * 100})
    return out
