#!/usr/bin/env python3
"""finder_eject_selftest — does Eject keep the Finder answering?

    python3 tools/finder_eject_selftest.py

WHY THIS FILE EXISTS
Eject ran `sync` and `umount` straight from the sidebar button's clicked
handler. Both take as long as the drive takes — flushing a file that was just
copied to a slow stick is tens of seconds — and for all of it the main loop was
inside subprocess.run: no redraw, no answer to the window manager, no hint that
anything was happening. The one operation on this machine whose entire purpose
is to protect the user's files looked like a hang, which invites exactly the
yank it exists to prevent.

The commands now run on a worker thread and report back through the main loop,
so this checks the two halves of that: the click RETURNS while the drive is
still flushing, and the answer still lands (sidebar rebuilt, status written)
once it finishes. No display is needed — the window is never constructed; only
the eject path is driven, against a stubbed subprocess.
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/"
                        "notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_LANG", "en")
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbfinder-eject-")

import gi                                                      # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib                                 # noqa: E402

import finder                                                  # noqa: E402

CHECKS = [0]
FAILURES = []
SYNC_SECONDS = 0.6          # stands in for a real drive's write-out
MOUNT = "/media/usbstick"


def check(cond, what):
    CHECKS[0] += 1
    if cond:
        print("  ok   %s" % what)
    else:
        FAILURES.append(what)
        print("  FAIL %s" % what)


class FakeCompleted(object):
    returncode = 0
    stderr = b""


class StubFinder(finder.Finder):
    """The eject path only, with no window: __init__ is skipped and the few
    attributes _eject touches are supplied by hand."""

    def __init__(self):                                        # noqa: D107
        self.rel = ""
        self._ejecting = set()
        self.calls = []
        self.threads = []
        self.status_text = None
        self.sidebar_fills = 0

    def abspath(self, rel):
        return os.path.join(os.environ["NB_HOME"], rel)

    def _flash_status(self, msg, restore_ms=2400):
        self.status_text = msg

    def _fill_sidebar(self):
        self.sidebar_fills += 1


def fake_run(argv, **kw):
    WIN.calls.append(argv[0])
    WIN.threads.append(threading.current_thread())
    if argv[0] == "sync":
        time.sleep(SYNC_SECONDS)
    return FakeCompleted()


def pump(seconds):
    """Turn the main loop for a while, as a live Finder's would turn."""
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def main():
    global WIN
    WIN = StubFinder()
    finder.subprocess.run = fake_run

    print("\n-- the click comes back before the drive does")
    t0 = time.time()
    WIN._eject(MOUNT)
    click = time.time() - t0
    check(click < SYNC_SECONDS / 2,
          "the handler returns in %.3fs, while a %.1fs flush is still running "
          "— on the old code it returned only when the drive was done, and the "
          "window was frozen for every second of it" % (click, SYNC_SECONDS))
    check(WIN.status_text is None and WIN.sidebar_fills == 0,
          "and it has not yet claimed a result it cannot know")

    print("\n-- a second click while it is flushing is not a second unmount")
    WIN._eject(MOUNT)
    check(WIN.calls.count("umount") == 0 and len(WIN.threads) <= 1,
          "the in-flight drive is left alone: %r" % (WIN.calls,))

    print("\n-- the answer still arrives, on the main loop")
    pump(SYNC_SECONDS + 1.0)
    check(WIN.calls == ["sync", "umount"],
          "both commands ran, in order: %r" % (WIN.calls,))
    check(all(t is not threading.main_thread() for t in WIN.threads),
          "...off the main thread, which is the whole point")
    check(WIN.status_text == finder._t("Safe to remove the drive"),
          "the status bar says the drive can be pulled: %r" % WIN.status_text)
    check(WIN.sidebar_fills == 1,
          "and Devices is rebuilt once, so the ejected drive is gone from it "
          "(got %d rebuilds)" % WIN.sidebar_fills)
    check(MOUNT not in WIN._ejecting,
          "the drive is releasable again, so a re-inserted stick can eject")

    print("\n-- a refused unmount still reports")
    WIN.status_text = None
    WIN.calls = []
    class Busy(object):
        returncode = 1
        stderr = b"umount: /media/usbstick: target is busy"
    finder.subprocess.run = lambda argv, **kw: (
        WIN.calls.append(argv[0]) or Busy() if argv[0] == "umount"
        else WIN.calls.append(argv[0]) or FakeCompleted())
    WIN._eject(MOUNT)
    pump(1.0)
    # A busy volume is the COMMON, actionable failure: the message says what to
    # do, and — the point of this check — the raw "/media/usbstick: target is
    # busy" stderr never reaches the UI either way.
    raw_out = ("/media/usbstick" in (WIN.status_text or "")
               or "umount" in (WIN.status_text or ""))
    check(not raw_out and "in use" in (WIN.status_text or "")
          and "eject" in (WIN.status_text or ""),
          "busy eject is actionable and leaks no raw stderr: %r"
          % WIN.status_text)

    print()
    if FAILURES:
        print("FINDER EJECT SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        for f in FAILURES:
            print("   - %s" % f)
        return 1
    print("FINDER EJECT SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
