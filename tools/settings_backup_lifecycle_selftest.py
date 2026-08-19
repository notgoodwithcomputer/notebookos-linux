#!/usr/bin/env python3
"""Headless lifecycle checks for Settings' USB backup job."""
import os
import shutil
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-backup-home-"))

import nbjobs  # noqa: E402
import settings  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Bar:
    def __init__(self):
        self.values = []
        self.text = ""

    def set_fraction(self, value):
        self.values.append(value)

    def set_text(self, text):
        self.text = text


class Button:
    def __init__(self):
        self.sensitive = True
        self.visible = True

    def set_sensitive(self, value):
        self.sensitive = value

    def hide(self):
        self.visible = False

    def show(self):
        self.visible = True


class Pane:
    pass


for name in ("_backup_start_job", "_backup_worker", "_backup_sources",
             "_backup_dest_dir", "_backup_progress", "_backup_finished",
             "_backup_crashed", "_backup_stopped", "_backup_failed",
             "_backup_verify", "_backup_cancel", "_backup_close",
             "_start_error", "_copy_error"):
    setattr(Pane, name, getattr(settings.Settings, name))


def make_case(count=30, size=2048):
    root = tempfile.mkdtemp(prefix="nb-backup-case-")
    home = os.path.join(root, "home")
    stick = os.path.join(root, "stick")
    os.makedirs(stick)
    for dirname in settings.BACKUP_DIRS:
        os.makedirs(os.path.join(home, dirname), exist_ok=True)
    for i in range(count):
        path = os.path.join(home, settings.BACKUP_DIRS[0], "f%03d" % i)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
    pane = Pane()
    pane._alive = True
    pane._bk_working = True
    pane._bk_dest = stick
    pane._bk_total = count * size
    pane._bk_bar = Bar()
    pane._bk_btn = Button()
    pane._bk_stop = Button()
    pane.results = []
    pane._update_backup_button = lambda: None
    pane._show_backup_result = lambda text, warn=False: pane.results.append(
        (text, warn))
    pane._bk_clear_status = lambda: None
    pane._backup_sources = lambda: [os.path.join(home,
                                                  settings.BACKUP_DIRS[0])]
    dispatch = nbjobs.ManualDispatcher()
    pane._bk_jobs = nbjobs.JobOwner(dispatch=dispatch, name="backup-test")
    return root, home, stick, pane, dispatch


real_copy2 = shutil.copy2
real_run = settings.run
settings.run = lambda *args, **kwargs: (0, "")
try:
    # Ordinary completion reaches verification and is the only path allowed to
    # say the stick is safe to remove.
    root, home, stick, pane, dispatch = make_case()
    try:
        job = pane._backup_start_job(stick, pane._bk_total)
        check(job is not None and job.join(10), "active backup worker finishes")
        dispatch.drain()
        check(any("Safe to remove" in text for text, _warn in pane.results),
              "verified completion alone says safe to remove")
        check(pane._bk_bar.values == sorted(pane._bk_bar.values),
              "active progress is monotonic")
        check(not pane._bk_bar.values or max(pane._bk_bar.values) <= 1.0,
              "active progress never exceeds 100 percent")
    finally:
        pane._bk_jobs.close()
        shutil.rmtree(root, ignore_errors=True)

    # Explicit Stop is cooperative at the next file boundary. The callback is
    # delivered only after the worker's finally block has flushed the device.
    root, home, stick, pane, dispatch = make_case(80)
    copied = []
    try:
        def stopping_copy(src, dst, *args, **kwargs):
            result = real_copy2(src, dst, *args, **kwargs)
            copied.append(dst)
            if len(copied) == 7:
                pane._backup_cancel()
            return result

        shutil.copy2 = stopping_copy
        job = pane._backup_start_job(stick, pane._bk_total)
        check(job is not None and job.join(10), "cancelled worker reaches a boundary")
        dispatch.drain()
        check(len(copied) == 7, "Stop starts no file after its cancellation boundary")
        check(any("incomplete" in text.lower() for text, _w in pane.results),
              "Stop reports an incomplete backup")
        check(not any("Safe to remove" in text for text, _w in pane.results),
              "cancelled backup never claims verified completion")
    finally:
        shutil.copy2 = real_copy2
        pane._bk_jobs.close()
        shutil.rmtree(root, ignore_errors=True)

    # Destruction cancels and suppresses every queued callback. No invisible
    # copy continues after the owner disappears.
    root, home, stick, pane, dispatch = make_case(80)
    copied = []
    try:
        def closing_copy(src, dst, *args, **kwargs):
            result = real_copy2(src, dst, *args, **kwargs)
            copied.append(dst)
            if len(copied) == 7:
                pane._alive = False
                pane._backup_close()
            return result

        shutil.copy2 = closing_copy
        job = pane._backup_start_job(stick, pane._bk_total)
        check(job is not None and job.join(10), "closed worker reaches a boundary")
        dispatch.drain()
        check(len(copied) == 7, "close starts no further file")
        check(pane.results == [], "closed owner delivers no stale outcome")
    finally:
        shutil.copy2 = real_copy2
        pane._bk_jobs.close()
        shutil.rmtree(root, ignore_errors=True)

    # The worker owns the total captured at start. Rebuilding the pane may
    # reset its displayed measurement, but cannot make an active copy divide by
    # zero or report nonsensical progress.
    root, home, stick, pane, dispatch = make_case(24)
    changed = []
    try:
        def resetting_copy(src, dst, *args, **kwargs):
            result = real_copy2(src, dst, *args, **kwargs)
            if not changed:
                pane._bk_total = 0
                changed.append(True)
            return result

        shutil.copy2 = resetting_copy
        total_at_start = 24 * 2048
        job = pane._backup_start_job(stick, total_at_start)
        check(job is not None and job.join(10), "copy survives pane remeasurement")
        dispatch.drain()
        check(pane._bk_bar.values and pane._bk_bar.values[-1] == 1.0,
              "progress uses the job's captured total through completion")
    finally:
        shutil.copy2 = real_copy2
        pane._bk_jobs.close()
        shutil.rmtree(root, ignore_errors=True)
finally:
    shutil.copy2 = real_copy2
    settings.run = real_run

# Printer test polling is owned by the window. Closing Settings removes the
# exact source, and even a callback already dispatched by GLib is inert.
class PrinterPane:
    _on_destroy = settings.Settings._on_destroy
    _poll_test = settings.Settings._poll_test
    _reset_test_btn = settings.Settings._reset_test_btn


class AudioJobs:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


printer = PrinterPane()
printer._alive = True
printer._audio_jobs = AudioJobs()
printer._pr_test_source = 731
printer._pr_reset_source = 732
printer._dt_source = None
printer._backup_close = lambda: None
printer._save_settings = lambda: None
removed = []
real_source_remove = settings.GLib.source_remove
real_printer_stopped = settings.nbprint.printer_stopped
real_jobs_pending = settings.nbprint.jobs_pending
settings.GLib.source_remove = lambda source: removed.append(source)
settings.nbprint.printer_stopped = lambda _name: (_ for _ in ()).throw(
    AssertionError("closed poll queried printer state"))
settings.nbprint.jobs_pending = lambda _name: (_ for _ in ()).throw(
    AssertionError("closed poll queried jobs"))
try:
    printer._on_destroy()
    check(printer._audio_jobs.closed == 1,
          "close releases the audio job owner exactly once")
    check(removed == [731, 732],
          "close removes printer poll and terminal reset sources")
    check(printer._pr_test_source is None,
          "close clears printer-test source ownership")
    check(printer._pr_reset_source is None,
          "close clears printer-reset source ownership")
    check(printer._poll_test(object(), "Office") is False,
          "dispatched printer-test poll is inert after close")
    check(printer._reset_test_btn(object()) is False,
          "dispatched printer reset is inert after close")
finally:
    settings.GLib.source_remove = real_source_remove
    settings.nbprint.printer_stopped = real_printer_stopped
    settings.nbprint.jobs_pending = real_jobs_pending

print()
if failures:
    print("SETTINGS BACKUP LIFECYCLE SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("SETTINGS BACKUP LIFECYCLE SELFTEST: %d checks, all pass" % checks)
print("RESULT: ALL PASS")
