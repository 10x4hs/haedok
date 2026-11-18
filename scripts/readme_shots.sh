#!/usr/bin/env bash
# README 스크린샷을 처음부터 다시 만든다 — docs/README-assets.md 참고.
#
#   scripts/readme_shots.sh                 # 두 세계를 찍고 docs/images/ 의 JPG를 덮어쓴다
#   PORT=8147 OUT=/tmp/shots scripts/readme_shots.sh
#   CHROME=/path/to/chrome-headless-shell scripts/readme_shots.sh   # 따로 받은 크로미움을 쓸 때
#
# 1) 이야기 세계: 빈 SQLite → 계정 셋 → 지우가 위키문헌에서 「운수 좋은 날」을 열고 대결을 연다(네트워크 필요)
#    → 하린·민준이 참가해 읽고 반응 → 랜딩·로그인·가입·인증·리더·반응 시트·서점·관리자·PC 리더
# 2) 시드 세계: 빈 SQLite → 계정 셋 → seed_board → seed_races → seed_chat → seed_goals → seed_mileage
#    → seed_posts → seed_posts_ig(readme_photos.py가 그린 사진 여섯 장) (네트워크 없이 심는다)
#    → 하린 시점의 홈·내 정보·대결·모집·친구·채팅·마일리지·순위·게시물(소식·댓글·프로필·올리기)·공개 페이지
# 3) PNG → JPEG(250 KB 이하) → docs/images/app · docs/images/admin
#
# 필요한 것: .venv(앱), uv(playwright·pillow를 임시로 받아 쓴다), playwright 크로미움(`playwright install chromium`)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=${PORT:-8077}
OUT=${OUT:-$(mktemp -d)}
PY=.venv/bin/python
BASE="http://127.0.0.1:$PORT"
export BASE CHROME="${CHROME:-}"
SERVER=""

stop() { if [ -n "$SERVER" ]; then kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; SERVER=""; fi; }
trap stop EXIT

start() {  # $1 = SQLite 파일. 빈 DB로 앱을 띄우고(테이블은 앱이 만든다) /health가 200일 때까지 기다린다
  rm -f "$1"
  export DATABASE_URL="sqlite+aiosqlite:///$1"
  DWELL_SCALE=0.01 ADMIN_EMAILS=admin@haedok.app SECRET_KEY=readme-shots LOG_JSON=false \
    .venv/bin/uvicorn app.main:app --port "$PORT" >"$OUT/server.log" 2>&1 &
  SERVER=$!
  for _ in $(seq 1 60); do
    curl -fs -o /dev/null "$BASE/health" && return 0
    sleep 0.5
  done
  echo "앱이 뜨지 않았어요 — $OUT/server.log 를 보세요" >&2
  exit 1
}

shots() { uv run -q --no-project --with playwright python scripts/readme_shots.py "$@"; }

mkdir -p "$OUT/png"
echo "== 1) 이야기 세계 ($OUT)"
start "$OUT/story.db"
"$PY" scripts/readme_shots.py --seed "$DATABASE_URL"
shots "$OUT/png"
stop

echo "== 2) 시드 세계"
uv run -q --no-project --with playwright python scripts/readme_photos.py "$OUT/photos"
export PHOTOS="$OUT/photos"
start "$OUT/world.db"
"$PY" scripts/readme_shots.py --seed "$DATABASE_URL"
for s in board races chat goals mileage posts; do
  "$PY" "scripts/seed_$s.py" "$DATABASE_URL"
done
"$PY" scripts/seed_posts_ig.py "$DATABASE_URL" "$PHOTOS"
shots --world "$OUT/png"
stop

echo "== 3) JPEG"
uv run -q --no-project --with pillow python scripts/readme_shots.py --jpeg "$OUT/png"
echo "done — PNG: $OUT/png"
