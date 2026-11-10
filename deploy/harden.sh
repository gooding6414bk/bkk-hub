#!/usr/bin/env bash
# 보호 설정(systemd drop-in)을 설치한다. 자동 업데이트로는 적용되지 않는다 — 주인이 한 번 직접 실행.
#   설치:   sudo bash deploy/harden.sh
#   되돌림: sudo bash deploy/harden.sh undo
# 설치 뒤 보드가 열리는지, 'sudo systemctl start bkk-stock@watchdog' 가 성공하는지 확인한다.
set -euo pipefail
DIR="${BKK_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
USER_NAME="${BKK_USER:-$(stat -c %U "$DIR")}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
GAPDIR="${BKK_GAP_DIR:-$(dirname "$DIR")/bkk-gap}"
SRC="$DIR/deploy/hardening"
declare -A MAP=([web.conf]=bkk-hub-web.service [jobs.conf]=bkk-stock@.service [update.conf]=bkk-hub-update.service)

if [ "${1:-}" = "undo" ]; then
  for f in "${!MAP[@]}"; do rm -f "/etc/systemd/system/${MAP[$f]}.d/50-bkk-hardening.conf"; done
  systemctl daemon-reload
  systemctl restart bkk-hub-web.service
  echo "보호 설정을 뺐습니다."
  exit 0
fi

for f in "${!MAP[@]}"; do
  d="/etc/systemd/system/${MAP[$f]}.d"
  mkdir -p "$d"
  sed -e "s#__DIR__#$DIR#g" -e "s#__HOME__#$HOME_DIR#g" -e "s#__GAPDIR__#$GAPDIR#g" "$SRC/$f" > "$d/50-bkk-hardening.conf"
done
systemctl daemon-reload
systemctl restart bkk-hub-web.service
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${BKK_STOCK_PORT:-8020}/login"; then
    echo "보호 설정 적용 완료 (갭 봇 폴더: $GAPDIR 접근 차단)"
    exit 0
  fi
  sleep 2
done
echo "보드가 안 떠서 보호 설정을 되돌립니다. journalctl -u bkk-hub-web -n 50 을 확인하세요." >&2
for f in "${!MAP[@]}"; do rm -f "/etc/systemd/system/${MAP[$f]}.d/50-bkk-hardening.conf"; done
systemctl daemon-reload
systemctl restart bkk-hub-web.service
exit 1
