#!/usr/bin/env python3
"""Display-free acceptance checks for Finder's open/launch routing.

Double-clicking is the one gesture the whole file system answers to, and every
one of its outcomes is invisible in a screenshot: which module got spawned,
with which argument, and — when nothing can be spawned — whether the user was
told anything at all. So the routing methods are driven directly here, on a
stub that records instead of launching, with no GTK window and no display.

The defect this file was written for: an item whose name ends in ".app" but
whose stem is not in APP_MODULES made `launch_app` return silently — no app,
no message. The everyday way to produce one is to rename an app (the rename
deliberately keeps the ".app" suffix, so the file stays an app while its stem
stops naming a module), after which its icon was dead forever and said nothing.

Run as:
  python3 tools/finder_routing_selftest.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import finder  # noqa: E402

FAILED = []


def check(ok, label):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILED.append(label)


class Row:
    def __init__(self, name, rel):
        self.cols = {1: name, 4: rel}


class Store:
    """The two columns _open_path reads: 1 = file name, 4 = relative path."""

    def __init__(self, row):
        self.row = row

    def get_iter(self, _path):
        return self.row

    def get_value(self, it, col):
        return it.cols[col]


class Probe:
    """A Finder with the launching and the status bar taken out.

    Finder is a Gtk.Window and cannot be constructed without a display, so the
    routing methods are called unbound against this recorder. Everything the
    routing touches is here; nothing else of Finder is needed."""

    def __init__(self, home, name="", rel=""):
        self.home = home
        self.store = Store(Row(name, rel))
        self.rel = ""
        self.launched = []      # (module, file argument)
        self.flashed = []       # status-bar messages the user would see
        self.loaded = []        # folders navigated into

    # -- the parts the routing calls out to --
    def abspath(self, rel):
        return os.path.join(self.home, rel)

    def load(self, rel, **_kw):
        self.loaded.append(rel)

    def _flash_status(self, msg, restore_ms=2400):
        self.flashed.append(msg)

    def _launch_module(self, mod, file_arg=None):
        self.launched.append((mod, file_arg))

    # -- the routing under test, bound to this recorder --
    def open_path(self):
        return finder.Finder._open_path(self, None)

    def launch_app(self, display_name, file_arg=None):
        return finder.Finder.launch_app(self, display_name, file_arg)

    def default_app_for(self, ext):
        return finder.Finder._default_app_for(self, ext)

    def _default_app_for(self, ext):
        return finder.Finder._default_app_for(self, ext)


def probe(home, name):
    """A Probe whose single row is `name`, with the file made on disk."""
    open(os.path.join(home, name), "w").close()
    return Probe(home, name, name)


def write_settings(home, payload):
    """Put `payload` where _default_app_for reads the user's choice from."""
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "settings.json"), "w") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh)


# ------------------------------------------------------------------ known --

def known_documents(home):
    """A document opens in its owning app, with its own path as argv[1]."""
    p = probe(home, "Recipe.txt")
    p.open_path()
    check(p.launched == [("writer", os.path.join(home, "Recipe.txt"))],
          "a .txt opens in Writer, handed its own path")

    p = probe(home, "Holiday.MP4")
    p.open_path()
    check(p.launched == [("media", os.path.join(home, "Holiday.MP4"))],
          "an upper-case .MP4 routes like .mp4 (extension match is caseless)")

    p = probe(home, "Backup.Tar.GZ")
    p.open_path()
    check(p.launched == [] and p.flashed,
          "a compound name routes on its LAST extension and says so when "
          "nothing claims it")

    p = Probe(home, "Pictures", "Pictures")
    os.makedirs(os.path.join(home, "Pictures"), exist_ok=True)
    p.open_path()
    check(p.loaded == ["Pictures"] and not p.launched,
          "a folder is navigated into, never handed to an app")

    link = os.path.join(home, "Shortcut.txt")
    os.symlink(os.path.join(home, "Recipe.txt"), link)
    p = Probe(home, "Shortcut.txt", "Shortcut.txt")
    p.open_path()
    check(p.launched == [("writer", link)],
          "a symlink to a document opens like the document")

    os.symlink(os.path.join(home, "nothing-here"), os.path.join(home, "Dead.txt"))
    p = Probe(home, "Dead.txt", "Dead.txt")
    p.open_path()
    check(not p.launched and p.flashed,
          "a broken symlink refreshes the view and says what happened")


# ---------------------------------------------------------------- default --

def default_app_choice(home):
    """Settings ▸ Default Applications wins, but only when it names an app
    that can actually take a file path."""
    write_settings(home, {"default_apps": {".txt": "ebook"}})
    check(Probe(home).default_app_for(".txt") == "ebook",
          "an explicit valid default is honoured over the built-in mapping")

    p = probe(home, "Story.txt")
    p.open_path()
    check(p.launched == [("ebook", os.path.join(home, "Story.txt"))],
          "the honoured default is what the double-click actually launches")

    write_settings(home, {"default_apps": {".txt": "writer"}})
    check(Probe(home).default_app_for(".txt") == "writer",
          "a default that agrees with the built-in mapping stays put")


def malformed_mappings(home):
    """Anything unreadable, mistyped or hand-edited falls back to the
    built-in mapping — never to a crash and never to a dead choice."""
    cases = [
        ({"default_apps": {".txt": "novel"}},
         "a module that ignores argv[1] is refused (not in FILE_OPENERS)"),
        ({"default_apps": {".txt": "no-such-module"}},
         "a module that does not exist is refused"),
        ({"default_apps": {".txt": ["writer"]}},
         "a non-string value is refused"),
        ({"default_apps": {".txt": None}},
         "a null value is refused"),
        ({"default_apps": ["writer"]},
         "a default_apps that is not a mapping is refused"),
        ({"default_apps": {".TXT": "ebook"}},
         "an upper-case key does not match the lower-cased lookup"),
        (["not", "a", "mapping"],
         "a settings file that is not an object is refused"),
        ("{ this is not json",
         "a corrupt settings file is refused"),
    ]
    for payload, label in cases:
        write_settings(home, payload)
        check(Probe(home).default_app_for(".txt") == "writer", label)

    os.remove(os.path.join(home, ".config", "notebook", "settings.json"))
    check(Probe(home).default_app_for(".txt") == "writer",
          "a missing settings file falls back to the built-in mapping")
    check(Probe(home).default_app_for(".qqq") is None and
          Probe(home).default_app_for("") is None,
          "an unknown or absent extension claims no app")


# --------------------------------------------------- missing app / failure --

def unclaimed_items(home):
    """Nothing may open with no explanation. Every dead end talks."""
    p = probe(home, "Mystery.qqq")
    p.open_path()
    check(not p.launched and p.flashed == ["No app for this file type"],
          "a file type no app claims flashes an explanation")

    p = Probe(home)
    p.launch_app("Calculator")
    check(p.launched == [("calculator", None)],
          "a known app launches its module")

    # THE REGRESSION. Renaming "Calculator.app" to "Adding Machine" leaves
    # "Adding Machine.app" on disk: still an app, no longer a known one.
    p = Probe(home)
    p.launch_app("Adding Machine")
    check(not p.launched and p.flashed == ["That app is not available"],
          "a renamed/unknown .app says so instead of doing nothing silently")

    p = probe(home, "Adding Machine.app")
    p.open_path()
    check(not p.launched and p.flashed == ["That app is not available"],
          "double-clicking that icon is never a dead control")

    p = Probe(home)
    p.launch_app("")
    check(not p.launched and p.flashed == ["That app is not available"],
          "a file named exactly '.app' is answered too")


def launch_failures(home):
    """The two ways a launch dies once a module HAS been chosen."""
    real_de, real_popen = finder.DE_DIR, finder.subprocess.Popen
    real_watch = finder.GLib.child_watch_add

    class Recorder(Probe):
        def _launch_module(self, mod, file_arg=None):
            return finder.Finder._launch_module(self, mod, file_arg)

        def hide(self):
            pass

        def _app_exited(self, *_args):
            pass

        def _launch_watch(self):
            # launch continuity is finder_launch_selftest's subject; this
            # suite tests ROUTING, so the watcher is an inert stub here
            return False

    try:
        finder.DE_DIR = os.path.join(home, "no-such-de")
        p = Recorder(home)
        p.launch_app("Calculator")
        check(p.flashed == ["That app is not available"],
              "a module absent from the image is reported, not ignored")

        finder.DE_DIR = real_de

        def refuse(*_a, **_kw):
            raise OSError("fork failed")

        finder.subprocess.Popen = refuse
        p = Recorder(home)
        p.launch_app("Calculator")
        check(p.flashed == ["Could not open that app"],
              "a rejected spawn keeps the Finder visible and reports it")

        captured = []

        class FakeProc:
            pid = 424242

        def accept(argv, **kwargs):
            captured.append((argv, kwargs))
            return FakeProc()

        finder.subprocess.Popen = accept
        finder.GLib.child_watch_add = lambda *_a, **_kw: 1
        hostile = os.path.join(home, "notes; touch SHOULD_NOT_EXIST.txt")
        p = Recorder(home)
        finder.Finder._launch_module(p, "writer", hostile)
        check(captured and captured[0][0][-1] == hostile
              and isinstance(captured[0][0], list),
              "punctuation in a filename stays one argv value, never shell text")
        check(not os.path.exists(os.path.join(home, "SHOULD_NOT_EXIST.txt")),
              "routing never executes filename contents")
    finally:
        finder.DE_DIR = real_de
        finder.subprocess.Popen = real_popen
        finder.GLib.child_watch_add = real_watch


def built_in_mapping_is_coherent():
    """Every built-in association must point at a module that exists and
    accepts a path; otherwise a document routes to an app that ignores it."""
    targets = set(finder.FILE_APPS.values())
    check(targets <= finder.FILE_OPENERS,
          "every built-in association names a module that takes argv[1]")
    missing = sorted(m for m in targets
                     if not (DE / (m + ".py")).exists())
    check(not missing, "every built-in association ships: %s" % missing)
    check(all(e == e.lower() and e.startswith(".")
              for e in finder.FILE_APPS),
          "every built-in extension key is lower-case and dotted")


if __name__ == "__main__":
    home = tempfile.mkdtemp(prefix="nb-routing-")
    finder.HOME = home
    known_documents(home)
    default_app_choice(home)
    malformed_mappings(home)
    unclaimed_items(home)
    launch_failures(home)
    built_in_mapping_is_coherent()
    if FAILED:
        print("\nFinder routing selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
        sys.exit(1)
    print("\nFinder routing selftest: OK")
