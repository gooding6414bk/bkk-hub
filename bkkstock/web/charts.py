"""서버에서 바로 그리는 SVG 차트. 외부 라이브러리를 쓰지 않는다. 색은 CSS 변수로 지정한다."""

from __future__ import annotations

import math
from html import escape


def _fmt(v: float) -> str:
    return f"{v:.1f}"


def price_chart(dates: list, closes: list[float], values: list[float], nxt_values: list[float] | None = None,
                marks: list[int] | None = None, width: int = 720, height: int = 260) -> str:
    n = len(closes)
    if n < 2:
        return '<p class="muted">차트를 그릴 이력이 아직 없습니다.</p>'
    pad_l, pad_r, pad_t = 8, 56, 10
    price_h = height * 0.66
    vol_top = price_h + 18
    vol_h = height - vol_top - 18
    w = width - pad_l - pad_r
    lo, hi = min(closes), max(closes)
    if hi == lo:
        hi, lo = hi * 1.01 + 1, lo * 0.99
    span = hi - lo
    lo -= span * 0.05
    hi += span * 0.05

    def x(i):
        return pad_l + w * i / (n - 1)

    def y(v):
        return pad_t + (price_h - pad_t) * (1 - (v - lo) / (hi - lo))

    pts = " ".join(f"{_fmt(x(i))},{_fmt(y(c))}" for i, c in enumerate(closes) if c == c)
    area = f"{_fmt(x(0))},{_fmt(price_h)} {pts} {_fmt(x(n - 1))},{_fmt(price_h)}"
    tot = [(values[i] or 0) + ((nxt_values[i] or 0) if nxt_values else 0) for i in range(n)]
    vmax = max(tot) or 1
    bw = max(1.0, w / n * 0.7)
    bars = []
    for i in range(n):
        kv = values[i] or 0
        nv = (nxt_values[i] or 0) if nxt_values else 0
        hk = vol_h * kv / vmax
        hn = vol_h * nv / vmax
        bx = x(i) - bw / 2
        base = vol_top + vol_h
        bars.append(f'<rect x="{_fmt(bx)}" y="{_fmt(base - hk)}" width="{_fmt(bw)}" height="{_fmt(hk)}" class="c-vol"/>')
        if hn > 0:
            bars.append(f'<rect x="{_fmt(bx)}" y="{_fmt(base - hk - hn)}" width="{_fmt(bw)}" height="{_fmt(hn)}" class="c-nxt"/>')
    grid = []
    for k in range(4):
        v = lo + (hi - lo) * (k + 0.5) / 4
        gy = y(v)
        grid.append(f'<line x1="{pad_l}" x2="{pad_l + w}" y1="{_fmt(gy)}" y2="{_fmt(gy)}" class="c-grid"/>'
                    f'<text x="{pad_l + w + 6}" y="{_fmt(gy + 4)}" class="c-axis">{_num(v)}</text>')
    labels = []
    for i in (0, n // 2, n - 1):
        d = dates[i]
        labels.append(f'<text x="{_fmt(x(i))}" y="{height - 4}" class="c-axis" text-anchor="{"start" if i == 0 else "end" if i == n - 1 else "middle"}">{escape(str(d)[5:])}</text>')
    dots = []
    for i in marks or []:
        if 0 <= i < n:
            dots.append(f'<circle cx="{_fmt(x(i))}" cy="{_fmt(y(closes[i]))}" r="4.5" class="c-mark"><title>급등일 {escape(str(dates[i]))}</title></circle>')
    last = closes[-1]
    return (f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="최근 {n}거래일 종가와 거래대금">'
            f'{"".join(grid)}<polygon points="{area}" class="c-area"/><polyline points="{pts}" class="c-line"/>'
            f'{"".join(dots)}<circle cx="{_fmt(x(n - 1))}" cy="{_fmt(y(last))}" r="3.5" class="c-last"/>'
            f'{"".join(bars)}{"".join(labels)}</svg>')


def sparkline(values: list[float | None], width: int = 120, height: int = 32, zero: bool = True) -> str:
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals + ([0] if zero else [])), max(vals + ([0] if zero else []))
    if hi == lo:
        hi += 1
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        if v is None:
            continue
        px = 2 + (width - 4) * i / max(n - 1, 1)
        py = 2 + (height - 4) * (1 - (v - lo) / (hi - lo))
        pts.append(f"{_fmt(px)},{_fmt(py)}")
    zl = ""
    if zero and lo < 0 < hi:
        zy = 2 + (height - 4) * (1 - (0 - lo) / (hi - lo))
        zl = f'<line x1="0" x2="{width}" y1="{_fmt(zy)}" y2="{_fmt(zy)}" class="c-grid"/>'
    return f'<svg viewBox="0 0 {width} {height}" class="spark" aria-hidden="true">{zl}<polyline points="{" ".join(pts)}" class="c-line"/></svg>'


def bars_pm(items: list[tuple[str, float]], max_abs: float) -> list[dict]:
    """점수 구성요소 막대 (가운데 0)."""
    out = []
    for label, v in items:
        pct = 0 if not max_abs else min(50.0, abs(v) / max_abs * 50)
        out.append({"label": label, "value": v, "pct": pct, "neg": v < 0})
    return out


def _num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:,.1f}"
    return f"{v:,.2f}"
