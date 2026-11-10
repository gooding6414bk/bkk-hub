"""화면 미리보기용 샘플 데이터. 가상의 회사 이름과 가격만 쓴다.

  BKK_STOCK_DATA=data-demo python -m bkkstock demo-seed
  BKK_STOCK_DATA=data-demo BKK_STOCK_DEMO=1 DASH_PASSWORD=demo python -m bkkstock web
"""

from __future__ import annotations

import datetime as dt
import json
import random

import numpy as np
import pandas as pd

from .analysis import outlook
from .clients.dart import classify
from .config import get_settings
from .store import Store
from .timeutil import iso_kst, last_trading_day, now_kst, trading_days_back

SYL = list("가나다라마바사아자차카타파하") + ["한빛", "새봄", "푸른", "누리", "다온", "온새", "미르", "하람"]
SUF = ["전자", "바이오", "화학", "중공업", "소재", "제약", "에너지", "로보틱스", "반도체", "건설", "식품", "게임즈", "테크", "홀딩스"]


def seed(days: int = 70, n: int = 320, seed_: int = 7) -> str:
    s = get_settings()
    store = Store(s.data_dir)
    rnd = random.Random(seed_)
    np.random.seed(seed_)
    end = last_trading_day(now_kst().date() - dt.timedelta(days=0 if now_kst().hour >= 21 else 1))
    tdays = trading_days_back(end, days)
    names, used = [], set()
    while len(names) < n:
        nm = rnd.choice(SYL) + rnd.choice(SYL[:14]) + rnd.choice(SUF)
        if nm not in used:
            used.add(nm)
            names.append(nm)
    codes = [f"9{i:05d}" for i in range(1, n + 1)]
    markets = ["KOSPI" if i < n * 0.4 else "KOSDAQ" for i in range(n)]
    uni = pd.DataFrame({
        "code": codes, "std_code": [f"KR7{c}00{i % 10}" for i, c in enumerate(codes)], "name": names,
        "market": markets, "group": "ST", "halted": "N", "liquidation": "N", "administrative": "N",
        "market_warning": "00", "warning_notice": "N", "unfaithful_disclosure": "N", "short_term_overheat": "0",
        "short_overheat": "N", "abnormal_surge": "N", "spac": "N", "caution_alert": "N",
        "listed_date": "20150101", "listed_shares": "10000", "mktcap_eok": "1000", "preferred": "0",
        "base_price": "10000", "low_liquidity": "N", "prev_volume": "0", "capital_increase": "00", "ex_rights": "00",
    })
    uni.loc[5, "administrative"] = "Y"
    uni.loc[9, "market_warning"] = "02"
    uni.loc[12, "warning_notice"] = "Y"
    uni.loc[20, "listed_date"] = tdays[-20].strftime("%Y%m%d")
    price = np.exp(np.random.uniform(np.log(2000), np.log(300000), n))
    shares = np.random.uniform(5e6, 3e8, n)
    vol_scale = np.random.uniform(0.008, 0.04, n)
    horse_codes = {codes[3]: tdays[-6], codes[7]: tdays[-12], codes[14]: tdays[-3]}
    material_codes = {codes[2]: tdays[-2], codes[11]: tdays[-4]}
    nxt_set = set(codes[: int(n * 0.35)])
    disclosures = []
    rno = 20260000000000
    # 가상의 밤사이 해외 시세 (미국 평일 기준)
    us_days = [tdays[0] - dt.timedelta(days=7) + dt.timedelta(days=k) for k in range((tdays[-1] - tdays[0]).days + 8)]
    us_days = [d for d in us_days if d.weekday() < 5]
    us = {}
    lv = {"EWY": 60.0, "^SOX": 5000.0, "^IXIC": 18000.0, "KRW=X": 1380.0}
    vol = {"EWY": 0.012, "^SOX": 0.02, "^IXIC": 0.012, "KRW=X": 0.004}
    ewy_r = {}
    for d in us_days:
        common = np.random.normal(0, 1)
        for sym in lv:
            r = vol[sym] * (0.7 * common + 0.7 * np.random.normal(0, 1)) * (-0.4 if sym == "KRW=X" else 1)
            lv[sym] *= 1 + r
            us.setdefault(sym, []).append({"symbol": sym, "date": d.isoformat(), "open": lv[sym], "close": lv[sym]})
            if sym == "EWY":
                ewy_r[d] = r
    store.write_clean("overseas", tdays[-1], pd.DataFrame([r for rows in us.values() for r in rows]))
    beta = np.random.uniform(0.4, 1.4, n)
    prev_close = price.copy()
    prev_nxt_after = np.zeros(n)
    for di, d in enumerate(tdays):
        prev_d = tdays[di - 1] if di else d - dt.timedelta(days=1)
        win = [r for ud, r in ewy_r.items() if prev_d <= ud < d]
        ewy_win = float(np.prod([1 + r for r in win]) - 1) if win else 0.0
        gap = beta * ewy_win + 0.4 * prev_nxt_after + np.random.normal(0, 0.004, n)
        opens = prev_close * (1 + gap)
        intraday = np.random.normal(0.0002, vol_scale * 0.8)
        rets = (opens * (1 + intraday)) / prev_close - 1
        for c, hd in {**horse_codes, **material_codes}.items():
            i = codes.index(c)
            if d == hd:
                rets[i] = 0.24 if c in horse_codes else 0.17
            elif hd < d <= hd + dt.timedelta(days=6):
                rets[i] = np.random.normal(-0.03, 0.02)
        price = prev_close * (1 + rets)
        value = price * shares * np.random.uniform(0.002, 0.02, n)
        for c, hd in {**horse_codes, **material_codes}.items():
            if d == hd:
                value[codes.index(c)] *= 9
        hi_ = np.maximum(opens, price) * (1 + np.abs(np.random.normal(0, 0.006, n)))
        lo_ = np.minimum(opens, price) * (1 - np.abs(np.random.normal(0, 0.006, n)))
        rows = pd.DataFrame({
            "date": d.strftime("%Y%m%d"), "code": codes, "name": names, "market": markets,
            "open": np.round(opens), "high": np.round(hi_), "low": np.round(lo_),
            "close": np.round(price), "change_pct": np.round(rets * 100, 2), "volume": np.round(value / price),
            "value": np.round(value), "mktcap": np.round(price * shares), "listed_shares": np.round(shares),
        })
        store.write_clean("prices_krx", d, rows)
        nxt_mask = rows["code"].isin(nxt_set).to_numpy()
        nxt_after = np.where(nxt_mask, np.random.normal(0, 0.006, n), 0.0)
        nx = rows[nxt_mask][["date", "code"]].copy()
        nx["close"] = np.round(price[nxt_mask] * (1 + nxt_after[nxt_mask]))
        nx["value"] = (rows.loc[nxt_mask, "value"] * np.random.uniform(0.15, 0.4)).round().values
        nx["volume"] = (nx["value"] / nx["close"]).round()
        store.write_clean("prices_nxt", d, nx)
        prev_nxt_after = nxt_after
        prev_close = price.copy()
        frgn = np.round(np.random.normal(0, 1, n) * value / 1e6 * 0.05)
        orgn_parts = {k: np.round(np.random.normal(0, 1, n) * value / 1e6 * w) for k, w in
                      (("scrt", 0.012), ("ivtr", 0.01), ("pe_fund", 0.006), ("fund", 0.015), ("insu", 0.005),
                       ("bank", 0.002), ("mrbn", 0.002))}
        orgn = sum(orgn_parts.values())
        etc_corp = np.round(np.random.normal(0, 1, n) * value / 1e6 * 0.006)
        inv = pd.DataFrame({"date": d.strftime("%Y%m%d"), "code": codes, "stck_clpr": np.round(price),
                            "frgn_ntby_tr_pbmn": frgn, "orgn_ntby_tr_pbmn": orgn, "etc_corp_ntby_tr_pbmn": etc_corp,
                            **{f"{k}_ntby_tr_pbmn": v for k, v in orgn_parts.items()}})
        inv["prsn_ntby_tr_pbmn"] = -(inv["frgn_ntby_tr_pbmn"] + inv["orgn_ntby_tr_pbmn"] + inv["etc_corp_ntby_tr_pbmn"])
        for k in ("frgn", "orgn", "prsn"):
            inv[f"{k}_ntby_qty"] = np.round(inv[f"{k}_ntby_tr_pbmn"] * 1e6 / price)
        store.write_clean("investor", d, inv)
        if di >= days - 25:
            store.write_clean("universe", d, uni)
        if di >= days - 40:
            wc = [codes[0], codes[2], codes[3], codes[9], codes[30], codes[45]]
            idx = [codes.index(c) for c in wc]
            store.write_clean("short", d, pd.DataFrame({"date": d.strftime("%Y%m%d"), "code": wc,
                                                        "ssts_vol_rlim": np.round(np.random.uniform(0.5, 6, len(wc)), 2)}))
            store.write_clean("credit", d, pd.DataFrame({"date": d.strftime("%Y%m%d"), "code": wc,
                                                         "whol_loan_rmnd_rate": np.round(np.random.uniform(0.5, 4, len(wc)), 2)}))
        for _ in range(rnd.randint(2, 5)):
            i = rnd.randrange(n)
            nm = rnd.choice(["분기보고서", "주요사항보고서(자기주식취득결정)", "단일판매ㆍ공급계약체결",
                             "임원ㆍ주요주주특정증권등소유상황보고서", "주식등의대량보유상황보고서",
                             "영업(잠정)실적(공정공시)", "기업설명회(IR)개최"])
            rno += 1
            disclosures.append((str(rno), d, codes[i], names[i], nm))
    # 가상 지수 (가상 종목 평균으로)
    lvl = {"KOSPI": 2600.0, "KOSDAQ": 850.0}
    for d in tdays:
        px = store.read_clean("prices_krx", d)
        rows_i = []
        for fam, nm_ in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
            sub = px[px["market"] == fam]
            r = float(sub["change_pct"].mean()) / 100
            lvl[fam] *= 1 + r
            rows_i.append({"date": d.strftime("%Y%m%d"), "family": fam, "name": nm_, "close": round(lvl[fam], 2),
                           "change_pct": round(r * 100, 2)})
        store.write_clean("index", d, pd.DataFrame(rows_i))
    special = [
        (codes[2], material_codes[codes[2]], "단일판매ㆍ공급계약체결"),
        (codes[11], material_codes[codes[11]] - dt.timedelta(days=1), "영업(잠정)실적(공정공시)"),
        (codes[0], tdays[-8], "주요사항보고서(유상증자결정)"),
        (codes[1], tdays[-15], "전환가액의조정"),
        (codes[4], tdays[-5], "임원ㆍ주요주주특정증권등거래계획보고서"),
        (codes[5], tdays[-30], "관리종목지정(감사의견 의견거절)"),
    ]
    for c, d, nm in special:
        rno += 1
        disclosures.append((str(rno), d, c, names[codes.index(c)], nm))
    with store.db() as con:
        for r, d, c, nm_corp, rep in disclosures:
            seen = dt.datetime.combine(d, dt.time(rnd.randint(8, 18), rnd.randint(0, 59)))
            con.execute("INSERT OR IGNORE INTO disclosures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r, d.strftime("%Y%m%d"), "00" + c, nm_corp, c, "Y", rep, nm_corp, d.strftime("%Y%m%d"), "",
                         iso_kst(seen), ",".join(classify(rep))))
        watch = [(codes[0], "보유", 120, None), (codes[3], "관심", None, None), (codes[2], "보유", 40, None),
                 (codes[30], "관심", None, None), (codes[45], "보유", 300, None), (codes[9], "관심", None, None)]
        last = store.read_clean("prices_krx", tdays[-1]).set_index("code")
        for c, kind, qty, _ in watch:
            avg = float(last.loc[c, "close"]) * rnd.uniform(0.85, 1.12) if qty else None
            con.execute("INSERT OR REPLACE INTO watchlist VALUES (?,?,?,?,?,?,?)",
                        (c, names[codes.index(c)], kind, qty, round(avg) if avg else None, "샘플", iso_kst()))
    for d in tdays[-30:]:
        df = outlook.compute(store, d)
        if not df.empty:
            store.write_clean("outlook", d, df)
    from .analysis import forecast as fcmod
    watch_codes = [r["code"] for r in store.watchlist()]
    fc = fcmod.forecast_many(store, watch_codes, with_backtest=True)
    if not fc.empty:
        t = fc["target"].iloc[0]
        fc["target"] = fc["target"].astype(str)
        fc["base_day"] = fc["base_day"].astype(str)
        store.write_clean("forecast", t, fc)
    for d in tdays[-12:]:
        rid = store.start_run("daily", d)
        for item, cnt in [("universe", n), ("krx_prices", n), ("krx_index", 40), ("nxt_prices", len(nxt_set)),
                          ("investor", n), ("disclosures", 40), ("watch_short_credit", 6), ("outlook", n)]:
            st = "fail" if (d == tdays[-4] and item == "krx_prices") else "ok"
            store.item(rid, item, st, None if st == "fail" else cnt, item not in ("universe", "nxt_prices"),
                       "빈 응답 (실패로 처리)" if st == "fail" else "")
        store.finish_run(rid, "partial" if d == tdays[-4] else "ok",
                         {"universe": {"status": "ok", "count": n}, "krx_prices": {"status": "ok", "count": n}})
        if d == tdays[-4]:
            rid2 = store.start_run("daily", d)
            store.item(rid2, "krx_prices", "ok", n, True, "다음 날 아침 재시도 성공")
            store.finish_run(rid2, "ok", {"krx_prices": {"status": "ok", "count": n}})
    for job in ("dart_poll", "morning", "backup"):
        rid = store.start_run(job, tdays[-1])
        store.finish_run(rid, "ok", {})
    for api in ("kis", "kis_master", "krx", "dart", "telegram", "google_news", "overseas"):
        store.api_mark(api, True, note="auth=header" if api == "krx" else None)
    store.api_mark("datagokr", False, "SERVICE_KEY_IS_NOT_REGISTERED_ERROR (샘플)")
    store.add_alert("위험", f"⛔ [관심] {names[3]}({codes[3]})\n재료 없는 급등 → 매수 금지", codes[3], "demo1")
    store.add_alert("공시", f"🚨 [보유] {names[0]}({codes[0]}) 공시\n주요사항보고서(유상증자결정)\n분류: 희석", codes[0], "demo2")
    for a in store.unsent_alerts():
        store.mark_sent(a["id"])
    return f"샘플 데이터 {len(tdays)}거래일 · {n}종목 → {s.data_dir}"
