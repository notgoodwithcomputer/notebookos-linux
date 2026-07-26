#!/bin/sh
echo "=== vte.sh ==="; cat /etc/profile.d/vte.sh 2>/dev/null
echo "=== does interactive bash reprint it? ==="
/bin/bash -ic 'echo AFTER-PROMPT' 2>&1 | head -5
echo "=== which binary contains the string ==="
for d in /usr/bin /bin /usr/sbin /sbin /usr/libexec; do grep -rl "written to disk unencrypted" "$d" 2>/dev/null; done
