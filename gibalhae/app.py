"""기발해 콘텐츠 관리 — 혼자 띄우기 (비밀번호 하나짜리 로그인). 다른 보드 앱에 붙일 때는 pipeline/webui.py 의 bp 만 가져가면 된다.

    GIBALHAE_PASSWORD=비밀번호 python gibalhae/app.py          http://localhost:5100/gibalhae
    GIBALHAE_PORT=5100 GIBALHAE_HOST=0.0.0.0 ARBI_INSTANCE=./instance

비밀번호가 없으면 시작하지 않는다(열린 채로 두지 않게). 세션 서명 키는 instance/gibalhae/secret 에 만들어 둔다.
"""
from __future__ import annotations

import hmac
import os
import secrets
import sys

from flask import Flask, abort, redirect, render_template_string, request, session, url_for

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import keys  # noqa: E402
from pipeline.webui import bp  # noqa: E402

LOGIN_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>기발해 콘텐츠 · 로그인</title><link rel="stylesheet" href="{{ url_for('gibalhae.static', filename='gibalhae.css') }}">
<style>.box{max-width:360px;margin:18vh auto;padding:26px;border-radius:14px;background:var(--cell)}.box h1{margin:0 0 14px;font-size:18px}
.box input{width:100%;padding:10px;border:1px solid var(--line2);border-radius:8px;background:var(--cell2);color:var(--fg);margin:6px 0 12px}
.box button{width:100%;height:38px;border:0;border-radius:8px;background:var(--acc);color:#111;font-weight:700;cursor:pointer}</style></head>
<body class="gb"><form class="box" method="post"><h1>기발해 콘텐츠</h1>{% if bad %}<div class="err">비밀번호가 틀립니다</div>{% endif %}
<input type="password" name="password" placeholder="비밀번호" autofocus autocomplete="current-password"><button type="submit">들어가기</button></form></body></html>"""


def _secret() -> str:
    p = os.path.join(keys.INSTANCE, "gibalhae", "secret")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or secrets.token_hex(32)
    except OSError:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        s = secrets.token_hex(32)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return s


def create_app(password: str | None = None) -> Flask:
    pw = password or os.environ.get("GIBALHAE_PASSWORD", "")
    if not pw:
        raise SystemExit("GIBALHAE_PASSWORD 를 정해야 시작합니다")
    app = Flask(__name__)
    app.secret_key = _secret()
    app.config.update(GIBALHAE_HOME_URL="/gibalhae", GIBALHAE_HOME_LABEL="기발해", GIBALHAE_LOGOUT_URL="/logout",
                      GIBALHAE_ALLOW=lambda: bool(session.get("ok")), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    @app.before_request
    def _gate():
        if request.endpoint in ("login", "gibalhae.static") or request.path.startswith("/gibalhae/static/"):
            return None
        if request.endpoint == "logout":
            return None
        if not session.get("ok"):
            if request.path.startswith("/api/"):
                return {"error": "로그인이 필요합니다", "login": url_for("login")}, 401
            return redirect(url_for("login", next=request.path))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
            if origin and request.host not in origin:
                abort(403)
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        bad = False
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("password", ""), pw):
                session["ok"] = True
                nxt = request.args.get("next") or "/gibalhae"
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/gibalhae")
            bad = True
        return render_template_string(LOGIN_HTML, bad=bad)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def root():
        return redirect("/gibalhae")

    app.register_blueprint(bp)
    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host=os.environ.get("GIBALHAE_HOST", "127.0.0.1"), port=int(os.environ.get("GIBALHAE_PORT", 5100)), debug=False)
