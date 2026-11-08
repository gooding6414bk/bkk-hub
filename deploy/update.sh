#!/usr/bin/env bash
# 자동 업데이트 (타이머가 10분마다 부른다). 새 커밋이 있을 때만 받아서 이 보드만 재시작한다.
# 같은 서버(Ubuntu-1)의 갭 차익거래 봇은 건드리지 않는다.
# 재시작 뒤 대시보드가 60초 안에 안 뜨면 이전 커밋으로 되돌린다.
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
BRANCH="${BKK_STOCK_BRANCH:-main}"
PORT="${BKK_STOCK_PORT:-8020}"
PY="$DIR/.venv/bin/python"
export ARBI_INSTANCE="$DIR/instance" GIBALHAE_CONTENT="$DIR/instance/gibalhae/content"

# 아침 예약 작업이 저장소에 올린 기발해 새 초안을 서버 데이터 폴더로 가져온다 (새 커밋이 없어도 매번)
sync_drafts() { "$PY" -m bkkstock gibalhae-sync >/dev/null 2>&1 || true; }

git fetch -q origin "$BRANCH" 2>/dev/null || exit 0
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "origin/$BRANCH")"
[ "$OLD" = "$NEW" ] && { sync_drafts; exit 0; }

# 수집 중이거나 기발해 영상 제작·올리기 중에는 건드리지 않는다 (보드를 재시작하면 제작이 끊긴다)
for j in daily backfill; do
  if systemctl is-active --quiet "bkk-stock@$j.service"; then echo "$j 실행 중이라 다음에 업데이트"; sync_drafts; exit 0; fi
done
if pgrep -f "gibalhae/pipeline/daily.py" >/dev/null 2>&1; then echo "기발해 작업 중이라 다음에 업데이트"; sync_drafts; exit 0; fi

if ! git merge --ff-only -q "origin/$BRANCH"; then
  "$PY" -m bkkstock notify "⚠️ bkk-hub 업데이트 실패: 서버에 고친 파일이 있어 받을 수 없습니다 (git status 확인)" || true
  exit 1
fi

changed() { ! git diff --quiet "$OLD" "$NEW" -- "$@"; }
changed requirements.txt && .venv/bin/pip install -q -r requirements.txt
changed deploy/systemd && sudo BKK_DIR="$DIR" BKK_USER="$(whoami)" BKK_PORT="$PORT" bash deploy/units.sh

sync_drafts
sudo systemctl restart bkk-hub-web.service
ok=0
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login"; then ok=1; break; fi
  sleep 2
done

if [ "$ok" = 1 ]; then
  echo "업데이트 완료 ${OLD:0:7} → ${NEW:0:7}"
  exit 0
fi

echo "대시보드가 안 떠서 되돌립니다"
git reset -q --hard "$OLD"
changed requirements.txt && .venv/bin/pip install -q -r requirements.txt
sudo systemctl restart bkk-hub-web.service
"$PY" -m bkkstock notify "🚨 bkk-hub 업데이트 ${NEW:0:7} 후 대시보드가 안 떠서 ${OLD:0:7}로 되돌렸습니다" || true
exit 1
