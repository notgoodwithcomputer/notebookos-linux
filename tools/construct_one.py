#!/usr/bin/env python3
"""Construct one app window without showing it; report import/constructor faults.

Usage: python3 tools/construct_one.py <appname>
"""
import importlib
import inspect
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
                                  "notebookos", "rootfs-overlay", "opt",
                                  "notebook", "de"))


def _prepare_home():
    """Return the temp home owned by this process, or None for caller-owned."""
    if os.environ.get("NB_HOME"):
        return None
    home = tempfile.mkdtemp(prefix="nbhome-construct-")
    os.environ["NB_HOME"] = home
    return home


def _cleanup_home(home):
    if home is None:
        return
    if os.environ.get("NB_HOME") == home:
        os.environ.pop("NB_HOME", None)
    shutil.rmtree(home, ignore_errors=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: construct_one.py APPNAME", file=sys.stderr)
        return 2
    name = argv[0]
    home = _prepare_home()
    try:
        sys.path.insert(0, DE)
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        try:
            module = importlib.import_module(name)
            cls = None
            for _member, candidate in inspect.getmembers(module,
                                                         inspect.isclass):
                if (candidate.__module__ == module.__name__ and
                        issubclass(candidate, Gtk.Window)):
                    cls = candidate
                    break
            if cls is None:
                print("NOCLASS %s" % name)
                return 2
            window = cls()
            count = 0
            while Gtk.events_pending() and count < 800:
                Gtk.main_iteration()
                count += 1
            try:
                window.destroy()
            except Exception:
                pass
            print("OK %s constructs" % name)
            return 0
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print("CRASH %s %s: %s" %
                  (name, type(exc).__name__, str(exc)[:100]))
            return 1
    finally:
        _cleanup_home(home)


if __name__ == "__main__":
    sys.exit(main())
