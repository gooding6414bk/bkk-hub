from __future__ import annotations

import time

import httpx

from ..config import Settings
from ..store import Store
from .base import ApiError, NotConfigured, make_http


class Telegram:
    api = "telegram"

    def __init__(self, settings: Settings, store: Store, http: httpx.Client | None = None):
        self.s = settings
        self.store = store
        self.http = http or make_http(15)
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return self.s.has("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    def send(self, text: str) -> None:
        if not self.configured:
            raise NotConfigured(self.api, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
        for chunk in _chunks(text, 3800):
            wait = 1.1 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.http.post(f"https://api.telegram.org/bot{self.s.get('TELEGRAM_BOT_TOKEN')}/sendMessage",
                                   json={"chat_id": self.s.get("TELEGRAM_CHAT_ID"), "text": chunk,
                                         "disable_web_page_preview": True})
            except httpx.HTTPError as e:      # 연결 오류도 ApiError로 — 보고 뒤의 백업이 건너뛰어지지 않게
                self.store.api_mark(self.api, False, f"연결 오류 {type(e).__name__}")
                raise ApiError(self.api, "network", type(e).__name__, retryable=True) from e
            self._last = time.monotonic()
            ok = r.status_code == 200 and r.json().get("ok")
            if not ok:
                desc = ""
                try:
                    desc = r.json().get("description", "")
                except ValueError:
                    desc = r.text[:150]
                self.store.api_mark(self.api, False, f"{r.status_code} {desc}")
                raise ApiError(self.api, str(r.status_code), desc)
            self.store.api_mark(self.api, True)

    def test(self) -> str:
        self.send("✅ bkk-stock 텔레그램 연결 테스트")
        return "테스트 메시지를 보냈습니다"


def _chunks(text: str, n: int) -> list[str]:
    if len(text) <= n:
        return [text]
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > n:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out
