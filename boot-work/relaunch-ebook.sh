#!/bin/sh
for p in $(ps -o pid,args 2>/dev/null | grep ebook.py | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
for p in $(ps 2>/dev/null | grep ebook.py | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
rm -f /tmp/nb-app-active
sleep 1
cd /opt/notebook/de
DISPLAY=:0 NB_HOME=/root PYTHONPATH=/tmp/de_override:/opt/notebook/de nohup python3 /tmp/de_override/ebook.py >/tmp/ebook.log 2>&1 &
echo launched
