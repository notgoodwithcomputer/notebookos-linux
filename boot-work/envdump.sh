#!/bin/sh
# find a DE process (shell.py or finder.py) and dump its environment
PID=$(ps 2>/dev/null | grep -E 'shell.py|finder.py' | grep -v grep | awk '{print $1}' | head -1)
echo "DE PID=$PID"
if [ -n "$PID" ]; then
  tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep -iE 'BASH_ENV|^ENV=|PROMPT|BASHRC|MOTD|GNUTLS|TLS|NB_|nosd|DC_' | head -40
  echo "--- full env keys ---"
  tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | sed 's/=.*//' | sort | tr '\n' ' '
fi
