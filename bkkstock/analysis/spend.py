"""💳 소비 — 결제 직전 추천 · 카드 실적 · 상품권 지갑 · 연말정산(신용카드 등 소득공제) 진행. 참고용 어림셈.

규칙은 2026년 사용분 기준: 조특법 §126의2, 한국세무사회 「2026 달라지는 세금제도」(자녀 수별 기본한도),
디지털 온누리 구매 할인 7%(2026-03~). 확인 2026-11-24.

결제 1건마다 '돌아오는 돈'을 더해 순서를 정한다:
    카드 할인·적립 + 상품권 선할인 + 실적 채우기 몫 + (공제 증가분 × 한계세율)
공제 증가분은 올해 예상 사용액(지금까지 × 365/지난 날수)에 이 결제를 더했을 때와 뺐을 때의 차이다.
문턱(총급여 25%)은 공제율이 낮은 신용카드 사용분부터 채워지므로, '언제' 쓰느냐보다 한 해 동안 '무엇으로 얼마' 쓰느냐가 공제를 정한다.
입력값은 서버 DB(meta 'spend_state')에만 저장한다 — 저장소·외부로 보내지 않는다. 금액은 원.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import re

from .move import BRACKETS

MAN = 10_000
LOW_PAY = 70_000_000                 # 총급여 7천만 원 이하: 문화·체육 30%, 한도가 큼
RATES = {"credit": .15, "debit": .30, "culture": .30, "market": .40, "transit": .40}
ORDER = ("credit", "debit", "culture", "market", "transit")       # 문턱은 공제율이 낮은 것부터 채워진다
GROUP_NAMES = {"credit": "신용카드", "debit": "체크·현금영수증·상품권", "culture": "도서·공연·체육",
               "market": "전통시장", "transit": "대중교통"}
ONNURI_RATE = 7.0                    # 디지털 온누리 구매 할인 %
LOCAL_RATE = 10.0                    # 지역사랑상품권 할인 % — 지자체마다 다름(지갑에서 바꿈)
VOUCHER_KINDS = ("온누리", "지역사랑", "민간")
MAX_RECORDS = 5000

# (키, 이름, 공제 묶음, 쓸 수 있는 정책상품권) — general 은 결제수단(신용/체크)에 따라 갈린다
PLACES = (
    ("시장", "전통시장", "market", ("온누리", "지역사랑")),
    ("동네", "동네 가게·식당 (소상공인)", "general", ("온누리", "지역사랑")),
    ("외식", "카페·외식 체인", "general", ("지역사랑",)),
    ("편의점", "편의점", "general", ("지역사랑",)),
    ("마트", "대형마트·백화점", "general", ()),
    ("온라인", "온라인 쇼핑·배달앱·구독", "general", ()),
    ("주유", "주유소", "general", ()),
    ("교통", "버스·지하철", "transit", ()),
    ("문화", "책·공연·영화·헬스·수영", "culture", ()),
    ("병원", "병원·약국", "general", ()),
    ("고정", "공과금·통신·보험·관리비", "none", ()),
    ("해외", "해외·면세점", "none", ()),
    ("기타", "기타", "general", ()),
)
PLACE = {k: (name, grp, vs) for k, name, grp, vs in PLACES}


# ---------------- 입력 ----------------
def num(v, default: float = 0.0) -> float:
    s = str(v if v is not None else "").replace(",", "").replace("원", "").replace("%", "").strip()
    if not s:
        return default
    try:
        x = float(s)
    except ValueError:
        return default
    return max(0.0, x) if x == x else default


def empty() -> dict:
    return {"profile": {"salary": 0.0, "children": 0, "rate": 0.0, "onnuri": ONNURI_RATE, "local": LOCAL_RATE,
                        "base": {k: 0.0 for k in ORDER}, "base_year": 0},
            "cards": [], "vouchers": [], "records": [], "next": 1}


def load(raw: str | None) -> dict:
    s = empty()
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return s
    if isinstance(d, dict):
        for k, v in s.items():
            if isinstance(d.get(k), type(v)):
                s[k] = d[k]
        s["profile"] = {**empty()["profile"], **s["profile"]}
    return s


def dump(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False)


def _new_id(state: dict) -> int:
    i = int(state.get("next") or 1)
    state["next"] = i + 1
    return i


def parse_bonus(text) -> dict:
    """'마트 5, 온라인 3 / 교통 10%' → {'마트': 5.0, '온라인': 3.0, '교통': 10.0} (장소 키만)."""
    out = {}
    for key, val in re.findall(r"([가-힣A-Za-z]+)\s*(\d+(?:\.\d+)?)", str(text or "")):
        if key in PLACE:
            out[key] = min(100.0, float(val))
    return out


def bonus_text(bonus: dict) -> str:
    return ", ".join(f"{k} {v:g}" for k, v in (bonus or {}).items())


def _won(tok: str) -> float:
    n, man = re.fullmatch(r"(\d+(?:\.\d+)?)(만)?", tok).groups()
    return float(n) * (MAN if man else 1)


def parse_caps(text) -> list[list[float]]:
    """'20만 1만, 40만 2만' → [[200000, 10000], [400000, 20000]] — 전월 실적 구간별 월 할인 한도(정액 카드는 월 할인액)."""
    toks = re.findall(r"\d+(?:\.\d+)?만?", str(text or "").replace(",", " "))
    pairs = [[_won(a), _won(b)] for a, b in zip(toks[::2], toks[1::2])]
    return sorted(pairs)


def _man(v: float) -> str:
    return f"{v / MAN:g}만" if v and v % 1000 == 0 else f"{v:g}"


def caps_text(caps) -> str:
    return ", ".join(f"{_man(a)} {_man(b)}" for a, b in caps or [])


def wallet_from_form(state: dict, f, today: dt.date) -> dict:
    """지갑 화면 저장: 프로필 + 카드 줄 + 상품권 줄. 이름이 빈 줄·'지우기' 줄은 버린다."""
    p = state["profile"]
    p.update(salary=num(f.get("salary")) * MAN, children=min(2, int(num(f.get("children")))),
             rate=min(50.0, num(f.get("rate"))), onnuri=min(30.0, num(f.get("onnuri"), ONNURI_RATE)),
             local=min(30.0, num(f.get("local"), LOCAL_RATE)),
             base={k: num(f.get(f"base_{k}")) * MAN for k in ORDER}, base_year=today.year)
    from .cards import PRESET
    this = today.strftime("%Y-%m")
    old = {c["id"]: c for c in state["cards"]}
    cards, i = [], 0
    while f"c{i}_name" in f:
        name = str(f.get(f"c{i}_name") or "").strip()[:30]
        preset = PRESET.get(str(f.get(f"c{i}_preset") or ""))
        cid = int(num(f.get(f"c{i}_id")))
        if preset and not cid:                     # 새 줄에서 목록 카드를 고르면 혜택 칸은 목록 값으로
            card = from_preset(preset, state, today)
            card["name"] = name or card["name"]
            cards.append(card)
        elif name and not f.get(f"c{i}_del"):
            prev = old.get(cid, {})
            card = {"id": cid or _new_id(state), "name": name,
                    "kind": "체크" if f.get(f"c{i}_kind") == "체크" else "신용",
                    "base": min(30.0, num(f.get(f"c{i}_base"))),          # 기본 적립·할인 %
                    "tier": num(f.get(f"c{i}_tier")) * MAN,              # 혜택이 생기는 전월 실적
                    "benefit": num(f.get(f"c{i}_benefit")),              # 실적 채우면 한 달 혜택(원, 어림)
                    "bonus": parse_bonus(f.get(f"c{i}_bonus")),          # 장소별 추가 할인 %
                    "caps": parse_caps(f.get(f"c{i}_caps")),             # 전월 실적 구간별 월 할인 한도
                    "fixed": bool(f.get(f"c{i}_fixed")),                 # 정액 혜택(실적만 채우면 받는 통신비 할인 등)
                    "prev_ok": bool(f.get(f"c{i}_prev")),                # 지난달 실적을 모를 때만 씀
                    "note": prev.get("note", ""), "preset": prev.get("preset", ""),
                    "month_used": prev.get("month_used"), "month": prev.get("month", "")}
            used = str(f.get(f"c{i}_month_used") or "").strip()
            if used:                                   # 카드 앱의 이번 달 이용액 — 다음 달엔 '지난달 실적'이 된다
                card.update(month_used=num(used), month=this)
            elif card["month"] == this:
                card.update(month_used=None, month="")
            cards.append(card)
        i += 1
    vouchers, i = [], 0
    while f"v{i}_name" in f:
        name = str(f.get(f"v{i}_name") or "").strip()[:30]
        kind = f.get(f"v{i}_kind") if f.get(f"v{i}_kind") in VOUCHER_KINDS else "민간"
        if name and not f.get(f"v{i}_del"):
            exp = str(f.get(f"v{i}_exp") or "").strip()
            try:
                exp = dt.date.fromisoformat(exp).isoformat() if exp else ""
            except ValueError:
                exp = ""
            vouchers.append({"id": int(num(f.get(f"v{i}_id"))) or _new_id(state), "kind": kind, "name": name,
                             "balance": num(f.get(f"v{i}_balance")), "exp": exp})
        i += 1
    state["cards"], state["vouchers"] = cards, vouchers
    return state


def from_preset(preset: dict, state: dict, today: dt.date) -> dict:
    keys = ("name", "kind", "base", "tier", "benefit", "bonus", "caps", "fixed", "note")
    return {**{k: preset[k] for k in keys}, "id": _new_id(state), "preset": preset["key"], "prev_ok": True,
            "month_used": None, "month": ""}


def import_presets(state: dict, keys, today: dt.date) -> int:
    """목록 카드를 지갑에 한 번에 넣는다(이미 있는 것은 건너뜀)."""
    from .cards import PRESET
    have = {c.get("preset") for c in state["cards"]}
    n = 0
    for k in keys:
        if k in PRESET and k not in have:
            state["cards"].append(from_preset(PRESET[k], state, today))
            n += 1
    return n


# ---------------- 결제수단 ----------------
def _card(state, cid):
    return next((c for c in state["cards"] if c["id"] == cid), None)


def _voucher(state, vid):
    return next((v for v in state["vouchers"] if v["id"] == vid), None)


def _ref(method: str):
    kind, _, rid = str(method or "").partition(":")
    try:
        return kind, int(rid)
    except ValueError:
        return kind, 0


def method_kind(state: dict, method: str) -> str:
    """신용 | 체크 | 온누리 | 지역사랑 | 민간 | 현금 (지운 카드·상품권은 현금처럼)."""
    kind, rid = _ref(method)
    obj = _card(state, rid) if kind == "c" else _voucher(state, rid) if kind == "v" else None
    return obj["kind"] if obj else "현금"


def method_label(state: dict, method: str) -> str:
    kind, rid = _ref(method)
    obj = _card(state, rid) if kind == "c" else _voucher(state, rid) if kind == "v" else None
    if obj:
        return f"{obj['name']} ({obj['kind']})"
    return "현금·계좌이체" if method == "cash" else "지운 결제수단"


def methods(state: dict) -> list[tuple[str, str]]:
    out = [(f"c:{c['id']}", f"{c['name']} ({c['kind']})") for c in state["cards"]]
    out += [(f"v:{v['id']}", f"{v['name']} ({v['kind']}, 잔액 {v['balance']:,.0f}원)") for v in state["vouchers"]]
    return out + [("cash", "현금·계좌이체 (현금영수증)")]


def group_of(place: str, kind: str, salary: float) -> str | None:
    """이 결제가 들어갈 공제 묶음. None = 공제 안 됨."""
    grp = PLACE.get(place, PLACE["기타"])[1]
    if grp == "none":
        return None
    if grp == "culture" and salary > LOW_PAY:
        grp = "general"
    if grp != "general":
        return grp
    return "credit" if kind == "신용" else "debit"


# ---------------- 연말정산 ----------------
def basic_limit(salary: float, children: int) -> float:
    i = min(max(int(children), 0), 2)
    return ((300, 350, 400) if salary <= LOW_PAY else (250, 275, 300))[i] * MAN


def deduction(a: dict, salary: float, children: int) -> dict:
    low = salary <= LOW_PAY
    threshold = salary * .25
    rem, part = threshold, {}
    for k in ORDER:
        amt = float(a.get(k, 0.0))
        used = min(amt, rem)
        rem -= used
        part[k] = (amt - used) * RATES[k]
    raw = sum(part.values())
    limit = basic_limit(salary, children)
    xlimit = (300 if low else 200) * MAN
    special = part["market"] + part["transit"] + (part["culture"] if low else 0.0)
    general = min(raw, limit)
    extra = min(max(0.0, raw - limit), special, xlimit)
    spent = sum(float(a.get(k, 0.0)) for k in ORDER)
    return {"threshold": threshold, "spent": spent, "over": max(0.0, spent - threshold), "raw": raw, "limit": limit,
            "xlimit": xlimit, "general": general, "extra": extra, "total": general + extra, "part": part}


def earned_deduction(s: float) -> float:
    """근로소득공제."""
    if s <= 5_000_000:
        d = s * .7
    elif s <= 15_000_000:
        d = 3_500_000 + (s - 5_000_000) * .4
    elif s <= 45_000_000:
        d = 7_500_000 + (s - 15_000_000) * .15
    elif s <= 100_000_000:
        d = 12_000_000 + (s - 45_000_000) * .05
    else:
        d = 14_750_000 + (s - 100_000_000) * .02
    return min(d, 20_000_000)


def marginal_rate(p: dict) -> float:
    """한계세율(지방소득세 포함). 직접 넣었으면 그 값, 아니면 본인·자녀 인적공제와 4대보험 8%만 빼서 어림."""
    if p.get("rate"):
        return float(p["rate"]) / 100
    s = float(p.get("salary") or 0)
    base = s - earned_deduction(s) - 1_500_000 * (1 + int(p.get("children") or 0)) - s * .08
    if base <= 0:
        return 0.0
    for top, rate, _ in BRACKETS:
        if base <= top:
            return rate * 1.1
    return 0.495


def ytd(state: dict, today: dt.date) -> dict:
    """올해 묶음별 사용액 = 지갑에 넣은 '앱 기록 전 사용액' + 올해 기록."""
    p, salary = state["profile"], float(state["profile"].get("salary") or 0)
    a = {k: 0.0 for k in ORDER}
    if p.get("base_year") == today.year:
        for k in ORDER:
            a[k] += float((p.get("base") or {}).get(k, 0.0))
    if salary > LOW_PAY:                    # 7천만 원 넘으면 문화·체육 우대가 없다 — 결제수단 몫으로(어림: 체크 쪽)
        a["debit"] += a["culture"]
        a["culture"] = 0.0
    year = str(today.year)
    for r in state["records"]:
        if str(r.get("date", ""))[:4] == year:
            g = group_of(r.get("place"), method_kind(state, r.get("method")), salary)
            if g:
                a[g] += float(r.get("amount") or 0)
    return a


def projected(a: dict, today: dt.date) -> dict:
    days = 366 if calendar.isleap(today.year) else 365
    f = days / max(today.timetuple().tm_yday, 30)
    return {k: v * f for k, v in a.items()}


def advice(proj: dict, fut: dict | None, t: float) -> tuple[str, str]:
    if not fut:
        return "설정 필요", "👛 지갑에서 총급여를 넣으면 연말정산 공제까지 따져서 추천합니다."
    th = fut["threshold"]
    if sum(proj.values()) <= th:
        return "공제 없음", (f"이 속도면 올해 사용액이 문턱({th / MAN:,.0f}만 원)을 넘지 못합니다. "
                           "공제는 신경 쓰지 말고 카드 혜택만 보세요.")
    if proj["credit"] < th:
        return "신용카드 구간", ("신용카드 사용이 문턱보다 적을 전망이라 신용카드를 써도 공제 손해가 없습니다. "
                             "혜택 큰 카드 위주로 쓰세요.")
    if fut["raw"] < fut["limit"]:
        return "체크카드 구간", (f"신용카드가 문턱을 넘을 전망입니다. 일반 가맹점은 체크카드·현금영수증이 공제를 15%p 더 받습니다"
                             f"(돌아오는 세금 약 {15 * t:.1f}%p). 카드 혜택이 그보다 크면 신용카드.")
    return "한도 채움", ("기본 공제 한도를 채울 전망입니다. 일반 결제는 다시 혜택 큰 신용카드로 하세요. "
                     "전통시장·대중교통(·문화)은 추가 한도까지 계속 공제됩니다.")


# ---------------- 카드 실적 ----------------
def _cap_for(card: dict, amount: float | None) -> float | None:
    """전월 실적 amount 로 받는 월 할인 한도(정액 카드는 월 할인액). None = 한도 없음."""
    caps = card.get("caps") or []
    if not caps:
        return None
    if amount is None:                       # 지난달 실적을 모름 → 지갑의 '지난달 실적 채움' 표시대로 첫 구간
        return caps[0][1] if card.get("prev_ok", True) else 0.0
    hit = [cap for need, cap in caps if amount >= need]
    return hit[-1] if hit else 0.0


def card_status(state: dict, card: dict, today: dt.date) -> dict:
    this = today.strftime("%Y-%m")
    prev = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    mine = [r for r in state["records"] if r.get("method") == f"c:{card['id']}"]
    used = sum(float(r["amount"]) for r in mine if str(r["date"])[:7] == this)
    last = sum(float(r["amount"]) for r in mine if str(r["date"])[:7] == prev)
    manual = card.get("month_used")
    if manual is not None and card.get("month") == this:
        used = max(used, float(manual))
    prev_amt = last or None
    if manual is not None and card.get("month") == prev:
        prev_amt = max(last, float(manual))
    caps = card.get("caps") or []
    tier = float(card.get("tier") or (caps[0][0] if caps else 0))
    prev_ok = True if not tier else (prev_amt >= tier if prev_amt is not None else bool(card.get("prev_ok", True)))
    cap = _cap_for(card, prev_amt)
    got = sum(_bonus_won(card, r["place"], float(r["amount"])) for r in mine if str(r["date"])[:7] == this)
    nxt = next(((need, c) for need, c in caps if used < need), None)       # 이번 달에 넘으면 다음 달 한도가 오르는 구간
    return {"used": used, "tier": tier, "left": max(0.0, tier - used), "prev_ok": prev_ok, "prev_amt": prev_amt,
            "pct": min(100.0, used / tier * 100) if tier else 100.0, "cap": cap,
            "cap_left": None if cap is None or card.get("fixed") else max(0.0, cap - got),
            "next_need": nxt[0] if nxt else None, "next_cap": nxt[1] if nxt else None,
            "days_left": calendar.monthrange(today.year, today.month)[1] - today.day}


def _bonus_won(card: dict, place: str, amount: float) -> float:
    return amount * float((card.get("bonus") or {}).get(place, 0)) / 100


def card_rate(card: dict, place: str, prev_ok: bool) -> float:
    """이 장소에서 받는 할인·적립 % (지난달 실적을 못 채우면 기본 적립만)."""
    return float(card.get("base") or 0) + (float((card.get("bonus") or {}).get(place, 0)) if prev_ok else 0.0)


def card_discount(card: dict, st: dict, place: str, amount: float) -> tuple[float, str]:
    """이 결제에서 받는 카드 할인·적립(원)과 설명 — 추가 할인은 이번 달 남은 한도까지만."""
    base = amount * float(card.get("base") or 0) / 100
    bonus = _bonus_won(card, place, amount) if st["prev_ok"] else 0.0
    why = f"{card_rate(card, place, st['prev_ok']):g}%"
    if bonus and st["cap_left"] is not None and bonus > st["cap_left"]:
        bonus = st["cap_left"]
        why += f" · 이번 달 남은 한도 {st['cap_left']:,.0f}원까지"
    return base + bonus, why


def fill_value(card: dict, st: dict, amount: float) -> tuple[float, str]:
    """이 결제가 다음 달 혜택에 보태는 몫(원). 정액 카드는 구간별 월 할인액 차이, 그 밖은 '한 달 혜택'을 실적 조건으로 나눔."""
    caps = card.get("caps") or []
    used = st["used"]
    if card.get("fixed") and caps:
        lower = [(need, c) for need, c in caps if used >= need]
        lo_need, lo_cap = lower[-1] if lower else (0.0, 0.0)
        if st["next_need"] is None:
            return 0.0, ""
        span = st["next_need"] - lo_need
        x = min(amount, st["next_need"] - used)
        return (st["next_cap"] - lo_cap) * x / span, f"다음 달 할인 {st['next_cap']:,.0f}원까지 {st['next_need'] - used:,.0f}원 남음"
    if st["tier"] and card.get("benefit") and st["left"] > 0:
        return float(card["benefit"]) * min(amount, st["left"]) / st["tier"], f"이번 달 실적까지 {st['left']:,.0f}원 남음"
    return 0.0, ""


def _live_vouchers(state, kind, today):
    vs = [v for v in state["vouchers"] if v["kind"] == kind and v["balance"] > 0
          and not (v.get("exp") and v["exp"] < today.isoformat())]
    return sorted(vs, key=lambda v: v.get("exp") or "9999")          # 만료가 가까운 것부터 쓴다


# ---------------- 추천 ----------------
def recommend(state: dict, place: str, amount: float, today: dt.date) -> dict:
    p = state["profile"]
    salary, children = float(p.get("salary") or 0), int(p.get("children") or 0)
    t = marginal_rate(p) if salary else 0.0
    proj = projected(ytd(state, today), today)
    base = deduction(proj, salary, children)["total"] if salary else 0.0

    def tax(grp, x):
        if not grp or not salary or x <= 0:
            return 0.0
        q = dict(proj)
        q[grp] += x
        return (deduction(q, salary, children)["total"] - base) * t

    def option(label, method, parts, note="", covered=amount):
        parts = [(n, v, why) for n, v, why in parts if v > 0.5]
        value = sum(v for _, v, _ in parts)
        return {"label": label, "method": method, "parts": parts, "note": note, "value": value,
                "pct": value / covered * 100 if covered else 0.0, "covered": covered}

    opts = []
    for c in state["cards"]:
        st = card_status(state, c, today)
        disc, why = card_discount(c, st, place, amount)
        fill, fill_why = fill_value(c, st, amount)
        grp = group_of(place, c["kind"], salary)
        note = "" if st["prev_ok"] else "지난달 실적을 못 채워 이번 달 추가 할인이 없습니다"
        opts.append(option(f"{c['name']} ({c['kind']})", f"c:{c['id']}", [
            ("카드 할인·적립", disc, why), ("실적 채우기", fill, fill_why),
            ("공제로 돌아오는 세금", tax(grp, amount), GROUP_NAMES.get(grp, ""))], note))
    for kind, rate in (("온누리", p.get("onnuri", ONNURI_RATE)), ("지역사랑", p.get("local", LOCAL_RATE))):
        if kind not in PLACE.get(place, PLACE["기타"])[2]:
            continue
        vs = _live_vouchers(state, kind, today)
        bal = sum(v["balance"] for v in vs)
        if not bal:
            continue
        use = min(amount, bal)
        notes = [] if (place, kind) == ("시장", "온누리") else ["가맹점인지 확인하세요"]
        if use < amount:
            notes.append(f"잔액 {bal:,.0f}원 — 나머지 {amount - use:,.0f}원은 다른 수단으로")
        opts.append(option(f"{kind}상품권", f"v:{vs[0]['id']}", [
            ("상품권 할인", use * float(rate) / 100, f"{float(rate):g}%"),
            ("공제로 돌아오는 세금", tax(group_of(place, kind, salary), use), "")], " · ".join(notes), use))
    opts.append(option("현금·계좌이체 (현금영수증)", "cash", [
        ("공제로 돌아오는 세금", tax(group_of(place, "현금", salary), amount), "")]))
    opts.sort(key=lambda o: (-o["pct"], -o["value"]))
    return {"place": place, "place_name": PLACE.get(place, PLACE["기타"])[0], "amount": amount, "options": opts,
            "rate": t, "advice": advice(proj, deduction(proj, salary, children) if salary else None, t)}


# ---------------- 기록 ----------------
def add_record(state: dict, f, today: dt.date) -> str | None:
    amount, place, method = num(f.get("amount")), f.get("place"), str(f.get("method") or "cash")
    if amount <= 0 or place not in PLACE:
        return "금액과 장소를 넣어 주세요."
    try:
        day = dt.date.fromisoformat(str(f.get("date") or ""))
    except ValueError:
        day = today
    kind, rid = _ref(method)
    if kind == "c" and not _card(state, rid) or kind == "v" and not _voucher(state, rid) or kind not in ("c", "v", "cash"):
        return "결제수단을 다시 골라 주세요."
    if kind == "v":
        v = _voucher(state, rid)
        v["balance"] = max(0.0, v["balance"] - amount)
    state["records"].append({"id": _new_id(state), "date": day.isoformat(), "amount": amount, "place": place,
                             "method": method, "memo": str(f.get("memo") or "").strip()[:40]})
    state["records"] = sorted(state["records"], key=lambda r: (r["date"], r["id"]))[-MAX_RECORDS:]
    return None


def delete_record(state: dict, rid: int) -> bool:
    r = next((r for r in state["records"] if r["id"] == rid), None)
    if not r:
        return False
    kind, vid = _ref(r["method"])
    if kind == "v" and _voucher(state, vid):
        _voucher(state, vid)["balance"] += float(r["amount"])          # 상품권으로 낸 기록을 지우면 잔액을 돌려놓는다
    state["records"].remove(r)
    return True


def benefit_of(state: dict, r: dict, today: dt.date) -> float:
    """기록 1건의 즉시 혜택(카드 할인·적립 또는 상품권 할인) 어림 — 지금 카드 설정 기준."""
    kind, rid = _ref(r.get("method"))
    amt = float(r.get("amount") or 0)
    if kind == "c" and _card(state, rid):
        c = _card(state, rid)
        return amt * card_rate(c, r.get("place"), card_status(state, c, today)["prev_ok"]) / 100   # 월 한도 전 어림
    if kind == "v" and _voucher(state, rid):
        rate = {"온누리": "onnuri", "지역사랑": "local"}.get(_voucher(state, rid)["kind"])
        return amt * float(state["profile"].get(rate, 0)) / 100 if rate else 0.0
    return 0.0


def record_rows(state: dict, today: dt.date, limit: int | None = None) -> list[dict]:
    rows = [{**r, "place_name": PLACE.get(r.get("place"), PLACE["기타"])[0],
             "method_name": method_label(state, r.get("method")), "benefit": benefit_of(state, r, today)}
            for r in reversed(state["records"])]
    return rows[:limit] if limit else rows


# ---------------- 대시보드 ----------------
def dashboard(state: dict, today: dt.date) -> dict:
    p = state["profile"]
    salary, children = float(p.get("salary") or 0), int(p.get("children") or 0)
    t = marginal_rate(p) if salary else 0.0
    a = ytd(state, today)
    proj = projected(a, today)
    now = deduction(a, salary, children) if salary else None
    fut = deduction(proj, salary, children) if salary else None
    month = today.strftime("%Y-%m")
    mrec = [r for r in state["records"] if str(r["date"])[:7] == month]
    vouchers = []
    for v in state["vouchers"]:
        left = (dt.date.fromisoformat(v["exp"]) - today).days if v.get("exp") else None
        warn = []
        if left is not None and left < 0:
            warn.append("만료됨")
        elif left is not None and left <= 30:
            warn.append(f"만료 {left}일 전")
        if v["kind"] == "민간" and v["balance"] > 0:
            warn.append("민간상품권은 발행사가 망하면 못 씁니다 — 먼저 쓰세요")
        vouchers.append({**v, "days_left": left, "warn": warn})
    zone, text = advice(proj, fut, t)
    return {"ready": bool(salary or state["cards"]), "salary": salary, "rate": t, "ytd": a, "proj": proj,
            "now": now, "fut": fut, "refund_now": now["total"] * t if now else 0.0,
            "refund_fut": fut["total"] * t if fut else 0.0, "zone": zone, "advice": text,
            "cards": [{**c, **card_status(state, c, today)} for c in state["cards"]], "vouchers": vouchers,
            "month_spent": sum(float(r["amount"]) for r in mrec),
            "month_benefit": sum(benefit_of(state, r, today) for r in mrec),
            "recent": record_rows(state, today, 5), "groups": GROUP_NAMES}
