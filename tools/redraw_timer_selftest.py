#!/usr/bin/env python3
"""Static gate on GLib timer churn in the shared shell/app layer.

A handler wired to a HIGH-FREQUENCY GTK signal (pointer motion, scroll, draw)
must not add or remove GLib sources: on this software-rendered, compositor-less
stack every motion event that tears a timeout down and builds a new one is main
loop work paid for out of the same budget as the panel's own repaints. Nothing
else can see this -- py_compile is green on it, and no runtime selftest drives a
pointer across a menu -- so the churn ships as "the panel feels heavy" with no
failing gate anywhere.

Checks, over de/shell.py and de/nbapp.py only:
  1. no handler connected to a high-frequency signal creates/removes a GLib
     source (the bug class);
  2. shell.py's menu idle timeout is a SELF-RE-ARMING source (_menu_idle), not
     one restarted per motion event, and it still closes the menu.

Usage: python3 tools/redraw_timer_selftest.py
"""
import ast
import os
import sys

DE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
FILES = ["shell.py", "nbapp.py"]

# signals that fire many times a second while a person simply moves the pointer
HOT = {"motion-notify-event", "motion_notify_event", "scroll-event",
       "scroll_event", "draw", "enter-notify-event", "leave-notify-event"}
# GLib main-context source calls that must not appear on a hot path
SOURCE_CALLS = {"timeout_add", "timeout_add_seconds", "idle_add",
                "source_remove", "add_tick_callback"}


def _attr_path(node):
    """"GLib.timeout_add" / "self._menu_close" for an attribute expression."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _methods(tree):
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _source_calls_in(fn):
    """Names of GLib source add/remove calls made in a function body."""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            path = _attr_path(node.func)
            if path.split(".")[-1] in SOURCE_CALLS and path.startswith("GLib"):
                out.append((path, node.lineno))
    return out


bad = []
for name in FILES:
    path = os.path.join(DE, name)
    tree = ast.parse(open(path, encoding="utf-8").read(), name)
    methods = _methods(tree)
    hot_handlers = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and _attr_path(node.func).endswith("connect")
                and len(node.args) >= 2):
            continue
        sig = node.args[0]
        if not (isinstance(sig, ast.Constant) and sig.value in HOT):
            continue
        handler = _attr_path(node.args[1]).split(".")[-1]
        fn = methods.get(handler)
        if fn is None:                 # lambda or handler defined elsewhere
            continue
        hot_handlers += 1
        for call, lineno in _source_calls_in(fn):
            bad.append("%s L%d: %s() on the %r handler %s() -- a GLib source "
                       "per pointer event" % (name, lineno, call, sig.value,
                                              handler))
    print("%-10s %d hot-signal handler(s) checked" % (name, hot_handlers))

# --- shell.py menu idle timer must re-arm itself -------------------------
shell = ast.parse(open(os.path.join(DE, "shell.py"), encoding="utf-8").read(),
                  "shell.py")
sm = _methods(shell)
idle = sm.get("_menu_idle")
if idle is None:
    bad.append("shell.py: no _menu_idle() -- the menu idle timeout is not a "
               "self-re-arming source")
else:
    calls = [_attr_path(n.func) for n in ast.walk(idle) if isinstance(n, ast.Call)]
    if not any(c.endswith("timeout_add_seconds") for c in calls):
        bad.append("shell.py: _menu_idle() never re-arms itself")
    if "self._menu_close" not in calls:
        bad.append("shell.py: _menu_idle() never closes the menu -- an idle "
                   "menu would keep the whole-screen input shape")
    if not any(isinstance(n, ast.Attribute) and n.attr == "_menu_active_at"
               for n in ast.walk(idle)):
        bad.append("shell.py: _menu_idle() ignores _menu_active_at -- it would "
                   "close a menu someone is still using")
act = sm.get("_menu_activity")
if act is None or not any(isinstance(n, ast.Attribute)
                          and n.attr == "_menu_active_at"
                          for n in ast.walk(act)):
    bad.append("shell.py: _menu_activity() does not stamp _menu_active_at")

for line in bad:
    print("TIMER-CHURN " + line)
print("clean" if not bad else "%d timer-churn issue(s)" % len(bad))
sys.exit(1 if bad else 0)
