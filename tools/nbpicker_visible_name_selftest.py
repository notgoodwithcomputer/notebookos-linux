#!/usr/bin/env python3
"""Picker-created names must remain visible to the picker."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbpicker  # noqa: E402


def main():
    checks = [not nbpicker._visible_leaf("."),
              not nbpicker._visible_leaf(".."),
              not nbpicker._visible_leaf(".draft.writer"),
              not nbpicker._visible_leaf("a/b"),
              nbpicker._visible_leaf("draft.writer")]
    labels = ["dot", "dot-dot", "hidden document", "nested path",
              "ordinary document"]
    for ok, label in zip(checks, labels):
        print(("PASS" if ok else "FAIL") + ": " + label)
    all_ok = all(checks)
    print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILED"))
    return not all_ok


if __name__ == "__main__":
    raise SystemExit(main())
