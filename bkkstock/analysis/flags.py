"""회피 엔진: 사면 안 되는 이유를 찾는다. 알파(초과수익) 검증 없이도 쓸 수 있는 층.

level: block = 매수 금지, warn = 주의, info = 참고
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

import pandas as pd

from ..master import MARKET_WARNING
from ..timeutil import is_trading_day, trading_days_back

HORSE_RET = 0.15          # 하루 +15% 이상
HORSE_RET_VOL = 0.08      # 또는 +8% 이상이면서
HORSE_VOL_X = 5.0         # 거래대금이 20일 평균의 5배 이상
LIMIT_UP = 0.295          # 상한가 근처
HORSE_LOOKBACK = 20       # 최근 20거래일 안의 급등
HORSE_BLOCK_DAYS = 20     # 재료 없는 급등 → 20거래일 매수 금지
HORSE_RECHECK_DAYS = 5    # 재료 있는 급등 → 5거래일 뒤 재평가


@dataclass
class Flag:
    level: str
    kind: str
    text: str
    since: str = ""

    def dict(self) -> dict:
        return asdict(self)


def _yes(v) -> bool:
    return str(v or "").strip().upper() == "Y"


def universe_flags(row: dict | pd.Series | None, today: dt.date) -> list[Flag]:
    if row is None:
        return []
    r = dict(row)
    out: list[Flag] = []
    if _yes(r.get("halted")):
        out.append(Flag("block", "거래정지", "거래정지 종목"))
    if _yes(r.get("liquidation")):
        out.append(Flag("block", "정리매매", "정리매매 중 (상장폐지 예정)"))
    if _yes(r.get("administrative")):
        out.append(Flag("block", "관리종목", "관리종목 지정"))
    if _yes(r.get("caution_alert")):
        out.append(Flag("block", "투자주의환기", "투자주의환기종목"))
    if _yes(r.get("unfaithful_disclosure")):
        out.append(Flag("block", "불성실공시", "불성실공시법인 지정"))
    mw = str(r.get("market_warning") or "00").zfill(2)
    if mw in ("02", "03"):
        out.append(Flag("block", MARKET_WARNING[mw], f"시장경보: {MARKET_WARNING[mw]}"))
    elif mw == "01":
        out.append(Flag("warn", "투자주의", "시장경보: 투자주의"))
    if _yes(r.get("warning_notice")):
        out.append(Flag("warn", "경고예고", "투자경고 지정 예고"))
    sto = str(r.get("short_term_overheat") or "").strip()
    if sto and sto not in ("0", "00", "N"):
        out.append(Flag("warn", "단기과열", "단기과열 종목"))
    if _yes(r.get("abnormal_surge")):
        out.append(Flag("warn", "이상급등", "이상급등 종목"))
    if _yes(r.get("short_overheat")):
        out.append(Flag("warn", "공매도과열", "공매도 과열 종목"))
    if _yes(r.get("spac")):
        out.append(Flag("info", "스팩", "기업인수목적회사(스팩)"))
    ld = str(r.get("listed_date") or "").strip()
    if len(ld) == 8 and ld.isdigit():
        try:
            listed = dt.date(int(ld[:4]), int(ld[4:6]), int(ld[6:]))
            recent = trading_days_back(today, 60)
            if listed >= recent[0]:
                out.append(Flag("warn", "신규상장", f"상장 60거래일 미만 ({listed:%Y-%m-%d} 상장)"))
        except ValueError:
            pass
    return out


def disclosure_flags(disclosures: list[dict], today: dt.date) -> list[Flag]:
    out: list[Flag] = []
    seen: set[str] = set()
    windows = {"희석": (60, "warn"), "부실": (120, "block"), "매도압력": (30, "warn"), "소송": (60, "warn"),
               "계약해지": (30, "warn"), "영업위험": (30, "warn"), "상폐우려": (60, "warn")}
    for d in sorted(disclosures, key=lambda x: x.get("rcept_dt") or "", reverse=True):
        tags = set(filter(None, (d.get("tags") or "").split(",")))
        rd = d.get("rcept_dt") or ""
        try:
            day = dt.date(int(rd[:4]), int(rd[4:6]), int(rd[6:8]))
        except (ValueError, IndexError):
            continue
        for tag, (days, level) in windows.items():
            if tag in tags and (today - day).days <= days and tag not in seen:
                seen.add(tag)
                out.append(Flag(level, tag, f"{tag} 공시: {d.get('report_nm')}", since=day.isoformat()))
    return out


# 급등의 '재료'로 인정하는 공시. 지분 보고·조회공시 요구·시장조치·IR·정정은 재료가 아니다
# (조회공시 요구는 거래소가 '설명 안 되는 급등'이라 묻는 것 — 차단을 풀어 주면 안 된다).
MATERIAL_TAGS = {"실적", "수주", "주주환원"}
MATERIAL_WORDS = ("잠정실적", "영업(잠정)실적", "매출액또는손익구조", "공급계약체결", "단일판매ㆍ공급계약체결", "임상",
                  "품목허가", "허가", "기술이전", "기술도입", "라이선스", "합병", "분할결정", "영업양수", "영업양도",
                  "자기주식취득", "주식소각", "무상증자결정", "현금ㆍ현물배당", "특허", "투자판단관련주요경영사항",
                  "신규시설투자", "타법인주식및출자증권취득", "공개매수", "최대주주변경을수반하는")
NOT_MATERIAL_WORDS = ("조회공시", "대량보유", "소유상황", "특정증권등", "기업설명회", "IR", "투자주의", "투자경고",
                      "투자위험", "단기과열", "매매거래정지", "풍문", "해명", "미확정")


def is_material(d: dict) -> bool:
    name = (d.get("report_nm") or "").replace(" ", "")
    tags = set(filter(None, (d.get("tags") or "").split(",")))
    if "정정" in tags or any(w in name for w in NOT_MATERIAL_WORDS):
        return False
    return bool(tags & MATERIAL_TAGS) or any(w in name for w in MATERIAL_WORDS)


def horse_events(hist: pd.DataFrame, market: dict | None = None) -> pd.DataFrame:
    """한 종목의 일별 시세(오래된 순)에서 경주마형 급등일을 찾는다.
    market: {날짜: 그날 같은 시장 종목들의 중앙 등락률}. 시장 전체가 오른 날(예: 지수 +5%)에 같이 오른 것은
    '재료 없는 급등'이 아니므로 시장 대비 초과 상승률로 판정한다."""
    if hist is None or len(hist) < 2:
        return pd.DataFrame(columns=["date", "ret", "value_x", "mkt"])
    h = hist.sort_values("date").copy()
    h["ret"] = h["close"] / h["close"].shift(1) - 1
    h["mkt"] = h["date"].map(market).fillna(0.0).astype(float) if market else 0.0
    ex = h["ret"] - h["mkt"]
    base = h["total_value"] if "total_value" in h.columns else h["value"]
    h["avg20"] = base.shift(1).rolling(20, min_periods=5).mean()
    h["value_x"] = base / h["avg20"]
    cond = (ex >= HORSE_RET) | ((h["ret"] >= LIMIT_UP) & (ex >= HORSE_RET_VOL)) | ((ex >= HORSE_RET_VOL) & (h["value_x"] >= HORSE_VOL_X))
    return h.loc[cond, ["date", "ret", "value_x", "mkt"]].reset_index(drop=True)


def market_returns(prices: pd.DataFrame, index: pd.DataFrame | None = None) -> dict[str, dict]:
    """시장(코스피·코스닥)별 날짜별 '시장 수익률'. {시장: {날짜: 수익률}}
    = max(같은 시장 종목들의 중앙 등락률, 그 시장 지수 등락률). 대형주가 지수를 끌어올린 날(중앙값은 작음)에
    대형주가 '재료 없는 급등'으로 잡히지 않게 둘 중 큰 쪽을 뺀다."""
    if prices is None or prices.empty or "close" not in prices:
        return {}
    p = prices.sort_values(["code", "date"])
    r = p.groupby("code")["close"].pct_change(fill_method=None)
    mk = p["market"].astype(str).str.upper() if "market" in p.columns else pd.Series("ALL", index=p.index)
    df = pd.DataFrame({"date": p["date"], "mk": mk.where(mk.isin(["KOSPI", "KOSDAQ"]), "ALL"), "r": r}).dropna()
    out: dict[str, dict] = {}
    for m, g in df.groupby("mk"):
        out[m] = g.groupby("date")["r"].median().to_dict()
    if index is not None and not index.empty and {"date", "name", "change_pct"} <= set(index.columns):
        names = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
        ix = index.assign(r=pd.to_numeric(index["change_pct"].astype(str).str.replace(",", ""), errors="coerce") / 100)
        for m, nm in names.items():
            sub = ix[ix["name"].astype(str) == nm].dropna(subset=["r"])
            if sub.empty:
                continue
            cur = out.setdefault(m, {})
            for d, r in zip(sub["date"], sub["r"]):
                cur[d] = max(cur.get(d, r), r)
    return out


# 상장폐지 요건(시가총액) — 2026-07부터 코스피 300억·코스닥 200억, 2027-01부터 500억·300억.
# 30거래일 연속 밑돌면 관리종목. 금액은 규정 원문으로 다시 확인할 것(뉴스레터마다 다르게 적힘) → 경고는 '주의'까지만.
MKTCAP_RULES = [(dt.date(2027, 1, 1), {"KOSPI": 500e8, "KOSDAQ": 300e8}),
                (dt.date(2026, 7, 1), {"KOSPI": 300e8, "KOSDAQ": 200e8})]


def delist_flags(market: str, mktcap: float | None, close: float | None, today: dt.date) -> list[Flag]:
    out: list[Flag] = []
    m = (market or "").upper()
    rule = next((r for start, r in MKTCAP_RULES if today >= start), None)
    nxt = MKTCAP_RULES[0][1] if today < MKTCAP_RULES[0][0] else None
    if rule and m in rule and mktcap and mktcap > 0:
        lim = rule[m]
        if mktcap < lim:
            out.append(Flag("warn", "시총미달", f"시가총액 {mktcap / 1e8:,.0f}억 < 상장 유지 기준 {lim / 1e8:,.0f}억 "
                                               "(30거래일 연속이면 관리종목)"))
        elif mktcap < lim * 1.5:
            out.append(Flag("info", "시총근접", f"시가총액 {mktcap / 1e8:,.0f}억 — 기준 {lim / 1e8:,.0f}억의 1.5배 미만"))
        if nxt and m in nxt and mktcap < nxt[m] and not out:
            out.append(Flag("info", "시총근접", f"2027-01 기준 {nxt[m] / 1e8:,.0f}억에 못 미침 (지금 {mktcap / 1e8:,.0f}억)"))
    if close is not None and 0 < close < 1000:
        out.append(Flag("info", "동전주", f"주가 {close:,.0f}원 — 1,000원 미만 (동전주 상장폐지 요건 확인)"))
    return out


def horse_flags(hist: pd.DataFrame, disclosures: list[dict], today: dt.date, market: dict | None = None) -> list[Flag]:
    ev = horse_events(hist, market)
    if ev.empty:
        return []
    window = trading_days_back(today, HORSE_LOOKBACK)
    ev = ev[ev["date"] >= window[0]]
    if ev.empty:
        return []
    last = ev.iloc[-1]
    day: dt.date = last["date"]
    # 재료: 급등 전날 ~ 당일에 나온 공시 (장 마감 후 공시가 다음 날 급등의 이유일 수 있다)
    prev = trading_days_back(day, 2)[0]
    material = [d for d in disclosures if (d.get("rcept_dt") or "") in (prev.strftime("%Y%m%d"), day.strftime("%Y%m%d"))
                and is_material(d)]
    after = [d for d in _following_trading_days(day, HORSE_BLOCK_DAYS)]
    pct = f"{last['ret'] * 100:+.1f}%" + (f"(시장 {last['mkt'] * 100:+.1f}%)" if abs(last.get("mkt") or 0) >= 0.01 else "")
    vx = f", 거래대금 {last['value_x']:.1f}배" if pd.notna(last["value_x"]) else ""
    if not material:
        until = after[-1] if after else day
        if today <= until:
            return [Flag("block", "경주마", f"재료 없는 급등 {day:%m/%d} {pct}{vx} → {until:%m/%d}까지 매수 금지",
                         since=day.isoformat())]
        return []
    recheck = after[HORSE_RECHECK_DAYS - 1] if len(after) >= HORSE_RECHECK_DAYS else day
    names = ", ".join((d.get("report_nm") or "")[:30] for d in material[:2])
    if today < recheck:
        return [Flag("warn", "재료급등", f"재료 있는 급등 {day:%m/%d} {pct}{vx} · {names} → {recheck:%m/%d} 재평가",
                     since=day.isoformat())]
    return [Flag("info", "재료급등", f"재료 있는 급등 {day:%m/%d} {pct} · 재평가 기간 지남", since=day.isoformat())]


def _following_trading_days(day: dt.date, n: int) -> list[dt.date]:
    out = []
    d = day
    while len(out) < n:
        d += dt.timedelta(days=1)
        if is_trading_day(d):
            out.append(d)
    return out


def worst_level(flags: list[Flag]) -> str:
    levels = {f.level for f in flags}
    for lv in ("block", "warn", "info"):
        if lv in levels:
            return lv
    return ""
