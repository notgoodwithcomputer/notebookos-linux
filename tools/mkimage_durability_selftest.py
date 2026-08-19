#!/usr/bin/env python3
"""The UEFI image builder publishes only a completely verified image."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "mkimage-uefi.sh")
text = open(PATH, encoding="utf-8").read()

assert 'OUT_TMP=$(mktemp "$WORK/.notebookos-uefi.img.XXXXXX")' in text
assert 'trap cleanup EXIT' in text
assert "trap 'exit 143' TERM" in text
assert 'rm -f "$OUT"' not in text

publish = text.index('mv -f -- "$OUT_TMP" "$OUT"')
verify = text.index('$SGDISK -v "$OUT_TMP"')
root_copy = text.index('if="$ROOTFS" of="$OUT_TMP"')
assert root_copy < verify < publish

construction = text[text.index('echo "== 3/5'):publish]
assert 'of="$OUT"' not in construction
assert '"$OUT" >/dev/null' not in construction

assert text.index('chmod 0644 "$OUT_TMP"') < publish
assert text.index('OUT_TMP=', publish) > publish

print("MKIMAGE DURABILITY SELFTEST: 9 checks, all pass")
print("RESULT: ALL PASS")
