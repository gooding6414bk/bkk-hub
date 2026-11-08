"""기발해 검사 스크립트 두 개를 하위 프로세스로 돌린다 (원래 독립 실행 스크립트라 그대로 둔다)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.mark.parametrize("script", ["gibalhae_pipeline_check.py", "gibalhae_dash_check.py", "gibalhae_standalone_check.py"])
def test_gibalhae_script(script):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BKK_STOCK", "ARBI_", "GIBALHAE_"))}
    r = subprocess.run([sys.executable, str(HERE / script)], cwd=HERE.parent, env=env, capture_output=True,
                       text=True, timeout=300)
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    assert r.returncode == 0, f"{script}\n" + "\n".join(fails) + "\n" + r.stdout[-1500:] + r.stderr[-1500:]
