#!/bin/sh
echo "=== /etc/profile ==="; cat /etc/profile 2>/dev/null
echo "=== /etc/bash.bashrc ==="; cat /etc/bash.bashrc 2>/dev/null
echo "=== /root/.bashrc ==="; cat /root/.bashrc 2>/dev/null
echo "=== /root/.profile ==="; cat /root/.profile 2>/dev/null
echo "=== /etc/profile.d ==="; ls -la /etc/profile.d/ 2>/dev/null
echo "=== grep profile.d ==="; grep -rl "unencrypted" /etc/profile.d/ /etc/profile /etc/bash.bashrc /root/.bashrc 2>/dev/null
echo "=== which prints it? grep /etc ==="; grep -rn "written to disk unencrypted" /etc 2>/dev/null | head
