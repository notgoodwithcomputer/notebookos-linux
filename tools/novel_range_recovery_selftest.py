#!/usr/bin/env python3
"""Display-free recovery checks for damaged Novel formatting spans."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-novel-ranges-home-"))
import novel  # noqa: E402


bad = [float("nan"), float("inf"), float("-inf")]
for value in bad:
    body, ranges = novel.Novel._migrate_legacy_body(
        "Chapter", "Chapter\ntext",
        {"bold": [[value, 2], [8, 12]]})
    assert body == "text", (value, body)
    assert ranges == {"bold": [[0, 4]]}, (value, ranges)
print("PASS legacy migration drops non-finite spans and keeps valid spans")


class Buffer:
    def __init__(self):
        self.applied = []

    def get_char_count(self):
        return 4

    def get_iter_at_offset(self, offset):
        return offset

    def apply_tag_by_name(self, name, start, end):
        self.applied.append((name, start, end))


buf = Buffer()
novel.Novel._apply_ranges(novel.Novel.__new__(novel.Novel), buf, {
    "bold": [[float("inf"), 2], [0, 4]],
    "italic": [[float("nan"), 3]],
})
assert buf.applied == [("bold", 0, 4)], buf.applied
print("PASS current formatting drops non-finite spans and keeps valid spans")
print("RESULT: ALL PASS")
