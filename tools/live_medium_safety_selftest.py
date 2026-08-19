#!/usr/bin/env python3
"""Live-media discovery must not replay journals on disks it scans."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "live", "init.sh")
text = open(PATH, encoding="utf-8").read()
body = text[text.index("try_medium() {"):text.index("\n}\n", text.index("try_medium() {"))]

assert "mount -o ro " not in body
assert "mount -t iso9660 -o ro" in body
assert "mount -t vfat -o ro" in body
assert "mount -t ext2 -o ro" in body
assert "mount -t ext3 -o ro,noload" in body
assert "mount -t ext4 -o ro,noload" in body
assert body.index("ext4 -o ro,noload") < body.index("[ -f")
assert body.index("ext3 -o ro,noload") < body.index("[ -f")
assert len(re.findall(r"mount -t ext[34] -o ro,noload", body)) == 2

print("LIVE MEDIUM SAFETY SELFTEST: 9 checks, all pass")
