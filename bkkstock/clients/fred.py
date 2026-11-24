"""미국 연준 경제자료(FRED) — 기준금리·미국채 2년·장단기 금리차·물가·근원물가·실업률.

키 없이 받는 공개 그래프 다운로드(fredgraph.csv)를 쓴다. 사람이 보는 FRED 그래프의 'CSV 내려받기'와 같은 주소.
막히면 📊 지표의 미국 칸만 비고 다른 수집에는 영향이 없다. 조회만 한다.
"""

from __future__ import annotations

import datetime as dt

import httpx

from ..config import Settings
from ..store import Store
from .base import ApiError

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED 계열 → (이름, 주기). 2026-11-24 확인.
SERIES = {
    "DFEDTARU": ("us_fed", "D"),       # 연방기금금리 목표 상단
    "DGS2": ("us_2y", "D"),            # 미국채 2년
    "T10Y2Y": ("us_spread", "D"),      # 10년 − 2년
    "CPIAUCSL": ("us_cpi", "M"),       # 소비자물가지수
    "CPILFESL": ("us_core_cpi", "M"),  # 근원 소비자물가지수 (식품·에너지 제외)
    "UNRATE": ("us_unrate", "M"),      # 실업률
}


class FredClient:
    api = "fred"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        # 보드 공통 User-Agent 를 FRED 가 막는다 (2026-11 확인) — 기본 httpx 이름으로 받는다
        self.http = http or httpx.Client(timeout=30, follow_redirects=True)

    def history(self, series_id: str, start: dt.date) -> list[dict]:
        """[{period: 'YYYY-MM-DD', value}] 오래된 순. 빈 값('.')은 건너뛴다."""
        try:
            r = self.http.get(URL, params={"id": series_id, "cosd": start.isoformat()})
            text = r.text
        except httpx.HTTPError as e:
            self.store.api_mark(self.api, False, f"{series_id}: {e}")
            raise ApiError(self.api, "network", f"{series_id}: {e}", retryable=True) from e
        lines = text.strip().splitlines()
        if r.status_code != 200 or not lines or "," not in lines[0]:
            self.store.api_mark(self.api, False, f"{series_id}: 응답 {r.status_code}")
            raise ApiError(self.api, str(r.status_code), f"{series_id} 자료를 못 받았습니다")
        out = []
        for line in lines[1:]:
            d, _, v = line.partition(",")
            try:
                out.append({"period": dt.date.fromisoformat(d.strip()).isoformat(), "value": float(v)})
            except ValueError:
                continue
        if not out:
            self.store.api_mark(self.api, False, f"{series_id}: 빈 응답")
            raise ApiError(self.api, "empty", f"{series_id} 자료가 비어 있습니다")
        self.store.api_mark(self.api, True)
        return out

    def test(self) -> str:
        rows = self.history("DFEDTARU", dt.date.today() - dt.timedelta(days=20))
        return f"미국 기준금리(상단) {rows[-1]['value']:.2f}% ({rows[-1]['period']})"
