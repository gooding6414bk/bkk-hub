"""부동산 규제지역(투기과열지구·조정대상지역)과 토지거래허가구역 목록 (수도권).

부동산 화면의 규제 지도(web/static/regmap.svg)와 목록이 같은 데이터를 쓴다.
CHECKED 기준으로 공식 자료(국토교통부·정책브리핑·서울시·경기도)를 확인해 손으로 적은 값이다.
지정·해제·연장 발표가 나오면 이 파일과 지도를 함께 고친다.

kind
  both = 투기과열지구·조정대상지역 + 토지거래허가구역
  adj  = 투기과열지구·조정대상지역만
  land = 토지거래허가구역만
날짜는 "YYYY-MM-DD" 문자열, 모르거나 없으면 "".
"""

from __future__ import annotations

from typing import NamedTuple

CHECKED = "2026-11-25"


class Zone(NamedTuple):
    name: str          # 행정구역 이름 ("강남구", "성남시 분당구")
    sido: str          # "서울" / "경기"
    kind: str          # both / adj / land
    adj_since: str     # 투기과열·조정 지정(효력) 일. 기존 지정 유지면 ""
    land_since: str    # 토지거래허가구역 효력 발생일
    land_until: str    # 토지거래허가구역 만료일
    land_scope: str    # 허가 대상 주택
    note: str
    sources: tuple[str, ...]


# ── 출처 ──────────────────────────────────────────────────────────
SRC_1015_MOLIT = "https://www.molit.go.kr/USR/NEWS/m_71/dtl.jsp?lcmspage=1&id=95091308"
SRC_1015_KOREA = "https://www.korea.kr/news/policyNewsView.do?newsId=148950973"
SRC_1015_BRIEF = "https://www.korea.kr/briefing/policyBriefingView.do?newsId=156722073"
SRC_GG_LIST = "https://gris.gg.go.kr/gnPlan/selectLandTransactionZone.do"
SRC_0630_MOLIT = "https://www.molit.go.kr/USR/NEWS/m_71/dtl.jsp?id=95092167"
SRC_0630_KOREA = "https://www.korea.kr/news/policyNewsView.do?newsId=148967354"
SRC_0630_GG = "https://gnews.gg.go.kr/briefing/brief_gongbo_view.do?BS_CODE=S017&number=70759"
SRC_SEOUL_4GU = "https://opengov.seoul.go.kr/press/34357494"
SRC_SEOUL_APGU = "https://www.seoul.go.kr/news/news_report.do?nttNo=455446&srchCtgry=465"

SCOPE_1015 = "아파트 + 같은 단지에 아파트 1개 동 이상 포함된 연립·다세대"
SCOPE_APT = "아파트만"

_SEOUL_KEEP = ("강남구", "서초구", "송파구", "용산구")
_SEOUL_NEW = (
    "종로구", "중구", "성동구", "광진구", "동대문구", "중랑구", "성북구",
    "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구",
    "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구", "강동구",
)
_GG_1015 = (
    "과천시", "광명시", "성남시 분당구", "성남시 수정구", "성남시 중원구",
    "수원시 영통구", "수원시 장안구", "수원시 팔달구", "안양시 동안구",
    "용인시 수지구", "의왕시", "하남시",
)
_GG_0701 = ("화성시 동탄구", "용인시 기흥구", "구리시")

ZONES: tuple[Zone, ...] = (
    *(
        Zone(n, "서울", "both", "", "2025-03-24", "2026-12-31", SCOPE_APT,
             "규제지역은 기존 지정 유지(10·15 대책에서 유지). 토허는 서울시 지정"
             "(2025-03-24 최초, 2025-10-01~2026-12-31 재지정), 10·15 국토부 지정과 만료일 같음",
             (SRC_1015_KOREA, SRC_SEOUL_4GU))
        for n in _SEOUL_KEEP
    ),
    *(
        Zone(n, "서울", "both", "2025-10-16", "2025-10-20", "2026-12-31", SCOPE_1015,
             "10·15 대책 신규 지정(국토부). 토허 연장 여부 미정",
             (SRC_1015_MOLIT, SRC_1015_KOREA, SRC_1015_BRIEF))
        for n in _SEOUL_NEW
    ),
    *(
        Zone(n, "경기", "both", "2025-10-16", "2025-10-20", "2026-12-31", SCOPE_1015,
             "10·15 대책 신규 지정(국토부, 경기 12곳 501.00㎢). 토허 연장 여부 미정",
             (SRC_1015_MOLIT, SRC_1015_KOREA, SRC_GG_LIST))
        for n in _GG_1015
    ),
    *(
        Zone(n, "경기", "both", "2026-07-01", "2026-07-05", "2027-12-31", SCOPE_APT,
             "2026-06-30 주정심 의결 추가 지정. 토허는 경기도 지정"
             "(기흥 81.64㎢·동탄 55.52㎢·구리 33.34㎢)",
             (SRC_0630_MOLIT, SRC_0630_KOREA, SRC_0630_GG))
        for n in _GG_0701
    ),
)

# 위 시·구 전체 지정과 겹치는 서울시 자체 토지거래허가구역(동 단위, 주택 종류 무관 토지 기준).
# 지도에는 따로 표시하지 않는다.
SEOUL_EXTRA_LAND: tuple[tuple[str, str, str, str], ...] = (
    # (구역, 효력 만료, 비고, 출처)
    ("압구정 아파트지구(강남)·여의도 아파트지구(영등포)·목동 택지개발 아파트단지(양천)·"
     "성수전략정비구역(성동), 약 4.6㎢", "2027-04-26",
     "2021-04-27 최초 지정, 2026-04-01 서울시 도시계획위원회에서 1년 연장", SRC_SEOUL_APGU),
)

# 지도 원본(KOSTAT 2013 시군구 경계)에 없는 새 구 → 지도에서 표시할 상위 시.
MAP_PARENT = {"화성시 동탄구": "화성시"}

# 지도 class 이름.
MAP_CLASS = {"both": "reg-both", "adj": "reg-adj", "land": "reg-land"}
MAP_PARTIAL_CLASS = "reg-partial"


def by_name() -> dict[str, Zone]:
    return {z.name: z for z in ZONES}


def map_class(name: str) -> str:
    """지도 구역 이름(공백 무시)에 줄 class. 지정이 없으면 ""."""
    key = name.replace(" ", "")
    for z in ZONES:
        if z.name.replace(" ", "") == key:
            return MAP_CLASS[z.kind]
    for child, parent in MAP_PARENT.items():
        if parent.replace(" ", "") == key:
            return MAP_PARTIAL_CLASS
    return ""
