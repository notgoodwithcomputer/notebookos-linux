#!/usr/bin/env python3
"""Headless checks for Maps' synchronous no-pack startup state.

The last line of Maps.__init__ used to be GLib.idle_add(self._show_empty) with
the source id thrown away, although self.canvas and self._empty both already
exist by then. That bought a first frame with self._empty still None — a blank
canvas, and the notice explaining why only afterwards — and left an idle source
holding the callback, so a window closed inside that turn of the loop ran
_show_empty against a destroyed canvas with nobody able to remove the source.

No Gtk.Window is built: _show_empty runs against a bare Maps.__new__ instance
carrying a recording stand-in for the canvas, and the startup path is read off
the parsed syntax tree rather than grepped for.
"""
import ast
import inspect
import json
import os
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
# Pinned BEFORE maps is imported, because nbi18n resolves the language once at
# import. fr is a language whose catalog carries both empty-state strings, so
# copy that came back in English would mean _show_empty had skipped _t().
os.environ["NB_LANG"] = "fr"
import maps  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Canvas:
    def __init__(self):
        self.draws = 0

    def queue_draw(self):
        self.draws += 1


# Read straight from the catalog rather than through maps._t: comparing _t's
# output against _t's output would pass just as happily on hard-coded English.
with open(os.path.join(DE, "lang_fr.json")) as fh:
    CATALOG = json.load(fh)
EXPECTED = (CATALOG["No maps"],
            CATALOG["Map files are read from the Maps folder in Home."])

# Given nothing but the two fields it is entitled to touch, so reaching for a
# third raises here instead of passing quietly.
app = maps.Maps.__new__(maps.Maps)
app.canvas = Canvas()
app._empty = None
result = app._show_empty()
check(result is False, "the empty-state sink remains a one-shot callback")
check(app._empty == EXPECTED,
      "the actionable translated empty-state copy is installed")
check(app.canvas.draws == 1, "installing the empty state requests one redraw")

tree = ast.parse(textwrap.dedent(inspect.getsource(maps.Maps.__init__)))
calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def is_show_empty_ref(node):
    return (isinstance(node, ast.Attribute) and node.attr == "_show_empty"
            and isinstance(node.value, ast.Name) and node.value.id == "self")


def is_sync_call(stmt):
    """True for the bare statement `self._show_empty()`."""
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
            and is_show_empty_ref(stmt.value.func)
            and not stmt.value.args and not stmt.value.keywords)


# Handing _show_empty to ANY scheduler is the defect, not idle_add specifically:
# a timeout, a connect or a thread target would strand it just the same.
deferred = [call for call in calls
            if any(is_show_empty_ref(arg)
                   for arg in list(call.args) + [kw.value
                                                 for kw in call.keywords])]
idle_adds = [call for call in calls
             if isinstance(call.func, ast.Attribute)
             and call.func.attr == "idle_add"
             and getattr(call.func.value, "id", None) == "GLib"]
direct = [node for node in ast.walk(tree) if is_sync_call(node)]
check(not deferred, "Maps startup hands _show_empty to no scheduler")
check(not idle_adds, "Maps startup arms no GLib.idle_add source at all")
check(len(direct) == 1,
      "the startup path installs its state synchronously, exactly once")

# ...and in the NO-MAPS branch: a synchronous call sited anywhere in __init__
# would satisfy the count above while still leaving the empty window blank.
branches = [node for node in ast.walk(tree)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Attribute)
            and node.test.attr == "maps"
            and isinstance(node.test.value, ast.Name)
            and node.test.value.id == "self"]
check(len(branches) == 1, "Maps startup branches once on `self.maps`")
check(bool(branches) and any(is_sync_call(stmt) for stmt in branches[0].orelse),
      "the no-map branch is the one that installs the empty state")

print("\n%d checks, %d failed" % (checks, len(failures)))
sys.exit(1 if failures else 0)
