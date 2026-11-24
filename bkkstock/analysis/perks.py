"""결제 전에 챙길 것 — 어느 카드로 내든 받는 통신사 멤버십 할인·포인트 적립·포인트 사용처.

카드 순위에는 넣지 않는다(결제수단과 상관없이 받으므로). 결제 추천 화면에 '바코드 내밀기' 안내로만 보인다.
키: program, kind(할인/적립/사용), label, match(가게 이름에 들어갈 말), places(장소 키), rate(%) 또는 fixed(원),
min(사용: 최소 포인트), needs({'skt': [등급]}, {'kt': [등급]}).
통신사 멤버십: T월드 브랜드 목록·membership.kt.com 제휴사 상세(2026-11-24 확인). 횟수(하루 1회 등)는 label 에만.
"""

SKT_ALL = ["일반", "실버", "골드", "VIP"]
KT_ALL = ["일반", "실버", "골드", "VIP", "VVIP"]


def _skt(label, match, rate=0, fixed=0, grades=("VIP",)):
    return {"program": "SKT 멤버십", "kind": "할인", "label": f"SKT 멤버십 {label}", "match": match, "rate": rate, "fixed": fixed,
            "needs": {"skt": list(grades)}}


def _kt(label, match, rate=0, fixed=0, grades=("실버",)):
    return {"program": "KT 멤버십", "kind": "할인", "label": f"KT 멤버십 {label}", "match": match, "rate": rate, "fixed": fixed,
            "needs": {"kt": list(grades)}}


PERKS: list[dict] = [
    # SKT T멤버십 — VIP 와 전 등급
    _skt("파리바게뜨 1천 원당 100원(VIP, 모바일카드)", ["파리바게뜨"], 10),
    _skt("파리바게뜨 1천 원당 50원(실버)", ["파리바게뜨"], 5, grades=("실버",)),
    _skt("뚜레쥬르 15%(VIP·골드)", ["뚜레쥬르"], 15, grades=("VIP", "골드")),
    _skt("배스킨라빈스 싱글레귤러 50%(VIP)", ["배스킨라빈스", "배스킨", "베스킨"], fixed=2_150),
    _skt("도미노·피자헛·파파존스 30%(VIP)", ["도미노", "피자헛", "파파존스"], 30),
    _skt("도미노·피자헛 20%(골드·실버)", ["도미노", "피자헛"], 20, grades=("골드", "실버")),
    _skt("CU·세븐일레븐 1천 원당 100원(VIP)", ["CU", "씨유", "세븐일레븐", "7-eleven"], 10),
    _skt("GS25 화요일 신선식품(도시락·김밥) 1천 원당 200원", ["GS25", "지에스25"], 20, grades=SKT_ALL),
    _skt("CGV 무료 연 3회·1+1 연 9회, 그 밖엔 최대 4천 원(VIP)", ["CGV"], fixed=4_000),
    _skt("아웃백 15%(VIP, 하루 최대 2만 원·월 4회)", ["아웃백"], 15),
    _skt("빕스 15%(VIP)", ["빕스", "VIPS"], 15),
    _skt("메가MGC커피 20%(VIP)", ["메가커피", "메가MGC", "메가엠지씨"], 20),
    _skt("던킨 15%(VIP, 하루 1회)", ["던킨"], 15),
    _skt("공차·폴바셋 10%", ["공차", "폴바셋", "폴 바셋"], 10, grades=SKT_ALL),
    _skt("롯데리아 1천 원당 150원", ["롯데리아"], 15, grades=SKT_ALL),
    _skt("이마트 짝수 달 7%·홀수 달 3%(VIP)", ["이마트"], 3),
    # KT 멤버십 — 실버 기준(하루 1회·결제 20만 원까지)
    _kt("GS25 5%(실버)", ["GS25", "지에스25"], 5),
    _kt("GS25 10%(VIP 이상)", ["GS25", "지에스25"], 10, grades=("VIP", "VVIP")),
    _kt("파리바게뜨 5%(실버)", ["파리바게뜨", "파리크라상"], 5),
    _kt("뚜레쥬르 10%(실버)", ["뚜레쥬르"], 10),
    _kt("도미노 15%(실버)", ["도미노"], 15),
    _kt("아웃백·빕스 5%(실버)", ["아웃백", "빕스", "VIPS"], 5),
    _kt("CGV 최대 5천 원(2D, 동반 4인)", ["CGV"], fixed=5_000, grades=KT_ALL),
    _kt("메가박스 최대 6천 원(동반 4인)", ["메가박스"], fixed=6_000, grades=KT_ALL),
    _kt("롯데시네마 최대 5천 원(동반 4인)", ["롯데시네마"], fixed=5_000, grades=KT_ALL),
    _kt("배스킨라빈스 생일 달 케이크 4천 원", ["배스킨라빈스", "배스킨"], fixed=4_000, grades=KT_ALL),
    _kt("스타벅스 음료 사이즈업(월 1회)", ["스타벅스"], grades=KT_ALL),
]
