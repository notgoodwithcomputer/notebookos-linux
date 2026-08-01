#!/usr/bin/env python3
"""JOB 2 proof: drive the two combo-readback controls under a NON-ENGLISH
language and check the branch still takes the right path.

Run:  DISPLAY=:0 python3 job2_proof.py [lang ...]

Nothing here reads the source. It builds the REAL app window, opens the REAL
Gtk.Dialog (Gtk.Dialog.run is stubbed so the modal loop returns immediately),
picks a row in the combo the way a user would, and then inspects the value the
app STORED.
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = "/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/" \
     "rootfs-overlay/opt/notebook/de"

LANGS = sys.argv[1:] or ["ru", "es", "pl", "sr", "zh", "hi"]


def run_lang(code):
    home = tempfile.mkdtemp(prefix="nbproof-%s-" % code)
    os.makedirs(os.path.join(home, ".config", "notebook"), exist_ok=True)
    env = dict(os.environ, NB_LANG=code, NB_HOME=home, PYTHONPATH=DE)
    import subprocess
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--child",
                        code], env=env, capture_output=True, text=True)
    shutil.rmtree(home, ignore_errors=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.stderr.write(r.stderr[-2500:])
    return r.returncode


def child(code):
    sys.path.insert(0, DE)
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import nbi18n

    assert nbi18n.lang() == code, "catalog did not load: %r" % nbi18n.lang()
    fails = []

    def check(name, got, want):
        ok = got == want
        print("   %-34s %-9s got=%r want=%r"
              % (name, "ALL PASS" if ok else "SOME FAILED", got, want))
        if not ok:
            fails.append(name)

    # ---- what the combo actually SHOWS in this language (proves the setup
    #      really is translated, i.e. the test can fail) --------------------
    probe = Gtk.ComboBoxText()
    probe.append_text("Letter")
    probe.append_text("Personal")
    probe.set_active(0)
    shown_letter = probe.get_active_text()
    probe.set_active(1)
    shown_personal = probe.get_active_text()
    print("[%s] combo renders 'Letter'->%r  'Personal'->%r"
          % (code, shown_letter, shown_personal))

    # =====================================================================
    #  A. Writer -> Format -> Page setup -> Size
    # =====================================================================
    import writer
    w = writer.Writer()

    orig_run = Gtk.Dialog.run

    def pick_size(dlg, idx):
        combos = []
        walk(dlg.get_content_area(), Gtk.ComboBoxText, combos)
        combos[0].set_active(idx)          # size_c is the first combo
        return Gtk.ResponseType.OK

    Gtk.Dialog.run = lambda dlg: pick_size(dlg, 0)      # row 0 == "Letter"
    w._page_setup()
    check("writer page size stored", w._page.get("size"), "Letter")
    # reopening must not explode: the app does list(PAGE_SIZES).index(stored)
    try:
        Gtk.Dialog.run = lambda dlg: Gtk.ResponseType.CANCEL
        w._page_setup()
        print("   %-34s %-9s (dialog reopened)" % ("writer page setup reopen", "PASS"))
    except Exception as e:
        print("   %-34s %-9s %s: %s" % ("writer page setup reopen", "FAIL",
                                        type(e).__name__, e))
        fails.append("writer page setup reopen")
    # and the geometry the stored value resolves to must be Letter, not the
    # silent PAGE_SIZES fallback
    check("writer page geometry", writer.PAGE_SIZES.get(w._page.get("size")),
          writer.PAGE_SIZES["Letter"])
    Gtk.Dialog.run = orig_run
    w.destroy()

    # =====================================================================
    #  B. Calendar -> new event -> Calendar picker
    # =====================================================================
    import calendar as cal_mod
    c = cal_mod.Calendar()
    before = len(c.events)
    cal_names = c._cal_names()
    print("[%s] calendars defined: %r" % (code, cal_names))

    def fill_event(dlg):
        entries, combos = [], []
        walk(dlg.get_content_area(), Gtk.Entry, entries)
        walk(dlg.get_content_area(), Gtk.ComboBoxText, combos)
        entries[0].set_text("Proof event")
        combos[0].set_active(2)        # start 09:00
        combos[1].set_active(1)        # duration "1 hour"
        combos[2].set_active(0)        # calendar picker -> first calendar
        combos[3].set_active(0)        # repeats -> none
        return Gtk.ResponseType.OK

    Gtk.Dialog.run = fill_event
    c._event_dialog(None)
    Gtk.Dialog.run = orig_run
    if len(c.events) != before + 1:
        print("   %-34s %-9s (no event was created)"
              % ("calendar event created", "FAIL"))
        fails.append("calendar event created")
    else:
        ev = c.events[-1]
        check("calendar event .cal stored", ev.get("cal"), cal_names[0])
        # the real consequence: does the event belong to a DEFINED calendar?
        check("calendar event is in a real cal", ev.get("cal") in cal_names,
              True)
    c.destroy()

    print("[%s] %s" % (code, "ALL PASS" if not fails
                       else "FAILURES: " + ", ".join(fails)))
    return 1 if fails else 0


def walk(w, kind, out, depth=0):
    import gi
    from gi.repository import Gtk
    if depth > 40:
        return
    if isinstance(w, kind):
        out.append(w)
    if isinstance(w, Gtk.Container):
        for ch in w.get_children():
            walk(ch, kind, out, depth + 1)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        sys.exit(child(sys.argv[2]))
    rc = 0
    for lg in LANGS:
        rc |= run_lang(lg)
    print("\nRESULT: %s" % ("PASS" if not rc else "FAIL"))
    sys.exit(rc)
