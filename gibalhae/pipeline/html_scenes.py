"""장면 지시 → HTML (JSON2Video 의 html 요소가 1080×1920 으로 찍는다). 채널의 얼굴: 흰 카드·붉은 선·Noto Sans KR.

graphics.py(PIL) 와 같은 장면 종류·인자를 받는다. 배경은 투명이라 뒤에 깔린 배경 영상·그라데이션이 비친다.
    카드: "큰 글자"                 훅 카드
    도면: 침대 창가, 점선, 화살표 창문   위에서 본 방 (SVG)
    달력: 2026년 12월, 손없는날 9 10 19 20 29 30, 우리날 14
    해법: "하나" / "둘" / "셋"
    단어: "택일", "날을 고르는 일"
    마무리: "한 줄"
"""
from __future__ import annotations

import calendar
import html as H
import re

from pipeline.common import quoted_text, split_quoted

W, HGT = 1080, 1920
SEAL = "#c7000a"
INK = "#1c1917"
MID = "#4a4340"
MUTED = "#837a75"
PAPER = "#f7f5f3"
BRAND = "기발해 풍수·택일"

BASE_CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{HGT}px;background:transparent;font-family:'Noto Sans KR',sans-serif;color:{INK};
  word-break:keep-all;-webkit-font-smoothing:antialiased}}
.card{{position:absolute;left:70px;right:70px;background:#fff;border-radius:36px;
  box-shadow:0 24px 60px rgba(0,0,0,.45);overflow:hidden}}
.card::before{{content:"";position:absolute;left:0;right:0;top:0;height:14px;background:{SEAL}}}
.eyebrow{{position:absolute;left:60px;top:44px;font-size:28px;font-weight:700;color:{SEAL};letter-spacing:.02em}}
.title{{position:absolute;left:70px;right:70px;top:150px;height:320px;display:flex;align-items:center;justify-content:center;
  text-align:center;padding:40px 60px 20px;font-size:58px;font-weight:900;line-height:1.3}}
.mark{{position:absolute;right:70px;top:60px;display:flex;align-items:center;gap:14px;color:#fff;font-size:28px;font-weight:500}}
.mark b{{display:inline-flex;width:44px;height:44px;border-radius:9px;background:{SEAL};color:#fff;font-weight:900;
  align-items:center;justify-content:center;font-size:28px}}
.label{{position:absolute;left:40px;top:34px;font-size:36px;font-weight:700}}
.paper{{background:{PAPER}}}
.paper::before{{background:#d6cdc6;height:5px}}
</style>
"""


def _page(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'>{BASE_CSS}</head><body>{body}</body></html>"


def mark() -> str:
    return f"<div class='mark'><b>기</b>{BRAND}</div>"


def title_card(title: str) -> str:
    return f"<div class='card title'>{H.escape(title)}</div>"


# ------------------------------------------------------------------ 장면 --
def hook(text: str) -> str:
    t = H.escape(text)
    size = 96 if len(text) <= 14 else (84 if len(text) <= 20 else 70)
    return _page(mark() + f"""
<div class='card' style='top:520px;height:760px'>
  <div class='eyebrow'>{BRAND}</div>
  <div style='position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;
       padding:120px 70px 60px;font-size:{size}px;font-weight:900;line-height:1.28'>{t}</div>
</div>""")


def word(args: str) -> str:
    parts = split_quoted(args.replace(",", " / ")) if args else [""]
    w = H.escape(parts[0])
    sub = H.escape(parts[1]) if len(parts) > 1 else ""
    return _page(mark() + f"""
<div class='card' style='top:520px;height:820px'>
  <div class='eyebrow'>{BRAND}</div>
  <div style='position:absolute;left:0;right:0;top:170px;text-align:center;font-size:150px;font-weight:900;color:{SEAL};line-height:1.1'>{w}</div>
  <div style='position:absolute;left:80px;right:80px;top:430px;text-align:center;font-size:50px;font-weight:500;color:{MID};line-height:1.5'>{sub}</div>
</div>""")


def closing(line: str) -> str:
    line = H.escape(line or "방향은 사람마다 다릅니다")
    return _page(mark() + f"""
<div class='card' style='top:480px;height:860px'>
  <div style='position:absolute;left:0;right:0;top:110px;display:flex;justify-content:center'>
    <div style='width:140px;height:140px;border-radius:24px;background:{SEAL};color:#fff;font-size:92px;font-weight:900;
         display:flex;align-items:center;justify-content:center'>기</div>
  </div>
  <div style='position:absolute;left:0;right:0;top:290px;text-align:center;font-size:66px;font-weight:900'>{BRAND}</div>
  <div style='position:absolute;left:80px;right:80px;top:410px;text-align:center;font-size:46px;font-weight:700;color:{SEAL};line-height:1.45'>{line}</div>
  <div style='position:absolute;left:80px;right:80px;top:560px;text-align:center;font-size:40px;color:{MID};line-height:1.6'>
    전국 전화·카카오톡 상담 · 필요시 방문<br>자세한 건 설명란에</div>
  <div style='position:absolute;left:0;right:0;top:740px;text-align:center;font-size:30px;color:{MUTED}'>목소리는 AI 음성입니다</div>
</div>""")


def solutions(args: str, title: str) -> str:
    items = split_quoted(args)[:4] or [args]
    n = len(items)
    top, gap = 560, 24
    h = (1400 - top - gap * (n - 1)) // n
    cards = []
    for i, it in enumerate(items):
        y = top + i * (h + gap)
        cards.append(f"""
<div class='card' style='top:{y}px;height:{h}px;left:90px;right:90px;border-radius:24px'>
  <div style='position:absolute;left:40px;top:34px;font-size:56px;font-weight:900;color:{SEAL}'>{i + 1}</div>
  <div style='position:absolute;left:130px;right:40px;top:40px;font-size:42px;font-weight:700;line-height:1.3'>{H.escape(it)}</div>
</div>""")
    return _page(mark() + title_card(title) + "".join(cards))


def room(args: str, title: str) -> str:
    """위에서 본 방 — SVG. 인자: 위치(창가/문앞/화장실벽/벽/북/남/동/서/가운데), 실선/점선, 화살표 창문/문/화장실."""
    a = args.replace("，", ",")
    pos = "벽"
    for key in ("창가", "문앞", "문 앞", "화장실벽", "화장실 벽", "북서", "북동", "남서", "남동", "북", "남", "동", "서", "벽", "가운데"):
        if key in a:
            pos = key.replace(" ", "")
            break
    good = "점선" not in a
    color = SEAL if good else MUTED
    # 방: 0..700 × 0..720 (viewBox)
    RW, RH = 700, 720
    bw, bh = 230, 300
    rot = False
    if pos in ("창가", "북"):
        bx, by = 80, 24
    elif pos in ("문앞", "남"):
        bx, by, rot = 210, RH - bh - 24, True
    elif pos in ("화장실벽", "동", "북동"):
        bx, by = RW - bw - 24, 240
    elif pos == "가운데":
        bx, by = (RW - bw) // 2, (RH - bh) // 2
    elif pos == "남서":
        bx, by = 220, RH - bh - 24
    elif pos == "남동":
        bx, by = RW - bw - 24, RH - bh - 24
    else:
        bx, by = 28, 220
    dash = "" if good else "stroke-dasharray='18 14'"
    pw = int(bw * 0.7)
    px = bx + (bw - pw) // 2
    py = by + 24 if not rot else by + bh - 60
    arrow = ""
    m = re.search(r"화살표\s*([가-힣]+)", a)
    if m:
        src = m.group(1)
        cx, cy = px + pw // 2, py + 18
        if src.startswith("창"):
            sx, sy = RW // 2, 0
        elif src.startswith("문"):
            sx, sy = 120, RH
        else:
            sx, sy = RW - 105, 216
        arrow = (f"<defs><marker id='ah' markerWidth='12' markerHeight='12' refX='9' refY='6' orient='auto'>"
                 f"<path d='M0,0 L12,6 L0,12 z' fill='{color}'/></marker></defs>"
                 f"<line x1='{sx}' y1='{sy}' x2='{cx}' y2='{cy}' stroke='{color}' stroke-width='7' marker-end='url(#ah)' opacity='.9'/>")
    label = "좋은 배치" if good else "피할 배치"
    svg = f"""
<svg viewBox='-40 -60 {RW + 80} {RH + 120}' width='760' height='760' xmlns='http://www.w3.org/2000/svg' font-family='Noto Sans KR'>
  <rect x='0' y='0' width='{RW}' height='{RH}' fill='none' stroke='{MID}' stroke-width='9'/>
  <rect x='{RW // 2 - 125}' y='-6' width='250' height='12' fill='#7fb0e0'/>
  <text x='{RW // 2 - 40}' y='-22' font-size='30' fill='{MUTED}'>창문</text>
  <rect x='60' y='{RH - 6}' width='120' height='12' fill='{PAPER}'/>
  <path d='M60 {RH} A120 120 0 0 1 180 {RH - 120}' fill='none' stroke='{MUTED}' stroke-width='4'/>
  <text x='70' y='{RH + 44}' font-size='30' fill='{MUTED}'>문</text>
  <rect x='{RW - 210}' y='0' width='210' height='216' fill='none' stroke='{MID}' stroke-width='5'/>
  <text x='{RW - 180}' y='48' font-size='28' fill='{MUTED}'>화장실</text>
  <rect x='{bx}' y='{by}' width='{bw}' height='{bh}' rx='8' fill='{"rgba(199,0,10,.06)" if good else "none"}' stroke='{color}' stroke-width='9' {dash}/>
  <rect x='{px}' y='{py}' width='{pw}' height='36' rx='12' fill='{color}'/>
  <text x='{bx + 8}' y='{by + bh + 42}' font-size='32' font-weight='700' fill='{color}'>침대</text>
  <text x='{bx + bw + 14}' y='{py + 28}' font-size='28' fill='{color}'>머리</text>
  {arrow}
</svg>"""
    return _page(mark() + title_card(title) + f"""
<div class='card paper' style='top:560px;height:860px'>
  <div class='label' style='color:{color}'>{label}</div>
  <div style='position:absolute;left:0;right:0;top:70px;display:flex;justify-content:center'>{svg}</div>
</div>""")


def calendar_card(args: str, title: str) -> str:
    m = re.search(r"(\d{1,2})\s*월", args)
    month = int(m.group(1)) if m else 1
    ym = re.search(r"(\d{4})\s*년", args)
    year = int(ym.group(1)) if ym else None

    def nums(key):
        mm = re.search("(?:" + key + r")[^,]*", args)
        return {int(n) for n in re.findall(r"\d+", mm.group(0))} if mm else set()

    sonless, ours = nums(r"손\s*없는\s*날"), nums(r"우리\s*날|우리\s*가족\s*날|좋은\s*날")
    if year:
        first_wd, ndays = calendar.monthrange(year, month)
        first_col = (first_wd + 1) % 7
    else:
        first_col, ndays = 0, 30 if month in (4, 6, 9, 11) else (28 if month == 2 else 31)
    cells = ["<div></div>"] * first_col
    for d in range(1, ndays + 1):
        cls = "our" if d in ours else ""
        dot = "<i></i>" if d in sonless else ""
        cells.append(f"<div class='{cls}'><span>{d}</span>{dot}</div>")
    heads = "".join(f"<b class='{'sun' if i == 0 else ''}'>{d}</b>" for i, d in enumerate("일월화수목금토"))
    head_title = f"{year}년 {month}월" if year else f"{month}월"
    return _page(mark() + title_card(title) + f"""
<style>
.cal{{position:absolute;left:50px;right:50px;top:110px}}
.cal .h{{font-size:44px;font-weight:900;margin-bottom:18px}}
.cal .g{{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}}
.cal b{{text-align:center;font-size:26px;font-weight:500;color:{MUTED};padding-bottom:10px;border-bottom:2px solid #e6dfda}}
.cal b.sun{{color:{SEAL}}}
.cal .g div{{height:92px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:30px;position:relative}}
.cal .g div i{{width:12px;height:12px;border-radius:50%;background:{MUTED};margin-top:4px}}
.cal .g div.our span{{width:66px;height:66px;border-radius:50%;border:5px solid {SEAL};display:flex;align-items:center;justify-content:center;color:{SEAL};font-weight:900}}
.leg{{position:absolute;left:50px;bottom:30px;font-size:24px;color:{MUTED};display:flex;gap:28px;align-items:center}}
.leg i{{display:inline-block;width:12px;height:12px;border-radius:50%;background:{MUTED};margin-right:8px}}
.leg s{{display:inline-block;width:26px;height:26px;border-radius:50%;border:4px solid {SEAL};margin-right:8px;vertical-align:middle;text-decoration:none}}
</style>
<div class='card paper' style='top:560px;height:840px'>
  <div class='cal'><div class='h'>{head_title}</div><div class='g'>{heads}{"".join(cells)}</div></div>
  <div class='leg'><span><i></i>손없는날</span><span><s></s><span style='color:{SEAL}'>우리 가족 날</span></span></div>
</div>""")


def scene_html(kind: str, args: str, title: str) -> str:
    if kind == "카드":
        return hook(quoted_text(args) or title)
    if kind == "단어":
        return word(args)
    if kind == "마무리":
        return closing(quoted_text(args))
    if kind == "해법":
        return solutions(args, title)
    if kind == "달력":
        return calendar_card(args, title)
    return room(args, title)


def background_html() -> str:
    """배경 영상이 없을 때 깔 그라데이션 + 비네트."""
    return _page(f"<div style='position:absolute;inset:0;background:radial-gradient(ellipse at 50% 40%,#2e2826 0%,#1e1b1a 55%,#0f0d0d 100%)'></div>")


def dim_html() -> str:
    """배경 영상 위에 깔 어둡게 하는 막."""
    return _page("<div style='position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,8,8,.55),rgba(10,8,8,.72))'></div>")
