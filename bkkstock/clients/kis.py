"""한국투자증권 Open API (조회 전용).

주문 API는 일부러 만들지 않는다.
시장 코드: J=KRX, NX=넥스트레이드, UN=통합 (공식 예제 기준).
접근 토큰은 24시간 유효하고, 자주 재발급하면 차단되므로 파일에 캐시한다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..store import Store
from ..timeutil import KST, now_kst
from .base import ApiError, NotConfigured, RateLimiter, make_http

BASE_URL = "https://openapi.koreainvestment.com:9443"
MASTER_URLS = {
    "KOSPI": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "KOSDAQ": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
}
RATE_LIMIT_CODES = {"EGW00201"}           # 초당 거래건수 초과
TOKEN_EXPIRED_CODES = {"EGW00123", "EGW00121"}


class KisClient:
    api = "kis"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None,
                 limiter: RateLimiter | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http()
        self.limiter = limiter or RateLimiter(settings.kis_rate)
        self.token_path: Path = store.secrets_dir / "kis_token.json"

    # ---------- 토큰 ----------
    def _require(self) -> None:
        missing = [k for k in ("KIS_APP_KEY", "KIS_APP_SECRET") if not self.s.get(k)]
        if missing:
            raise NotConfigured(self.api, missing)

    def _load_token(self) -> dict | None:
        try:
            data = json.loads(self.token_path.read_text())
        except (OSError, ValueError):
            return None
        if data.get("app_key_tail") != self.s.get("KIS_APP_KEY")[-6:]:
            return None   # 키가 바뀌었으면 옛 토큰은 쓰지 않는다
        return data

    def token_info(self) -> dict | None:
        return self._load_token()

    def clear_token(self) -> None:
        try:
            self.token_path.unlink()
        except FileNotFoundError:
            pass

    def token(self, force: bool = False) -> str:
        self._require()
        cached = self._load_token()
        if cached and not force:
            exp = dt.datetime.fromisoformat(cached["expires_at"])
            if exp - now_kst() > dt.timedelta(hours=1):
                return cached["access_token"]
        if cached and time.time() - cached.get("issued_ts", 0) < 65:
            # 1분 안에 다시 발급받으면 차단될 수 있다
            if dt.datetime.fromisoformat(cached["expires_at"]) > now_kst():
                return cached["access_token"]
            raise ApiError(self.api, "token_wait", "토큰을 1분 안에 다시 받을 수 없습니다", retryable=True)
        r = self.http.post(f"{BASE_URL}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": self.s.get("KIS_APP_KEY"),
            "appsecret": self.s.get("KIS_APP_SECRET"),
        })
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or "access_token" not in body:
            msg = body.get("error_description") or body.get("msg1") or r.text[:200]
            self.store.api_mark(self.api, False, f"토큰 발급 실패: {msg}")
            raise ApiError(self.api, str(body.get("error_code", r.status_code)), f"토큰 발급 실패: {msg}")
        exp_s = body.get("access_token_token_expired")
        try:
            exp = dt.datetime.strptime(exp_s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        except (TypeError, ValueError):
            exp = now_kst() + dt.timedelta(seconds=int(body.get("expires_in", 86400)))
        data = {
            "access_token": body["access_token"],
            "expires_at": exp.isoformat(),
            "issued_ts": time.time(),
            "app_key_tail": self.s.get("KIS_APP_KEY")[-6:],
        }
        tmp = self.token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.token_path)
        self.store.api_mark(self.api, True)
        return data["access_token"]

    # ---------- 호출 ----------
    def _get(self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = "") -> tuple[dict, str]:
        token = self.token()
        retried_token = False
        for attempt in range(4):
            self.limiter.wait()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.s.get("KIS_APP_KEY"),
                "appsecret": self.s.get("KIS_APP_SECRET"),
                "tr_id": tr_id,
                "custtype": "P",
                "tr_cont": tr_cont,
            }
            try:
                r = self.http.get(f"{BASE_URL}{path}", headers=headers, params=params)
            except httpx.HTTPError as e:
                if attempt < 3:
                    time.sleep(1 + attempt)
                    continue
                self.store.api_mark(self.api, False, f"연결 오류: {e}")
                raise ApiError(self.api, "network", str(e), retryable=True) from e
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = str(body.get("msg_cd", ""))
            if body.get("rt_cd") == "0":
                self.store.api_mark(self.api, True)
                return body, r.headers.get("tr_cont", "")
            if code in RATE_LIMIT_CODES or r.status_code == 429:
                time.sleep(1.0 + attempt)
                continue
            if (code in TOKEN_EXPIRED_CODES or r.status_code == 401) and not retried_token:
                token = self.token(force=True)
                retried_token = True
                continue
            msg = body.get("msg1") or r.text[:200]
            self.store.api_mark(self.api, False, f"{tr_id} {code} {msg}")
            raise ApiError(self.api, code or str(r.status_code), msg)
        self.store.api_mark(self.api, False, f"{tr_id} 호출 제한 반복")
        raise ApiError(self.api, "rate_limit", "초당 호출 제한이 계속됩니다", retryable=True)

    # ---------- 조회 ----------
    def inquire_price(self, code: str, market: str = "J") -> dict:
        body, _ = self._get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code})
        return body.get("output") or {}

    def daily_chart(self, code: str, start: dt.date, end: dt.date, market: str = "J",
                    adjusted: bool = True) -> list[dict]:
        """일봉. 한 번에 최대 100개."""
        body, _ = self._get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100", {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
        })
        rows = body.get("output2") or []
        return [r for r in rows if r.get("stck_bsop_date")]

    def investor_daily(self, code: str, day: dt.date, market: str = "UN") -> list[dict]:
        """종목별 투자자 매매동향(일별). day 이전 여러 날이 같이 온다."""
        body, _ = self._get("/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily", "FHPTJ04160001", {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        })
        rows = body.get("output2") or []
        return [r for r in rows if r.get("stck_bsop_date")]

    def short_daily(self, code: str, start: dt.date, end: dt.date) -> list[dict]:
        body, _ = self._get("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000", {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
        })
        rows = body.get("output2") or []
        return [r for r in rows if r.get("stck_bsop_date")]

    def credit_daily(self, code: str, day: dt.date) -> list[dict]:
        body, _ = self._get("/uapi/domestic-stock/v1/quotations/daily-credit-balance", "FHPST04760000", {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20476",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
        })
        rows = body.get("output") or []
        return [r for r in rows if r.get("deal_date")]

    # ---------- 종목 마스터 (공개 파일, 키 불필요) ----------
    def download_master(self, market: str) -> bytes:
        r = self.http.get(MASTER_URLS[market], timeout=60)
        if r.status_code != 200 or not r.content.startswith(b"PK"):
            self.store.api_mark("kis_master", False, f"{market} 마스터 다운로드 실패 {r.status_code}")
            raise ApiError("kis_master", str(r.status_code), f"{market} 마스터 다운로드 실패")
        self.store.api_mark("kis_master", True)
        return r.content

    def test(self) -> str:
        self.token()
        out = self.inquire_price("005930")
        price = out.get("stck_prpr")
        if not price:
            raise ApiError(self.api, "empty", "삼성전자 현재가 응답이 비어 있습니다")
        return f"토큰 정상, 삼성전자 현재가 {int(price):,}원"


def to_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(str(v).replace(",", "")))
    except ValueError:
        return None


def to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", ""))
    except ValueError:
        return None
