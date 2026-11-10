"""투자자별 수급을 구체적으로: 기간별 순매수 금액, 거래대금 대비 비율, 시가총액 대비 비율, 기관 세부, 연속 순매수 일수.

한국투자증권 '종목별 투자자 매매동향(일별)' 행에는 주체별 순매수 수량(*_ntby_qty)과 금액(*_ntby_tr_pbmn)이 있다.
금액 단위(원/천 원/백만 원)는 문서마다 달라서, 수량×종가와 비교해 자동으로 맞춘다.
"""

from __future__ import annotations

import math

import pandas as pd

MAIN = [("외국인", "frgn"), ("기관", "orgn"), ("개인", "prsn")]
INST = [("금융투자", "scrt"), ("투신", "ivtr"), ("사모", "pe_fund"), ("연기금", "fund"), ("보험", "insu"),
        ("은행", "bank"), ("기타금융", "mrbn"), ("기타법인", "etc_corp")]
PERIODS = [1, 5, 20, 60]


def _amount_col(prefix: str) -> list[str]:
    # 사모·기타법인은 이름이 조금 다르다
    return [f"{prefix}_ntby_tr_pbmn", f"{prefix}_ntby_pbmn"]


def _qty_col(prefix: str) -> list[str]:
    return [f"{prefix}_ntby_qty", f"{prefix}_ntby_vol"]


def _pick(df: pd.DataFrame, names: list[str]) -> pd.Series | None:
    for n in names:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce")
    return None


def unit_factor(inv: pd.DataFrame, closes: pd.Series | None = None) -> float:
    """금액 열을 원으로 바꾸는 배수 (1, 1e3, 1e6 중). 수량×종가 ÷ 금액의 중앙값으로 정한다."""
    if inv.empty:
        return 1e6
    close = closes if closes is not None else _pick(inv, ["stck_clpr", "close"])
    ratios = []
    for _, p in MAIN:
        q, a = _pick(inv, _qty_col(p)), _pick(inv, _amount_col(p))
        if q is None or a is None or close is None:
            continue
        r = (q * close / a).replace([math.inf, -math.inf], pd.NA)
        r = r[(q.abs() > 100) & (a.abs() > 0)].dropna()
        ratios.extend(r.abs().tolist())
    if not ratios:
        return 1e6   # 한국투자증권 기본: 백만 원
    med = float(pd.Series(ratios).median())
    return min((1.0, 1e3, 1e6), key=lambda f: abs(math.log10(max(med, 1e-9)) - math.log10(f)))


def breakdown(inv: pd.DataFrame, prices: pd.DataFrame | None = None) -> dict | None:
    """한 종목의 투자자 매매 이력(오래된 순)과 시세 이력으로 수급 요약을 만든다. 금액은 원 단위."""
    if inv is None or inv.empty:
        return None
    inv = inv.sort_values("date").reset_index(drop=True)
    px = None
    if prices is not None and not prices.empty:
        px = prices.sort_values("date").set_index("date")
    closes = None
    if px is not None:
        closes = inv["date"].map(px["close"]) if "close" in px else None
    f = unit_factor(inv, closes)
    series = {}
    for name, p in MAIN + INST:
        a = _pick(inv, _amount_col(p))
        if a is not None:
            series[name] = (a.fillna(0) * f).tolist()
    if not series:
        return None
    n = len(inv)
    value = None
    mktcap = None
    if px is not None:
        tv = "total_value" if "total_value" in px.columns else "value"
        value = inv["date"].map(px[tv]).astype(float)
        mktcap = float(px["mktcap"].iloc[-1]) if "mktcap" in px.columns and pd.notna(px["mktcap"].iloc[-1]) else None
    periods = []
    for k in PERIODS:
        if k > n:
            continue
        row = {"days": k, "groups": {}}
        traded = float(value.iloc[-k:].sum()) if value is not None else None
        for name in series:
            net = float(sum(series[name][-k:]))
            row["groups"][name] = {
                "net": net,
                "pct_value": (net / traded * 100) if traded else None,      # 그 기간 거래대금 대비
                "pct_mktcap": (net / mktcap * 100) if mktcap else None,     # 시가총액 대비
            }
        row["traded"] = traded
        periods.append(row)
    streak = {}
    for name in ("외국인", "기관", "개인"):
        s = series.get(name) or []
        k, sign = 0, 0
        for v in reversed(s):
            sg = (v > 0) - (v < 0)
            if sg == 0 or (sign and sg != sign):
                break
            sign = sg
            k += 1
        streak[name] = k * sign      # +5: 5일 연속 순매수, −3: 3일 연속 순매도
    cum = {name: pd.Series(series[name]).cumsum().tolist() for name in ("외국인", "기관", "개인") if name in series}
    return {"periods": periods, "streak": streak, "cum": cum, "dates": [str(d) for d in inv["date"]],
            "days": n, "unit_factor": f, "inst_names": [nm for nm, _ in INST if nm in series]}


def short_credit(short: pd.DataFrame | None, credit: pd.DataFrame | None) -> dict:
    out: dict = {}
    if short is not None and not short.empty:
        s = short.sort_values("date")
        last = s.iloc[-1]
        out["short_ratio"] = _f(last.get("ssts_vol_rlim"))          # 공매도 거래량 비중 %
        out["short_value_ratio"] = _f(last.get("ssts_tr_pbmn_rlim"))
        out["short_series"] = [(_f(v) or 0) for v in s.get("ssts_vol_rlim", pd.Series(dtype=float))]
        out["short_date"] = str(last.get("date"))
    if credit is not None and not credit.empty:
        c = credit.sort_values("date")
        last = c.iloc[-1]
        out["credit_rate"] = _f(last.get("whol_loan_rmnd_rate"))    # 신용 잔고율 %
        out["credit_series"] = [(_f(v) or 0) for v in c.get("whol_loan_rmnd_rate", pd.Series(dtype=float))]
        out["credit_date"] = str(last.get("date"))
    return out


def _f(v):
    try:
        x = float(str(v).replace(",", ""))
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None
