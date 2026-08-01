#!/usr/bin/env python3
"""tofu_sweep — find every character in the desktop's user-facing text that the
GUEST's fonts cannot draw.

A character with no glyph in any shipped font renders as a "tofu" box — on this
OS, often a box printing the codepoint's hex digits, which is exactly what a
non-technical user must never see. This walks every string literal in the DE
sources, collects the non-ASCII characters, and asks Pango (pointed at the guest
font tree via FONTCONFIG_FILE) whether each one actually resolves.

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf python3 tools/tofu_sweep.py

FONTCONFIG_FILE IS NOT OPTIONAL. Run without it and Pango answers from the
HOST's fonts, which gives confidently wrong answers in BOTH directions: it hides
real tofu the guest would show, and it invents tofu that does not exist. On
2026-07-28 a run without it reported 588 Hangul syllables as unrenderable and
concluded that every Korean screen in the OS was broken. The bundled
NotoSansCJKsc-Regular.otf covers U+AC00-U+D7A3 in full; Korean was fine. This
script now refuses to run without the guest config rather than let that happen
again.
"""
import os
import re
import sys
import ast
import unicodedata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
GUEST_FC = os.path.join(REPO, "tools", "guest-fonts.conf")

# ---------------------------------------------------------------- font guard
# This MUST run before gi/Pango is imported, because fontconfig is configured
# from the environment the moment Pango first touches it.
#
# fontconfig does NOT resolve a relative FONTCONFIG_FILE against the current
# directory — it resolves it against FONTCONFIG_PATH (/etc/fonts). So the
# documented invocation `FONTCONFIG_FILE=tools/guest-fonts.conf` is silently
# ignored: fontconfig prints "Cannot load default config file", falls back to
# the HOST's fonts, and the sweep then reports all 588 Hangul syllables in
# lang_ko.json as tofu. That is the exact false alarm this file's docstring
# warns about, and the old guard did not catch it because it compared
# os.path.abspath(FONTCONFIG_FILE) — which happily "fixes" a relative path that
# fontconfig itself never will. Normalise the variable and re-exec so the child
# process gets an absolute path that fontconfig actually honours.
_fc = os.environ.get("FONTCONFIG_FILE", "")
if os.path.abspath(_fc or "/nonexistent") != os.path.abspath(GUEST_FC):
    sys.stderr.write(
        "tofu_sweep: refusing to run against the HOST's fonts.\n"
        "  This tool is only meaningful pointed at the GUEST font tree; without\n"
        "  it the answers are wrong in both directions (it has previously\n"
        "  reported all of Korean as broken when it was not).\n\n"
        "  Re-run as:\n"
        "    DISPLAY=:0 FONTCONFIG_FILE=%s \\\n"
        "        python3 tools/tofu_sweep.py\n" % GUEST_FC)
    raise SystemExit(2)
if not os.path.isabs(_fc):
    os.environ["FONTCONFIG_FILE"] = os.path.abspath(GUEST_FC)
    os.execv(sys.executable, [sys.executable] + sys.argv)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

# The faces the UI actually asks for. A character is only safe if it resolves in
# the family the text will be rendered in (or fontconfig substitutes for it).
FAMILIES = ["Nimbus Sans", "Newsreader", "DejaVu Sans", "DejaVu Sans Mono"]


def literals(path):
    """(char, line, snippet) for every non-ASCII char in a source file's strings.

    Docstrings are skipped. They are never drawn, and counting them produced a
    permanent phantom failure: video._clip_badges' docstring names the very
    glyphs it is warning the reader NOT to use, so the sweep reported the
    warning itself as tofu."""
    src = open(path, encoding="utf-8").read()
    found = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return found
    lines = src.splitlines()
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
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


def _assert_guest_fonts():
    """Loud warning if the HOST's font tree leaked in despite the guard.

    The guest ships ~13 families. A count far above that means fontconfig
    ignored guest-fonts.conf and every answer below is about the wrong machine."""
    win = Gtk.OffscreenWindow()
    fams = sorted(f.get_name() for f in win.get_pango_context().list_families())
    win.destroy()
    if "Nimbus Sans" not in fams or len(fams) > 40:
        sys.stderr.write(
            "tofu_sweep: WARNING - fontconfig is not showing the guest tree\n"
            "  (%d families visible, Nimbus Sans %s). Results below describe\n"
            "  the HOST's fonts and must not be trusted.\n"
            % (len(fams), "present" if "Nimbus Sans" in fams else "MISSING"))
    return fams


def main():
    _assert_guest_fonts()
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
