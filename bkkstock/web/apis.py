"""API 관리 페이지에 보여줄 목록. 키 값은 절대 화면에 다시 보내지 않는다(끝 4자리만)."""

from __future__ import annotations

from ..clients.krx import SERVICE_NAMES
from ..timeutil import last_trading_day, now_kst, prev_trading_day

APIS = [
    {
        "id": "kis", "name": "한국투자증권 Open API", "status_ids": ["kis"],
        "purpose": "넥스트레이드 시세, 종목별 투자자 매매(통합), 공매도·신용잔고. 조회 전용이며 주문 기능은 없습니다.",
        "keys": [("KIS_APP_KEY", "APP KEY"), ("KIS_APP_SECRET", "APP SECRET"), ("KIS_ACCOUNT", "계좌번호 앞 8자리 (선택)")],
        "site": "https://apiportal.koreainvestment.com",
        "steps": ["한국투자증권 앱에서 본인 명의 계좌 비대면 개설", "KIS Developers 포털 가입 후 API 신청",
                  "공동인증서나 모바일 OTP로 본인 인증", "실전투자용 앱 등록 (모의투자는 이 시스템에 필요 없음)",
                  "APP SECRET은 발급 직후에만 보이니 바로 저장"],
        "notes": "접근 토큰은 24시간 유효하고 서버가 하루 한 번 자동으로 받습니다. 1분 안에 두 번 받으면 차단될 수 있습니다.",
    },
    {
        "id": "kis_master", "name": "종목 마스터 파일 (한국투자증권 공개 파일)", "status_ids": ["kis_master"],
        "purpose": "그날의 상장 종목, 거래정지·관리종목·시장경보 상태. 그날 저장하지 않으면 다시 구할 수 없는 데이터입니다.",
        "keys": [], "public": True, "site": "https://github.com/koreainvestment/open-trading-api",
        "steps": ["키가 필요 없습니다."],
    },
    {
        "id": "krx", "name": "KRX Open API", "status_ids": ["krx"],
        "purpose": "코스피·코스닥 전 종목 일별 시세와 지수. 날짜 하나로 전 종목이 옵니다.",
        "keys": [("KRX_OPENAPI_KEY", "인증키")],
        "site": "https://openapi.krx.co.kr",
        "steps": ["KRX Data Marketplace 회원가입", "마이페이지 → API 인증키 신청 → 승인 대기",
                  "서비스 목록에서 아래 서비스마다 활용신청 → 다시 승인 대기"],
        "services": list(SERVICE_NAMES.values()),
        "notes": "활용신청을 안 한 서비스는 권한 오류가 납니다. 그날 데이터가 저녁에 아직 없으면 다음 날 아침 자동으로 다시 받습니다.",
    },
    {
        "id": "dart", "name": "OpenDART", "status_ids": ["dart"],
        "purpose": "공시 목록. 10분마다 조회해 처음 본 시각을 기록하고, 관심 종목 위험 공시는 바로 알립니다.",
        "keys": [("DART_API_KEY", "인증키 (40자리)")],
        "site": "https://opendart.fss.or.kr",
        "steps": ["인증키 신청/관리 → 인증키 신청", "이메일과 사용 용도 입력", "메일로 온 인증 링크 클릭 후 키 확인"],
    },
    {
        "id": "datagokr", "name": "공공데이터포털 주식시세 (선택)", "status_ids": ["datagokr"], "optional": True,
        "purpose": "KRX 시세와 대조하는 백업용. 다음 영업일 13시 이후 갱신됩니다.",
        "keys": [("DATA_GO_KR_KEY", "일반 인증키 (Decoding)")],
        "site": "https://www.data.go.kr/data/15094808/openapi.do",
        "steps": ["회원가입 후 '금융위원회_주식시세정보' 활용신청", "마이페이지 → 개발계정에서 Decoding 키 확인"],
    },
    {
        "id": "news", "name": "뉴스 (네이버 검색 API, 선택)", "status_ids": ["naver", "google_news"], "optional": True,
        "purpose": "관심 종목 뉴스. 네이버 키가 없으면 키 없이 구글 뉴스 RSS를 씁니다.",
        "keys": [("NAVER_CLIENT_ID", "Client ID"), ("NAVER_CLIENT_SECRET", "Client Secret")],
        "site": "https://developers.naver.com/apps/#/register",
        "steps": ["네이버 개발자센터 → 애플리케이션 등록", "사용 API에서 '검색' 선택", "Client ID와 Secret 확인"],
    },
    {
        "id": "ecos", "name": "한국은행 ECOS (지표)", "status_ids": ["ecos"], "optional": True,
        "purpose": "📊 지표 화면의 한국 기준금리·국고채 3·10년·원/달러·원/엔·원/위안(매매기준율)·소비자물가. 하루 한 번(저녁·아침) 1년치를 받습니다.",
        "keys": [("ECOS_API_KEY", "인증키")],
        "site": "https://ecos.bok.or.kr/api/",
        "steps": ["ecos.bok.or.kr → Open API → 회원가입", "인증키 신청 (무료, 사용 목적: 개인 투자 참고)",
                  "발급된 인증키를 아래 칸에 붙여넣고 '연결 테스트'"],
        "notes": "키가 없어도 보드는 그대로 돌고, 지표 화면의 한국 금리·환율 칸만 비어 있습니다.",
    },
    {
        "id": "fred", "name": "미국 연준 경제자료 FRED (키 불필요)", "status_ids": ["fred"], "public": True,
        "purpose": "📊 지표의 미국 기준금리·미국채 2년·장단기 금리차·소비자물가·근원물가·실업률.",
        "keys": [], "site": "https://fred.stlouisfed.org",
        "steps": ["키가 필요 없습니다 (FRED 그래프의 CSV 내려받기 주소). 막히면 미국 칸만 빕니다."],
    },
    {
        "id": "overseas", "name": "해외 시세 (야후 파이낸스, 키 불필요)", "status_ids": ["overseas", "overseas_macro"], "public": True,
        "purpose": "밤사이 미국 상장 한국 ETF(EWY)·필라델피아 반도체·나스닥·원/달러 → 내일 예측가와 시장 개요. 미국채 10년·VIX·달러인덱스·S&P 500·나스닥 → 📊 지표.",
        "keys": [], "site": "https://finance.yahoo.com/quote/EWY",
        "steps": ["키가 필요 없습니다. 비공식 경로라 막히면 내일 예측가의 '해외 반영' 부분만 빠지고 변동 범위는 그대로 나옵니다."],
    },
    {
        "id": "telegram", "name": "텔레그램 봇 (주식 전용)", "status_ids": ["telegram"],
        "purpose": "일일 보고, 보유 종목 위험 알림, 수집 실패 알림.",
        "keys": [("TELEGRAM_BOT_TOKEN", "봇 토큰"), ("TELEGRAM_CHAT_ID", "chat_id")],
        "site": "https://t.me/BotFather",
        "steps": ["@BotFather → /newbot → 토큰 받기", "봇에게 아무 메시지나 보낸 뒤 getUpdates로 chat_id 확인",
                  "코인 봇과 다른 토큰을 쓰세요 (같은 토큰은 충돌 오류가 납니다)"],
    },
]

BY_ID = {a["id"]: a for a in APIS}


def run_test(ctx, api_id: str) -> str:
    today = now_kst().date()
    ref = prev_trading_day(today) if now_kst().hour < 21 else last_trading_day(today)
    if api_id == "kis":
        return ctx.kis.test()
    if api_id == "kis_master":
        n = len(ctx.kis.download_master("KOSPI"))
        return f"코스피 마스터 파일 {n:,}바이트 다운로드"
    if api_id == "krx":
        return ctx.krx.test(ref)
    if api_id == "dart":
        return ctx.dart.test(ref)
    if api_id == "datagokr":
        return ctx.datagokr.test(prev_trading_day(ref))
    if api_id == "news":
        return ctx.news.test()
    if api_id == "telegram":
        return ctx.tg.test()
    if api_id == "overseas":
        return ctx.overseas.test()
    if api_id == "fred":
        return ctx.fred.test()
    if api_id == "ecos":
        return ctx.ecos.test()
    raise KeyError(api_id)
