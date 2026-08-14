#!/usr/bin/env python3
"""finder_open_folder_selftest — stepping into a folder travels (Article G2).

Back and Forward already slid the outgoing listing along the grid, but opening
a folder — by far the most repeated navigation in the OS — was a hard cut. Going
IN cut and coming OUT slid, which reads as an unfinished machine.

An open now slides the outgoing listing off to the LEFT, the way Forward
travels, so Back is its exact inverse: the listing leaves the way it arrived.

THE TRAP THIS GUARDS. The direction of travel and "is this a return?" are two
different questions, and finder answers both from one value. `restores_place()`
reads `_nav_dir` to decide whether to put the person back on a remembered row,
and it counts FORWARD as a return. So stamping `_nav_dir = FORWARD` on an open
would have slid correctly AND silently restored a stale selection and scroll
position into a folder the person had just opened — where everyone expects the
top. The slide therefore rides its own one-shot signal, `_nav_enter`, and this
suite pins that separation: the motion is checked here, and so is the absence
of the side effect.

The behavioural half DRIVES the real code path (a store row -> _open_path ->
load) and reads the slide the Finder actually scheduled, rather than reading
the source and believing it. It needs a display; without one the source
contract still runs and the skip is PRINTED, never laundered into a pass.
"""
import ast
import atexit
import inspect
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# FINDER_MODULE_DIR points the whole suite — import AND source reading — at a
# scratch copy of de/, so a red proof sabotages a COPY and is then graded on
# that copy. A suite that imports the mutant but reads the pristine file grades
# the wrong thing and reports a false green; that has happened here before.
DE = Path(os.environ.get("FINDER_MODULE_DIR")
          or REPO / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


home = tempfile.mkdtemp(prefix="finder_open_folder_")
atexit.register(shutil.rmtree, home, True)
os.environ["NB_HOME"] = home
os.makedirs(os.path.join(home, "Documents", "Sub"), exist_ok=True)

sys.path.insert(0, str(DE))
import finder                                                   # noqa: E402
import nbicons                                                  # noqa: E402
import nbmotion                                                 # noqa: E402
import nbtransitions                                            # noqa: E402

# ---------------------------------------------------------------- source half
open_src = inspect.getsource(finder.Finder._open_path)
load_src = inspect.getsource(finder.Finder.load)

check("_open_path carries the open-folder inventory marker",
      "nbmotion-inventory: finder.open-folder" in open_src)
check("_open_path arms the one-shot enter signal",
      "_nav_enter = True" in open_src)


def assigns(src, attr):
    """Whether `src` ASSIGNS self.<attr>. Asked of the syntax tree, not of the
    text: the comment in _open_path explains at length why it does not stamp
    the history direction, and a substring search would read its own
    explanation as the offence it is warning about."""
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr == attr:
                return True
    return False


# The whole point of the separate signal: an open must not masquerade as a
# history move, because restores_place() reads that value and counts FORWARD
# as a return.
check("_open_path does NOT stamp the history direction (restores_place trap)",
      not assigns(open_src, "_nav_dir"))
check("_open_path really does arm the enter signal (same syntax check)",
      assigns(open_src, "_nav_enter"))
check("load consumes the enter signal (one-shot, so it cannot leak forward)",
      "_nav_enter = False" in load_src)
check("an arrival is still not a return (restores_place excludes NONE)",
      finder.restores_place(nbtransitions.NONE) is False)
check("a history move is still a return",
      finder.restores_place(nbtransitions.BACK)
      and finder.restores_place(nbtransitions.FORWARD))
# The slide must stay behind the motion policy, or a still machine animates.
check("the slide is gated on the live motion policy",
      "nbmotion.policy(" in load_src)

# ----------------------------------------------------------- behavioural half
display_ready = False
if os.environ.get("DISPLAY"):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        display_ready = Gtk.init_check()[0]
    except Exception:                                           # noqa: BLE001
        display_ready = False

if not display_ready:
    # Printed, not silent: a skipped half is missing evidence, not a pass.
    print("SKIP behavioural half: no usable display "
          "(run under tools/guestrun.sh with DISPLAY set)")
else:
    import cairo

    def fresh():
        """A Finder with the capture stubbed to always succeed and the slide
        recorded instead of run, so the DECISION is observable without a
        mapped toplevel (an unmapped window captures nothing)."""
        # The Home listing is rel "" — "Home" would resolve to NB_HOME/Home,
        # which does not exist, and the empty store would make every
        # behavioural check below vacuous.
        w = finder.Finder(start="")
        signs = []
        w._snapshot_content = lambda: (
            cairo.ImageSurface(cairo.FORMAT_ARGB32, 40, 40), 40, 40)
        w._start_nav_slide = lambda snap, sign: signs.append(sign)
        return w, signs

    def row_for(w, rel):
        """The store path of the row naming `rel` — the real double-click
        target, so the test enters the folder the way a person does."""
        store, it = w.store, None
        it = store.get_iter_first()
        while it is not None:
            if store.get_value(it, 4) == rel:
                return store.get_path(it)
            it = store.iter_next(it)
        return None

    w, signs = fresh()
    path = row_for(w, "Documents")
    check("the Documents row is present to open", path is not None)
    if path is not None:
        w._open_path(path)
        check("opening a folder slides the outgoing listing LEFT",
              signs == [-1.0])
        check("opening a folder actually navigated", w.rel == "Documents")
        check("the enter signal is consumed by the load", w._nav_enter is False)

        # ...and Back is its exact inverse, which is the whole claim.
        signs.clear()
        w.go_back()
        check("Back slides it RIGHT — the exact inverse", signs == [1.0])

        # A jump that is neither a step in nor a history move has no direction
        # and must not invent one (sidebar places, a refresh).
        signs.clear()
        w.load("Documents")
        check("a plain jump does not slide", signs == [])

    # Instant equivalence (Article F4): a still machine stays still.
    w2, signs2 = fresh()
    p2 = row_for(w2, "Documents")
    nbmotion.set_reduced_motion(True)
    try:
        if p2 is not None:
            w2._open_path(p2)
            check("under reduced motion an open does NOT slide", signs2 == [])
            check("...and still navigates", w2.rel == "Documents")
    finally:
        nbmotion.set_reduced_motion(False)

    # ---- the breadcrumb grows with the step, it does not restage ----------
    # set_trail rebuilds every pill on every navigation. Only the components
    # DEEPER than where you came from are new, so only those may open; the
    # pills already on screen must be packed plainly or the whole bar restages
    # on each move — the same rule as the sidebar's arriving volumes.
    trail_src = inspect.getsource(finder.Crumbs.set_trail)
    check("set_trail carries an inventory marker",
          "nbmotion-inventory:" in trail_src)

    def crumb_revealers(w):
        return [c for c in w.crumb.get_children()
                if isinstance(c, Gtk.Revealer)]

    wc, _ = fresh()
    check("the first paint opens no crumb (the bar arrives whole)",
          crumb_revealers(wc) == [])
    before_text = wc.crumb.get_text()

    pc = row_for(wc, "Documents")
    if pc is not None:
        wc._open_path(pc)
        check("stepping in opens exactly the one new crumb",
              len(crumb_revealers(wc)) == 1)
        check("...and the trail still reads as plain text",
              wc.crumb.get_text() == before_text + "  ›  Documents")
        # Going back only REMOVES components, so nothing is new to open.
        wc.go_back()
        check("going back opens no crumb", crumb_revealers(wc) == [])
        check("...and the trail is back to where it started",
              wc.crumb.get_text() == before_text)

    # ---- Back/Forward icon colour travels instead of snapping -------------
    # The button's own colours ease on the theme's 90ms feedback spring, but the
    # arrow inside it is a pre-rendered cairo SURFACE that CSS cannot reach, so
    # it used to snap while its button eased.
    nav_src = inspect.getsource(finder.Finder._set_nav)
    mix = finder.Finder._mix_hex
    check("the icon tween lands EXACTLY on both end colours",
          mix("#3A362E", "#B3AD9E", 0.0).lower() == "#3a362e"
          and mix("#3A362E", "#B3AD9E", 1.0).lower() == "#b3ad9e")
    check("...and genuinely travels between them",
          mix("#3A362E", "#B3AD9E", 0.5).lower() == "#767266")
    # nbicons caches surfaces in an unbounded dict keyed on colour, so a
    # continuous tween would leak one entry per frame forever.
    check("the tween is quantised, so the icon cache cannot grow without bound",
          isinstance(finder.Finder._NAV_TWEEN_STEPS, int)
          and finder.Finder._NAV_TWEEN_STEPS <= 8
          and "steps" in nav_src)
    # Frames come from the widget's frame clock: an unmapped button could start
    # a tween that never ticks and sit at its OLD colour, showing the wrong
    # enabled state. That is a correctness guard, not an optimisation.
    check("an off-screen button is never left mid-tween",
          "_img_mapped" in nav_src)

    wn, _ = fresh()
    writes = []
    real_set_image = nbicons.set_image

    def record(img, name, size, color="#1A1916", width=1.6):
        writes.append((name, color))
        return real_set_image(img, name, size, color, width)

    nbicons.set_image = record
    try:
        pn = row_for(wn, "Documents")
        if pn is not None:
            writes[:] = []
            wn._open_path(pn)          # Back becomes enabled
            back = [c for n, c in writes if n == "back"]
            # This window is never mapped, so the guard above must take the
            # instant path — one write, landing on the ENABLED colour.
            check("an unmapped Back lands instantly on the right colour",
                  back == ["#3A362E"])
            nbmotion.set_reduced_motion(True)
            try:
                writes[:] = []
                wn.go_back()           # Back becomes disabled again
                back2 = [c for n, c in writes if n == "back"]
                check("under reduced motion the colour is written once",
                      back2 == ["#B3AD9E"])
            finally:
                nbmotion.set_reduced_motion(False)
    finally:
        nbicons.set_image = real_set_image

    # A capture that fails must degrade to the old instant swap. Navigation is
    # the feature; the slide is decoration on top of it and may never break it.
    w3, signs3 = fresh()
    p3 = row_for(w3, "Documents")
    w3._snapshot_content = lambda: None
    if p3 is not None:
        w3._open_path(p3)
        check("a failed capture falls back to no slide", signs3 == [])
        check("...and navigation still happens", w3.rel == "Documents")

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
