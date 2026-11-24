"""종목 재무·배당 (OpenDART, 이미 있는 DART 키). 종목 화면을 열 때 필요하면 받아 7일간 캐시한다.

- 주요계정(fnlttSinglAcnt): 최근 사업보고서 1건 = 3개 연도 매출·영업이익·순이익·자산·부채·자본. 연결(CFS) 우선.
- 올해 최근 분기 보고서의 누적 실적(있으면).
- 배당(alotMatter): 주당 배당금·배당성향·배당수익률 3개 연도.
확정 실적만 쓴다(증권사 추정치·목표가는 보드 원칙상 넣지 않음). 은행·보험은 '매출액' 계정이 없어 일부 칸이 빈다.
"""

from __future__ import annotations

import datetime as dt
import json
import time

from ..clients.base import ApiError

TTL = 7 * 86400
CORP_MAP_TTL = 30 * 86400
QUARTERS = [("11014", "3분기"), ("11012", "반기"), ("11013", "1분기")]
KEYS = [("sales", ("매출액", "영업수익", "수익(매출액)")), ("op", ("영업이익",)), ("net", ("당기순이익",)),
        ("assets", ("자산총계",)), ("liab", ("부채총계",)), ("equity", ("자본총계",))]


def _amt(v) -> float | None:
    s = str(v or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _key_of(name: str) -> str | None:
    n = (name or "").replace(" ", "")
    for key, prefixes in KEYS:
        if any(n.startswith(p.replace(" ", "")) for p in prefixes):
            return key
    return None


def corp_code_for(ctx, code: str) -> str | None:
    rows = ctx.store.query("SELECT corp_code FROM disclosures WHERE stock_code=? AND corp_code != '' LIMIT 1", (code,))
    if rows and rows[0]["corp_code"]:
        return rows[0]["corp_code"]
    raw = ctx.store.meta_get("dart_corp_map")
    cached = json.loads(raw) if raw else {}
    if not cached or time.time() - cached.get("_t", 0) > CORP_MAP_TTL or code not in cached:
        if cached and code not in cached and time.time() - cached.get("_t", 0) < 86400:
            return None                                   # 하루 안에 받은 목록에도 없으면 다시 받지 않는다
        cached = {r["stock_code"]: r["corp_code"] for r in ctx.dart.corp_codes()}
        cached["_t"] = time.time()
        ctx.store.meta_set("dart_corp_map", json.dumps(cached))
    return cached.get(code)


def parse_accounts(rows: list[dict]) -> dict:
    """주요계정 응답 → {'labels': [전전기, 전기, 당기], 'values': {key: [a, b, c]}, 'fs': 'CFS'|'OFS', 'ytd': …}."""
    fs = "CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"
    rows = [r for r in rows if r.get("fs_div", fs) == fs]
    vals: dict[str, list] = {}
    ytd: dict[str, list] = {}
    labels = ["", "", ""]
    for r in rows:
        key = _key_of(r.get("account_nm", ""))
        if not key or key in vals:
            continue
        vals[key] = [_amt(r.get("bfefrmtrm_amount")), _amt(r.get("frmtrm_amount")), _amt(r.get("thstrm_amount"))]
        if r.get("thstrm_add_amount") not in (None, "") and r.get("frmtrm_add_amount") not in (None, ""):
            ytd[key] = [_amt(r.get("frmtrm_add_amount")), _amt(r.get("thstrm_add_amount"))]
        if not labels[2]:
            labels = [r.get("bfefrmtrm_nm", ""), r.get("frmtrm_nm", ""), r.get("thstrm_nm", "")]
    return {"labels": labels, "values": vals, "fs": fs, "ytd": ytd}


def parse_dividends(rows: list[dict]) -> dict:
    want = {"dps": "주당현금배당금", "payout": "현금배당성향", "yield": "현금배당수익률"}
    out = {}
    for r in rows:
        se = (r.get("se") or "").replace(" ", "")
        kind = (r.get("stock_knd") or "").strip()
        for key, word in want.items():
            if word in se and key not in out and (kind in ("", "보통주") or key == "payout"):
                out[key] = [_amt(r.get("lwfr")), _amt(r.get("frmtrm")), _amt(r.get("thstrm"))]
    return out


def _ratio(a, b, mul=100.0):
    return a / b * mul if a is not None and b not in (None, 0) else None


def build(acc: dict, year: int, ytd_label: str | None, div: dict) -> dict:
    v = acc["values"]
    years = [year - 2, year - 1, year]
    rows = []
    for i, y in enumerate(years):
        g = lambda k: (v.get(k) or [None] * 3)[i]  # noqa: E731
        rows.append({"year": y, "sales": g("sales"), "op": g("op"), "net": g("net"),
                     "margin": _ratio(g("op"), g("sales")), "debt": _ratio(g("liab"), g("equity")),
                     "roe": _ratio(g("net"), g("equity"))})
    s0, s2 = rows[0]["sales"], rows[2]["sales"]
    cagr = ((s2 / s0) ** 0.5 - 1) * 100 if s0 and s2 and s0 > 0 and s2 > 0 else None
    last = rows[-1]
    bits = []
    if cagr is not None:
        bits.append(f"매출 2년 {cagr:+.0f}%/년")
    if last["margin"] is not None:
        bits.append(f"영업이익률 {last['margin']:.0f}%")
    if last["debt"] is not None:
        bits.append(f"부채비율 {last['debt']:.0f}%")
    if last["roe"] is not None:
        bits.append(f"ROE {last['roe']:.0f}%")
    warns = []
    if last["debt"] is not None and last["debt"] > 200:
        warns.append(f"부채비율 {last['debt']:.0f}% — 200% 넘음")
    if last["net"] is not None and last["net"] < 0:
        warns.append(f"{year}년 순손실")
    if all(r["op"] is not None and r["op"] < 0 for r in rows):
        warns.append("3년 연속 영업손실")
    ytd = None
    if ytd_label and acc.get("ytd"):
        y = acc["ytd"]
        ytd = {"label": ytd_label, "sales": y.get("sales"), "op": y.get("op"), "net": y.get("net")}
    dv = []
    if div:
        for i, yy in enumerate(years):
            g = lambda k: (div.get(k) or [None] * 3)[i]  # noqa: E731
            dv.append({"year": yy, "dps": g("dps"), "payout": g("payout"), "yield": g("yield")})
    return {"years": rows, "summary": " · ".join(bits), "warns": warns, "ytd": ytd, "div": dv,
            "fs": "연결" if acc["fs"] == "CFS" else "별도", "financial": v.get("sales") is None}


def fetch(ctx, code: str, today: dt.date) -> dict:
    corp = corp_code_for(ctx, code)
    if not corp:
        return {"error": "DART 회사 고유번호를 찾지 못했습니다 (상장 직후이거나 ETF·리츠일 수 있음)."}
    acc, year = None, None
    for y in (today.year - 1, today.year - 2):
        rows = ctx.dart.major_accounts(corp, y, "11011")
        if rows:
            acc, year = parse_accounts(rows), y
            break
    if not acc:
        return {"error": "최근 사업보고서 재무가 없습니다."}
    ytd_label = None
    for reprt, label in QUARTERS:
        q = ctx.dart.major_accounts(corp, year + 1, reprt)
        if q:
            qa = parse_accounts(q)
            if qa["ytd"]:
                acc["ytd"], ytd_label = qa["ytd"], f"{year + 1}년 {label} 누적"
            break
    div = parse_dividends(ctx.dart.dividends(corp, year, "11011"))
    return build(acc, year, ytd_label, div)


def demo(code: str, today: dt.date) -> dict:
    k = sum(ord(c) for c in code) % 7
    y = today.year - 1
    base = 1e12 * (1 + k / 5)
    acc = {"labels": ["", "", ""], "fs": "CFS", "ytd": {"sales": [base * .7, base * .78], "op": [base * .05, base * .06],
                                                         "net": [base * .03, base * .04]},
           "values": {"sales": [base * .85, base * .93, base], "op": [base * .05, base * .06, base * .07 - k * 1e10],
                      "net": [base * .03, base * .04, base * .05 - k * 1e10], "assets": [base * 2] * 3,
                      "liab": [base * .8, base * .85, base * (0.9 + k * .4)], "equity": [base * 1.1] * 3}}
    div = {"dps": [500 + k * 50, 550 + k * 50, 600 + k * 50], "payout": [20.0, 22.0, 25.0], "yield": [1.5, 1.7, 2.0]}
    return dict(build(acc, y, f"{y + 1}년 3분기 누적", div), demo=True)


def get(ctx, code: str, today: dt.date, force: bool = False) -> dict:
    """캐시(7일) 먼저. 받기에 실패하면 지난 캐시와 오류 문구를 같이."""
    if ctx.settings.demo:
        return demo(code, today)
    if not ctx.settings.get("DART_API_KEY"):
        return {"error": "OpenDART 키가 있어야 재무를 받을 수 있습니다."}
    key = f"fin:{code}"
    raw = ctx.store.meta_get(key)
    cached = json.loads(raw) if raw else None
    if cached and not force and time.time() - cached.get("_t", 0) < TTL:
        return cached
    try:
        out = fetch(ctx, code, today)
    except ApiError as e:
        if cached:
            return dict(cached, stale_error=str(e))
        return {"error": str(e)}
    out["_t"] = time.time()
    out["fetched"] = dt.datetime.now().strftime("%Y-%m-%d")
    if "error" not in out:
        ctx.store.meta_set(key, json.dumps(out, ensure_ascii=False))
    return out
