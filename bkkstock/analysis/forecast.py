"""내일 예측가 — 예측이 아니라 '근거를 밝힌 참고값'.

kospilab 의 '해외 참고가'는 한국 종목 영구선물 가격 × 환율이고, 그 사이트도 다음 날 시초가 예측이 아니라고 적는다.
여기서는 우리가 가진 자료로 같은 질문("밤사이 바깥에서 무슨 일이 있었나")에 답하고, 그 답이 과거에 얼마나 맞았는지를 같이 보여 준다.

  예상 시초가 = 기준 종가 × (1 + β·EWY 밤사이 수익률 + γ·넥스트레이드 시간외 수익률)
     β: 종목별 'EWY(미국 상장 한국 ETF)가 1% 움직일 때 다음 날 시초가 갭' (전 종목 평균 쪽으로 당긴 값)
     γ: 전 종목 공통 '넥스트레이드 15:30~20:00 움직임이 다음 날 시초가에 남는 정도'
  보통 범위 = 예상 시초가 ± 최근 20일 일간 변동성 (68%), 넓은 범위 = ± 2배 (95%)
  성적표 = 최근 날짜마다 그날 이전 자료로만 다시 계산해서(미래 정보 없이) 실제와 비교.
           '어제 종가 그대로'보다 오차가 작은지, 방향을 얼마나 맞혔는지, 범위 안에 얼마나 들었는지.

모델을 바꾸면 MODEL_VERSION 을 올린다. 매일 아침 예측을 저장해서 실제 적중률을 쌓는다.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..store import Store
from ..timeutil import is_trading_day, now_kst
from .history import overseas_latest, price_history

MODEL_VERSION = "fc-v1-2026-11"
SHRINK = 30          # 종목 β를 전 종목 평균 쪽으로 당기는 강도 (표본 30일이면 반반)
CLIP = 0.10          # 예상 갭은 ±10% 안으로
BACKTEST_DAYS = 40


def next_trading_day(d: dt.date) -> dt.date:
    d = d + dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def _us_complete(d: dt.date, now_utc: dt.datetime) -> bool:
    """미국 정규장(현지 날짜 d)이 끝났는지. 동부 16:00 = UTC 20:00~21:00."""
    return now_utc >= dt.datetime(d.year, d.month, d.day, 21, 0, tzinfo=dt.timezone.utc)


def overnight(ov: pd.DataFrame, symbol: str, prev_kr: dt.date, target: dt.date,
              now_utc: dt.datetime | None = None) -> float | None:
    """한국 prev_kr 장 마감 뒤 ~ target 장 시작 전 사이 끝난 해외 세션들의 누적 수익률."""
    if ov is None or ov.empty:
        return None
    s = ov[ov["symbol"] == symbol].sort_values("date")
    if s.empty:
        return None
    s = s.assign(ret=s["close"].pct_change())
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    win_all = s[(s["date"] >= prev_kr) & (s["date"] < target)]
    win = win_all[[_us_complete(d, now_utc) or symbol == "KRW=X" for d in win_all["date"]]]
    if win.empty:
        if not win_all.empty:
            return None          # 미국장이 아직 안 끝났다 (아침 7:30에 다시 계산)
        return 0.0 if s["date"].max() >= prev_kr else None     # 그 사이 미국 휴장
    return float(np.prod(1 + win["ret"].fillna(0)) - 1)


@dataclass
class Context:
    day: dt.date            # 가장 최근 종가가 있는 날
    prices: pd.DataFrame    # code, date, open, close, nxt_close …
    ov: pd.DataFrame
    feats: pd.DataFrame     # code, date(t), gap, ewy, nxt_after, ret1
    has_overseas: bool


def build_context(store: Store, day: dt.date | None = None, n: int = 120) -> Context | None:
    day = day or store.latest_clean_day("prices_krx")
    if not day:
        return None
    p = price_history(store, day, n)
    if p.empty:
        return None
    p = p.sort_values(["code", "date"]).copy()
    for c in ("open", "close", "nxt_close"):
        if c in p.columns:
            p[c] = pd.to_numeric(p[c], errors="coerce")
    p["prev_close"] = p.groupby("code")["close"].shift(1)
    p["prev_date"] = p.groupby("code")["date"].shift(1)
    p["prev_nxt_close"] = p.groupby("code")["nxt_close"].shift(1) if "nxt_close" in p else np.nan
    p["gap"] = p["open"] / p["prev_close"] - 1
    p["ret1"] = p["close"] / p["prev_close"] - 1
    p["nxt_after"] = (p["prev_nxt_close"] / p["prev_close"] - 1).where(p["prev_nxt_close"] > 0, 0.0).fillna(0.0)
    ov = overseas_latest(store)
    has_ov = not ov.empty and (ov["symbol"] == "EWY").any()
    if has_ov:
        e = ov[ov["symbol"] == "EWY"].sort_values("date")
        e_dates = list(e["date"])
        e_ret = (e["close"].pct_change()).fillna(0).to_numpy(float)
        sub = p.dropna(subset=["prev_date"])
        pairs = set(zip(sub["prev_date"], sub["date"]))           # 같은 행끼리 짝 (dropna 뒤 zip 어긋남 방지)
        ewy_by_pair = {}
        for prev_d, t in pairs:
            idx = [i for i, d in enumerate(e_dates) if prev_d <= d < t]
            ewy_by_pair[(prev_d, t)] = float(np.prod(1 + e_ret[idx]) - 1) if idx else 0.0
        p["ewy"] = [ewy_by_pair.get((a, b), np.nan) if a == a and a is not None else np.nan
                    for a, b in zip(p["prev_date"], p["date"])]
    else:
        p["ewy"] = np.nan
    feats = p.dropna(subset=["gap"]).copy()
    feats = feats[np.isfinite(feats["gap"]) & (feats["gap"].abs() < 0.35)]
    return Context(day=day, prices=p, ov=ov, feats=feats, has_overseas=bool(has_ov))


def _pooled(feats: pd.DataFrame, before: dt.date | None = None) -> tuple[float, float]:
    f = feats if before is None else feats[feats["date"] < before]
    f = f.dropna(subset=["ewy"]) if "ewy" in f else f.iloc[0:0]
    if len(f) < 50:
        return 1.0, 0.5
    X = np.column_stack([f["ewy"].to_numpy(float), f["nxt_after"].to_numpy(float)])
    y = f["gap"].to_numpy(float)
    xtx = X.T @ X + np.eye(2) * 1e-6
    b = np.linalg.solve(xtx, X.T @ y)
    return float(b[0]), float(b[1])


def _pooled_by_date(feats: pd.DataFrame) -> dict:
    """날짜 t마다 't 이전 자료만으로' 구한 공통 계수 (β, γ). 누적 합으로 한 번에 계산한다."""
    f = feats.dropna(subset=["ewy"]) if "ewy" in feats else feats.iloc[0:0]
    if f.empty:
        return {}
    x1 = f["ewy"].to_numpy(float)
    x2 = f["nxt_after"].to_numpy(float)
    y = f["gap"].to_numpy(float)
    g = pd.DataFrame({"date": f["date"].to_numpy(), "a": x1 * x1, "b": x1 * x2, "c": x2 * x2,
                      "d": x1 * y, "e": x2 * y, "n": 1}).groupby("date").sum().sort_index().cumsum()
    out = {}
    prev = None
    for t in sorted(set(feats["date"])):
        rows = g[g.index < t]
        if rows.empty or rows["n"].iloc[-1] < 50:
            out[t] = (1.0, 0.5)
            continue
        r = rows.iloc[-1]
        xtx = np.array([[r["a"], r["b"]], [r["b"], r["c"]]]) + np.eye(2) * 1e-6
        bb = np.linalg.solve(xtx, np.array([r["d"], r["e"]]))
        out[t] = (float(bb[0]), float(bb[1]))
    return out


def _stock_beta(f: pd.DataFrame, gamma: float, beta_pool: float) -> tuple[float, int]:
    f = f.dropna(subset=["ewy"])
    n = len(f)
    if n < 5:
        return beta_pool, n
    x = f["ewy"].to_numpy(float)
    y = f["gap"].to_numpy(float) - gamma * f["nxt_after"].to_numpy(float)
    sxx = float(x @ x)
    b = float(x @ y) / sxx if sxx > 1e-12 else beta_pool
    w = n / (n + SHRINK)
    return w * b + (1 - w) * beta_pool, n


def _vol20(rets: pd.Series) -> float | None:
    r = rets.dropna().tail(20)
    if len(r) < 10:
        return None
    return float(r.std(ddof=1))


def backtest(ctx: Context, code: str, days: int = BACKTEST_DAYS) -> dict:
    b = backtest_rows(ctx, code, days)
    if b.empty:
        return {"n": 0}
    nz = b[(b["pred"].abs() >= 0.001) & (b["actual_gap"] != 0)]
    hit = float((np.sign(nz["pred"]) == np.sign(nz["actual_gap"])).mean() * 100) if len(nz) else None
    mae_model = float((b["actual_gap"] - b["pred"]).abs().mean() * 100)
    mae_base = float(b["actual_gap"].abs().mean() * 100)
    cov = b.dropna(subset=["sig"])
    in1 = ((cov["ret1"] - cov["pred"]).abs() <= cov["sig"]).mean() * 100 if len(cov) else None
    return {"n": len(b), "hit": hit, "hit_n": len(nz), "mae_model": mae_model, "mae_base": mae_base,
            "improve": (1 - mae_model / mae_base) * 100 if mae_base > 0 else None,
            "range_cover": float(in1) if in1 is not None else None}


def backtest_rows(ctx: Context, code: str, days: int = BACKTEST_DAYS) -> pd.DataFrame:
    """날짜별 (그날 이전 자료로만 만든) 예측과 실제."""
    f_all = ctx.feats
    f = f_all[f_all["code"] == code].sort_values("date")
    rows = []
    pooled = _pooled_by_date(f_all)
    for t in list(f["date"])[-days:]:
        hist = f[f["date"] < t]
        if len(hist) < 15:
            continue
        beta_pool, gamma = pooled.get(t, (1.0, 0.5))
        beta, _ = _stock_beta(hist.tail(80), gamma, beta_pool)
        cur = f[f["date"] == t].iloc[0]
        ewy = 0.0 if (cur["ewy"] is None or (isinstance(cur["ewy"], float) and math.isnan(cur["ewy"]))) else float(cur["ewy"])
        pred = float(np.clip(beta * ewy + gamma * float(cur["nxt_after"]), -CLIP, CLIP))
        sig = _vol20(hist["ret1"])
        rows.append({"date": t, "pred": pred, "actual_gap": float(cur["gap"]), "ret1": float(cur["ret1"]), "sig": sig})
    return pd.DataFrame(rows)


def forecast(ctx: Context, code: str, now_utc: dt.datetime | None = None, with_backtest: bool = True) -> dict | None:
    p = ctx.prices[ctx.prices["code"] == code].sort_values("date")
    if p.empty or pd.isna(p["close"].iloc[-1]):
        return None
    last = p.iloc[-1]
    base_day: dt.date = last["date"]
    target = next_trading_day(base_day)
    close = float(last["close"])
    beta_pool, gamma = _pooled(ctx.feats)
    beta, n_beta = _stock_beta(ctx.feats[ctx.feats["code"] == code].tail(80), gamma, beta_pool)
    ewy = overnight(ctx.ov, "EWY", base_day, target, now_utc) if ctx.has_overseas else None
    sox = overnight(ctx.ov, "^SOX", base_day, target, now_utc) if ctx.has_overseas else None
    ndx = overnight(ctx.ov, "^IXIC", base_day, target, now_utc) if ctx.has_overseas else None
    fx = overnight(ctx.ov, "KRW=X", base_day, target, now_utc) if ctx.has_overseas else None
    nxt_after = 0.0
    if pd.notna(last.get("nxt_close")) and float(last.get("nxt_close") or 0) > 0:
        nxt_after = float(last["nxt_close"]) / close - 1
    pred = float(np.clip(beta * (ewy or 0.0) + gamma * nxt_after, -CLIP, CLIP))
    sig = _vol20(p["ret1"]) or 0.0
    center = close * (1 + pred)
    out = {
        "code": code, "base_day": base_day, "target": target, "close": close, "pred_gap": pred,
        "center": center, "lo1": center * (1 - sig), "hi1": center * (1 + sig),
        "lo2": center * (1 - 2 * sig), "hi2": center * (1 + 2 * sig), "sigma": sig,
        "beta": beta, "beta_n": n_beta, "beta_pool": beta_pool, "gamma": gamma,
        "ewy": ewy, "sox": sox, "nasdaq": ndx, "usdkrw": fx, "nxt_after": nxt_after,
        "us_ready": ewy is not None and ctx.has_overseas, "us_pending": ctx.has_overseas and ewy is None,
        "model": MODEL_VERSION,
    }
    if with_backtest:
        out["bt"] = backtest(ctx, code)
    return out


def forecast_many(store: Store, codes: list[str], with_backtest: bool = False) -> pd.DataFrame:
    ctx = build_context(store)
    if ctx is None:
        return pd.DataFrame()
    rows = []
    for c in codes:
        fc = forecast(ctx, c, with_backtest=with_backtest)
        if fc:
            bt = fc.pop("bt", None) or {}
            fc.update({f"bt_{k}": v for k, v in bt.items()})
            rows.append(fc)
    return pd.DataFrame(rows)


def live_record(store: Store, max_days: int = 60) -> dict:
    """저장해 둔 아침 예측과 실제 결과 비교 (모델 버전별)."""
    days = store.clean_days("forecast")[-max_days:]
    rows = []
    for d in days:
        fc = store.read_clean("forecast", d)
        px = store.read_clean("prices_krx", d)
        if fc is None or px is None or fc.empty or px.empty:
            continue
        px = px.assign(code=px["code"].astype(str))
        m = fc.merge(px[["code", "open", "close"]], on="code", how="inner", suffixes=("", "_act"))
        for _, r in m.iterrows():
            try:
                o, c, base = float(str(r["open"]).replace(",", "")), float(str(r["close_act"]).replace(",", "")), float(r["close"])
            except (TypeError, ValueError):
                continue
            if base <= 0:
                continue
            rows.append({"day": d, "pred": float(r["pred_gap"]), "gap": o / base - 1, "in1": float(r["lo1"]) <= c <= float(r["hi1"])})
    if not rows:
        return {"n": 0, "days": len(days)}
    b = pd.DataFrame(rows)
    nz = b[(b["pred"].abs() >= 0.001) & (b["gap"] != 0)]
    return {"n": len(b), "days": b["day"].nunique(),
            "hit": float((np.sign(nz["pred"]) == np.sign(nz["gap"])).mean() * 100) if len(nz) else None,
            "mae_model": float((b["gap"] - b["pred"]).abs().mean() * 100), "mae_base": float(b["gap"].abs().mean() * 100),
            "range_cover": float(b["in1"].mean() * 100)}
