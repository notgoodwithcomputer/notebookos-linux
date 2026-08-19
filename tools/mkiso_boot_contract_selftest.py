#!/usr/bin/env python3
"""Display-free contract for the final ISO boot-record gate."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "tools" / "mkiso.sh").read_text(encoding="utf-8")

checks = []


def check(ok, message):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + message)


gate = 'python3 "$ROOT/tools/iso_boot_check.py" "$ISO"'
done = 'SZ="$(du -h "$ISO" | cut -f1)"'
map_if = 'if [ -n "${MAP_PACKS:-}" ]; then'
map_end = text.rfind("\nfi\n", text.find(map_if))
gate_at = text.rfind(gate)

check(map_if in text, "the optional map-remaster branch is present")
check(gate_at > map_end >= 0,
      "the final boot check is outside the optional map branch")
check(gate_at < text.find(done),
      "the final boot check runs before the artifact is reported complete")
check('|| die "final ISO is not bootable' in text[gate_at:gate_at + 200],
      "a failed final boot check aborts publication")

print()
if not all(checks):
    print("RESULT: FAIL")
    raise SystemExit(1)
print("RESULT: ALL PASS (%d checks)" % len(checks))
