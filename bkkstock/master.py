"""한국투자증권 종목 마스터 파일(kospi_code.mst, kosdaq_code.mst) 파싱.

필드 폭과 이름은 공식 예제(koreainvestment/open-trading-api, stocks_info/)를 그대로 옮겼다.
이 파일 하나에 상장 종목 목록과 거래정지·관리종목·시장경고 상태가 같이 들어 있어서,
나중에 복구할 수 없는 "그날의 종목 상태"를 매일 찍어두는 데 쓴다.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd

KOSPI_WIDTHS = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1, 9,
                9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
KOSPI_COLS = ['그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류',
              '제조업', '저유동성', '지배구조지수종목', 'KOSPI200섹터업종', 'KOSPI100',
              'KOSPI50', 'KRX', 'ETP', 'ELW발행', 'KRX100',
              'KRX자동차', 'KRX반도체', 'KRX바이오', 'KRX은행', 'SPAC',
              'KRX에너지화학', 'KRX철강', '단기과열', 'KRX미디어통신', 'KRX건설',
              'Non1', 'KRX증권', 'KRX선박', 'KRX섹터_보험', 'KRX섹터_운송',
              'SRI', '기준가', '매매수량단위', '시간외수량단위', '거래정지',
              '정리매매', '관리종목', '시장경고', '경고예고', '불성실공시',
              '우회상장', '락구분', '액면변경', '증자구분', '증거금비율',
              '신용가능', '신용기간', '전일거래량', '액면가', '상장일자',
              '상장주수', '자본금', '결산월', '공모가', '우선주',
              '공매도과열', '이상급등', 'KRX300', 'KOSPI', '매출액',
              '영업이익', '경상이익', '당기순이익', 'ROE', '기준년월',
              '시가총액', '그룹사코드', '회사신용한도초과', '담보대출가능', '대주가능']

KOSDAQ_WIDTHS = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9,
                 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5,
                 9, 8, 9, 3, 1, 1, 1]
KOSDAQ_COLS = ['증권그룹구분코드', '시가총액 규모 구분 코드 유가',
               '지수업종 대분류 코드', '지수 업종 중분류 코드', '지수업종 소분류 코드', '벤처기업 여부 (Y/N)',
               '저유동성종목 여부', 'KRX 종목 여부', 'ETP 상품구분코드', 'KRX100 종목 여부 (Y/N)',
               'KRX 자동차 여부', 'KRX 반도체 여부', 'KRX 바이오 여부', 'KRX 은행 여부', '기업인수목적회사여부',
               'KRX 에너지 화학 여부', 'KRX 철강 여부', '단기과열종목구분코드', 'KRX 미디어 통신 여부',
               'KRX 건설 여부', '(코스닥)투자주의환기종목여부', 'KRX 증권 구분', 'KRX 선박 구분',
               'KRX섹터지수 보험여부', 'KRX섹터지수 운송여부', 'KOSDAQ150지수여부 (Y,N)', '주식 기준가',
               '정규 시장 매매 수량 단위', '시간외 시장 매매 수량 단위', '거래정지 여부', '정리매매 여부',
               '관리 종목 여부', '시장 경고 구분 코드', '시장 경고위험 예고 여부', '불성실 공시 여부',
               '우회 상장 여부', '락구분 코드', '액면가 변경 구분 코드', '증자 구분 코드', '증거금 비율',
               '신용주문 가능 여부', '신용기간', '전일 거래량', '주식 액면가', '주식 상장 일자', '상장 주수(천)',
               '자본금', '결산 월', '공모 가격', '우선주 구분 코드', '공매도과열종목여부', '이상급등종목여부',
               'KRX300 종목 여부 (Y/N)', '매출액', '영업이익', '경상이익', '단기순이익', 'ROE(자기자본이익률)',
               '기준년월', '전일기준 시가총액 (억)', '그룹사 코드', '회사신용한도초과여부', '담보대출가능여부', '대주가능여부']

# 두 시장의 이름을 하나로 맞춘다
KOSPI_MAP = {
    '그룹코드': 'group', '거래정지': 'halted', '정리매매': 'liquidation', '관리종목': 'administrative',
    '시장경고': 'market_warning', '경고예고': 'warning_notice', '불성실공시': 'unfaithful_disclosure',
    '단기과열': 'short_term_overheat', '공매도과열': 'short_overheat', '이상급등': 'abnormal_surge',
    'SPAC': 'spac', '상장일자': 'listed_date', '상장주수': 'listed_shares', '시가총액': 'mktcap_eok',
    '우선주': 'preferred', '기준가': 'base_price', '저유동성': 'low_liquidity', '전일거래량': 'prev_volume',
    '증자구분': 'capital_increase', '락구분': 'ex_rights', 'KOSPI200섹터업종': 'kospi200_sector',
}
KOSDAQ_MAP = {
    '증권그룹구분코드': 'group', '거래정지 여부': 'halted', '정리매매 여부': 'liquidation',
    '관리 종목 여부': 'administrative', '시장 경고 구분 코드': 'market_warning',
    '시장 경고위험 예고 여부': 'warning_notice', '불성실 공시 여부': 'unfaithful_disclosure',
    '단기과열종목구분코드': 'short_term_overheat', '공매도과열종목여부': 'short_overheat',
    '이상급등종목여부': 'abnormal_surge', '기업인수목적회사여부': 'spac', '주식 상장 일자': 'listed_date',
    '상장 주수(천)': 'listed_shares', '전일기준 시가총액 (억)': 'mktcap_eok', '우선주 구분 코드': 'preferred',
    '주식 기준가': 'base_price', '저유동성종목 여부': 'low_liquidity', '전일 거래량': 'prev_volume',
    '증자 구분 코드': 'capital_increase', '락구분 코드': 'ex_rights',
    '(코스닥)투자주의환기종목여부': 'caution_alert',
}
KEEP = ['code', 'std_code', 'name', 'market', 'group', 'halted', 'liquidation', 'administrative',
        'market_warning', 'warning_notice', 'unfaithful_disclosure', 'short_term_overheat', 'short_overheat',
        'abnormal_surge', 'spac', 'caution_alert', 'listed_date', 'listed_shares', 'mktcap_eok', 'preferred',
        'base_price', 'low_liquidity', 'prev_volume', 'capital_increase', 'ex_rights']

MARKET_WARNING = {"00": "", "01": "투자주의", "02": "투자경고", "03": "투자위험"}


def extract_mst(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = [n for n in z.namelist() if n.endswith(".mst")]
        if not names:
            raise ValueError("zip 안에 .mst 파일이 없습니다")
        return z.read(names[0])


def parse_master(mst_bytes: bytes, market: str) -> pd.DataFrame:
    if market == "KOSPI":
        widths, cols, mapping = KOSPI_WIDTHS, KOSPI_COLS, KOSPI_MAP
    elif market == "KOSDAQ":
        widths, cols, mapping = KOSDAQ_WIDTHS, KOSDAQ_COLS, KOSDAQ_MAP
    else:
        raise ValueError(market)
    tail = sum(widths)
    text = mst_bytes.decode("cp949", errors="replace")
    heads, tails = [], []
    for line in text.splitlines():
        if len(line) <= tail:
            continue
        head = line[:-tail]
        heads.append((head[0:9].rstrip(), head[9:21].rstrip(), head[21:].strip()))
        tails.append(line[-tail:])
    if not heads:
        return pd.DataFrame(columns=KEEP)
    df1 = pd.DataFrame(heads, columns=["code", "std_code", "name"])
    df2 = pd.read_fwf(io.StringIO("\n".join(tails)), widths=widths, names=cols, dtype=str, header=None)
    df2 = df2.rename(columns=mapping)
    df = pd.concat([df1, df2], axis=1)
    df["market"] = market
    for c in KEEP:
        if c not in df.columns:
            df[c] = None
    df = df[KEEP].copy()
    df["code"] = df["code"].astype(str).str.strip()
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].where(df[c].notna(), None)
            df[c] = df[c].map(lambda v: v.strip() if isinstance(v, str) else v)
    df["market_warning"] = df["market_warning"].fillna("00").map(lambda v: str(v).zfill(2))
    return df


def stocks_only(df: pd.DataFrame) -> pd.DataFrame:
    """주권(ST)만. ETF·ETN·리츠 등은 뺀다."""
    return df[df["group"] == "ST"].copy()
