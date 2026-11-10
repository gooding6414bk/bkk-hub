#!/usr/bin/env bash
# 자동 업데이트 (타이머가 10분마다 부른다). 새 커밋이 있을 때만, 먼저 임시 폴더에서 시험하고 통과하면 받아서
# 이 보드만 재시작한다. 같은 서버(Ubuntu-1)의 갭 차익거래 봇은 건드리지 않는다.
#
#  1. 새 커밋이 없으면 끝 (기발해 초안 동기화는 GIBALHAE_SYNC=1 일 때만 — 영상 일은 보류 중)
#  2. 전에 실패한 커밋(data/update.bad)이면 건너뜀 → 같은 나쁜 커밋을 10분마다 받았다 되돌리는 반복을 막음
#  3. 수집·백필이 돌고 있으면(작업 잠금 파일로 판단 — 화면에서 누른 작업도 잡힘) 또는 서버가 바쁘면 다음으로
#  4. requirements·systemd·units.sh 가 바뀐 커밋은 사람이 해야 함 → 받지 않고 한 번만 알림 (자동 배포에 sudo·pip 없음)
#  5. 임시 worktree 에서 전체 시험(pytest, 낮은 우선순위, 5분 제한) → 통과해야만 운영 폴더에 반영
#  6. 보드 재시작 뒤 60초 안에 /login 이 안 열리면 되돌리고 그 커밋을 실패로 기억
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
BRANCH="${BKK_STOCK_BRANCH:-main}"
PORT="${BKK_STOCK_PORT:-8020}"
PY="$DIR/.venv/bin/python"
DATA="${BKK_STOCK_DATA:-$DIR/data}"
BAD="$DATA/update.bad"
NOTIFIED="$DATA/update.notified"
export ARBI_INSTANCE="$DIR/instance" GIBALHAE_CONTENT="$DIR/instance/gibalhae/content"
mkdir -p "$DATA/locks"
touch "$BAD" "$NOTIFIED"

notify_once() {   # 같은 커밋·같은 이유로는 한 번만 알린다
  local key="$1" msg="$2"
  grep -qxF "$key" "$NOTIFIED" 2>/dev/null && return 0
  echo "$key" >> "$NOTIFIED"
  "$PY" -m bkkstock notify "$msg" >/dev/null 2>&1 || true
}

DRAFT_BRANCH="${GIBALHAE_DRAFT_BRANCH:-gibalhae-daily}"
sync_drafts() {
  [ "${GIBALHAE_SYNC:-0}" = 1 ] || return 0
  if git fetch -q origin "$DRAFT_BRANCH" 2>/dev/null; then
    tmp="$(mktemp -d)"
    if git archive FETCH_HEAD gibalhae/content 2>/dev/null | tar -x -C "$tmp" 2>/dev/null; then
      "$PY" -m bkkstock gibalhae-sync --src "$tmp/gibalhae/content" >/dev/null 2>&1 || true
    fi
    rm -rf "$tmp"
  fi
}

if ! git fetch -q origin "$BRANCH" 2>/dev/null; then
  echo "git fetch 실패 (네트워크 또는 저장소 권한)"
  exit 0
fi
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "origin/$BRANCH")"
[ "$OLD" = "$NEW" ] && { sync_drafts; exit 0; }
if grep -qxF "$NEW" "$BAD"; then echo "${NEW:0:7}은 전에 실패한 커밋이라 건너뜀"; exit 0; fi

# 바쁘면 다음에: 작업 잠금(daily·backfill·morning), 기발해 제작, 부하
for j in daily backfill; do
  if ! flock -n "$DATA/locks/$j.lock" true 2>/dev/null; then echo "$j 실행 중이라 다음에 업데이트"; exit 0; fi
done
if systemctl is-active --quiet "bkk-stock@morning.service" 2>/dev/null; then echo "아침 작업 중이라 다음에"; exit 0; fi
if pgrep -f "gibalhae/pipeline/daily.py" >/dev/null 2>&1; then echo "기발해 작업 중이라 다음에 업데이트"; exit 0; fi
load="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
if awk -v l="$load" 'BEGIN{exit !(l > 2.0)}'; then echo "서버가 바빠서(부하 $load) 다음에"; exit 0; fi

# 사람이 해야 하는 변경은 자동으로 받지 않는다 (sudo·pip 를 자동으로 돌리지 않음)
need=""
git diff --quiet "$OLD" "$NEW" -- 'requirements*.txt' || need="$need 의존성(requirements)"
git diff --quiet "$OLD" "$NEW" -- deploy/systemd deploy/units.sh || need="$need systemd"
if [ -n "$need" ]; then
  echo "사람이 반영해야 하는 변경:$need"
  notify_once "manual:$NEW" "🛠 bkk-hub ${NEW:0:7}에 직접 반영할 변경이 있습니다:$need. 서버에서: cd ~/bkk-hub && git merge --ff-only origin/$BRANCH && bash deploy/manual-update.sh"
  exit 0
fi

# 임시 worktree 에서 시험 — 운영 폴더·운영 데이터는 건드리지 않는다 (env -i 로 운영 경로 변수를 넘기지 않음)
WT="$(mktemp -d)"
cleanup() { git worktree remove --force "$WT" >/dev/null 2>&1; rm -rf "$WT"; git worktree prune >/dev/null 2>&1; }
trap cleanup EXIT
git worktree add -q --detach "$WT/src" "$NEW" >/dev/null 2>&1 || { echo "worktree 만들기 실패"; exit 0; }
ok_test=1
TEST=(-m pytest -q -x -p no:cacheprovider tests --deselect "tests/test_gibalhae.py::test_gibalhae_script[gibalhae_pipeline_check.py]")
if ! "$PY" -c "import pytest" >/dev/null 2>&1; then
  # pytest 가 없으면 최소한 모든 작업 모듈과 웹이 import 되는지만 본다
  TEST=(-c "import bkkstock.jobs.daily, bkkstock.jobs.morning, bkkstock.jobs.report, bkkstock.jobs.watchdog, bkkstock.jobs.backfill, bkkstock.web.app")
fi
if ! (cd "$WT/src" && env -i PATH=/usr/bin:/bin HOME="$WT" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
      nice -n 19 ionice -c3 timeout 300 "$PY" "${TEST[@]}" >"$WT/test.log" 2>&1); then
  ok_test=0
fi
if [ "$ok_test" != 1 ]; then
  echo "${NEW:0:7} 시험 실패 — 받지 않음"; tail -n 20 "$WT/test.log"
  echo "$NEW" >> "$BAD"
  notify_once "test:$NEW" "⚠️ bkk-hub ${NEW:0:7} 시험(pytest) 실패로 배포하지 않았습니다. 지금 버전(${OLD:0:7})으로 계속 돕니다."
  exit 1
fi

if ! git merge --ff-only -q "origin/$BRANCH"; then
  notify_once "ff:$NEW" "⚠️ bkk-hub 업데이트 실패: 서버에 고친 파일이 있어 받을 수 없습니다 (git status 확인)"
  exit 1
fi
sync_drafts
"$PY" -m bkkstock gibalhae-sync >/dev/null 2>&1 || true
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
sudo systemctl restart bkk-hub-web.service
echo "$NEW" >> "$BAD"
notify_once "rollback:$NEW" "🚨 bkk-hub 업데이트 ${NEW:0:7} 후 대시보드가 안 떠서 ${OLD:0:7}로 되돌렸습니다 (이 커밋은 다시 받지 않음)"
exit 1
