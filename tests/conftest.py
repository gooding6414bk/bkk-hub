import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from bkkstock.config import Settings
from bkkstock.store import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = tmp_path / ".env"
    env.write_text("# 테스트\nDASH_PASSWORD=pw\nDASH_SECRET=s\n")
    return Settings(env_file=env, data_dir=tmp_path / "data", values={"DASH_PASSWORD": "pw", "DASH_SECRET": "s"})


@pytest.fixture
def store(settings) -> Store:
    return Store(settings.data_dir)


def _stable(code: str) -> int:
    """hash()는 실행마다 바뀌어 테스트가 가끔 실패했다(가격이 일정해져 변동성 0). 고정값을 쓴다."""
    return sum(ord(ch) * (i + 1) for i, ch in enumerate(str(code)))


def make_prices(store: Store, days: list[dt.date], codes: list[str], rets: dict | None = None, base: float = 10000.0):
    """간단한 가격 이력. rets[(code, day)] = 그날 수익률."""
    px = {c: base for c in codes}
    for d in days:
        rows = []
        for c in codes:
            r = (rets or {}).get((c, d), 0.001 * (_stable(c) % 7 - 3) + 0.0005 * ((d.toordinal() + _stable(c)) % 5 - 2))
            px[c] *= 1 + r
            rows.append({"date": d.strftime("%Y%m%d"), "code": c, "name": f"종목{c}", "close": round(px[c]),
                         "change_pct": round(r * 100, 2), "volume": 1000, "value": 10_000_000 * (9 if r > 0.1 else 1),
                         "mktcap": 1e11})
        store.write_clean("prices_krx", d, pd.DataFrame(rows))
