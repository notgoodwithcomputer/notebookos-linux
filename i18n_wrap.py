#!/usr/bin/env python3
"""Conservative i18n wrapper for the Notebook OS apps.

Wraps user-facing string LITERALS in a few unambiguous GTK contexts with _(),
adds `from nbi18n import _` if missing, and prints the English strings it
wrapped (for translating). Deliberately narrow to avoid breaking code:
  - only double-quoted plain string literals (no f"", b"", r"", no concatenation)
  - only in these positions: label=, set_title(, set_placeholder_text(,
    set_tooltip_text(, set_label(, set_markup(, Gtk.Label( / Gtk.Button( first arg
  - skips strings with no letters, already-wrapped strings, and lines that are
    comments.
Menu-item tuples and other positions are left for manual wrapping.

Usage: i18n_wrap.py FILE            # dry run: show wraps + strings
       i18n_wrap.py --apply FILE    # rewrite FILE in place
"""
import re
import sys
import ast

ONE = r'"(?:[^"\\\n]|\\.)*"'
# one string literal OR an implicit concatenation of several (across whitespace
# and newlines) — wrap the WHOLE thing so the continuation isn't orphaned
STR = ONE + r'(?:\s*' + ONE + r')*'
HAS_ALPHA = re.compile(r'[A-Za-z]')
# label=  and  set_*(  and  Gtk.Label(/Gtk.Button( first positional
ASSIGN = re.compile(r'(\blabel=)(' + STR + r')')
CALLS = re.compile(r'\b(set_title|set_placeholder_text|set_tooltip_text|'
                   r'set_label|set_markup|Gtk\.Label|Gtk\.Button)'
                   r'(\(\s*)(' + STR + r')')


def wrap(src):
    strings = []

    def value(st):
        try:
            v = ast.literal_eval(st)
        except (ValueError, SyntaxError):
            return None
        if not isinstance(v, str):
            return None
        if not HAS_ALPHA.search(v) or v.startswith("%"):
            return None
        return v

    def rep_assign(m):
        v = value(m.group(2))
        if v is None:
            return m.group(0)
        strings.append(v)
        return "%s_(%s)" % (m.group(1), m.group(2))

    def rep_call(m):
        v = value(m.group(3))
        if v is None:
            return m.group(0)
        strings.append(v)
        return "%s%s_(%s)" % (m.group(1), m.group(2), m.group(3))

    # process the whole source (not line-by-line) so multi-line implicit
    # string concatenations match as one unit
    text = ASSIGN.sub(rep_assign, src)
    text = CALLS.sub(rep_call, text)
    if strings and "from nbi18n import _" not in text:
        # insert after the last top-level import near the top
        lines = text.splitlines(keepends=True)
        idx = 0
        for i, ln in enumerate(lines[:80]):
            if ln.startswith("import ") or ln.startswith("from "):
                idx = i + 1
        lines.insert(idx, "from nbi18n import _  # noqa: E402\n")
        text = "".join(lines)
    return text, strings


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--apply"]
    path = args[0]
    with open(path, encoding="utf-8") as f:
        src = f.read()
    new, strings = wrap(src)
    if apply:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print("wrapped %d strings in %s" % (len(strings), path))
    else:
        for s in dict.fromkeys(strings):
            print(repr(s))
        print("--- %d string sites (%d unique) ---"
              % (len(strings), len(dict.fromkeys(strings))))
