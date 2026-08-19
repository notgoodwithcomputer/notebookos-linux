#!/usr/bin/env python3
"""Display-free regression test for System Monitor's End Program identity check.

The bug this pins down: a program's ID is unique only while that program is
alive. End Program raises a MODAL confirmation card and does nothing until the
user clicks it, which can be minutes later. If the chosen program finishes in
that window the kernel may hand its ID to something new, and the SIGTERM would
land on a program the user never picked — silently ending, say, the desktop or
a document with unsaved work.

sysmon pins the target by (ID, start time) at the moment the card is raised and
re-checks the start time before signalling. This suite drives that logic
directly on a stub object, so it needs no X display and never opens a window.

Run as:
  PYTHONPATH=<...>/rootfs-overlay/opt/notebook/de python3 sysmon_selftest.py
"""
import os
import subprocess
import sys
import tempfile
import time

DE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "buildroot",
                  "board", "notebookos", "rootfs-overlay", "opt", "notebook",
                  "de")
sys.path.insert(0, os.path.normpath(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbsysmon-"))

import sysmon  # noqa: E402

ok = True

check_state_cases = (("R", True), ("S", True), ("Z", False),
                     ("X", False), ("x", False))


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


for state, expected in check_state_cases:
    check("process state %s has honest actionability" % state,
          sysmon.actionable_proc_state(state) is expected)


class Stub(object):
    """The bare surface _do_end touches: no Gtk, no window, no display."""
    _do_end = sysmon.SystemMonitor._do_end

    def __init__(self):
        self.said = []

    def _flash(self, msg, secs=6):
        self.said.append(msg)


def spawn():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    for _ in range(200):            # wait for /proc to have it
        if sysmon.proc_start_time(p.pid) is not None:
            break
        time.sleep(0.01)
    return p


# ---- proc_start_time ----------------------------------------------------
mine = sysmon.proc_start_time(os.getpid())
check("start time of a live program reads", mine is not None and mine.isdigit())
check("start time is stable across reads",
      sysmon.proc_start_time(os.getpid()) == mine)
check("start time of a nonexistent ID is None",
      sysmon.proc_start_time(4194305) is None)
check("start time of a junk ID is None", sysmon.proc_start_time("nope") is None)

# ---- the recycled-ID case: must NOT signal ------------------------------
# Stand in for "the chosen program finished and its ID was reissued": a live ID
# whose start time is not the one recorded when the card was raised. The target
# is this very test process, so a regression here would SIGTERM the suite —
# os.kill is stubbed out so the check is provable without that.
victim = sysmon.SystemMonitor._do_end.__globals__["os"]
signalled = []
real_kill = victim.kill
victim.kill = lambda pid, sig: signalled.append((pid, sig))
try:
    s = Stub()
    s._do_end(os.getpid(), "Writer", "999999999")
    check("a reissued ID is never signalled", signalled == [])
    check("and the user is told the program had finished",
          len(s.said) == 1 and "Writer" in s.said[0]
          and "finished" in s.said[0].lower())

    # None is not a stable identity: two failed /proc reads must never compare
    # equal and authorize a signal to a potentially recycled PID.
    s = Stub()
    s._do_end(4194305, "Gone", None)
    check("an unidentified ID is never signalled", signalled == [])
finally:
    victim.kill = real_kill

# ---- the honest case: an unchanged program IS ended ---------------------
p = spawn()
started = sysmon.proc_start_time(p.pid)
check("a spawned program has a start time", started is not None)
s = Stub()
s._do_end(p.pid, "Sleeper", started)
check("an unchanged program is ended", p.wait(timeout=10) is not None)
check("and the footer says it is ending",
      len(s.said) == 1 and "Sleeper" in s.said[0])

# ---- an ID that died between the card and the click ---------------------
p = spawn()
started = sysmon.proc_start_time(p.pid)
p.kill()
p.wait(timeout=10)
s = Stub()
s._do_end(p.pid, "Ghost", started)
check("an already-finished program is reported, not re-signalled",
      len(s.said) == 1 and "Ghost" in s.said[0])

# ---- the 2s table refresh must not rewrite rows that have not changed ---
# The old refresh rebuilt the model wholesale (clear() + append) on every tick,
# so an idle machine still rewrote every cell of every row twice a second and
# reset everything the model anchors. _sync_store folds a tick's rows into the
# existing model instead. A Gtk.ListStore is a plain model object, so this needs
# no display and opens no window; the store is built exactly as sysmon builds
# its own (cols 4/5 are the hidden numeric sort keys).
from gi.repository import Gtk, GObject  # noqa: E402


class StoreStub(object):
    _sync_store = sysmon.SystemMonitor._sync_store

    def __init__(self, sort_col=4, order=Gtk.SortType.DESCENDING):
        self.store = Gtk.ListStore(str, int, str, str,
                                   GObject.TYPE_INT64, GObject.TYPE_DOUBLE, str)
        self.store.set_sort_column_id(sort_col, order)
        self.changed = []
        self.deleted = []
        self.store.connect("row-changed", lambda m, p, i:
                           self.changed.append(m[p][1]))
        self.store.connect("row-deleted", lambda m, p: self.deleted.append(1))

    def pids(self):
        return [r[1] for r in self.store]

    def quiet(self):
        self.changed = []
        self.deleted = []


def row(name, pid, rss, pct, started=None):
    return (name, pid, sysmon.human_kb(rss), "%.0f%%" % pct, rss, pct,
            started or "start-%s" % pid)


tick1 = [row("Writer", 101, 40000, 3.0),
         row("Desktop", 102, 90000, 1.0),
         row("Music", 103, 20000, 0.0)]
s = StoreStub()
s._sync_store(tick1)
check("a first sync fills the table", sorted(s.pids()) == [101, 102, 103])
check("and it is sorted by memory, busiest first", s.pids() == [102, 101, 103])

# An idle tick: identical figures must touch nothing at all.
s.quiet()
s._sync_store(tick1)
check("an unchanged tick rewrites no row", s.changed == [])
check("an unchanged tick deletes no row", s.deleted == [])
check("and the table is untouched", s.pids() == [102, 101, 103])

# One program's figures move: only that row may be written.
s.quiet()
tick2 = [row("Writer", 101, 40000, 3.0),
         row("Desktop", 102, 90000, 1.0),
         row("Music", 103, 25000, 8.0)]
s._sync_store(tick2)
# (a row whose memory moved writes both the formatted cell and its hidden sort
# key, so the count of writes is not 1 — the claim under test is that no OTHER
# row is written, which the old clear()+append path could never satisfy.)
check("a moving figure rewrites only its own row", set(s.changed) == {103})
check("and the new value is in the model",
      dict((r[1], r[4]) for r in s.store)[103] == 25000)

# A row's identity survives a tick — this is what the selection, the keyboard
# cursor and the scroll offset all hang off, and what clear() used to destroy.
before = dict((r[1], r.iter.user_data) for r in s.store)
s._sync_store(tick2)
after = dict((r[1], r.iter.user_data) for r in s.store)
check("rows keep their identity across a tick", before == after)

# Reusing a PID is a different row identity, never an in-place rewrite of the
# stale selected program.
old_iter = next(r.iter.user_data for r in s.store if r[1] == 101)
s._sync_store([row("Other", 101, 40000, 3.0, "replacement-birth")])
new_iter = next(r.iter.user_data for r in s.store if r[1] == 101)
check("a reused PID replaces the row identity", old_iter != new_iter)

# Programs that start and finish are still picked up.
s.quiet()
s._sync_store([row("Writer", 101, 40000, 3.0),
               row("Terminal", 104, 10000, 0.0)])
check("a finished program leaves the table", sorted(s.pids()) == [101, 104])
check("a new program enters the table", 104 in s.pids())
s._sync_store([])
check("an empty tick empties the table", s.pids() == [])

print("\n%s" % ("ALL PASS" if ok else "FAILURES"))
sys.exit(0 if ok else 1)
