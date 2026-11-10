"""첫 주 점검. 설치 직후와 첫 주에 한 번씩 돌린다.  python -m bkkstock first-week-check"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..clients.base import ApiError, NotConfigured
from ..clients.kis import to_int
from ..redact import redact
from ..timeutil import last_trading_day, now_kst, prev_trading_day, trading_days_back
from .common import Ctx


def run(ctx: Ctx) -> list[tuple[str, str, str]]:
    """(항목, 결과 ok/fail/skip, 설명)"""
    out: list[tuple[str, str, str]] = []
    today = now_kst().date()
    ref = prev_trading_day(today) if now_kst().hour < 21 else last_trading_day(today)

    def check(name, fn):
        try:
            out.append((name, "ok", fn()))
        except NotConfigured as e:
            out.append((name, "skip", e.message))
        except ApiError as e:
            out.append((name, "fail", redact(e)))
        except Exception as e:  # noqa: BLE001
            out.append((name, "fail", redact(f"{type(e).__name__}: {e}")))

    check("한국투자증권 토큰·시세", ctx.kis.test)
    check("종목 마스터 파일", lambda: f"코스피 {len(ctx.kis.download_master('KOSPI')):,}바이트")
    check("KRX Open API", lambda: ctx.krx.test(ref))
    # KRX 는 그날 시세를 다음 영업일 아침에 올린다. KRX 대조 검사는 자료가 있는 가장 최근 거래일로 한다
    krx_day: dict = {}

    def krx_latest():
        if "d" not in krx_day:
            krx_day["d"], krx_day["rows"] = ctx.krx.latest_trades(ref)
        return krx_day["d"], krx_day["rows"]

    check("KRX 지수", lambda: f"{krx_latest()[0]} {len(ctx.krx.index_daily('KOSPI', krx_latest()[0]))}개 지수")
    check("OpenDART (접수 시각 필드 확인)", lambda: ctx.dart.test(ref))
    check("공공데이터포털", lambda: ctx.datagokr.test(prev_trading_day(ref)))
    check("뉴스", ctx.news.test)
    check("텔레그램", ctx.tg.test)

    def kis_consistency():
        msgs = []
        for code in ("005930", "000660", "035420"):
            j = ctx.kis.daily_chart(code, ref, ref, "J")
            nx = ctx.kis.daily_chart(code, ref, ref, "NX")
            un = ctx.kis.daily_chart(code, ref, ref, "UN")
            v = lambda rows: (to_int(rows[0].get("acml_vol")) if rows else 0) or 0
            jv, nv, uv = v(j), v(nx), v(un)
            diff = (uv - (jv + nv)) / uv * 100 if uv else 0
            msgs.append(f"{code} KRX {jv:,} + NXT {nv:,} = {jv + nv:,} / 통합 {uv:,} ({diff:+.2f}%)")
        return " | ".join(msgs)
    check("KIS 통합 = KRX + NXT 대조", kis_consistency)

    def survivorship():
        day = krx_latest()[0]
        old = trading_days_back(day, 500)[0]
        then = ctx.krx.daily_trades("KOSDAQ", old)
        now = ctx.krx.daily_trades("KOSDAQ", day)
        if not now or not then:
            raise ApiError("krx", "empty", f"코스닥 시세가 비어 있어 비교할 수 없습니다 ({old}: {len(then)}행, {day}: {len(now)}행)")
        from .daily import short_code
        a = {short_code(r.get("ISU_CD")) for r in then}
        b = {short_code(r.get("ISU_CD")) for r in now}
        gone = a - b
        return (f"{old} 코스닥 {len(a):,}종목 중 {day}에 없는 종목 {len(gone):,}개도 그날 시세가 있음 "
                f"→ KRX 과거 데이터에 상장폐지 종목 포함")
    check("상장폐지 종목 과거 시세", survivorship)

    def krx_fields():
        _, rows = krx_latest()
        from ..clients.krx import TRADE_COLS
        have = set(rows[0].keys()) if rows else set()
        missing = [k for k in TRADE_COLS if k not in have]
        sample = rows[0].get("ISU_CD") if rows else ""
        if missing:
            raise ApiError("krx", "fields", f"예상한 필드가 없습니다: {missing} (실제: {sorted(have)[:20]})")
        return f"필드 이름 일치 · 종목코드 예시 {sample}"
    check("KRX 응답 필드 이름", krx_fields)
    return out


def format_report(rows: list[tuple[str, str, str]]) -> str:
    icon = {"ok": "✅", "fail": "❌", "skip": "⏭️"}
    return "\n".join(f"{icon.get(s, '?')} {n}: {m}" for n, s, m in rows)
