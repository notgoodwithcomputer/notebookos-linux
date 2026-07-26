#!/usr/bin/env python3
"""
Headless selftest for the Finder-style file picker (de/nbpicker.py).

nbpicker is the OS-wide Open/Save dialog every app calls instead of building a
Gtk.FileChooserDialog. It is a hard thing to test because run() enters a modal
nested main loop and blocks — so nothing in the suite covered it, even though it
is on the critical path of every File > Open and File > Save. This drives the
REAL run() from a GLib idle callback that cancels the dialog once it is built,
so the actual construction path is exercised: the Gtk.Overlay wrapper, show_all,
_apply_view and _load all run exactly as they do in production.

The two things most likely to break here, and why:

  * The Save dialog wraps its content in a Gtk.Overlay purely so the
    press-and-hold accent palette (nbdiacritics) can draw INSIDE the dialog
    rather than in a second toplevel that would not paint over a modal on this
    no-compositor stack. A layout regression there is invisible until someone
    tries to type an accented file name — so this test types one.

  * A file list that fails to load through that overlay, or a suggested name
    that does not survive to the Name box.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  python3 nbpicker_selftest.py
"""
import os
import sys
import tempfile

os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbpick-home-")
_HOME = os.environ["NB_HOME"]
# Accented names are both realistic content and a check that the file list and
# the Name box render the very characters the accent palette exists to type.
open(os.path.join(_HOME, "Café notes.txt"), "w").close()
open(os.path.join(_HOME, "plain.txt"), "w").close()
os.makedirs(os.path.join(_HOME, "Álbumes"), exist_ok=True)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import nbapp        # noqa: E402
import nbpicker     # noqa: E402
import nbdiacritics  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %-46s %s%s" % (name, "PASS" if cond else "FAIL",
                            "" if cond or not detail else "  <- " + str(detail)))


def key(keyval, string="", state=0):
    return type("Ev", (), {"keyval": keyval, "string": string,
                           "state": Gdk.ModifierType(state)})()


def drive(picker, in_loop):
    """Run picker.run() but, once the modal loop is up and self.dlg exists,
    call `in_loop(dlg)` and cancel so run() unwinds and returns."""
    def tick():
        dlg = getattr(picker, "dlg", None)
        if dlg is None:
            return True                  # not built yet — keep waiting
        try:
            in_loop(dlg)
        finally:
            picker._cancel()             # response(CANCEL) -> run() returns
        return False
    GLib.timeout_add(30, tick)
    return picker.run()


def test_save_dialog():
    print("\nSAVE dialog — overlay, file list, accent palette end to end")
    nbdiacritics.HOLD_MS = 30
    parent = nbapp.AppWindow(); parent.realize()
    picker = nbpicker._Picker(parent, "save", "Save As", None,
                              "café draft", None, ".txt")
    seen = {}

    def in_loop(dlg):
        seen["overlay"] = getattr(dlg, "_overlay", None) is not None
        seen["diacritics"] = getattr(picker, "_diacritics", None) is not None
        seen["suggested"] = picker.name_entry.get_text()
        # the file list must have loaded through the overlay layout
        model = picker.store
        seen["rows"] = len(model)
        names = [model[i][model.get_n_columns() - 1] if False else None
                 for i in range(len(model))]
        seen["loaded_home"] = len(model) >= 3     # 2 files + 1 folder at least
        # end to end: hold "e" in the Name box; the palette must mount into the
        # dialog's overlay and a pick must replace the letter.
        e = picker.name_entry
        dlg.set_focus(e)
        e.set_text("caf"); e.set_position(3)
        d = picker._diacritics
        d._on_press(dlg, key(Gdk.KEY_e, "e"))
        e.set_text("cafe"); e.set_position(4)
        t0 = GLib.get_monotonic_time()
        while not d._open and GLib.get_monotonic_time() - t0 < 300000:
            Gtk.main_iteration_do(False)
        seen["palette_open"] = bool(d._open and d._layer is not None)
        d._on_press(dlg, key(Gdk.KEY_1, "1"))
        seen["committed"] = e.get_text()

    res = drive(picker, in_loop)
    parent.destroy()

    check("dialog wraps content in an overlay", seen.get("overlay"))
    check("accent palette installed on the dialog", seen.get("diacritics"))
    check("suggested name reaches the Name box", seen.get("suggested") == "café draft",
          seen.get("suggested"))
    check("home directory listed through the overlay", seen.get("loaded_home"),
          "rows=%r" % seen.get("rows"))
    check("accent palette opens INSIDE the modal dialog", seen.get("palette_open"))
    check("holding e commits e-acute into the name", seen.get("committed") == "café",
          seen.get("committed"))
    check("cancel returns None", res is None)


def test_open_dialog():
    print("\nOPEN dialog — constructs and lists files via the real modal path")
    parent = nbapp.AppWindow(); parent.realize()
    picker = nbpicker._Picker(parent, "open", "Open", None, "", ["*.txt"], None)
    seen = {}

    def in_loop(dlg):
        seen["overlay"] = getattr(dlg, "_overlay", None) is not None
        seen["rows"] = len(picker.store)
        # a *.txt filter still shows the folder (folders always show) + 2 .txt
        seen["has_rows"] = len(picker.store) >= 3

    res = drive(picker, in_loop)
    parent.destroy()
    check("open dialog wraps content in an overlay", seen.get("overlay"))
    check("open dialog lists filtered files", seen.get("has_rows"),
          "rows=%r" % seen.get("rows"))
    check("cancel returns None", res is None)


def test_default_ext():
    print("\nSAVE — default extension is appended to a bare name")
    parent = nbapp.AppWindow(); parent.realize()
    picker = nbpicker._Picker(parent, "save", "Save As", None, "notes",
                              None, ".txt")

    def tick():
        dlg = getattr(picker, "dlg", None)
        if dlg is None:
            return True
        picker.name_entry.set_text("shopping")
        picker._commit_save()             # bare name -> +.txt -> _finish -> OK
        if picker._result is None:        # safety net if it didn't finish
            picker._cancel()
        return False

    GLib.timeout_add(30, tick)
    res = picker.run()
    parent.destroy()
    check("bare name gains the default extension",
          res is not None and res.endswith("shopping.txt"), res)


def main():
    print("=" * 74)
    print("nbpicker selftest — Finder-style Open/Save over the real modal loop")
    print("=" * 74)
    test_save_dialog()
    test_open_dialog()
    test_default_ext()
    print("\n" + "=" * 74)
    print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
