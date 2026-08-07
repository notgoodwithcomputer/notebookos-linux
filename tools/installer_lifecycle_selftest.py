#!/usr/bin/env python3
"""installer_lifecycle_selftest — the installer's threading, not its output.

    python3 tools/installer_lifecycle_selftest.py

Display-free: it never constructs a window. Every object here is a real
Installer built with __new__ and the handful of attributes the code under test
touches, exactly as installer_target_selftest.py does for the write path.

The regression that started this file: arriving at the Summary step ran the
root-PARTUUID probe INLINE on the GTK main loop.

    elif key == "summary":
        self._partuuid_other = self._partuuid_clash()      # lsblk, right here

_partuuid_clash shells out to `lsblk -Pn`, which walks every block device on
the machine, and run_cmd gives it eight seconds. A disk that has spun down (or
a stuck USB stick — the kind of machine somebody is most likely to be
reinstalling) holds that call for the full timeout, and it ran BEFORE the stack
switched pages: the click on Next had already been taken, so the wizard sat
frozen on the Options step, repainting nothing, until lsblk answered. The disk
enumeration on the target step was moved onto a worker thread for precisely
this reason; this probe was the one left behind.

The probe now runs on a worker thread and posts its answer back, so this file
also checks the two things a thread makes possible and a direct call could not
get wrong: an answer that arrives after the user has moved on, and an answer
about a disk the user has since changed away from. Neither may be shown.
"""
import inspect
import os
import sys
import threading
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

FAILURES = []
CHECKS = [0]

# How long a "slow lsblk" is allowed to hold the caller before we call the main
# loop blocked. run_cmd's own timeout is 8s; anything above a fraction of a
# second here is a freeze the user sees.
BLOCKED_AFTER = 0.5


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(what)
        print("  FAIL  %s" % what)
    return bool(cond)


class Idle:
    """Stands in for GLib.idle_add: records what the worker posted so the test
    can run it on the main thread at the moment of its choosing."""

    def __init__(self):
        self.posted = []

    def add(self, fn, *args):
        self.posted.append((fn, args))
        return 1

    def deliver(self):
        """Run everything posted so far, as the GTK main loop would."""
        pending, self.posted = self.posted, []
        for fn, args in pending:
            fn(*args)
        return len(pending)


def blank_installer(installer, disk="/dev/sdb"):
    inst = installer.Installer.__new__(installer.Installer)
    inst.tools = {"lsblk": "/bin/lsblk"}
    inst.cfg = {"disk": disk}
    inst._closed = False
    inst._scan_gen = 0
    inst._clash_gen = 0
    inst._paint_source = 0
    inst._pulse_source = 0
    inst._pulse_on = False
    inst._partuuid_other = ""
    inst._refreshed = [0]
    inst._refresh_summary = lambda: inst._refreshed.__setitem__(
        0, inst._refreshed[0] + 1)
    return inst


class SlowLsblk:
    """A probe that blocks until it is released, and remembers which thread
    called it."""

    def __init__(self, out):
        self.out = out
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.threads = []

    def __call__(self, _argv, timeout=8):
        self.threads.append(threading.current_thread())
        self.entered.set()
        self.gate.wait(timeout)
        return 0, self.out

    def release(self):
        self.gate.set()


def rows(installer, uuid_disk):
    """lsblk -Pn output in which <uuid_disk> already holds a Notebook OS root."""
    return ('NAME="%s" PARTUUID="" TYPE="disk" PKNAME=""\n'
            'NAME="%s2" PARTUUID="%s" TYPE="part" PKNAME="%s"\n'
            'NAME="sdb" PARTUUID="" TYPE="disk" PKNAME=""\n'
            % (uuid_disk, uuid_disk, installer.ROOT_PARTUUID, uuid_disk))


# ------------------------------------------------------------------ the tests
def test_summary_arrival_never_probes_inline(installer):
    """The static half: the step change itself must not shell out.

    _set_step runs on the main loop with the user's click still in hand, and
    everything else it does (rail state, page switch, footer) is cheap. A
    subprocess in that method is the freeze, whatever it is called."""
    print("-- arriving at the Summary step does not run lsblk on the main loop")
    src = inspect.getsource(installer.Installer._set_step)
    check("self._partuuid_clash(" not in src,
          "_set_step must not call _partuuid_clash() itself — the probe belongs "
          "on a worker thread")
    check("_refresh_summary" in src,
          "_set_step still draws the Summary on arrival")
    # ...and the probe it does start hands the work off rather than doing it.
    probe = inspect.getsource(installer.Installer._start_clash_probe)
    check("threading.Thread" in probe,
          "the PARTUUID probe is started on a worker thread")


def test_probe_does_not_block_the_caller(installer):
    print("-- a slow lsblk does not hold the step change")
    inst = blank_installer(installer)
    idle = Idle()
    slow = SlowLsblk(rows(installer, "sda"))
    orig_cmd, orig_glib = installer.run_cmd, installer.GLib
    installer.run_cmd = slow
    installer.GLib = types.SimpleNamespace(idle_add=idle.add)
    main = threading.current_thread()
    try:
        t0 = time.time()
        inst._start_clash_probe()
        elapsed = time.time() - t0
        check(elapsed < BLOCKED_AFTER,
              "the step change returns at once (%.2fs, budget %.2fs)"
              % (elapsed, BLOCKED_AFTER))
        check(slow.entered.wait(5) is True, "the probe really did start")
        check(all(t is not main for t in slow.threads),
              "lsblk ran off the main thread: %r"
              % [t.name for t in slow.threads])
        check(inst._partuuid_other == "",
              "nothing is claimed about the disk while the answer is pending")
        slow.release()
        for _ in range(100):                      # let the worker post its idle
            if idle.posted:
                break
            time.sleep(0.02)
        check(idle.deliver() == 1, "the answer is posted back to the main loop")
        check(inst._partuuid_other == "/dev/sda",
              "and it is the right answer: %r" % inst._partuuid_other)
        check(inst._refreshed[0] == 1,
              "the Summary is redrawn once the warning is known")
    finally:
        slow.release()
        installer.run_cmd, installer.GLib = orig_cmd, orig_glib


def test_stale_answers_are_dropped(installer):
    print("-- an answer that arrives too late is not shown")
    orig_glib = installer.GLib
    idle = Idle()
    installer.GLib = types.SimpleNamespace(idle_add=idle.add)
    try:
        # a. Superseded: the user went back and returned to the Summary, so a
        # newer probe owns the page. The old one must not paint over it.
        inst = blank_installer(installer)
        inst._clash_gen = 1
        inst._apply_clash(1, "/dev/sdb", "/dev/sda")
        check(inst._partuuid_other == "/dev/sda",
              "the current probe's answer is applied")
        inst._partuuid_other = ""
        inst._clash_gen = 2                       # a later visit took over
        inst._apply_clash(1, "/dev/sdb", "/dev/sda")
        check(inst._partuuid_other == "",
              "a superseded probe's answer is dropped: %r"
              % inst._partuuid_other)

        # b. The answer is about a disk the user has since changed away from.
        # Naming the wrong disk in the erase confirmation is the one mistake
        # this whole screen exists to prevent.
        inst = blank_installer(installer, disk="/dev/sdc")
        inst._clash_gen = 1
        inst._apply_clash(1, "/dev/sdb", "/dev/sda")
        check(inst._partuuid_other == "",
              "an answer about a disk no longer chosen is dropped: %r"
              % inst._partuuid_other)
        check(inst._refreshed[0] == 0,
              "and it does not redraw the Summary either")

        # c. Starting a probe clears the previous visit's answer, so a stale
        # warning cannot stand while the new one is in flight.
        inst = blank_installer(installer, disk=None)
        inst._partuuid_other = "/dev/sda"
        inst._start_clash_probe()
        check(inst._partuuid_other == "",
              "with no disk chosen, the old warning is cleared and no probe "
              "runs: %r" % inst._partuuid_other)
        inst = blank_installer(installer)
        inst.tools = {}                           # no lsblk on this image
        inst._partuuid_other = "/dev/sda"
        inst._start_clash_probe()
        check(inst._partuuid_other == "",
              "with no lsblk, nothing is claimed: %r" % inst._partuuid_other)
    finally:
        installer.GLib = orig_glib


def test_probe_asks_about_the_disk_it_was_given(installer):
    """The worker is handed its target, so a probe already in flight cannot
    change which disk it is about when the user picks another one."""
    print("-- the probe is about the disk it was started for")
    orig = installer.run_cmd
    installer.run_cmd = lambda _a, timeout=8: (0, rows(installer, "sda"))
    try:
        inst = blank_installer(installer, disk="/dev/sdb")
        check(inst._partuuid_clash("/dev/sdb") == "/dev/sda",
              "a clash on another disk is found for the target passed in")
        check(inst._partuuid_clash("/dev/sda") == "",
              "installing over the clashing disk itself is not a clash")
        # The no-argument form still reads the current choice, because
        # installer_target_selftest and _clash_line's callers use it.
        check(inst._partuuid_clash() == "/dev/sda",
              "the no-argument form still answers for the chosen disk")
    finally:
        installer.run_cmd = orig


def test_window_owned_callbacks(installer):
    print("-- deferred UI work belongs to the live Installer window")
    inst = blank_installer(installer)
    inst._scan_gen, inst._clash_gen = 4, 7
    inst._paint_source, inst._pulse_source = 41, 42
    inst._pulse_on = True
    events = []
    original_glib = installer.GLib
    installer.GLib = types.SimpleNamespace(
        source_remove=lambda sid: events.append((sid, inst._closed)))
    try:
        first, second = inst._on_destroy(), inst._on_destroy()
    finally:
        installer.GLib = original_glib
    check(first is False and second is False and inst._closed,
          "destroy is idempotent and raises the closed gate")
    check(events == [(41, True), (42, True)],
          "destroy cancels owned sources only after raising the gate")
    check((inst._scan_gen, inst._clash_gen) == (5, 8),
          "destroy invalidates disk and PARTUUID probe generations")
    check((inst._paint_source, inst._pulse_source, inst._pulse_on)
          == (0, 0, False), "destroy clears paint and pulse ownership")

    # Results already dispatched by GLib still reach their Python sink. The
    # closed gate must precede every widget/model access.
    inst._refreshed[0] = 0
    check(inst._apply_clash(8, "/dev/sdb", "/dev/sda") is False
          and inst._partuuid_other == "" and inst._refreshed[0] == 0,
          "a closed PARTUUID result is inert")
    check(inst._populate_disks(5, []) is False,
          "a closed disk-scan result is inert")


class FakeProgress:
    def __init__(self):
        self.pulses = 0
        self.fraction = None
        self.text = None

    def pulse(self): self.pulses += 1
    def set_fraction(self, value): self.fraction = value
    def set_text(self, text): self.text = text


class FakeLabel:
    def __init__(self): self.text = None
    def set_text(self, text): self.text = text


def test_progress_source_ownership(installer):
    print("-- progress pulse has one owner and stops synchronously")
    inst = blank_installer(installer)
    inst._prog_bar, inst._prog_status = FakeProgress(), FakeLabel()
    removed = []
    original_glib = installer.GLib
    installer.GLib = types.SimpleNamespace(
        timeout_add=lambda _ms, _fn: 51,
        source_remove=lambda sid: removed.append(sid))
    try:
        check(inst._begin_pulse("Copying files") is False
              and inst._pulse_source == 51 and inst._pulse_on,
              "begin-pulse owns its repeating timer")
        check(inst._pulse_tick() is True and inst._prog_bar.pulses == 2,
              "a live pulse tick updates the bar and continues")
        check(inst._apply_progress(0.5, "Half way") is False,
              "a determinate update is delivered")
    finally:
        installer.GLib = original_glib
    check(removed == [51] and inst._pulse_source == 0 and not inst._pulse_on,
          "determinate progress cancels the pulse immediately")
    check(inst._prog_bar.fraction == 0.5 and inst._prog_status.text == "Half way",
          "determinate progress retains its existing UI semantics")
    inst._closed, inst._pulse_on, inst._pulse_source = True, True, 52
    before = inst._prog_bar.pulses
    check(inst._pulse_tick() is False and inst._pulse_source == 0
          and inst._prog_bar.pulses == before,
          "a closed dispatched pulse unregisters without touching widgets")


def main():
    os.environ.setdefault("NB_HOME", "/tmp")
    import installer
    test_summary_arrival_never_probes_inline(installer)
    test_probe_does_not_block_the_caller(installer)
    test_stale_answers_are_dropped(installer)
    test_probe_asks_about_the_disk_it_was_given(installer)
    test_window_owned_callbacks(installer)
    test_progress_source_ownership(installer)
    print()
    if FAILURES:
        print("INSTALLER LIFECYCLE SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        return 1
    print("INSTALLER LIFECYCLE SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
