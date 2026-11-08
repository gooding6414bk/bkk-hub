from bkkstock.master import KOSDAQ_COLS, KOSDAQ_WIDTHS, KOSPI_COLS, KOSPI_WIDTHS, parse_master, stocks_only


def _line(code, name, cols, widths, values):
    head = code.ljust(9) + ("KR7" + code + "000").ljust(12) + name
    parts = []
    for c, w in zip(cols, widths):
        v = values.get(c, "N" if w == 1 else "0" * w)
        parts.append(str(v).rjust(w)[:w] if not str(v).isalpha() else str(v).ljust(w)[:w])
    return head + "".join(parts)


def test_parse_kospi_flags():
    rows = [
        _line("005930", "삼성전자", KOSPI_COLS, KOSPI_WIDTHS, {"그룹코드": "ST", "상장일자": "19750611", "시장경고": "00"}),
        _line("000001", "정지회사", KOSPI_COLS, KOSPI_WIDTHS, {"그룹코드": "ST", "거래정지": "Y", "관리종목": "Y", "시장경고": "02"}),
        _line("069500", "ETF예시", KOSPI_COLS, KOSPI_WIDTHS, {"그룹코드": "EF"}),
    ]
    df = parse_master("\n".join(rows).encode("cp949"), "KOSPI")
    assert list(df["code"]) == ["005930", "000001", "069500"]
    assert df.loc[0, "name"] == "삼성전자"
    assert df.loc[0, "listed_date"] == "19750611"
    assert df.loc[1, "halted"] == "Y" and df.loc[1, "administrative"] == "Y" and df.loc[1, "market_warning"] == "02"
    assert len(stocks_only(df)) == 2


def test_parse_kosdaq_caution():
    rows = [_line("086520", "에코예시", KOSDAQ_COLS, KOSDAQ_WIDTHS,
                  {"증권그룹구분코드": "ST", "(코스닥)투자주의환기종목여부": "Y", "시장 경고 구분 코드": "01"})]
    df = parse_master("\r\n".join(rows).encode("cp949"), "KOSDAQ")
    assert df.loc[0, "caution_alert"] == "Y" and df.loc[0, "market_warning"] == "01"
    assert df.loc[0, "market"] == "KOSDAQ"
