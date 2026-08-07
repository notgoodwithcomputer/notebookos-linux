#!/usr/bin/env python3
"""The desktop board must rebuild ONCE per save, not once per monitor event.

    python3 tools/widgets_smoothness_selftest.py        (no DISPLAY needed)

The board watches nine shared stores with Gio file monitors. One save is never
one event: an app writes through nbapp.atomic_write_json (temp file + rename),
which Gio reports as a run of events on the watched path (DELETED, CREATED,
CHANGED, ATTRIBUTE_CHANGED, CHANGES_DONE_HINT), and the polling backend adds
more. When each of those rebuilt the board directly, a single edit tore down
and rebuilt all eight cards several times over -- and ticking a task on the
desktop wrote tasks.json, whose own monitor came straight back and rebuilt the
list under the pointer, undoing the in-place restyle _toggle_task exists to do.

So this drives the coalescing path itself (a real GLib main loop, which needs
no display) and pins the source-level rule that a monitor never calls _reload
directly. Nothing here opens a window: the real functions are borrowed off the
Widgets class onto a plain object, because the bug is in the timer wiring, not
in the widget tree.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Run from the repo (against the overlay sources) or ON THE GUEST (against what
# actually shipped), like the rest of the board suites.
_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
                                 "notebookos", "rootfs-overlay", "opt",
                                 "notebook", "de")),
    "/opt/notebook/de",
]
DE = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[0])
if DE not in sys.path:
    sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbhome-smooth-")

import gi                                                   # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib                              # noqa: E402

import widgets                                              # noqa: E402

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


check("the board has a coalescing reload", hasattr(widgets.Widgets,
                                                   "_queue_reload"))
if not hasattr(widgets.Widgets, "_queue_reload"):
    print("FAIL: monitor events still rebuild the board directly")
    raise SystemExit(1)


class _Board(object):
    """The board's reload plumbing on a plain object.

    The REAL functions, borrowed off the class -- not copies of them, so this
    cannot pass against a widgets.py that has changed. Widgets itself is not
    instantiated: it is a GtkWindow, and even __new__ constructs the GObject,
    which needs a display connection this test deliberately does not have. The
    rebuild steps _reload runs are stubbed; what is under test is the timer
    around them."""
    _queue_reload = widgets.Widgets.__dict__["_queue_reload"]
    _reload_now = widgets.Widgets.__dict__["_reload_now"]
    _cancel_reload = widgets.Widgets.__dict__["_cancel_reload"]
    _reload = widgets.Widgets.__dict__["_reload"]
    _safe = widgets.Widgets.__dict__["_safe"]

    def __init__(self):
        self._reload_pending = 0
        self.steps = []
        for name in ("_load_stores", "_rebuild_tasks", "_rebuild_calendar",
                     "_rebuild_tiles"):
            setattr(self, name, lambda n=name: self.steps.append(n))


def bare():
    return _Board()


def settle(ms):
    """Run the main loop for `ms`, so pending timeouts actually fire."""
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
    loop.run()


WINDOW = getattr(widgets, "_RELOAD_COALESCE_MS", 180)

# --- one save's worth of events is ONE rebuild -----------------------------
board = bare()
runs = []
board._reload = lambda: runs.append(1)
for _ in range(12):                     # a burst across several monitors
    board._queue_reload()
check("no rebuild while the burst is still arriving", not runs)
settle(WINDOW + 220)
check("a burst of 12 events rebuilds the board once", len(runs) == 1)

# ...and the board is not wedged afterwards: a LATER change still lands.
board._queue_reload()
settle(WINDOW + 220)
check("a later change still rebuilds", len(runs) == 2)

# --- the coalescing timeout never outlives its reason ----------------------
# A rebuild that happens for another reason (the desktop home returning, a day
# rollover) makes a queued one redundant; leaving it armed would rebuild the
# whole board a second time for nothing.
board2 = bare()
board2._queue_reload()
check("a rebuild was queued", bool(board2._reload_pending))
board2._reload()                        # the real one; its steps are _safe
check("reloading drops the queued rebuild", board2._reload_pending == 0)

runs2 = []
board3 = bare()
board3._reload = lambda: runs2.append(1)
board3._queue_reload()
board3._cancel_reload()                 # what "destroy" does
check("cancelling clears the source id", board3._reload_pending == 0)
settle(WINDOW + 220)
check("a cancelled rebuild never fires", not runs2)

# ...and cancelling twice (destroy after a reload) must not touch a dead source.
board3._cancel_reload()
check("cancelling twice is harmless", board3._reload_pending == 0)

# --- source-level: no monitor may call _reload on the spot -----------------
with open(os.path.join(DE, "widgets.py")) as fh:
    src = fh.read()
mon_lines = [ln.strip() for ln in src.splitlines()
             if "mon.connect" in ln or "_flag.monitor_file" in ln
             or "self._store_monitors.append" in ln]
connect = "\n".join(ln for ln in src.splitlines()
                    if 'mon.connect("changed"' in ln
                    or (ln.strip().startswith("lambda *_a")
                        and "_queue_reload" in ln))
check("store monitors queue a rebuild, never run one",
      "_queue_reload" in connect and "self._reload()" not in connect)
check("the monitor wiring was found at all", bool(mon_lines))

print("OK" if ok else "FAILED")
raise SystemExit(0 if ok else 1)
