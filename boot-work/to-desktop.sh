#!/bin/sh
# close any open app, clear the active flag so finder + widgets return
for p in $(ps 2>/dev/null | grep -E '\.py' | grep -v -E 'finder.py|shell.py|xflushd.py|widgets.py' | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
rm -f /tmp/nb-app-active
sleep 1
DISPLAY=:0 xdotool mousemove 960 500 mousemove 970 510 2>/dev/null
echo to-desktop
