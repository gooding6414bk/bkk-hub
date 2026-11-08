"""백업: 운영 DB 스냅샷 + 그날 원본 묶음. 로컬 14일 보관. BACKUP_CMD가 있으면 서버 밖으로 복사.

BACKUP_CMD 예: rclone copy {dir} gdrive:bkk-stock-backup
  ({dir}는 백업 폴더 경로로 바뀐다. 셸을 거치지 않고 실행한다.)
"""

from __future__ import annotations

import datetime as dt
import gzip
import re
import shlex
import shutil
import sqlite3
import subprocess
import tarfile

from .common import Ctx

_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{8})")

KEEP_DAYS = 14


def run(ctx: Ctx, day: dt.date) -> dict:
    bdir = ctx.store.backup_dir
    out: dict = {}
    tmp = bdir / f"ops-{day:%Y%m%d}.sqlite"
    src = sqlite3.connect(ctx.store.ops_path)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    gz = tmp.with_suffix(".sqlite.gz")
    with open(tmp, "rb") as f, gzip.open(gz, "wb") as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    out["ops"] = gz.name
    raw_day = ctx.store.raw_dir / day.isoformat()
    if raw_day.exists():
        tar = bdir / f"raw-{day.isoformat()}.tar"
        with tarfile.open(tar, "w") as t:   # 원본은 이미 gzip이라 다시 압축하지 않는다
            t.add(raw_day, arcname=raw_day.name)
        out["raw"] = tar.name
    cutoff = day - dt.timedelta(days=KEEP_DAYS)
    for p in bdir.iterdir():
        m = _STAMP.search(p.name)
        if not m:
            continue
        stamp = m.group(1).replace("-", "")
        try:
            d = dt.datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            continue
        if d < cutoff:
            p.unlink()
    cmd = ctx.settings.get("BACKUP_CMD")
    if cmd:
        args = [a.replace("{dir}", str(bdir)) for a in shlex.split(cmd)]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=1800)
            out["remote"] = "ok" if r.returncode == 0 else f"실패 {r.returncode}: {r.stderr[-200:]}"
        except (OSError, subprocess.TimeoutExpired) as e:
            out["remote"] = f"실패: {e}"
    else:
        out["remote"] = "BACKUP_CMD 미설정 (서버 밖 백업 없음)"
    status = "ok" if out.get("remote", "").startswith(("ok", "BACKUP_CMD")) else "partial"
    rid = ctx.store.start_run("backup", day)
    ctx.store.finish_run(rid, status, out)
    return out
