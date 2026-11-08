"""공공데이터포털 금융위원회_주식시세정보. 다음 영업일 13시 이후 갱신. 대조·백업용."""

from __future__ import annotations

import datetime as dt

import httpx

from ..config import Settings
from ..store import Store
from .base import ApiError, NotConfigured, make_http

URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"


class DataGoKrClient:
    api = "datagokr"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(30)

    def prices(self, day: dt.date, rows: int = 4000) -> list[dict]:
        key = self.s.get("DATA_GO_KR_KEY")
        if not key:
            raise NotConfigured(self.api, ["DATA_GO_KR_KEY"])
        r = self.http.get(URL, params={"serviceKey": key, "basDt": day.strftime("%Y%m%d"),
                                       "numOfRows": str(rows), "pageNo": "1", "resultType": "json"})
        try:
            body = r.json()
            header = body["response"]["header"]
        except (ValueError, KeyError, TypeError):
            msg = r.text[:200]
            self.store.api_mark(self.api, False, f"{r.status_code} {msg}")
            raise ApiError(self.api, str(r.status_code), msg)
        if header.get("resultCode") != "00":
            self.store.api_mark(self.api, False, f"{header.get('resultCode')} {header.get('resultMsg')}")
            raise ApiError(self.api, str(header.get("resultCode")), str(header.get("resultMsg")))
        items = (body["response"]["body"].get("items") or {}).get("item") or []
        self.store.api_mark(self.api, True)
        return items if isinstance(items, list) else [items]

    def test(self, day: dt.date) -> str:
        items = self.prices(day, 5)
        return f"{day} 응답 {len(items)}건 (전일 데이터는 다음 영업일 13시 이후 갱신)"
