"""💳 소비: 연말정산 공제 · 추천 순서 · 기록과 상품권 잔액."""
import datetime as dt

import pytest

from bkkstock.analysis import spend as sp

MAN = 10_000
DAY = dt.date(2026, 11, 24)


def state(salary=5000, **kw):
    s = sp.empty()
    s["profile"].update(salary=salary * MAN, base_year=DAY.year, **kw)
    return s


def test_threshold_is_filled_by_credit_first():
    d = sp.deduction({"credit": 2000 * MAN}, 5000 * MAN, 0)
    assert d["threshold"] == 1250 * MAN and d["total"] == pytest.approx(750 * MAN * .15)
    # 신용 1,000만 + 체크 1,500만: 문턱 1,250만 = 신용 1,000 + 체크 250 → 체크 1,250만 × 30% = 375만 → 기본한도 300만
    d = sp.deduction({"credit": 1000 * MAN, "debit": 1500 * MAN}, 5000 * MAN, 0)
    assert d["raw"] == pytest.approx(375 * MAN) and d["total"] == pytest.approx(300 * MAN)


def test_extra_limit_and_children():
    a = {"credit": 1000 * MAN, "debit": 1500 * MAN, "market": 500 * MAN}
    d = sp.deduction(a, 5000 * MAN, 0)
    assert d["general"] == 300 * MAN and d["extra"] == pytest.approx(200 * MAN)       # 전통시장 500만 × 40%
    assert sp.deduction(a, 5000 * MAN, 2)["limit"] == 400 * MAN
    high = sp.deduction({"credit": 3000 * MAN, "debit": 2000 * MAN, "transit": 1000 * MAN}, 8000 * MAN, 1)
    assert high["limit"] == 275 * MAN and high["extra"] == pytest.approx(200 * MAN)    # 7천 초과: 추가한도 200만


def test_marginal_rate_estimate():
    assert sp.marginal_rate({"salary": 5000 * MAN, "children": 0}) == pytest.approx(.165)
    assert sp.marginal_rate({"salary": 5000 * MAN, "rate": 26.4}) == pytest.approx(.264)
    assert sp.marginal_rate({"salary": 0}) == 0


def test_market_prefers_onnuri_and_fixed_bills_get_no_deduction():
    s = state(base={"credit": 1500 * MAN, "debit": 0, "culture": 0, "market": 0, "transit": 0})
    s["cards"] = [{"id": 1, "name": "A", "kind": "신용", "base": 1, "tier": 0, "benefit": 0, "bonus": {"고정": 5},
                   "prev_ok": True}]
    s["vouchers"] = [{"id": 2, "kind": "온누리", "name": "온누리", "balance": 200_000, "exp": ""}]
    r = sp.recommend(s, "시장", 100_000, DAY)
    assert r["options"][0]["label"] == "온누리상품권"
    assert r["options"][0]["parts"][0][1] == pytest.approx(7000)                        # 7% 할인
    bills = sp.recommend(s, "고정", 100_000, DAY)
    assert bills["options"][0]["label"].startswith("A") and bills["options"][0]["value"] == pytest.approx(6000)
    assert all("공제" not in name for o in bills["options"] for name, _, _ in o["parts"])
    assert all(o["label"] != "온누리상품권" for o in bills["options"])                  # 공과금엔 온누리를 못 쓴다


def test_check_card_beats_plain_credit_once_credit_passes_threshold():
    s = state(base={"credit": 1500 * MAN, "debit": 0, "culture": 0, "market": 0, "transit": 0})
    s["cards"] = [{"id": 1, "name": "신카", "kind": "신용", "base": .5, "tier": 0, "benefit": 0, "bonus": {}, "prev_ok": True},
                  {"id": 2, "name": "체카", "kind": "체크", "base": .2, "tier": 0, "benefit": 0, "bonus": {}, "prev_ok": True}]
    r = sp.recommend(s, "마트", 100_000, DAY)
    assert r["options"][0]["label"].startswith("체카") and r["advice"][0] == "체크카드 구간"
    # 신용카드가 문턱에 못 미치면 신용·체크 공제가 같아 카드 혜택이 큰 쪽이 이긴다
    s["profile"]["base"]["credit"] = 300 * MAN
    s["profile"]["base"]["debit"] = 1200 * MAN
    r = sp.recommend(s, "마트", 100_000, DAY)
    assert r["options"][0]["label"].startswith("신카") and r["advice"][0] == "신용카드 구간"


def test_card_tier_uses_records_and_fills():
    s = state()
    s["cards"] = [{"id": 1, "name": "실적카드", "kind": "신용", "base": 0, "tier": 300_000, "benefit": 15_000,
                   "bonus": {"외식": 10}, "prev_ok": True}]
    assert sp.add_record(s, {"date": "2026-10-05", "amount": "100000", "place": "외식", "method": "c:1"}, DAY) is None
    st = sp.card_status(s, s["cards"][0], DAY)
    assert st["prev_ok"] is False and st["left"] == 300_000                               # 지난달 10만 < 30만
    r = sp.recommend(s, "외식", 50_000, DAY)
    card = next(o for o in r["options"] if o["method"] == "c:1")
    assert "못 채워" in card["note"] and dict((n, v) for n, v, _ in card["parts"])["실적 채우기"] == pytest.approx(2500)


def test_voucher_record_moves_balance_and_delete_restores():
    s = state()
    s["vouchers"] = [{"id": 7, "kind": "지역사랑", "name": "동백전", "balance": 50_000, "exp": "2027-01-31"}]
    assert sp.add_record(s, {"date": "2026-11-20", "amount": "30,000", "place": "동네", "method": "v:7"}, DAY) is None
    assert s["vouchers"][0]["balance"] == 20_000 and sp.ytd(s, DAY)["debit"] == 30_000
    assert sp.add_record(s, {"amount": "0", "place": "동네"}, DAY) and sp.add_record(s, {"amount": "1", "place": "동네", "method": "v:99"}, DAY)
    assert sp.delete_record(s, s["records"][0]["id"]) and s["vouchers"][0]["balance"] == 50_000


def test_wallet_form_parses_rows_and_drops_blank_or_deleted():
    s = sp.empty()
    f = {"salary": "5,000", "children": "1", "base_credit": "800", "onnuri": "7", "local": "10",
         "c0_id": "", "c0_name": "신카", "c0_kind": "신용", "c0_base": "0.7", "c0_tier": "30", "c0_benefit": "15000",
         "c0_bonus": "마트 5, 교통 10%, 없는곳 9", "c0_prev": "on",
         "c1_id": "", "c1_name": "", "v0_id": "", "v0_name": "온누리", "v0_kind": "온누리", "v0_balance": "100000",
         "v0_exp": "2027-02-01", "v1_id": "", "v1_name": "버릴 것", "v1_kind": "민간", "v1_del": "on"}
    s = sp.wallet_from_form(s, f, DAY)
    assert s["profile"]["salary"] == 5000 * MAN and s["profile"]["base"]["credit"] == 800 * MAN
    assert len(s["cards"]) == 1 and s["cards"][0]["tier"] == 300_000 and s["cards"][0]["bonus"] == {"마트": 5, "교통": 10}
    assert [v["name"] for v in s["vouchers"]] == ["온누리"]
    assert sp.load(sp.dump(s)) == s and sp.load("not json") == sp.empty()


def test_monthly_cap_limits_bonus_by_last_month_tier():
    s = state()
    s["cards"] = [{"id": 1, "name": "한도카드", "kind": "체크", "base": 0, "tier": 200_000, "benefit": 0,
                   "bonus": {"편의점": 10}, "caps": [[200_000, 5_000], [400_000, 10_000]], "fixed": False,
                   "prev_ok": True, "month_used": 450_000, "month": "2026-10"}]          # 10월에 앱 숫자로 45만 → 11월 한도 1만
    st = sp.card_status(s, s["cards"][0], DAY)
    assert st["prev_amt"] == 450_000 and st["cap"] == 10_000 and st["cap_left"] == 10_000
    sp.add_record(s, {"date": "2026-11-10", "amount": "80000", "place": "편의점", "method": "c:1"}, DAY)   # 8천 받음
    r = sp.recommend(s, "편의점", 50_000, DAY)
    card = next(o for o in r["options"] if o["method"] == "c:1")
    assert card["parts"][0][1] == pytest.approx(2_000) and "남은 한도" in card["parts"][0][2]


def test_fixed_card_values_reaching_next_tier():
    s = state()
    s["cards"] = [{"id": 1, "name": "통신카드", "kind": "신용", "base": 0, "tier": 0, "benefit": 0, "bonus": {},
                   "caps": [[300_000, 10_000], [700_000, 15_000]], "fixed": True, "prev_ok": True,
                   "month_used": 250_000, "month": "2026-11"}]
    st = sp.card_status(s, s["cards"][0], DAY)
    assert st["used"] == 250_000 and st["next_need"] == 300_000 and st["cap_left"] is None
    fill, why = sp.fill_value(s["cards"][0], st, 100_000)
    assert fill == pytest.approx(10_000 * 50_000 / 300_000) and "다음 달 할인" in why     # 남은 5만만 쳐 준다


def test_month_used_from_form_rolls_into_last_month():
    s = state()
    f = {"salary": "5000", "c0_id": "", "c0_name": "카드", "c0_kind": "체크", "c0_tier": "20", "c0_month_used": "798,110"}
    s = sp.wallet_from_form(s, f, DAY)
    card = s["cards"][0]
    assert card["month_used"] == 798_110 and card["month"] == "2026-11"
    dec = dt.date(2026, 12, 3)
    st = sp.card_status(s, card, dec)
    assert st["prev_amt"] == 798_110 and st["prev_ok"] and st["used"] == 0
    f2 = {"salary": "5000", "c0_id": str(card["id"]), "c0_name": "카드", "c0_kind": "체크", "c0_tier": "20", "c0_month_used": ""}
    assert sp.wallet_from_form(s, f2, dec)["cards"][0]["month"] == "2026-11"            # 비워 저장해도 지난달 값은 남는다


def test_catalog_rules_match_shop_names_and_caps():
    from bkkstock.analysis.cards import CATALOG, PRESET
    assert all(c["checked"] and c["src"] for c in CATALOG)
    s = state()
    assert sp.import_presets(s, ["kb-nori2-play", "kb-nori-rf", "없는카드"], DAY) == 2
    assert sp.import_presets(s, ["kb-nori2-play"], DAY) == 0                               # 이미 있으면 건너뜀
    play, old = s["cards"]
    play.update(month_used=500_000, month="2026-10")                                        # 10월 50만 → 11월 한도 3만
    old.update(month_used=250_000, month="2026-10")                                         # 10월 25만 → 1만, 30만 조건 혜택 없음
    r = sp.recommend(s, "외식", 15_000, DAY, "스타벅스 강남점")
    by = {o["method"]: o for o in r["options"]}
    assert by[f"c:{old['id']}"]["parts"][0][1] == pytest.approx(3_000)                     # 20% (건당 2만까지)
    assert by[f"c:{play['id']}"]["parts"][0][1] == pytest.approx(1_500)                    # 10%, 항목 한도 3천 안
    assert r["options"][0]["method"] == f"c:{old['id']}"
    assert sp.recommend(s, "외식", 15_000, DAY)["hint"]                                    # 가게 이름이 없으면 안내
    for _ in range(2):                                                                     # CGV 건당 4천 원은 월 2회까지
        sp.add_record(s, {"date": "2026-11-05", "amount": "15000", "place": "문화", "memo": "CGV 왕십리", "method": f"c:{play['id']}"}, DAY)
    st = sp.card_status(s, play, DAY)
    assert st["got"] == pytest.approx(8_000) and st["cap_left"] == pytest.approx(22_000)
    disc, _ = sp.card_discount(play, st, "문화", 15_000, "CGV")
    assert disc == 0                                                                       # 3번째는 할인 없음
    transit = sp.recommend(s, "교통", 10_000, DAY)
    assert all(o["parts"][0][0] != "카드 할인·적립" for o in transit["options"] if o["method"] == f"c:{old['id']}")   # 25만 < 30만


def test_fixed_telco_card_from_catalog_pushes_to_tier():
    s = state()
    sp.import_presets(s, ["kb-kt-superdc7"], DAY)
    c = s["cards"][0]
    r = sp.recommend(s, "마트", 100_000, DAY)
    fill = dict((n, v) for n, v, _ in r["options"][0]["parts"])["실적 채우기"]
    assert r["options"][0]["method"] == f"c:{c['id']}" and fill == pytest.approx(15_000 * 100_000 / 300_000)


def test_tiered_rule_rate_and_cap():
    s = state()
    sp.import_presets(s, ["kbank-kt-double"], DAY)
    c = s["cards"][0]
    c.update(month_used=600_000, month="2026-10")                                          # 50만 구간: KT 요금 10%, 한도 1만
    st = sp.card_status(s, c, DAY)
    assert sp.card_discount(c, st, "고정", 80_000, "KT 휴대폰 요금")[0] == pytest.approx(8_000)
    assert sp.card_discount(c, st, "고정", 150_000, "KT")[0] == pytest.approx(10_000)
    c.update(month_used=250_000)                                                            # 20만 구간: 5%, 한도 5천
    st = sp.card_status(s, c, DAY)
    assert sp.card_discount(c, st, "고정", 80_000, "KT")[0] == pytest.approx(4_000)


def test_skt_grade_and_kt_conditions():
    s = state()
    sp.import_presets(s, ["hana-sk-family", "kb-kt-superdc7"], DAY)
    sk, dc7 = s["cards"]
    pizza = lambda: next(o for o in sp.recommend(s, "외식", 40_000, DAY, "도미노피자")["options"] if o["method"] == f"c:{sk['id']}")
    assert pizza()["parts"][0][1] == pytest.approx(40_000 * .0027)                       # SKT 멤버십 없음 → 기본 적립만
    s["profile"]["skt"] = "VIP"
    assert pizza()["parts"][0][1] == pytest.approx(8_000 + 40_000 * .0027)               # VIP: 카드 20% 더
    for d in ("2026-11-02", "2026-11-09", "2026-11-16"):                                 # 미스터피자 45% 세 번 → 외식 통합 3만 원
        sp.add_record(s, {"date": d, "amount": "40000", "place": "외식", "memo": "미스터피자", "method": f"c:{sk['id']}"}, DAY)
    st = sp.card_status(s, sk, DAY)
    assert st["got"] == pytest.approx(30_000) and sp.card_discount(sk, st, "외식", 40_000, "도미노")[0] == pytest.approx(108)
    assert sp.card_status(s, dc7, DAY)["fixed_amt"] == 15_000
    s["profile"]["kt"] = False                                                            # KT 요금이 없으면 DC7 혜택 없음
    assert sp.card_status(s, dc7, DAY)["fixed_amt"] is None
    assert sp.fill_value(dc7, sp.card_status(s, dc7, DAY), 100_000)[0] == 0
    form = {"salary": "5000", "skt": "VIP"}                                               # KT 칸을 끄고 저장
    assert sp.wallet_from_form(s, form, DAY)["profile"]["kt"] == "" and s["profile"]["skt"] == "VIP"


def test_membership_perks_pick_the_bigger_carrier_and_kt_silver_cashback():
    s = state()
    s["profile"].update(skt="VIP", kt="실버")
    perks = sp.perks_for(s, "외식", "도미노피자 신촌", 30_000)
    by = {k["program"]: k for k in perks}
    assert by["SKT 멤버십"]["won"] == pytest.approx(9_000) and "더 큼" in by["SKT 멤버십"]["text"]
    assert by["KT 멤버십"]["won"] == pytest.approx(4_500) and "하나만" in by["KT 멤버십"]["text"]
    assert sp.perks_for(s, "외식", "", 30_000) == []                                    # 가게 이름이 없으면 없음
    sp.import_presets(s, ["kbank-kt-double"], DAY)
    c = s["cards"][0]
    c.update(month_used=600_000, month="2026-10")                                          # 50만 구간: 영화외식 묶음 1만
    st = sp.card_status(s, c, DAY)
    assert sp.card_discount(c, st, "외식", 30_000, "파리바게뜨")[0] == pytest.approx(1_500)   # 실버 5%
    assert sp.card_discount(c, st, "문화", 15_000, "메가박스")[0] == pytest.approx(6_000)
    s["profile"]["kt"] = "VIP"
    assert sp.card_discount(c, sp.card_status(s, c, DAY), "외식", 30_000, "파리바게뜨")[0] == pytest.approx(3_000)


def test_point_perks_earn_and_use():
    s = state()
    s["profile"].update(skt="VIP", kt="")
    s["points"] = [{"id": 1, "kind": "해피포인트", "balance": 3_200, "exp": "2027-01-10"},
                   {"id": 2, "kind": "OK캐쉬백", "balance": 800, "exp": ""}]
    perks = sp.perks_for(s, "외식", "파리바게뜨", 10_000)
    kinds = {(k["program"], k["kind"]) for k in perks}
    assert ("SKT 멤버십", "할인") in kinds and ("해피포인트", "적립") in kinds and ("해피포인트", "사용") in kinds
    assert next(k for k in perks if k["kind"] == "사용")["won"] == 3_200 and "3,200점" in next(k for k in perks if k["kind"] == "사용")["text"]
    assert not [k for k in sp.perks_for(s, "편의점", "GS25", 5_000) if k["kind"] == "사용"]   # OK캐쉬백 800점 < 1천 점
    rows = sp.point_rows(s, DAY)
    assert rows[0]["warn"] == ["소멸 47일 전"] and rows[1]["warn"] == []
