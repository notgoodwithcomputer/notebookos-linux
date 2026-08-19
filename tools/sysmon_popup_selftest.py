#!/usr/bin/env python3
"""Display-free pin for System Monitor's context-menu popup call site.

Task 045 moved the right-click menu off the popup_at_pointer / legacy popup
fallback pair and onto the shared nbapp.popup_at helper, which keeps a menu on
the working screen. This suite pins that call site two ways so a revert fails
in a sandbox with no GTK display:

  * statically: the _on_tree_button AST must contain exactly
    nbapp.popup_at(menu, event=event) and no menu.popup* call, and
  * dynamically: the handler is driven with a stub tree/event and a fake Gtk
    whose menus have NO popup methods, so only the nbapp.popup_at path can
    satisfy the recorded-arguments check.

Run as:
  python3 tools/sysmon_popup_selftest.py
"""
import ast
import os
import sys
import tempfile

DE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "buildroot", "board",
    "notebookos", "rootfs-overlay", "opt", "notebook", "de"))
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbsysmon-popup-")

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


# ---- static pin: the call site itself -----------------------------------
with open(os.path.join(DE, "sysmon.py")) as fh:
    tree = ast.parse(fh.read())

method = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_on_tree_button":
        method = node
        break
check("_on_tree_button exists", method is not None)

popup_at_calls = []
forbidden = []
for node in ast.walk(method) if method else ():
    if not isinstance(node, ast.Call):
        continue
    fn = node.func
    if (isinstance(fn, ast.Attribute) and fn.attr == "popup_at"
            and isinstance(fn.value, ast.Name) and fn.value.id == "nbapp"):
        popup_at_calls.append(node)
    elif (isinstance(fn, ast.Attribute)
          and fn.attr in ("popup", "popup_at_pointer", "popup_at_widget")):
        forbidden.append(fn.attr)

check("exactly one nbapp.popup_at call", len(popup_at_calls) == 1)
if popup_at_calls:
    call = popup_at_calls[0]
    check("positional args are exactly (menu,)",
          len(call.args) == 1 and isinstance(call.args[0], ast.Name)
          and call.args[0].id == "menu")
    check("keywords are exactly event=event",
          len(call.keywords) == 1 and call.keywords[0].arg == "event"
          and isinstance(call.keywords[0].value, ast.Name)
          and call.keywords[0].value.id == "event")
check("no menu.popup* fallback remains", forbidden == [])

# ---- dynamic pin: drive the handler without a display -------------------
import sysmon  # noqa: E402
from gi.repository import Gdk  # noqa: E402


class FakeStyle(object):
    def __init__(self):
        self.classes = []

    def add_class(self, name):
        self.classes.append(name)


class FakeMenu(object):
    """A menu with NO popup/popup_at_pointer: only nbapp.popup_at can show it."""

    def __init__(self):
        self.style = FakeStyle()
        self.items = []
        self.shown = False

    def get_style_context(self):
        return self.style

    def append(self, item):
        self.items.append(item)

    def show_all(self):
        self.shown = True


class FakeItem(object):
    def __init__(self, label=None):
        self.label = label

    def connect(self, *_a, **_k):
        pass


class FakeGtk(object):
    Menu = FakeMenu
    MenuItem = FakeItem
    SeparatorMenuItem = FakeItem


class FakeSelection(object):
    def __init__(self):
        self.selected = None

    def select_path(self, path):
        self.selected = path


class FakeTree(object):
    def __init__(self):
        self.selection = FakeSelection()

    def get_path_at_pos(self, x, y):
        return ("row-path", None, 0, 0)

    def grab_focus(self):
        pass

    def get_selection(self):
        return self.selection


class FakeEvent(object):
    button = 3
    type = Gdk.EventType.BUTTON_PRESS
    x = 5.0
    y = 5.0


class StubApp(object):
    _on_tree_button = sysmon.SystemMonitor._on_tree_button

    def _end_process(self):
        pass

    def _copy_pid(self):
        pass


calls = []
real_gtk = sysmon.Gtk
real_popup_at = sysmon.nbapp.popup_at
sysmon.Gtk = FakeGtk
sysmon.nbapp.popup_at = lambda *a, **k: calls.append((a, k))
try:
    event = FakeEvent()
    ret = StubApp()._on_tree_button(FakeTree(), event)
finally:
    sysmon.Gtk = real_gtk
    sysmon.nbapp.popup_at = real_popup_at

check("handler returns True", ret is True)
check("nbapp.popup_at called exactly once", len(calls) == 1)
if calls:
    args, kwargs = calls[0]
    check("called with the menu positionally",
          len(args) == 1 and isinstance(args[0], FakeMenu))
    check("called with event=event and nothing else",
          kwargs == {"event": event})
    check("menu was show_all()ed before popping",
          isinstance(args[0], FakeMenu) and args[0].shown)

print("OVERALL: " + ("PASS" if ok else "FAIL"))
print("RESULT: " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
