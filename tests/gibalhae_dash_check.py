"""기발해 콘텐츠 관리 화면 (통합 보드 bkk hub 안) — /gibalhae 화면·API (관리자만), 키 저장·마스킹, 글 고치기, 작업 실행(daily.py 승인·검수).

실행: python tests/gibalhae_dash_check.py  (pytest에서는 tests/test_gibalhae.py가 이 스크립트를 돌린다)   (네트워크 없음 — 키 확인은 연결 실패가 보고되는지만 본다)
"""
import json, os, shutil, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CONTENT = tempfile.mkdtemp(prefix="gibalhae-dash-")
os.environ["GIBALHAE_CONTENT"] = CONTENT
shutil.copytree(os.path.join(ROOT, "gibalhae", "content", "_example"), os.path.join(CONTENT, "2026-10-24"))
# 통합 보드(bkk hub) 설정: 임시 .env·데이터·instance
_TMP = tempfile.mkdtemp(prefix="hub-dash-")
with open(os.path.join(_TMP, ".env"), "w") as f:
    f.write("DASH_PASSWORD=test-pass-123\nDASH_SECRET=s\n")
os.environ["BKK_STOCK_ENV"] = os.path.join(_TMP, ".env")
os.environ["BKK_STOCK_DATA"] = os.path.join(_TMP, "data")
os.environ["ARBI_INSTANCE"] = os.path.join(_TMP, "instance")
from bkkstock.web import app as appmod             # noqa: E402

c = appmod.app.test_client()
ok = 0
def check(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok += bool(cond)
    return cond

check(c.get("/gibalhae").status_code in (302, 401), "로그인 전 /gibalhae 막힘")
c.post("/login", data={"password": "test-pass-123"})
with c.session_transaction() as _s:                 # 보드의 CSRF 토큰 (gibalhae.js는 <meta name="csrf">에서 읽어 보냄)
    c.environ_base["HTTP_X_CSRF"] = _s["csrf"]
r = c.get("/media"); check(r.status_code == 200 and "🎬 영상".encode() in r.data and "gibalhae.js" in r.data.decode(), "/media 화면 (예전 /gibalhae 도 열림)")
r = c.get("/"); check("🎬".encode() in r.data and 'href="/media"' in r.data.decode() and "기발해".encode() not in r.data, "보드 상단 🎬 메뉴 · 메인에 이름 안 보임")

o = c.get("/api/gibalhae/overview").get_json()
check(o["days"] and o["days"][0]["date"] == "2026-10-24" and o["days"][0]["shorts"]["status"] == "draft", "overview: 예시 날짜 draft")
check("json2video" in o["keys"] and o["keys"]["json2video"]["set"] is False and len(o["topics"]) >= 16, "overview: 키 상태·소재 목록")

d = c.get("/api/gibalhae/content/2026-10-24").get_json()
check(d["shorts"]["title"].startswith("침대") and d["checks"]["shorts"]["fails"] == [] and "---" in d["raw"]["blog"], "content: 대본·글·검수")
check(c.get("/api/gibalhae/content/2099-01-01").status_code == 404, "없는 날짜 404")
check(c.get("/api/gibalhae/content/2026-10-24/build/../../shorts.md").status_code == 404, "build 파일 경로 탈출 막힘")

# 글 고치기 → 금지 표현 검수
bad = d["raw"]["blog"].replace("필요하면 방문도 합니다.", "사무실로 오세요.")
r = c.put("/api/gibalhae/content/2026-10-24/blog", json={"text": bad}).get_json()
check(r["saved"] and any("사무실" in f for f in r["fails"]), "글 저장 + 금지 표현 잡힘")
r = c.put("/api/gibalhae/content/2026-10-24/blog", json={"text": d["raw"]["blog"]}).get_json()
check(r["saved"] and r["fails"] == [], "글 되돌림")
check(c.put("/api/gibalhae/content/2026-10-24/blog", json={"text": "제목 없음"}).status_code == 400, "머리말 없는 글은 거부")

# 키 저장·마스킹·확인
r = c.post("/api/gibalhae/keys", json={"json2video": "abcd1234efgh5678", "wp_url": "myblog.com/", "nope": "x"}).get_json()
check(r["saved"] == ["json2video", "wp_url"] and r["keys"]["json2video"]["hint"] == "••••5678" and r["keys"]["wp_url"]["hint"] == "https://myblog.com", "키 저장 · 끝 4자리만 · 주소 정리")
raw = json.dumps(c.get("/api/gibalhae/keys").get_json())
check("abcd1234" not in raw and "5678" in raw, "키 값은 화면에 안 나감")
from pipeline import keys as K          # noqa: E402  (fakeapp 이 ARBI_INSTANCE 를 임시로 둔다)
check(os.path.exists(K.FILE) and K.get("json2video") == "abcd1234efgh5678", f"instance 에 저장 {K.FILE}")
check(c.post("/api/gibalhae/keys", json={"youtube_token": "not json"}).status_code == 400, "유튜브 토큰 형식 검사")
r = c.post("/api/gibalhae/keys/json2video/check").get_json()
check(r["ok"] is False and r["detail"], f"키 확인(네트워크 없음): {r['detail'][:60]}")
check(c.post("/api/gibalhae/keys/nope/check").status_code == 400, "모르는 항목 확인 400")
r = c.post("/api/gibalhae/keys", json={"json2video": ""}).get_json()
check(r["keys"]["json2video"]["set"] is False, "키 지우기")

# 작업: 검수 → 승인(쇼츠만) → 반려
def run(cmd, date, args=None):
    r = c.post("/api/gibalhae/action", json={"cmd": cmd, "date": date, "args": args or []}).get_json()
    jid = r["job"]["id"]
    for _ in range(100):
        j = c.get(f"/api/gibalhae/jobs/{jid}").get_json()
        if j["status"] != "running":
            return j
        time.sleep(0.2)
    return j
j = run("check", "2026-10-24"); check(j["status"] == "ok" and "금지 표현 없음" in j["log"], "작업: 검수")
j = run("approve", "2026-10-24", ["--shorts"]); check(j["status"] == "ok" and "승인" in j["log"], "작업: 쇼츠만 승인")
o = c.get("/api/gibalhae/overview").get_json()["days"][0]
check(o["shorts"]["status"] == "approved" and o["blog"]["status"] == "draft", "승인 뒤 상태 반영")
j = run("reject", "2026-10-24", ["--shorts", "--why", "너무 길다"]); check(j["status"] == "ok", "작업: 반려")
check(c.get("/api/gibalhae/content/2026-10-24").get_json()["shorts"]["meta"]["reject_reason"] == "너무 길다", "반려 사유 기록")
check(c.post("/api/gibalhae/action", json={"cmd": "rm", "date": "2026-10-24"}).status_code == 400, "모르는 작업 400")
check(c.post("/api/gibalhae/action", json={"cmd": "approve", "date": "2026-10-24", "args": ["; rm -rf /"]}).status_code == 400, "이상한 인자 400")
check(len(c.get("/api/gibalhae/jobs").get_json()["jobs"]) >= 3, "작업 목록")

shutil.rmtree(CONTENT, ignore_errors=True)
print(f"통과 {ok}")
sys.exit(0 if ok == 26 else 1)
