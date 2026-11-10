"""실제 DART 공시 제목으로 분류·재료 판정을 고정한다 (2026-11 내부 점검에서 찾은 오탐 포함)."""

import datetime as dt

import pandas as pd
import pytest

from bkkstock.analysis.flags import disclosure_flags, horse_flags, is_material
from bkkstock.analysis.history import adjust_prices
from bkkstock.clients.dart import classify

CASES = [
    # (제목, 들어가야 할 분류, 들어가면 안 되는 분류)
    ("주권매매거래정지해제(액면병합 주권 변경상장)", set(), {"부실"}),
    ("주권매매거래정지(액면분할)", set(), {"부실"}),
    ("주권매매거래정지(조회공시 요구)", set(), {"부실"}),
    ("주권매매거래정지(상장적격성 실질심사 사유 발생)", {"부실"}, set()),
    ("감사의견거절", {"부실"}, set()),
    ("감사보고서제출(감사의견 적정)", set(), {"부실"}),
    ("감사의견 비적정설 조회공시", {"부실"}, set()),
    ("관리종목지정해제", set(), {"부실"}),
    ("상장폐지결정", {"부실"}, set()),
    ("회생절차개시신청", {"부실"}, set()),
    ("단일판매ㆍ공급계약체결", {"수주"}, {"계약해지"}),
    ("단일판매ㆍ공급계약해지", {"계약해지"}, {"수주"}),
    ("추가상장(무상증자)", set(), {"희석"}),
    ("추가상장(주식매수선택권행사)", set(), {"희석"}),
    ("추가상장(전환청구권행사)", {"희석"}, set()),
    ("유상증자결정(제3자배정)", {"희석"}, set()),
    ("유상증자결정(철회)", set(), {"희석"}),
    ("주요사항보고서(전환사채권발행결정)", {"희석"}, set()),
    ("무상증자결정", set(), {"희석"}),
    ("[기재정정]단일판매ㆍ공급계약체결", {"정정", "수주"}, set()),
    ("최대주주변경", {"매도압력"}, set()),
    ("소송등의제기ㆍ신청(일정금액이상의청구)", {"소송"}, set()),
    ("연결재무제표기준영업(잠정)실적(공정공시)", {"실적"}, set()),
    ("주식등의대량보유상황보고서(일반)", set(), {"희석", "부실", "매도압력"}),
    # 반대자 재점검(c2에서도 틀리던 것)
    ("불성실공시법인미지정(공시번복ㆍ공시불이행)", set(), {"부실"}),
    ("주권매매거래정지(주식의병합,분할등전자등록변경,말소)", set(), {"부실"}),
    ("전환사채(해외전환사채포함)만기전사채취득", set(), {"희석"}),
    ("유상증자결정(종속회사의주요경영사항)", set(), {"희석"}),
    ("최대주주변경을수반하는주식양수도계약해제ㆍ취소등", set(), {"매도압력"}),
    ("매출액또는손익구조30%(대규모법인은15%)이상변경", set(), {"실적"}),
    ("자기주식처분결정", {"매도압력"}, set()),
    ("거래처와의거래중단", {"영업위험"}, set()),
    ("중대재해발생", {"영업위험"}, set()),
    ("의무보유해제(보호예수해제)", {"매도압력"}, set()),
    # c4 (B 담당 반박 답변 표)
    ("주요사항보고서(해산사유발생)", {"부실"}, set()),
    ("자산유동화관련중요사항발생등보고서(해산)", set(), {"부실"}),
    ("[기재정정]회생계획인가", {"부실"}, set()),
    ("기타시장안내 (개선계획서 제출)", {"부실"}, set()),
    ("기타시장안내 (기업심사위원회 개최 결과 안내)", {"부실"}, set()),
    ("기타시장안내 (실질심사 대상여부 결정을 위한 조사기간 연장 안내)", {"부실"}, set()),
    ("기타시장안내 (대학수학능력시험일(11.19) 거래시간 임시 변경)", set(), {"부실"}),
    ("증권발행결과(자율공시) (제19회차 CB)", {"희석"}, set()),
    ("[발행조건확정]증권신고서(지분증권)", {"희석"}, set()),
    ("경영권변경등에관한계약체결", {"매도압력"}, set()),
    ("주요사항보고서(자기주식취득신탁계약체결결정)", {"주주환원"}, set()),
    ("주요사항보고서(자기주식취득신탁계약해지결정)", set(), {"주주환원"}),
    ("타인에대한담보제공결정", set(), {"영업위험"}),
    ("중대재해발생(자회사의 주요경영사항)", set(), {"영업위험"}),
    ("주권매매거래정지", set(), {"부실"}),
    ("주권매매거래정지기간변경 (투자자 보호)", {"부실"}, set()),
    ("주권매매거래정지(감사의견 의견거절)", {"부실"}, set()),
    ("감사의견비적정설에대한조회공시요구", {"부실"}, set()),
    ("주요사항보고서(자기전환사채매도결정)", {"희석"}, set()),
    ("투자판단관련주요경영사항 (단일판매ㆍ공급계약 해지)", {"계약해지"}, {"수주"}),
    ("기타시장안내(시가총액 미달 우려 종목 안내)", {"상폐우려"}, {"부실"}),
]


@pytest.mark.parametrize("title,must,must_not", CASES)
def test_classify_real_titles(title, must, must_not):
    tags = set(classify(title))
    assert must <= tags, (title, tags)
    assert not (must_not & tags), (title, tags)


@pytest.mark.parametrize("title,material", [
    ("조회공시요구(현저한시황변동)", False),
    ("주식등의대량보유상황보고서(일반)", False),
    ("임원ㆍ주요주주특정증권등소유상황보고서", False),
    ("기업설명회(IR)개최(안내공시)", False),
    ("투자주의종목지정(소수계좌거래집중)", False),
    ("현저한시황변동에대한조회공시요구(풍문또는보도)에대한답변(미확정)", False),
    ("단일판매ㆍ공급계약체결", True),
    ("연결재무제표기준영업(잠정)실적(공정공시)", True),
    ("투자판단관련주요경영사항(임상시험계획승인)", True),
    ("[기재정정]단일판매ㆍ공급계약체결", False),
])
def test_material_whitelist(title, material):
    assert is_material({"report_nm": title, "tags": ",".join(classify(title))}) is material


def _hist(closes, chg):
    days = pd.bdate_range("2026-09-01", periods=len(closes)).date
    return pd.DataFrame({"date": days, "code": "000001", "close": closes, "open": closes, "high": closes,
                         "low": closes, "change_pct": chg, "value": [1e9] * len(closes), "total_value": [1e9] * len(closes)})


def test_reverse_split_is_not_a_horse():
    # 10:1 액면병합: 원 종가 500 → 5,100 (KRX 등락률은 기준가 5,000 대비 +2%)
    closes = [500] * 25 + [5100, 5150]
    chg = [0.0] * 25 + [2.0, 0.98]
    adj = adjust_prices(_hist(closes, chg))
    rets = adj["close"].pct_change().dropna()
    assert rets.abs().max() < 0.05
    assert adj["raw_close"].iloc[-2] == 5100 and abs(adj["close"].iloc[0] - 5000) < 1
    today = adj["date"].iloc[-1]
    assert horse_flags(adj, [], today) == []


def test_real_surge_without_material_still_blocked():
    closes = [1000] * 25 + [1300]
    chg = [0.0] * 25 + [30.0]
    adj = adjust_prices(_hist(closes, chg))
    flags = horse_flags(adj, [{"rcept_dt": adj["date"].iloc[-1].strftime("%Y%m%d"),
                               "report_nm": "조회공시요구(현저한시황변동)", "tags": ""}], adj["date"].iloc[-1])
    assert flags and flags[0].level == "block" and flags[0].kind == "경주마"


def test_contract_cancel_is_warned():
    today = dt.date(2026, 11, 10)
    fl = disclosure_flags([{"rcept_dt": "20261104", "report_nm": "단일판매ㆍ공급계약해지", "tags": "계약해지"}], today)
    assert fl and fl[0].kind == "계약해지" and fl[0].level == "warn"


def test_retag_on_version_change(store):
    with store.db() as con:
        con.execute("INSERT INTO disclosures(rcept_no, day, report_nm, rcept_dt, first_seen_at, tags) VALUES (?,?,?,?,?,?)",
                    ("1", "20261105", "주권매매거래정지해제(액면병합 주권 변경상장)", "20261105", "2026-11-05T08:00:00+09:00", "부실"))
    assert store.retag_disclosures(classify, "test-v") == 1
    assert store.query("SELECT tags, first_seen_at FROM disclosures")[0] == {"tags": "", "first_seen_at": "2026-11-05T08:00:00+09:00"}
    assert store.retag_disclosures(classify, "test-v") == 0


def test_market_wide_rally_is_not_a_horse():
    # 시장 전체가 +12% 오른 날 같이 +16% 오른 종목은 '재료 없는 급등'이 아니다
    closes = [1000] * 25 + [1160]
    chg = [0.0] * 25 + [16.0]
    adj = adjust_prices(_hist(closes, chg))
    day = adj["date"].iloc[-1]
    assert horse_flags(adj, [], day, {day: 0.12}) == []
    assert horse_flags(adj, [], day, {day: 0.0})[0].kind == "경주마"


def test_backfill_day_does_not_use_future_universe(store):
    from bkkstock.analysis.history import latest_universe
    store.write_clean("universe", dt.date(2026, 11, 10), pd.DataFrame([{"code": "000001", "administrative": "Y"}]))
    df, d = latest_universe(store, dt.date(2026, 9, 15))
    assert df.empty and d is None
