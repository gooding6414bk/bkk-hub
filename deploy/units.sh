#!/usr/bin/env bash
# systemd 파일을 그려서 설치하고 타이머를 켠다. root로 실행 (install.sh와 update.sh가 sudo로 부른다).
set -euo pipefail
: "${BKK_DIR:?}" "${BKK_USER:?}"
PORT="${BKK_PORT:-8020}"
SRC="$BKK_DIR/deploy/systemd"
for f in "$SRC"/*; do
  name="$(basename "$f")"
  sed -e "s#__DIR__#$BKK_DIR#g" -e "s#__USER__#$BKK_USER#g" -e "s#__PORT__#$PORT#g" "$f" > "/etc/systemd/system/$name"
done
systemctl daemon-reload
systemctl enable --now bkk-hub-web.service >/dev/null
for t in daily dart-poll morning report watchdog; do
  systemctl enable --now "bkk-stock-$t.timer" >/dev/null
done
systemctl enable --now bkk-hub-update.timer >/dev/null
echo "systemd 설치 완료: 통합 보드 1개, 주식 타이머 5개, 자동 업데이트 1개"
