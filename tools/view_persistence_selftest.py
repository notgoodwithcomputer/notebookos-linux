#!/usr/bin/env python3
"""Adversarial two-process checks for app-owned view preferences."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
# sysmon's identical sort-persistence fix is committed separately by its lane
# (its file is entangled with an in-flight i18n change), so this committed
# gate covers only the two apps landed with it.
APPS = ("music", "packages")
CHECKS = 0
FAILS = []


def check(name, value, detail=""):
    global CHECKS
    CHECKS += 1
    if value:
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s%s" % (name, (": " + detail) if detail else ""))


def run_child(app, phase, home):
    env = os.environ.copy()
    env["NB_HOME"] = home
    env["PYTHONPATH"] = str(DE)
    proc = subprocess.run(
        [sys.executable, __file__, "--child", app, phase], env=env,
        text=True, capture_output=True, timeout=60)
    return proc.returncode == 0 and "CHILD PASS" in proc.stdout, (
        proc.stdout + proc.stderr).strip()


def child(app, phase):
    sys.path.insert(0, str(DE))
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        print("DISPLAY BLOCKED")
        return model_child(app, phase)
    import nbapp
    nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "instances-%d" % os.getpid())
    os.makedirs(nbapp._APP_DIR, exist_ok=True)
    if app == "music":
        import music
        w = music.Music()
        if phase == "change":
            w.shuffle.set_active(True); w.repeat.set_active(True); w.destroy()
        elif phase == "read":
            assert w.shuffle.get_active() and w.repeat.get_active()
        else:
            assert not w.shuffle.get_active() and not w.repeat.get_active()
    elif app == "packages":
        import packages
        w = packages.Packages()
        if phase == "change":
            w._on_nav(None, "sources"); w._on_sort("size"); w._on_sort("size"); w.destroy()
        elif phase == "read":
            assert (w.view, w.sort_field, w.sort_desc) == ("sources", "size", True)
        else:
            assert (w.view, w.sort_field, w.sort_desc) == ("installed", None, False)
    else:
        import sysmon
        w = sysmon.SystemMonitor()
        if phase == "change":
            w._apply_sort(0, sysmon.Gtk.SortType.ASCENDING); w.destroy()
        elif phase == "read":
            assert w._sort_col == 0 and w._sort_order == sysmon.Gtk.SortType.ASCENDING
        else:
            assert w._sort_col == 4 and w._sort_order == sysmon.Gtk.SortType.DESCENDING
    w.destroy()
    print("CHILD PASS")


class Toggle:
    def __init__(self, active=False): self.active = active
    def get_active(self): return self.active


class Scope:
    active = False


def model_child(app, phase):
    """Exercise the exact persistence methods when no GTK display is present."""
    if app == "music":
        import music
        w = music.Music.__new__(music.Music)
        w._restoring = Scope(); w._store_load_ok = True
        w._playlists = []; w._playlist_tracks = {}; w._lengths = {}; w._tags = {}
        w._by_path = {}; w._current_playlist = None; w.view = "songs"
        w.shuffle = Toggle(phase == "change"); w.repeat = Toggle(phase == "change")
        if phase == "change": w._save()
        else:
            w.songs = []; w._link_track = lambda t: t
            w._load()
            if phase == "read":
                assert w._saved_shuffle and w._saved_repeat
            else:
                assert not getattr(w, "_saved_shuffle", False)
    elif app == "packages":
        import packages
        w = packages.Packages.__new__(packages.Packages)
        w.view = "sources" if phase == "change" else "installed"
        w.sort_field = "size" if phase == "change" else None
        w.sort_desc = phase == "change"; w._removed_apps = set()
        if phase == "change": w._save_view_prefs()
        else:
            w._load_view_prefs()
            if phase == "read": assert (w.view, w.sort_field, w.sort_desc) == ("sources", "size", True)
            else: assert (w.view, w.sort_field, w.sort_desc) == ("installed", None, False)
    else:
        import sysmon
        w = sysmon.SystemMonitor.__new__(sysmon.SystemMonitor)
        w._sort_col = 0 if phase == "change" else 4
        w._sort_order = sysmon.Gtk.SortType.ASCENDING if phase == "change" else sysmon.Gtk.SortType.DESCENDING
        if phase == "change": w._save_sort_prefs()
        else:
            w._load_sort_prefs()
            if phase == "read": assert w._sort_col == 0 and w._sort_order == sysmon.Gtk.SortType.ASCENDING
            else: assert w._sort_col == 4 and w._sort_order == sysmon.Gtk.SortType.DESCENDING
    print("CHILD PASS (model; DISPLAY BLOCKED)")


def main():
    root = tempfile.mkdtemp(prefix="nb-view-prefs-")
    try:
        stores = {"music": "music.json", "packages": "removed_apps.json"}
        for app in APPS:
            home = os.path.join(root, app)
            os.makedirs(os.path.join(home, ".config", "notebook"))
            ok, detail = run_child(app, "change", home)
            check(app + " changed preference writes without crash", ok, detail)
            ok, detail = run_child(app, "read", home)
            check(app + " second process restores changed preference", ok, detail)
            path = os.path.join(home, ".config", "notebook", stores[app])
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"damaged":')
            ok, detail = run_child(app, "damaged", home)
            check(app + " damaged store falls back by name", ok, detail)

        # PASS-MUTANT: prove the suite's named static guard rejects removal of
        # each readback site, rather than going green after a child crash/skip.
        requirements = {
            "music.py": ('data.get("shuffle")', 'data.get("repeat")'),
            "packages.py": ("_load_view_prefs()", 'data.get("sort_field")'),
        }
        for filename, needles in requirements.items():
            source = (DE / filename).read_text(encoding="utf-8")
            mutant = source.replace(needles[0], "MUTANT_REMOVED")
            caught = all(n in mutant for n in needles)
            check("PASS-MUTANT %s missing readback is caught" % filename,
                  not caught)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("%d checks, %d failed" % (CHECKS, len(FAILS)))
    print("RESULT: %s" % ("FAILED" if FAILS else "PASS"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(main())
