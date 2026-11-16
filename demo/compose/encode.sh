#!/usr/bin/env bash
# 렌더된 프레임 + track.wav → 마스터(1080x1920 H.264 CRF16) · 웹용(720x1280, 2-pass, 6 MB 이하) · 포스터 · 접촉 인화지
#   bash demo/compose/encode.sh
set -euo pipefail
DEMO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$DEMO/out"
WAV="$DEMO/music/track.wav"
FPS=30
[ -f "$WAV" ] || { echo "missing $WAV — run demo/music/analyze.py first" >&2; exit 1; }
[ -f "$OUT/frames/00001.jpg" ] || { echo "missing frames — run node demo/compose/render.mjs first" >&2; exit 1; }
LEN=$(python3 -c "import json;print(json.load(open('$OUT/frames.json'))['length'])")

# 1) 마스터
ffmpeg -v error -y -framerate $FPS -i "$OUT/frames/%05d.jpg" -i "$WAV" \
  -map 0:v -map 1:a -t "$LEN" \
  -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -profile:v high -tune film \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -c:a aac -b:a 192k -ar 48000 -movflags +faststart \
  "$OUT/haedok-demo.mp4"

# 2) 웹용 720x1280 — 2-pass로 6 MB(6,000,000 B) 안에 맞춘다 (영상 1000k + 소리 96k)
VB=${VB:-1000k}
PASS="$OUT/x264-2pass"
ffmpeg -v error -y -i "$OUT/haedok-demo.mp4" -vf "scale=720:1280:flags=lanczos" -c:v libx264 -preset slow -b:v $VB \
  -maxrate 2000k -bufsize 4000k -pix_fmt yuv420p -pass 1 -passlogfile "$PASS" -an -f mp4 /dev/null
ffmpeg -v error -y -i "$OUT/haedok-demo.mp4" -vf "scale=720:1280:flags=lanczos" -c:v libx264 -preset slow -b:v $VB \
  -maxrate 2000k -bufsize 4000k -pix_fmt yuv420p -pass 2 -passlogfile "$PASS" \
  -c:a aac -b:a 96k -ar 48000 -movflags +faststart "$OUT/haedok-demo-web720.mp4"
rm -f "$PASS"*
SIZE=$(stat -f%z "$OUT/haedok-demo-web720.mp4" 2>/dev/null || stat -c%s "$OUT/haedok-demo-web720.mp4")
[ "$SIZE" -le 6000000 ] || { echo "web copy is $SIZE B (> 6 MB) — rerun with a lower VB" >&2; exit 1; }

# 3) 포스터 — 랜딩 장면 가운데 프레임
POSTER=$(python3 -c "
import json; m = json.load(open('$OUT/frames.json'))
fs = [f['f'] for f in m['frames'] if f['shot'] == 'landing']
print(fs[len(fs) // 2])")
ffmpeg -v error -y -i "$OUT/frames/$(printf %05d "$POSTER").jpg" -vf "scale=720:1280:flags=lanczos" -q:v 3 "$OUT/haedok-demo-poster.jpg"

# 4) 접촉 인화지
uv run -q --with pillow python "$DEMO/compose/contact.py"

for f in haedok-demo.mp4 haedok-demo-web720.mp4; do
  ffprobe -v error -show_entries format=duration,size:stream=codec_name,width,height,r_frame_rate -of compact "$OUT/$f"
done
ls -la "$OUT/haedok-demo-poster.jpg"
