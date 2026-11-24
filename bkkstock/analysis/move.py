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
                   want_loan=bool(f.get("want_loan")), note=str(f.get("note", ""))[:60])

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
