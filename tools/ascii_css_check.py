#!/usr/bin/env python3
"""ascii_css_check — catch the em-dash-in-a-byte-string SyntaxError fast.

Every app installs its CSS from a b-triple-quoted literal. Python requires those to
be pure ASCII, so typing a nice em dash (—), a curly quote (’ “ ”) or an ellipsis
(…) inside one is an instant SyntaxError that stops the app importing at all —
and because several modules import finder.py, one slip there takes ten apps down
with it. The characters are perfectly fine in ordinary str comments and in
user-facing copy; only the byte-string CSS blocks must stay ASCII.

Reports the file, line and character so the fix is obvious. Exit 1 if any found.

    python3 tools/ascii_css_check.py
"""
import os
import re
import sys
import unicodedata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

BYTES_BLOCK = re.compile(r'b(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', re.S)
BYTES_LINE = re.compile(r'b"([^"\n]*)"|b\'([^\'\n]*)\'')


def check(path):
    src = open(path, encoding="utf-8").read()
    hits = []
    for m in list(BYTES_BLOCK.finditer(src)) :
        block, start = m.group(1), m.start(1)
        for i, ch in enumerate(block):
            if ord(ch) > 127:
                line = src[:start + i].count("\n") + 1
                hits.append((line, ch))
    for m in BYTES_LINE.finditer(src):
        text = m.group(1) or m.group(2) or ""
        for i, ch in enumerate(text):
            if ord(ch) > 127:
                line = src[:m.start()].count("\n") + 1
                hits.append((line, ch))
    return hits


def main():
    bad = 0
    for f in sorted(os.listdir(DE)):
        if not f.endswith(".py"):
            continue
        for (line, ch) in check(os.path.join(DE, f)):
            bad += 1
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "?"
            print("%s:%d  non-ASCII %r (U+%04X %s) inside a bytes literal"
                  % (f, line, ch, ord(ch), name))
    if bad:
        print("\n%d occurrence(s) — these are SyntaxErrors, the apps will not "
              "import. Use a plain ASCII '-' inside byte-string CSS blocks." % bad)
    else:
        print("clean: no non-ASCII inside any bytes literal")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
