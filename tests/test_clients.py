import datetime as dt
import json

import httpx
import pytest

from bkkstock.clients.base import ApiError, NotConfigured, RateLimiter
from bkkstock.clients.dart import DartClient, classify
from bkkstock.clients.kis import KisClient
from bkkstock.clients.krx import KrxClient


def kis_with(settings, store, handler):
    settings.values.update(KIS_APP_KEY="APPKEY123456", KIS_APP_SECRET="SECRET")
    return KisClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(handler)), limiter=RateLimiter(1000))


def test_kis_token_cached_and_reused(settings, store, monkeypatch):
    calls = {"token": 0, "price": 0}

    def handler(req: httpx.Request):
        if req.url.path == "/oauth2/tokenP":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "T1", "access_token_token_expired": "2099-01-01 00:00:00"})
        calls["price"] += 1
        assert req.headers["authorization"] == "Bearer T1"
        assert req.headers["tr_id"] == "FHKST01010100"
        return httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "70000"}})

    kis = kis_with(settings, store, handler)
    assert "70,000" in kis.test()
    kis2 = kis_with(settings, store, handler)    # 새 프로세스라고 가정
    kis2.inquire_price("005930")
    assert calls["token"] == 1                     # 파일 캐시 덕분에 재발급 안 함
    st = (store.secrets_dir / "kis_token.json").stat().st_mode
    assert oct(st)[-3:] == "600"


def test_kis_rate_limit_then_success(settings, store, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    seq = iter([{"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"},
                {"rt_cd": "0", "output2": [{"stck_bsop_date": "20261106", "acml_vol": "5"}]}])

    def handler(req):
        if req.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "T", "access_token_token_expired": "2099-01-01 00:00:00"})
        return httpx.Response(200, json=next(seq))

    kis = kis_with(settings, store, handler)
    rows = kis.daily_chart("005930", dt.date(2026, 11, 6), dt.date(2026, 11, 6), "NX")
    assert rows[0]["acml_vol"] == "5"


def test_kis_expired_token_refreshes_once(settings, store, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 10_000_000.0)
    tokens = iter(["OLD", "NEW"])
    state = {"n": 0}

    def handler(req):
        if req.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": next(tokens), "access_token_token_expired": "2099-01-01 00:00:00"})
        state["n"] += 1
        if req.headers["authorization"] == "Bearer OLD":
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "1"}})

    kis = kis_with(settings, store, handler)
    kis.token()
    # 1분 안의 재발급은 막히므로 발급 시각을 과거로 돌린다
    data = json.loads(kis.token_path.read_text())
    data["issued_ts"] = 0
    kis.token_path.write_text(json.dumps(data))
    assert kis.inquire_price("005930")["stck_prpr"] == "1"


def test_kis_not_configured(settings, store):
    kis = KisClient(settings, store)
    with pytest.raises(NotConfigured):
        kis.token()


def test_krx_header_then_query_fallback_is_remembered(settings, store):
    settings.values["KRX_OPENAPI_KEY"] = "K"
    seen = []

    def handler(req):
        mode = "header" if req.headers.get("AUTH_KEY") else ("query" if req.url.params.get("AUTH_KEY") else "none")
        seen.append(mode)
        if mode == "query":
            return httpx.Response(200, json={"OutBlock_1": [{"ISU_CD": "005930"}]})
        return httpx.Response(401, json={"respCode": "401", "respMsg": "Unauthorized Key"})

    krx = KrxClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert krx.daily_trades("KOSPI", dt.date(2026, 11, 6)) == [{"ISU_CD": "005930"}]
    krx.daily_trades("KOSPI", dt.date(2026, 11, 6))
    assert seen == ["header", "query", "query"]


def test_krx_unapproved_service_hint(settings, store):
    settings.values["KRX_OPENAPI_KEY"] = "K"
    krx = KrxClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"respCode": "401", "respMsg": "Unauthorized API Call"}))))
    with pytest.raises(ApiError) as e:
        krx.index_daily("KOSPI", dt.date(2026, 11, 6))
    assert "활용신청" in str(e.value)


def test_dart_pagination_stops_at_known(settings, store):
    settings.values["DART_API_KEY"] = "D"
    pages = {1: [{"rcept_no": "3"}, {"rcept_no": "2"}], 2: [{"rcept_no": "1"}, {"rcept_no": "0"}], 3: [{"rcept_no": "-1"}]}
    asked = []

    def handler(req):
        p = int(req.url.params["page_no"])
        asked.append(p)
        return httpx.Response(200, json={"status": "000", "list": pages[p], "total_page": 3})

    dart = DartClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(handler)))
    items = dart.list_all(dt.date(2026, 11, 6), dt.date(2026, 11, 6), stop_at={"1"})
    assert asked == [1, 2] and [i["rcept_no"] for i in items] == ["3", "2", "1", "0"]


def test_dart_no_data_is_empty_not_error(settings, store):
    settings.values["DART_API_KEY"] = "D"
    dart = DartClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."}))))
    assert dart.list_all(dt.date(2026, 11, 7), dt.date(2026, 11, 7)) == []


def test_dart_bad_key_is_error(settings, store):
    settings.values["DART_API_KEY"] = "D"
    dart = DartClient(settings, store, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "010", "message": "등록되지 않은 인증키입니다."}))))
    with pytest.raises(ApiError):
        dart.list_page(dt.date(2026, 11, 6), dt.date(2026, 11, 6))
    assert store.api_status()["dart"]["last_error"].startswith("010")


@pytest.mark.parametrize("name,has,hasnt", [
    ("주요사항보고서(유상증자결정)", {"희석"}, set()),
    ("[기재정정]전환가액의조정", {"희석", "정정"}, set()),
    ("주식등의대량보유상황보고서(일반)", set(), {"매도압력"}),
    ("임원ㆍ주요주주특정증권등거래계획보고서", {"매도압력"}, set()),
    ("관리종목지정(감사의견 의견거절)", {"부실"}, set()),
    ("주요사항보고서(자기주식취득결정)", {"주주환원"}, {"희석"}),
])
def test_classify(name, has, hasnt):
    tags = set(classify(name))
    assert has <= tags and not (hasnt & tags)
