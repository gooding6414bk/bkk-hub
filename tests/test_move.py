"""🏡 갈아타기 계산."""
import pytest

from bkkstock.analysis import move


def test_high_price_single_home_tax():
    p = move.Plan(sell_price=20, buy_cost=9, expenses=0.3, hold_years=7, live_years=7, exempt_ok=True)
    cg = move.capital_gains(p)
    assert cg["taxable_gain"] == pytest.approx(10.7e8 * 8 / 20)                      # 12억 넘는 부분만
    assert cg["ltgd"] == pytest.approx(cg["taxable_gain"] * .56)                      # 보유 28% + 거주 28%
    base = cg["taxable_gain"] * .44 - 2_500_000
    assert cg["total"] == pytest.approx((base * .38 - 19_940_000) * 1.1)


def test_under_12_is_exempt_and_short_hold_is_heavy():
    assert move.capital_gains(move.Plan(sell_price=11, buy_cost=5, hold_years=3, live_years=3))["total"] == 0
    short = move.capital_gains(move.Plan(sell_price=11, buy_cost=10, hold_years=1.5, live_years=1.5, exempt_ok=True))
    assert short["total"] == pytest.approx((1e8 - 2_500_000) * .60 * 1.1)              # 2년 미만 → 비과세 아님, 60%


def test_no_residence_gets_general_deduction():
    rate, text = move.ltgd_rate(move.Plan(hold_years=8, live_years=0, exempt_ok=True))
    assert rate == pytest.approx(.16) and "일반" in text


def test_acquisition_tax_and_loan_tiers():
    a = move.acquisition_tax(7.5e8, False)
    assert a["rate"] == pytest.approx(.02) and a["total"] == pytest.approx(7.5e8 * .022)
    assert move.acquisition_tax(30e8, True)["total"] == pytest.approx(30e8 * (.033 + .002))
    assert move.loan_cap(12e8)[0] == pytest.approx(4.8e8)          # LTV 40% 가 더 작음
    assert move.loan_cap(14e8)[0] == pytest.approx(5.6e8)
    assert move.loan_cap(20e8)[0] == pytest.approx(4e8) and move.loan_cap(30e8)[0] == pytest.approx(2e8)


def test_calc_gap():
    r = move.calc(move.Plan(sell_price=20, buy_cost=9, hold_years=7, live_years=7, target_price=30, cash=2, want_loan=True))
    assert r["ready"] and r["loan"] == pytest.approx(2e8)
    assert r["gap"] == pytest.approx(r["net_sale"] + 2e8 + 2e8 - (30e8 + r["acq"]["total"] + r["buy_fee"]))
    assert r["gap"] < 0


def test_wait_effect_and_scenarios():
    p = move.Plan(sell_price=21, buy_cost=6.3, expenses=0.3, hold_years=7, live_years=7, exempt_ok=True, cash=1)
    w = move.wait_effect(p)
    assert w["rate_now"] == pytest.approx(56) and w["later"] < w["now"] and w["years"] == pytest.approx(3)
    taxable = 14.4e8 * 9 / 21
    assert w["later"] == pytest.approx(move.income_tax(taxable * .2 - 2_500_000) * 1.1)
    sc = {round(x["price"]): x for x in move.scenarios(p)}
    assert set(sc) == {25, 30, 35}
    assert sc[25]["loan"] == pytest.approx(4e8) and sc[30]["loan"] == pytest.approx(2e8)
    assert sc[30]["cash_needed"] > sc[25]["cash_needed"] + 5e8          # 25억을 넘으면 대출이 줄어 현금이 더 크게 는다
    assert move.holding_tax_note(move.Plan(gongsi=11))["taxable"] is False
    assert move.holding_tax_note(move.Plan(gongsi=14))["base"] == pytest.approx(2e8 * .6)


def test_switch_cost_breakeven_and_joint_ownership():
    p = move.Plan(sell_price=21, buy_cost=6.3, expenses=0.3, hold_years=7, live_years=7, exempt_ok=True, age=40)
    sw = move.switch_cost(p, 30)
    expect = move.capital_gains(p)["total"] + 21e8 * .007 + 30e8 * .033 + 30e8 * .007
    assert sw["total"] == pytest.approx(expect) and sw["breakeven"] == pytest.approx(expect / 30e8 * 100)
    jc = move.holding_tax_owner_compare(p, 21)              # 공시가 21억 집
    assert jc["elder"] == 0 and jc["joint"] < jc["single"]  # 40세: 고령자 공제 없음 → 공동명의(18억 공제)가 유리
    old = move.holding_tax_owner_compare(move.Plan(age=70), 21)
    assert old["elder"] == .40 and old["single"] < jc["single"]
