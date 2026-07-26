#!/bin/bash
# Screenshot a Notebook OS surface with headless Firefox at 1920×1080.
#   tools/shot.sh <url> <out.png> [wait_ms]
set -eu
URL="${1:?usage: shot.sh <url> <out.png> [wait_ms]}"
OUT="${2:?usage: shot.sh <url> <out.png> [wait_ms]}"
WAIT="${3:-4500}"
PROFILE=$(mktemp -d)
# --window-size sets the layout viewport; the OS canvas is a fixed 1920×1080.
firefox --headless --profile "$PROFILE" \
  --window-size=1920,1080 \
  --screenshot "$OUT" \
  "$URL" >/dev/null 2>&1 || firefox-esr --headless --profile "$PROFILE" \
  --window-size=1920,1080 --screenshot "$OUT" "$URL" >/dev/null 2>&1 || true
rm -rf "$PROFILE"
[ -f "$OUT" ] && echo "wrote $OUT ($(stat -c%s "$OUT") bytes)" || { echo "screenshot failed"; exit 1; }
