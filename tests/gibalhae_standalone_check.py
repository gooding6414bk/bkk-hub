"""기발해 콘텐츠 관리 — 혼자 띄우기(gibalhae/app.py): 비밀번호 로그인, 자체 템플릿·정적 파일, 블루프린트 API 가 호스트 앱 없이 돈다.

실행: python tests/gibalhae_standalone_check.py  (pytest에서는 tests/test_gibalhae.py가 돌린다)
"""
import os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gibalhae-alone-")
os.environ["ARBI_INSTANCE"] = os.path.join(TMP, "instance")
os.environ["GIBALHAE_CONTENT"] = os.path.join(TMP, "content")
shutil.copytree(os.path.join(ROOT, "gibalhae", "content", "_example"), os.path.join(TMP, "content", "2026-10-24"))
sys.path.insert(0, os.path.join(ROOT, "gibalhae"))
import app as alone  # noqa: E402

ok = 0
def check(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok += bool(cond)

try:
    alone.create_app("")
    check(False, "비밀번호 없으면 시작 안 함")
except SystemExit:
    check(True, "비밀번호 없으면 시작 안 함")

app = alone.create_app("pw-1234")
c = app.test_client()
r = c.get("/gibalhae"); check(r.status_code == 302 and "/login" in r.headers["Location"], "로그인 전 /gibalhae → 로그인")
check(c.get("/api/gibalhae/overview").status_code == 401, "로그인 전 API 401")
r = c.post("/login", data={"password": "wrong"}); check(r.status_code == 200 and "틀립니다".encode() in r.data, "틀린 비밀번호")
r = c.post("/login?next=/gibalhae", data={"password": "pw-1234"}); check(r.status_code == 302 and r.headers["Location"].endswith("/gibalhae"), "로그인")
r = c.get("/gibalhae"); check(r.status_code == 200 and "기발해 콘텐츠".encode() in r.data and "/gibalhae/static/gibalhae.js" in r.data.decode(), "/gibalhae 화면 + 자체 정적 파일 경로")
check(c.get("/gibalhae/static/gibalhae.css").status_code == 200 and c.get("/gibalhae/static/gibalhae.js").status_code == 200, "자체 CSS·JS 서빙")
o = c.get("/api/gibalhae/overview").get_json(); check(o["days"][0]["date"] == "2026-10-24", "overview API")
r = c.post("/api/gibalhae/keys", json={"pexels": "pxl-key-0001"}).get_json(); check(r["keys"]["pexels"]["hint"] == "••••0001", "키 저장")
check(os.path.exists(os.path.join(TMP, "instance", "gibalhae", "keys.json")), "instance 에 키 파일")
check(c.post("/api/gibalhae/keys", json={"pexels": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403, "다른 출처 POST 403")
r = c.post("/logout"); check(r.status_code == 302 and c.get("/api/gibalhae/overview").status_code == 401, "로그아웃")

shutil.rmtree(TMP, ignore_errors=True)
print(f"통과 {ok}")
sys.exit(0 if ok == 12 else 1)
