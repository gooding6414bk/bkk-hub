"""📊 지표 화면용 계산: 최신값·기준일·1개월 변화·1년 중 위치·추세선, 그리고 다가오는 일정.

원칙 (2026-11 팀장 회의 + 레드팀): 오른다·내린다 예측, 매수·매도 신호, 종합 점수는 만들지 않는다.
'지금 값이 1년 중 어디쯤인지'와 '무슨 뜻인지'만. 하루 등락은 기본으로 보이지 않는다(1개월 변화).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..store import Store
from ..timeutil import is_trading_day

# 화면 묶음. (이름, 무슨 뜻 한 줄, 변화 표시: 'pp'=%p 차이, 'pct'=% 변화)
GROUPS = [
    ("💸 한국 금리", "kr_rates", [
        ("kr_base", "한국은행이 정하는 정책금리. 예금·대출 금리의 바닥.", "pp"),
        ("kr_3y", "앞으로 1~3년 금리 기대. 기준금리보다 높으면 시장은 '더 안 내린다' 쪽.", "pp"),
        ("kr_10y", "장기 금리. 오르면 성장주·리츠·채권 가격에 부담.", "pp"),
    ]),
    ("🇺🇸 미국 금리", "us_rates", [
        ("us_fed", "미국 연준 기준금리(목표 범위 상단). 한국과의 금리 차이가 원/달러와 외국인 자금에 영향.", "pp"),
        ("us_2y", "앞으로 2년 미국 금리 기대 — 연준이 금리를 어디로 가져갈지에 가장 민감.", "pp"),
        ("us_10y", "전 세계 돈의 기준값. 오르면 성장주가 약해지기 쉽고 달러로 돈이 몰림.", "pp"),
        ("us_spread", "10년 − 2년. 마이너스(역전)면 경기 둔화 경고로 자주 쓰이지만, 2022~24년 긴 역전에도 침체는 오지 않았음.", "pp"),
    ]),
    ("🇺🇸 미국 경제", "us_econ", [
        ("us_cpi_yoy", "미국 물가 (1년 전 대비). 연준 목표 2%. 높으면 금리 인하가 늦어짐.", "pp"),
        ("us_core_yoy", "식품·에너지를 뺀 물가. 연준이 더 눈여겨보는 숫자.", "pp"),
        ("us_unrate", "실업률. 빠르게 오르면 경기 둔화 신호로 읽히고, 금리 인하 기대가 커짐.", "pp"),
    ]),
    ("📈 미국 주가", "us_equity", [
        ("spx", "미국 대형주 500개. 세계 주식의 기준.", "pct"),
        ("nasdaq", "기술주 중심. 한국 반도체·IT와 같이 움직이는 경우가 많음.", "pct"),
    ]),
    ("💱 환율", "fx", [
        ("usdkrw", "오르면 원화 약세 — 수출주엔 유리, 외국인 자금엔 부담이 되는 경우가 많음.", "pct"),
        ("jpykrw", "일본과 경쟁하는 자동차·조선·철강 비교용 (100엔당 원).", "pct"),
        ("cnykrw", "중국 수출·소비재 비교용.", "pct"),
        ("dxy", "달러의 전 세계 강약. 강하면 신흥국 주식에서 돈이 빠지는 경향.", "pct"),
    ]),
    ("🌡 위험", "risk", [
        ("vix", "미국 주식 옵션으로 본 '앞으로 흔들림' 기대. 높으면 흔들림이 큰 시기 — 오를지 내릴지는 알려 주지 않음.", "pct"),
    ]),
    ("🧺 한국 물가", "prices", [
        ("kr_cpi_yoy", "1년 전보다 물가가 얼마나 올랐나. 한국은행 목표는 2%. 높으면 금리를 내리기 어려움.", "pp"),
    ]),
]
LABELS = {"kr_base": "한국 기준금리", "kr_3y": "국고채 3년", "kr_10y": "국고채 10년", "us_10y": "미국채 10년",
          "us_fed": "미국 기준금리", "us_2y": "미국채 2년", "us_spread": "장단기 금리차 (10년−2년)",
          "us_cpi_yoy": "미국 소비자물가", "us_core_yoy": "미국 근원물가", "us_unrate": "미국 실업률",
          "spx": "S&P 500", "nasdaq": "나스닥",
          "usdkrw": "원/달러", "jpykrw": "원/엔 (100엔)", "cnykrw": "원/위안", "dxy": "달러인덱스",
          "vix": "VIX", "kr_cpi_yoy": "소비자물가 (1년 전 대비)"}
UNITS = {"kr_base": "%", "kr_3y": "%", "kr_10y": "%", "us_10y": "%", "us_fed": "%", "us_2y": "%", "us_spread": "%p",
         "us_cpi_yoy": "%", "us_core_yoy": "%", "us_unrate": "%", "spx": "", "nasdaq": "",
         "usdkrw": "원", "jpykrw": "원", "cnykrw": "원", "dxy": "", "vix": "", "kr_cpi_yoy": "%"}
YOY = {"kr_cpi_yoy": "kr_cpi", "us_cpi_yoy": "us_cpi", "us_core_yoy": "us_core_cpi"}   # 지수 → 1년 전 대비 %
SOURCES = {"ecos": "한국은행 ECOS", "yahoo": "야후 파이낸스", "fred": "FRED (미국 연준)"}
STALE_DAYS = {"D": 7, "M": 50}           # 일별 자료가 7일, 월별이 50일 넘게 안 바뀌면 '지연'
JUMP = {"pct": 8.0, "pp": 1.0}           # 하루 이만큼 넘게 튀면 '확인 필요' (지우지 않고 표시만)
JUMP_BY_SERIES = {"vix": 60.0}           # VIX 는 하루 10~30% 움직임이 흔함

# 다가오는 일정 — 공식 발표를 손으로 넣는다. 날짜는 한국 시각 기준 '결과가 나오는 날'.
# FOMC 는 미국 2일째 오후 2시(동부) = 한국 다음 날 새벽 3~4시. 10·12월 2027 FOMC 는 출처끼리 달라 비워 둠.
EVENTS_CHECKED = "2026-11-24"
EVENTS = [
    ("2026-11-26", "금통위", "한국은행 기준금리 결정"),
    ("2026-12-10", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-01-14", "금통위", "한국은행 기준금리 결정"),
    ("2027-01-28", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-02-25", "금통위", "한국은행 기준금리 결정"),
    ("2027-03-18", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-04-09", "금통위", "한국은행 기준금리 결정"),
    ("2027-04-29", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-05-27", "금통위", "한국은행 기준금리 결정"),
    ("2027-06-10", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-07-15", "금통위", "한국은행 기준금리 결정"),
    ("2027-07-29", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-08-26", "금통위", "한국은행 기준금리 결정"),
    ("2027-09-16", "FOMC", "미국 기준금리 결정 (한국 새벽)"),
    ("2027-10-15", "금통위", "한국은행 기준금리 결정"),
    ("2027-11-25", "금통위", "한국은행 기준금리 결정"),
]


def option_expiry(year: int, month: int) -> dt.date:
    """코스피200 옵션 만기: 둘째 목요일, 휴장이면 그 전 거래일."""
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(3 - d.weekday()) % 7 + 7)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def upcoming_events(today: dt.date, n: int = 3) -> list[dict]:
    items = [(dt.date.fromisoformat(d), kind, desc) for d, kind, desc in EVENTS]
    for k in range(3):
        y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        e = option_expiry(y, m)
        items.append((e, "옵션만기", "선물·옵션 동시 만기 (쿼드러플 위칭)" if m in (3, 6, 9, 12) else "코스피200 옵션 만기"))
    out = [{"date": d, "kind": k, "desc": s, "dday": (d - today).days} for d, k, s in sorted(items) if d >= today]
    return out[:n]


def schedule_gap(today: dt.date) -> str | None:
    """일정표가 비어 가면 운영 화면에 알린다 (1년에 한 번 손으로 넣는 걸 잊지 않게)."""
    last = {}
    for d, kind, _ in EVENTS:
        last[kind] = max(last.get(kind, d), d)
    for kind in ("금통위", "FOMC"):
        if kind not in last or dt.date.fromisoformat(last[kind]) < today + dt.timedelta(days=60):
            return f"📊 지표의 일정표에 두 달 뒤 {kind} 일정이 없습니다 — bkkstock/analysis/macro.py EVENTS 에 공식 일정을 넣어 주세요."
    return None


def _frame(store: Store) -> pd.DataFrame:
    day = store.latest_clean_day("macro")
    if not day:
        return pd.DataFrame()
    df = store.read_clean("macro", day)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])


def _series(df: pd.DataFrame, name: str) -> tuple[pd.Series, str, str]:
    """(날짜 인덱스 값, 주기, 출처). 물가는 지수 → 1년 전 대비 %로 바꾼다."""
    if name in YOY:
        s = df[df["series"] == YOY[name]]
        if s.empty:
            return pd.Series(dtype=float), "M", ""
        v = s.assign(d=pd.to_datetime(s["period"] + "-01")).set_index("d")["value"].sort_index()
        yoy = (v / v.shift(12) - 1) * 100
        return yoy.dropna(), "M", str(s["source"].iloc[0])
    s = df[df["series"] == name]
    if s.empty:
        return pd.Series(dtype=float), "D", ""
    v = s.assign(d=pd.to_datetime(s["period"])).drop_duplicates("d", keep="last").set_index("d")["value"].sort_index()
    return v, str(s["freq"].iloc[0]), str(s["source"].iloc[0])


def card(df: pd.DataFrame, name: str, meaning: str, mode: str, today: dt.date) -> dict:
    s, freq, src = _series(df, name) if not df.empty else (pd.Series(dtype=float), "D", "")
    base = {"name": name, "label": LABELS[name], "unit": UNITS[name], "meaning": meaning, "mode": mode,
            "source": SOURCES.get(src, ""), "freq": freq}
    if s.empty:
        return dict(base, empty=True)
    last_d, last_v = s.index[-1].date(), float(s.iloc[-1])
    year = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    ago = s[s.index <= s.index[-1] - pd.Timedelta(days=28 if freq == "M" else 30)]
    chg = None
    if len(ago):
        a = float(ago.iloc[-1])
        chg = last_v - a if mode == "pp" else ((last_v / a - 1) * 100 if a else None)
    lo, hi = float(year.min()), float(year.max())
    pos = (last_v - lo) / (hi - lo) * 100 if hi > lo else 50.0
    jump = False
    if freq == "D" and len(s) >= 2:
        prev = float(s.iloc[-2])
        d1 = last_v - prev if mode == "pp" else ((last_v / prev - 1) * 100 if prev else 0)
        jump = abs(d1) > JUMP_BY_SERIES.get(name, JUMP[mode])
    spark = year.resample("W").last().dropna() if freq == "D" else year
    if freq == "M":                       # 월간 자료는 그달 말 기준으로 지연을 잰다 (10월 물가는 11월 초에 나옴)
        nxt = (last_d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        ref_end, shown = nxt - dt.timedelta(days=1), f"{last_d.year}년 {last_d.month}월"
    else:
        ref_end, shown = last_d, last_d.isoformat()
    return dict(base, empty=False, value=last_v, date=last_d, shown=shown, chg=chg, lo=lo, hi=hi, pos=pos,
                stale=(today - ref_end).days > STALE_DAYS.get(freq, 7), jump=jump,
                spark=[float(x) for x in spark.tolist()])


def view(store: Store, today: dt.date) -> dict:
    df = _frame(store)
    groups = []
    for title, key, items in GROUPS:
        groups.append({"title": title, "key": key, "cards": [card(df, n, m, mode, today) for n, m, mode in items]})
    fetched = str(df["fetched_at"].max()) if not df.empty and "fetched_at" in df else ""
    return {"groups": groups, "events": upcoming_events(today, 3), "events_checked": EVENTS_CHECKED,
            "fetched_at": fetched, "has_data": not df.empty,
            "has_ecos": (not df.empty) and bool((df["source"] == "ecos").any())}


def home_cells(store: Store, today: dt.date) -> dict:
    """홈 '시장 한눈에' 칸 중 지표에서 오는 것: 원/달러·국고채 3년·미국채 10년(값·기준일·1개월 변화)."""
    df = _frame(store)
    out = {}
    for name, mode in (("usdkrw", "pct"), ("kr_3y", "pp"), ("us_10y", "pp")):
        c = card(df, name, "", mode, today)
        if not c.get("empty"):
            out[name] = c
    return out
