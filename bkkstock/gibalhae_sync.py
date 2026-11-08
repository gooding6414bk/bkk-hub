"""기발해 초안을 저장소(gibalhae/content)에서 서버 데이터 폴더(GIBALHAE_CONTENT)로 가져온다.

왜: 서버 대시보드에서 승인·글 고치기·영상 만들기를 하면 파일이 바뀐다. 그 파일이 git 추적 파일이면
자동 업데이트(git pull)와 부딪히거나 지워진다(건우아비 update.sh는 서버 쪽 변경을 되돌린다).
그래서 서버에서는 콘텐츠를 git 밖에 두고, 아침 예약 작업이 저장소에 올린 새 초안만 복사해 온다.

규칙: 서버에 없는 날짜 폴더와 파일만 복사한다. 이미 있는 파일은 절대 덮어쓰지 않는다.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPO_CONTENT = REPO / "gibalhae" / "content"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def target_dir() -> Path:
    return Path(os.environ.get("GIBALHAE_CONTENT") or REPO_CONTENT)


def sync(src: Path = REPO_CONTENT, dst: Path | None = None) -> list[str]:
    dst = dst or target_dir()
    if not src.is_dir() or src.resolve() == dst.resolve():
        return []
    dst.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for day in sorted(p for p in src.iterdir() if p.is_dir() and (DATE.match(p.name) or p.name == "_example")):
        for f in day.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            out = dst / rel
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            copied.append(str(rel))
    return copied
