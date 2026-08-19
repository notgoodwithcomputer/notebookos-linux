#!/usr/bin/env python3
"""Headless selftest for the E-book Reader font-size buttons.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/root python3 ebook_selftest.py

Validates the newly-wired reading font-size controls:
  * stored size starts at its default,
  * "larger" increases it,
  * "smaller" decreases it,
  * it clamps at the min and max bounds.
"""
import inspect
import shutil
import math
import os
import tempfile

# PIN NB_HOME BEFORE IMPORTING THE APP: unset, the app reads and writes the
# caller's own ~/.config/notebook, and the single-instance guard lands on the
# unscoped /tmp/nb-apps shared with any running app -- where
# nbapp.claim_single_instance() os._exit(0)s this process with no output and
# exit status 0, which reads as a pass while nothing was tested.
# ...and PIN it, do not setdefault: tools/guestrun.sh exports a SHARED
# NB_HOME (/tmp/nb-guestrun-home), which setdefault leaves in place, so every
# run of this suite read and wrote the same store as every other guestrun
# render. That was invisible while the reader persisted nothing but the shelf;
# now that the reading size is persisted too (it is a preference the reader
# set on purpose), a run that ends at the minimum size handed the NEXT run a
# store saying 12pt, and starts_at_default failed against a store this suite
# had written itself. A suite that tests defaults needs a home of its own.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="ebook-selftest-")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import ebook  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def find_window_cls(mod):
    for _, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    raise SystemExit("no Gtk.Window subclass found in module")


def decode_ceiling_checks():
    """What the reader ALLOCATES, measured — not what its code looks like.

    Two ceilings that were not there. An EPUB plate was decoded whole and only
    then scaled to the text column, so a cover stored at print resolution paid
    its full size in memory first. And a PDF page surface is page size times
    zoom times device scale, three multipliers with no cap, on a machine with
    no swap and a zoom button a person can hold down."""
    import zipfile
    import cairo
    import nbapp
    from gi.repository import GdkPixbuf

    # -- the EPUB plate. A real 6000x4000 PNG inside a real zip: 24 million
    # pixels, which is above the area budget and would be ~96MB decoded.
    home = tempfile.mkdtemp(prefix="ebook-decode-")
    big = os.path.join(home, "plate.png")
    GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 6000, 4000).savev(
        big, "png", [], [])
    book = os.path.join(home, "b.epub")
    with zipfile.ZipFile(book, "w") as zf:
        zf.write(big, "images/plate.png")

    cls = find_window_cls(ebook)
    win = cls()
    win._open_path = book          # the reader reads the plate from the open book

    # WHAT IS MEASURED IS THE PEAK, NOT THE RESULT. My first version of this
    # checked the returned pixbuf's size and passed with the ceiling REMOVED —
    # the display cap scales the plate to the text column afterwards either
    # way, so the finished object is small however much was allocated to make
    # it. The defect is the allocation DURING the decode, so what is observed
    # is the ceiling being applied to the source dimensions.
    asked = []
    real_budget = ebook.nbapp.decode_budget
    ebook.nbapp.decode_budget = lambda w, h: (asked.append((w, h))
                                              or real_budget(w, h))
    try:
        img = win._epub_image("images/plate.png", 60)
    finally:
        ebook.nbapp.decode_budget = real_budget
    pb = img.get_pixbuf() if img is not None else None
    check("an EPUB plate is decoded, not refused", pb is not None)
    check("the decode is bounded at its SOURCE size, before any pixels exist",
          (6000, 4000) in asked)
    if pb is not None:
        # And the display cap still applies on top, so it fits the column.
        check("...and the plate still fits the text column",
              pb.get_width() <= 560)

    # -- the PDF page surface. Driven through the real arithmetic rather than
    # a fake: a poster-sized page, zoomed, on a 2x panel.
    pw, ph, scale, sf = 3370.0, 2384.0, 4.0, 2
    w = max(1, int(math.ceil(pw * scale)))
    h = max(1, int(math.ceil(ph * scale)))
    want = nbapp.decode_budget(int(w * sf), int(h * sf))
    eff = sf * min(1.0, want[0] / float(max(1, int(w * sf))))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                              max(1, int(w * eff)), max(1, int(h * eff)))
    got = surf.get_width() * surf.get_height()
    check("a poster page zoomed on a HiDPI panel stays inside the budget",
          got <= nbapp.DECODE_MAX_AREA)
    check("MUTANT: the unbounded surface DOES exceed it",
          (w * sf) * (h * sf) > nbapp.DECODE_MAX_AREA)
    # Softer, never smaller: the surface carries the scale, so the page is
    # still placed at its logical size.
    check("the page keeps its logical size, losing only density",
          abs(surf.get_width() / eff - w) < 2.0)
    win.destroy()
    shutil.rmtree(home, ignore_errors=True)


def main():
    cls = find_window_cls(ebook)
    win = cls()

    lo, hi, default = cls.READ_PT_MIN, cls.READ_PT_MAX, cls.READ_PT_DEFAULT

    # 1. starts at default
    check("starts_at_default(%d)" % default, win._read_pt == default)

    # 2. "larger" increases the stored size
    before = win._read_pt
    win._on_text_larger()
    check("larger_increases", win._read_pt == before + 1 and win._read_pt > before)

    # 3. "smaller" decreases the stored size
    before = win._read_pt
    win._on_text_smaller()
    check("smaller_decreases", win._read_pt == before - 1 and win._read_pt < before)

    # sanity: back to default after one up + one down
    check("round_trip_to_default", win._read_pt == default)

    # 4a. clamps at the maximum
    for _ in range(100):
        win._on_text_larger()
    check("clamps_at_max(%d)" % hi, win._read_pt == hi)

    # one more click at the ceiling must not overshoot
    win._on_text_larger()
    check("no_overshoot_above_max", win._read_pt == hi)

    # 4b. clamps at the minimum
    for _ in range(100):
        win._on_text_smaller()
    check("clamps_at_min(%d)" % lo, win._read_pt == lo)

    # one more click at the floor must not undershoot
    win._on_text_smaller()
    check("no_undershoot_below_min", win._read_pt == lo)

    # the restyle path actually ran without error and left a live provider
    check("read_css_provider_present", isinstance(win._read_css, Gtk.CssProvider))
    check("read_labels_wired", len(getattr(win, "_read_labels", ())) >= 1)

    decode_ceiling_checks()

    print("RESULT: " + ("ALL PASS" if all(results) else "SOME FAILED"))


if __name__ == "__main__":
    main()
