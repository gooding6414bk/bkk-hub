"""📊 지표 수집: 한국은행 ECOS(금리·환율·물가) + 야후(미국채 10년·VIX·달러인덱스).

새 타이머 없이 일일 수집(평일 20:30) 끝과 아침 작업(07:30·08:30)에 얹는다. 실패해도 다른 수집·healthz 에는
영향이 없고, 화면에 '기준일'과 '지연'으로만 보인다. 매번 1년치(물가는 2년치)를 통째로 다시 받아
그날 파일 하나에 긴 형식으로 저장한다 → 개정된 값도 저절로 반영된다.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..clients.base import ApiError
from ..clients.ecos import SERIES as ECOS_SERIES
from ..clients.fred import SERIES as FRED_SERIES
from ..clients.overseas import MACRO_SYMBOLS
from ..timeutil import iso_kst, now_kst
from .common import Ctx

YAHOO_SERIES = {"^TNX": ("us_10y", "%"), "^VIX": ("vix", ""), "DX-Y.NYB": ("dxy", ""), "^GSPC": ("spx", ""),
                "^IXIC": ("nasdaq", "")}


def collect(ctx: Ctx) -> dict:
    today = now_kst().date()
    rows: list[dict] = []
    fetched = iso_kst()
    msgs, bad = [], []
    if ctx.ecos.configured:
        for name, (_stat, cycle, _item, label, unit) in ECOS_SERIES.items():
            start = today - dt.timedelta(days=760 if cycle == "M" else 400)
            try:
                for r in ctx.ecos.series(name, start, today):
                    rows.append({"source": "ecos", "series": name, "label": label, "unit": unit, "freq": cycle,
                                 "period": r["period"], "value": r["value"], "fetched_at": fetched})
            except ApiError as e:
                bad.append(name)
                msgs.append(f"{name}: {e.code}")
    else:
        msgs.append("ECOS 키 없음 (한국 금리·환율·물가 건너뜀)")
    for sid, (name, cycle) in FRED_SERIES.items():       # 미국 거시 (키 없음)
        start = today - dt.timedelta(days=760 if cycle == "M" else 400)
        try:
            for r in ctx.fred.history(sid, start):
                period = r["period"][:7] if cycle == "M" else r["period"]
                rows.append({"source": "fred", "series": name, "label": sid, "unit": "", "freq": cycle,
                             "period": period, "value": r["value"], "fetched_at": fetched})
        except ApiError as e:
            bad.append(sid)
            msgs.append(f"{sid}: {e.code}")
    yrows, yfail = ctx.overseas.macro_history("1y")
    for r in yrows:
        name, unit = YAHOO_SERIES[r["symbol"]]
        rows.append({"source": "yahoo", "series": name, "label": MACRO_SYMBOLS[r["symbol"]], "unit": unit, "freq": "D",
                     "period": r["date"], "value": float(r["close"]), "fetched_at": fetched})
    bad += yfail
    if rows:
        ctx.store.write_raw(today, "macro", rows)
        ctx.store.write_clean("macro", today, pd.DataFrame(rows))
    status = "ok" if rows and not bad else ("partial" if rows else "fail")
    out = {"status": status, "rows": len(rows), "failed": bad, "note": " · ".join(msgs)}
    rid = ctx.store.start_run("macro", today)
    ctx.store.finish_run(rid, status, out)
    return out


def safe_collect(ctx: Ctx) -> str:
    """다른 작업에 얹어 부를 때: 어떤 오류도 밖으로 내보내지 않는다."""
    try:
        r = collect(ctx)
        return f"{r['status']} {r['rows']}행" + (f" · 실패 {', '.join(r['failed'])}" if r["failed"] else "")
    except Exception as e:  # noqa: BLE001
        return f"지표 수집 오류 {type(e).__name__}"
