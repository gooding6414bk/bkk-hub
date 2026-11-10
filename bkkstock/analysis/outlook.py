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
from .flags import delist_flags, disclosure_flags, horse_flags, market_returns, universe_flags, worst_level
from .history import investor_history, latest_universe, price_history

MODEL_VERSION = "v2-2026-11"   # v2: 수정주가, 경주마 시장 대비, 공시 분류 c3, 위험 경고가 있으면 등급 상한
WEIGHTS = {"reversal": 15.0, "lottery": 15.0, "lowvol": 10.0, "flow": 10.0}
FLAG_PENALTY = {"block": -40.0, "warn": -8.0}
WARN_CAP = -24.0
WARN_SCORE_CAP = 4.9      # 경고가 하나라도 있으면 점수 상한 (= '중립' 이하)
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
    idx = store.read_range("index", sorted(set(prices["date"])), columns=["date", "name", "change_pct"])
    if not idx.empty:
        idx["date"] = pd.to_datetime(idx["date"].astype(str), format="mixed").dt.date
    mret = market_returns(prices, idx)
    mk_of = {}
    if "market" in prices.columns:
        mk_of = prices.drop_duplicates("code", keep="last").set_index("code")["market"].astype(str).str.upper().to_dict()
    rows = []
    for code in targets:
        if code not in f.index:
            continue
        flags = universe_flags(uni_rows.get(code), day)
        dl = disc.get(code, [])
        flags += disclosure_flags(dl, day)
        flags += horse_flags(hist_by_code.get(code), dl, day, mret.get(mk_of.get(code, ""), mret.get("ALL")))
        fr0 = f.loc[code]
        flags += delist_flags(mk_of.get(code, ""), _num(fr0.get("mktcap")), _num(fr0.get("close")), day)
        pen = 0.0
        if any(fl.level == "block" for fl in flags):
            pen += FLAG_PENALTY["block"]
        warns = sum(1 for fl in flags if fl.level == "warn")
        pen += max(WARN_CAP, warns * FLAG_PENALTY["warn"])
        comp = s.loc[code]
        total = float(comp["reversal"] + comp["lottery"] + comp["lowvol"] + comp["flow"] + pen)
        total = max(-100.0, min(100.0, total))
        if warns:
            total = min(total, WARN_SCORE_CAP)       # 희석·부실 같은 경고가 붙으면 '양호' 등급을 못 받는다
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


def _num(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


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
    """저장된 과거 점수와 이후 실제 수익을 비교한다. 표본이 모자라면 그렇다고 돌려준다.

    - 출발점은 점수 낸 다음 날 시가(실제로 살 수 있는 가격), 끝은 그 뒤 horizon거래일 종가.
    - 시장 기준선: 같은 날 전 종목 동일가중 평균 대비 초과수익.
    - t값은 겹치지 않는 표본(horizon일 간격)으로만 내고, 12개 미만이면 내지 않는다
      (매일 겹쳐 쓰면 실력 0인 점수도 |t|>2가 70% 넘게 나온다).
    - 그날 종목 목록(universe)이 없는 날(설치 전 백필)은 시장조치 필터가 빠진 점수라 검증에서 뺀다.
    """
    days = store.clean_days("outlook")
    uni_days = set(store.clean_days("universe"))
    live = [d for d in days if d in uni_days]
    if len(live) < horizon + 5:
        return {"status": "insufficient", "score_days": len(live), "need": horizon + 5, "horizon": horizon,
                "backfill_days": len(days) - len(live)}
    end = days[-1]
    prices = price_history(store, end, min(max_days + horizon + 5, 400), columns=["date", "code", "open", "close", "change_pct"])
    if prices.empty:
        return {"status": "insufficient", "score_days": len(live), "need": horizon + 5, "horizon": horizon}
    close = prices.pivot_table(index="date", columns="code", values="close")
    opn = prices.pivot_table(index="date", columns="code", values="open")
    dates = list(close.index)
    rows = []
    for d in live[-max_days:]:
        if d not in close.index:
            continue
        i = dates.index(d)
        if i + 1 + horizon >= len(dates):
            continue
        fwd = close.iloc[i + 1 + horizon] / opn.iloc[i + 1] - 1
        excess = fwd - fwd.mean()
        sc = store.read_clean("outlook", d, columns=["code", "score", "enough_history", "blocked"])
        if sc is None or sc.empty:
            continue
        sc = sc[(sc["enough_history"]) & (~sc["blocked"])].set_index("code")
        joined = pd.DataFrame({"score": sc["score"], "excess": excess}).dropna()
        if len(joined) < 50:
            continue
        joined["q"] = pd.qcut(joined["score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        rows.append(joined.groupby("q", observed=True)["excess"].mean().rename(d))
    if not rows:
        return {"status": "insufficient", "score_days": len(live), "need": horizon + 5, "horizon": horizon}
    tab = pd.DataFrame(rows)
    spread = (tab[5] - tab[1])
    non_overlap = spread.iloc[::horizon]
    t = None
    if len(non_overlap) >= 12 and non_overlap.std(ddof=1) > 0:
        t = round(float(non_overlap.mean() / (non_overlap.std(ddof=1) / np.sqrt(len(non_overlap)))), 2)
    return {
        "status": "ok", "horizon": horizon, "n_days": len(tab), "n_independent": len(non_overlap),
        "quintiles": {int(k): round(float(v) * 100, 2) for k, v in tab.mean().items()},
        "spread_mean": round(float(spread.mean()) * 100, 2),
        "spread_t": t,
        "verdict": ("아직 판단 못 함" if t is None else "통과 후보" if t > 2 else "반대로 나옴" if t < -2 else "차이 없음"),
        "note": f"겹치지 않는 표본 {len(non_overlap)}개{'(12개 이상이어야 판단)' if t is None else ''}. "
                "시장 하락기에는 변동성이 낮은 쪽이 덜 빠져서 차이가 생길 수 있습니다.",
    }


def avoid_scorecard(store: Store, horizon: int = 20, max_days: int = 250) -> dict:
    """회피 엔진 성적표: '사지 말라'고 표시한 종목이 그 뒤 실제로 시장보다 못했나.

    표시 종류(경주마·희석·부실 …)별로, 표시가 처음 붙은 날 다음 날 시가 → horizon거래일 뒤 종가의
    시장(동일가중) 대비 초과수익. 같은 날 여러 종목에 붙은 표시는 독립이 아니어서(건수로 t를 내면 착시),
    horizon일 간격 구간별 평균으로 묶고, 구간이 12개 이상일 때만 t와 판정을 낸다.
    그날 종목 목록이 없는 날(설치 전 백필)은 뺀다."""
    days = store.clean_days("outlook")
    uni_days = set(store.clean_days("universe"))
    live = [d for d in days if d in uni_days][-max_days:]
    if not live:
        return {"status": "insufficient", "kinds": [], "horizon": horizon}
    prices = price_history(store, live[-1], min(len(live) + horizon + 10, 400), columns=["date", "code", "open", "close", "change_pct"])
    if prices.empty:
        return {"status": "insufficient", "kinds": [], "horizon": horizon}
    close = prices.pivot_table(index="date", columns="code", values="close")
    opn = prices.pivot_table(index="date", columns="code", values="open")
    dates = list(close.index)
    seen: set = set()
    ev = []
    for d in live:
        if d not in close.index:
            continue
        i = dates.index(d)
        if i + 1 + horizon >= len(dates):
            continue
        fwd = close.iloc[i + 1 + horizon] / opn.iloc[i + 1] - 1
        excess = fwd - fwd.mean()
        sc = store.read_clean("outlook", d, columns=["code", "flags"])
        if sc is None or sc.empty:
            continue
        for code, fl in zip(sc["code"], sc["flags"]):
            for f in json.loads(fl or "[]"):
                if f.get("level") not in ("block", "warn"):
                    continue
                key = (code, f["kind"], f.get("since") or d.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                x = excess.get(code)
                if x is not None and x == x:
                    ev.append({"kind": f["kind"], "level": f["level"], "block": i // horizon, "x": float(x)})
    if not ev:
        return {"status": "insufficient", "kinds": [], "horizon": horizon, "days": len(live)}
    df = pd.DataFrame(ev)
    kinds = []
    for (kind, level), g in df.groupby(["kind", "level"]):
        blocks = g.groupby("block")["x"].mean()
        t = None
        if len(blocks) >= 12 and blocks.std(ddof=1) > 0:
            t = round(float(blocks.mean() / (blocks.std(ddof=1) / np.sqrt(len(blocks)))), 2)
        verdict = ("판단 보류" if t is None else "피한 게 맞음" if t <= -2 else "오히려 나았음" if t >= 2 else "차이 없음")
        kinds.append({"kind": kind, "level": level, "n": len(g), "blocks": len(blocks),
                      "mean": round(float(g["x"].mean()) * 100, 2), "t": t, "verdict": verdict})
    kinds.sort(key=lambda k: (k["level"] != "block", -k["n"]))
    return {"status": "ok", "kinds": kinds, "horizon": horizon, "days": len(live)}
