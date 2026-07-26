#!/bin/bash
# Full interaction proof against a freshly booted guest (tools/qmp.py boot):
#   Finder auto-maps → double-click Calculator.app → app launches fullscreen,
#   focused → mouse-press 7 × 6 = on the keypad → 42 → Esc → Finder returns
#   → open the panel app-switcher (grab-free papertone menu).
# Screenshots land in boot-work/proof/. Clicks are spaced ~2.5s: under TCG
# the guest X server lags and tighter spacing makes clicks queue up.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
Q="python3 $ROOT/tools/qmp.py"
G="python3 $ROOT/tools/gsh.py"
OUT="$ROOT/boot-work/proof"; mkdir -p "$OUT"

echo "[1/7] boot desktop (Finder should be auto-mapped)"
$Q shot "$OUT/30-desktop.png"

echo "[2/7] double-click Calculator.app"
$Q dblclick 675 400
$G 'for i in $(seq 1 25); do xwininfo -root -tree 2>/dev/null | grep -q "calculator.py\".*1920x1080" && { echo CALC-MAPPED; exit 0; }; sleep 2; done; echo TIMEOUT-NO-CALC' || true
sleep 3
$Q shot "$OUT/31-calc.png"

echo "[3/7] mouse-press 7 x 6 ="
$Q click 907 609;  sleep 2.5
$Q click 1226 676; sleep 2.5
$Q click 1120 676; sleep 2.5
$Q click 1120 810; sleep 3
$Q shot "$OUT/32-answer.png"

echo "[4/7] Esc quits the app"
$Q key esc
$G 'for i in $(seq 1 10); do ps w 2>/dev/null | grep -v grep | grep -q calculator || { echo CALC-EXITED; exit 0; }; sleep 2; done; echo CALC-STILL-RUNNING' || true

echo "[5/7] Finder returns"
sleep 6
$Q shot "$OUT/33-finder-back.png"

echo "[6/7] panel app-switcher (grab-free menu)"
$Q click 99 22
sleep 2.5
$Q shot "$OUT/34-menu.png"

echo "[7/7] toggle the menu shut"
$Q click 99 22
sleep 1.5
$Q shot "$OUT/35-closed.png"

echo "done — screenshots in $OUT"
