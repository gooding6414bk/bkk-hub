"""💳 카드 목록 — 상품설명서를 앱 형식으로 옮긴 것. 지갑에서 고르면 혜택 칸이 채워진다.

키: name, kind(신용/체크), base(기본 적립 %), bonus({장소: %}), tier(혜택이 생기는 전월 실적, 원),
benefit(실적 채우면 한 달 혜택 어림 — caps 가 없을 때만), caps([[전월 실적, 월 통합 할인 한도], ...] — fixed 면 월 할인액),
fixed(정액 혜택), rules(가맹점 할인: label, match(가게 이름에 들어갈 말), places(장소 키), rate(%) 또는 fixed(건당 원),
min(건당 최소), upto(건당 할인 대상 최대 금액), month_cap(항목 월 한도), times(월 횟수), need(항목 전월 실적, 없으면 tier)),
note(앱이 못 담는 조건), summary(목록 한 줄), src(확인한 출처), checked(확인일).
카드사가 혜택을 바꾸면 여기 값도 바꿔야 한다 — 확인일을 같이 적는다.
"""

TELCO = ["SKT", "KT", "LG", "유플러스", "통신", "휴대폰"]

CATALOG: list[dict] = [
    {
        "key": "kb-nori2-play", "name": "KB국민 노리2 체크카드(Play)", "kind": "체크", "base": 0.0, "bonus": {},
        "tier": 200_000, "benefit": 0.0, "fixed": False,
        "caps": [[0, 3_000], [200_000, 20_000], [400_000, 30_000], [600_000, 40_000], [800_000, 50_000]],
        "rules": [
            {"label": "스타벅스·커피빈 10%", "match": ["스타벅스", "커피빈"], "rate": 10, "month_cap": 3_000, "need": 0},
            {"label": "구글플레이·앱스토어 10%", "match": ["구글플레이", "google play", "앱스토어", "app store"], "rate": 10, "month_cap": 5_000},
            {"label": "인터파크 공연 10%", "match": ["인터파크"], "rate": 10, "month_cap": 7_000},
            {"label": "올리브영·미용실 5%", "match": ["올리브영", "미용실", "헤어"], "rate": 5, "month_cap": 2_000},
            {"label": "GS25·CU 5%", "match": ["GS25", "지에스25", "CU", "씨유"], "rate": 5, "month_cap": 2_000},
            {"label": "넷플릭스·유튜브 프리미엄 건당 1천 원", "match": ["넷플릭스", "netflix", "유튜브", "youtube"], "fixed": 1_000, "min": 10_000, "times": 2},
            {"label": "배민·요기요 건당 1천 원", "match": ["배민", "배달의민족", "요기요"], "fixed": 1_000, "min": 10_000, "times": 1},
            {"label": "통신요금 자동이체 2,500원", "places": ["고정"], "match": TELCO + ["리브엠", "liiv"], "fixed": 2_500, "min": 50_000, "times": 1},
            {"label": "CGV 건당 4천 원", "match": ["CGV"], "fixed": 4_000, "min": 10_000, "times": 2},
            {"label": "에버랜드·롯데월드 1만 5천 원", "match": ["에버랜드", "롯데월드"], "fixed": 15_000, "min": 30_000, "times": 1},
            {"label": "[Play] PC방 10%", "match": ["PC방", "피시방"], "rate": 10, "month_cap": 2_000, "need": 300_000},
            {"label": "[Play] 패스트푸드 건당 1천 원", "match": ["맥도날드", "버거킹", "롯데리아", "KFC", "맘스터치", "패스트푸드"],
             "fixed": 1_000, "min": 10_000, "times": 1, "need": 300_000},
        ],
        "note": ("후불교통 탑재(교통 할인은 없음). 호텔·백화점·대형마트·역사 입점 매장, 상품권·선불 충전, 다른 간편결제 경유는 할인 제외. "
                 "후불교통요금·지방세·상하수도·상품권은 전월 실적에서 빠짐. 커피 할인은 실적 없이도, Play 혜택(PC방·패스트푸드)은 전월 30만 원 이상."),
        "summary": "커피 10%·편의점 5%·CGV 건당 4천 원 등 · 월 통합 한도 2만~5만 원(실적 20만~80만)",
        "src": "https://m.kbcard.com/c/07986 · 상품설명서 07986 (2026-05-22 심의)", "checked": "2026-11-25",
    },
    {
        "key": "kb-nori-rf", "name": "KB국민 노리 체크카드", "kind": "체크", "base": 0.0, "bonus": {},
        "tier": 200_000, "benefit": 0.0, "fixed": False,
        "caps": [[200_000, 10_000], [300_000, 20_000], [500_000, 30_000], [1_000_000, 50_000]],
        "rules": [
            {"label": "버스·지하철 10%", "places": ["교통"], "rate": 10, "month_cap": 2_000, "need": 300_000},
            {"label": "통신요금 자동이체 2,500원", "places": ["고정"], "match": TELCO, "fixed": 2_500, "min": 50_000, "times": 1, "need": 300_000},
            {"label": "CGV 35%", "match": ["CGV"], "rate": 35, "min": 10_000, "upto": 20_000},
            {"label": "스타벅스 20%", "match": ["스타벅스"], "rate": 20, "min": 10_000, "upto": 20_000},
            {"label": "아웃백·빕스 20%", "match": ["아웃백", "빕스", "VIPS"], "rate": 20, "min": 30_000, "upto": 50_000},
            {"label": "에버랜드·롯데월드 현장 50%", "match": ["에버랜드", "롯데월드"], "rate": 50, "min": 30_000, "upto": 50_000},
            {"label": "GS25 5%", "match": ["GS25", "지에스25"], "rate": 5, "min": 10_000, "upto": 20_000},
            {"label": "교보문고 5%", "match": ["교보문고"], "rate": 5, "min": 20_000, "upto": 50_000},
        ],
        "note": ("후불교통 탑재. 버스·지하철 10%는 이 카드를 후불교통으로 쓸 때만, 교통·통신 할인은 전월 30만 원 이상. "
                 "해외 이용·후불교통요금은 전월 실적에서 빠짐. 최근 1개월 70만 원 이상이면 타행이체·KB ATM 수수료 면제."),
        "summary": "버스·지하철 10%·스타벅스 20%·CGV 35% 등 · 월 통합 한도 1만~5만 원(실적 20만~100만)",
        "src": "https://m.kbcard.com/CXHIACRC0002.cms?mainCC=b&allianceCode=01664 · 상품설명서 01664 (2026-05-22 심의)",
        "checked": "2026-11-25",
    },
    {
        "key": "kb-kt-superdc7", "name": "kt super DC 7 KB국민BC카드", "kind": "신용", "base": 0.0, "bonus": {},
        "tier": 300_000, "benefit": 0.0, "fixed": True, "caps": [[300_000, 15_000], [700_000, 20_000]], "rules": [],
        "note": ("KT 요금(단말 할부 포함)을 이 카드로 자동이체해야 할인. 금액은 KT 요금 7만 원 이상 기준 — 7만 원 미만이면 월 7천 원. "
                 "할인은 청구액을 넘지 않음. 신규 발급은 2019-01 중단, 갱신도 막혔을 수 있음(확인 필요). 연회비 2만 5천~2만 7천 원."),
        "summary": "정액: 전월 30만 원이면 KT 요금 1만 5천 원, 70만 원이면 2만 원 할인",
        "src": "https://m.bccard.com/app/mobileweb/CardDetail.do?exec=cardDetail&cardGdsNo=1000025054 · 2016-11 출시 기사",
        "checked": "2026-11-25",
    },
]
PRESET = {c["key"]: c for c in CATALOG}
