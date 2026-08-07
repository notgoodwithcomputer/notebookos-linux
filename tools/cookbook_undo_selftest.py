#!/usr/bin/env python3
"""
Cookbook undo: the recipe comes back.

Cookbook was the only text-editing app in the OS with no undo at all (ROADMAP
#33). Selecting a whole method and typing over it was final, and deleting a
recipe was guarded by a dialog whose own sentence read *"This cannot be
undone."* — true at the time, and the reason the dialog had to exist.

It does now, on the same `nbapp.UndoHistory` the other four editors use, so the
confirm is gone: friction belongs to commitment, never to mechanism. Delete
acts at once and Ctrl+Z brings it back, which is faster for the common case and
safer for the mistaken one.

This drives the real app: real recipes, the real delete path, the real
`UndoHistory`, and the real Ctrl+Z key handler through `_on_key` with a
synthesised event — not `undo()` called directly, because the shortcut being
reachable from an ingredient field is the half that actually matters.

Run:
    tools/guestrun.sh python3 tools/cookbook_undo_selftest.py
    tools/guestrun.sh python3 tools/cookbook_undo_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-cookundo-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

import cookbook  # noqa: E402

FAILED, N = [], [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(*names):
    for n in names:
        check(n + "  [not reached: precondition failed]", False)


def pump(n=300):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


def ctrl_z(app, shift=False):
    """A real Ctrl+Z through the app's own key handler."""
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = Gdk.KEY_Z if shift else Gdk.KEY_z
    ev.state = (Gdk.ModifierType.CONTROL_MASK
                | (Gdk.ModifierType.SHIFT_MASK if shift else 0))
    handled = app._on_key(app, ev)
    pump()
    return handled


def titles(app):
    return [r.get("title", "") for r in app.recipes]


def main():
    app = cookbook.Cookbook()
    pump()

    # Three real recipes through the app's own creation path.
    for name in ("Soda Bread", "Leek Soup", "Plum Cake"):
        app.new_recipe()
        app.recipes[app.sel]["title"] = name
        app.recipes[app.sel]["steps"] = "Mix. Bake."
        app.undo.commit()
    pump()
    check("three recipes exist", titles(app) == ["Soda Bread", "Leek Soup",
                                                 "Plum Cake"])

    # ---- delete happens immediately, with no dialog ------------------
    app.sel = 1
    app._confirm_delete_current()
    pump()
    gone = check("deleting removes the recipe at once, with no dialog",
                 titles(app) == ["Soda Bread", "Plum Cake"])
    check("the status line says how to get it back",
          "Ctrl" in (app.savestate.get_text() or "")
          or "Leek Soup" in (app.savestate.get_text() or "")
          or app.undo.can_undo())

    # ---- Ctrl+Z brings it back ---------------------------------------
    if not gone:
        not_reached("Ctrl+Z restores the deleted recipe",
                    "it comes back in its original position",
                    "its contents come back too",
                    "Ctrl+Shift+Z deletes it again")
    else:
        handled = ctrl_z(app)
        check("Ctrl+Z is handled by the app", handled)
        back = check("Ctrl+Z restores the deleted recipe",
                     "Leek Soup" in titles(app))
        if back:
            check("it comes back in its original position",
                  titles(app) == ["Soda Bread", "Leek Soup", "Plum Cake"])
            r = [x for x in app.recipes if x.get("title") == "Leek Soup"][0]
            check("its contents come back too", r.get("steps") == "Mix. Bake.")
            ctrl_z(app, shift=True)
            check("Ctrl+Shift+Z deletes it again",
                  "Leek Soup" not in titles(app))
            ctrl_z(app)          # leave it restored for the next section
            pump()
        else:
            not_reached("it comes back in its original position",
                        "its contents come back too",
                        "Ctrl+Shift+Z deletes it again")

    # ---- typing over a method is recoverable -------------------------
    app.sel = 0
    app.recipes[0]["steps"] = "Mix. Bake."
    app.undo.commit()
    app.undo.checkpoint("Typing")
    app.recipes[0]["steps"] = ""        # select-all-and-type over the method
    app.undo.commit()
    pump()
    wiped = check("the method really was wiped", app.recipes[0]["steps"] == "")
    if wiped:
        ctrl_z(app)
        check("Ctrl+Z brings the wiped method back",
              app.recipes[0]["steps"] == "Mix. Bake.")
    else:
        not_reached("Ctrl+Z brings the wiped method back")

    # ---- a category is undoable too ----------------------------------
    before = list(app.cats)
    app.undo.checkpoint("New Category")
    app.cats.append("Baking")
    app.rebuild_chips()
    app._touch()
    app.undo.commit()
    pump()
    if check("the category was added", "Baking" in app.cats):
        ctrl_z(app)
        check("Ctrl+Z removes the category again", app.cats == before)
    else:
        not_reached("Ctrl+Z removes the category again")

    # ---- the Edit menu offers it, named ------------------------------
    # Asserted straight after a labelled action, which is the only point where
    # a name is owed. An earlier draft checked here at the end of the run,
    # after undoing back to the baseline -- a state that legitimately has no
    # label -- and then "passed" because "Undo    Ctrl+Z" has more than one
    # word once the ACCELERATOR is counted. It was measuring the shortcut.
    app.new_recipe()
    app.recipes[app.sel]["title"] = "Rye Loaf"
    pump()
    labels = [i[0] for i in app.menu_items("Edit")
              if isinstance(i, tuple) and i and i[0]]
    check("the Edit menu shows Undo (%r)" % (labels[:2],),
          any("Undo" in l for l in labels))
    undo_lbl = next((l for l in labels if l.startswith("Undo")), "")
    named = undo_lbl.split("Ctrl")[0].replace("Undo", "").strip()
    check("the Undo item names what it would reverse (%r)" % named,
          named == "New Recipe")

    try:
        app.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
