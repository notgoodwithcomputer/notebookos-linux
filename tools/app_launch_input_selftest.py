#!/usr/bin/env python3
"""The guest app launcher rejects shell/path syntax before guest contact."""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "app-launch.sh")

cases = [
    [],
    ["../../tmp/escaped"],
    ["writer;touch /tmp/pwned"],
    ["$(touch /tmp/pwned)"],
    ["writer", "1;touch /tmp/pwned"],
]
for args in cases:
    result = subprocess.run([SCRIPT] + args, capture_output=True, text=True)
    assert result.returncode == 2, (args, result.returncode, result.stderr)

text = open(SCRIPT, encoding="utf-8").read()
validation = text.index('case "$MOD" in')
guest = text.index('$G "setsid env')
output = text.index('$Q shot')
assert validation < guest < output
assert "invalid module identifier" in text
assert "wait must be a whole number" in text

print("APP LAUNCH INPUT SELFTEST: 9 checks, all pass")
