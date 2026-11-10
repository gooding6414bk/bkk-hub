"""내일 예측가 — 예측이 아니라 '근거를 밝힌 참고값'.

kospilab 의 '해외 참고가'는 한국 종목 영구선물 가격 × 환율이고, 그 사이트도 다음 날 시초가 예측이 아니라고 적는다.
여기서는 우리가 가진 자료로 같은 질문("밤사이 바깥에서 무슨 일이 있었나")에 답하고, 그 답이 과거에 얼마나 맞았는지를 같이 보여 준다.

  예상 시초가 = 기준 종가 × (1 + β·EWY 미국장 수익률 + γ·넥스트레이드 시간외 수익률)
     EWY 미국장 수익률: 한국 장이 끝난 뒤 열린 미국 세션들의 '첫 시가 → 마지막 종가'.
        (v1은 종가→종가라 한국 낮 장 움직임이 한 번 더 들어갔다 — 2026-11 내부 점검. 50종목 실측 개선 +2.3% → +7.6%)
     β: 종목별 'EWY가 1% 움직일 때 다음 날 시초가 갭' (전 종목 평균 쪽으로 당긴 값)
     γ: 전 종목 공통 '넥스트레이드 15:30~20:00 움직임이 다음 날 시초가에 남는 정도'.
        NXT 자료가 60거래일 쌓이기 전에는 0으로 둔다(검증 전).
  종가 보통 범위 = 예상 시초가 ± 최근 20일 일간 변동성 (68%), 넓은 범위 = ± 2배 (95%) — 시초가가 아니라 '종가' 범위다.
  성적표 = 최근 날짜마다 그날 이전 자료로만 다시 계산해서(미래 정보 없이) 실제와 비교.
           '어제 종가 그대로'보다 오차가 작은지(차이가 표준오차 2배 안이면 '차이 없음'), 종가가 범위 안에 얼마나 들었는지.
           방향 적중률은 '항상 상승'만 찍어도 비슷하게 나와서 보이지 않는다.

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
from .history import LEAN_COLS, overseas_latest, price_history

MODEL_VERSION = "fc-v2-2026-11"
NXT_MIN_DAYS = 60    # γ를 쓰기 시작하는 NXT 자료 일수
DEFAULT_BETA = 0.3   # 공통 계수를 낼 자료가 모자랄 때 (실측 공통 β 0.16~0.41)
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
    if symbol == "KRW=X":
        return float(np.prod(1 + win["ret"].fillna(0)) - 1)     # 환율은 24시간 거래
    return session_return(win)


def session_return(win: pd.DataFrame) -> float:
    """창 안 미국 세션들의 '첫 시가 → 마지막 종가'. 시가가 없으면 종가→종가로."""
    first_open = pd.to_numeric(win["open"], errors="coerce").iloc[0] if "open" in win.columns else np.nan
    last_close = float(win["close"].iloc[-1])
    if pd.notna(first_open) and first_open > 0:
        return float(last_close / first_open - 1)
    return float(np.prod(1 + win["ret"].fillna(0)) - 1)


@dataclass
class Context:
    day: dt.date            # 가장 최근 종가가 있는 날
    prices: pd.DataFrame    # code, date, open, close, nxt_close …
    ov: pd.DataFrame
    feats: pd.DataFrame     # code, date(t), gap, ewy, nxt_after, ret1
    has_overseas: bool
    nxt_days: int = 0


def build_context(store: Store, day: dt.date | None = None, n: int = 120) -> Context | None:
    day = day or store.latest_clean_day("prices_krx")
    if not day:
        return None
    p = price_history(store, day, n, columns=LEAN_COLS)
    if p.empty:
        return None
    # 전 종목 120일이라 메모리를 많이 쓴다(웹 상한 900MB) — 쓰는 열만 남기고 float32로
    keep = [c for c in ("code", "date", "open", "close", "nxt_close") if c in p.columns]
    p = p[keep].sort_values(["code", "date"]).reset_index(drop=True)
    for c in ("open", "close", "nxt_close"):
        p[c] = pd.to_numeric(p[c], errors="coerce").astype("float32") if c in p.columns else np.float32("nan")
    g = p.groupby("code", sort=False)
    prev_close = g["close"].shift(1)
    p["prev_date"] = g["date"].shift(1)
    prev_nxt = g["nxt_close"].shift(1)
    p["gap"] = (p["open"] / prev_close - 1).astype("float32")
    p["ret1"] = (p["close"] / prev_close - 1).astype("float32")
    p["nxt_after"] = (prev_nxt / prev_close - 1).where(prev_nxt > 0, 0.0).fillna(0.0).astype("float32")
    del prev_close, prev_nxt
    ov = overseas_latest(store)
    has_ov = not ov.empty and (ov["symbol"] == "EWY").any()
    if has_ov:
        e = ov[ov["symbol"] == "EWY"].sort_values("date")
        e = e.assign(ret=e["close"].pct_change())
        e_dates = list(e["date"])
        # 날짜 짝은 종목과 상관없이 몇백 개뿐 — 짝마다 한 번 계산해서 merge로 붙인다
        pairs = p[["prev_date", "date"]].dropna().drop_duplicates()
        vals = []
        for prev_d, t in zip(pairs["prev_date"], pairs["date"]):
            idx = [i for i, d in enumerate(e_dates) if prev_d <= d < t]
            vals.append(session_return(e.iloc[idx]) if idx else 0.0)
        pairs = pairs.assign(ewy=np.array(vals, dtype="float32"))
        p = p.merge(pairs, on=["prev_date", "date"], how="left")
    else:
        p["ewy"] = np.float32("nan")
    feats = p.loc[p["gap"].notna(), ["code", "date", "gap", "ewy", "nxt_after", "ret1"]]
    feats = feats[np.isfinite(feats["gap"]) & (feats["gap"].abs() < 0.35)].reset_index(drop=True)
    nxt_days = int(feats.loc[feats["nxt_after"] != 0, "date"].nunique()) if len(feats) else 0
    if nxt_days < NXT_MIN_DAYS:
        feats = feats.assign(nxt_after=np.float32(0.0))   # NXT 자료가 짧으면 γ를 쓰지 않는다 (검증 전)
    p = p[["code", "date", "close", "nxt_close", "ret1"]]
    return Context(day=day, prices=p, ov=ov, feats=feats, has_overseas=bool(has_ov), nxt_days=nxt_days)


def _pooled(feats: pd.DataFrame, before: dt.date | None = None) -> tuple[float, float]:
    f = feats if before is None else feats[feats["date"] < before]
    f = f.dropna(subset=["ewy"]) if "ewy" in f else f.iloc[0:0]
    if len(f) < 50:
        return DEFAULT_BETA, 0.0
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
            out[t] = (DEFAULT_BETA, 0.0)
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
    return score_errors(b["pred"], b["actual_gap"], b["ret1"], b["sig"])


def score_errors(pred: pd.Series, gap: pd.Series, ret1: pd.Series | None = None, sig: pd.Series | None = None,
                 in1: pd.Series | None = None) -> dict:
    """'어제 종가 그대로'(갭 0) 대비 오차. 차이가 표준오차 2배 안이면 '차이 없음'으로 판정한다."""
    err_m = (gap - pred).abs()
    err_b = gap.abs()
    diff = (err_b - err_m) * 100                        # 양수 = 모델이 나음 (%p)
    n = len(diff)
    se = float(diff.std(ddof=1) / math.sqrt(n)) if n > 2 else None
    mae_model, mae_base = float(err_m.mean() * 100), float(err_b.mean() * 100)
    verdict = None
    if se is not None and n >= 10:
        verdict = "better" if diff.mean() > 2 * se else "worse" if diff.mean() < -2 * se else "same"
    cover = None
    if in1 is not None and len(in1):
        cover = float(in1.mean() * 100)
    elif ret1 is not None and sig is not None:
        ok = sig.notna()
        if ok.any():
            cover = float(((ret1[ok] - pred[ok]).abs() <= sig[ok]).mean() * 100)
    return {"n": n, "mae_model": mae_model, "mae_base": mae_base,
            "improve": (1 - mae_model / mae_base) * 100 if mae_base > 0 else None,
            "verdict": verdict, "range_cover": cover}


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
        beta_pool, gamma = pooled.get(t, (DEFAULT_BETA, 0.0))
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
    if ctx.nxt_days < NXT_MIN_DAYS:
        gamma, nxt_used = 0.0, False
    else:
        nxt_used = True
    pred = float(np.clip(beta * (ewy or 0.0) + gamma * nxt_after, -CLIP, CLIP))
    sig = _vol20(p["ret1"]) or 0.0
    center = close * (1 + pred)
    out = {
        "code": code, "base_day": base_day, "target": target, "close": close, "pred_gap": pred,
        "center": tick_round(center), "lo1": tick_round(center * (1 - sig)), "hi1": tick_round(center * (1 + sig)),
        "lo2": tick_round(center * (1 - 2 * sig)), "hi2": tick_round(center * (1 + 2 * sig)), "sigma": sig,
        "beta": beta, "beta_n": n_beta, "beta_pool": beta_pool, "gamma": gamma, "nxt_used": nxt_used,
        "nxt_days": ctx.nxt_days, "enough": n_beta >= 15,
        "ewy": ewy, "sox": sox, "nasdaq": ndx, "usdkrw": fx, "nxt_after": nxt_after,
        "us_ready": ewy is not None and ctx.has_overseas, "us_pending": ctx.has_overseas and ewy is None,
        "model": MODEL_VERSION,
    }
    if with_backtest:
        out["bt"] = backtest(ctx, code)
    return out


TICKS = [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500), (float("inf"), 1000)]


def tick_round(price: float) -> float:
    """호가 단위로 반올림 (1원 단위 예상가는 실제보다 정밀해 보인다)."""
    if price is None or not math.isfinite(price):
        return price
    for lim, t in TICKS:
        if price < lim:
            return float(round(price / t) * t)
    return float(price)


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


def live_record(store: Store, max_days: int = 60, model: str = MODEL_VERSION) -> dict:
    """저장해 둔 아침 예측과 실제 결과 비교 (지금 모델 버전만 — 다른 버전과 섞지 않는다).
    여러 종목이 같은 날 같은 방향으로 움직이므로 정보량은 '종목 × 일'이 아니라 대략 '일수'다."""
    days = store.clean_days("forecast")[-max_days:]
    rows = []
    for d in days:
        fc = store.read_clean("forecast", d)
        if fc is not None and "model" in fc.columns:
            fc = fc[fc["model"] == model]
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
        return {"n": 0, "days": 0, "model": model}
    b = pd.DataFrame(rows)
    # 날짜별 평균 오차로 묶어서 판정 (같은 날 종목들은 함께 움직여 독립이 아니다)
    by_day = b.groupby("day").agg(pred=("pred", "mean"), gap=("gap", "mean"), in1=("in1", "mean"))
    out = score_errors(by_day["pred"], by_day["gap"], in1=b["in1"])
    out.update({"n": len(b), "days": len(by_day), "model": model,
                "mae_model": float((b["gap"] - b["pred"]).abs().mean() * 100),
                "mae_base": float(b["gap"].abs().mean() * 100)})
    return out
