"""관심 종목 뉴스. 네이버 검색 API 키가 있으면 그것을, 없으면 구글 뉴스 RSS를 쓴다.
제목만 보고 간단한 위험·호재 단어를 표시한다. 점수에는 아주 약하게만 반영한다."""

from __future__ import annotations

import email.utils
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from ..config import Settings
from ..store import Store
from .base import ApiError, make_http

NEG_WORDS = ("하한가", "급락", "폭락", "유상증자", "전환사채", "CB 발행", "감자", "상장폐지", "관리종목", "거래정지",
             "횡령", "배임", "압수수색", "적자전환", "적자 전환", "어닝쇼크", "소송", "리콜", "블록딜", "오버행",
             "투자경고", "투자위험", "불성실")
POS_WORDS = ("상한가", "급등", "최대 실적", "사상 최대", "흑자전환", "흑자 전환", "어닝서프라이즈", "수주", "공급계약",
             "자사주 매입", "자사주 소각", "배당 확대", "목표가 상향")

_TAG = re.compile(r"<[^>]+>")


def tone(title: str) -> str:
    t = title or ""
    neg = any(w in t for w in NEG_WORDS)
    pos = any(w in t for w in POS_WORDS)
    if neg and not pos:
        return "neg"
    if pos and not neg:
        return "pos"
    return ""


class NewsClient:
    api = "news"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(15)

    @property
    def source(self) -> str:
        return "naver" if self.s.has("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET") else "google_rss"

    def search(self, query: str, limit: int = 15) -> list[dict]:
        if self.source == "naver":
            return self._naver(query, limit)
        return self._google(query, limit)

    def _naver(self, query: str, limit: int) -> list[dict]:
        r = self.http.get("https://openapi.naver.com/v1/search/news.json",
                          params={"query": query, "display": str(limit), "sort": "date"},
                          headers={"X-Naver-Client-Id": self.s.get("NAVER_CLIENT_ID"),
                                   "X-Naver-Client-Secret": self.s.get("NAVER_CLIENT_SECRET")})
        if r.status_code != 200:
            self.store.api_mark("naver", False, f"{r.status_code} {r.text[:150]}")
            raise ApiError("naver", str(r.status_code), r.text[:150])
        self.store.api_mark("naver", True)
        out = []
        for it in r.json().get("items", []):
            title = html.unescape(_TAG.sub("", it.get("title", "")))
            out.append({
                "title": title,
                "link": it.get("originallink") or it.get("link"),
                "published": _rfc822(it.get("pubDate")),
                "source": urllib.parse.urlparse(it.get("originallink") or "").netloc,
                "tone": tone(title),
            })
        return out

    def _google(self, query: str, limit: int) -> list[dict]:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        try:
            r = self.http.get(url)
        except httpx.HTTPError as e:
            self.store.api_mark("google_news", False, str(e))
            raise ApiError("google_news", "network", str(e)) from e
        if r.status_code != 200:
            self.store.api_mark("google_news", False, f"{r.status_code}")
            raise ApiError("google_news", str(r.status_code), "구글 뉴스 RSS 응답 오류")
        self.store.api_mark("google_news", True)
        root = ET.fromstring(r.content)
        out = []
        for item in root.iter("item"):
            title = html.unescape(item.findtext("title") or "")
            src = item.find("source")
            out.append({
                "title": title,
                "link": item.findtext("link"),
                "published": _rfc822(item.findtext("pubDate")),
                "source": src.text if src is not None else "",
                "tone": tone(title),
            })
            if len(out) >= limit:
                break
        return out

    def test(self) -> str:
        items = self.search("삼성전자", 3)
        return f"{'네이버' if self.source == 'naver' else '구글 뉴스 RSS'} 응답 {len(items)}건"


def _rfc822(s: str | None) -> str:
    if not s:
        return ""
    try:
        return email.utils.parsedate_to_datetime(s).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return s
