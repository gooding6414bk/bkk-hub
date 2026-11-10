"""뉴스·공시를 호재/악재로 나누고 비율과 요약을 만든다.

서버에 언어 모델 키가 없으므로 금융 용어 사전 방식이다. 어떤 단어 때문에 그렇게 나눴는지(근거)를 같이 보여 준다.
같은 소식이 여러 매체에 실린 것은 제목이 비슷하면 하나로 묶는다.
뉴스는 이미 가격에 반영됐을 가능성이 커서 참고 점수에는 넣지 않는다 (설계 원칙).
"""

from __future__ import annotations

import re
from collections import Counter, OrderedDict

# (분류 이름, 방향, 단어들)
LEXICON: list[tuple[str, str, tuple[str, ...]]] = [
    ("실적 개선", "pos", ("흑자전환", "흑자 전환", "최대 실적", "사상 최대", "역대 최대", "어닝서프라이즈", "어닝 서프라이즈",
                      "깜짝 실적", "호실적", "영업이익 증가", "실적 개선", "매출 증가", "턴어라운드", "컨센서스 상회")),
    ("수주·계약", "pos", ("수주", "공급계약", "공급 계약", "계약 체결", "납품", "단일판매", "수출 계약", "협약 체결")),
    ("주주환원", "pos", ("자사주 매입", "자사주 소각", "자기주식 취득", "주식 소각", "배당 확대", "배당 증가", "특별배당", "밸류업")),
    ("승인·신사업", "pos", ("FDA 승인", "품목허가", "허가 획득", "승인 획득", "특허", "기술이전", "신제품", "양산", "상용화", "임상 성공")),
    ("증권가 상향", "pos", ("목표가 상향", "목표주가 상향", "투자의견 상향", "매수 의견", "비중확대", "최선호주", "탑픽")),
    ("수급 호재", "pos", ("외국인 순매수", "기관 순매수", "외인 순매수", "매수세 유입", "지수 편입", "MSCI 편입")),
    ("주가 강세", "pos", ("상한가", "급등", "신고가", "강세")),
    ("실적 악화", "neg", ("적자전환", "적자 전환", "어닝쇼크", "어닝 쇼크", "영업손실", "순손실", "실적 부진", "실적 악화",
                      "역성장", "컨센서스 하회", "감익", "적자 지속")),
    ("희석·자금조달", "neg", ("유상증자", "전환사채", "CB 발행", "BW 발행", "신주인수권", "교환사채", "제3자배정", "오버행", "보호예수 해제")),
    ("법적·규제", "neg", ("소송", "압수수색", "횡령", "배임", "검찰", "기소", "제재", "과징금", "불공정", "분식", "감리")),
    ("거래 위험", "neg", ("거래정지", "관리종목", "상장폐지", "투자경고", "투자위험", "감사의견", "불성실공시", "의견거절")),
    ("증권가 하향", "neg", ("목표가 하향", "목표주가 하향", "투자의견 하향", "매도 의견", "비중축소")),
    ("수급 악재", "neg", ("블록딜", "대량 매도", "외국인 순매도", "기관 순매도", "공매도 급증", "지수 제외", "차익실현")),
    ("주가 약세", "neg", ("하한가", "급락", "폭락", "신저가", "약세")),
    ("사고·리콜", "neg", ("리콜", "화재", "사고", "결함", "중단", "철수")),
]

_PUNCT = re.compile(r"[\[\]()<>『』「」\"'“”‘’…·,.!?:;/|-]")


def classify_title(title: str) -> dict:
    t = title or ""
    hits: OrderedDict[str, list[str]] = OrderedDict()
    for cat, _, words in LEXICON:
        found = [w for w in words if w in t]
        if found:
            hits[cat] = found
    pos = [c for c in hits if _dir(c) == "pos"]
    neg = [c for c in hits if _dir(c) == "neg"]
    # 주가 강세/약세 단어만 있으면 재료가 아니라 결과라서 약하게 본다
    strong_pos = [c for c in pos if c != "주가 강세"]
    strong_neg = [c for c in neg if c != "주가 약세"]
    if strong_pos and not strong_neg:
        tone = "pos"
    elif strong_neg and not strong_pos:
        tone = "neg"
    elif strong_pos and strong_neg:
        tone = "mixed"
    elif pos and not neg:
        tone = "pos"
    elif neg and not pos:
        tone = "neg"
    else:
        tone = "neutral"
    return {"tone": tone, "categories": list(hits), "words": sorted({w for ws in hits.values() for w in ws})}


def _dir(cat: str) -> str:
    for c, d, _ in LEXICON:
        if c == cat:
            return d
    return ""


def _norm(title: str) -> str:
    t = re.sub(r"\s*-\s*[^-]{1,20}$", "", title or "")     # 끝의 '- 매체명' 제거
    t = _PUNCT.sub(" ", t)
    return " ".join(t.split())


def _similar(a: str, b: str) -> bool:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) >= 0.6


def dedupe(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    keys: list[str] = []
    for it in items:
        k = _norm(it.get("title", ""))
        for i, prev in enumerate(keys):
            if _similar(k, prev):
                out[i]["dupes"] = out[i].get("dupes", 0) + 1
                break
        else:
            out.append(dict(it, dupes=0))
            keys.append(k)
    return out


def summarize(items: list[dict], subject: str = "") -> dict:
    """뉴스 목록 → 분류·비율·요약 문장."""
    uniq = dedupe(items)
    for it in uniq:
        it.update(classify_title(it.get("title", "")))
    n = len(uniq)
    cnt = Counter(it["tone"] for it in uniq)
    cats_pos = Counter(c for it in uniq for c in it["categories"] if _dir(c) == "pos")
    cats_neg = Counter(c for it in uniq for c in it["categories"] if _dir(c) == "neg")
    ratio = {k: (cnt.get(k, 0) / n * 100 if n else 0) for k in ("pos", "neg", "mixed", "neutral")}
    by_cat: dict[str, list[dict]] = {}
    for it in uniq:
        for c in it["categories"]:
            by_cat.setdefault(c, []).append(it)
    lines = []
    if n:
        lines.append(f"최근 {n}건(같은 소식 묶음) 중 호재 {cnt.get('pos', 0)}건({ratio['pos']:.0f}%), "
                     f"악재 {cnt.get('neg', 0)}건({ratio['neg']:.0f}%), 섞임 {cnt.get('mixed', 0)}건, 중립 {cnt.get('neutral', 0)}건.")
        if cats_pos:
            lines.append("호재는 주로 " + ", ".join(f"{c}({k})" for c, k in cats_pos.most_common(3)) + ".")
        if cats_neg:
            lines.append("악재는 주로 " + ", ".join(f"{c}({k})" for c, k in cats_neg.most_common(3)) + ".")
        if not cats_pos and not cats_neg:
            lines.append("눈에 띄는 호재·악재 단어가 없습니다.")
        bal = cnt.get("pos", 0) - cnt.get("neg", 0)
        if n >= 5:
            lines.append("뉴스 분위기는 " + ("긍정 쪽" if bal >= 2 else "부정 쪽" if bal <= -2 else "엇갈림") +
                         "입니다. 뉴스는 대개 이미 가격에 반영돼 있으니 방향 판단의 근거로 쓰지는 마세요.")
    groups = []
    for cat, d, _ in LEXICON:
        if cat in by_cat:
            groups.append({"category": cat, "dir": d, "count": len(by_cat[cat]),
                           "examples": [x["title"] for x in by_cat[cat][:2]]})
    groups.sort(key=lambda g: (-g["count"], g["dir"]))
    return {"items": uniq, "n": n, "counts": dict(cnt), "ratio": ratio, "summary": " ".join(lines), "groups": groups,
            "raw_n": len(items)}


DISC_TONE = {"희석": "neg", "부실": "neg", "매도압력": "neg", "소송": "neg", "주주환원": "pos", "실적": "neutral", "수주": "pos"}


def disclosure_summary(discs: list[dict], days: int = 90) -> dict:
    n = len(discs)
    cnt = Counter()
    tagcnt = Counter()
    for d in discs:
        tags = [t for t in (d.get("tags") or "").split(",") if t and t != "정정"]
        tones = {DISC_TONE.get(t, "neutral") for t in tags} or {"neutral"}
        tone = "mixed" if {"pos", "neg"} <= tones else ("neg" if "neg" in tones else "pos" if "pos" in tones else "neutral")
        cnt[tone] += 1
        tagcnt.update(tags)
    ratio = {k: (cnt.get(k, 0) / n * 100 if n else 0) for k in ("pos", "neg", "mixed", "neutral")}
    return {"n": n, "counts": dict(cnt), "ratio": ratio, "tags": tagcnt.most_common()}
