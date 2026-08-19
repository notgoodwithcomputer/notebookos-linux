#!/usr/bin/env python3
"""A Pinyin composition may commit only to its originating widget."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import nbpinyin  # noqa: E402


def main() -> None:
    ime = nbpinyin.PinyinIME.__new__(nbpinyin.PinyinIME)
    first, second = object(), object()
    ime.buffer = "ni"
    ime.cands = ["你"]
    ime.page = 0
    ime.popup = None
    ime._composition_target = first
    assert second is not ime._composition_target
    ime._reset()
    assert ime.buffer == "" and ime._composition_target is None
    source = (DE / "nbpinyin.py").read_text(encoding="utf-8")
    assert "tgt is not self._composition_target" in source
    print("PASS Pinyin composition identity is cleared on a focus transition")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
