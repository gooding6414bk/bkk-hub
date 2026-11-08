"""기발해 초안을 저장소(gibalhae/content 또는 gibalhae-daily 브랜치)에서 서버 데이터 폴더(GIBALHAE_CONTENT)로 가져온다.

왜: 서버 대시보드에서 승인·글 고치기·영상 만들기를 하면 파일이 바뀐다. 그 파일이 git 추적 파일이면
자동 업데이트(git pull)와 부딪히거나 지워진다. 그래서 서버에서는 콘텐츠를 git 밖에 둔다.

규칙 (한쪽 방향, 서버 편집 우선):
  - 서버에 없는 파일은 복사한다.
  - 서버 파일이 마지막으로 가져온 그대로(서버에서 손대지 않음)이고 저장소 쪽이 바뀌었으면 새 내용으로 바꾼다.
    예: 주인이 채팅으로 「승인」해서 클라우드 세션이 status 를 바꾼 경우.
  - 서버에서 고친 파일(승인·수정·제작 결과)은 절대 덮어쓰지 않는다.
가져온 내용의 해시는 <서버 폴더>/.sync-manifest.json 에 둔다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPO_CONTENT = REPO / "gibalhae" / "content"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MANIFEST = ".sync-manifest.json"


def target_dir() -> Path:
    return Path(os.environ.get("GIBALHAE_CONTENT") or REPO_CONTENT)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sync(src: Path = REPO_CONTENT, dst: Path | None = None) -> list[str]:
    dst = dst or target_dir()
    if not src.is_dir() or src.resolve() == dst.resolve():
        return []
    dst.mkdir(parents=True, exist_ok=True)
    mpath = dst / MANIFEST
    try:
        manifest: dict[str, str] = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    changed: list[str] = []
    for day in sorted(p for p in src.iterdir() if p.is_dir() and (DATE.match(p.name) or p.name == "_example")):
        for f in day.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(src))
            out = dst / rel
            new = _sha(f)
            if out.exists():
                cur = _sha(out)
                untouched = manifest.get(rel) == cur
                if not untouched or cur == new:
                    continue          # 서버에서 고쳤거나 이미 같다
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".sync-tmp")
            shutil.copy2(f, tmp)
            os.replace(tmp, out)
            manifest[rel] = new
            changed.append(rel)
    if changed:
        tmp = mpath.with_name(MANIFEST + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=0), encoding="utf-8")
        os.replace(tmp, mpath)
    return changed
