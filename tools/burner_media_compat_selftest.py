#!/usr/bin/env python3
"""Display-free optical-media compatibility contract."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import burner  # noqa: E402


def disc(media, blank=True):
    return {"present": True, "media": media, "blank": blank,
            "bytes": 700 * 1024 * 1024}


assert burner.compatible_media("audio", disc("CD-R"))
assert not burner.compatible_media("audio", disc("DVD-R"))
assert burner.compatible_media("video", disc("DVD-R"))
assert not burner.compatible_media("video", disc("CD-R"))
assert not burner.compatible_media("video", disc(None))
assert not burner.compatible_media("audio", disc("CD-RW", blank=False))
print("PASS each authoring mode accepts only a known blank compatible medium")

print("RESULT: ALL PASS")
