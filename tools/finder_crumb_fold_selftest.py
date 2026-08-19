#!/usr/bin/env python3
"""The breadcrumb must not fold a two-pill trail at the size a user first sees.

The crumb bar folds leading pills behind a "…" when the trail is wider than the
toolbar can give it, keeping the current folder in view. But folding the ONLY
leading pill of a two-pill trail — the root, "Home" — replaces it with a "…"
of nearly the same width: no space is won, and the ellipsis reads as a stray
".." between the Actions button and the current folder. This bit at 775px, the
Finder window's DEFAULT width on a 1280x800 desktop (it leaves the widget board
free), where "Home › Applications" — the first screen every user meets — folded
to "… › Applications".

The fold itself is right for a genuinely long trail, so this pins the boundary:
a two-pill trail never folds; a deep one still does when it overflows. It needs
a display (the fold is driven by the scroller's real allocated width), and it
hosts the content at exact sizes off-screen rather than trusting the source.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
HOME = tempfile.mkdtemp(prefix="finder-crumb-fold-")
os.environ["NB_HOME"] = HOME
os.makedirs(os.path.join(HOME, "Applications"), exist_ok=True)
DEEP = os.path.join(HOME, "Documents", "A very long folder name",
                    "Another deep one", "And a third level", "Fourth")
os.makedirs(DEEP, exist_ok=True)

import gi                                             # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                         # noqa: E402
import finder                                         # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <- %s" % detail))
    if not ok:
        FAILS.append(name)


if not Gtk.init_check()[0]:
    print("SKIP no display; the fold is a real-allocation property")
    raise SystemExit(0)


def pump(n=80):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def pills(bar):
    out = []
    for c in bar.get_children():
        b = c.get_child() if isinstance(c, Gtk.Revealer) else c
        out.append(b.get_label() if isinstance(b, Gtk.Button)
                   else "<%s>" % type(b).__name__)
    return out


def hosted(rel, w, h):
    win = finder.Finder()
    child = win.get_child()
    win.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_size_request(w, h)
    off.add(child)
    off.show_all()
    pump()
    win.load(rel)
    pump()
    result = (getattr(win.crumb, "_folded_from", None), pills(win.crumb))
    off.destroy()
    pump(5)
    return result


# 775x715 is the default window size on a 1280x800 desktop — the first screen.
for w, h in ((775, 715), (1024, 740), (1280, 800)):
    folded, labels = hosted("Applications", w, h)
    chk("two-pill Home > Applications does not fold at %dx%d" % (w, h),
        folded == 0 and labels == ["Home", "Applications"],
        "folded_from=%s pills=%s" % (folded, labels))

# ...but a genuinely overflowing DEEP trail still folds, keeping the current
# folder visible behind a leading "…".
folded, labels = hosted(
    "Documents/A very long folder name/Another deep one/"
    "And a third level/Fourth", 775, 715)
chk("a deep overflowing trail still folds at 775x715",
    folded and folded > 0 and labels and labels[0] == "…"
    and labels[-1] == "Fourth",
    "folded_from=%s pills=%s" % (folded, labels))

# ...and the pills that are NOT folded must actually be READABLE. Checking the
# widget tree is not enough: the row can hold "Home" and "Applications" at full
# width while the scroller — which anchors right to keep the current folder in
# view — shows only the last 19px of "Home", so the first screen every user
# sees reads "Hidden | Actions | e | Applications". That is the mid-letter
# sliver 52672195 removed, and this suite passed straight through it, because
# nothing here looked at the scroller. Measured 2026-08-17: crumb natural 162,
# scroller page 123, value 39.
def hosted_scroll(rel, w, h):
    win = finder.Finder()
    child = win.get_child()
    win.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_size_request(w, h)
    off.add(child)
    off.show_all()
    pump()
    win.load(rel)
    pump()
    off.check_resize()
    pump()
    adj = win._crumbscroll.get_hadjustment()
    got = (adj.get_value(), adj.get_page_size(), adj.get_upper(),
           win.crumb.get_preferred_width()[1])
    off.destroy()
    pump(5)
    return got


for w, h in ((775, 715), (1024, 740), (1280, 800)):
    value, page, upper, natural = hosted_scroll("Applications", w, h)
    chk("every pill of Home > Applications is fully visible at %dx%d "
        "(no mid-letter sliver)" % (w, h),
        value == 0 and page + 0.5 >= natural,
        "scrolled=%.0f page=%.0f natural=%d" % (value, page, natural))

print("\n%d failure(s)" % len(FAILS))
print("RESULT: %s" % ("FAILED" if FAILS else "ALL PASS"))
sys.exit(1 if FAILS else 0)
