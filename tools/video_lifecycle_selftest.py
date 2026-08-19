#!/usr/bin/env python3
"""Video editor export-lifecycle regression test (no display, no ffmpeg).

Drives de/video.py's export lifecycle methods on a bare instance, so it runs
anywhere python3 + PyGObject are installed. It does not open a window: every
method exercised here only touches plain attributes.

What it pins down: an export is "in flight" from the moment the user clicks
Export, not from the moment the ffmpeg process exists. Assembling the command
probes every clip with a blocking ffprobe on a worker thread first, so the card
sits on 'Preparing…' with _exp_proc still None. A stray click on the scrim or a
stray Esc in that window used to tear the dialog down and throw the export away
without a word; only the explicit Cancel button may abort.

Usage: python3 tools/video_lifecycle_selftest.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
DE = os.path.normpath(DE)
# appended, never prepended: de/calendar.py would shadow the stdlib module
sys.path.append(DE)

import video  # noqa: E402

FAILED = []


def check(ok, name, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (
        ("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def bare():
    """A VideoEditor with no GTK behind it, in the state the export card is in
    right after Export is clicked: worker thread building the command, no
    ffmpeg process yet."""
    obj = video.VideoEditor.__new__(video.VideoEditor)
    obj._exp_layer = object()      # the Export overlay is open
    obj._exp_proc = None           # ffmpeg has NOT been launched yet
    obj._exp_preparing = False
    obj._exp_build_gen = 0
    obj._exp_poll_id = 0
    obj._exp_errfh = None
    obj._exp_progress_file = None
    obj._exp_err_file = None
    obj._exp_out = None
    obj._exp_done = False
    obj._exp_tmp_imgs = []
    obj._closed = 0
    obj._close_export = lambda: (setattr(obj, "_closed", obj._closed + 1),
                                 True)[1]
    return obj


class FakeProc(object):
    """A subprocess that is still running."""

    returncode = None

    def poll(self):
        return None

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


class FakeEvent(object):
    def __init__(self, keyval):
        self.keyval = keyval
        self.state = 0


def esc(obj):
    """Send Esc through the app's key handler, stopping before the base-class
    handler (which needs a real window)."""
    video.nbapp.AppWindow._on_key = lambda self, w, e: "fell-through"
    return video.VideoEditor._on_key(obj, None,
                                     FakeEvent(video.Gdk.KEY_Escape))


def main():
    print("video export lifecycle selftest")

    # --- the prepare phase counts as in flight -------------------------------
    o = bare()
    o._exp_preparing = True
    check(o._exp_busy() is True, "preparing counts as an export in flight")
    check(o._exp_scrim_press() is True and o._closed == 0,
          "a click outside the card is ignored while Preparing",
          "the export was silently thrown away")
    check(esc(o) is True and o._closed == 0,
          "Esc is ignored while Preparing",
          "the export was silently thrown away")

    # --- and so does the render phase (the guard that already existed) -------
    o = bare()
    o._exp_proc = FakeProc()
    check(o._exp_busy() is True, "a running ffmpeg counts as in flight")
    check(o._exp_scrim_press() is True and o._closed == 0,
          "a click outside the card is ignored while rendering")
    check(esc(o) is True and o._closed == 0, "Esc is ignored while rendering")

    # --- an idle dialog still closes on both ---------------------------------
    o = bare()
    check(o._exp_busy() is False, "an idle export card is not in flight")
    check(o._exp_scrim_press() is True and o._closed == 1,
          "a click outside the card closes an idle export card")
    o = bare()
    check(esc(o) is True and o._closed == 1, "Esc closes an idle export card")

    # --- the flag is not sticky ---------------------------------------------
    o = bare()
    o._exp_preparing = True
    o._exp_build_gen = 4
    o._exp_show_status = lambda *a, **k: None
    o._exp_cleanup_tmp = lambda: None
    o._exp_reset_controls = lambda: None
    o._exp_build_done(4, None, 0, "no")     # the prepare failed
    check(o._exp_busy() is False,
          "a finished prepare clears the in-flight flag",
          "the card could never be closed again")
    check(o._exp_scrim_press() is True and o._closed == 1,
          "the card closes again once the prepare has finished")

    o = bare()
    o._exp_preparing = True
    o._exp_build_gen = 2
    o._exp_show_status = lambda *a, **k: None
    o._exp_cleanup_tmp = lambda: None
    o._exp_build_done(1, ["ffmpeg"], 10, None)   # superseded attempt
    check(o._exp_busy() is True,
          "a superseded prepare does not clear the LIVE attempt's flag")

    # --- explicit Cancel / teardown always wins ------------------------------
    o = bare()
    o._exp_preparing = True
    o._discard_partial_export = lambda: None
    o._export_teardown()
    check(o._exp_busy() is False, "teardown (Cancel, close, destroy) stops it")
    check(o._exp_build_gen == 1,
          "teardown supersedes the pending command-build")

    # --- a failed autosave quarantine must preserve the only original -------
    fd, project = tempfile.mkstemp(prefix="nb-video-store-", suffix=".json")
    os.write(fd, b'{"foreign":"original project bytes"}')
    os.close(fd)
    o = video.VideoEditor.__new__(video.VideoEditor)
    o._quarantine_pending = True
    o._serialize = lambda: {"version": 2, "bin": [], "clips": []}
    old_file = video.PROJECT_FILE
    old_quarantine = o._quarantine_autosave
    old_write = video.nbapp.atomic_write_json
    old_note_failure = video.nbapp.note_save_failure
    writes = []
    video.PROJECT_FILE = project
    o._quarantine_autosave = lambda: False
    video.nbapp.atomic_write_json = lambda *a: writes.append(a)
    # The assertion below is about replacing PROJECT_FILE.  A save failure may
    # also post a notification through the same shared atomic writer; keep that
    # independent side effect from looking like a project overwrite.
    video.nbapp.note_save_failure = lambda *_a, **_k: None
    try:
        o._save_project()
        with open(project, "rb") as fh:
            after = fh.read()
    finally:
        video.PROJECT_FILE = old_file
        o._quarantine_autosave = old_quarantine
        video.nbapp.atomic_write_json = old_write
        video.nbapp.note_save_failure = old_note_failure
        os.unlink(project)
    check(after == b'{"foreign":"original project bytes"}',
          "failed quarantine leaves original autosave bytes untouched")
    check(not writes and o._quarantine_pending,
          "replacement is refused and quarantine remains pending")

    print("")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
