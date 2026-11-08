"""저장소.

- raw/YYYY-MM-DD/   API 응답 원본. 한 번 쓰면 고치지 않는다. 같은 이름이면 .v2, .v3 로 추가.
- clean/<표>/YYYY-MM-DD[.vN].parquet   분석용 정리본. 읽을 때는 그날의 가장 최신 버전.
- ops.sqlite        수집 기록, 공시 처음 본 시각, 관심 종목, 알림, API 상태.

코인 프로젝트에서 실제로 문제가 된 것들을 막는 규칙:
  덮어쓰기 금지, 수집 시각 기록, 빈 결과는 실패, 종목코드는 문자열.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .timeutil import iso_kst

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job TEXT NOT NULL,
  day TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,          -- running, ok, partial, fail, skipped
  summary TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS job_runs_job_day ON job_runs(job, day);
CREATE TABLE IF NOT EXISTS item_results (
  run_id INTEGER NOT NULL,
  item TEXT NOT NULL,
  status TEXT NOT NULL,          -- ok, fail, skip, warn
  count INTEGER,
  recoverable INTEGER NOT NULL DEFAULT 1,
  message TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS item_results_run ON item_results(run_id);
CREATE TABLE IF NOT EXISTS disclosures (
  rcept_no TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  corp_code TEXT, corp_name TEXT, stock_code TEXT, corp_cls TEXT,
  report_nm TEXT, flr_nm TEXT, rcept_dt TEXT, rm TEXT,
  first_seen_at TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS disclosures_stock ON disclosures(stock_code, rcept_dt);
CREATE INDEX IF NOT EXISTS disclosures_day ON disclosures(day);
CREATE TABLE IF NOT EXISTS api_status (
  api TEXT PRIMARY KEY,
  last_ok_at TEXT, last_fail_at TEXT, last_error TEXT,
  calls_day TEXT, calls_today INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
  code TEXT PRIMARY KEY,
  name TEXT,
  kind TEXT NOT NULL DEFAULT '관심',   -- 보유, 관심
  qty REAL, avg_price REAL, memo TEXT,
  added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  code TEXT,
  dedupe_key TEXT UNIQUE,
  message TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS news_cache (
  code TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  items TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS key_changes (
  at TEXT NOT NULL, api TEXT NOT NULL, keys TEXT NOT NULL, source TEXT
);
"""

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _check_name(name: str) -> None:
    if not _SAFE.match(name):
        raise ValueError(f"잘못된 이름: {name}")


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.clean_dir = self.data_dir / "clean"
        self.secrets_dir = self.data_dir / "secrets"
        self.backup_dir = self.data_dir / "backups"
        self.ops_path = self.data_dir / "ops.sqlite"
        for d in (self.raw_dir, self.clean_dir, self.secrets_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)
        try:
            self.secrets_dir.chmod(0o700)
        except OSError:
            pass
        with self.db() as con:
            con.executescript(SCHEMA)

    # ---------- sqlite ----------
    @contextmanager
    def db(self):
        con = sqlite3.connect(self.ops_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def query(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self.db() as con:
            return [dict(r) for r in con.execute(sql, tuple(args)).fetchall()]

    # ---------- raw ----------
    def _versioned(self, base: Path, stem: str, suffix: str) -> Path:
        p = base / f"{stem}{suffix}"
        n = 2
        while p.exists():
            p = base / f"{stem}.v{n}{suffix}"
            n += 1
        return p

    def write_raw(self, day: dt.date, name: str, obj: Any) -> Path:
        """JSON 직렬화 가능한 응답 원본을 gzip으로 저장. 기존 파일은 절대 덮어쓰지 않는다."""
        _check_name(name)
        base = self.raw_dir / day.isoformat()
        base.mkdir(parents=True, exist_ok=True)
        p = self._versioned(base, name, ".json.gz")
        payload = {"collected_at": iso_kst(), "data": obj}
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return p

    def write_raw_bytes(self, day: dt.date, name: str, data: bytes, suffix: str) -> Path:
        _check_name(name)
        base = self.raw_dir / day.isoformat()
        base.mkdir(parents=True, exist_ok=True)
        p = self._versioned(base, name, suffix)
        p.write_bytes(data)
        return p

    # ---------- clean ----------
    def write_clean(self, table: str, day: dt.date, df: pd.DataFrame) -> Path:
        _check_name(table)
        base = self.clean_dir / table
        base.mkdir(parents=True, exist_ok=True)
        df = df.copy()
        if "collected_at" not in df.columns:
            df["collected_at"] = iso_kst()
        p = self._versioned(base, day.isoformat(), ".parquet")
        df.to_parquet(p, index=False)
        return p

    def _latest_file(self, table: str, day: dt.date) -> Path | None:
        base = self.clean_dir / table
        if not base.exists():
            return None
        files = list(base.glob(f"{day.isoformat()}.parquet")) + list(base.glob(f"{day.isoformat()}.v*.parquet"))
        if not files:
            return None

        def ver(p: Path) -> int:
            m = re.search(r"\.v(\d+)\.parquet$", p.name)
            return int(m.group(1)) if m else 1

        return max(files, key=ver)

    def read_clean(self, table: str, day: dt.date) -> pd.DataFrame | None:
        p = self._latest_file(table, day)
        if p is None:
            return None
        return pd.read_parquet(p)

    def clean_days(self, table: str) -> list[dt.date]:
        base = self.clean_dir / table
        if not base.exists():
            return []
        days = set()
        for p in base.glob("*.parquet"):
            try:
                days.add(dt.date.fromisoformat(p.name[:10]))
            except ValueError:
                continue
        return sorted(days)

    def latest_clean_day(self, table: str) -> dt.date | None:
        days = self.clean_days(table)
        return days[-1] if days else None

    def read_range(self, table: str, days: Iterable[dt.date]) -> pd.DataFrame:
        frames = []
        for d in days:
            df = self.read_clean(table, d)
            if df is not None and len(df):
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # ---------- job runs ----------
    def start_run(self, job: str, day: dt.date) -> int:
        with self.db() as con:
            cur = con.execute(
                "INSERT INTO job_runs(job, day, started_at, status) VALUES (?,?,?,?)",
                (job, day.isoformat(), iso_kst(), "running"),
            )
            return int(cur.lastrowid)

    def item(self, run_id: int, item: str, status: str, count: int | None = None,
             recoverable: bool = True, message: str = "") -> None:
        with self.db() as con:
            con.execute(
                "INSERT INTO item_results(run_id, item, status, count, recoverable, message, at) VALUES (?,?,?,?,?,?,?)",
                (run_id, item, status, count, 1 if recoverable else 0, message[:500], iso_kst()),
            )

    def finish_run(self, run_id: int, status: str, summary: dict | None = None, error: str = "") -> None:
        with self.db() as con:
            con.execute(
                "UPDATE job_runs SET finished_at=?, status=?, summary=?, error=? WHERE id=?",
                (iso_kst(), status, json.dumps(summary or {}, ensure_ascii=False), error[:2000], run_id),
            )

    def last_run(self, job: str, day: dt.date | None = None) -> dict | None:
        if day:
            rows = self.query("SELECT * FROM job_runs WHERE job=? AND day=? ORDER BY id DESC LIMIT 1", (job, day.isoformat()))
        else:
            rows = self.query("SELECT * FROM job_runs WHERE job=? ORDER BY id DESC LIMIT 1", (job,))
        return rows[0] if rows else None

    def run_items(self, run_id: int) -> list[dict]:
        return self.query("SELECT * FROM item_results WHERE run_id=? ORDER BY rowid", (run_id,))

    def latest_item_status(self, job: str, day: dt.date) -> dict[str, str]:
        """그날 같은 작업의 여러 실행(본 실행 + 재시도)을 합쳐 항목별 마지막 상태."""
        rows = self.query(
            """SELECT i.item, i.status FROM item_results i JOIN job_runs r ON r.id = i.run_id
               WHERE r.job=? AND r.day=? ORDER BY i.run_id, i.rowid""", (job, day.isoformat()))
        out: dict[str, str] = {}
        for r in rows:
            out[r["item"]] = r["status"]
        return out

    def last_item_count(self, item: str, before_run: int) -> int | None:
        rows = self.query(
            "SELECT count FROM item_results WHERE item=? AND status='ok' AND run_id<? ORDER BY run_id DESC LIMIT 1",
            (item, before_run),
        )
        return rows[0]["count"] if rows else None

    # ---------- api status ----------
    def api_mark(self, api: str, ok: bool, error: str = "", note: str | None = None) -> None:
        now = iso_kst()
        today = now[:10]
        with self.db() as con:
            row = con.execute("SELECT calls_day, calls_today FROM api_status WHERE api=?", (api,)).fetchone()
            calls = (row["calls_today"] + 1) if row and row["calls_day"] == today else 1
            if row is None:
                con.execute("INSERT INTO api_status(api, calls_day, calls_today) VALUES (?,?,0)", (api, today))
            if ok:
                con.execute("UPDATE api_status SET last_ok_at=?, calls_day=?, calls_today=? WHERE api=?",
                            (now, today, calls, api))
            else:
                con.execute("UPDATE api_status SET last_fail_at=?, last_error=?, calls_day=?, calls_today=? WHERE api=?",
                            (now, error[:500], today, calls, api))
            if note is not None:
                con.execute("UPDATE api_status SET note=? WHERE api=?", (note, api))

    def api_status(self) -> dict[str, dict]:
        return {r["api"]: r for r in self.query("SELECT * FROM api_status")}

    # ---------- alerts ----------
    def add_alert(self, kind: str, message: str, code: str | None = None, dedupe_key: str | None = None) -> int | None:
        """같은 dedupe_key가 이미 있으면 None (중복 알림 방지)."""
        with self.db() as con:
            try:
                cur = con.execute(
                    "INSERT INTO alerts(created_at, kind, code, dedupe_key, message) VALUES (?,?,?,?,?)",
                    (iso_kst(), kind, code, dedupe_key, message),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def unsent_alerts(self) -> list[dict]:
        return self.query("SELECT * FROM alerts WHERE sent=0 ORDER BY id")

    def mark_sent(self, alert_id: int) -> None:
        with self.db() as con:
            con.execute("UPDATE alerts SET sent=1 WHERE id=?", (alert_id,))

    # ---------- watchlist ----------
    def watchlist(self) -> list[dict]:
        return self.query("SELECT * FROM watchlist ORDER BY CASE kind WHEN '보유' THEN 0 ELSE 1 END, added_at")

    def watch_codes(self) -> list[str]:
        return [r["code"] for r in self.query("SELECT code FROM watchlist")]

    def upsert_watch(self, code: str, name: str, kind: str, qty: float | None, avg_price: float | None, memo: str) -> None:
        code = normalize_code(code)
        if kind not in ("보유", "관심"):
            raise ValueError("kind는 보유 또는 관심")
        with self.db() as con:
            con.execute(
                """INSERT INTO watchlist(code, name, kind, qty, avg_price, memo, added_at) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET name=excluded.name, kind=excluded.kind, qty=excluded.qty,
                   avg_price=excluded.avg_price, memo=excluded.memo""",
                (code, name, kind, qty, avg_price, memo, iso_kst()),
            )

    def remove_watch(self, code: str) -> None:
        with self.db() as con:
            con.execute("DELETE FROM watchlist WHERE code=?", (normalize_code(code),))


def normalize_code(code: Any) -> str:
    """종목코드는 항상 6자리 문자열. 숫자로 읽혀 앞의 0이 빠진 경우도 복구."""
    s = str(code).strip()
    if s.startswith("A") and len(s) == 7:
        s = s[1:]
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)
    if not re.match(r"^[0-9A-Z]{6}$", s):
        raise ValueError(f"종목코드 형식이 아닙니다: {code!r}")
    return s
