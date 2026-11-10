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
  set-password                         대시보드 비밀번호 새로 만들기 (로그인 잠금도 풂)
  unlock                               로그인 잠금 풀기 (새 휴대폰이 '실패가 많아 잠김'일 때)
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
    from .redact import setup_logging
    setup_logging()
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
    sub.add_parser("set-password")
    sub.add_parser("unlock")
    p = sub.add_parser("notify")
    p.add_argument("text")
    p = sub.add_parser("web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8020)
    p = sub.add_parser("gibalhae-sync")
    p.add_argument("--src", help="초안 폴더 (기본: 저장소의 gibalhae/content)")
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
        # 같은 서버의 Caddy(127.0.0.1)가 붙이는 X-Forwarded-* 만 믿는다. waitress 3은 이 설정이 없으면
        # 그 헤더를 지워 버려서 모든 접속자가 127.0.0.1 한 사람으로 보인다(로그인 잠금이 전원에게 걸림).
        serve(app, host=args.host, port=args.port, threads=8, ident="bkk-hub",
              trusted_proxy="127.0.0.1", trusted_proxy_count=1,
              trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
              clear_untrusted_proxy_headers=True)
        return
    if args.cmd == "set-password":
        import secrets
        from .config import get_settings, write_env_values
        from .store import Store
        pw = secrets.token_urlsafe(12)
        write_env_values(get_settings().env_file, {"DASH_PASSWORD": pw, "DASH_SECRET": secrets.token_hex(32)})
        Store(get_settings().data_dir).clear_login_fails()
        print(f"새 대시보드 비밀번호: {pw}")
        print("적용: sudo systemctl restart bkk-hub-web   (기존 로그인은 모두 풀립니다)")
        return
    if args.cmd == "unlock":
        from .config import get_settings
        from .store import Store
        n = Store(get_settings().data_dir).clear_login_fails()
        print(f"로그인 실패 기록 {n}건을 지웠습니다. 바로 다시 로그인할 수 있습니다.")
        return
    if args.cmd == "gibalhae-sync":
        from pathlib import Path
        from .gibalhae_sync import REPO_CONTENT, sync, target_dir
        copied = sync(Path(args.src) if args.src else REPO_CONTENT)
        print(f"{len(copied)}개 파일 → {target_dir()}")
        return
    if args.cmd == "demo-seed":
        from .demo import seed
        print(seed(days=args.days))
        return

    from .jobs.common import AlreadyRunning, make_ctx
    ctx = make_ctx()
    day = dt.date.fromisoformat(args.day) if getattr(args, "day", None) else None
    hc = args.cmd if args.cmd in HC_JOBS else None
    _hc_ping(ctx, hc, "start")
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
        _hc_ping(ctx, hc, "0")
        sys.exit(0)
    except BaseException:
        _hc_ping(ctx, hc, "1")
        raise
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    st = res.get("status") if isinstance(res, dict) else None
    bad = st == "fail" or (args.cmd in ("morning", "report", "watchdog") and st == "partial")
    _hc_ping(ctx, hc, "1" if bad else "0")
    if bad:
        sys.exit(1)


# 서버 밖 '죽음 감지' (healthchecks.io 등). .env 의 HC_PING_URL=https://hc-ping.com/<ping-key> 이면
# 작업마다 <주소>/<작업이름>/start · /0(성공) · /1(실패)을 보낸다. 핑이 안 오면 그쪽에서 메일로 알린다.
# 텔레그램이 없어도, 서버가 통째로 죽어도 알 수 있는 길. 실패해도 작업에는 영향이 없다.
HC_JOBS = {"daily", "morning", "report", "watchdog", "dart-poll", "backfill"}


def _hc_ping(ctx, job: str | None, what: str) -> None:
    base = (ctx.settings.get("HC_PING_URL") or "").rstrip("/")
    if not job or not base.startswith("https://"):
        return
    try:
        import httpx
        url = f"{base}/bkk-{job}" + ("/start" if what == "start" else f"/{what}")
        httpx.get(url, params={"create": "1"}, timeout=10)
    except Exception:  # noqa: BLE001
        pass
