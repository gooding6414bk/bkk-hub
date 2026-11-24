"""종목 재무·배당 (OpenDART) — 2026-11 주식 메뉴 1차."""
import datetime as dt

import httpx
import pytest

from bkkstock.analysis import fundamentals as fm
from bkkstock.jobs.common import make_ctx

ACC = [  # 주요계정 응답(연결·별도 둘 다 옴)
    {"fs_div": "CFS", "account_nm": "매출액", "bfefrmtrm_amount": "1,000,000,000,000", "frmtrm_amount": "1,100,000,000,000",
     "thstrm_amount": "1,210,000,000,000", "thstrm_nm": "제10기", "frmtrm_nm": "제9기", "bfefrmtrm_nm": "제8기"},
    {"fs_div": "CFS", "account_nm": "영업이익", "bfefrmtrm_amount": "50,000,000,000", "frmtrm_amount": "60,000,000,000", "thstrm_amount": "121,000,000,000"},
    {"fs_div": "CFS", "account_nm": "당기순이익(손실)", "bfefrmtrm_amount": "30,000,000,000", "frmtrm_amount": "40,000,000,000", "thstrm_amount": "-10,000,000,000"},
    {"fs_div": "CFS", "account_nm": "부채총계", "bfefrmtrm_amount": "1", "frmtrm_amount": "1", "thstrm_amount": "900,000,000,000"},
    {"fs_div": "CFS", "account_nm": "자본총계", "bfefrmtrm_amount": "1", "frmtrm_amount": "1", "thstrm_amount": "300,000,000,000"},
    {"fs_div": "OFS", "account_nm": "매출액", "thstrm_amount": "5"},
]
QTR = [{"fs_div": "CFS", "account_nm": "매출액", "thstrm_amount": "1", "thstrm_add_amount": "990,000,000,000",
        "frmtrm_add_amount": "900,000,000,000"}]
DIV = [{"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,000", "frmtrm": "900", "lwfr": "800"},
       {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "1,050", "frmtrm": "950", "lwfr": "850"},
       {"se": "(연결)현금배당성향(%)", "stock_knd": "", "thstrm": "-", "frmtrm": "25.5", "lwfr": "30.1"},
       {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "2.1", "frmtrm": "1.9", "lwfr": "1.8"}]


def test_build_metrics_and_warnings():
    acc = fm.parse_accounts(ACC)
    assert acc["fs"] == "CFS" and acc["values"]["sales"][2] == 1.21e12
    out = fm.build(acc, 2025, None, fm.parse_dividends(DIV))
    last = out["years"][-1]
    assert last["margin"] == pytest.approx(10) and last["debt"] == pytest.approx(300)
    assert "매출 2년 +10%/년" in out["summary"] and "부채비율 300%" in out["summary"]
    assert any("200% 넘음" in w for w in out["warns"]) and any("순손실" in w for w in out["warns"])
    assert [d["dps"] for d in out["div"]] == [800, 900, 1000]            # 보통주만
    assert out["div"][2]["payout"] is None and out["div"][1]["payout"] == 25.5


def _dart_ctx(settings, years_with_report=(2025,)):
    settings.values["DART_API_KEY"] = "K" * 40
    ctx = make_ctx(settings)
    calls = []

    def handler(req):
        p, q = req.url.path, dict(req.url.params)
        calls.append((p.rsplit("/", 1)[-1], q.get("bsns_year"), q.get("reprt_code")))
        if p.endswith("fnlttSinglAcnt.json"):
            if q["reprt_code"] == "11011" and int(q["bsns_year"]) in years_with_report:
                return httpx.Response(200, json={"status": "000", "list": ACC})
            if q["reprt_code"] == "11014" and q["bsns_year"] == "2026":
                return httpx.Response(200, json={"status": "000", "list": QTR})
            return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})
        if p.endswith("alotMatter.json"):
            return httpx.Response(200, json={"status": "000", "list": DIV})
        raise AssertionError(p)
    ctx.dart.http = httpx.Client(transport=httpx.MockTransport(handler))
    with ctx.store.db() as con:
        con.execute("INSERT INTO disclosures(rcept_no, day, corp_code, corp_name, stock_code, report_nm, rcept_dt, first_seen_at, tags) "
                    "VALUES ('1','2026-11-01','00126380','가','000001','x','20261101','2026-11-01','')")
    return ctx, calls


def test_fetch_uses_latest_annual_report_quarter_and_caches(settings):
    ctx, calls = _dart_ctx(settings)
    today = dt.date(2026, 11, 24)
    out = fm.get(ctx, "000001", today)
    assert out["years"][-1]["year"] == 2025 and out["ytd"]["label"] == "2026년 3분기 누적"
    assert out["ytd"]["sales"] == [9e11, 9.9e11]
    n = len(calls)
    fm.get(ctx, "000001", today)
    assert len(calls) == n                                                  # 7일 캐시
    assert fm.get(ctx, "000001", today, force=True) and len(calls) > n


def test_fetch_falls_back_a_year_when_annual_not_filed(settings):
    ctx, _ = _dart_ctx(settings, years_with_report=(2024,))
    out = fm.get(ctx, "000001", dt.date(2026, 2, 10))
    assert out["years"][-1]["year"] == 2024


def test_no_dart_key(settings):
    ctx = make_ctx(settings)
    assert "OpenDART" in fm.get(ctx, "000001", dt.date(2026, 11, 24))["error"]


def test_financial_company_gets_no_debt_warning():
    rows = [r for r in ACC if r["account_nm"] != "매출액"]
    rows = [dict(r, thstrm_amount="3,900,000,000,000") if r["account_nm"] == "부채총계" else r for r in rows]
    out = fm.build(fm.parse_accounts(rows), 2025, None, {})
    assert out["financial"] and not any("200%" in w for w in out["warns"])
