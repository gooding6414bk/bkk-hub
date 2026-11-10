"""OpenDART. 공시 목록 응답에는 접수일자만 있고 시각은 없다고 알려져 있어서,
10분마다 조회해 '처음 본 시각'을 직접 기록한다 (첫 주 점검에서 필드를 다시 확인)."""

from __future__ import annotations

import datetime as dt
import io
import xml.etree.ElementTree as ET
import zipfile

import httpx

from ..config import Settings
from ..store import Store
from .base import ApiError, NotConfigured, make_http

BASE_URL = "https://opendart.fss.or.kr/api"
NO_DATA = "013"
OK = "000"
RATE = "020"

STATUS_TEXT = {
    "010": "등록되지 않은 키", "011": "사용할 수 없는 키", "012": "접근할 수 없는 IP",
    "013": "조회된 데이터 없음", "014": "파일이 존재하지 않음", "020": "요청 제한 초과",
    "021": "조회 가능한 회사 수 초과", "100": "필드의 부적절한 값", "101": "부적절한 접근",
    "800": "시스템 점검 중", "900": "정의되지 않은 오류", "901": "개인정보 보유기간 만료 키",
}


class DartClient:
    api = "dart"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(30)

    def _key(self) -> str:
        k = self.s.get("DART_API_KEY")
        if not k:
            raise NotConfigured(self.api, ["DART_API_KEY"])
        return k

    def list_page(self, bgn: dt.date, end: dt.date, page_no: int = 1, page_count: int = 100,
                  corp_code: str | None = None, corp_cls: str | None = None) -> dict:
        params = {
            "crtfc_key": self._key(),
            "bgn_de": bgn.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_no": str(page_no),
            "page_count": str(page_count),
        }
        if corp_code:
            params["corp_code"] = corp_code
        if corp_cls:
            params["corp_cls"] = corp_cls
        try:
            r = self.http.get(f"{BASE_URL}/list.json", params=params)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.store.api_mark(self.api, False, f"공시 목록 연결 오류 {e}")
            raise ApiError(self.api, "network", str(e), retryable=True) from e
        status = str(body.get("status", ""))
        if status == OK:
            self.store.api_mark(self.api, True)
            return body
        if status == NO_DATA:
            self.store.api_mark(self.api, True)
            return {"list": [], "total_page": 0, "total_count": 0}
        msg = body.get("message") or STATUS_TEXT.get(status, "")
        self.store.api_mark(self.api, False, f"{status} {msg}")
        raise ApiError(self.api, status, msg, retryable=status in (RATE, "800"))

    def list_all(self, bgn: dt.date, end: dt.date, stop_at: set[str] | None = None,
                 max_pages: int = 60, corp_cls: str | None = None) -> list[dict]:
        """최신순으로 받는다. stop_at에 있는 접수번호를 만나면 그 페이지까지만."""
        out: list[dict] = []
        page = 1
        while page <= max_pages:
            body = self.list_page(bgn, end, page, corp_cls=corp_cls)
            items = body.get("list") or []
            out.extend(items)
            if stop_at and any(i.get("rcept_no") in stop_at for i in items):
                break
            if page >= int(body.get("total_page") or 0):
                break
            page += 1
        return out

    def corp_codes(self) -> list[dict]:
        r = self.http.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": self._key()}, timeout=120)
        if r.status_code != 200 or not r.content.startswith(b"PK"):
            self.store.api_mark(self.api, False, "회사 고유번호 파일 다운로드 실패")
            raise ApiError(self.api, str(r.status_code), "회사 고유번호 파일 다운로드 실패")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0])
        root = ET.fromstring(xml)
        out = []
        for el in root.iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            if stock:
                out.append({"corp_code": el.findtext("corp_code"), "corp_name": el.findtext("corp_name"),
                            "stock_code": stock})
        self.store.api_mark(self.api, True)
        return out

    def test(self, day: dt.date) -> str:
        body = self.list_page(day, day, 1, 10)
        fields = sorted((body.get("list") or [{}])[0].keys())
        has_time = any("tm" in f or "time" in f for f in fields)
        return f"{day} 공시 {body.get('total_count', 0)}건 · 접수 시각 필드 {'있음' if has_time else '없음'}"


# ---------- 공시 분류 ----------
RISK_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("희석", ("유상증자", "전환사채", "신주인수권부사채", "교환사채", "전환가액의조정", "전환가액조정",
             "전환청구권행사", "신주인수권행사", "교환청구권행사", "추가상장", "주식등의발행", "제3자배정",
             "회차CB", "회차BW", "회차EB", "증권신고서(지분증권)", "자기전환사채매도")),
    ("부실", ("관리종목", "상장폐지", "감사의견", "의견거절", "한정", "계속기업", "회생", "파산", "부도",
             "영업정지", "자본잠식", "감자결정", "횡령", "배임", "불성실공시", "투자주의환기", "상장적격성",
             "해산사유", "개선계획", "개선기간", "실질심사", "기업심사위원회")),
    # 5% 대량보유·소유주식변동 보고는 매수일 수도 있어서 넣지 않는다
    ("매도압력", ("특정증권등거래계획", "거래계획보고서", "최대주주변경", "보호예수해제", "의무보유해제", "경영권변경")),
    ("소송", ("소송등의제기", "소송등의판결", "가처분")),
]
GOOD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("주주환원", ("자기주식취득결정", "자기주식소각", "주식소각결정", "현금ㆍ현물배당", "현금배당",
                 "자기주식취득신탁계약체결")),
    ("실적", ("영업(잠정)실적", "매출액또는손익구조", "잠정실적")),
    ("수주", ("단일판매ㆍ공급계약", "공급계약체결")),
]


# 같은 낱말이 들어가도 뜻이 반대거나 위험이 아닌 제목 (실제 공시 제목 사례는 tests/test_disclosure_titles.py)
EXCLUDE: dict[str, tuple[str, ...]] = {
    # 무상증자·스톡옵션 행사 추가상장은 희석 위험이 아니다. 철회는 오히려 반대. 만기 전 사채 취득은 희석을 줄인다.
    # 거래정지 '해제', 액면분할·병합(주식의 병합·분할)에 따른 정지·변경상장, 조회공시 요구에 따른 정지,
    # 지정 '해제'·'미지정'은 부실이 아니다
    "부실": ("해제", "액면분할", "액면병합", "변경상장", "거래정지(조회공시", "주식분할", "주식병합", "병합", "분할",
             "미지정", "자산유동화", "우려", "미달"),
    "희석": ("무상증자", "주식매수선택권", "철회", "만기전", "사채취득"),
    # 공급계약 '해지'는 수주가 아니라 악재 (아래 계약해지로 분류)
    "수주": ("해지",),
    # 주식양수도계약 해제·취소는 최대주주가 안 바뀐다는 뜻. 보호예수·의무보유 '해제'는 매도 압력이 맞다.
    "매도압력": ("계약해제", "취소"),
    # 매출액·손익구조 30% 변동은 오를 수도 내릴 수도 있다 — 방향을 모르니 '실적'(호재)로 보지 않는다
    "실적": ("매출액또는손익구조",),
}
# 종속회사·자회사 공시는 모회사의 희석·주주환원이 아니다
SUBSIDIARY = ("종속회사", "자회사의주요경영사항", "자회사의주요")
CANCEL_WORDS = ("공급계약해지", "판매ㆍ공급계약해지", "공급계약해제")
EXTRA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("매도압력", ("자기주식처분결정", "특수관계인에대한주식의처분")),
    ("영업위험", ("거래처와의거래중단", "중대재해", "생산중단")),
    # 거래소의 '시가총액 미달·주가 1,000원 미달 우려' 안내 — 아직 지정 전이라 매수 금지가 아니라 주의
    ("상폐우려", ("시가총액미달", "시가총액기준미달", "1,000원미달", "1천원미달", "관리종목지정우려", "상장폐지우려",
                 "미달우려", "관리종목지정사유발생우려")),
]
# '주권매매거래정지'는 정지 사유가 위험할 때만 부실 (사유 없는 정지·병합 정지 등은 universe의 거래정지 표시가 맡는다)
HALT_RISK = ("상장폐지", "실질심사", "상장적격성", "감사", "의견거절", "횡령", "배임", "회생", "부도", "불성실", "투자자보호",
             "관리종목")


def classify(report_nm: str) -> list[str]:
    name = (report_nm or "").replace(" ", "")
    tags = []
    if name.startswith("[기재정정]") or name.startswith("[첨부정정]") or "정정" in name[:8]:
        tags.append("정정")
    sub = any(w in name for w in SUBSIDIARY)
    halt = "거래정지" in name
    for tag, words in RISK_RULES + GOOD_RULES + EXTRA_RULES:
        if tag in tags:
            continue
        if sub and tag != "계약해지":
            continue          # 자회사·종속회사 공시는 모회사의 위험·호재로 보지 않는다
        if tag == "부실" and halt and not any(w in name for w in HALT_RISK):
            continue
        if any(w in name for w in words) and not any(x in name for x in EXCLUDE.get(tag, ())):
            if tag == "부실" and "감사의견" in name and "적정" in name and not any(
                    x in name for x in ("부적정", "비적정", "의견거절", "한정")):
                continue          # '감사의견 적정' 같은 제목
            tags.append(tag)
    if (halt and not sub and "부실" not in tags and any(w in name for w in HALT_RISK)
            and not any(x in name for x in EXCLUDE["부실"])):
        tags.append("부실")       # 위험한 사유의 거래정지 (예: 정지기간 변경(투자자 보호))
    if any(w in name for w in CANCEL_WORDS) and "계약해지" not in tags:
        tags.append("계약해지")
    return tags


RISK_TAGS = {"희석", "부실", "매도압력", "소송", "계약해지", "영업위험", "상폐우려"}
CLASSIFY_VERSION = "c4-2026-11"   # 분류 규칙을 바꾸면 올린다 → 저장된 공시의 분류를 다시 매긴다(retag)
