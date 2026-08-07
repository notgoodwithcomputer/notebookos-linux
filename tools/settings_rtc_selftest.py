#!/usr/bin/env python3
"""
Set Clock has to survive a restart.

`_apply_datetime` ran `date -s` and stopped. That moves the RUNNING clock only;
the battery-backed clock on the board is untouched, and on x86 that is what the
kernel reads back at boot (`read_persistent_clock64` -> the CMOS, which the fork
still has in arch/x86/kernel/rtc.c). So every restart threw the setting away.
With no networking anywhere in this OS there is no NTP to correct it afterwards,
and the Calendar, Journal, Tasks and Bill Tracker all date their records from
that clock (ROADMAP #15).

The ROADMAP recorded it as unfixable — *"no `hwclock` in the tree"*. There is
one: busybox provides it (`CONFIG_HWCLOCK=y`) and the built image has it at
`/sbin/hwclock -> ../bin/busybox`. The premise was wrong, not the symptom.

`run` is stubbed rather than actually setting the machine's clock — a test that
really called `date -s` would move the developer's system time and, on a machine
where it succeeded, leave it moved. What is asserted is the pair of commands the
app issues and, more importantly, **what it tells the user when the second one
fails**: the time IS set, it just will not survive a restart, and those are two
different sentences.

Run:
    tools/guestrun.sh python3 tools/settings_rtc_selftest.py
    tools/guestrun.sh python3 tools/settings_rtc_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-rtc-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import settings as S  # noqa: E402

FAILED, N = [], [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump(n=300):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


class Recorder:
    """Stand in for settings.run, recording every command and choosing a
    return code per program."""

    def __init__(self, rcs=None):
        self.calls = []
        self.rcs = rcs or {}

    def __call__(self, cmd, timeout=4):
        self.calls.append(list(cmd))
        prog = cmd[0] if cmd else ""
        return self.rcs.get(prog, 0), ""

    def issued(self, prog):
        return [c for c in self.calls if c and c[0] == prog]


def open_datetime(app):
    """Build the Date & Time page so its widgets exist."""
    page = app._page_datetime()
    pump()
    return page


def main():
    real_run = S.run
    app = S.Settings()
    pump()

    # There is deliberately NO check that _apply_datetime survives being called
    # before its page exists. Settings builds pages lazily, and this method is
    # reachable only from the Set Clock button, which the page itself creates --
    # so the state cannot occur. An earlier draft asserted it, failed on
    # self._cal, and the "fix" would have been defensive code for an
    # unreachable path. `self._dt_status` is still declared in __init__ because
    # _set_status already treats None as "nowhere to report", and one line
    # recording which page owns the widget is worth having.
    open_datetime(app)

    # ---- 1. the happy path issues BOTH commands ----------------------
    rec = Recorder()
    S.run = rec
    app._apply_datetime()
    pump()
    dated = check("it sets the running clock (date -s)", bool(rec.issued("date")))
    wrote = check("it writes the battery-backed clock (hwclock)",
                  bool(rec.issued("hwclock")))
    if dated and wrote:
        di = next(i for i, c in enumerate(rec.calls) if c[0] == "date")
        hi = next(i for i, c in enumerate(rec.calls) if c[0] == "hwclock")
        check("the hardware clock is written AFTER the system clock", di < hi)
        check("hwclock is told to copy system -> hardware",
              any("-w" in c or "--systohc" in c for c in rec.issued("hwclock")))
    else:
        not_reached("one of the two commands was never issued",
                    "the hardware clock is written AFTER the system clock",
                    "hwclock is told to copy system -> hardware")
    ok_msg = app._dt_status.get_text()
    check("it confirms (%r)" % ok_msg, bool(ok_msg))

    # ---- 2. no RTC: the time IS set, and the message says so ---------
    rec = Recorder(rcs={"hwclock": 1})
    S.run = rec
    app._apply_datetime()
    pump()
    warn = app._dt_status.get_text()
    check("a failed hwclock still reports the clock as set (%r)" % warn[:52],
          "set" in warn.lower())
    check("and says it will not survive a restart",
          "restart" in warn.lower() or "again" in warn.lower())
    check("the two outcomes do not say the same thing", warn != ok_msg)

    # ---- 3. date itself failing is a different sentence again --------
    rec = Recorder(rcs={"date": 1})
    S.run = rec
    app._apply_datetime()
    pump()
    bad = app._dt_status.get_text()
    check("a failed date -s says the clock was NOT set (%r)" % bad[:52],
          bad not in (ok_msg, warn))
    check("and it does not go on to write the hardware clock",
          not rec.issued("hwclock"))

    S.run = real_run
    try:
        app.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
