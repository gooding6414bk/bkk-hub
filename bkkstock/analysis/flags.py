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
    windows = {"희석": (60, "warn"), "부실": (120, "block"), "매도압력": (30, "warn"), "소송": (60, "warn")}
    for d in sorted(disclosures, key=lambda x: x.get("rcept_dt") or "", reverse=True):
        tags = set(filter(None, (d.get("tags") or "").split(",")))
        rd = d.get("rcept_dt") or ""
        try:
            day = dt.date(int(rd[:4]), int(rd[4:6]), int(rd[6:8]))
        except (ValueError, IndexError):
            continue
        for tag, (days, level) in windows.items():
            if tag in tags and (today - day).days <= days and tag not in seen:
                # 감사의견 '적정' 같은 문구가 섞인 보고서는 부실로 보지 않는다
                if tag == "부실" and "적정" in (d.get("report_nm") or "") and "부적정" not in (d.get("report_nm") or ""):
                    continue
                seen.add(tag)
                out.append(Flag(level, tag, f"{tag} 공시: {d.get('report_nm')}", since=day.isoformat()))
    return out


def horse_events(hist: pd.DataFrame) -> pd.DataFrame:
    """한 종목의 일별 시세(오래된 순)에서 경주마형 급등일을 찾는다."""
    if hist is None or len(hist) < 2:
        return pd.DataFrame(columns=["date", "ret", "value_x"])
    h = hist.sort_values("date").copy()
    h["ret"] = h["close"] / h["close"].shift(1) - 1
    base = h["total_value"] if "total_value" in h.columns else h["value"]
    h["avg20"] = base.shift(1).rolling(20, min_periods=5).mean()
    h["value_x"] = base / h["avg20"]
    cond = (h["ret"] >= HORSE_RET) | (h["ret"] >= LIMIT_UP) | ((h["ret"] >= HORSE_RET_VOL) & (h["value_x"] >= HORSE_VOL_X))
    return h.loc[cond, ["date", "ret", "value_x"]].reset_index(drop=True)


def horse_flags(hist: pd.DataFrame, disclosures: list[dict], today: dt.date) -> list[Flag]:
    ev = horse_events(hist)
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
                and "정정" not in (d.get("tags") or "")]
    after = [d for d in _following_trading_days(day, HORSE_BLOCK_DAYS)]
    pct = f"{last['ret'] * 100:+.1f}%"
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
