"""로그·오류 문구에서 키 값을 가린다.

DART(crtfc_key)·공공데이터(serviceKey)·KRX(AUTH_KEY)는 주소에 키를 싣는다. httpx가 주소를 통째로 찍거나
예외 문구에 주소가 섞이면 journald·운영 화면에 키가 남으므로, 남기기 전에 여기서 가린다.
"""

from __future__ import annotations

import logging
import re

_PARAM = re.compile(r"(?i)\b(crtfc_key|servicekey|auth_key|appkey|appsecret|secretkey|access_token|token|key)=([^&\s'\"]+)")
_BOT = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_HEADER = re.compile(r"(?i)\b(authorization|appsecret|appkey|auth_key)(['\"]?\s*[:=]\s*['\"]?)(bearer\s+)?([A-Za-z0-9._\-+/=]{8,})")


def redact(text: object) -> str:
    s = str(text)
    s = _PARAM.sub(lambda m: f"{m.group(1)}=•••", s)
    s = _BOT.sub("/bot•••", s)
    s = _HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}•••", s)
    return s


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        clean = redact(msg)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if clean != msg or record.args:
            record.msg, record.args = clean, ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """루트 로거를 켜되, 주소를 통째로 찍는 httpx·httpcore는 경고 이상만 남기고 모든 출력에서 키를 가린다."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    f = RedactFilter()
    for h in logging.getLogger().handlers:
        if not any(isinstance(x, RedactFilter) for x in h.filters):
            h.addFilter(f)
