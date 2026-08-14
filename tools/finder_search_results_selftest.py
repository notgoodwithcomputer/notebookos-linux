#!/usr/bin/env python3
"""finder_search_results_selftest — the whole-Home search results settle IN.

finder.search-results (motion inventory): when the async whole-Home scan lands
its matches a beat after the in-folder filter, the results settle in beneath
what was already found (SURFACE_IN) rather than the list silently growing longer.
It is a fade on the ACTIVE view's opacity — the same primitive list<->grid uses
— that STARTS the view hidden and ends it at FULL opacity, so the results settle
in and the view is never left dim.

The contract is read from the source that runs (commands_selftest's way, and the
way the Finder's other motion suites work): constructing the Finder and driving
nbmotion's frame clock is display- and timing-dependent, and the fade's landing
is already gated by tools/motion_selftest.py. What is finder-specific — the
marker, the gating on real matches, opacity-only, and the hidden->full settle-in
pattern — is what this pins. Exit status is the failure count.

RED-PROOF (recorded 2026-08-08): changing `_settle_search_results`'s
`fade_to(view, 1.0, ...)` to `fade_to(view, 0.0, ...)` aims the settle at a dim
and turns "the settle fades the active view ... UP to full opacity" red;
removing the `# nbmotion-inventory: finder.search-results` marker turns the
marker check red.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="finder-search-"))

import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
import finder                                                    # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


# 1. the async delivery carries the marker, delegates to the settle, and fires
#    ONLY when the scan actually added matches (gated on self._wide) — a fade on
#    every keystroke's in-folder filter would flicker; the wide results are the
#    one clean async arrival.
wd = inspect.getsource(finder.Finder._wide_done)
check("wide-search delivery carries the finder.search-results marker",
      "nbmotion-inventory: finder.search-results" in wd)
check("it settles only when the scan added matches (gated on self._wide)",
      "if self._wide" in wd and "_settle_search_results()" in wd)

# 2. the settle is opacity-only (F2 — never a layout property) and is the
#    hidden->full settle-IN pattern (fade to 0 instantly, then up to 1.0): the
#    active view starts dim and ends at FULL opacity, so results settle in and
#    the view is never left dimmed. Order matters: 0 before 1.
st = inspect.getsource(finder.Finder._settle_search_results)
check("the settle animates opacity only (no layout property, F2)",
      "fade_to" in st and not any(b in st for b in
      ("set_size_request", "set_margin", "set_padding", "set_border_width")))
check("the settle fades the active view from hidden UP to full opacity "
      "(settles in, ends at full — no dim-leak)",
      "fade_to(view, 0.0, 0)" in st and "fade_to(view, 1.0," in st
      and st.index("fade_to(view, 0.0, 0)") < st.index("fade_to(view, 1.0,"))
check("the settle targets the ACTIVE view (list or grid), like list<->grid",
      "_grid_sw" in st and "_list_sw" in st and "_view" in st)

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
