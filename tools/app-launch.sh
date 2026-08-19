#!/bin/bash
# Launch one DE app on the running guest X (via the ttyS1 debug shell),
# wait, screenshot it to boot-work/apps/<mod>.png, and report survival:
#   <mod>: OK | CRASH | NO-WINDOW
# (Kill the Finder once before sweeping so apps show fullscreen and clean.)
#
#   app-launch.sh <module> [wait_secs]
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[ "$#" -ge 1 ] || { echo "usage: app-launch.sh MODULE [wait-seconds]" >&2; exit 2; }
MOD="$1"; WAIT="${2:-14}"
# MOD is sent inside a root shell command in the guest and used as a host-side
# filename. A module is an identifier, never shell syntax or a path.
case "$MOD" in
  ""|*[!A-Za-z0-9_]*)
    echo "app-launch: invalid module identifier: $MOD" >&2
    exit 2
    ;;
esac
case "$WAIT" in
  ""|*[!0-9]*)
    echo "app-launch: wait must be a whole number of seconds" >&2
    exit 2
    ;;
esac
OUT="$ROOT/boot-work/apps"; mkdir -p "$OUT"
G="python3 $ROOT/tools/gsh.py"
Q="python3 $ROOT/tools/qmp.py"
APPS='writer|novel|academic|journal|screenplay|tasks|calendar|cookbook|ebook|calculator|accounting|contacts|messages|illustrator|sequencer|video|media|music|packages|g2048|tetris'

num() { tr -d '\r' | grep -xE '[0-9]+' | tail -1; }

# kill any previously-launched app (keep shell.py + finder.py), reset log
$G "for p in \$(ps w | grep -v grep | grep -E 'de/($APPS)\.py' | awk '{print \$1}'); do kill \$p 2>/dev/null; done; : > /tmp/app.log; sleep 1" >/dev/null 2>&1 || true

# NOTE: a command must follow the '&' or gsh's group-close eats the launch.
$G "setsid env GTK_THEME=Papertone NB_HOME=/root PYTHONPATH=/opt/notebook/de python3 /opt/notebook/de/${MOD}.py >/tmp/app.log 2>&1 </dev/null & sleep 0.3; echo started" >/dev/null 2>&1 || true

sleep "$WAIT"
$Q shot "$OUT/${MOD}.png" >/dev/null 2>&1 || true

TB=$($G "grep -c Traceback /tmp/app.log" | num); TB="${TB:-0}"
ALIVE=$($G "ps w | grep -v grep | grep -c de/${MOD}.py" | num); ALIVE="${ALIVE:-0}"

if [ "$TB" != "0" ]; then
  echo "${MOD}: CRASH"
  $G "grep -A3 Traceback /tmp/app.log | tail -8" | grep -vE '__GSH|DISPLAY=:0|^~ #|grep -A3' | sed 's/^/    /'
elif [ "$ALIVE" = "0" ]; then
  echo "${MOD}: NO-WINDOW"
  $G "tail -4 /tmp/app.log" | grep -vE '__GSH|DISPLAY=:0|^~ #|tail -4' | sed 's/^/    /'
else
  echo "${MOD}: OK"
fi
