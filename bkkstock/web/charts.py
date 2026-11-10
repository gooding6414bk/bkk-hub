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


def candle_chart(dates: list, o: list, h: list, l: list, c: list, values: list, nxt_values: list | None = None,
                 mas: tuple = (5, 20, 60), marks: list[int] | None = None, width: int = 760, height: int = 320) -> str:
    """캔들 + 이동평균 + 거래대금(KRX 진한, NXT 연한). 상승 빨강, 하락 파랑 (한국 관례)."""
    n = len(c)
    if n < 2:
        return '<p class="muted">차트를 그릴 이력이 아직 없습니다.</p>'
    pad_l, pad_r, pad_t = 6, 58, 8
    price_h = height * 0.70
    vol_top = price_h + 14
    vol_h = height - vol_top - 18
    w = width - pad_l - pad_r
    lows = [x for x in l if x == x and x is not None] or [min(c)]
    highs = [x for x in h if x == x and x is not None] or [max(c)]
    lo, hi = min(lows), max(highs)
    if hi == lo:
        hi, lo = hi * 1.01 + 1, lo * 0.99
    span = hi - lo
    lo -= span * 0.04
    hi += span * 0.04
    step = w / n
    bw = max(1.0, step * 0.62)

    def x(i):
        return pad_l + step * (i + 0.5)

    def y(v):
        return pad_t + (price_h - pad_t) * (1 - (v - lo) / (hi - lo))

    parts = []
    for k in range(4):
        v = lo + (hi - lo) * (k + 0.5) / 4
        parts.append(f'<line x1="{pad_l}" x2="{pad_l + w}" y1="{_fmt(y(v))}" y2="{_fmt(y(v))}" class="c-grid"/>'
                     f'<text x="{pad_l + w + 6}" y="{_fmt(y(v) + 4)}" class="c-axis">{_num(v)}</text>')
    for i in range(n):
        oi, hi_, li, ci = o[i], h[i], l[i], c[i]
        if ci is None or ci != ci:
            continue
        oi = oi if (oi is not None and oi == oi and oi > 0) else ci
        hi_ = hi_ if (hi_ is not None and hi_ == hi_ and hi_ > 0) else max(oi, ci)
        li = li if (li is not None and li == li and li > 0) else min(oi, ci)
        cls = "c-up" if ci > oi else "c-down" if ci < oi else "c-flat"
        parts.append(f'<line x1="{_fmt(x(i))}" x2="{_fmt(x(i))}" y1="{_fmt(y(hi_))}" y2="{_fmt(y(li))}" class="{cls} wick"/>')
        top, bot = y(max(oi, ci)), y(min(oi, ci))
        parts.append(f'<rect x="{_fmt(x(i) - bw / 2)}" y="{_fmt(top)}" width="{_fmt(bw)}" height="{_fmt(max(1.0, bot - top))}" class="{cls}"/>')
    ma_cls = {5: "c-ma5", 20: "c-ma20", 60: "c-ma60"}
    for m in mas:
        if n < m:
            continue
        pts = []
        for i in range(m - 1, n):
            win = [v for v in c[i - m + 1:i + 1] if v is not None and v == v]
            if len(win) == m:
                pts.append(f"{_fmt(x(i))},{_fmt(y(sum(win) / m))}")
        if len(pts) > 1:
            parts.append(f'<polyline points="{" ".join(pts)}" class="c-ma {ma_cls.get(m, "c-ma20")}"><title>{m}일 이동평균</title></polyline>')
    tot = [(values[i] or 0) + ((nxt_values[i] or 0) if nxt_values else 0) for i in range(n)]
    vmax = max(tot) or 1
    base = vol_top + vol_h
    for i in range(n):
        hk = vol_h * (values[i] or 0) / vmax
        hn = vol_h * ((nxt_values[i] or 0) if nxt_values else 0) / vmax
        parts.append(f'<rect x="{_fmt(x(i) - bw / 2)}" y="{_fmt(base - hk)}" width="{_fmt(bw)}" height="{_fmt(hk)}" class="c-vol"/>')
        if hn > 0:
            parts.append(f'<rect x="{_fmt(x(i) - bw / 2)}" y="{_fmt(base - hk - hn)}" width="{_fmt(bw)}" height="{_fmt(hn)}" class="c-nxt"/>')
    for i in marks or []:
        if 0 <= i < n and c[i] == c[i]:
            top_v = h[i] if (h[i] is not None and h[i] == h[i] and h[i] > 0) else c[i]
            parts.append(f'<circle cx="{_fmt(x(i))}" cy="{_fmt(y(top_v) - 7)}" r="3.5" class="c-mark"><title>급등일 {escape(str(dates[i]))}</title></circle>')
    for i in (0, n // 2, n - 1):
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        parts.append(f'<text x="{_fmt(x(i))}" y="{height - 4}" class="c-axis" text-anchor="{anchor}">{escape(str(dates[i])[5:])}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="최근 {n}거래일 캔들, 이동평균, 거래대금">'
            + "".join(parts) + "</svg>")


def multi_line(series: list[tuple[str, list, str]], dates: list, width: int = 760, height: int = 220,
               zero: float | None = None, fmt=None, unit: str = "") -> str:
    """여러 선 (이름, 값들, css 클래스). zero가 있으면 그 값에 기준선."""
    vals = [v for _, s, _ in series for v in s if v is not None and v == v]
    n = max((len(s) for _, s, _ in series), default=0)
    if len(vals) < 2 or n < 2:
        return '<p class="muted">자료가 아직 부족합니다.</p>'
    fmt = fmt or _num
    pad_l, pad_r, pad_t, pad_b = 6, 66, 10, 20
    lo, hi = min(vals + ([zero] if zero is not None else [])), max(vals + ([zero] if zero is not None else []))
    if hi == lo:
        hi += 1
    span = hi - lo
    lo -= span * 0.06
    hi += span * 0.06
    w = width - pad_l - pad_r
    hgt = height - pad_t - pad_b

    def x(i):
        return pad_l + w * i / (n - 1)

    def y(v):
        return pad_t + hgt * (1 - (v - lo) / (hi - lo))

    parts = []
    for k in range(4):
        v = lo + (hi - lo) * (k + 0.5) / 4
        parts.append(f'<line x1="{pad_l}" x2="{pad_l + w}" y1="{_fmt(y(v))}" y2="{_fmt(y(v))}" class="c-grid"/>'
                     f'<text x="{pad_l + w + 6}" y="{_fmt(y(v) + 4)}" class="c-axis">{fmt(v)}{unit}</text>')
    if zero is not None:
        parts.append(f'<line x1="{pad_l}" x2="{pad_l + w}" y1="{_fmt(y(zero))}" y2="{_fmt(y(zero))}" class="c-zero"/>')
    legend = []
    for k, (name, s, cls) in enumerate(series):
        pts = [f"{_fmt(x(i))},{_fmt(y(v))}" for i, v in enumerate(s) if v is not None and v == v]
        if len(pts) > 1:
            parts.append(f'<polyline points="{" ".join(pts)}" class="c-series {cls}"><title>{escape(name)}</title></polyline>')
            lastv = next((v for v in reversed(s) if v is not None and v == v), None)
            legend.append(f'<span class="lg {cls}"><i></i>{escape(name)} {fmt(lastv) if lastv is not None else ""}{unit}</span>')
    for i in (0, n // 2, n - 1):
        if i < len(dates):
            anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
            parts.append(f'<text x="{_fmt(x(i))}" y="{height - 4}" class="c-axis" text-anchor="{anchor}">{escape(str(dates[i])[5:])}</text>')
    return (f'<div class="legend-row">{"".join(legend)}</div><svg viewBox="0 0 {width} {height}" class="chart" role="img">'
            + "".join(parts) + "</svg>")


def tone_bar(ratio: dict) -> list[dict]:
    order = [("pos", "호재"), ("mixed", "섞임"), ("neutral", "중립"), ("neg", "악재")]
    return [{"key": k, "label": lbl, "pct": float(ratio.get(k, 0) or 0)} for k, lbl in order if (ratio.get(k, 0) or 0) > 0]
