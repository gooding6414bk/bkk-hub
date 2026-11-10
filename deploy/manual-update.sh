#!/usr/bin/env bash
# 자동 업데이트가 '직접 반영할 변경'이라고 알렸을 때 (의존성·systemd 파일 변경). 서버에서 주인이 실행.
#   cd ~/bkk-hub && git merge --ff-only origin/main && bash deploy/manual-update.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
.venv/bin/pip install -q -r requirements.txt
sudo BKK_DIR="$DIR" BKK_USER="$(whoami)" BKK_PORT="${BKK_STOCK_PORT:-8020}" bash deploy/units.sh
sudo systemctl restart bkk-hub-web.service
echo "반영 완료. 보드가 열리는지 확인하세요."
