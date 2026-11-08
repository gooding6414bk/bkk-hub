"""연결 키 보관·확인 — instance/gibalhae/keys.json (git 무시, 서버마다 따로). 대시보드 「연결」 탭이 여기를 읽고 쓴다.

우선순위: 이 파일 → 환경변수 → (클라우드 세션) 프록시 주입. 값은 로그·화면에 절대 그대로 내보내지 않는다(끝 4자리만).

    python gibalhae/pipeline/keys.py            상태만 (값 없음)
    python gibalhae/pipeline/keys.py check json2video
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # 저장소 맨 위
INSTANCE = os.environ.get("ARBI_INSTANCE") or os.path.join(_ROOT, "instance")
FILE = os.path.join(INSTANCE, "gibalhae", "keys.json")

# 이름 → (화면 이름, 환경변수, 어디서 받나, 한 줄 설명)
GUIDE = {
    "json2video":      ("JSON2Video API 키", "JSON2VIDEO_API_KEY", "json2video.com → Dashboard → API key",
                        "쇼츠 렌더(배경·음성·자막). 1초 1크레딧. 무료 600(워터마크·비상업), 채널 운영은 Hobby 이상"),
    "pexels":          ("Pexels API 키", "PEXELS_API_KEY", "pexels.com/api → Get started (무료)",
                        "얼굴 없는 배경 영상. 없으면 어두운 그라데이션 배경"),
    "elevenlabs":      ("ElevenLabs API 키 (선택)", "ELEVENLABS_API_KEY", "elevenlabs.io → Profile → API keys",
                        "JSON2Video 가 ElevenLabs 음성을 이미 포함하므로 보통 필요 없다. 내 계정 음성(복제 등)을 쓸 때만"),
    "youtube_token":   ("유튜브 토큰 (JSON 한 줄)", "YT_TOKEN_JSON", "내 PC: python gibalhae/pipeline/youtube_auth.py client_secret.json",
                        "쇼츠 예약 업로드. 구글 로그인 한 번으로 만든 JSON 을 통째로 붙여넣는다"),
    "wp_url":          ("워드프레스 주소 (선택)", "WP_URL", "https://내블로그.com", "네이버 대신/병행해 워드프레스에 바로 올릴 때"),
    "wp_user":         ("워드프레스 아이디 (선택)", "WP_USER", "", ""),
    "wp_app_password": ("워드프레스 응용 프로그램 비밀번호 (선택)", "WP_APP_PASSWORD", "프로필 → 응용 프로그램 비밀번호", ""),
}
SECRET = {"json2video", "pexels", "elevenlabs", "youtube_token", "wp_app_password"}
CHECKABLE = ("json2video", "pexels", "elevenlabs", "youtube_token", "wordpress")


def load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(patch: dict) -> dict:
    """빈 문자열이면 지운다. 모르는 이름은 무시."""
    d = load()
    for k, v in patch.items():
        if k not in GUIDE:
            continue
        v = (v or "").strip() if isinstance(v, str) else ""
        if k == "youtube_token" and v:
            try:
                tok = json.loads(v)
                if not isinstance(tok, dict) or not tok.get("refresh_token") or not tok.get("client_id"):
                    raise ValueError
                v = json.dumps(tok, ensure_ascii=False)
            except ValueError:
                raise ValueError("유튜브 토큰은 youtube_auth.py 가 찍어 준 JSON 한 줄이어야 합니다 (refresh_token · client_id 포함)")
        if k == "wp_url" and v:
            v = v.rstrip("/")
            if not v.startswith("http"):
                v = "https://" + v
        if v:
            d[k] = v
        else:
            d.pop(k, None)
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, FILE)
    return d


def get(name: str) -> str | None:
    v = load().get(name)
    if v:
        return v
    env = GUIDE.get(name, (None, None))[1]
    return os.environ.get(env) if env else None


def hint(name: str) -> str:
    v = get(name)
    if not v:
        return ""
    if name == "youtube_token":
        try:
            return "client … " + json.loads(v).get("client_id", "")[:12] + "…"
        except ValueError:
            return "••••"
    if name in SECRET:
        return "••••" + v[-4:]
    return v


def public() -> dict:
    """화면용: 값 없이 설정 여부·힌트·출처."""
    stored = load()
    out = {}
    for k, (label, env, where, desc) in GUIDE.items():
        src = "file" if stored.get(k) else ("env" if os.environ.get(env) else "")
        out[k] = {"label": label, "where": where, "desc": desc, "set": bool(get(k)), "hint": hint(k), "source": src,
                  "secret": k in SECRET}
    return out


# ------------------------------------------------------------------ 확인 --
def _http(url: str, headers: dict | None = None, data: bytes | None = None, method: str = "GET", timeout: int = 20) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "gibalhae-pipeline", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        body, code = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:                       # 연결 실패·시간 초과
        return 0, f"연결 실패: {e}"
    try:
        return code, json.loads(body)
    except ValueError:
        return code, body[:300]


def check(name: str) -> dict:
    """키로 가볍게 한 번 불러 본다. {ok, detail} — 값은 돌려주지 않는다."""
    if name == "json2video":
        k = get("json2video") or "proxy-injected"
        code, d = _http("https://api.json2video.com/v2/movies?limit=1", {"x-api-key": k})
        if isinstance(d, dict) and d.get("success"):
            q = (d.get("remaining_quota") or {}).get("time")
            return {"ok": True, "detail": f"연결됨 · 남은 크레딧 {q}초" + (" (≈ 쇼츠 %d편)" % (q // 55) if isinstance(q, (int, float)) else "")}
        return {"ok": False, "detail": (d.get("message") if isinstance(d, dict) else str(d)) or f"HTTP {code}"}
    if name == "pexels":
        k = get("pexels")
        if not k:
            return {"ok": False, "detail": "키 없음"}
        code, d = _http("https://api.pexels.com/videos/search?query=bedroom&per_page=1&orientation=portrait", {"Authorization": k})
        if code == 200 and isinstance(d, dict):
            return {"ok": True, "detail": f"연결됨 · 'bedroom' 세로 영상 {d.get('total_results', '?')}개"}
        return {"ok": False, "detail": (d.get("error") if isinstance(d, dict) else str(d)) or f"HTTP {code}"}
    if name == "elevenlabs":
        k = get("elevenlabs")
        if not k:
            return {"ok": False, "detail": "키 없음 (선택 항목)"}
        code, d = _http("https://api.elevenlabs.io/v1/user/subscription", {"xi-api-key": k})
        if code == 200 and isinstance(d, dict):
            return {"ok": True, "detail": f"연결됨 · {d.get('tier', '?')} · 글자 {d.get('character_count', '?')}/{d.get('character_limit', '?')}"}
        return {"ok": False, "detail": (str((d.get('detail') or {}).get('message', d.get('detail'))) if isinstance(d, dict) else str(d)) or f"HTTP {code}"}
    if name == "youtube_token":
        tok = youtube_access_token()
        if not tok.get("ok"):
            return tok
        code, d = _http("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", {"Authorization": "Bearer " + tok["token"]})
        if code == 200 and isinstance(d, dict) and d.get("items"):
            return {"ok": True, "detail": "연결됨 · 채널 " + d["items"][0]["snippet"]["title"]}
        return {"ok": False, "detail": (d.get("error", {}).get("message") if isinstance(d, dict) else str(d)) or f"HTTP {code} (채널이 없는 계정?)"}
    if name == "wordpress":
        url, user, pw = get("wp_url"), get("wp_user"), get("wp_app_password")
        if not (url and user and pw):
            return {"ok": False, "detail": "주소·아이디·응용 프로그램 비밀번호 셋 다 필요 (선택 항목)"}
        auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
        code, d = _http(f"{url}/wp-json/wp/v2/users/me?context=edit", {"Authorization": "Basic " + auth})
        if code == 200 and isinstance(d, dict):
            return {"ok": True, "detail": f"연결됨 · {d.get('name')} ({', '.join(d.get('roles') or [])})"}
        return {"ok": False, "detail": (d.get("message") if isinstance(d, dict) else str(d)) or f"HTTP {code}"}
    return {"ok": False, "detail": "모르는 항목"}


def youtube_access_token() -> dict:
    raw = get("youtube_token")
    if not raw:
        return {"ok": False, "detail": "토큰 없음 — youtube_auth.py 로 만든 JSON 을 넣는다"}
    try:
        t = json.loads(raw)
    except ValueError:
        return {"ok": False, "detail": "토큰이 JSON 이 아니다"}
    data = urllib.parse.urlencode({"client_id": t.get("client_id", ""), "client_secret": t.get("client_secret", ""),
                                   "refresh_token": t.get("refresh_token", ""), "grant_type": "refresh_token"}).encode()
    code, d = _http(t.get("token_uri") or "https://oauth2.googleapis.com/token", {"Content-Type": "application/x-www-form-urlencoded"}, data, "POST")
    if code == 200 and isinstance(d, dict) and d.get("access_token"):
        return {"ok": True, "token": d["access_token"]}
    msg = d.get("error_description") or d.get("error") if isinstance(d, dict) else str(d)
    if "invalid_grant" in str(msg):
        msg = "토큰 만료 또는 취소됨 — 구글 클라우드 OAuth 동의 화면이 '테스트' 상태면 7일마다 만료된다. '프로덕션으로 게시' 뒤 youtube_auth.py 다시"
    return {"ok": False, "detail": str(msg) or f"HTTP {code}"}


def main(argv: list[str]) -> int:
    if argv[:1] == ["check"] and len(argv) > 1:
        r = check(argv[1])
        print(("ok   " if r["ok"] else "FAIL ") + r["detail"])
        return 0 if r["ok"] else 1
    for k, v in public().items():
        print(f"{'설정됨' if v['set'] else '없음  '}  {k:<16} {v['hint']}  {v['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
