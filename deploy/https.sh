#!/usr/bin/env bash
# 통합 보드에 HTTPS 주소 붙이기: https://hub-<서버IP>.sslip.io  (도메인 없이 IP 이름으로 인증서 발급)
# 같은 서버의 갭 보드가 쓰는 Caddy 설정(/etc/caddy/Caddyfile)에 블록 하나만 덧붙인다.
#   - 원본을 백업하고, 설정 검사가 실패하면 되돌린다
#   - Caddy는 재시작하지 않고 다시 읽기(reload)만 한다 → 갭 보드 연결이 끊기지 않는다
# 사용법: bash deploy/https.sh            (주소를 바꾸려면 HUB_DOMAIN=... bash deploy/https.sh)
set -euo pipefail
PORT="${BKK_STOCK_PORT:-8020}"
CF=/etc/caddy/Caddyfile

if ! command -v caddy >/dev/null; then
  echo "Caddy가 없습니다. 갭 보드가 이 서버에 있다면 Caddy가 있어야 합니다. 서버를 확인하세요." >&2
  exit 1
fi
ip="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null || curl -fsS --max-time 5 https://api.ipify.org)"
ip="$(echo "$ip" | tr -d '[:space:]')"
[ -n "$ip" ] || { echo "공인 IP를 알 수 없습니다" >&2; exit 1; }
DOMAIN="${HUB_DOMAIN:-hub-${ip//./-}.sslip.io}"

if [ -f "$CF" ] && grep -q "^$DOMAIN " "$CF"; then
  echo "이미 있습니다: https://$DOMAIN"
  exit 0
fi

backup="$CF.bak-$(date +%Y%m%d-%H%M%S)"
[ -f "$CF" ] && sudo cp -p "$CF" "$backup"
printf '\n%s {\n\tencode gzip\n\treverse_proxy 127.0.0.1:%s\n}\n' "$DOMAIN" "$PORT" | sudo tee -a "$CF" >/dev/null

if ! sudo caddy validate --config "$CF" --adapter caddyfile >/tmp/bkk-hub-caddy.log 2>&1; then
  echo "Caddy 설정 검사 실패 — 원래대로 되돌립니다. 로그: /tmp/bkk-hub-caddy.log" >&2
  [ -f "$backup" ] && sudo cp -p "$backup" "$CF"
  exit 1
fi
sudo systemctl reload caddy

echo "HTTPS 주소: https://$DOMAIN"
echo "(인증서 발급에 10~30초 걸릴 수 있습니다. Lightsail 방화벽에서 80·443 포트가 열려 있어야 합니다. 갭 보드가 HTTPS면 이미 열려 있습니다.)"
for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null --max-time 5 "https://$DOMAIN/login"; then echo "확인: 열립니다"; exit 0; fi
  sleep 3
done
echo "아직 안 열립니다. 1~2분 뒤 브라우저로 열어 보세요. 안 되면: journalctl -u caddy -n 30 --no-pager"
