#!/usr/bin/env python3
"""
Headless selftest for the Save picker's REPLACE confirmation (de/nbpicker.py).

nbpicker is the shared Save dialog: Writer, Illustrator, the GBA SDK, the video
editor and everything else that writes a file goes through it. It therefore
carries the widest destructive path in the OS, and until this test existed that
path was guarded only by an ARMED BUTTON — typing the name of a file you already
have flashed a one-line hint in the footer ('"notes.txt" exists - Save again to
replace') and the very next press overwrote the file. There was no statement of
what was about to happen, no way to decline except noticing the hint in time,
and nothing anywhere that said it could not be undone.

What is checked here is behaviour, through the real modal loop, not the shape of
the code:

  1. A name that does not exist yet saves straight through, with no card in the
     way. (A confirmation that fires on every save is a confirmation nobody
     reads.)
  2. A name that DOES exist opens an explicit Replace card.
  3. Declining that card returns no path AND leaves the file on disk
     byte-for-byte as it was.
  4. Pressing Save a SECOND time asks AGAIN. This is the one that fails against
     the old arm-the-button behaviour, where the second press was the destroy.
  5. Choosing Replace does hand the path back, so the guard has not simply
     broken saving over an existing file.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  python3 nbpicker_replace_selftest.py
"""
import os
import sys
import tempfile

os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbreplace-home-"))
HOME = os.environ["NB_HOME"]

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

import nbapp        # noqa: E402
import nbpicker     # noqa: E402

PASS, FAIL = [], []

EXISTING = "Letter.txt"
ORIGINAL = "the original letter, which must survive a declined replace\n"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %-52s %s%s" % (name, "PASS" if cond else "FAIL",
                            "" if cond or not detail else "  <- " + str(detail)))


def seed():
    with open(os.path.join(HOME, EXISTING), "w", encoding="utf-8") as fh:
        fh.write(ORIGINAL)


def read_existing():
    with open(os.path.join(HOME, EXISTING), encoding="utf-8") as fh:
        return fh.read()


def answer_confirm(picker, response, log, tries=120):
    """Arm a one-shot answer for the NEXT replace card the picker opens.

    Appends the response it gave to `log`, or None if no card ever appeared —
    which is what the old arm-the-button behaviour produces, and is exactly the
    condition tests 2 and 4 fail on."""
    state = {"n": 0}

    def tick():
        dlg = getattr(picker, "_replace_dlg", None)
        if dlg is None:
            state["n"] += 1
            if state["n"] > tries:
                log.append(None)          # no confirmation was ever shown
                return False
            return True
        log.append(response)
        dlg.response(response)
        return False

    GLib.timeout_add(10, tick)


def no_card(log):
    """True when the picker never opened a replace card. The watcher may not
    get its `tries` before the modal loop unwinds, so an EMPTY log and a log
    holding only the give-up marker mean the same thing: nothing was asked."""
    return all(x is None for x in log)


def drive(suggested, script):
    """Open the real Save picker on $NB_HOME and run `script(picker)` inside the
    modal loop. Returns (result_path, picker)."""
    parent = nbapp.AppWindow()
    parent.realize()
    picker = nbpicker._Picker(parent, "save", "Save As", HOME, suggested,
                              None, None)

    def tick():
        if getattr(picker, "dlg", None) is None:
            return True                   # dialog not built yet
        try:
            script(picker)
        finally:
            if picker._result is None:
                picker._cancel()
        return False

    GLib.timeout_add(30, tick)
    res = picker.run()
    parent.destroy()
    return res, picker


def test_new_name_needs_no_confirmation():
    print("\nA NEW name saves straight through — no card in the way")
    seed()
    log = []

    def script(p):
        p.name_entry.set_text("a name nobody has used.txt")
        answer_confirm(p, Gtk.ResponseType.OK, log, tries=8)
        p._commit_save()

    res, _p = drive("untitled.txt", script)
    check("a new name returns its path", res is not None
          and res.endswith("a name nobody has used.txt"), res)
    check("no replace card was shown for a new name", no_card(log), log)


def test_existing_name_asks_and_declining_changes_nothing():
    print("\nAn EXISTING name asks first, and Cancel leaves the file alone")
    seed()
    log = []

    def script(p):
        p.name_entry.set_text(EXISTING)
        answer_confirm(p, Gtk.ResponseType.CANCEL, log)
        p._commit_save()                  # first Save press
        answer_confirm(p, Gtk.ResponseType.CANCEL, log)
        p._commit_save()                  # second Save press must ASK AGAIN

    res, _p = drive(EXISTING, script)

    check("an existing name shows a replace card", log[:1] == [Gtk.ResponseType.CANCEL],
          log)
    # THE regression guard. The old code armed the Save button: the first press
    # only flashed a hint and the SECOND press destroyed the file with no card
    # at all. Against that code this line reads log == [CANCEL-not-shown, None]
    # and fails.
    check("a SECOND Save press asks again, it does not just do it",
          log == [Gtk.ResponseType.CANCEL, Gtk.ResponseType.CANCEL], log)
    check("declining returns no path", res is None, res)
    check("declining leaves the file byte-for-byte unchanged",
          read_existing() == ORIGINAL, repr(read_existing()[:40]))


def test_replace_still_works():
    print("\nChoosing Replace still hands the path back")
    seed()
    log = []

    def script(p):
        p.name_entry.set_text(EXISTING)
        answer_confirm(p, Gtk.ResponseType.OK, log)
        p._commit_save()

    res, _p = drive(EXISTING, script)
    check("a card was shown before replacing", log == [Gtk.ResponseType.OK], log)
    check("accepting returns the existing path",
          res == os.path.join(HOME, EXISTING), res)
    # nbpicker only chooses the path; the CALLER writes. Nothing here should
    # have touched the file yet.
    check("the picker itself wrote nothing", read_existing() == ORIGINAL)


def test_folder_is_never_offered_as_replaceable():
    print("\nA folder of the same name is refused, not offered for replacing")
    os.makedirs(os.path.join(HOME, "Reports"), exist_ok=True)
    log = []

    def script(p):
        p.name_entry.set_text("Reports")
        answer_confirm(p, Gtk.ResponseType.OK, log, tries=8)
        p._commit_save()

    res, picker = drive("Reports", script)
    check("saving onto a folder returns no path", res is None, res)
    check("no replace card is offered for a folder", no_card(log), log)


def main():
    print("=" * 78)
    print("nbpicker replace-confirmation selftest — the OS-wide overwrite guard")
    print("=" * 78)
    test_new_name_needs_no_confirmation()
    test_existing_name_asks_and_declining_changes_nothing()
    test_replace_still_works()
    test_folder_is_never_offered_as_replaceable()
    print("\n" + "=" * 78)
    print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
