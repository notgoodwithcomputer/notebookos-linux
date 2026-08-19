#!/usr/bin/env python3
"""Headless regression: punctuation must not discard unknown pinyin."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi  # noqa: E402
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402
import nbpinyin  # noqa: E402


class Entry:
    def __init__(self):
        self.text = ""
        self.pos = 0
    def get_selection_bounds(self): return ()
    def get_position(self): return self.pos
    def set_position(self, pos): self.pos = pos
    def insert_text(self, text, pos):
        self.text = self.text[:pos] + text + self.text[pos:]
        return pos + len(text)


class Event:
    state = Gdk.ModifierType(0)
    keyval = Gdk.KEY_comma
    string = ","


target = Entry()
ime = nbpinyin.PinyinIME.__new__(nbpinyin.PinyinIME)
ime.active = True
ime.buffer = "qzx"
ime.cands = []
ime.page = 0
ime.popup = None
ime._composition_target = target
ime._focus_text = lambda: target

handled = ime._on_key(None, Event())
ok = handled is False and target.text == "qzx" and ime.buffer == ""
print(("PASS" if ok else "FAIL"),
      "unknown composition is committed before punctuation passes through")
# Terminal verdict for the release runner (see run_all_gates SUCCESSWORD): a
# bare PASS line is not a report it will trust.
print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
raise SystemExit(not ok)
