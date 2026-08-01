#!/usr/bin/env python3
"""
Headless selftest for academics.py's Style-cycle paragraph control.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/root python3 academic_style_selftest.py

Validates that the previously-dead "Style ▾" button now cycles the current
line's paragraph style Body -> Heading -> Subheading -> Body, applying/removing
the heading & subheading TextTags and updating the button label.
Self-contained and idempotent: creates no files.
"""
import inspect
import os
import tempfile

# PIN NB_HOME BEFORE IMPORTING THE APP: unset, the app reads and writes the
# caller's own ~/.config/notebook, and the single-instance guard lands on the
# unscoped /tmp/nb-apps shared with any running app -- where
# nbapp.claim_single_instance() os._exit(0)s this process with no output and
# exit status 0, which reads as a pass while nothing was tested.
os.environ.setdefault("NB_HOME",
                      tempfile.mkdtemp(prefix="acadstyle-selftest-"))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import academics as mod

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def line_has(buf, name):
    """True if the tag `name` is applied at the start of line 0."""
    tag = buf.get_tag_table().lookup(name)
    if tag is None:
        return False
    it = buf.get_iter_at_line(0)
    return it.has_tag(tag)


# ---- locate the AppWindow subclass defined in academics.py ----
win_cls = None
for _n, _obj in inspect.getmembers(mod, inspect.isclass):
    if _obj.__module__ == mod.__name__ and issubclass(_obj, Gtk.Window):
        win_cls = _obj
        break
check("window class located", win_cls is not None)

win = win_cls()

# ---- open a note so self.body / its buffer exist ----
win._new_lecture()
check("note created (self.body exists)", hasattr(win, "body"))

buf = win.body.get_buffer()

# heading / subheading tags must exist in the buffer's tag table
check("heading tag registered", buf.get_tag_table().lookup("heading") is not None)
check("subheading tag registered",
      buf.get_tag_table().lookup("subheading") is not None)

# put text on the current line so paragraph tags have something to cover
buf.set_text("Introduction to Topology")
# cursor sits on line 0 after set_text; confirm the line starts as Body
ins = buf.get_iter_at_mark(buf.get_insert())
check("initial line is Body (no heading/subheading)",
      not line_has(buf, "heading") and not line_has(buf, "subheading"))

# ---- cycle 1: Body -> Heading ----
win._cycle_style()
check("cycle 1 applies heading tag", line_has(buf, "heading"))
check("cycle 1 has no subheading tag", not line_has(buf, "subheading"))
check("cycle 1 label == Heading", win.stylelbl.get_text() == "Heading")

# ---- cycle 2: Heading -> Subheading ----
win._cycle_style()
check("cycle 2 applies subheading tag", line_has(buf, "subheading"))
check("cycle 2 removed heading tag", not line_has(buf, "heading"))
check("cycle 2 label == Subheading", win.stylelbl.get_text() == "Subheading")

# ---- cycle 3: Subheading -> Body (both tags removed) ----
win._cycle_style()
check("cycle 3 removed heading tag", not line_has(buf, "heading"))
check("cycle 3 removed subheading tag", not line_has(buf, "subheading"))
check("cycle 3 label == Body", win.stylelbl.get_text() == "Body")

# ---- guard: cycling with no body must not raise ----
try:
    delattr(win, "body")
    win._cycle_style()
    guarded = True
except Exception:
    guarded = False
check("cycle is a no-op when no body exists", guarded)

print("RESULT: " + ("ALL PASS" if all(results) else "SOME FAILED"))
