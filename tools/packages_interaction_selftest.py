#!/usr/bin/env python3
"""
Searching, sorting and walking the package list.

`tools/func_coverage.py` reported 13 of packages.py's 39 functions never
entered by any suite, and all thirteen were the interaction handlers: search,
sort, sidebar navigation, row selection, keyboard walking, Open, and the menu.
`packages_selftest` covers the enumeration; nothing covered what a person does
to it.

Everything here goes through the real handlers on a real window. The list is
enumerated live from the desktop image on disk, so the fixtures are the actual
shipped packages rather than seeded rows.

Run:
    tools/guestrun.sh python3 tools/packages_interaction_selftest.py
    tools/guestrun.sh python3 tools/packages_interaction_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-pkg-")
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

import packages as P  # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(300):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


def labels(root, out=None):
    out = [] if out is None else out
    if isinstance(root, Gtk.Label):
        out.append(" ".join(root.get_text().split()))
    if isinstance(root, Gtk.Container):
        for k in root.get_children():
            labels(k, out)
    return out


def key(app, keyval, widget=None):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = keyval
    ev.state = Gdk.ModifierType(0)
    ev.string = ""
    ev.window = app.get_window()
    return ev


def search(app, text):
    app.entry.set_text(text)      # fires "changed" -> _on_search
    pump()


def main():
    app = P.Packages()
    pump()
    total = len(P.PACKAGES)
    check("the image enumerates packages", total > 5, "%d packages" % total)

    # ---- search reaches all three fields it claims to ---------------------
    # "Search matches the display name, the kind, and the module filename."
    pkg = next((p for p in P.PACKAGES if p[P.KIND] == "Application"), None)
    got = check("there is an application to search for", pkg is not None)
    if not got:
        not_reached("no application package", "search finds a package by name",
                    "...by kind", "...by module filename")
        return 1
    name, mod = pkg[P.NAME], os.path.basename(pkg[P.PATH])[:-3]

    search(app, name)
    check("search finds a package by name", app.sel is not None
          and P.PACKAGES[app.sel][P.NAME] == name,
          repr(name))
    search(app, "application")
    check("...by kind", len(app._visible_order) > 1,
          "%d rows" % len(app._visible_order))
    search(app, mod)
    check("...by module filename", any(
        os.path.basename(P.PACKAGES[i][P.PATH])[:-3] == mod
        for i in app._visible_order), repr(mod))

    # ---- a search that matches nothing is honest about it -----------------
    search(app, "zzzz-no-such-package")
    check("a search with no matches clears the selection", app.sel is None,
          repr(app.sel))
    said = " ".join(labels(app.detail))
    check("...and the inspector says so rather than going blank",
          "no package selected" in said.lower(), repr(said[:60]))
    listed = " ".join(labels(app.listbox))
    check("...and the empty list says WHICH kind of empty it is",
          "match" in listed.lower(), repr(listed[:60]))

    # ---- Esc clears a search, and only a non-empty one --------------------
    search(app, name)
    handled = app._on_entry_key(app.entry, key(app, Gdk.KEY_Escape))
    pump()
    check("Esc clears a non-empty search",
          handled is True and app.entry.get_text() == "",
          "handled=%r text=%r" % (handled, app.entry.get_text()))
    handled = app._on_entry_key(app.entry, key(app, Gdk.KEY_Escape))
    check("...and falls through on an empty one, so Esc still leaves",
          handled is False, repr(handled))

    # ---- sorting orders on the real values, not the formatted strings -----
    search(app, "")
    for field, col in (("name", P.NAME), ("size", P.SIZE_B),
                       ("modified", P.MTIME), ("kind", P.KIND)):
        app.sort_field, app.sort_desc = None, False
        app._on_sort(field)
        pump()
        vals = [P.PACKAGES[i][col] for i in app._visible_order]
        if col in (P.SIZE_B, P.MTIME):
            ok = all(vals[k] <= vals[k + 1] for k in range(len(vals) - 1))
        else:
            low = [str(v).lower() for v in vals]
            ok = all(low[k] <= low[k + 1] for k in range(len(low) - 1))
        check("sorting by %s ascends" % field, ok, repr(vals[:4]))
        app._on_sort(field)              # same field again reverses
        pump()
        rvals = [P.PACKAGES[i][col] for i in app._visible_order]
        check("...and clicking %s again reverses it" % field,
              rvals == list(reversed(vals)), repr(rvals[:4]))

    # ---- the keyboard walks the list AS DISPLAYED -------------------------
    app.sort_field, app.sort_desc = "name", True     # not the enumeration order
    app._on_sort("name")
    app._on_sort("name")
    pump()
    order = list(app._visible_order)
    walked = check("there is a sorted list to walk", len(order) > 2,
                   "%d rows" % len(order))
    if walked:
        app._select_row(order[0])
        app._on_row_key(None, key(app, Gdk.KEY_Down), order[0])
        check("Down moves to the next row SHOWN", app.sel == order[1],
              "%r wanted %r" % (app.sel, order[1]))
        app._on_row_key(None, key(app, Gdk.KEY_End), app.sel)
        check("End goes to the last row shown", app.sel == order[-1],
              "%r wanted %r" % (app.sel, order[-1]))
        app._on_row_key(None, key(app, Gdk.KEY_Home), app.sel)
        check("Home goes to the first row shown", app.sel == order[0])
        app._on_row_key(None, key(app, Gdk.KEY_Up), app.sel)
        check("Up at the top stays put rather than wrapping",
              app.sel == order[0], repr(app.sel))
    else:
        not_reached("nothing to walk", "Down moves to the next row SHOWN",
                    "End goes to the last row shown",
                    "Home goes to the first row shown",
                    "Up at the top stays put rather than wrapping")

    # ---- a search that hides the selection lands on the first row SHOWN ---
    # The comment says "fall back to the first visible row". With a sort
    # applied, the first visible row is not the first package in enumeration
    # order, and the inspector is what tells the user which one they have.
    query, hidden = None, None
    if walked:
        # A query that matches several packages and NOT the one selected, so
        # the fallback is forced and there is more than one row to land on.
        for cand in ("s", "e", "i", "o", "n", "r", "t"):
            match = [i for i, pk in enumerate(P.PACKAGES)
                     if app._matches(pk, cand)]
            outside = [i for i in order if i not in match]
            if len(match) > 2 and outside:
                query, hidden = cand, outside[-1]
                break
    if query is None:
        not_reached("no query hides the selection",
                    "after a search hides the selection, it lands on the "
                    "first row SHOWN")
    else:
        app.sort_field, app.sort_desc = "name", True
        app._on_sort("name")
        app._on_sort("name")
        pump()
        app._select_row(hidden)
        search(app, query)
        vis = list(app._visible_order)
        check("after a search hides the selection, it lands on the first "
              "row SHOWN", bool(vis) and app.sel == vis[0],
              "sel=%r (%s)  first shown=%r (%s)"
              % (app.sel, P.PACKAGES[app.sel][P.NAME] if app.sel is not None
                 else "-", vis[0] if vis else None,
                 P.PACKAGES[vis[0]][P.NAME] if vis else "-"))

    # ---- the sidebar and the menu ----------------------------------------
    search(app, "")
    for vid in ("updates", "sources", "installed"):
        app._on_nav(None, vid)
        pump()
        check("the %s view opens" % vid, app.view == vid, repr(app.view))

    items = dict((i[0], i[1]) for i in app.menu_items("Package")
                 if isinstance(i, tuple))
    check("the Package menu offers Open, Verify and Find",
          # "Find", not "Find…": _focus_search raises nothing to answer — it
          # shows Installed and puts the caret in the search box already on
          # screen — and nbcommands registers edit.find without an ellipsis.
          # The ellipsis promised a card that never came.
          all(k in items for k in ("Open", "Verify Package", "Find")),
          repr(sorted(items)))
    check("Clear Search is greyed out with no search",
          items.get("Clear Search") is None)
    search(app, name)
    items = dict((i[0], i[1]) for i in app.menu_items("Package")
                 if isinstance(i, tuple))
    check("...and live once there is one", items.get("Clear Search") is not None)

    # Open is offered for an application and not for a system component.
    sysp = next((i for i, p in enumerate(P.PACKAGES)
                 if p[P.KIND] != "Application"), None)
    if sysp is not None:
        search(app, "")
        app._select_row(sysp)
        system_detail = labels(app.detail)
        check("a system component has no uninstall or restore affordance",
              "Uninstall" not in system_detail and "Restore" not in system_detail,
              repr(system_detail))
        items = dict((i[0], i[1]) for i in app.menu_items("Package")
                     if isinstance(i, tuple))
        check("Open is greyed out for a system component, not a dead stub",
              items.get("Open") is None, P.PACKAGES[sysp][P.NAME])

    appi = next((i for i, p in enumerate(P.PACKAGES)
                 if p[P.KIND] == "Application"), None)
    if appi is not None:
        app._select_row(appi)
        check("an installed app offers Uninstall in its detail pane",
              "Uninstall" in labels(app.detail), repr(labels(app.detail)))

    # ---- clicking a row selects it ----------------------------------------
    search(app, "")
    order = list(app._visible_order)
    if len(order) > 1:
        app._select_row(order[0])
        app._on_select(None, order[1])
        check("clicking a row selects it", app.sel == order[1],
              "%r wanted %r" % (app.sel, order[1]))
    else:
        not_reached("fewer than two rows", "clicking a row selects it")

    # ---- Open starts the app the same way the Finder does ------------------
    # The comment claims parity with finder._launch_module. Both must be
    # `python3 <module path>` with PYTHONPATH pointing at the desktop
    # directory: an app started with a different environment is an app that
    # behaves differently, and this image has already lost GStreamer's plugin
    # registry once to a missing variable.
    appi = next((i for i, pk in enumerate(P.PACKAGES)
                 if pk[P.KIND] == "Application"), None)
    if appi is None:
        not_reached("no application", "Open spawns the module",
                    "...with PYTHONPATH set to the desktop directory")
    else:
        app._select_row(appi)
        spawned = []
        real_popen = P.subprocess.Popen

        class _Fake(object):
            def __init__(self, argv, env=None, **kw):
                spawned.append((list(argv), dict(env or {})))

        # Clear PYTHONPATH from the PARENT first. Without this the child
        # inherits the harness's copy and the check passes whether or not
        # _on_open sets it — which is exactly what production looks like,
        # since session.sh exports it. The contract worth holding is that the
        # app sets it itself, so it launches correctly from anywhere.
        keep = os.environ.pop("PYTHONPATH", None)
        P.subprocess.Popen = _Fake      # NB: the same module object as ours
        # Open first asks nbtrust whether the module is signed, and nbtrust
        # verifies through `openssl` — via the SAME subprocess module, so the
        # fake above would answer the signature check too and every launch
        # would be refused. The trust gate has its own suite
        # (app_trust_selftest); here it is held open so the LAUNCH is what
        # is measured.
        real_check = getattr(P.nbtrust, "check_path", None) if P.nbtrust else None
        if real_check is not None:
            P.nbtrust.check_path = lambda _path: (True, "")
        try:
            app._on_open()
            pump()
        finally:
            P.subprocess.Popen = real_popen
            if real_check is not None:
                P.nbtrust.check_path = real_check
            if keep is not None:
                os.environ["PYTHONPATH"] = keep
        ran = check("Open spawns the module", len(spawned) == 1
                    and spawned[0][0][:1] == ["python3"]
                    and spawned[0][0][1].endswith(".py"),
                    repr(spawned[:1])[:90])
        if ran:
            argv, env = spawned[0]
            check("...with PYTHONPATH set to the desktop directory",
                  env.get("PYTHONPATH") == P.DE_DIR,
                  repr(env.get("PYTHONPATH")))
            check("...pointing at the selected package",
                  argv[1] == P.PACKAGES[appi][P.PATH], repr(argv[1]))
        else:
            not_reached("nothing spawned",
                        "...with PYTHONPATH set to the desktop directory",
                        "...pointing at the selected package")

    # ---- Find… puts the cursor in the box, from any view ------------------
    app._on_nav(None, "sources")
    pump()
    app._focus_search()
    pump()
    check("Find returns to Installed and focuses the box",
          app.view == "installed" and app.entry.is_focus(),
          "view=%r focus=%s" % (app.view, app.entry.is_focus()))
    app._clear_search()
    pump()
    check("Clear Search empties it", app.entry.get_text() == "")

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
    gtk_ready = Gtk.init_check()[0]
    if gtk_ready:
        rc = main()
    else:
        print("SKIP: GTK3 display is unavailable; interaction section not run")
        rc = 0
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
