#!/bin/sh
DE=/opt/notebook/de
cd /tmp/tests
PASS=0; FAIL=0; FAILED=""
for t in *_selftest.py; do
  OUT=$(DISPLAY=:0 NB_HOME=/root PYTHONPATH=$DE python3 "$t" 2>&1)
  R=$(echo "$OUT" | grep -E "^RESULT:" | tail -1)
  if echo "$R" | grep -q "ALL PASS"; then PASS=$((PASS+1)); printf "PASS  %-30s %s\n" "$t" "$R"
  else FAIL=$((FAIL+1)); FAILED="$FAILED $t"; printf "FAIL  %-30s %s\n" "$t" "$R"; echo "$OUT" | grep "^FAIL " | head -4 | sed 's/^/     /'; fi
done
echo "SUITES: $PASS passed, $FAIL failed"; [ -n "$FAILED" ] && echo "FAILED:$FAILED"
