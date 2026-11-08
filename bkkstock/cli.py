"""명령줄 진입점.  python -m bkkstock <명령>

  daily [--day YYYY-MM-DD] [--force]   20:30 일일 수집
  dart-poll                            새 공시 조회 (10분마다)
  morning                              07:30 아침 작업
  report                               21:00 보고 + 백업
  watchdog                             21:40 감시
  backfill [--days 90]                 과거 시세 채우기 (설치 직후 한 번)
  first-week-check                     첫 주 점검
  test-apis                            API 연결 확인만
  notify "문구"                        텔레그램으로 한 줄 보내기 (배포 스크립트용)
  web [--host 127.0.0.1] [--port 8020] 통합 보드 (주식 + 기발해)
  gibalhae-sync                        저장소의 새 기발해 초안을 서버 데이터 폴더로 복사
  demo-seed                            샘플 데이터 만들기 (화면 미리보기용)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="bkkstock")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("daily")
    p.add_argument("--day")
    p.add_argument("--force", action="store_true")
    sub.add_parser("dart-poll")
    sub.add_parser("morning")
    p = sub.add_parser("report")
    p.add_argument("--day")
    sub.add_parser("watchdog")
    p = sub.add_parser("backfill")
    p.add_argument("--days", type=int, default=90)
    sub.add_parser("first-week-check")
    sub.add_parser("test-apis")
    p = sub.add_parser("notify")
    p.add_argument("text")
    p = sub.add_parser("web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8020)
    sub.add_parser("gibalhae-sync")
    p = sub.add_parser("demo-seed")
    p.add_argument("--days", type=int, default=70)
    args = ap.parse_args(argv)

    if args.cmd == "web":
        # 기발해 작업 기록이 프로세스 메모리에 있어서 단일 프로세스·여러 스레드로 띄운다 (건우아비 보드와 같은 방식)
        from waitress import serve
        from .gibalhae_sync import sync
        copied = sync()
        if copied:
            logging.info("기발해 초안 %d개 파일을 데이터 폴더로 가져옴", len(copied))
        from .web.app import app
        serve(app, host=args.host, port=args.port, threads=8, ident="bkk-hub")
        return
    if args.cmd == "gibalhae-sync":
        from .gibalhae_sync import sync, target_dir
        copied = sync()
        print(f"{len(copied)}개 파일 → {target_dir()}")
        return
    if args.cmd == "demo-seed":
        from .demo import seed
        print(seed(days=args.days))
        return

    from .jobs.common import AlreadyRunning, make_ctx
    ctx = make_ctx()
    day = dt.date.fromisoformat(args.day) if getattr(args, "day", None) else None
    try:
        if args.cmd == "daily":
            from .jobs import daily
            res = daily.run(ctx, day, force=args.force)
        elif args.cmd == "dart-poll":
            from .jobs import dart_poll
            res = dart_poll.run(ctx)
        elif args.cmd == "morning":
            from .jobs import morning
            res = morning.run(ctx)
        elif args.cmd == "report":
            from .jobs import report
            res = report.run(ctx, day)
        elif args.cmd == "watchdog":
            from .jobs import watchdog
            res = watchdog.run(ctx)
        elif args.cmd == "backfill":
            from .jobs import backfill
            res = backfill.run(ctx, days=args.days)
        elif args.cmd == "notify":
            if ctx.tg.configured:
                ctx.tg.send(args.text)
            res = {"sent": ctx.tg.configured}
        elif args.cmd in ("first-week-check", "test-apis"):
            from .jobs import first_week
            rows = first_week.run(ctx)
            print(first_week.format_report(rows))
            if args.cmd == "test-apis":
                return
            sys.exit(0 if all(s != "fail" for _, s, _ in rows) else 1)
        else:
            ap.error(args.cmd)
            return
    except AlreadyRunning as e:
        print(f"이미 실행 중: {e}")
        sys.exit(0)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    if isinstance(res, dict) and res.get("status") == "fail":
        sys.exit(1)
