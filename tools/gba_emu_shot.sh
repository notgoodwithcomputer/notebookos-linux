#!/bin/bash
# Capture a REAL-emulator screenshot of a .gba, headlessly, on the build host.
#
# The tree's vbam is a host-runnable x86_64 glibc binary, but its screen capture
# is bound to an input button (unreachable with no window). So: run it headless
# (SDL dummy driver — the GBA core still fills its `pix` framebuffer), then attach
# gdb and call systemScreenCapture(0) directly, which writes the current frame to
# <romdir>/<rom>00.png. This is the ground-truth check that the mode-0 runtime's
# hardware register/OAM/VRAM writes render correctly (vs the host PPU simulator in
# tools/gba_render_check.py).
#
#   tools/gba_emu_shot.sh <rom.gba> [out.png] [seconds-to-run]
set -u
ROM="${1:?usage: gba_emu_shot.sh <rom.gba> [out.png] [seconds]}"
OUT="${2:-${ROM%.gba}_emu.png}"
SECS="${3:-3}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VB="$ROOT/buildroot/output/build/vbam-2.1.4/vbam"
[ -x "$VB" ] || { echo "no vbam at $VB (build the vbam package)"; exit 2; }
command -v gdb >/dev/null || { echo "gdb required"; exit 2; }

romdir=$(cd "$(dirname "$ROM")" && pwd)
rombase=$(basename "$ROM" .gba)
shot="$romdir/${rombase}00.png"
rm -f "$shot"

SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy "$VB" --no-opengl "$ROM" >/dev/null 2>&1 &
pid=$!
sleep "$SECS"
gdb -p "$pid" -batch -ex 'call (void)systemScreenCapture(0)' -ex detach -ex quit \
    >/dev/null 2>&1
kill -9 "$pid" 2>/dev/null
sleep 1
if [ -f "$shot" ]; then
    mv -f "$shot" "$OUT"
    echo "wrote $OUT ($(file -b "$OUT" 2>/dev/null))"
else
    echo "no screenshot produced"
    exit 1
fi
