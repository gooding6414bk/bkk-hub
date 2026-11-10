"""설정과 비밀키.

키는 서버의 .env 파일(권한 600)에만 둔다. 코드와 git에는 넣지 않는다.
대시보드의 API 관리 페이지도 이 파일을 고쳐 쓴다.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

# .env에 들어갈 수 있는 키 전체. 여기 없는 이름은 대시보드에서 쓸 수 없다.
KNOWN_KEYS = [
    "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT", "KIS_ACCOUNT_PROD",
    "KRX_OPENAPI_KEY",
    "DART_API_KEY",
    "DATA_GO_KR_KEY",
    "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "DASH_PASSWORD", "DASH_SECRET",
    "BACKUP_CMD",
    "INVESTOR_SCOPE",
    "KIS_RATE_PER_SEC",
    "HC_PING_URL",
]

_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        # 줄 끝 주석 제거 (따옴표 밖일 때만)
        if not (val.startswith('"') or val.startswith("'")) and " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def env_path() -> Path:
    return Path(os.environ.get("BKK_STOCK_ENV", REPO_DIR / ".env"))


def write_env_values(path: Path, updates: dict[str, str]) -> None:
    """기존 줄과 주석은 그대로 두고 지정한 키만 바꾼다. 원자적으로 쓰고 권한 600."""
    for k in updates:
        if k not in KNOWN_KEYS:
            raise ValueError(f"알 수 없는 키 이름: {k}")
        if "\n" in updates[k] or "\r" in updates[k]:
            raise ValueError(f"{k}: 줄바꿈은 넣을 수 없습니다")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    out: list[str] = []
    for raw in lines:
        m = _LINE.match(raw.strip()) if not raw.strip().startswith("#") else None
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(raw)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}={v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return "•" * 6 + value[-4:]


@dataclass
class Settings:
    env_file: Path
    data_dir: Path
    values: dict[str, str] = field(default_factory=dict)
    demo: bool = False

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default) or default

    def has(self, *keys: str) -> bool:
        return all(bool(self.values.get(k)) for k in keys)

    @property
    def kis_rate(self) -> float:
        try:
            return max(1.0, min(18.0, float(self.get("KIS_RATE_PER_SEC", "12"))))
        except ValueError:
            return 12.0

    @property
    def investor_scope(self) -> str:
        v = self.get("INVESTOR_SCOPE", "all").lower()
        return v if v in ("all", "watchlist") else "all"


_settings: Settings | None = None


def load_settings() -> Settings:
    path = env_path()
    values: dict[str, str] = {}
    if path.exists():
        values.update(parse_env_text(path.read_text(encoding="utf-8")))
    # 환경변수가 파일보다 우선 (systemd EnvironmentFile 등)
    for k in KNOWN_KEYS:
        if os.environ.get(k):
            values[k] = os.environ[k]
    data_dir = Path(os.environ.get("BKK_STOCK_DATA", REPO_DIR / "data"))
    demo = os.environ.get("BKK_STOCK_DEMO", "") == "1"
    return Settings(env_file=path, data_dir=data_dir, values=values, demo=demo)


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        _settings = load_settings()
    return _settings
