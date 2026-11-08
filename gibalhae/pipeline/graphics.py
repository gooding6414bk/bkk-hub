"""장면 지시 → PNG. 채널의 '얼굴'인 그래픽 — 흰 카드·붉은 선(#c7000a)·진회색, 서체 Noto Sans KR.

    python gibalhae/pipeline/graphics.py 2026-10-24          shorts.md 장면마다 build/scene_NN.png (1080×1920)
    python gibalhae/pipeline/graphics.py 2026-10-24 --blog   blog.md 머리말 images: 의 지시마다 build/blog/NN.png (1200×800)

장면 종류와 인자 (대본 한 줄 `[3-10s | 종류: 인자]`):
    카드: "큰 글자 한 줄"                                  훅·제목 카드
    도면: 침대 창가, 점선, 화살표 창문                       방을 위에서 본 도면. 위치 창가/문앞/화장실벽/벽/북/남/동/서,
                                                            실선(좋은 배치·붉은)/점선(피할 배치·회색), 화살표 창문/문/화장실
    해법: "첫째 ..." / "둘째 ..." / "셋째 ..."              해법 카드 2~4장 (붉은 실선 번호)
    달력: 11월, 손없는날 9 10 19 20 29 30, 우리날 14          월 달력. 손없는날 회색 점, 우리날 붉은 원
    단어: "택일", "날을 고르는 일"                           한 줄 정의(시리즈 D) — 큰 단어 + 작은 풀이
    마무리: (인자 없음 또는 "한 줄")                          로고·상담 안내 카드

쇼츠 화면: 위 25% 제목 카드 · 가운데 50% 그래픽 · 아래 25% 자막 자리(render.py 가 자막을 넣는다).
"""
from __future__ import annotations

import calendar
import os
import re
import sys

import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.common import (FONT_BOLD, FONT_REGULAR, build_dir, day_dir, parse_scenes, quoted_text,  # noqa: E402
                             read_doc, split_quoted)

W, H = 1080, 1920
SEAL = (199, 0, 10)            # #c7000a
INK = (28, 25, 23)
MID = (74, 67, 64)
MUTED = (131, 122, 117)
LINE = (214, 205, 198)
PAPER = (247, 245, 243)
WHITE = (255, 255, 255)
BG_TOP = (30, 27, 26)
BG_BOT = (46, 40, 38)
BRAND = "기발해 풍수·택일"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """단어 단위로 줄을 나눈다(한국어는 어절 단위 — keep-all)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if draw.textlength(cand, font=f) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_block(draw, xy, text, f, width, fill, align="left", spacing=1.3) -> int:
    """여러 줄 글. 끝난 y 를 돌려준다."""
    x, y = xy
    lh = int(f.size * spacing)
    for ln in wrap(draw, text, f, width):
        tw = draw.textlength(ln, font=f)
        xx = x if align == "left" else (x + (width - tw) / 2 if align == "center" else x + width - tw)
        draw.text((xx, y), ln, font=f, fill=fill)
        y += lh
    return y


def gradient(w: int, h: int, top=BG_TOP, bot=BG_BOT) -> Image.Image:
    """위→아래 그라데이션 + 가장자리 어둡게(비네트) + 종이 같은 미세한 질감. 정지 화면이 밋밋하지 않게."""
    base = Image.new("RGB", (1, h))
    for y in range(h):
        k = y / max(h - 1, 1)
        base.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3)))
    img = base.resize((w, h))
    # 비네트
    mask = Image.new("L", (w // 8, h // 8), 0)
    ImageDraw.Draw(mask).ellipse((-w // 40, -h // 40, w // 8 + w // 40, h // 8 + h // 40), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(w // 48)).resize((w, h))
    dark = Image.new("RGB", (w, h), (12, 10, 10))
    img = Image.composite(img, dark, mask.point(lambda v: 120 + v * 135 // 255))
    # 질감(고정 seed — 매번 같은 그림)
    rnd = random.Random(7)
    noise = Image.effect_noise((w // 2, h // 2), 18).resize((w, h))
    img = Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.045)
    _ = rnd
    return img


def card(draw, box, radius=28, fill=WHITE, line=SEAL, line_w=10, img: Image.Image | None = None):
    """흰 카드 + 위쪽 붉은 선. img 를 주면 아래에 부드러운 그림자를 깐다."""
    x0, y0, x1, y1 = box
    if img is not None:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((x0 + 4, y0 + 18, x1 + 4, y1 + 26), radius=radius, fill=(0, 0, 0, 110))
        sh = sh.filter(ImageFilter.GaussianBlur(22))
        img.paste(sh, (0, 0), sh)
    draw.rounded_rectangle(box, radius=radius, fill=fill)
    draw.rounded_rectangle((x0, y0, x1, y0 + line_w), radius=radius // 3, fill=line)


def brand_mark(draw, x, y, size=30, color=WHITE):
    """오른쪽 위 작은 인장 — 붉은 네모 안 '기'."""
    draw.rounded_rectangle((x, y, x + size + 10, y + size + 10), radius=6, fill=SEAL)
    f = font(size - 2, True)
    draw.text((x + 6, y + 1), "기", font=f, fill=WHITE)
    draw.text((x + size + 24, y + 2), BRAND, font=font(size - 4), fill=color)


# ------------------------------------------------------------------ 요소 --
def draw_room(draw, box, args: str, scale: float = 1.0) -> None:
    """위에서 본 방. box 안에 그린다. args: '침대 창가, 점선, 화살표 창문'."""
    x0, y0, x1, y1 = box
    rw, rh = x1 - x0, y1 - y0
    lw = max(4, int(6 * scale))
    # 방 벽
    draw.rectangle(box, outline=MID, width=lw)
    # 창(위 벽 가운데) · 문(아래 왼쪽) · 화장실(오른쪽 위 구석 작은 방)
    cw = int(rw * 0.36)
    draw.rectangle((x0 + (rw - cw) // 2, y0 - lw, x0 + (rw + cw) // 2, y0 + lw), fill=(120, 170, 220))
    draw.text((x0 + (rw - cw) // 2, y0 - int(44 * scale)), "창문", font=font(int(30 * scale)), fill=MUTED)
    dw = int(rw * 0.18)
    draw.rectangle((x0 + int(rw * 0.08), y1 - lw, x0 + int(rw * 0.08) + dw, y1 + lw), fill=PAPER)
    draw.arc((x0 + int(rw * 0.08), y1 - dw, x0 + int(rw * 0.08) + 2 * dw, y1 + dw), 180, 270, fill=MUTED, width=lw // 2)
    draw.text((x0 + int(rw * 0.08), y1 + int(10 * scale)), "문", font=font(int(30 * scale)), fill=MUTED)
    bw, bh = int(rw * 0.3), int(rh * 0.3)
    draw.rectangle((x1 - bw, y0, x1, y0 + bh), outline=MID, width=lw // 2)
    draw.text((x1 - bw + int(14 * scale), y0 + int(10 * scale)), "화장실", font=font(int(28 * scale)), fill=MUTED)

    # 침대 위치
    a = args.replace("，", ",")
    pos = "벽"
    for key in ("창가", "문앞", "문 앞", "화장실벽", "화장실 벽", "북서", "북동", "남서", "남동", "북", "남", "동", "서", "벽", "가운데"):
        if key in a:
            pos = key.replace(" ", "")
            break
    good = "점선" not in a
    bed_w, bed_h = int(rw * 0.34), int(rh * 0.42)      # 세로로 긴 침대, 머리는 위쪽
    rot = False
    if pos in ("창가", "북"):
        bx, by = x0 + int(rw * 0.12), y0 + lw + int(rh * 0.03)
    elif pos in ("문앞", "남"):
        bx, by = x0 + int(rw * 0.3), y1 - bed_h - lw - int(rh * 0.03)
        rot = True
    elif pos in ("화장실벽", "동", "북동"):
        bx, by = x1 - bed_w - lw - int(rw * 0.03), y0 + bh + int(rh * 0.04)
    elif pos in ("가운데",):
        bx, by = x0 + (rw - bed_w) // 2, y0 + (rh - bed_h) // 2
    elif pos in ("남서",):
        bx, by = x0 + lw + int(rw * 0.3), y1 - bed_h - lw - int(rh * 0.03)
    elif pos in ("남동",):
        bx, by = x1 - bed_w - lw - int(rw * 0.03), y1 - bed_h - lw - int(rh * 0.03)
    else:                                            # 벽 · 서 · 북서: 왼쪽 벽에 머리를 붙인 좋은 자리
        bx, by = x0 + lw + int(rw * 0.04), y0 + int(rh * 0.3)
    color = SEAL if good else MUTED
    bed = (bx, by, bx + bed_w, by + bed_h)
    if good:
        draw.rectangle(bed, outline=color, width=lw)
    else:
        _dashed_rect(draw, bed, color, lw, int(18 * scale))
    # 머리(베개) 표시 — 침대 위쪽(rot 이면 아래쪽: 문을 향해 머리)
    pw = int(bed_w * 0.7)
    px = bx + (bed_w - pw) // 2
    py = (by + int(bed_h * 0.08)) if not rot else (by + bed_h - int(bed_h * 0.2))
    draw.rounded_rectangle((px, py, px + pw, py + int(bed_h * 0.12)), radius=10, fill=color)
    draw.text((bx + int(8 * scale), by + bed_h + int(6 * scale)), "침대", font=font(int(30 * scale), True), fill=color)
    head_y = by - int(8 * scale) if not rot else by + bed_h + int(8 * scale)
    draw.text((bx + bed_w + int(10 * scale), py - int(4 * scale)), "머리", font=font(int(26 * scale)), fill=color)

    # 화살표: 기운이 드나드는 곳 → 침대 머리
    m = re.search(r"화살표\s*([가-힣]+)", a)
    if m:
        src = m.group(1)
        cx, cy = px + pw // 2, py + int(bed_h * 0.06)
        if src.startswith("창"):
            sx, sy = x0 + rw // 2, y0
        elif src.startswith("문"):
            sx, sy = x0 + int(rw * 0.08) + dw // 2, y1
        else:
            sx, sy = x1 - bw // 2, y0 + bh
        _arrow(draw, (sx, sy), (cx, cy), color, lw)
    _ = head_y


def _dashed_rect(draw, box, color, width, dash):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        length = max(abs(bx - ax), abs(by - ay))
        n = max(1, length // dash)
        for i in range(0, n, 2):
            t0, t1 = i / n, min((i + 1) / n, 1)
            draw.line((ax + (bx - ax) * t0, ay + (by - ay) * t0, ax + (bx - ax) * t1, ay + (by - ay) * t1), fill=color, width=width)


def _arrow(draw, a, b, color, width):
    import math
    draw.line((a, b), fill=color, width=max(3, width - 2))
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    L = 26
    p1 = (b[0] - L * math.cos(ang - 0.5), b[1] - L * math.sin(ang - 0.5))
    p2 = (b[0] - L * math.cos(ang + 0.5), b[1] - L * math.sin(ang + 0.5))
    draw.polygon([b, p1, p2], fill=color)


def draw_calendar(draw, box, args: str, scale: float = 1.0) -> None:
    """'11월, 손없는날 9 10 19 20 29 30, 우리날 14'  (연도는 '2026년' 이 있으면 쓰고 없으면 요일 없이)"""
    x0, y0, x1, y1 = box
    m = re.search(r"(\d{1,2})\s*월", args)
    month = int(m.group(1)) if m else 1
    ym = re.search(r"(\d{4})\s*년", args)
    year = int(ym.group(1)) if ym else None
    nums = lambda key: [int(n) for n in re.findall(r"\d+", (re.search("(?:" + key + r")[^,]*", args) or re.match("", "")).group(0))]
    sonless = set(nums(r"손\s*없는\s*날"))
    ours = set(nums(r"우리\s*날|우리\s*가족\s*날|좋은\s*날"))
    title = f"{year}년 {month}월" if year else f"{month}월"
    draw.text((x0, y0), title, font=font(int(44 * scale), True), fill=INK)
    days = ["일", "월", "화", "수", "목", "금", "토"]
    cw = (x1 - x0) / 7
    ty = y0 + int(70 * scale)
    for i, d in enumerate(days):
        draw.text((x0 + i * cw + cw / 2 - 12 * scale, ty), d, font=font(int(26 * scale)), fill=SEAL if i == 0 else MUTED)
    draw.line((x0, ty + int(42 * scale), x1, ty + int(42 * scale)), fill=LINE, width=2)
    if year:
        first_wd, ndays = calendar.monthrange(year, month)
        first_col = (first_wd + 1) % 7                 # calendar 는 월=0, 우리는 일=0
    else:
        first_col, ndays = 0, 30 if month in (4, 6, 9, 11) else (28 if month == 2 else 31)
    rows = (first_col + ndays + 6) // 7
    ch = (y1 - ty - int(50 * scale)) / max(rows, 5)
    f = font(int(30 * scale))
    for d in range(1, ndays + 1):
        col = (first_col + d - 1) % 7
        row = (first_col + d - 1) // 7
        cx = x0 + col * cw + cw / 2
        cy = ty + int(50 * scale) + row * ch + ch / 2
        if d in ours:
            r = min(cw, ch) * 0.36
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=SEAL, width=max(3, int(5 * scale)))
        tw = draw.textlength(str(d), font=f)
        draw.text((cx - tw / 2, cy - f.size * 0.62), str(d), font=f, fill=SEAL if d in ours else INK)
        if d in sonless:
            rr = max(4, int(6 * scale))
            draw.ellipse((cx - rr, cy + f.size * 0.55, cx + rr, cy + f.size * 0.55 + 2 * rr), fill=MUTED)
    ly = y1 - int(28 * scale)
    draw.ellipse((x0, ly + 8, x0 + 12, ly + 20), fill=MUTED)
    draw.text((x0 + 22, ly - 4), "손없는날", font=font(int(24 * scale)), fill=MUTED)
    draw.ellipse((x0 + 180 * scale, ly - 2, x0 + 180 * scale + 30, ly + 28), outline=SEAL, width=3)
    draw.text((x0 + 180 * scale + 40, ly - 4), "우리 가족 날", font=font(int(24 * scale)), fill=SEAL)


def draw_solutions(draw, box, items: list[str], scale: float = 1.0, img: Image.Image | None = None) -> None:
    x0, y0, x1, y1 = box
    n = max(len(items), 1)
    gap = int(24 * scale)
    h = (y1 - y0 - gap * (n - 1)) / n
    for i, it in enumerate(items):
        yy = y0 + i * (h + gap)
        card(draw, (x0, yy, x1, yy + h), radius=22, line_w=int(8 * scale), img=img)
        if img is not None:
            draw.__init__(img)
        nf = font(int(54 * scale), True)
        draw.text((x0 + int(36 * scale), yy + int(28 * scale)), str(i + 1), font=nf, fill=SEAL)
        text_block(draw, (x0 + int(120 * scale), yy + int(36 * scale)), it, font(int(40 * scale), True),
                   x1 - x0 - int(160 * scale), INK, spacing=1.25)


# ------------------------------------------------------------------ 장면 --
def render_scene(kind: str, args: str, title: str, path: str, narration: str = "") -> str:
    img = gradient(W, H)
    draw = ImageDraw.Draw(img)
    brand_mark(draw, W - 360, 60)

    def card_(box, **kw):
        card(draw, box, img=img, **kw)
        draw.__init__(img)          # 그림자를 붙인 뒤 같은 그림에 계속 그린다
    top = (70, 150, W - 70, 470)
    mid = (90, 560, W - 90, 1400)

    if kind == "카드":
        text = quoted_text(args) or title
        card_((70, 420, W - 70, 1120), radius=36, line_w=14)
        draw.text((130, 480), BRAND, font=font(30, True), fill=SEAL)
        f = font(92, True)
        lines = wrap(draw, text, f, W - 260)
        while len(lines) > 3 and f.size > 56:
            f = font(f.size - 6, True)
            lines = wrap(draw, text, f, W - 260)
        y = 770 - int(len(lines) * f.size * 1.3 / 2)
        for ln in lines:
            tw = draw.textlength(ln, font=f)
            draw.text(((W - tw) / 2, y), ln, font=f, fill=INK)
            y += int(f.size * 1.3)
    elif kind == "단어":
        parts = split_quoted(args.replace(",", " / ")) if args else [title]
        word = parts[0]
        card_((70, 420, W - 70, 1180), radius=36, line_w=14)
        f = font(140, True)
        tw = draw.textlength(word, font=f)
        draw.text(((W - tw) / 2, 560), word, font=f, fill=SEAL)
        if len(parts) > 1:
            text_block(draw, (140, 800), parts[1], font(50), W - 280, MID, align="center")
    elif kind == "마무리":
        line = quoted_text(args) or "방향은 사람마다 다릅니다"
        card_((70, 420, W - 70, 1240), radius=36, line_w=14)
        draw.rounded_rectangle((W / 2 - 70, 520, W / 2 + 70, 660), radius=18, fill=SEAL)
        draw.text((W / 2 - 42, 540), "기", font=font(90, True), fill=WHITE)
        f = font(64, True)
        tw = draw.textlength(BRAND, font=f)
        draw.text(((W - tw) / 2, 700), BRAND, font=f, fill=INK)
        text_block(draw, (140, 820), line, font(46, True), W - 280, SEAL, align="center")
        text_block(draw, (140, 960), "전국 전화·카카오톡 상담 · 필요시 방문\n자세한 건 설명란에", font(40), W - 280, MID, align="center")
        draw.text((W / 2 - draw.textlength("목소리는 AI 음성입니다", font=font(30)) / 2, 1140), "목소리는 AI 음성입니다",
                  font=font(30), fill=MUTED)
    else:
        # 위: 제목 카드
        card_(top, radius=30, line_w=12)
        f = font(58, True)
        lines = wrap(draw, title, f, W - 260)
        while len(lines) > 2 and f.size > 40:
            f = font(f.size - 4, True)
            lines = wrap(draw, title, f, W - 260)
        y = 310 - int(len(lines) * f.size * 1.3 / 2)
        for ln in lines:
            tw = draw.textlength(ln, font=f)
            draw.text(((W - tw) / 2, y), ln, font=f, fill=INK)
            y += int(f.size * 1.3)
        # 가운데: 그래픽 카드
        if kind == "도면":
            card_(mid, radius=30, fill=PAPER, line=LINE, line_w=4)
            draw_room(draw, (mid[0] + 110, mid[1] + 120, mid[2] - 110, mid[3] - 90), args, scale=1.15)
            label = "좋은 배치" if "점선" not in args else "피할 배치"
            draw.text((mid[0] + 40, mid[1] + 30), label, font=font(36, True), fill=SEAL if "점선" not in args else MUTED)
        elif kind == "달력":
            card_(mid, radius=30, fill=PAPER, line=LINE, line_w=4)
            draw_calendar(draw, (mid[0] + 60, mid[1] + 50, mid[2] - 60, mid[3] - 40), args, scale=1.0)
        elif kind == "해법":
            items = split_quoted(args)[:4] or [narration]
            draw_solutions(draw, (mid[0], mid[1], mid[2], mid[3]), items, scale=1.0, img=img)
    img.save(path, "PNG", optimize=True)
    return path


def render_blog_image(directive: str, path: str, title: str = "") -> str:
    """blog.md 머리말 images: 항목 '도면: 침대 창가, 점선' → 1200×800 PNG."""
    kind, _, args = directive.partition(":")
    kind, args = kind.strip(), args.strip()
    bw, bh = 1200, 800
    img = Image.new("RGB", (bw, bh), PAPER)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, bw, 10), fill=SEAL)
    if kind == "도면":
        draw_room(draw, (330, 120, bw - 330, bh - 110), args, scale=0.9)
        label = "좋은 배치" if "점선" not in args else "피할 배치"
        draw.text((40, 36), label, font=font(34, True), fill=SEAL if "점선" not in args else MUTED)
    elif kind == "달력":
        draw_calendar(draw, (120, 60, bw - 120, bh - 60), args, scale=0.85)
    elif kind == "해법":
        draw_solutions(draw, (80, 70, bw - 80, bh - 70), split_quoted(args)[:4], scale=0.7)
    else:
        text_block(draw, (100, 300), quoted_text(args) or title, font(64, True), bw - 200, INK, align="center")
    draw.text((bw - 330, bh - 50), BRAND + " · gibalhae.com", font=font(24), fill=MUTED)
    img.save(path, "PNG", optimize=True)
    return path


def render_day(date: str) -> list[str]:
    meta, body = read_doc(os.path.join(day_dir(date), "shorts.md"))
    out = build_dir(date)
    paths = []
    for s in parse_scenes(body):
        p = os.path.join(out, f"scene_{s.idx:02d}.png")
        render_scene(s.kind, s.args, str(meta.get("title", "")), p, s.text)
        paths.append(p)
    return paths


def render_blog(date: str) -> list[str]:
    meta, _ = read_doc(os.path.join(day_dir(date), "blog.md"))
    out = os.path.join(build_dir(date), "blog")
    os.makedirs(out, exist_ok=True)
    paths = []
    for i, d in enumerate(meta.get("images") or [], 1):
        p = os.path.join(out, f"{i:02d}.png")
        render_blog_image(str(d), p, str(meta.get("title", "")))
        paths.append(p)
    return paths


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    date = argv[0]
    paths = render_blog(date) if "--blog" in argv else render_day(date)
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
