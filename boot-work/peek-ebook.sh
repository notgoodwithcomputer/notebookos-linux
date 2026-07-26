#!/bin/sh
# find finder pid, kill it so the running ebook becomes frontmost
for p in $(ps 2>/dev/null | grep -E 'finder.py|widgets.py' | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
sleep 1
# nudge paint on ebook via a pointer wiggle
DISPLAY=:0 xdotool mousemove 960 500 mousemove 961 501 2>/dev/null
echo peeked
