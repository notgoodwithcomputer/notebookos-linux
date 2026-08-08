#!/usr/bin/env python3
"""Named, handler-level checks for user-visible messaging honesty.

Run with a fresh NB_HOME (the driver supplies one):
  PYTHONPATH=buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
    python3 tools/messaging_honesty_selftest.py
"""
import errno
import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


APPS = (
    "video music media ebook maps language workout mealplanner cookbook "
    "contacts tasks journal novel writer screenplay sequencer illustrator "
    "calendar g2048 finder packages sysmon terminal calculator nbgame "
    "usbwriter settings"
).split()
ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
OLD = {"Save failed", "Write failed"}


def fail(name, detail):
    print("FAIL %s: %s" % (name, detail))
    return False


def pass_(name):
    print("PASS %s" % name)
    return True


def assert_full_disk_sentence(name, shown):
    if shown in OLD:
        return fail(name, "generic omission survived: %r" % shown)
    low = shown.lower()
    if "disk is full" not in low or "free up space" not in low or "try again" not in low:
        return fail(name, "missing condition/action in %r" % shown)
    return pass_(name)


def exercise(appname, clsname, write_name, handler_name, path_attr, prep=None,
             success_hook=None):
    mod = importlib.import_module(appname)
    cls = getattr(mod, clsname)
    obj = cls.__new__(cls)
    shown = []
    path = os.path.join(os.environ["NB_HOME"], "Documents", appname + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    setattr(obj, path_attr, path)
    obj._flash = shown.append
    obj._flash_status = shown.append
    if prep:
        prep(obj)
    with mock.patch.object(mod.nbapp, "atomic_write_json",
                           side_effect=OSError(errno.ENOSPC, "No space left")):
        ok = getattr(cls, write_name)(obj, path)
        if ok is not False:
            return fail(appname + "_full_disk_action", "write did not report failure")
        if success_hook:
            success_hook(obj)
        if appname == "calendar":
            # Calendar reports directly in _write_document.
            sentence = shown[-1]
        else:
            shown.clear()
            getattr(cls, handler_name)(obj)
            sentence = shown[-1]
    return assert_full_disk_sentence(appname + "_full_disk_action", sentence)


def run_live():
    results = []
    results.append(exercise(
        "tasks", "Tasks", "_write_doc", "_file_save", "_doc_path",
        lambda o: (setattr(o, "tasks", []), setattr(o, "_close_menu", lambda: None))))
    results.append(exercise(
        "video", "VideoEditor", "_write_file", "_file_save", "_path",
        lambda o: setattr(o, "_serialize", lambda: {})))
    results.append(exercise(
        "screenplay", "Screenplay", "_write_file", "_file_save", "_path",
        lambda o: (
            setattr(o, "_fmt_of", lambda _p: "json"),
            setattr(o, "scripttitle", type("T", (), {"get_text": lambda _s: "Draft"})()),
            setattr(o, "scriptsubtitle", type("T", (), {"get_text": lambda _s: ""})()),
            setattr(o, "body", type("B", (), {"get_buffer": lambda _s: type("G", (), {
                "get_start_iter": lambda _s: None, "get_end_iter": lambda _s: None,
                "get_text": lambda _s, *_a: "INT. ROOM - DAY"})()})()),
            setattr(o, "_serialize_body_tags", lambda _b: []))))
    results.append(exercise(
        "calendar", "Calendar", "_write_document", None, "_doc_path",
        lambda o: setattr(o, "_serialize_document", lambda: {})))
    return all(results)


def inventory():
    """Guarded reads: every scoped app exists and was examined for all axes."""
    axes = {
        "recovery": ("damaged", "quarantin", "could not read", "open failed"),
        "failure": ("failed", "could not", "not saved", "unavailable"),
        "progress": ("saved", "synced", "back", "finished", "done"),
        "reset": ("reset", "cleared", "removed", "deleted", "undo"),
    }
    ok = True
    for app in APPS:
        path = DE / (app + ".py")
        if not path.is_file():
            ok = fail("inventory_%s" % app, "source missing") and ok
            continue
        text = path.read_text(encoding="utf-8").lower()
        present = [axis for axis, words in axes.items() if any(w in text for w in words)]
        print("AUDIT %s: %s" % (app, ", ".join(present) if present else "no axis sentence"))
    return pass_("all_scoped_apps_guarded_read") and ok


def pass_mutant():
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="nb-msg-mutant-") as home:
        env["NB_HOME"] = home
        proc = subprocess.run(
            [sys.executable, __file__, "--mutant"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode == 0 or "FAIL mutant_generic_save_sentence" not in proc.stdout:
        return fail("pass_mutant_generic_save_sentence", proc.stdout.strip())
    return pass_("pass_mutant_generic_save_sentence")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--mutant":
        return 0 if assert_full_disk_sentence("mutant_generic_save_sentence", "Save failed") else 1
    if "NB_HOME" not in os.environ:
        with tempfile.TemporaryDirectory(prefix="nb-msg-") as home:
            env = dict(os.environ)
            env["NB_HOME"] = home
            return subprocess.call([sys.executable, __file__, "--live"], env=env)
    ok = inventory() and run_live()
    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        ok = pass_mutant() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
