#!/usr/bin/env python3
"""Display-free generation-purity checks for clipboard snapshots."""
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import xclipd  # noqa: E402


class Core:
    def event(self, *_args):
        return None


def daemon(pending, old_text="old text", old_image="old image"):
    d = xclipd.ClipboardDaemon.__new__(xclipd.ClipboardDaemon)
    d.text, d.image = old_text, old_image
    d.core = Core()
    d._serve_generation = 4
    d._snapshot_generation = 4
    d._snapshot_deadline_id = 1
    d._snapshot_pending = {4: pending}
    d._serve = lambda: None
    return d


d = daemon({"text": ..., "image": "new image"})
d._snapshot_deadline(4)
assert d.text is None and d.image == "new image"
print("PASS a new image cannot inherit the previous copy's text")

d = daemon({"text": "new text", "image": ...})
d._snapshot_deadline(4)
assert d.text == "new text" and d.image is None
print("PASS new text cannot inherit the previous copy's image")

d = daemon({"text": ..., "image": ...})
d._snapshot_deadline(4)
assert d.text == "old text" and d.image == "old image"
print("PASS no callbacks retain the last complete snapshot")

print("RESULT: ALL PASS")
