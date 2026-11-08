"""참고 점수: 앞으로 1~3개월 동안 시장 대비 나을 쪽인지 못할 쪽인지에 대한 '기울기'.

이것은 내일 오를 확률이 아니다. 토론에서 근거가 있다고 합의한 신호만 쓴다.
  - 한국 개별주는 단기 반전이 우세하다: 최근 20일 많이 오른 종목은 불리
  - 복권형 종목(MAX 효과): 최근 하루 최대 상승률이 큰 종목은 불리
  - 저변동성 효과: 변동성이 큰 종목은 불리
  - 외국인·기관 순매수: 약한 양(+)의 신호
  - 회피 엔진 경고: 강한 음(-)
가중치는 아래에 고정해 두고(사전 등록), 대시보드에서 바꿀 수 없다.
바꾸려면 MODEL_VERSION을 올리고 그날부터 성적을 따로 센다.
점수는 매일 저장되어 나중에 실제 성과와 비교(검증)된다.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from ..store import Store
from .flags import disclosure_flags, horse_flags, universe_flags, worst_level
from .history import investor_history, latest_universe, price_history

MODEL_VERSION = "v1-2026-11"
WEIGHTS = {"reversal": 15.0, "lottery": 15.0, "lowvol": 10.0, "flow": 10.0}
FLAG_PENALTY = {"block": -40.0, "warn": -8.0}
WARN_CAP = -24.0
MIN_DAYS = 21

GRADES = [  # (하한, 이름, 화살표)
    (20, "상대적 양호", "↑"),
    (5, "약간 양호", "↗"),
    (-5, "중립", "→"),
    (-20, "약간 약세", "↘"),
    (-1000, "주의", "↓"),
]


def grade(score: float, blocked: bool) -> tuple[str, str]:
    if blocked:
        return "매수 금지", "⛔"
    for lo, name, arrow in GRADES:
        if score >= lo:
            return name, arrow
    return "주의", "↓"


def _centered_rank(s: pd.Series) -> pd.Series:
    """0~1 백분위를 -1~+1로."""
    return (s.rank(pct=True) - 0.5) * 2


def factor_frame(prices: pd.DataFrame, investors: pd.DataFrame | None = None) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()
    p = prices.sort_values(["code", "date"]).copy()
    p["ret1"] = p.groupby("code")["close"].pct_change(fill_method=None)
    g = p.groupby("code")
    last = g.tail(1).set_index("code")
    n = g.size()
    close_20 = g["close"].apply(lambda s: s.iloc[-21] if len(s) >= 21 else np.nan)
    tail20 = p.groupby("code").tail(20)
    f = pd.DataFrame(index=last.index)
    f["name"] = last.get("name")
    f["date"] = last["date"]
    f["close"] = last["close"]
    f["mktcap"] = last.get("mktcap")
    f["days"] = n
    f["ret20"] = last["close"] / close_20 - 1
    f["max20"] = tail20.groupby("code")["ret1"].max()
    f["vol20"] = tail20.groupby("code")["ret1"].std()
    f["value20"] = tail20.groupby("code")["total_value"].mean() if "total_value" in tail20 else np.nan
    if investors is not None and not investors.empty:
        inv = investors.groupby("code").tail(20)
        flow = (inv["frgn_ntby_tr_pbmn"].fillna(0) + inv["orgn_ntby_tr_pbmn"].fillna(0)).groupby(inv["code"]).sum()
        f["flow20"] = flow / f["mktcap"]
    else:
        f["flow20"] = np.nan
    return f


def score_frame(f: pd.DataFrame) -> pd.DataFrame:
    ok = f["days"] >= MIN_DAYS
    s = pd.DataFrame(index=f.index)
    s["reversal"] = 0.0
    s["lottery"] = 0.0
    s["lowvol"] = 0.0
    s["flow"] = 0.0
    if ok.any():
        sub = f[ok]
        s.loc[ok, "reversal"] = -_centered_rank(sub["ret20"]) * WEIGHTS["reversal"]
        s.loc[ok, "lottery"] = -_centered_rank(sub["max20"]) * WEIGHTS["lottery"]
        s.loc[ok, "lowvol"] = -_centered_rank(sub["vol20"]) * WEIGHTS["lowvol"]
        if sub["flow20"].notna().sum() >= 30:
            fl = sub["flow20"]
            s.loc[fl.dropna().index, "flow"] = _centered_rank(fl.dropna()) * WEIGHTS["flow"]
    s = s.fillna(0.0)
    s.loc[~ok, ["reversal", "lottery", "lowvol", "flow"]] = 0.0
    s["enough_history"] = ok
    return s


def compute(store: Store, day: dt.date, codes: list[str] | None = None) -> pd.DataFrame:
    """그날 기준 점수. codes를 주면 그 종목만 결과에 남기되, 순위는 전체 종목 기준으로 매긴다."""
    prices = price_history(store, day, 60)
    if prices.empty:
        return pd.DataFrame()
    uni, _ = latest_universe(store, day)
    if not uni.empty:
        st_codes = set(uni.loc[uni["group"] == "ST", "code"])
        if st_codes:
            prices = prices[prices["code"].isin(st_codes)]
    inv = investor_history(store, day, 30)
    f = factor_frame(prices, inv)
    if f.empty:
        return f
    s = score_frame(f)
    uni_rows = {r["code"]: r for r in uni.to_dict("records")} if not uni.empty else {}
    targets = codes if codes is not None else list(f.index)
    disc = _disclosures_by_code(store, day, targets)
    hist_by_code = {c: h for c, h in prices[prices["code"].isin(targets)].groupby("code")}
    rows = []
    for code in targets:
        if code not in f.index:
            continue
        flags = universe_flags(uni_rows.get(code), day)
        dl = disc.get(code, [])
        flags += disclosure_flags(dl, day)
        flags += horse_flags(hist_by_code.get(code), dl, day)
        pen = 0.0
        if any(fl.level == "block" for fl in flags):
            pen += FLAG_PENALTY["block"]
        warns = sum(1 for fl in flags if fl.level == "warn")
        pen += max(WARN_CAP, warns * FLAG_PENALTY["warn"])
        comp = s.loc[code]
        total = float(comp["reversal"] + comp["lottery"] + comp["lowvol"] + comp["flow"] + pen)
        total = max(-100.0, min(100.0, total))
        blocked = worst_level(flags) == "block"
        g, arrow = grade(total, blocked)
        fr = f.loc[code]
        rows.append({
            "day": day.isoformat(), "code": code, "name": fr.get("name"), "score": round(total, 1),
            "grade": g, "arrow": arrow, "blocked": blocked, "enough_history": bool(comp["enough_history"]),
            "reversal": round(float(comp["reversal"]), 1), "lottery": round(float(comp["lottery"]), 1),
            "lowvol": round(float(comp["lowvol"]), 1), "flow": round(float(comp["flow"]), 1),
            "flag_penalty": round(pen, 1),
            "ret20": _r(fr.get("ret20")), "max20": _r(fr.get("max20")), "vol20": _r(fr.get("vol20")),
            "flow20": _r(fr.get("flow20"), 6), "close": _r(fr.get("close"), 0),
            "flags": json.dumps([fl.dict() for fl in flags], ensure_ascii=False),
            "model": MODEL_VERSION,
        })
    return pd.DataFrame(rows)


def _r(v, nd: int = 4):
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _disclosures_by_code(store: Store, day: dt.date, codes: list[str]) -> dict[str, list[dict]]:
    since = (day - dt.timedelta(days=130)).strftime("%Y%m%d")
    until = day.strftime("%Y%m%d")
    rows = store.query(
        "SELECT * FROM disclosures WHERE stock_code IS NOT NULL AND stock_code != '' AND rcept_dt BETWEEN ? AND ?",
        (since, until))
    want = set(codes)
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["stock_code"] in want:
            out.setdefault(r["stock_code"], []).append(r)
    return out


EXPLAIN = {
    "reversal": "최근 20일 수익률 (많이 오를수록 불리: 한국은 단기 반전 우세)",
    "lottery": "최근 20일 하루 최대 상승률 (복권형일수록 불리)",
    "lowvol": "최근 20일 변동성 (클수록 불리)",
    "flow": "외국인·기관 20일 순매수 / 시가총액 (많을수록 약간 유리)",
    "flag_penalty": "회피 엔진 경고 (매수 금지 −40, 주의 하나당 −8, 최대 −24)",
}


def verify(store: Store, horizon: int = 20, max_days: int = 250) -> dict:
    """저장된 과거 점수와 이후 실제 수익을 비교한다. 표본이 모자라면 그렇다고 돌려준다."""
    days = store.clean_days("outlook")
    if len(days) < horizon + 5:
        return {"status": "insufficient", "score_days": len(days), "need": horizon + 5, "horizon": horizon}
    end = days[-1]
    prices = price_history(store, end, min(max_days + horizon + 5, 400))
    if prices.empty:
        return {"status": "insufficient", "score_days": len(days), "need": horizon + 5, "horizon": horizon}
    close = prices.pivot_table(index="date", columns="code", values="close")
    dates = list(close.index)
    rows = []
    for d in days[-max_days:]:
        if d not in close.index:
            continue
        i = dates.index(d)
        if i + horizon >= len(dates):
            continue
        fwd = close.iloc[i + horizon] / close.iloc[i] - 1
        excess = fwd - fwd.mean()
        sc = store.read_clean("outlook", d)
        if sc is None or sc.empty:
            continue
        sc = sc[(sc["enough_history"]) & (~sc["blocked"])].set_index("code")
        joined = pd.DataFrame({"score": sc["score"], "excess": excess}).dropna()
        if len(joined) < 50:
            continue
        joined["q"] = pd.qcut(joined["score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        rows.append(joined.groupby("q", observed=True)["excess"].mean().rename(d))
    if not rows:
        return {"status": "insufficient", "score_days": len(days), "need": horizon + 5, "horizon": horizon}
    tab = pd.DataFrame(rows)
    spread = (tab[5] - tab[1])
    return {
        "status": "ok", "horizon": horizon, "n_days": len(tab),
        "quintiles": {int(k): round(float(v) * 100, 2) for k, v in tab.mean().items()},
        "spread_mean": round(float(spread.mean()) * 100, 2),
        "spread_t": round(float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))), 2) if len(spread) > 2 and spread.std(ddof=1) > 0 else None,
        "note": "겹치는 기간이라 t값은 부풀려져 있습니다. 비중첩 표본으로 다시 보세요.",
    }
