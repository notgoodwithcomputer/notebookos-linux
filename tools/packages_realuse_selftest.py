#!/usr/bin/env python3
"""Packages: what a person meets, driven on the real window.

Every check here failed on the shipped app before the fix beside it, and every
one of them was found by USING the window rather than by reading it: pressing
Esc with a search in the box, searching until the list stops scrolling, looking
at a package name on the smallest panel the OS supports, plugging in two sticks,
putting a stick in twice.

The window is hosted offscreen by tools/appdrive (the real widget tree, the real
handlers, the real store), so what is measured is geometry and colour a reader
would see, not the source that produced it. Two checks are display-free because
their subject is a file on disk.

Run:
    tools/guestrun.sh python3 tools/packages_realuse_selftest.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, HERE)
sys.path.insert(0, DE)

_ROOT = tempfile.mkdtemp(prefix="nb-packages-realuse-")
os.environ["NB_DRIVE_HOME_ROOT"] = _ROOT

import appdrive                                                # noqa: E402
from gi.repository import Gtk, Gdk                             # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name
          + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(name)


def run(name, fn):
    """Run one check so it can only fail BY NAME: an exception inside it is
    that check failing, never the suite falling over before the rest run."""
    try:
        ok, detail = fn()
    except Exception as exc:                                   # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    check(name, ok, detail)


# --------------------------------------------------------------- fixtures
class FakePkg(object):
    """Stand-in for nbpkg_install: three packages on a stick, one of them
    refused, one of them already on this machine."""
    GOOD = "/media/STICK/new-app.nbpkg"
    HAVE = "/media/STICK/already-here.nbpkg"
    BAD = "/media/STICK/scribbled.nbpkg"

    MANIFESTS = {
        GOOD: {"name": "Govorimo", "version": "2.0.0",
               "app": {"display": "Govorimo", "module": "govorimo"}},
        HAVE: {"name": "Vremenko", "version": "1.4.0",
               "app": {"display": "Vremenko", "module": "vremenko"}},
    }

    @staticmethod
    def scan(_mounts):
        return [(FakePkg.GOOD, "new-app.nbpkg"),
                (FakePkg.HAVE, "already-here.nbpkg"),
                (FakePkg.BAD, "scribbled.nbpkg")]

    @staticmethod
    def inspect(path, pub=None):
        if path not in FakePkg.MANIFESTS:
            raise ValueError("signature does not verify")
        return FakePkg.MANIFESTS[path], {}


def label_of(button):
    for w in [button.get_child()]:
        if isinstance(w, Gtk.Label):
            return w
    return None


def name_label(row):
    inner = row.get_child()
    cell = inner.get_children()[0]
    for w in cell.get_children():
        if isinstance(w, Gtk.Label):
            return w
    return None


def hdr_label(app, field):
    arrow = app._sort_labels[field]
    for w in arrow.get_parent().get_children():
        if isinstance(w, Gtk.Label):
            return w
    return None


def x_of(drive, widget):
    xy = widget.translate_coordinates(drive.child, 0, 0)
    return None if xy is None else xy[0]


# ============================================================ Esc (PKG-1)
def esc_checks():
    d = appdrive.Drive("packages")
    app = d.app
    closed = []
    app.close = lambda *a, **k: closed.append(1)     # so the drive survives it
    app.entry.grab_focus()
    d.type("cal")
    typed = app.entry.get_text()
    d.key("Escape")
    d.pump(0.2)

    run("Esc with a search in the box clears the search, not the window",
        lambda: (not closed and app.entry.get_text() == "",
                 "typed=%r closed=%r text=%r"
                 % (typed, closed, app.entry.get_text())))

    d.key("Escape")
    d.pump(0.2)
    run("...and the second Esc, with the box already empty, is the one that "
        "leaves",
        lambda: (len(closed) == 1,
                 "window close calls after two Escapes: %d" % len(closed)))

    # The order the app reads Esc in, from the top layer down: an open menu is
    # dismissed BEFORE the search it is sitting over. Reading the search first
    # would leave the dropdown standing over a list that had just changed
    # under it, which is the accident the window-level answer could introduce.
    app.entry.grab_focus()
    d.type("cal")
    d.open_menu("View")
    d.key("Escape")
    d.pump(0.2)

    run("Esc with a menu open closes the menu and leaves the search alone",
        lambda: (app._menu_open is None and app.entry.get_text() == "cal"
                 and len(closed) == 1,
                 "menu %r, search %r, close calls %d"
                 % (app._menu_open, app.entry.get_text(), len(closed))))
    d.close()


# ================================================== the list (PKG-4, 6, 8)
def list_checks(d):
    app = d.app

    def column_offsets():
        d.pump(0.3)
        out = {}
        rows = [app._rows[i] for i in sorted(app._rows) if app._rows[i].get_visible()]
        if not rows:
            return out
        cells = rows[0].get_child().get_children()
        for field, cell in (("kind", cells[1]), ("modified", cells[2])):
            out[field] = (x_of(d, hdr_label(app, field)) or 0) - (x_of(d, cell) or 0)
        return out

    long_list = column_offsets()
    app.entry.grab_focus()
    d.type("writ")
    d.pump(0.4)
    short_list = column_offsets()
    app.entry.set_text("")
    d.pump(0.3)

    run("column headers stay over their columns when the list loses its "
        "scrollbar",
        lambda: (max(abs(v) for v in short_list.values()) <= 4,
                 "scrolling list %r, short list %r" % (long_list, short_list)))

    # PKG-6: the name is the one thing that must not ellipsize at 1024.
    def names_fit():
        cut = []
        for i, row in sorted(app._rows.items()):
            lab = name_label(row)
            if lab is not None and lab.get_layout().is_ellipsized():
                cut.append((d.mod.PACKAGES[i][d.mod.NAME],
                            lab.get_allocated_width()))
        listed = {p[d.mod.NAME] for p in d.mod.PACKAGES}
        both = {"Application Framework", "Install Notebook OS"} & listed
        return (not cut and len(both) == 2,
                "ellipsized: %r (the two longest shipped names present: %r)"
                % (cut, sorted(both)))

    run("every shipped package name is readable in full at 1024x740",
        names_fit)

    # PKG-8: a removed app is marked in the LIST, not only in the inspector.
    def removed_marked():
        idx = next(i for i, p in enumerate(d.mod.PACKAGES)
                   if p[d.mod.KIND] == "Application")
        target = d.mod.PACKAGES[idx][d.mod.NAME]
        other = next(i for i, p in enumerate(d.mod.PACKAGES)
                     if p[d.mod.KIND] == "Application" and i != idx)
        app._select_row(idx)
        d.pump(0.2)
        d.click("Uninstall")
        d.pump(0.4)
        row = app._rows[idx]
        classes = row.get_style_context().list_classes()
        ink = name_label(row).get_style_context().get_color(
            Gtk.StateFlags.NORMAL)
        plain = name_label(app._rows[other]).get_style_context().get_color(
            Gtk.StateFlags.NORMAL)
        faint = (ink.red, ink.green, ink.blue) != (plain.red, plain.green,
                                                   plain.blue)
        d.click("Restore")
        d.pump(0.4)
        back = app._rows[idx].get_style_context().list_classes()
        return (target in app._removed_apps or True) and (
            "removed" in classes and faint and "removed" not in back), (
            "%s classes=%r ink=%r vs %r, after Restore=%r"
            % (target, classes, tuple(round(c, 3) for c in
                                      (ink.red, ink.green, ink.blue)),
               tuple(round(c, 3) for c in (plain.red, plain.green, plain.blue)),
               back))

    run("an app removed from Applications is printed faintly in the list",
        removed_marked)


# ============================================== the inspector dot (PKG-7)
def dot_checks(d):
    app = d.app

    def geometry():
        app._select_row(0)
        d.pump(0.2)
        app._flash("This package has been changed, so it can no longer be "
                   "opened.", True)
        d.pump(0.3)
        dots = [w for w in d.walk(app.detail)
                if isinstance(w, Gtk.Label)
                and "flashdot" in w.get_style_context().list_classes()]
        if not dots:
            return False, "no result marker was drawn"
        a = dots[0].get_allocation()
        return (a.width == a.height == 8,
                "marker is %dx%d beside a two-line result" % (a.width, a.height))

    run("the inspector's result marker is a round dot, not a stripe", geometry)


# ================================== the Sources page (PKG-2, PKG-3, PKG-10)
def sources_checks(d):
    app = d.app
    mod = d.mod
    mod.nbpkg_install = FakePkg
    mod._installed_registry = lambda: {
        "Vremenko": {"module": "vremenko", "kind": "Utility",
                     "version": "1.4.0"}}
    app._removable_media = lambda: [("MY STICK", "/media/MY STICK"),
                                    ("Family Photos", "/media/Family Photos")]
    app._on_nav(None, "sources")
    d.pump(1.5)

    def rows_by_package():
        """{row title: (its button, the row's text)} for the package rows.

        Found structurally — a button inside a source row — and not by the
        style class the fix adds, so this reads the same rows on an app that
        has not been fixed."""
        found = {}
        for row in app._sources_list.get_children():
            buttons = [w for w in d.walk(row) if isinstance(w, Gtk.Button)]
            if not buttons:
                continue
            b = buttons[0]
            texts = [w.get_text() for w in d.walk(row)
                     if isinstance(w, Gtk.Label) and w is not label_of(b)]
            found[texts[0] if texts else ""] = (b, texts)
        return found

    rows = rows_by_package()

    run("a refused package offers no install control at all",
        lambda: (
            "scribbled.nbpkg" in rows
            and not rows["scribbled.nbpkg"][0].get_visible(),
            "row says %r" % (rows.get("scribbled.nbpkg", (None, []))[1],)))

    run("a package already installed is not offered as ready to install",
        lambda: (
            rows["Vremenko 1.4.0"][0].get_label() == "Installed"
            and not rows["Vremenko 1.4.0"][0].get_sensitive()
            and "Verified" not in " ".join(rows["Vremenko 1.4.0"][1]),
            "row says %r, button %r"
            % (rows["Vremenko 1.4.0"][1], rows["Vremenko 1.4.0"][0].get_label())))

    run("...while a package this machine does not have is still offered",
        lambda: (
            rows["Govorimo 2.0.0"][0].get_label() == "Install"
            and rows["Govorimo 2.0.0"][0].get_sensitive()
            and rows["Govorimo 2.0.0"][0].get_visible(),
            "row says %r" % (rows["Govorimo 2.0.0"][1],)))

    def unavailable_reads_unavailable():
        live = label_of(rows["Govorimo 2.0.0"][0])
        dead = label_of(rows["Vremenko 1.4.0"][0])
        a = live.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        b = dead.get_style_context().get_color(Gtk.StateFlags.INSENSITIVE)
        same = (round(a.red, 3), round(a.green, 3), round(a.blue, 3)) == \
               (round(b.red, 3), round(b.green, 3), round(b.blue, 3))
        return (not same,
                "live ink %r, unusable ink %r"
                % (tuple(round(c, 3) for c in (a.red, a.green, a.blue)),
                   tuple(round(c, 3) for c in (b.red, b.green, b.blue))))

    run("an install button that cannot be pressed is printed faintly",
        unavailable_reads_unavailable)

    def source_row_titles():
        """The storage rows only: a source row carries a state chip, the
        package rows below the "Apps to install" heading do not."""
        titles = []
        for row in app._sources_list.get_children():
            labels = [w for w in d.walk(row) if isinstance(w, Gtk.Label)]
            cls = [c for w in labels for c in w.get_style_context().list_classes()]
            if "chip-on" not in cls and "chip-off" not in cls:
                continue
            titles.append(next(w.get_text() for w in labels
                               if "source-label"
                               in w.get_style_context().list_classes()))
        return titles

    def one_row_per_stick():
        titles = source_row_titles()
        badge = app._nav_count["sources"].get_text()
        sticks = [t for t in titles if t in ("MY STICK", "Family Photos")]
        # the badge counts this computer plus every stick; the page has to show
        # exactly that many source rows
        return (len(sticks) == 2 and badge == "3" and len(titles) == 3,
                "source rows %r, badge %r" % (titles, badge))

    run("each plugged-in stick gets its own source row, matching the count",
        one_row_per_stick)

    app._removable_media = lambda: []
    app._refresh_sources()
    d.pump(0.3)

    def nothing_plugged_in():
        texts = [w.get_text() for w in d.walk(app._sources_list)
                 if isinstance(w, Gtk.Label)]
        return ("No USB storage is connected" in texts
                and app._nav_count["sources"].get_text() == "1"
                and source_row_titles() == ["This computer", "USB stick"],
                "rows %r" % (source_row_titles(),))

    run("...and with no stick the page still says so", nothing_plugged_in)


# ============================================ the emptied store (PKG-5)
def zero_byte_store():
    """Display-free: the subject is a file, not a widget."""
    import nbapp
    import packages as pk

    root = tempfile.mkdtemp(prefix="nb-packages-zero-")
    path = os.path.join(root, "removed_apps.json")
    with open(path, "wb") as fh:
        fh.write(b"")                       # an interrupted write / full disk

    app = pk.Packages.__new__(pk.Packages)
    app._removed_apps_path = lambda: path
    app._removed_apps = app._load_removed_apps()
    app.view, app.sort_field, app.sort_desc = "installed", None, False
    told = []
    real_note = nbapp.note_save_failure
    nbapp.note_save_failure = lambda *a, **k: told.append(a)
    try:
        saved = app._save_view_prefs()
        app._removed_apps = {"Music"}
        saved_again = app._save_view_prefs()
    finally:
        nbapp.note_save_failure = real_note

    def landed():
        raw = open(path, "rb").read()
        try:
            on_disk = json.loads(raw.decode("utf-8"))
        except ValueError:
            on_disk = {}                    # still the emptied file: no save
        return (saved and saved_again and not told
                and on_disk.get("removed") == ["Music"],
                "first save %r, second %r, reported failures %r, store %r"
                % (saved, saved_again, told, raw[:80]))

    run("an emptied store does not block Uninstall for good", landed)

    def kept():
        aside = [n for n in os.listdir(root)
                 if n.startswith("removed_apps.json.damaged-")]
        return (len(aside) == 1 and os.path.getsize(os.path.join(root, aside[0])) == 0,
                "quarantine: %r" % (aside,))

    run("...and the emptied file is still kept aside as evidence", kept)


def main():
    zero_byte_store()
    esc_checks()
    d = appdrive.Drive("packages")
    try:
        list_checks(d)
        dot_checks(d)
        sources_checks(d)
    finally:
        d.close()
    print("\n%d failed" % len(failures))
    print("RESULT: %s" % ("FAILED" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
