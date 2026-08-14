#!/usr/bin/env python3
"""
Export must never destroy a file the user did not name in this act.

Every PDF export in this OS writes to a DETERMINISTIC path under Documents --
`timetable.pdf`, `journal-<today>.pdf`, a slug of the recipe title, the book's
own title. Exporting twice is not an edge case, it is the normal way these apps
are used: you export, you notice a typo, you fix it and export again. Until this
check existed, four of the five apps overwrote the earlier file with no dialog,
no status line and no trace -- and because there is no network and no cloud, the
file under $NB_HOME was the only copy. The Video Editor had the guard; novel,
journal, cookbook and academics did not.

This is an execution test, not an inspection: it constructs the real app, plants
a decoy at the real destination, calls the real export method, and reads the
bytes back off disk. Both directions are asserted, because a guard that can only
say no is a dead end rather than a fix:

  DECLINE  the confirm is raised, and the decoy survives byte-for-byte
  ACCEPT   the confirm's action runs, and a real PDF replaces the decoy

Asserting the confirm was *raised* is what keeps this honest. A test that simply
patched the dialog away and checked the file would pass just as well against an
app that never asks -- it would be measuring its own mock.

Run under the guest environment so the apps construct against the shipped theme:
    tools/guestrun.sh python3 tools/export_overwrite_selftest.py
    tools/guestrun.sh python3 tools/export_overwrite_selftest.py --de DIR

`--de` points the run at a different copy of the app tree, so the red-proof can
reinstate the bug on a scratch copy instead of the shipped one.
"""
import os
import sys
import time
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])

# NB_HOME must be set before the app modules are imported: each computes its
# DOCS_DIR at module level, so a later change would be ignored and the test
# would write into the developer's real Documents folder.
_HOME = tempfile.mkdtemp(prefix="nb-exportguard-")
os.environ["NB_HOME"] = _HOME
os.makedirs(os.path.join(_HOME, "Documents"), exist_ok=True)
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

DOCS = os.path.join(_HOME, "Documents")
DECOY = b"%PDF-1.4 the user's earlier export -- must survive\n"

FAILURES = []
CHECKS = [0]


def check(ok, label):
    CHECKS[0] += 1
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILURES.append(label)


def pump(n=200):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


def settle(app, n=400):
    """Drain the GTK queue AND wait for an export that runs OFF the thread.

    Journal's export moved onto an nbjobs worker, and this gate then reported
    it broken — "accepting replaces it with a real PDF (51 bytes)", the 51
    being the decoy still sitting there. The app was fine: measured with a
    proper wait it writes a 12,008-byte PDF. pump() only drains events that
    are ALREADY pending, so it returned before the worker had written
    anything, and the gate blamed the app for its own assumption that export
    is synchronous.

    Joining the thread is necessary and NOT sufficient: nbjobs hands results
    back through the GLib main loop, so the delivery has to be pumped too.
    A genuinely broken export still fails — the join returns, the pump finds
    no file, and the checks below report it."""
    jobs = getattr(app, "jobs", None)
    job = jobs.job("export") if jobs is not None and hasattr(jobs, "job") else None
    if job is not None:
        job.join(60)
    ctx = GLib.MainContext.default()
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        ctx.iteration(False)


# --------------------------------------------------------------------------
# Per-app adapters. Each says how to give the app something worth exporting,
# what filename that export lands on, and how to trigger it.
# --------------------------------------------------------------------------

def seed_journal(app):
    app.entries = [app._new_entry_dict("Rain all morning. The roof held.")]
    app.active = 0
    return "journal-" + time.strftime("%Y-%m-%d") + ".pdf"


def seed_cookbook(app):
    r = app._make_recipe()
    r["title"] = "Export Guard Loaf"
    app.recipes = [r]
    app.sel = 0
    return app._pdf_name(r)


def seed_academics(app):
    app.homework = [{"title": "Problem set 3", "cls": 0, "due": "2026-09-01",
                     "done": False, "notes": ""}]
    app.view = "homework"
    return "homework.pdf"


def seed_novel(app):
    app._title = "Export Guard"
    return "Export Guard.pdf"


APPS = [
    # module, class, seed, export call, confirm style
    ("journal",   "Journal",   seed_journal,
     lambda a, n: a._export_pdf(),            "callback"),
    ("cookbook",  "Cookbook",  seed_cookbook,
     lambda a, n: a._export_pdf(),            "callback"),
    ("academics", "Academics", seed_academics,
     lambda a, n: a._export_pdf(),            "modal"),
    ("novel",     "Novel",     seed_novel,
     lambda a, n: a._commit_export_pdf(n),    "callback"),
]


def build(module, clsname):
    import importlib
    mod = importlib.import_module(module)
    app = getattr(mod, clsname)()
    pump()
    return app


def run_one(module, clsname, seed, export, style):
    print("\n-- %s" % module)
    try:
        app = build(module, clsname)
    except Exception as exc:
        check(False, "%s: constructs (%s: %s)" % (module, type(exc).__name__, exc))
        return
    try:
        name = seed(app)
    except Exception as exc:
        check(False, "%s: seeds content (%s: %s)"
              % (module, type(exc).__name__, exc))
        return

    dest = os.path.join(DOCS, name)
    calls = []

    # ---- DECLINE: the guard is raised and nothing is written -------------
    with open(dest, "wb") as fh:
        fh.write(DECOY)

    if style == "callback":
        def confirm(title, message, ok_label, on_yes):
            calls.append((title, message, ok_label, on_yes))
    else:
        def confirm(heading, detail, ok_label="Delete"):
            calls.append((heading, detail, ok_label, None))
            return False
    app._confirm = confirm

    try:
        export(app, name)
        pump()
    except Exception as exc:
        check(False, "%s: export runs (%s: %s)"
              % (module, type(exc).__name__, exc))
        return

    check(len(calls) == 1,
          "%s: exporting onto an existing file asks first (%d asked)"
          % (module, len(calls)))
    on_disk = open(dest, "rb").read() if os.path.exists(dest) else b""
    check(on_disk == DECOY,
          "%s: declining leaves the earlier file byte-for-byte intact"
          % module)
    if calls:
        title, message, ok_label, _cb = calls[0]
        check(name in message,
              "%s: the question names the file it would replace" % module)
        check("Replace" in ok_label or "replace" in ok_label,
              "%s: the confirming button says what it does (%r)"
              % (module, ok_label))

    # ---- ACCEPT: the export really happens -------------------------------
    if not calls:
        return
    if style == "callback":
        calls[0][3]()          # run the on_yes the app handed the dialog
    else:
        app._confirm = lambda *a, **k: True
        try:
            export(app, name)
        except Exception as exc:
            check(False, "%s: accepting exports (%s: %s)"
                  % (module, type(exc).__name__, exc))
            return
    settle(app)

    out = open(dest, "rb").read() if os.path.exists(dest) else b""
    check(out.startswith(b"%PDF") and out != DECOY,
          "%s: accepting replaces it with a real PDF (%d bytes)"
          % (module, len(out)))

    # ---- A first export must NOT ask -------------------------------------
    fresh = os.path.join(DOCS, "unwritten-" + name)
    if os.path.exists(fresh):
        os.remove(fresh)
    calls.clear()
    os.remove(dest)
    if style == "callback":
        app._confirm = lambda *a: calls.append(a)
    else:
        app._confirm = lambda *a, **k: (calls.append(a), False)[1]
    try:
        export(app, name)
        settle(app)
    except Exception as exc:
        check(False, "%s: first export runs (%s: %s)"
              % (module, type(exc).__name__, exc))
        return
    check(not calls,
          "%s: exporting to a name nothing occupies asks nothing" % module)
    check(os.path.exists(dest),
          "%s: that first export actually wrote the file" % module)

    try:
        app.destroy()
    except Exception:
        pass


def main():
    print("export-overwrite guard — Documents at %s" % DOCS)
    for spec in APPS:
        run_one(*spec)

    print("\n%d checks, %d passed, %d FAILED"
          % (CHECKS[0], CHECKS[0] - len(FAILURES), len(FAILURES)))
    if FAILURES:
        print("RESULT: FAILED")
        for f in FAILURES:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
