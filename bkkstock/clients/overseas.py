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
# 📊 지표용 (내일 예측가와 따로 — 이 중 하나가 막혀도 예측가는 영향 없음)
MACRO_SYMBOLS = {
    "^TNX": "미국채 10년",
    "^VIX": "VIX (미국 변동성)",
    "DX-Y.NYB": "달러인덱스",
}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"


class OverseasClient:
    api = "overseas"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(20)

    def history(self, symbol: str, range_: str = "1y", api: str | None = None) -> list[dict]:
        """[{date: 현지 거래일(YYYY-MM-DD), open, close}] 오래된 순."""
        api = api or self.api
        try:
            r = self.http.get(URL.format(sym=symbol), params={"range": range_, "interval": "1d"},
                              headers={"User-Agent": "Mozilla/5.0"})
            body = r.json()
            res = body["chart"]["result"][0]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            self.store.api_mark(api, False, f"{symbol}: {e}")
            raise ApiError(api, "fetch", f"{symbol} 시세를 못 받았습니다: {e}") from e
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
            self.store.api_mark(api, False, f"{symbol}: 빈 응답")
            raise ApiError(api, "empty", f"{symbol} 시세가 비어 있습니다")
        self.store.api_mark(api, True)
        return out

    def all_history(self, range_: str = "1y") -> list[dict]:
        """기호 하나가 막혀도 나머지는 받는다. 전부 실패할 때만 오류."""
        rows, last_err = [], None
        for sym in SYMBOLS:
            try:
                for r in self.history(sym, range_):
                    rows.append(dict(r, symbol=sym))
            except ApiError as e:
                last_err = e
        if not rows and last_err:
            raise last_err
        return rows

    def macro_history(self, range_: str = "1y") -> tuple[list[dict], list[str]]:
        """지표용 기호. (행, 실패한 기호). 상태는 'overseas_macro' 로 따로 남긴다."""
        rows, failed = [], []
        for sym in MACRO_SYMBOLS:
            try:
                for r in self.history(sym, range_, api="overseas_macro"):
                    rows.append(dict(r, symbol=sym))
            except ApiError:
                failed.append(sym)
        return rows, failed

    def test(self) -> str:
        rows = self.history("EWY", "5d")
        last = rows[-1]
        return f"EWY {last['date']} 종가 {last['close']:.2f}달러"
