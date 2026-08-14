#!/usr/bin/env python3
"""cookbook_layout_selftest — a longer translation may shorten one label,
never push the app off the panel.

Run as:  DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf \
         python3 tools/cookbook_layout_selftest.py

WHY THIS EXISTS
---------------
The minsize sweep measured cookbook at 1019 of the 1024px budget in Polish —
five pixels from the overflow class this OS has actually shipped (content
clipped off the smallest panel, and the part that falls off is the part you
need). The width was pinned by the edit header: sidebar + page margin + the
stat strip are all fixed, so every extra pixel a catalog gives the
Start-cooking label used to become a pixel of app minimum. The fix makes that
label yield: below its natural width it ellipsizes (the tooltip carries the
sentence), so the app's minimum no longer depends on any catalog's length.

This suite pins the INVARIANT, not a number: doubling the button's label must
change the app's minimum width by exactly zero. A check against a measured
constant would go stale the day the sidebar is redesigned; the invariant holds
through any redesign that keeps the label elastic, and fails the moment
someone removes the ellipsize — reintroducing the latent overflow — which is
precisely the regression it exists to catch (red-proved: with the ellipsize
line removed, the doubled label grows the minimum by ~90px and checks 3 and 4
fail naming it).

Measures REAL layout, so it needs a display and the guest font metrics; on a
machine without one it REFUSES loudly rather than passing vacuously.
"""
import importlib
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, DE)

# Assigned, not setdefault: isolation a caller can switch off is not
# isolation (task 010).
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="cookbook-layout-")
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

if not Gtk.init_check([])[0]:
    print("UNAVAILABLE: no display. This suite measures real layout and a "
          "pass without a measurement would be vacuous — refusing instead.")
    sys.exit(1)

import uishot  # noqa: E402
import dialogshot  # noqa: E402
import nbapp  # noqa: E402

W, H = 1024, 722
uishot.load_theme()
nbapp.screen_size = lambda: (W, H)

cookbook = importlib.import_module("cookbook")
dialogshot.install_app_css(cookbook)

fails = []
ran = []


def check(cond, message):
    print(("ok   " if cond else "FAIL ") + message)
    ran.append(message)
    if not cond:
        fails.append(message)


def pump(n=60):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def find_by_class(widget, name):
    if widget.get_style_context().has_class(name):
        return widget
    if isinstance(widget, Gtk.Container):
        for ch in widget.get_children():
            got = find_by_class(ch, name)
            if got is not None:
                return got
    return None


cls = None
for _n, c in inspect.getmembers(cookbook, inspect.isclass):
    if c.__module__ == cookbook.__name__ and issubclass(c, Gtk.Window):
        cls = c
        break
app = cls()
child = app.get_child()
app.remove(child)
off = Gtk.OffscreenWindow()
off.set_size_request(W, H)
off.add(child)
off.show_all()
pump()

btn = find_by_class(child, "startcook")
check(btn is not None, "the edit header holds the Start-cooking button")
lbl = btn.get_child() if btn is not None else None
check(isinstance(lbl, Gtk.Label),
      "the button's child is the label the layout depends on")

# 1. The mechanism: the label yields below its natural width.
check(lbl is not None and lbl.get_ellipsize() == Pango.EllipsizeMode.END,
      "the Start-cooking label ellipsizes under pressure")

# 2. Parity today: on the smallest panel, the shipped label renders WHOLE.
#    The fix must be invisible at every width the OS actually grants.
whole = (lbl is not None and lbl.get_layout() is not None
         and not lbl.get_layout().is_ellipsized())
check(whole, "at %dx%d the shipped label is not truncated" % (W, H))

# 3. The invariant: label length contributes ZERO to the app's minimum
#    width. Not "stays under some constant" — a constant goes stale with the
#    next redesign; zero is zero through any redesign that keeps the label
#    elastic.
m0 = child.get_preferred_width()[0]
lbl.set_text(lbl.get_text() * 2 + " extended")
pump()
m1 = child.get_preferred_width()[0]
check(m1 == m0,
      "doubling the label leaves the minimum unchanged (%d -> %d)" % (m0, m1))

# 4. The consequence the user cares about: even with a hostile translation,
#    the app still fits the smallest shipped panel.
check(m1 <= W, "with the doubled label the app still fits %dpx (needs %d)"
      % (W, m1))

# 5. ONE empty state on screen. A first-run cookbook showed the sidebar list
#    saying "No recipes / Add one with New Recipe, below." beside the main
#    pane saying "No recipes / Add one with New Recipe, below the list." —
#    the same two sentences a few hundred pixels apart, differing by two
#    words, which reads as a rendering fault rather than a considered empty
#    state. The main pane owns the message; the list stays quiet unless it
#    knows something the main pane does not.
texts = []


def collect(widget):
    if isinstance(widget, Gtk.Label):
        t = widget.get_text() or ""
        if t.strip():
            texts.append(t.strip())
    if isinstance(widget, Gtk.Container):
        for ch in widget.get_children():
            collect(ch)


app2 = cls()
app2.recipes = []
app2.active_cat = 0
app2.rebuild_list()
app2._refresh_editor() if hasattr(app2, "_refresh_editor") else None
child2 = app2.get_child()
app2.remove(child2)
off2 = Gtk.OffscreenWindow()
off2.set_size_request(W, H)
off2.add(child2)
off2.show_all()
pump()
collect(child2)
empties = [t for t in texts if t.lower().startswith("no recipes")]
check(len(empties) == 1,
      "an empty cookbook states it once, not twice (found %r)" % (empties,))
hints = [t for t in texts if "new recipe" in t.lower() and "," in t]
check(len(hints) <= 1,
      "and offers the next move once, not twice (found %r)" % (hints,))

# ...and the list DOES speak when it alone knows why it is empty: recipes
# exist, a category filter is hiding them. "No recipes" was untrue there.
app3 = cls()
app3.recipes = [{"name": "Soup", "cat": "Dinner", "ing": "", "steps": ""}]
app3.cats = ["Dinner", "Baking"]
app3.active_cat = 2                      # "Baking": real, and empty
app3.rebuild_list()
pump()
rows = [r for r in app3.listbox.get_children()]
filtered = []
for r in rows:
    collect_target = r
    saved = list(texts)
    del texts[:]
    collect(collect_target)
    filtered += texts
    del texts[:]
    texts.extend(saved)
check(any("Baking" in t for t in filtered),
      "a filtered-empty list names the category, not a bare 'No recipes' "
      "(found %r)" % (filtered,))

off2.destroy()
off.destroy()

print("%s — %d/%d checks passed"
      % ("FAIL" if fails else "PASS", len(ran) - len(fails), len(ran)))
sys.exit(1 if fails else 0)
