"""💳 카드 목록 — 상품설명서를 앱 형식으로 옮긴 것. 지갑에서 고르면 혜택 칸이 채워진다.

키: name, kind(신용/체크), base(기본 적립 %), bonus({장소: 추가 할인 %}), tier(혜택이 생기는 전월 실적, 원),
benefit(실적 채우면 한 달 받는 혜택 어림, 원), caps([[전월 실적, 월 할인 한도], ...] — fixed 면 월 할인액),
fixed(정액 혜택), note(정액 할인·조건 등 앱이 못 담는 내용), src(확인한 출처), checked(확인일).
카드사가 혜택을 바꾸면 여기 값도 바꿔야 한다 — 확인일을 같이 적는다.
"""

CATALOG: list[dict] = []
PRESET = {c["key"]: c for c in CATALOG}
