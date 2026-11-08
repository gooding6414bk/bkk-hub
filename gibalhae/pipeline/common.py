"""기발해 콘텐츠 파이프라인 공통 — 경로, 머리말(frontmatter) 읽고 쓰기, 쇼츠 대본의 장면 지시 파싱, 서체.

대본 파일(shorts.md) 형식:

    ---
    title: 침대 머리 창문 쪽, 괜찮을까요
    status: draft
    ---
    [0-2s | 카드: "침대 머리를 창문 쪽으로 두셨나요"]
    침대 머리를 창문 쪽으로 두셨나요.

    [3-10s | 도면: 침대 창가, 점선]
    많은 집이 그렇게 놓습니다. ...

장면 지시 한 줄 `[시작-끝s | 종류: 인자]` 뒤에 그 장면의 나레이션 문장들이 온다.
종류: 카드 · 도면 · 달력 · 해법 · 단어 · 마무리 (graphics.py 가 그린다). 시작·끝 초는 목표치일 뿐이고
실제 길이는 음성(tts.py)이 정한다.

외부 패키지 없이 돌아간다(PyYAML 없이 머리말을 읽는다 — 값은 문자열·숫자·불리언·한 줄 목록·여러 줄 블록(|)만).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

PIPE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PIPE)                       # gibalhae/
CONTENT = os.environ.get("GIBALHAE_CONTENT") or os.path.join(ROOT, "content")
FONTS = os.path.join(PIPE, "fonts")
FONT_REGULAR = os.path.join(FONTS, "NotoSansKR-Regular.ttf")
FONT_BOLD = os.path.join(FONTS, "NotoSansKR-Bold.ttf")
VOICES = os.path.join(PIPE, "voices.json")

STATUSES = ("draft", "approved", "built", "published", "rejected")
SCENE_KINDS = ("카드", "도면", "달력", "해법", "단어", "마무리")


# ------------------------------------------------------------------ 머리말 --
def _parse_scalar(v: str):
    s = v.strip()
    if s == "":
        return ""
    if s in ("true", "false"):
        return s == "true"
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x) for x in _split_commas(inner)]
    if (s[0] == s[-1]) and s[0] in "\"'" and len(s) >= 2:
        if s[0] == '"':
            try:
                return json.loads(s)
            except ValueError:
                pass
        return s[1:-1]
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _split_commas(s: str) -> list[str]:
    out, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """'---' 로 감싼 머리말과 본문을 나눈다. 중첩은 한 단계(들여쓴 key: value)까지."""
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    head, body = m.group(1), text[m.end():]
    meta: dict = {}
    lines = head.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            i += 1
            continue
        mm = re.match(r"^(\s*)([A-Za-z0-9_가-힣-]+):\s?(.*)$", ln)
        if not mm:
            i += 1
            continue
        indent, key, val = len(mm.group(1)), mm.group(2), mm.group(3)
        target = meta
        if indent > 0:
            # 바로 위의 최상위 키 아래로
            parent = _last_dict_key(meta)
            if parent is not None:
                target = meta[parent]
        if val.strip() == "|" or val.strip() == ">":
            block, i = [], i + 1
            while i < len(lines) and (lines[i].startswith(" " * (indent + 2)) or not lines[i].strip()):
                block.append(lines[i][indent + 2:] if lines[i].strip() else "")
                i += 1
            while block and not block[-1]:
                block.pop()
            target[key] = "\n".join(block)
            continue
        if val.strip() == "" and i + 1 < len(lines) and re.match(r"^\s+[A-Za-z0-9_가-힣-]+:", lines[i + 1]):
            target[key] = {}
            meta[key] = target[key] if target is meta else target[key]
            i += 1
            continue
        if val.strip() == "" and i + 1 < len(lines) and re.match(r"^\s+- ", lines[i + 1]):
            items, i = [], i + 1
            while i < len(lines) and re.match(r"^\s+- ", lines[i]):
                items.append(_parse_scalar(lines[i].split("- ", 1)[1]))
                i += 1
            target[key] = items
            continue
        target[key] = _parse_scalar(val)
        i += 1
    return meta, body


def _last_dict_key(meta: dict):
    for k in reversed(list(meta.keys())):
        if isinstance(meta[k], dict):
            return k
    return None


def dump_frontmatter(meta: dict) -> str:
    out = ["---"]
    for k, v in meta.items():
        out.extend(_dump_kv(k, v, 0))
    out.append("---")
    return "\n".join(out) + "\n"


def _dump_kv(k, v, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(v, dict):
        lines = [f"{pad}{k}:"]
        for kk, vv in v.items():
            lines.extend(_dump_kv(kk, vv, indent + 2))
        return lines
    if isinstance(v, list):
        if not v:
            return [f"{pad}{k}: []"]
        return [f"{pad}{k}:"] + [f"{pad}  - {_dump_scalar(x)}" for x in v]
    if isinstance(v, str) and "\n" in v:
        return [f"{pad}{k}: |"] + [f"{pad}  {ln}" if ln else "" for ln in v.split("\n")]
    return [f"{pad}{k}: {_dump_scalar(v)}"]


def _dump_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or s[0] in "[{\"'" or ":" in s or s.strip() != s or s in ("true", "false") or re.fullmatch(r"-?\d+(\.\d+)?", s):
        return json.dumps(s, ensure_ascii=False)
    return s


def read_doc(path: str) -> tuple[dict, str]:
    with open(path, encoding="utf-8") as f:
        return parse_frontmatter(f.read())


def write_doc(path: str, meta: dict, body: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_frontmatter(meta))
        f.write(body if body.startswith("\n") else "\n" + body)


def set_status(path: str, status: str, **extra) -> dict:
    assert status in STATUSES, status
    meta, body = read_doc(path)
    meta["status"] = status
    for k, v in extra.items():
        meta[k] = v
    write_doc(path, meta, body)
    return meta


# ------------------------------------------------------------------ 대본 --
@dataclass
class Scene:
    idx: int
    kind: str              # 카드 · 도면 · 달력 · 해법 · 단어 · 마무리
    args: str              # 종류 뒤의 인자 원문
    start: float           # 대본이 적은 목표 시작 초
    end: float
    lines: list[str] = field(default_factory=list)   # 나레이션 문장들

    @property
    def text(self) -> str:
        return " ".join(ln.strip() for ln in self.lines if ln.strip())


SCENE_RE = re.compile(r"^\[\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*s?\s*\|\s*([^:\]|]+?)\s*(?::\s*(.*?))?\s*\]\s*$")


def parse_scenes(body: str) -> list[Scene]:
    scenes: list[Scene] = []
    for raw in body.split("\n"):
        ln = raw.rstrip()
        m = SCENE_RE.match(ln.strip())
        if m:
            kind = m.group(3).strip()
            if kind not in SCENE_KINDS:
                raise ValueError(f"모르는 장면 종류 '{kind}' — {', '.join(SCENE_KINDS)} 중 하나여야 한다: {ln}")
            scenes.append(Scene(len(scenes), kind, (m.group(4) or "").strip(), float(m.group(1)), float(m.group(2))))
            continue
        if not ln.strip() or ln.strip().startswith("#") or ln.strip().startswith("<!--"):
            continue
        if not scenes:
            raise ValueError(f"장면 지시 '[0-2s | 카드: ...]' 보다 먼저 나온 글: {ln}")
        scenes[-1].lines.append(ln.strip())
    empty = [s.idx + 1 for s in scenes if not s.text]
    if empty:
        raise ValueError(f"나레이션이 없는 장면: {empty}")
    return scenes


def split_quoted(args: str) -> list[str]:
    """해법 카드 인자 '"첫째" / "둘째" / "셋째"' → 세 문자열. 따옴표는 있어도 없어도 된다."""
    parts = [p.strip() for p in re.split(r"\s/\s|\s\|\s", args) if p.strip()]
    return [p.strip("\"'“”") for p in parts]


def quoted_text(args: str) -> str:
    """카드 인자 '"큰 글자 한 줄"' → 큰 글자 한 줄."""
    return args.strip().strip("\"'“”")


# ------------------------------------------------------------------ 폴더 --
def day_dir(date: str) -> str:
    return os.path.join(CONTENT, date)


def build_dir(date: str) -> str:
    d = os.path.join(day_dir(date), "build")
    os.makedirs(d, exist_ok=True)
    return d


def list_days() -> list[str]:
    if not os.path.isdir(CONTENT):
        return []
    return sorted(d for d in os.listdir(CONTENT) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))


def load_voices() -> dict:
    with open(VOICES, encoding="utf-8") as f:
        return json.load(f)
