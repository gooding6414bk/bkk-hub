"""기발해 콘텐츠 파이프라인 — 머리말·대본 파싱, 검수 규칙, 그래픽·자막·소재 큐·상태 기계 (네트워크·음성 없음).

실행: python tests/gibalhae_pipeline_check.py  (pytest에서는 tests/test_gibalhae.py가 돌린다)
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = os.path.join(ROOT, "gibalhae")
TMP = tempfile.mkdtemp(prefix="gibalhae-test-")
os.environ["GIBALHAE_CONTENT"] = os.path.join(TMP, "content")
sys.path.insert(0, G)

from pipeline import common as C  # noqa: E402
from pipeline.check import check_file, check_text  # noqa: E402
from pipeline.graphics import render_blog_image, render_scene  # noqa: E402
from pipeline.tts import parse_srt, srt_time, write_srt  # noqa: E402
from pipeline.upload_youtube import payload  # noqa: E402
from pipeline.publish_blog import md_to_html  # noqa: E402
from pipeline.render import group_words, karaoke_ass, srt_to_ass  # noqa: E402
from pipeline import html_scenes as HS  # noqa: E402
from pipeline.render_json2video import build_movie  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


EX = os.path.join(G, "content", "_example")

# ---------------------------------------------------------------- 머리말 --
meta, body = C.read_doc(os.path.join(EX, "shorts.md"))
check(meta["status"] == "draft" and meta["series"] == "A", "예시 대본 머리말")
check(meta["youtube"]["publish_at"] == "2026-10-24T18:00:00+09:00", "중첩 키(youtube.publish_at)")
check(len(meta["titles"]) == 3 and len(meta["tags"]) == 8, "목록(titles 3 · tags 8)")
check("AI 음성" in meta["youtube"]["description"], "여러 줄 블록(description)")
m2, _ = C.parse_frontmatter(C.dump_frontmatter(meta))
check(m2 == meta, "머리말 쓰고 다시 읽으면 같다")
bm, _ = C.read_doc(os.path.join(EX, "blog.md"))
check(bm["images"][2].startswith('해법: "벽에'), "따옴표 속 따옴표가 든 목록 항목")
check(C.parse_frontmatter("본문만")[0] == {}, "머리말 없는 글")

# ---------------------------------------------------------------- 대본 --
scenes = C.parse_scenes(body)
check([s.kind for s in scenes] == ["카드", "도면", "도면", "해법", "마무리"], "장면 5개 종류 순서")
check(scenes[1].args == "침대 창가, 점선" and scenes[1].start == 3 and scenes[1].end == 10, "장면 인자·목표 초")
check(C.split_quoted(scenes[3].args) == ["벽에 머리를 붙입니다", "문이 비스듬히 보이는 자리", "화장실 벽은 피합니다"], "해법 3장 나누기")
check(C.parse_scenes("[0-2s | 마무리]\n끝.")[0].args == "", "인자 없는 장면")
for bad, why in (("[0-2s | 사진: x]\n말", "모르는 종류"), ("말 먼저\n[0-2s | 카드: x]", "지시보다 앞선 글"), ("[0-2s | 카드: x]\n", "나레이션 없음")):
    try:
        C.parse_scenes(bad)
        check(False, f"잘못된 대본 거부 — {why}")
    except ValueError:
        check(True, f"잘못된 대본 거부 — {why}")

# ---------------------------------------------------------------- 검수 --
f, w = check_file(os.path.join(EX, "shorts.md"))
check(not f, f"예시 대본 금지 표현 없음 {f}")
f, w = check_file(os.path.join(EX, "blog.md"))
check(not f, f"예시 글 금지 표현 없음 {f}")
for text, why in (("이 날을 놓치면 망합니다", "공포 훅"), ("사무실로 오세요", "사무실"), ("출산택일도 봅니다", "출산택일"),
                  ("100% 좋아집니다", "효과 단정"), ("병이 낫습니다", "의료")):
    f, _ = check_text(text, "t")
    check(bool(f), f"금지 잡힘 — {why}")
f, w = check_text("11월 3일은 좋은 날입니다", "t")
check(not f and w, "특정 날짜 지정은 경고")
long = "---\ntitle: t\nstatus: draft\n---\n[0-2s | 카드: \"x\"]\n" + "가나다라마바사아자차. " * 25 + "\n[3-5s | 마무리]\n끝.\n"
p = os.path.join(TMP, "shorts.md")
open(p, "w", encoding="utf-8").write(long)
f, _ = check_file(p)
check(any("60초" in x for x in f), "60초 넘는 대본은 실패")
p = os.path.join(TMP, "blog.md")
open(p, "w", encoding="utf-8").write("---\ntitle: t\nstatus: draft\ntags: [a]\n---\n짧은 글. 상담은 전화로.\n")
f, w = check_file(p)
check(any("카카오톡" in x for x in f) and any("자" in x for x in w), "블로그: 카톡 안내 없으면 실패 · 짧으면 경고")

# ---------------------------------------------------------------- 그래픽 --
from PIL import Image  # noqa: E402
for kind, args in (("카드", '"침대 머리를 창문 쪽으로 두셨나요"'), ("도면", "침대 창가, 점선, 화살표 창문"), ("도면", "침대 벽, 실선"),
                   ("달력", "2026년 12월, 손없는날 9 10 19 20 29 30, 우리날 14"), ("해법", '"하나" / "둘" / "셋" / "넷"'),
                   ("단어", '"택일", "날을 고르는 일"'), ("마무리", "")):
    out = os.path.join(TMP, f"{kind}.png")
    render_scene(kind, args, "제목 하나", out, "나레이션")
    im = Image.open(out)
    check(im.size == (1080, 1920), f"장면 {kind} 1080×1920")
out = os.path.join(TMP, "blog.png")
render_blog_image("도면: 침대 창가, 점선", out)
check(Image.open(out).size == (1200, 800), "블로그 이미지 1200×800")
check(all(os.path.exists(x) for x in (C.FONT_REGULAR, C.FONT_BOLD)), "한글 서체 두 개가 저장소에 있다")

# ---------------------------------------------------------------- 자막 --
check(srt_time(3661.5) == "01:01:01,500", "srt 시각")
cues = parse_srt("1\n00:00:00,100 --> 00:00:03,800\n첫 줄\n\n2\n00:00:03,750 --> 00:00:06,837\n둘째\n")
check(cues == [(0.1, 3.8, "첫 줄"), (3.75, 6.837, "둘째")], "srt 읽기")
sp = os.path.join(TMP, "a.srt")
write_srt(cues, sp)
check(parse_srt(open(sp, encoding="utf-8").read()) == cues, "srt 쓰고 읽기")
ap = srt_to_ass(sp, os.path.join(TMP, "a.ass"))
txt = open(ap, encoding="utf-8").read()
check("PlayResY: 1920" in txt and "Dialogue: 0,0:00:00.10,0:00:03.80,Sub,,0,0,0,,첫 줄" in txt, "ass 변환(화면 크기·대사)")

words = [(0.1, 0.6, "침대"), (0.7, 1.1, "머리를"), (1.2, 1.5, "창문"), (1.6, 2.0, "쪽으로"), (2.1, 3.0, "두셨나요."), (3.2, 3.6, "많은"), (3.7, 4.2, "집이")]
g = group_words(words, 5.0)
check(len(g) == 2 and len(g[0]["words"]) == 5 and g[0]["end"] <= g[1]["start"], "어절 묶기 — 문장 끝에서 끊고 겹치지 않음")
kp = karaoke_ass({"scenes": [{"start": 0, "end": 5.0, "text": "x", "words": words}]}, os.path.join(TMP, "k.ass"))
kt = open(kp, encoding="utf-8").read()
check(kt.count("Dialogue:") == 7 and "{\\c&H0A00C7&" in kt and "\\N" in kt, "읽는 단어 켜지는 자막 — 어절마다 한 줄, 두 줄 나눔")

# ---------------------------------------------------------------- JSON2Video --
for kind, args in (("카드", '"훅"'), ("도면", "침대 창가, 점선, 화살표 창문"), ("달력", "2026년 12월, 손없는날 9 10, 우리날 14"),
                   ("해법", '"하나" / "둘"'), ("단어", '"택일", "풀이"'), ("마무리", "")):
    h = HS.scene_html(kind, args, "제목")
    check(h.startswith("<!doctype html>") and "Noto+Sans+KR" in h and "기발해" in h, f"장면 HTML {kind}")
check("<svg" in HS.scene_html("도면", "침대 벽, 실선", "t") and "marker-end" in HS.scene_html("도면", "침대 창가, 화살표 창문", "t"), "도면 SVG · 화살표")
check("우리 가족 날" in HS.scene_html("달력", "2026년 12월, 우리날 14", "t") and "class='our'" in HS.scene_html("달력", "2026년 12월, 우리날 14", "t"), "달력 우리날 표시")
import shutil as _sh  # noqa: E402
_sh.copytree(EX, os.path.join(C.CONTENT, "2026-10-24"))
mv = build_movie("2026-10-24")
check(mv["width"] == 1080 and mv["height"] == 1920 and len(mv["scenes"]) == 5, "movie.json 세로 1080×1920 · 장면 5")
check(mv["elements"][0]["type"] == "subtitles" and mv["elements"][0]["language"] == "ko", "자막 요소(한국어) 영화 단위")
vo = [e for e in mv["scenes"][1]["elements"] if e["type"] == "voice"][0]
check(vo["model"] == "elevenlabs" and vo["text"].startswith("많은 집이") and "transition" in mv["scenes"][1] and "transition" not in mv["scenes"][0], "장면별 음성 · 전환")
check(all(e["type"] == "html" for e in mv["scenes"][0]["elements"][:2]), "배경 없으면 그라데이션 HTML")
with open(os.path.join(C.build_dir("2026-10-24"), "background.json"), "w", encoding="utf-8") as f:
    f.write('{"url": "https://videos.pexels.com/x.mp4"}')
mv2 = build_movie("2026-10-24", "sunhi-azure")
check(mv2["scenes"][0]["elements"][0]["type"] == "video" and mv2["scenes"][0]["elements"][0]["muted"] is True, "배경 영상 있으면 video 요소")
check([e for e in mv2["scenes"][0]["elements"] if e["type"] == "voice"][0]["voice"] == "ko-KR-SunHiNeural", "--voice 로 목소리 바꾸기")

# ---------------------------------------------------------------- 유튜브 --
pl = payload(meta)
check(pl["status"]["privacyStatus"] == "private" and pl["status"]["publishAt"] == "2026-10-24T09:00:00Z", "예약 시각 KST→UTC")
check(pl["status"]["containsSyntheticMedia"] is True and "Shorts" in pl["snippet"]["tags"], "AI 합성 표시·Shorts 태그")
check(payload(meta, now=True)["status"]["privacyStatus"] == "public", "--now 면 바로 공개")
check("AI" in payload({"title": "t", "youtube": {"description": "x"}})["snippet"]["description"], "설명에 AI 표기 없으면 붙인다")

# ---------------------------------------------------------------- 블로그 html --
h = md_to_html("## 소제목\n\n문단 **굵게** 끝.\n\n![설명](1)\n\n- 하나\n- 둘\n")
check("<h3>소제목</h3>" in h and "<b>굵게</b>" in h and 'data-image="1"' in h and "<li>둘</li>" in h, "마크다운 → html")

# ---------------------------------------------------------------- 상태 기계 --
from pipeline import daily as D  # noqa: E402
D.TOPICS = os.path.join(TMP, "topics.md")
shutil.copy(os.path.join(G, "topics.md"), D.TOPICS)
n, t = D.next_topic()
check(n == 1 and t["series"] == "A", "소재 큐 첫 줄")
date = D.new("2030-01-01", None)
check(os.path.exists(os.path.join(C.day_dir(date), "shorts.md")) and os.path.exists(os.path.join(C.day_dir(date), "blog.md")), "new 가 두 파일을 만든다")
check(D.next_topic()[0] == 2, "쓴 소재는 건너뛴다")
sm, _ = C.read_doc(os.path.join(C.day_dir(date), "shorts.md"))
check(sm["status"] == "draft" and sm["youtube"]["publish_at"] == "2030-01-01T18:00:00+09:00", "새 대본 머리말(draft · 18:00 예약)")
try:
    D.build(date, [])
    check(False, "draft 는 build 안 됨")
except SystemExit:
    check(True, "draft 는 build 안 됨")
D.approve(date, ["--shorts"])
sm, _ = C.read_doc(os.path.join(C.day_dir(date), "shorts.md"))
bm2, _ = C.read_doc(os.path.join(C.day_dir(date), "blog.md"))
check(sm["status"] == "approved" and bm2["status"] == "draft", "--shorts 는 쇼츠만 승인")
D.reject(date, ["--shorts", "--why", "길다"])
sm, _ = C.read_doc(os.path.join(C.day_dir(date), "shorts.md"))
check(sm["status"] == "rejected" and sm["reject_reason"] == "길다", "반려 사유 기록")

shutil.rmtree(TMP, ignore_errors=True)
print("실패 %d" % fails)
sys.exit(1 if fails else 0)
