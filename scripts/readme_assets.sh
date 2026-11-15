#!/usr/bin/env bash
# README 에셋(소셜 배너)을 다시 만든다. 스크린샷은 docs/README-assets.md 참고.
#
#   scripts/readme_assets.sh            # 배너
set -euo pipefail
cd "$(dirname "$0")/.."

BANNER=docs/images/banner.png
PY=${PY:-.venv/bin/python}

banner() {
  "$PY" -c "import PIL" 2>/dev/null || { echo "Pillow가 필요해요: uv pip install pillow --python $PY"; exit 1; }
  "$PY" scripts/readme_banner.py "$BANNER"
}

banner
