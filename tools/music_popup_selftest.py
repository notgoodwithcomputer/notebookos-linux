#!/usr/bin/env python3
"""Display-free pin for Music's add-to-playlist popup call site.

Task 045 moved the + button's playlist menu off the popup_at_widget / legacy
popup fallback pair and onto the shared nbapp.popup_at helper. Because
_on_add_clicked wraps everything in a swallow-all try (a row click must never
crash), a silent revert would pass any test that merely calls the handler —
so this suite pins the call site two ways, both display-free:

  * statically: the _on_add_clicked AST must contain exactly
    nbapp.popup_at(menu, widget=button, anchor="widget-sw") and no
    menu.popup* call, and
  * dynamically: the handler is driven with a fake Gtk whose menus have NO
    popup methods, and the check asserts nbapp.popup_at was actually reached
    with the exact arguments — a revert leaves the recorder empty.

Run as:
  python3 tools/music_popup_selftest.py
"""
import ast
import os
import sys
import tempfile

DE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "buildroot", "board",
    "notebookos", "rootfs-overlay", "opt", "notebook", "de"))
sys.path.insert(0, DE)
# ASSIGNED, not setdefault: importing music with NB_HOME unset (or inherited
# from guestrun.sh) walks the developer's real home for a library scan.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbmusic-popup-")

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


# ---- static pin: the call site itself -----------------------------------
with open(os.path.join(DE, "music.py")) as fh:
    tree = ast.parse(fh.read())

method = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_on_add_clicked":
        method = node
        break
check("_on_add_clicked exists", method is not None)

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
    kw = {k.arg: k.value for k in call.keywords}
    check("keywords are exactly widget=button, anchor='widget-sw'",
          len(call.keywords) == 2
          and isinstance(kw.get("widget"), ast.Name)
          and kw["widget"].id == "button"
          and isinstance(kw.get("anchor"), ast.Constant)
          and kw["anchor"].value == "widget-sw")
check("no menu.popup* fallback remains", forbidden == [])
check("outer safety try still wraps the handler",
      method is not None and any(isinstance(n, ast.Try) for n in method.body))

# ---- dynamic pin: drive the handler without a display -------------------
import music  # noqa: E402


class FakeStyle(object):
    def add_class(self, name):
        pass


class FakeMenu(object):
    """A menu with NO popup/popup_at_widget: only nbapp.popup_at can show it."""

    def __init__(self):
        self.items = []
        self.shown = False

    def get_style_context(self):
        return FakeStyle()

    def append(self, item):
        self.items.append(item)

    def show_all(self):
        self.shown = True


class FakeItem(object):
    def __init__(self, label=None):
        self.label = label

    def set_sensitive(self, flag):
        pass

    def connect(self, *_a, **_k):
        pass

    def get_child(self):
        return None

    def set_label(self, text):
        self.label = text


class FakeLabel(object):
    """The menu item's own AccelLabel child. _set_user_text stamps the item AND
    its child (a Gtk.MenuItem keeps its text there, and the show_all walk
    descends into it), and it decides with isinstance(child, Gtk.Label) — so a
    fake Gtk without a Label attribute raised AttributeError inside the
    handler's outer try, which swallowed it and left popup_at unreached. The
    fake has to carry every attribute of the module surface the handler
    actually touches, or it tests the fake."""

    def __init__(self, label=None):
        self.label = label


class FakeGtk(object):
    Menu = FakeMenu
    MenuItem = FakeItem
    SeparatorMenuItem = FakeItem
    Label = FakeLabel


class StubApp(object):
    _on_add_clicked = music.Music._on_add_clicked
    _playlists = ["Mix 1"]
    _playlist_tracks = {"Mix 1": []}

    def _add_to_playlist(self, song, name):
        pass

    def _add_to_new_playlist(self, song):
        pass


calls = []
button = object()
song = object()
real_gtk = music.Gtk
real_popup_at = music.nbapp.popup_at
music.Gtk = FakeGtk
music.nbapp.popup_at = lambda *a, **k: calls.append((a, k))
try:
    StubApp()._on_add_clicked(button, song)
finally:
    music.Gtk = real_gtk
    music.nbapp.popup_at = real_popup_at

check("nbapp.popup_at was reached exactly once (the outer try must not have "
      "swallowed a fallback path)", len(calls) == 1)
if calls:
    args, kwargs = calls[0]
    check("called with the menu positionally",
          len(args) == 1 and isinstance(args[0], FakeMenu))
    check("called with widget=button, anchor='widget-sw' and nothing else",
          kwargs == {"widget": button, "anchor": "widget-sw"})
    check("menu was show_all()ed before popping",
          isinstance(args[0], FakeMenu) and args[0].shown)

print("OVERALL: " + ("PASS" if ok else "FAIL"))
print("RESULT: " + ("PASS" if ok else "FAILED"))
sys.exit(0 if ok else 1)
