from __future__ import annotations

import threading
import time

import httpx

from ..redact import redact

USER_AGENT = "bkk-stock/0.1 (+personal data collector)"


class ApiError(Exception):
    def __init__(self, api: str, code: str, message: str, retryable: bool = False):
        message = redact(message)   # 연결 오류 문구에 키가 실린 주소가 섞여도 화면·기록에 남지 않게
        super().__init__(f"[{api}] {code}: {message}")
        self.api = api
        self.code = code
        self.message = message
        self.retryable = retryable


class NotConfigured(ApiError):
    def __init__(self, api: str, keys: list[str]):
        super().__init__(api, "not_configured", "키가 설정되지 않았습니다: " + ", ".join(keys))


class RateLimiter:
    """초당 호출 수 제한. 여러 스레드에서 같이 써도 된다."""

    def __init__(self, per_sec: float):
        self.interval = 1.0 / max(per_sec, 0.1)
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._next > now:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval


def make_http(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
