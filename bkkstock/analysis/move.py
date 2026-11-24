"""🏡 우리 집 갈아타기(1주택 → 상급지 1주택) 계산 — 참고용 어림셈.

세금·대출 규칙은 2026-11 기준(지식 > 부동산 탭과 같은 출처). 실제 신고·대출 전에는 세무사·은행 확인.
입력값은 서버 DB(meta 'move_plan')에만 저장한다 — 저장소·외부로 보내지 않는다.
금액 단위는 원. 화면에서는 억 원으로 받는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

EOK = 100_000_000
HIGH_PRICE = 12 * EOK            # 1세대 1주택 고가주택 기준 (실거래가)
BASIC_DEDUCTION = 2_500_000      # 양도소득 기본공제

# 종합소득세율 (과세표준 상한, 세율, 누진공제) — 2023년 귀속부터
BRACKETS = [(14_000_000, .06, 0), (50_000_000, .15, 1_260_000), (88_000_000, .24, 5_760_000),
            (150_000_000, .35, 15_440_000), (300_000_000, .38, 19_940_000), (500_000_000, .40, 25_940_000),
            (1_000_000_000, .42, 35_940_000), (float("inf"), .45, 65_940_000)]


@dataclass
class Plan:
    sell_price: float = 0.0        # 지금 집 예상 매도가 (억)
    buy_cost: float = 0.0          # 지금 집 취득가 (억)
    expenses: float = 0.0          # 필요경비: 취득세·중개보수·확장/섀시 등 증빙 (억)
    hold_years: float = 0.0        # 보유 연수
    live_years: float = 0.0        # 거주 연수
    exempt_ok: bool = True         # 1세대 1주택 비과세 요건(2년 보유, 조정지역 취득이면 2년 거주) 충족
    target_price: float = 0.0      # 갈 집 매수가 (억)
    target_over85: bool = False    # 갈 집 전용 85㎡ 초과
    cash: float = 0.0              # 지금 쓸 수 있는 현금·예금 (억)
    want_loan: bool = True         # 주담대를 쓸지
    note: str = ""                 # 메모 (단지 이름 등, 화면에만)
    area: str = ""                 # 평형 (예: 25평 · 전용 59㎡)
    owner: str = ""                # 명의 (예: 배우자 단독)
    gongsi: float = 0.0            # 올해 공시가격 (억, 알리미에서) — 보유세 어림용
    age: int = 0                   # 나이 (종부세 고령자 공제·대출 만기·청약 판단용, 이 서버에만)

    @classmethod
    def from_form(cls, f) -> "Plan":
        def num(k):
            try:
                return max(0.0, float(str(f.get(k, "") or 0).replace(",", "")))
            except ValueError:
                return 0.0
        return cls(sell_price=num("sell_price"), buy_cost=num("buy_cost"), expenses=num("expenses"),
                   hold_years=num("hold_years"), live_years=num("live_years"), exempt_ok=bool(f.get("exempt_ok")),
                   target_price=num("target_price"), target_over85=bool(f.get("target_over85")), cash=num("cash"),
                   want_loan=bool(f.get("want_loan")), note=str(f.get("note", ""))[:60],
                   area=str(f.get("area", ""))[:30], owner=str(f.get("owner", ""))[:30], gongsi=num("gongsi"),
                   age=int(min(num("age"), 120)))

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in keys})

    def to_dict(self) -> dict:
        return asdict(self)


def income_tax(base: float) -> float:
    if base <= 0:
        return 0.0
    for top, rate, ded in BRACKETS:
        if base <= top:
            return base * rate - ded
    return 0.0


def ltgd_rate(p: Plan) -> tuple[float, str]:
    """장기보유특별공제율과 설명."""
    y, r = int(p.hold_years), int(p.live_years)
    if y < 3:
        return 0.0, "보유 3년 미만 — 장기보유특별공제 없음"
    if p.exempt_ok and r >= 2:
        hold = min(y, 10) * .04
        live = .08 if r == 2 else min(r, 10) * .04
        return hold + live, f"1세대 1주택: 보유 {min(y, 10)}년 × 4% + 거주 {min(r, 10)}년 × 4%"
    return min(y, 15) * .02, f"일반: 보유 {min(y, 15)}년 × 2% (거주 2년 미만이라 1주택 공제율을 못 받음)"


def capital_gains(p: Plan) -> dict:
    price, cost = p.sell_price * EOK, p.buy_cost * EOK
    gain = max(0.0, price - cost - p.expenses * EOK)
    out = {"gain": gain, "taxable_gain": 0.0, "ltgd": 0.0, "ltgd_text": "", "base": 0.0, "tax": 0.0, "local": 0.0,
           "total": 0.0, "rate_note": ""}
    if gain <= 0 or price <= 0:
        out["rate_note"] = "양도차익 없음"
        return out
    if p.exempt_ok and p.hold_years >= 2:
        if price <= HIGH_PRICE:
            out["rate_note"] = "12억 원 이하 1세대 1주택 — 비과세"
            return out
        taxable = gain * (price - HIGH_PRICE) / price
        out["rate_note"] = f"12억 원 넘는 부분만 과세 ({(price - HIGH_PRICE) / price * 100:.0f}%)"
    else:
        taxable = gain
        out["rate_note"] = "비과세 요건 미충족 — 차익 전체 과세"
    rate, text = ltgd_rate(p)
    ltgd = taxable * rate
    base = max(0.0, taxable - ltgd - BASIC_DEDUCTION)
    if p.hold_years < 1:
        tax, out["rate_note"] = base * .70, out["rate_note"] + " · 1년 미만 보유 70%"
    elif p.hold_years < 2:
        tax, out["rate_note"] = base * .60, out["rate_note"] + " · 2년 미만 보유 60%"
    else:
        tax = income_tax(base)
    out.update(taxable_gain=taxable, ltgd=ltgd, ltgd_text=text, base=base, tax=tax, local=tax * .1, total=tax * 1.1)
    return out


def brokerage(price: float) -> tuple[float, str]:
    """매매 중개보수 상한 (부가세 별도)."""
    if price < 50_000_000:
        rate = .006
    elif price < 200_000_000:
        rate = .005
    elif price < 900_000_000:
        rate = .004
    elif price < 1_200_000_000:
        rate = .005
    elif price < 1_500_000_000:
        rate = .006
    else:
        rate = .007
    return price * rate, f"상한 {rate * 100:.1f}% (부가세 별도, 협의 가능)"


def acquisition_tax(price: float, over85: bool) -> dict:
    """1주택(일시적 2주택 포함) 취득세 + 지방교육세 + 농어촌특별세."""
    if price <= 600_000_000:
        rate = .01
    elif price <= 900_000_000:
        rate = (price / EOK * 2 / 3 - 3) / 100
    else:
        rate = .03
    acq = price * rate
    edu = acq * .1
    farm = price * .002 if over85 else 0.0
    return {"rate": rate, "acq": acq, "edu": edu, "farm": farm, "total": acq + edu + farm}


def loan_cap(price: float) -> tuple[float, str]:
    """수도권·규제지역 주담대: 집값 구간 한도와 LTV 40% 중 작은 값. DSR 은 소득에 따라 더 줄 수 있음."""
    if price <= 15 * EOK:
        tier = 6 * EOK
    elif price <= 25 * EOK:
        tier = 4 * EOK
    else:
        tier = 2 * EOK
    ltv = price * .40
    cap = min(tier, ltv)
    why = "집값 구간 한도" if tier <= ltv else "LTV 40%"
    return cap, f"{why} 기준 (15억 이하 6억 · 25억 이하 4억 · 25억 초과 2억, LTV 40% 중 작은 값). DSR·소득에 따라 더 줄 수 있음"


def calc(p: Plan) -> dict:
    sell = p.sell_price * EOK
    cg = capital_gains(p)
    sell_fee, sell_fee_note = brokerage(sell) if sell else (0.0, "")
    net_sale = max(0.0, sell - cg["total"] - sell_fee)
    tgt = p.target_price * EOK
    acq = acquisition_tax(tgt, p.target_over85) if tgt else {"rate": 0, "acq": 0, "edu": 0, "farm": 0, "total": 0}
    buy_fee, buy_fee_note = brokerage(tgt) if tgt else (0.0, "")
    loan, loan_note = loan_cap(tgt) if (tgt and p.want_loan) else (0.0, "대출 안 씀")
    need = tgt + acq["total"] + buy_fee
    have = net_sale + p.cash * EOK + loan
    return {"cg": cg, "sell_fee": sell_fee, "sell_fee_note": sell_fee_note, "net_sale": net_sale, "acq": acq,
            "buy_fee": buy_fee, "buy_fee_note": buy_fee_note, "loan": loan, "loan_note": loan_note,
            "need": need, "have": have, "gap": have - need, "ready": bool(sell and tgt)}


def wait_effect(p: Plan) -> dict | None:
    """같은 값에 '보유·거주 10년을 채우고' 팔면 양도세가 얼마나 줄어드나 (장기보유특별공제 최대 80%)."""
    if not (p.sell_price and p.exempt_ok) or (p.hold_years >= 10 and p.live_years >= 10):
        return None
    now = capital_gains(p)
    later = capital_gains(Plan(**{**p.to_dict(), "hold_years": max(10.0, p.hold_years), "live_years": max(10.0, p.live_years)}))
    years = max(10 - p.hold_years, 10 - p.live_years, 0)
    return {"now": now["total"], "later": later["total"], "save": now["total"] - later["total"], "years": years,
            "rate_now": ltgd_rate(p)[0] * 100}


def scenarios(p: Plan) -> list[dict]:
    """갈 집 가격별로 '모자라는 돈'을 한 표에. 지금 집 매도가 기준 +4억·+9억·+14억과 사용자가 넣은 값."""
    if not p.sell_price:
        return []
    prices = {round(p.sell_price + d) for d in (4, 9, 14)}
    if p.target_price:
        prices.add(round(p.target_price, 1))
    out = []
    for t in sorted(prices):
        r = calc(Plan(**{**p.to_dict(), "target_price": float(t), "want_loan": True}))
        sw = switch_cost(p, float(t))
        out.append({"price": t, "loan": r["loan"], "acq": r["acq"]["total"], "fee": r["buy_fee"], "need": r["need"],
                    "net_sale": r["net_sale"], "cash_needed": max(0.0, r["need"] - r["net_sale"] - r["loan"]),
                    "switch": sw["total"], "breakeven": sw["breakeven"]})
    return out


def holding_tax_note(p: Plan) -> dict | None:
    """1세대 1주택 종부세 대상인지만 어림 (공시가격 12억 원 공제). 정확한 세액은 홈택스 모의계산."""
    if not p.gongsi:
        return None
    g = p.gongsi * EOK
    over = max(0.0, g - 12 * EOK)
    base = over * .60
    return {"gongsi": g, "over": over, "base": base, "taxable": over > 0,
            "rough": base * .005 if over else 0.0}     # 과세표준 3억 이하 0.5% (세액공제 전)


def switch_cost(p: Plan, target: float | None = None) -> dict:
    """갈아타기에 '사라지는 돈': 양도세 + 판 집 중개보수 + 취득세 등 + 산 집 중개보수.
    breakeven = 이 비용을 메우려면 갈 집이 지금 집보다 '추가로' 몇 % 더 올라야 하나 (갈 집 가격 대비)."""
    t = (target if target is not None else p.target_price) * EOK
    sell = p.sell_price * EOK
    tax = capital_gains(p)["total"] if sell else 0.0
    sf = brokerage(sell)[0] if sell else 0.0
    acq = acquisition_tax(t, p.target_over85)["total"] if t else 0.0
    bf = brokerage(t)[0] if t else 0.0
    total = tax + sf + acq + bf
    return {"tax": tax, "sell_fee": sf, "acq": acq, "buy_fee": bf, "total": total,
            "breakeven": total / t * 100 if t else None}


def holding_tax_owner_compare(p: Plan, gongsi_new: float) -> dict:
    """갈 집 종부세: 단독(1세대 1주택 12억 공제 + 나이·보유 세액공제) vs 부부 공동(각자 9억 = 18억 공제).
    과세표준 = (공시가 − 공제) × 60%, 세율은 1주택 구간 0.5~2.7% 중 낮은 구간만 어림(세부담 상한·재산세 공제 무시)."""
    g = gongsi_new * EOK

    def tax(base):
        steps = [(3e8, .005), (6e8, .007), (12e8, .010), (25e8, .013), (50e8, .015), (94e8, .020), (float("inf"), .027)]
        out, prev = 0.0, 0.0
        for top, r in steps:
            if base > prev:
                out += (min(base, top) - prev) * r
            prev = top
        return out
    age = p.age or 0
    elder = .40 if age >= 70 else .30 if age >= 65 else .20 if age >= 60 else 0.0
    single = tax(max(0.0, g - 12 * EOK) * .6) * (1 - min(.8, elder))          # 새 집이라 보유 공제는 0으로 시작
    joint = 2 * tax(max(0.0, g / 2 - 9 * EOK) * .6)
    return {"single": single, "joint": joint, "elder": elder}
