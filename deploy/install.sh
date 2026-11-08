#!/usr/bin/env bash
# bkk hub 설치 (📈 주식 + 🎬 기발해 통합 보드). 저장소 폴더 안에서 일반 사용자(ubuntu)로 실행한다:  bash deploy/install.sh
# 같은 서버(Ubuntu-1)의 갭 차익거래 봇과는 서비스·포트·폴더·텔레그램 봇이 모두 따로다.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
USER_NAME="$(whoami)"
PORT="${BKK_STOCK_PORT:-8020}"

if [ "$USER_NAME" = "root" ]; then
  echo "root가 아닌 일반 사용자(ubuntu)로 실행하세요. 필요한 곳에서만 sudo를 씁니다." >&2
  exit 1
fi

echo "== 1/5 파이썬 가상환경"
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 2/5 설정 파일 (.env)"
[ -f .env ] || cp .env.example .env
chmod 600 .env
NEWPW="$(.venv/bin/python - <<'PY'
import secrets
from pathlib import Path
from bkkstock.config import parse_env_text, write_env_values
p = Path(".env")
v = parse_env_text(p.read_text(encoding="utf-8"))
up = {}
pw = ""
if not v.get("DASH_PASSWORD"):
    pw = secrets.token_urlsafe(12)
    up["DASH_PASSWORD"] = pw
if not v.get("DASH_SECRET"):
    up["DASH_SECRET"] = secrets.token_hex(32)
if up:
    write_env_values(p, up)
print(pw)
PY
)"

echo "== 3/5 데이터 폴더"
mkdir -p data instance/gibalhae/content
chmod 700 data instance
# 기발해 콘텐츠는 git 밖(instance/gibalhae/content)에 둔다. 저장소의 초안을 처음 한 번 복사
GIBALHAE_CONTENT="$DIR/instance/gibalhae/content" .venv/bin/python -m bkkstock gibalhae-sync
# 로컬 렌더(--engine local)를 쓸 때만 필요. 기본(JSON2Video)에는 없어도 된다
command -v ffmpeg >/dev/null 2>&1 || echo "(참고) ffmpeg 없음: 기발해 로컬 렌더를 쓰려면 sudo apt-get install -y ffmpeg"

echo "== 4/5 테스트"
if .venv/bin/python -m pip show pytest >/dev/null 2>&1 || .venv/bin/pip install -q pytest; then
  .venv/bin/python -m pytest -q -p no:cacheprovider tests >/tmp/bkk-hub-test.log 2>&1 \
    && echo "테스트 통과" || { echo "테스트 실패. /tmp/bkk-hub-test.log 를 확인하세요."; exit 1; }
fi

echo "== 5/5 systemd 서비스와 타이머"
sudo BKK_DIR="$DIR" BKK_USER="$USER_NAME" BKK_PORT="$PORT" bash deploy/units.sh

echo
echo "메모리와 CPU (코인 봇과 같이 쓰므로 확인):"
free -h | head -2
echo "CPU $(nproc)개 · 부하 $(cut -d' ' -f1-3 /proc/loadavg)"
echo
if [ -n "$NEWPW" ]; then
  echo "대시보드 비밀번호: $NEWPW"
  echo "(.env의 DASH_PASSWORD에 저장됨. 이 화면을 닫기 전에 적어 두세요.)"
fi
cat <<MSG

다음 순서
1) 키 넣기:   nano $DIR/.env   (또는 SSH 터널로 대시보드 API 관리 화면에서)
2) 점검:      cd $DIR && .venv/bin/python -m bkkstock first-week-check
3) 과거 시세: cd $DIR && nice -n 15 .venv/bin/python -m bkkstock backfill --days 90
4) 보드:      PC에서  ssh -L $PORT:localhost:$PORT ubuntu@서버IP   →   http://localhost:$PORT
5) 기발해:    보드 → 🎬 기발해 → 연결 탭에서 JSON2Video·유튜브 키 넣기
MSG
