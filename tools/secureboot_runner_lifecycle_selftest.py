#!/usr/bin/env python3
"""The Secure Boot harness owns and reaps its background QEMU process."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "run-uefi-secureboot.sh")
text = open(PATH, encoding="utf-8").read()

cleanup = text.index("cleanup() {")
launch = text.index("setsid qemu-system-x86_64")
capture = text.index("QPID=$!")
delay = text.index('sleep "$SHOT_AT"')
finish = text.index("cleanup\n", delay)

assert cleanup < launch
assert 'trap cleanup EXIT' in text[cleanup:launch]
assert "trap 'exit 130' INT" in text[cleanup:launch]
assert launch < capture < delay < finish

body = text[cleanup:launch]
assert 'kill -0 "$QPID"' in body
assert 'kill "$QPID"' in body
assert 'wait "$QPID"' in body
assert 'kill -9' not in text
assert 'QPID=' in body

print("SECUREBOOT RUNNER LIFECYCLE SELFTEST: 10 checks, all pass")
