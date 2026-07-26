#!/bin/sh
for p in $(ps 2>/dev/null | grep -E 'ebook.py' | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
sleep 1
touch /tmp/nb-app-active
cd /opt/notebook/de
DISPLAY=:0 NB_HOME=/root nohup python3 /opt/notebook/de/accounting.py >/tmp/acct.log 2>&1 &
echo launched
