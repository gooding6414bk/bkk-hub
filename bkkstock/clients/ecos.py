"""한국은행 경제통계시스템(ECOS) Open API — 기준금리·국고채·환율·소비자물가.

무료 인증키가 필요하다(ecos.bok.or.kr → Open API → 인증키 신청). 키는 주소 경로에 실리므로
오류 문구·로그에 남기 전에 redact 가 가린다. 조회만 한다.
"""

from __future__ import annotations

import datetime as dt

import httpx

from ..config import Settings
from ..redact import redact
from ..store import Store
from .base import ApiError, NotConfigured, make_http

BASE_URL = "https://ecos.bok.or.kr/api"

# 이름 → (통계표, 주기, 항목, 화면 이름, 단위). 2026-11-24 sample 키로 코드 확인.
SERIES: dict[str, tuple[str, str, str, str, str]] = {
    "kr_base": ("722Y001", "D", "0101000", "한국 기준금리", "%"),
    "kr_3y": ("817Y002", "D", "010200000", "국고채 3년", "%"),
    "kr_10y": ("817Y002", "D", "010210000", "국고채 10년", "%"),
    "usdkrw": ("731Y001", "D", "0000001", "원/달러", "원"),
    "jpykrw": ("731Y001", "D", "0000002", "원/엔 (100엔)", "원"),
    "cnykrw": ("731Y001", "D", "0000053", "원/위안", "원"),
    "kr_cpi": ("901Y009", "M", "0", "소비자물가지수", "2020=100"),
}


class EcosClient:
    api = "ecos"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(20)

    @property
    def configured(self) -> bool:
        return bool(self.s.get("ECOS_API_KEY"))

    def _key(self) -> str:
        k = self.s.get("ECOS_API_KEY")
        if not k:
            raise NotConfigured(self.api, ["ECOS_API_KEY"])
        return k

    def series(self, name: str, start: dt.date, end: dt.date) -> list[dict]:
        """[{period: 'YYYY-MM-DD' 또는 'YYYY-MM', value}] 오래된 순."""
        stat, cycle, item, _label, _unit = SERIES[name]
        if cycle == "M":
            a, b = start.strftime("%Y%m"), end.strftime("%Y%m")
        else:
            a, b = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        url = f"{BASE_URL}/StatisticSearch/{self._key()}/json/kr/1/1000/{stat}/{cycle}/{a}/{b}/{item}"
        try:
            r = self.http.get(url)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            msg = redact(f"{name}: {e}")
            self.store.api_mark(self.api, False, msg)
            raise ApiError(self.api, "network", msg, retryable=True) from e
        res = body.get("RESULT")
        if res and str(res.get("CODE", "")).startswith("INFO-200"):     # 해당 기간 자료 없음
            return []
        if res:
            msg = f"{name}: {res.get('CODE')} {res.get('MESSAGE', '')}"
            self.store.api_mark(self.api, False, msg)
            raise ApiError(self.api, str(res.get("CODE", "error")), msg)
        rows = (body.get("StatisticSearch") or {}).get("row") or []
        out = []
        for row in rows:
            t, v = str(row.get("TIME") or ""), row.get("DATA_VALUE")
            try:
                val = float(str(v).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if len(t) == 8:
                period = f"{t[:4]}-{t[4:6]}-{t[6:]}"
            elif len(t) == 6:
                period = f"{t[:4]}-{t[4:]}"
            else:
                continue
            out.append({"period": period, "value": val})
        self.store.api_mark(self.api, True)
        return out

    def test(self) -> str:
        today = dt.date.today()
        rows = self.series("kr_base", today - dt.timedelta(days=14), today)
        if not rows:
            raise ApiError(self.api, "empty", "기준금리 응답이 비어 있습니다")
        last = rows[-1]
        return f"한국 기준금리 {last['value']:.2f}% ({last['period']})"
