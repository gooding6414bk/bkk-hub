"""유튜브 업로드 인증 — 내 PC 에서 한 번만 돌린다(브라우저로 구글 로그인). 결과 JSON 한 줄을 비밀값으로 보관한다.

준비 (10분):
  1. https://console.cloud.google.com → 프로젝트 만들기(이름 아무거나, 예: gibalhae)
  2. API 및 서비스 → 라이브러리 → "YouTube Data API v3" 사용 설정
  3. OAuth 동의 화면 → 외부 → 앱 이름·이메일만 채움 → 테스트 사용자에 업로드할 구글 계정 추가
     (게시 상태 '테스트' 면 토큰이 7일마다 만료된다 → '프로덕션으로 게시' 를 누르면 만료되지 않는다. 검토는 필요 없다)
  4. 사용자 인증 정보 → OAuth 클라이언트 ID → 데스크톱 앱 → JSON 다운로드 → client_secret.json 으로 저장
  5. 터미널:
        pip install google-api-python-client google-auth-oauthlib
        python gibalhae/pipeline/youtube_auth.py client_secret.json
     브라우저가 열리면 유튜브 채널이 있는 계정으로 로그인·허용.
  6. 화면에 찍힌 JSON 한 줄을
        - 클라우드 세션: 환경 설정 → 비밀값(환경변수) YT_TOKEN_JSON 에 붙여넣기
        - 내 PC: gibalhae/secrets/youtube_token.json 으로 저장 (git 무시)
     채팅에는 붙여넣지 않는다.
"""
from __future__ import annotations

import json
import os
import sys

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]


def main(argv: list[str]) -> int:
    if not argv or not os.path.exists(argv[0]):
        print(__doc__)
        return 2
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(argv[0], SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    tok = {"token": creds.token, "refresh_token": creds.refresh_token, "token_uri": creds.token_uri,
           "client_id": creds.client_id, "client_secret": creds.client_secret, "scopes": list(creds.scopes or SCOPES)}
    if not creds.refresh_token:
        print("FAIL refresh_token 이 없다 — 구글 계정 → 보안 → 서드파티 앱에서 이 앱 접근을 지우고 다시 돌린다")
        return 1
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "youtube_token.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tok, f, ensure_ascii=False)
    print(f"저장: {path}\n\n클라우드 환경 비밀값 YT_TOKEN_JSON 에 넣을 한 줄:\n")
    print(json.dumps(tok, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
