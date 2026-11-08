"""검수 — 금지 표현·신분 노출·길이를 기계로 먼저 거른다. 사람이 승인하기 전에 돌린다.

    python gibalhae/pipeline/check.py 2026-10-24            그날 blog.md · shorts.md 둘 다
    python gibalhae/pipeline/check.py path/to/shorts.md     파일 하나

끝난 코드 0 = 통과(경고는 있어도 됨), 1 = 금지 항목 있음.
규칙의 근거는 gibalhae/brand.md 「금지」 — 거기 바뀌면 여기도 바꾼다.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import day_dir, parse_scenes, read_doc  # noqa: E402

# (정규식, 이유) — 걸리면 실패
FORBIDDEN = [
    (r"찾아\s*오세요|방문해\s*주세요|사무실(로|에서|에)\s*(오|방문|상담)", "사무실 없음 — '전국 전화·카카오톡 상담, 필요시 방문' 으로"),
    (r"출산\s*택일|제왕절개", "출산·제왕절개 택일은 하지 않음"),
    (r"망합니다|망한다|큰일\s*납니다|죽습니다|불행해집니다", "공포 훅 금지"),
    (r"100\s*%|반드시\s*(좋아|잘|낫|성공)|틀림없이|무조건\s*(좋아|잘|낫)|보장", "효과 단정 금지"),
    (r"치료|완치|병이\s*낫|암이|우울증이\s*(낫|사라)", "의료 효과 언급 금지"),
    (r"(대박|돈이\s*들어|부자가\s*됩니다|매출이\s*(반드시|무조건))", "재물 단정 금지"),
    (r"실명|본명은|주민등록|생년월일은", "신분 노출 금지(실명·생년)"),
    (r"\b(010-\d{3,4}-\d{4})\b(?!.*(상담|문의))", "전화번호는 상담 안내 줄에만"),
]
# (정규식, 이유) — 걸리면 경고만
WARN = [
    (r"\d{1,2}월\s*\d{1,2}일[이은는]?\s*(좋은|길한|이사|결혼)", "특정 날짜를 좋은 날로 지정한 것처럼 읽힐 수 있음"),
    (r"선생님께서|선생님이\s*직접", "화자는 3인칭 '도형 선생은 이렇게 봅니다' — 직접 등장처럼 읽히지 않게"),
    (r"저희\s*사무실|저희\s*가게|우리\s*동네", "지역·장소 특정 주의"),
    (r"[一-鿿]", "한자 — 화면 카드·자막에는 한글로"),
]
REQUIRED_BLOG = [
    (r"전화", "글 끝에 '전화·카카오톡 전국 상담' 안내"),
    (r"카카오톡|카톡", "글 끝에 '전화·카카오톡 전국 상담' 안내"),
]
CHARS_PER_SEC = 3.4          # edge-tts 한국어, 속도 -8% 실측: 193자(공백 제외) → 58초(쉼 포함)
SHORTS_MAX_SEC = 57
SHORTS_MIN_SEC = 15


def check_text(text: str, label: str) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    for pat, why in FORBIDDEN:
        for m in re.finditer(pat, text):
            fails.append(f"{label}: 금지 '{m.group(0)}' — {why}")
    for pat, why in WARN:
        for m in re.finditer(pat, text):
            warns.append(f"{label}: 주의 '{m.group(0)}' — {why}")
    return fails, warns


def check_file(path: str) -> tuple[list[str], list[str]]:
    meta, body = read_doc(path)
    name = os.path.basename(path)
    fails, warns = check_text(body + "\n" + str(meta.get("title", "")), name)
    if name.startswith("shorts"):
        try:
            scenes = parse_scenes(body)
        except ValueError as e:
            return fails + [f"{name}: 대본 형식 — {e}"], warns
        chars = sum(len(re.sub(r"\s", "", s.text)) for s in scenes)
        est = chars / CHARS_PER_SEC
        if est > SHORTS_MAX_SEC:
            fails.append(f"{name}: 나레이션 {chars}자 ≈ {est:.0f}초 — 60초 넘음. {int(SHORTS_MAX_SEC * CHARS_PER_SEC)}자 안으로")
        elif est < SHORTS_MIN_SEC and meta.get("series") != "D":
            warns.append(f"{name}: 나레이션 {chars}자 ≈ {est:.0f}초 — 짧음(단어 카드 시리즈 D 가 아니면 30~50초)")
        if not scenes or scenes[0].kind != "카드":
            warns.append(f"{name}: 첫 장면은 훅 카드가 원칙")
        if scenes and scenes[-1].kind != "마무리":
            warns.append(f"{name}: 마지막 장면은 마무리 카드가 원칙")
        for s in scenes:
            if s.kind == "카드" and len(s.args.strip("\"'“”")) > 22:
                warns.append(f"{name}: 장면 {s.idx + 1} 카드 글자 {len(s.args)}자 — 큰 글자 1줄(22자 안)")
        y = meta.get("youtube") or {}
        if isinstance(y, dict) and "AI" not in str(y.get("description", "")):
            warns.append(f"{name}: 유튜브 설명에 'AI 음성' 표기가 없음")
    if name.startswith("blog"):
        plain = re.sub(r"!\[.*?\]\(.*?\)|^#+\s*|\*\*|\n+", " ", body, flags=re.M).strip()   # 공백 포함 글자 수
        if len(plain) < 1400:
            warns.append(f"{name}: 본문 {len(plain)}자 — 1,500~2,500자 권장")
        if len(plain) > 3200:
            warns.append(f"{name}: 본문 {len(plain)}자 — 2,500자 안 권장")
        tail = body[-600:]
        for pat, why in REQUIRED_BLOG:
            if not re.search(pat, tail):
                fails.append(f"{name}: {why} 가 끝 부분에 없음")
        tags = meta.get("tags") or []
        if len(tags) < 5:
            warns.append(f"{name}: 태그 {len(tags)}개 — 8개 권장")
    return fails, warns


def check_day(date: str) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    d = day_dir(date)
    found = False
    for name in ("blog.md", "shorts.md"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            found = True
            f, w = check_file(p)
            fails += f
            warns += w
    if not found:
        fails.append(f"{d} 에 blog.md · shorts.md 가 없음")
    return fails, warns


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    target = argv[0]
    fails, warns = check_file(target) if target.endswith(".md") else check_day(target)
    for w in warns:
        print("WARN " + w)
    for f in fails:
        print("FAIL " + f)
    if not fails and not warns:
        print("ok   금지 표현 없음")
    elif not fails:
        print(f"ok   금지 표현 없음 (경고 {len(warns)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
