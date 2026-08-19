#!/usr/bin/env python3
"""Prove every b\"\"\"...\"\"\" CSS block in a module actually PARSES.

py_compile only proves the bytes are a legal Python literal, and the token
checker only greps values -- both stay green on CSS that GTK cannot parse. Most
apps also wrap load_from_data in try/except, so a parse failure ships as an
UNSTYLED app rather than a crash. This is the only gate that can go red on it.
"""
import re
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

BLOCK = re.compile(r'b?(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', re.S)

# Several apps build their CSS as a Python TEMPLATE and substitute real values
# at runtime (contacts.py alone has 38 of them, writer.py styles its page desk
# this way). The raw block legitimately does not parse -- "background: %(desk)s"
# is not CSS -- so checking it verbatim reports the app's own token indirection
# as breakage. Measured before this was handled: 73 "errors", every single one a
# placeholder, in files that were completely fine.
#
# Substituting a valid literal first keeps the gate pointed at real corruption
# (a mangled declaration still fails) without punishing the files that are, if
# anything, the best-organised ones in the tree.
_PLACEHOLDER = re.compile(r"%\((\w+)\)s|%s|\{(\w*)\}")
_CSS_DECL = re.compile(
    r"(?m)(?:^|[;{])\s*(?:background(?:-\w+)?|color|border(?:-\w+)?|"
    r"padding(?:-\w+)?|margin(?:-\w+)?|font(?:-\w+)?|transition(?:-\w+)?|"
    r"box-shadow|opacity|min-width|min-height|outline(?:-\w+)?)\s*:")


def _fill(css):
    """Replace runtime placeholders with a value legal in any position."""
    return _PLACEHOLDER.sub("#000000", css)

bad = 0
for path in sys.argv[1:]:
    src = open(path, encoding="utf-8").read()
    name = path.split("/")[-1]
    blocks = 0
    for m in BLOCK.finditer(src):
        body = _fill(m.group(1))
        if "{" not in body or "}" not in body or not _CSS_DECL.search(body):
            continue                      # not a stylesheet
        blocks += 1
        line0 = src.count("\n", 0, m.start(1)) + 1
        errs = []
        prov = Gtk.CssProvider()
        prov.connect("parsing-error",
                     lambda p, sec, err, e=errs: e.append(
                         (sec.get_start_line() + line0, err.message)))
        try:
            prov.load_from_data(body.encode("utf-8"))
        except GLib.Error as e:
            errs.append((line0, str(e)))
        for ln, msg in errs:
            bad += 1
            print("PARSE-ERROR %s L%d: %s" % (name, ln, msg))
    print("%-22s %d css block(s)%s" % (name, blocks, "" if blocks else "  (none)"))
print("clean" if not bad else "%d parse error(s)" % bad)
sys.exit(1 if bad else 0)
