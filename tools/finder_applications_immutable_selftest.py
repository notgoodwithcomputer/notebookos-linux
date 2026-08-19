#!/usr/bin/env python3
"""Acceptance test for Finder's immutable Applications view and popup path."""
import atexit
import ast
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ("buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/"
                 "finder.py")
text = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(text)
failed = []


def check(label, condition):
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        failed.append(label)


def method(name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


# Menu absence and shared popup routing are valuable even without a display.
context = ast.get_source_segment(text, method("_popup_context_menu"))
background = ast.get_source_segment(text, method("_popup_background_menu"))
actions = ast.get_source_segment(text, method("_popup_actions_menu"))
apps_branch = context.split('elif self.rel == "Applications":', 1)[1].split(
    "        else:", 1)[0]
check("Applications context omits every mutation affordance",
      all(('(_t("%s")' % label) not in apps_branch
          for label in ("Cut", "Rename", "Duplicate", "Move to Trash")))
check("Applications background omits New Folder and Paste",
      'self.rel != "Applications"' in background)
check("removed-app management entries are absent from Finder menus",
      "Remove from Applications" not in actions
      and "Restore Removed Apps" not in actions)

popup_methods = ("_popup_context_menu", "_popup_background_menu",
                 "_popup_actions_menu")
for name in popup_methods:
    node = method(name)
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    shared = [c for c in calls if isinstance(c.func, ast.Attribute)
              and isinstance(c.func.value, ast.Name)
              and c.func.value.id == "nbapp" and c.func.attr == "popup_at"]
    legacy = [c for c in calls if isinstance(c.func, ast.Attribute)
              and c.func.attr in {"popup", "popup_at_pointer", "popup_at_widget"}]
    check(name + " routes only through nbapp.popup_at",
          len(shared) == 1 and not legacy)

display_ready = False
if os.environ.get("DISPLAY"):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        display_ready = Gtk.init_check()[0]
    except Exception:
        display_ready = False

home = tempfile.mkdtemp(prefix="finder_apps_immutable_")
atexit.register(shutil.rmtree, home, True)
os.environ["NB_HOME"] = home
apps = os.path.join(home, "Applications")
os.makedirs(apps)
for name in ("Calculator.app", "Writer.app"):
    Path(apps, name).write_text("app", encoding="utf-8")

import sys
sys.path.insert(0, str(SOURCE.parent))
import finder

if display_ready:
    w = finder.Finder("Applications")
    messages = []
    w._flash_status = lambda msg, *a, **k: messages.append(msg)
    original = sorted(os.listdir(apps))
    w._clipboard = (str(Path(home, "payload.txt")), False)
    Path(home, "payload.txt").write_text("payload", encoding="utf-8")
    for fn, args in ((w._paste, ()), (w._new_folder, ()),
                     (w._begin_rename, ()), (w._duplicate_selected, ()),
                     (w._trash_selected, ()),
                     (w._on_name_edited, (None, "0", "Changed"))):
        fn(*args)
    check("all direct mutation handlers refuse without changing Applications",
          sorted(os.listdir(apps)) == original and len(messages) == 6
          and all(m == "Applications are managed in Packages."
                  for m in messages))

    # Golden contract: save remains a sorted JSON list of display names.
    w._removed_apps = {"Writer", "Calculator"}
    w._save_removed_apps()
    store_path = Path(w._removed_apps_path())
    check("removed-apps store stays a sorted JSON display-name list",
          json.loads(store_path.read_text(encoding="utf-8"))
          == ["Calculator", "Writer"])

    # Packages-style external atomic replacement, then the existing poll pulse.
    tmp = store_path.with_suffix(".external")
    tmp.write_text(json.dumps(["Calculator"]), encoding="utf-8")
    os.replace(tmp, store_path)
    w._poll_devices()
    visible = [row[1] for row in w.store]
    check("mtime freshness reload drops externally removed app",
          "Calculator.app" not in visible and "Writer.app" in visible)

    recorded = []
    old_popup = finder.nbapp.popup_at
    finder.nbapp.popup_at = lambda menu, **kw: recorded.append(kw) or menu
    event = type("Event", (), {"button": 3, "time": 1})()
    w._popup_context_menu(event)
    w._popup_background_menu(event)
    w._popup_actions_menu(w.actions_btn)
    finder.nbapp.popup_at = old_popup
    check("popup recorder sees pointer, pointer, and widget anchors",
          recorded == [{"event": event}, {"event": event},
                       {"widget": w.actions_btn, "anchor": "widget-sw"}])
    w.destroy()
else:
    class Probe:
        rel = "Applications"

        def __init__(self):
            self.messages = []
            self._removed_apps = set()
            self._removed_apps_mtime = None
            self.loads = 0
            self.visible = []
            self._closed = False
            self._mounts_sig = ()
            self._clipboard = None

        def _flash_status(self, message, *args, **kwargs):
            self.messages.append(message)

        def _end_rename_mode(self):
            return False

        def _removed_apps_path(self):
            return str(Path(home, ".config/notebook/removed_apps.json"))

        _removed_apps_stamp = finder.Finder._removed_apps_stamp
        _load_removed_apps = finder.Finder._load_removed_apps
        _app_is_removed = finder.Finder._app_is_removed

        def _devices(self):
            return []

        def load(self, *args, **kwargs):
            self.loads += 1
            self._load_removed_apps()
            self.visible = [n for n in os.listdir(apps)
                            if not self._app_is_removed(n)]

    p = Probe()
    for name, args in (("_paste", ()), ("_new_folder", ()),
                       ("_begin_rename", ()), ("_duplicate_selected", ()),
                       ("_trash_selected", ()),
                       ("_on_name_edited", (None, "0", "Changed"))):
        getattr(finder.Finder, name)(p, *args)
    check("all direct mutation handlers refuse in Applications",
          len(p.messages) == 6 and all(
              m == "Applications are managed in Packages." for m in p.messages))

    store_path = Path(p._removed_apps_path())
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps(["Writer", "Calculator"]), encoding="utf-8")
    p._load_removed_apps()
    check("removed-apps golden file is a JSON display-name list",
          p._removed_apps == {"Writer", "Calculator"})
    tmp = store_path.with_suffix(".external")
    tmp.write_text(json.dumps(["Calculator"]), encoding="utf-8")
    os.replace(tmp, store_path)
    finder.Finder._poll_devices(p)
    check("mtime freshness reload drops externally removed app",
          p.loads == 1 and p.visible == ["Writer.app"])

    class Style:
        def add_class(self, *_args): pass

    class Item:
        def __init__(self, label=None): self.label = label
        def get_style_context(self): return Style()
        def set_sensitive(self, *_args): pass
        def connect(self, *_args): pass

    class Menu(Item):
        def __init__(self): self.items = []
        def append(self, item): self.items.append(item)
        def show_all(self): pass

    old = (finder.Gtk.Menu, finder.Gtk.MenuItem,
           finder.Gtk.SeparatorMenuItem, finder.nbapp.popup_at)
    finder.Gtk.Menu = Menu
    finder.Gtk.MenuItem = Item
    finder.Gtk.SeparatorMenuItem = Item
    recorded = []
    finder.nbapp.popup_at = lambda menu, **kw: recorded.append(kw) or menu
    p._undo = None
    p._clipboard = None
    p._selected_iter = lambda: (None, None)
    # Trash and the clipboard now act on the SELECTION, not on one row,
    # so the stub answers the list helper too. Same fixture, same
    # assertions -- only the door the code comes in through moved.
    p._selected_paths = lambda: [None.path]
    p._do_undo = lambda: None
    p._get_info = lambda: None
    p._context_open = lambda: None
    p._copy_selected = lambda: None
    event = object()
    button = object()
    finder.Finder._popup_context_menu(p, event)
    finder.Finder._popup_background_menu(p, event)
    finder.Finder._popup_actions_menu(p, button)
    (finder.Gtk.Menu, finder.Gtk.MenuItem,
     finder.Gtk.SeparatorMenuItem, finder.nbapp.popup_at) = old
    check("popup recorder sees pointer, pointer, and widget anchors",
          recorded == [{"event": event}, {"event": event},
                       {"widget": button, "anchor": "widget-sw"}])
    print("SKIP GTK display construction; handlers ran through headless recorders")

if failed:
    print("RESULT: SOME FAILED")
    raise SystemExit(1)
print("RESULT: ALL PASS")
