#!/usr/bin/env python3
"""
Headless CHILD-LIFECYCLE selftest for the Terminal (de/terminal.py).

Covers the one piece of shell-process state the app keeps by hand:
_pending_spawn, the guard that tells "the shell you are looking at has exited"
apart from "a New Session deliberately terminated the old shell".  It is raised
by _start_new_session() and lowered by the VTE spawn callback _spawned().

The regression: when the respawn never reaches that callback -- spawn_async
raising synchronously on an unusable pty/cwd, or the terminal widget being gone
-- nothing lowered the guard, so it latched on for the life of the window.
_on_child_exited() then returned early for ever: the window stopped closing
when its shell exited, and stayed open with no shell in it and no way back to
the Finder except the menu.

It also covers the other piece of hand-kept state on the same paths: ownership
of the 250ms one-shot _spawned() arms to clear VTE's startup notice.  That
callback outlives the call that scheduled it, so the window has to be able to
retire it -- otherwise a window destroyed inside those 250ms leaves a timeout
that fires against a dead VTE widget, and a repeated spawn stacks a second
cleanup on top of the first.  The cases below pin the ownership rules: the
source id is dropped by whichever of dispatch / replacement / destroy gets
there first, a closed window neither arms nor feeds, and destroy raises the
closed gate before it cancels or saves and is safe to deliver twice.

Display-free and static: no Gtk.Window is constructed and no process is
spawned.  The Terminal instance is built with __new__ (so __init__ never runs)
and driven against a stub terminal widget, which is also what lets the failing
spawn be reproduced deterministically.

Run as:
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  python3 tools/terminal_lifecycle_selftest.py
"""
import os
import sys

DE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "buildroot", "board", "notebookos", "rootfs-overlay", "opt", "notebook",
    "de")
if DE_DIR not in sys.path:
    sys.path.insert(0, DE_DIR)

from gi.repository import GLib          # noqa: E402
import terminal                          # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%-58s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok and detail:
        print("    | " + detail)


class StubTerm(object):
    """The handful of Vte.Terminal calls the session paths touch."""

    def __init__(self, spawn_error=None):
        self.spawn_error = spawn_error
        self.spawn_calls = 0
        self.fed = []
        self.reset_calls = 0

    def spawn_async(self, *_a, **_k):
        self.spawn_calls += 1
        if self.spawn_error is not None:
            raise self.spawn_error

    def reset(self, *_a):
        self.reset_calls += 1

    def feed(self, data):
        self.fed.append(data)

    def grab_focus(self):
        pass


def make_app(term):
    """A Terminal with no window behind it: __init__ is skipped, so only the
    child-lifecycle attributes exist and only those paths can run."""
    app = terminal.Terminal.__new__(terminal.Terminal)
    app.term = term
    app._child_pid = None          # no real pid: _start_new_session sends no
    app._pending_spawn = False     # signal, so nothing on this host is killed
    app._closed = False
    app._startup_notice_source = 0
    app.closed = 0
    app._confirmed = True
    # self.close() is the behaviour under test; _confirm() would want a dialog.
    app.close = lambda *_a: setattr(app, "closed", app.closed + 1)
    app._confirm = lambda *_a: app._confirmed
    return app


def main():
    # 1. A New Session whose respawn raises synchronously must leave the guard
    #    down -- this is the regression itself.
    term = StubTerm(spawn_error=GLib.Error("pty unavailable"))
    app = make_app(term)
    app._start_new_session()
    check("failed respawn clears _pending_spawn", app._pending_spawn is False,
          "guard latched on after spawn_async raised")
    check("failed respawn shows a notice", any(b"could not be started" in f or
                                               b"No shell" in f
                                               for f in term.fed))

    # 2. ...and the consequence: the window must still close when a shell exit
    #    arrives afterwards, instead of being ignored for ever.
    app._on_child_exited()
    check("window still closes after a failed respawn", app.closed == 1,
          "child-exited ignored: %d close() calls" % app.closed)

    # 3. Same for the other early return out of _spawn_shell.
    app = make_app(None)
    app._pending_spawn = True
    app._spawn_shell()
    check("_spawn_shell with no widget clears the guard",
          app._pending_spawn is False)

    # 4. The guard must still do its job: while a deliberate respawn is in
    #    flight, the old shell's exit must NOT close the window.
    term = StubTerm()
    app = make_app(term)
    app._child_pid = 999999
    app._start_new_session()
    check("in-flight respawn holds the guard up", app._pending_spawn is True)
    app._on_child_exited()
    check("old shell's exit does not close the window", app.closed == 0,
          "window closed during a New Session")

    # 5. The spawn callback lowers the guard and records the new shell, so a
    #    later exit of THAT shell closes the window.
    app._spawned(term, os.getpid(), None)
    check("_spawned lowers the guard", app._pending_spawn is False)
    check("_spawned records the new pid", app._child_pid == os.getpid())
    # Unpatched, so this is the real GLib path: the window must be holding the
    # id it was handed. Retire it again so the test leaves no timeout behind.
    check("_spawned owns the real startup-cleanup source",
          app._startup_notice_source != 0)
    if app._startup_notice_source:
        GLib.source_remove(app._startup_notice_source)
        app._startup_notice_source = 0

    # 6. A spawn callback carrying an error is a dead session, not a pending
    #    one: the guard comes down there too.
    app._pending_spawn = True
    app._spawned(term, -1, GLib.Error("exec failed"))
    check("_spawned(error) lowers the guard", app._pending_spawn is False)

    # 7. The delayed startup cleanup is owned by the window. Its live sink
    # clears ownership and feeds once; a dispatched sink after close is inert.
    app = make_app(StubTerm())
    fed = []
    app._feed_child = lambda data: fed.append(data)
    app._startup_notice_source = 71
    check("live startup cleanup unregisters",
          app._clear_startup_notice() is False and
          app._startup_notice_source == 0)
    check("live startup cleanup feeds once", fed == [b"\x0c"])
    app._closed = True
    app._startup_notice_source = 72
    check("closed startup cleanup is inert",
          app._clear_startup_notice() is False and
          app._startup_notice_source == 0 and fed == [b"\x0c"])

    # 8. A later successful spawn replaces the prior delayed cleanup rather
    # than leaving two callbacks racing to clear the same terminal -- while a
    # spawn callback landing after the window is gone arms nothing at all.
    # GLib is patched on the terminal module (and restored) so the scheduling
    # itself can be read back without running a main loop.
    app = make_app(StubTerm())
    gone = make_app(StubTerm())
    gone._closed = True
    removed = []
    scheduled = []
    real_remove = terminal.GLib.source_remove
    real_add = terminal.GLib.timeout_add
    terminal.GLib.source_remove = lambda source_id: removed.append(source_id)
    terminal.GLib.timeout_add = lambda delay, fn, *_a: (
        scheduled.append((delay, fn)), 82)[1]
    app._startup_notice_source = 81
    try:
        app._spawned(app.term, 123, None)
        gone._spawned(gone.term, 124, None)
    finally:
        terminal.GLib.source_remove = real_remove
        terminal.GLib.timeout_add = real_add
    check("successful spawn replaces prior startup cleanup",
          removed == [81] and app._startup_notice_source == 82)
    check("replacement keeps the 250ms one-shot",
          scheduled == [(250, app._clear_startup_notice)],
          "scheduled: %r" % (scheduled,))
    check("spawn after close arms nothing",
          gone._startup_notice_source == 0 and
          all(fn != gone._clear_startup_notice for _d, fn in scheduled),
          "a closed window scheduled: %r" % (scheduled,))
    check("spawn after close still records the child",
          gone._child_pid == 124 and gone._pending_spawn is False)

    # 9. Destruction raises the gate before cancellation/save, owns the source
    # ID to zero, and a repeated destroy delivery is harmless.
    app = make_app(StubTerm())
    app._startup_notice_source = 91
    events = []
    app._save_prefs = lambda: events.append(("save", app._closed))
    real_remove = terminal.GLib.source_remove
    terminal.GLib.source_remove = lambda source_id: events.append(
        ("remove-%d" % source_id, app._closed))
    try:
        first = app._on_destroy()
        second = app._on_destroy()
    finally:
        terminal.GLib.source_remove = real_remove
    check("destroy returns False and clears startup ownership",
          first is False and second is False and
          app._startup_notice_source == 0)
    check("destroy cancels then saves exactly once behind closed gate",
          events == [("remove-91", True), ("save", True)])

    ok = all(RESULTS)
    print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
