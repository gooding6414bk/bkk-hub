"""밤사이 해외 시장: 미국 상장 한국 ETF(EWY), 필라델피아 반도체(^SOX), 나스닥(^IXIC), 원/달러(KRW=X).

야후 파이낸스 차트 경로(비공식, 키 불필요)를 쓴다. 막히면 내일 예측가의 '해외 반영' 부분만 빠지고
변동 범위는 그대로 나온다.
"""

from __future__ import annotations

import datetime as dt

import httpx

from ..config import Settings
from ..store import Store
from .base import ApiError, make_http

SYMBOLS = {
    "EWY": "미국 상장 한국 ETF",
    "^SOX": "필라델피아 반도체",
    "^IXIC": "나스닥",
    "KRW=X": "원/달러",
}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"


class OverseasClient:
    api = "overseas"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(20)

    def history(self, symbol: str, range_: str = "1y") -> list[dict]:
        """[{date: 현지 거래일(YYYY-MM-DD), open, close}] 오래된 순."""
        try:
            r = self.http.get(URL.format(sym=symbol), params={"range": range_, "interval": "1d"},
                              headers={"User-Agent": "Mozilla/5.0"})
            body = r.json()
            res = body["chart"]["result"][0]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            self.store.api_mark(self.api, False, f"{symbol}: {e}")
            raise ApiError(self.api, "fetch", f"{symbol} 시세를 못 받았습니다: {e}") from e
        offset = int(res.get("meta", {}).get("gmtoffset") or 0)
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        out = []
        for i, ts in enumerate(res.get("timestamp") or []):
            close = (q.get("close") or [None])[i]
            if close is None:
                continue
            d = dt.datetime.fromtimestamp(ts + offset, tz=dt.timezone.utc).date()
            out.append({"date": d.isoformat(), "open": (q.get("open") or [None])[i], "close": float(close)})
        if not out:
            self.store.api_mark(self.api, False, f"{symbol}: 빈 응답")
            raise ApiError(self.api, "empty", f"{symbol} 시세가 비어 있습니다")
        self.store.api_mark(self.api, True)
        return out

    def all_history(self, range_: str = "1y") -> list[dict]:
        rows = []
        for sym in SYMBOLS:
            for r in self.history(sym, range_):
                rows.append(dict(r, symbol=sym))
        return rows

    def test(self) -> str:
        rows = self.history("EWY", "5d")
        last = rows[-1]
        return f"EWY {last['date']} 종가 {last['close']:.2f}달러"
