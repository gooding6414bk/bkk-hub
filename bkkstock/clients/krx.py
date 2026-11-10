"""KRX Open API (openapi.krx.co.kr). 서비스마다 활용신청 승인이 따로 필요하다.

인증키는 헤더(AUTH_KEY)로 보내고, 거절되면 쿼리 파라미터로 한 번 더 시도한 뒤 어느 쪽이 통했는지 기억한다.
응답 필드 이름(BAS_DD, ISU_CD, TDD_CLSPRC ...)은 첫 주 점검에서 실제 응답으로 확인한다.
원본을 모두 저장하므로 이름이 달라도 정리 단계만 고치면 된다.
"""

from __future__ import annotations

import datetime as dt

import httpx

from ..config import Settings
from ..store import Store
from ..timeutil import prev_trading_day
from .base import ApiError, NotConfigured, make_http

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
ENDPOINTS = {
    ("trade", "KOSPI"): "sto/stk_bydd_trd",
    ("trade", "KOSDAQ"): "sto/ksq_bydd_trd",
    ("base", "KOSPI"): "sto/stk_isu_base_info",
    ("base", "KOSDAQ"): "sto/ksq_isu_base_info",
    ("index", "KOSPI"): "idx/kospi_dd_trd",
    ("index", "KOSDAQ"): "idx/kosdaq_dd_trd",
    ("index", "KRX"): "idx/krx_dd_trd",
}
SERVICE_NAMES = {
    "sto/stk_bydd_trd": "유가증권 일별매매정보",
    "sto/ksq_bydd_trd": "코스닥 일별매매정보",
    "sto/stk_isu_base_info": "유가증권 종목기본정보",
    "sto/ksq_isu_base_info": "코스닥 종목기본정보",
    "idx/kospi_dd_trd": "KOSPI 시리즈 일별시세정보",
    "idx/kosdaq_dd_trd": "KOSDAQ 시리즈 일별시세정보",
    "idx/krx_dd_trd": "KRX 시리즈 일별시세정보",
}


class KrxClient:
    api = "krx"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(30)

    def _mode(self) -> str:
        st = self.store.api_status().get(self.api) or {}
        return "query" if (st.get("note") or "") == "auth=query" else "header"

    def get(self, endpoint: str, day: dt.date) -> list[dict]:
        key = self.s.get("KRX_OPENAPI_KEY")
        if not key:
            raise NotConfigured(self.api, ["KRX_OPENAPI_KEY"])
        params = {"basDd": day.strftime("%Y%m%d")}
        modes = [self._mode()]
        modes.append("query" if modes[0] == "header" else "header")
        last_err = None
        for mode in modes:
            headers = {"AUTH_KEY": key} if mode == "header" else {}
            p = dict(params, **({"AUTH_KEY": key} if mode == "query" else {}))
            try:
                r = self.http.get(f"{BASE_URL}/{endpoint}", params=p, headers=headers)
            except httpx.HTTPError as e:
                self.store.api_mark(self.api, False, f"{endpoint} 연결 오류 {e}")
                raise ApiError(self.api, "network", str(e), retryable=True) from e
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 200 and "OutBlock_1" in body:
                self.store.api_mark(self.api, True, note=f"auth={mode}")
                return body["OutBlock_1"] or []
            last_err = (str(body.get("respCode", r.status_code)), body.get("respMsg") or r.text[:200])
            if r.status_code not in (401, 403):
                break
        code, msg = last_err or ("unknown", "")
        name = SERVICE_NAMES.get(endpoint, endpoint)
        hint = " (이 서비스의 활용신청 승인 여부를 확인하세요)" if code in ("401", "403") else ""
        self.store.api_mark(self.api, False, f"{name}: {code} {msg}{hint}")
        raise ApiError(self.api, code, f"{name}: {msg}{hint}")

    def daily_trades(self, market: str, day: dt.date) -> list[dict]:
        return self.get(ENDPOINTS[("trade", market)], day)

    def base_info(self, market: str, day: dt.date) -> list[dict]:
        return self.get(ENDPOINTS[("base", market)], day)

    def index_daily(self, family: str, day: dt.date) -> list[dict]:
        return self.get(ENDPOINTS[("index", family)], day)

    def latest_trades(self, day: dt.date, back: int = 3) -> tuple[dt.date, list[dict]]:
        """day 부터 거꾸로, 유가증권 일별매매정보가 실제로 있는 첫 거래일과 그 행.
        KRX 는 그날 시세를 다음 영업일 아침에 올리므로, 밤에 그날 날짜로 물으면 빈 목록이 온다."""
        d = day
        for _ in range(back + 1):
            rows = self.daily_trades("KOSPI", d)
            if rows:
                return d, rows
            d = prev_trading_day(d)
        return day, []

    def test(self, day: dt.date) -> str:
        d, rows = self.latest_trades(day)
        if not rows:
            raise ApiError(self.api, "empty", f"{day} 부터 최근 거래일 유가증권 일별매매정보가 비어 있습니다")
        note = "" if d == day else f" · {day} 치는 아직 안 올라옴(다음 영업일 아침)"
        return f"{d} 유가증권 {len(rows)}종목 응답{note}"


# KRX 응답 → 정리 열 이름
TRADE_COLS = {
    "BAS_DD": "date", "ISU_CD": "code", "ISU_NM": "name", "MKT_NM": "market_name",
    "SECT_TP_NM": "section", "TDD_CLSPRC": "close", "CMPPREVDD_PRC": "change", "FLUC_RT": "change_pct",
    "TDD_OPNPRC": "open", "TDD_HGPRC": "high", "TDD_LWPRC": "low", "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value", "MKTCAP": "mktcap", "LIST_SHRS": "listed_shares",
}
INDEX_COLS = {
    "BAS_DD": "date", "IDX_CLSS": "family", "IDX_NM": "name", "CLSPRC_IDX": "close",
    "CMPPREVDD_IDX": "change", "FLUC_RT": "change_pct", "OPNPRC_IDX": "open", "HGPRC_IDX": "high",
    "LWPRC_IDX": "low", "ACC_TRDVOL": "volume", "ACC_TRDVAL": "value", "MKTCAP": "mktcap",
}
