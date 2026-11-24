"""화면 설정(사용자 맞춤): 홈 위젯 순서·보이기, 내 종목 정렬·보기, 종목 화면 섹션 펼침.

휴대폰과 PC가 같은 설정 하나를 쓴다(폭에 따른 배치는 CSS가 정한다). 서버 meta 'ui.prefs' 에 JSON 으로.
서버가 아는 키·값만 받는다 — 나머지는 버린다.
"""

from __future__ import annotations

import copy
import json

# (id, 이름, 설명, 기본 보이기, PC 폭 클래스, 잠금)
WIDGETS = [
    ("risk", "오늘 볼 것", "내 종목의 매수 금지·위험 공시·주의", True, "w-wide", True),
    ("pnl", "보유 손익", "평가금액·평가손익·오늘 손익", True, "w-narrow", False),
    ("watch", "내 종목", "보유·관심 종목 목록", True, "w-wide", False),
    ("events", "다가오는 일정", "금통위·FOMC·옵션만기", True, "w-narrow", False),
    ("market", "시장 한눈에", "코스피·코스닥·환율·금리·EWY", True, "w-span2", False),
    ("ind", "지표 요약", "미국 금리·장단기 금리차·VIX·달러", False, "w-narrow", False),
    ("alerts", "최근 알림", "텔레그램으로 보낸(또는 못 보낸) 알림", False, "w-wide", False),
    ("mktrisk", "시장 전체 위험", "재료 없는 급등·위험 공시 (매수 후보 아님)", False, "w-span2", False),
    ("status", "수집 상태", "꺼 두어도 문제가 생기면 저절로 보임", False, "w-span2", False),
]
WIDGET_IDS = [w[0] for w in WIDGETS]
WATCH_SORTS = {"own": "보유 먼저", "risk": "위험 먼저", "pnl": "평가손익 큰 순", "name": "이름 순"}
WATCH_VIEWS = {"list": "목록", "compact": "간단히"}
STOCK_SECTIONS = ["risk", "chart", "flow", "fin", "div", "news", "disc", "forecast"]

DEFAULT = {
    "home": [[w[0], w[3]] for w in WIDGETS],
    "watch": {"sort": "own", "view": "list"},
    "stock": {"closed": []},
}
KEY = "ui.prefs"


def load(store) -> dict:
    out = copy.deepcopy(DEFAULT)
    try:
        saved = json.loads(store.meta_get(KEY) or "{}")
    except ValueError:
        saved = {}
    return merge(out, saved)


def merge(base: dict, new: dict) -> dict:
    """new 에서 아는 것만 받아 base 에 덮는다."""
    if not isinstance(new, dict):
        return base
    home = new.get("home")
    if isinstance(home, list):
        seen, rows = set(), []
        for item in home:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] in WIDGET_IDS and item[0] not in seen:
                seen.add(item[0])
                rows.append([item[0], bool(item[1]) or item[0] == "risk"])     # '오늘 볼 것'은 숨길 수 없다
        for wid, *_ in WIDGETS:                                                # 새로 생긴 위젯은 뒤에 기본값으로
            if wid not in seen:
                rows.append([wid, dict((w[0], w[3]) for w in WIDGETS)[wid]])
        base["home"] = rows
    watch = new.get("watch")
    if isinstance(watch, dict):
        if watch.get("sort") in WATCH_SORTS:
            base["watch"]["sort"] = watch["sort"]
        if watch.get("view") in WATCH_VIEWS:
            base["watch"]["view"] = watch["view"]
    stock = new.get("stock")
    if isinstance(stock, dict) and isinstance(stock.get("closed"), list):
        base["stock"]["closed"] = [s for s in stock["closed"] if s in STOCK_SECTIONS][:len(STOCK_SECTIONS)]
    return base


def save(store, new: dict) -> dict:
    cur = copy.deepcopy(DEFAULT) if (isinstance(new, dict) and new.get("reset")) else load(store)
    merged = merge(cur, new if isinstance(new, dict) else {})
    store.meta_set(KEY, json.dumps(merged, ensure_ascii=False))
    return merged


def home_layout(prefs: dict) -> list[dict]:
    info = {w[0]: w for w in WIDGETS}
    return [{"id": wid, "name": info[wid][1], "desc": info[wid][2], "on": on, "width": info[wid][4], "locked": info[wid][5]}
            for wid, on in prefs["home"]]


def sort_watch(rows: list[dict], mode: str) -> list[dict]:
    if mode == "name":
        return sorted(rows, key=lambda w: (w.get("name") or w["code"]))
    if mode == "pnl":
        return sorted(rows, key=lambda w: (w.get("pnl") is None, -(w.get("pnl") or 0)))
    if mode == "risk":
        def risk(w):
            lv = [f["level"] for f in w.get("flags") or []]
            return (0 if w.get("blocked") or "block" in lv else 1 if "warn" in lv else 2, w.get("kind") != "보유")
        return sorted(rows, key=risk)
    return sorted(rows, key=lambda w: w.get("kind") != "보유")          # own: 보유 먼저(넣은 순서 유지)
