#!/usr/bin/env python3
"""finder_i18n_selftest — is the Applications folder actually in the user's
language, and does translating it break anything?

    DISPLAY=:0 python3 tools/finder_i18n_selftest.py

WHY THIS FILE EXISTS
Every app name, every Kind and every month name was already translated in all
seventeen catalogs, and every one of them still reached the screen in English.
nbi18n's automatic layer walks Labels and Buttons; the Finder's file list is a
Gtk.TreeView, so nothing in it was ever looked up. The result was that the
first screen a non-English user opens, and the one they open most, was the
only screen in the OS still entirely in English — and Home showed "Documents"
and "Music" in the list while the sidebar three centimetres to the left said
"Documentos" and "Música".

Translating a file list is the kind of fix that breaks things quietly, so this
file spends most of its checks on what must NOT change:

  * the store still holds the on-disk name, so launching still finds the
    module (APP_MODULES is keyed on the real name), and renaming still renames
    the file that exists;
  * a document the user named is never touched, in any language;
  * a folder the user made and called "Music" is not renamed on screen just
    because a provisioned folder of that name exists in Home;
  * the list is SORTED by what is on screen, or a translated folder is in no
    order at all;
  * an app can still be found by typing either of its names.

This process runs in Spanish: NB_LANG is set before nbi18n is imported, which
is the only moment it is read. English is checked in a subprocess, because the
catalog cannot be swapped afterwards.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/"
                        "notebook/de")
sys.path.insert(0, DE)

# Before `import finder`, which reads both of these at import time.
os.environ["NB_LANG"] = "es"
HOME = tempfile.mkdtemp(prefix="nbfinder-i18n-")
os.environ["NB_HOME"] = HOME
APPS = os.path.join(HOME, "Applications")
os.makedirs(APPS, exist_ok=True)
for _n in ("Settings", "Calculator", "Music", "Writer", "2048"):
    with open(os.path.join(APPS, _n + ".app"), "w") as _fh:
        _fh.write("#!/bin/sh\n# Notebook OS application package\n")
os.makedirs(os.path.join(HOME, "Documents", "Music"), exist_ok=True)
os.makedirs(os.path.join(HOME, "Music"), exist_ok=True)
with open(os.path.join(HOME, "Documents", "Music.txt"), "w") as _fh:
    _fh.write("notes\n")

import gi                                                      # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                  # noqa: E402

import finder                                                  # noqa: E402

CHECKS = [0]
FAILURES = []


def check(cond, what):
    CHECKS[0] += 1
    if cond:
        print("  ok   %s" % what)
    else:
        FAILURES.append(what)
        print("  FAIL %s" % what)


def run_names():
    print("\n-- what the list says")
    d = finder.display_name
    check(d("Settings.app") == "Ajustes",
          "an app is called what the catalog calls it: %r" % d("Settings.app"))
    check(d("2048.app") == "2048",
          "an app whose name is a number is left alone")
    check(d(".Trash") == "Papelera", "and so is the Trash: %r" % d(".Trash"))
    check(d("Music", "Music") == "Música",
          "a folder the OS provisions in Home follows the sidebar")
    check(d("Applications", "Applications") == "Aplicaciones",
          "...including Applications itself")

    # The half of this that must NOT happen.
    check(d("Music.txt") == "Music.txt",
          "a document keeps the name its owner typed")
    check(d("Music", "Documents/Music") == "Music",
          "a folder the USER made and called Music, inside Documents, is "
          "their own word — not the provisioned folder of that name")
    check(d("Music") == "Music",
          "and with no path to prove it is the provisioned one, nothing is "
          "assumed")


def run_kind_and_date():
    print("\n-- the columns beside the name")
    check(finder.kind_for("Settings.app", False) == "Sistema",
          "Kind is translated at its source: %r"
          % finder.kind_for("Settings.app", False))
    check(finder.kind_for("x", True) == "Carpeta", "...including Folder")
    check(finder.kind_for("holiday.png", False) == "Imagen",
          "...and the per-extension kinds")
    got = finder.kind_for("archive.xyz", False)
    check(got == "Archivo XYZ",
          "the extension fallback is one catalog key, not a translated word "
          "glued to English word order: %r" % got)
    from nbi18n import _t
    check(_t("15 Jul 2026") == "15 jul 2026",
          "a date reaches the catalog's date rule: %r" % _t("15 Jul 2026"))


def run_search():
    print("\n-- finding an app by either of its names")
    n = finder.search_names("Settings.app")
    check("ajustes" in n and "settings" in n,
          "both names are searchable: %r" % (n,))
    n2 = finder.search_names("Music", "Music")
    check("música" in n2 and "music" in n2,
          "a provisioned folder too: %r" % (n2,))
    n3 = finder.search_names("holiday.png")
    check(n3 == ("holiday.png",),
          "an ordinary file has exactly one name: %r" % (n3,))


def run_window():
    print("\n-- the window, in Spanish")
    win = finder.Finder()
    win.load("Applications")
    n = 0
    while Gtk.events_pending() and n < 400:
        Gtk.main_iteration()
        n += 1

    raw = [row[1] for row in win.store]
    check("Settings.app" in raw,
          "the STORE still holds the on-disk name, which is what launching, "
          "renaming and the icon map all read: %r" % raw)
    check(finder.APP_MODULES.get("Settings.app"[:-4]) == "settings",
          "...so the launcher still resolves the module")

    shown = [finder.display_name(r[1], r[4]) for r in win.store]
    check(shown == sorted(shown, key=str.lower),
          "the list is in the order it is READ in, not the order it is "
          "stored in: %r" % shown)

    # Type-ahead, both ways round.
    for typed, want in (("aju", "Settings.app"), ("set", "Settings.app")):
        win._typeahead = ""
        for ch in typed:
            win._type_ahead(ch)
        model, it = win._selected_iter()
        sel = model.get_value(it, 1) if it is not None else None
        check(sel == want,
              "typing %r reaches %s (got %r)" % (typed, want, sel))

    win.destroy()


def run_rename():
    print("\n-- renaming edits the FILE")
    win = finder.Finder()
    win.load("Applications")
    n = 0
    while Gtk.events_pending() and n < 400:
        Gtk.main_iteration()
        n += 1
    idx = [i for i, row in enumerate(win.store) if row[1] == "Settings.app"]
    check(bool(idx), "the app is in the listing")
    if idx:
        entry = Gtk.Entry()
        entry.set_text(finder.display_name("Settings.app"))
        win._on_edit_started(None, entry, str(idx[0]))
        check(entry.get_text() == "Settings",
              "the rename field opens on the file's own name, not the "
              "translated one — committing it unchanged would have renamed "
              "Settings.app to Ajustes.app and the app would never launch "
              "again (got %r)" % entry.get_text())
    win.destroy()


def run_english():
    """In English none of this may do anything at all."""
    print("\n-- English is untouched")
    src = r'''
import os, sys, tempfile
sys.path.insert(0, %r)
os.environ["NB_LANG"] = "en"
os.environ["NB_HOME"] = tempfile.mkdtemp()
import finder
print(finder.display_name("Settings.app"), "|",
      finder.display_name("Music", "Music"), "|",
      finder.kind_for("Settings.app", False), "|",
      finder.kind_for("archive.xyz", False), "|",
      finder.search_names("Settings.app"))
'''
    r = subprocess.run([sys.executable, "-c", src % DE],
                       capture_output=True, text=True)
    got = (r.stdout or "").strip()
    parts = [p.strip() for p in got.split("|")]
    check(len(parts) == 5 and parts[0] == "Settings",
          "an app still reads as itself: %r" % got)
    check(len(parts) == 5 and parts[1] == "Music", "and a folder does too")
    check(len(parts) == 5 and parts[2] == "System" and parts[3] == "XYZ File",
          "and Kind is the English it always was: %r" % parts[2:4])
    check(len(parts) == 5 and parts[4] == "('settings',)",
          "one name, so search does no extra work: %r" % parts[4])


def main():
    run_names()
    run_kind_and_date()
    run_search()
    try:
        run_window()
    except Exception as e:                                     # noqa: BLE001
        check(False, "the window could not be driven: %r" % e)
    try:
        run_rename()
    except Exception as e:                                     # noqa: BLE001
        check(False, "the rename path could not be driven: %r" % e)
    run_english()

    print()
    if FAILURES:
        print("FINDER I18N SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        for f in FAILURES:
            print("   - %s" % f)
        return 1
    print("FINDER I18N SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
