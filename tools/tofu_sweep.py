#!/usr/bin/env python3
"""tofu_sweep — find every character in the desktop's user-facing text that the
GUEST's fonts cannot draw.

A character with no glyph in any shipped font renders as a "tofu" box — on this
OS, often a box printing the codepoint's hex digits, which is exactly what a
non-technical user must never see. This walks every string literal in the DE
sources, collects the non-ASCII characters, and asks Pango (pointed at the guest
font tree via FONTCONFIG_FILE) whether each one actually resolves.

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf python3 tools/tofu_sweep.py
"""
import os
import re
import sys
import ast
import unicodedata

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# The faces the UI actually asks for. A character is only safe if it resolves in
# the family the text will be rendered in (or fontconfig substitutes for it).
FAMILIES = ["Nimbus Sans", "Newsreader", "DejaVu Sans", "DejaVu Sans Mono"]


def literals(path):
    """(char, line, snippet) for every non-ASCII char in a source file's strings."""
    src = open(path, encoding="utf-8").read()
    found = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return found
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for ch in node.value:
                if ord(ch) > 127:
                    ln = getattr(node, "lineno", 0)
                    snip = lines[ln - 1].strip()[:90] if 0 < ln <= len(lines) else ""
                    found.append((ch, ln, snip))
    return found


def json_literals(path):
    import json
    out = []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return out

    def walk(o):
        if isinstance(o, str):
            for ch in o:
                if ord(ch) > 127:
                    out.append((ch, 0, o[:80]))
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def unknown(ch, family):
    """True if `family` (after fontconfig substitution) has no glyph for ch."""
    win = Gtk.OffscreenWindow()
    ctx = win.get_pango_context()
    layout = Pango.Layout(ctx)
    desc = Pango.FontDescription(family + " 12")
    layout.set_font_description(desc)
    layout.set_text(ch, -1)
    n = layout.get_unknown_glyphs_count()
    win.destroy()
    return n > 0


def main():
    files = sorted(f for f in os.listdir(DE) if f.endswith((".py", ".json")))
    where = {}     # char -> list of (file, line, snippet)
    for f in files:
        p = os.path.join(DE, f)
        got = json_literals(p) if f.endswith(".json") else literals(p)
        for (ch, ln, snip) in got:
            where.setdefault(ch, []).append((f, ln, snip))

    print("%d distinct non-ASCII characters in DE text\n" % len(where))
    bad = []
    for ch in sorted(where):
        miss = [fam for fam in FAMILIES if unknown(ch, fam)]
        if len(miss) == len(FAMILIES):        # nothing can draw it -> tofu
            bad.append(ch)
    if not bad:
        print("No tofu: every character resolves in at least one shipped face.")
    for ch in bad:
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = "?"
        print("TOFU  U+%04X  %s" % (ord(ch), name))
        seen = set()
        for (f, ln, snip) in where[ch]:
            key = (f, ln)
            if key in seen:
                continue
            seen.add(key)
            print("        %s:%s  %s" % (f, ln, snip))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
