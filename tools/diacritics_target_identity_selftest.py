#!/usr/bin/env python3
"""A held accent key may replace text only in its originating widget."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import nbdiacritics  # noqa: E402


def main() -> None:
    picker = nbdiacritics.DiacriticsPicker.__new__(nbdiacritics.DiacriticsPicker)
    first, second = object(), object()
    picker._held = (1, "e")
    picker._held_target = first
    picker._hold_src = picker._rel_src = 0
    picker._focus_text = lambda: second
    picker._open = picker._dead = False
    picker._cancel_hold = lambda: None
    picker._cancel_release = lambda: None
    picker._show()
    assert picker._held is None and picker._held_target is None
    print("PASS diacritic holds are cancelled when focus leaves their origin")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
