#!/usr/bin/env bash
# README 에셋(히어로 GIF · 소셜 배너)을 다시 만든다. 스크린샷은 docs/README-assets.md 참고.
#
#   scripts/readme_assets.sh            # 전부
#   scripts/readme_assets.sh gif        # GIF만
#   scripts/readme_assets.sh banner     # 배너만
#
# 환경변수: START(초, 기본 14) DURATION(초, 기본 14) FPS(기본 12) WIDTH(기본 360) COLORS(팔레트 색 수, 기본 128)
set -euo pipefail
cd "$(dirname "$0")/.."

VIDEO=app/static/demo/haedok-demo.mp4
GIF=docs/images/hero.gif
BANNER=docs/images/banner.png
PY=${PY:-.venv/bin/python}
START=${START:-14} DURATION=${DURATION:-14} FPS=${FPS:-12} WIDTH=${WIDTH:-360} COLORS=${COLORS:-128}

gif() {
  command -v ffmpeg >/dev/null || { echo "ffmpeg가 필요해요 (brew install ffmpeg)"; exit 1; }
  local palette; palette=$(mktemp -t haedok-palette).png
  # hqdn3d: 영상의 미세한 노이즈를 걷어내야 GIF가 작아진다
  local filters="fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,hqdn3d=3:2:6:4"
  # 1차: 이 구간에 맞는 팔레트  2차: 그 팔레트로 디더링(bayer가 가장 작게 나온다)
  ffmpeg -v error -y -ss "$START" -t "$DURATION" -i "$VIDEO" \
    -vf "${filters},palettegen=max_colors=${COLORS}:stats_mode=diff" "$palette"
  ffmpeg -v error -y -ss "$START" -t "$DURATION" -i "$VIDEO" -i "$palette" \
    -lavfi "${filters} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
    -loop 0 "$GIF"
  rm -f "$palette"
  local size; size=$(stat -f %z "$GIF" 2>/dev/null || stat -c %s "$GIF")
  echo "$GIF $((size / 1024)) KB (${START}s~$((START + DURATION))s, ${FPS}fps, ${WIDTH}px, ${COLORS}색)"
  [ "$size" -le $((5 * 1024 * 1024)) ] || { echo "5 MB를 넘었어요 — DURATION이나 FPS를 줄여 주세요"; exit 1; }
}

banner() {
  "$PY" -c "import PIL" 2>/dev/null || { echo "Pillow가 필요해요: uv pip install pillow --python $PY"; exit 1; }
  "$PY" scripts/readme_banner.py "$BANNER"
}

case "${1:-all}" in
  gif) gif ;;
  banner) banner ;;
  all) gif; banner ;;
  *) echo "usage: $0 [gif|banner|all]"; exit 2 ;;
esac
