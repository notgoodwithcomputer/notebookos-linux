#!/usr/bin/env python3
"""Headless ownership tests plus real-X clipboard persistence round trips."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/de/xclipd.py")
spec = importlib.util.spec_from_file_location("xclipd", DAEMON)
xclipd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xclipd)

FAILED = []


def check(name, condition, detail=""):
    print("PASS: " + name if condition else "FAIL: " + name
          + (" -- " + detail if detail else ""))
    if not condition:
        FAILED.append(name)


def headless():
    print("== HEADLESS ==")
    core = xclipd.ClipboardCore()
    check("external copy requests a snapshot",
          core.event("owner-appeared") == xclipd.TAKE_SNAPSHOT)
    core.event("snapshot-taken", True)
    check("dead owner makes daemon serve its snapshot",
          core.event("owner-vanished") == xclipd.ASSERT_OWNERSHIP)
    check("the daemon's ownership echo is ignored",
          core.event("self-owner-change") == xclipd.IGNORE
          and not core.self_claim_pending)

    core = xclipd.ClipboardCore()
    core.event("owner-appeared")
    core.event("snapshot-taken", True)
    core.event("owner-vanished")
    core.event("self-owner-change")
    check("a new legitimate live owner is only snapshotted",
          core.event("owner-appeared") == xclipd.TAKE_SNAPSHOT
          and core.owner_live)
    check("a snapshot event never asserts over that live owner",
          core.event("snapshot-taken", True) == xclipd.IGNORE
          and core.owner_live)

    oversized = "€" * (xclipd.TEXT_CAP // 3 + 10)
    capped = xclipd.cap_text(oversized)
    check("oversize text is UTF-8 capped",
          len(capped.encode("utf-8")) <= xclipd.TEXT_CAP
          and len(capped) < len(oversized))
    check("oversize decoded images are rejected",
          not xclipd.image_fits(4096, 4096, 4))
    check("images at the decoded cap are accepted",
          xclipd.image_fits(2048, 2048, 4))
    marker = "manager-probe-marker"
    check("a surviving manager probe selects display skip",
          clipboard_manager_present(marker, marker))
    check("a dead manager probe keeps display tests enabled",
          not clipboard_manager_present("", marker))

    with tempfile.TemporaryDirectory(prefix="xclipd-lock-") as td:
        path = os.path.join(td, "daemon.lock")
        first = xclipd.acquire_instance_lock(path)
        second = xclipd.acquire_instance_lock(path)
        check("a held lock refuses a second instance",
              first is not None and second is None)
        if first is not None:
            os.close(first)

    # Exercise the real startup import path without requiring a display.  The
    # daemon previously imported Gtk and Gdk but passed an undefined GLib to
    # ClipboardDaemon, so every graphical session lost clipboard persistence.
    fake_gtk = types.SimpleNamespace(
        init_check=lambda: (True, []), main=lambda: None,
        main_quit=lambda: None)
    fake_gdk = object()
    fake_glib = object()
    fake_repository = types.ModuleType("gi.repository")
    fake_repository.Gtk = fake_gtk
    fake_repository.Gdk = fake_gdk
    fake_repository.GLib = fake_glib
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_args: None
    fake_gi.repository = fake_repository
    old_gi = sys.modules.get("gi")
    old_repository = sys.modules.get("gi.repository")
    old_lock = xclipd.acquire_instance_lock
    old_daemon = xclipd.ClipboardDaemon
    old_signal = xclipd.signal.signal
    old_close = xclipd.os.close
    constructed = []
    try:
        sys.modules["gi"] = fake_gi
        sys.modules["gi.repository"] = fake_repository
        xclipd.acquire_instance_lock = lambda: 71
        xclipd.ClipboardDaemon = lambda Gtk, Gdk, GLib: constructed.append(
            (Gtk, Gdk, GLib))
        xclipd.signal.signal = lambda *_args: None
        xclipd.os.close = lambda _fd: None
        xclipd.main()
    finally:
        xclipd.acquire_instance_lock = old_lock
        xclipd.ClipboardDaemon = old_daemon
        xclipd.signal.signal = old_signal
        xclipd.os.close = old_close
        if old_gi is None:
            sys.modules.pop("gi", None)
        else:
            sys.modules["gi"] = old_gi
        if old_repository is None:
            sys.modules.pop("gi.repository", None)
        else:
            sys.modules["gi.repository"] = old_repository
    check("graphical startup supplies Gtk, Gdk, and GLib to the daemon",
          constructed == [(fake_gtk, fake_gdk, fake_glib)],
          repr(constructed))

    class TargetEntry:
        @staticmethod
        def new(name, _flags, info):
            return name, info

    class FakeClipboard:
        def __init__(self):
            self.targets = []
            self.stored = False

        def set_with_data(self, targets, getter, clearer, data):
            self.targets = targets
            self.getter = getter
            return True

        def set_can_store(self, targets):
            self.can_store = targets

        def store(self):
            self.stored = True

    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.Gtk = type("Gtk", (), {"TargetEntry": TargetEntry})
    daemon.clipboard = FakeClipboard()
    daemon.core = xclipd.ClipboardCore()
    daemon.text, daemon.image = "fallback text", object()
    daemon._serve()
    names = {name for name, _info in daemon.clipboard.targets}
    check("mixed clipboard snapshots advertise text and image together",
          "UTF8_STRING" in names and "image/png" in names
          and daemon.clipboard.stored)

    class RefusingClipboard(FakeClipboard):
        def set_with_data(self, *_args):
            raise RuntimeError("X selection claim failed")

    refusing = object.__new__(xclipd.ClipboardDaemon)
    refusing.Gtk = daemon.Gtk
    refusing.clipboard = RefusingClipboard()
    refusing.core = xclipd.ClipboardCore()
    refusing.text, refusing.image = "still copied", None
    check("a failed clipboard ownership claim is contained",
          refusing._serve() is False)
    check("a failed claim cannot poison the next owner-change event",
          refusing.core.self_claim_pending is False)

    class AsyncClipboard:
        def __init__(self):
            self.requests = []

        def request_text(self, callback, generation):
            self.requests.append(("text", callback, generation))

        def request_image(self, callback, generation):
            self.requests.append(("image", callback, generation))

    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.clipboard = AsyncClipboard()
    daemon.core = xclipd.ClipboardCore()
    daemon.core.event("owner-appeared")
    daemon.text = daemon.image = None
    daemon._snapshot_generation = 0
    daemon._snapshot_pending = {}
    daemon.core.event("snapshot-taken", True)
    daemon._snapshot()
    check("an incomplete replacement keeps the last good clipboard snapshot",
          daemon.core.have_snapshot)
    first = list(daemon.clipboard.requests)
    daemon._snapshot()
    second = daemon.clipboard.requests[2:]
    for kind, callback, generation in second:
        callback(None, "new" if kind == "text" else None, generation)
    for kind, callback, generation in first:
        callback(None, "stale" if kind == "text" else None, generation)
    check("late clipboard replies cannot overwrite a newer snapshot",
          daemon.text == "new")

    # A short-lived owner may disappear before its already-queued async replies
    # run. Those replies are still valid and must beat the older snapshot.
    class FakeGLib:
        callbacks = []
        removed = []

        @classmethod
        def timeout_add(cls, _delay, callback, generation):
            cls.callbacks.append((callback, generation))
            return 91

        @classmethod
        def source_remove(cls, source_id):
            cls.removed.append(source_id)

    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.clipboard = AsyncClipboard()
    daemon.GLib = FakeGLib
    daemon.core = xclipd.ClipboardCore()
    daemon.core.event("owner-appeared")
    daemon.core.event("snapshot-taken", True)  # prior complete owner A
    daemon.text, daemon.image = "old", None
    daemon._snapshot_generation = 0
    daemon._snapshot_pending = {}
    daemon._serve_generation = 0
    daemon._snapshot_deadline_id = 0
    daemon.in_handler = False
    served = []
    daemon._serve = lambda: served.append((daemon.text, daemon.image))
    daemon._snapshot()                         # owner B copy requests queued
    requests = list(daemon.clipboard.requests)
    event = type("Event", (), {"owner": None})()
    daemon._owner_change(None, event)          # B exits before callbacks run
    for kind, callback, generation in requests:
        callback(None, "new" if kind == "text" else None, generation)
    check("queued replies from a just-closed owner replace stale clipboard data",
          daemon.text == "new" and served == [("new", None)],
          repr((daemon.text, served)))
    check("completing the vanished owner's snapshot cancels its fallback timer",
          FakeGLib.removed == [91] and daemon._snapshot_deadline_id == 0,
          repr(FakeGLib.removed))

    FakeGLib.callbacks, FakeGLib.removed = [], []
    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.clipboard = AsyncClipboard()
    daemon.GLib = FakeGLib
    daemon.core = xclipd.ClipboardCore()
    daemon.core.event("owner-appeared")
    daemon.core.event("snapshot-taken", True)
    daemon.text, daemon.image = "old", None
    daemon._snapshot_generation = 0
    daemon._snapshot_pending = {}
    daemon._serve_generation = 0
    daemon._snapshot_deadline_id = 0
    daemon.in_handler = False
    served = []
    daemon._serve = lambda: served.append((daemon.text, daemon.image))
    daemon._snapshot()
    requests = list(daemon.clipboard.requests)
    daemon._owner_change(None, event)
    deadline, generation = FakeGLib.callbacks[-1]
    check("a vanished owner that never replies falls back to the prior snapshot",
          deadline(generation) is False and served == [("old", None)],
          repr(served))
    for kind, callback, generation in requests:
        callback(None, "too late" if kind == "text" else None, generation)
    check("replies after the fallback deadline cannot overwrite clipboard data",
          daemon.text == "old" and served == [("old", None)],
          repr((daemon.text, served)))

    # Text may arrive while the image target never answers. The grace deadline
    # must preserve that completed new format, not revert the whole clipboard.
    FakeGLib.callbacks, FakeGLib.removed = [], []
    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.clipboard = AsyncClipboard()
    daemon.GLib = FakeGLib
    daemon.core = xclipd.ClipboardCore()
    daemon.core.event("owner-appeared")
    daemon.core.event("snapshot-taken", True)
    daemon.text, daemon.image = "old", None
    daemon._snapshot_generation = 0
    daemon._snapshot_pending = {}
    daemon._serve_generation = 0
    daemon._snapshot_deadline_id = 0
    daemon.in_handler = False
    served = []
    daemon._serve = lambda: served.append((daemon.text, daemon.image))
    daemon._snapshot()
    requests = list(daemon.clipboard.requests)
    daemon._owner_change(None, event)
    text_request = next(request for request in requests if request[0] == "text")
    text_request[1](None, "new partial", text_request[2])
    deadline, generation = FakeGLib.callbacks[-1]
    check("a completed text reply survives when the image request hangs",
          deadline(generation) is False and daemon.text == "new partial"
          and served == [("new partial", None)],
          repr((daemon.text, served)))

    # A new external owner can race ahead of the daemon's own owner-change
    # echo. It still has to be snapshotted rather than consumed as that echo.
    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.core = xclipd.ClipboardCore()
    daemon.core.have_snapshot = True
    daemon.core.self_claim_pending = True
    daemon._serve_generation = 0
    daemon._snapshot_deadline_id = 0
    daemon.in_handler = False
    daemon.GLib = FakeGLib
    snapshots = []
    daemon._snapshot = lambda: snapshots.append(True)
    daemon._owner_change(None, type("Event", (), {"owner": object()})())
    check("an external owner racing the daemon echo is still snapshotted",
          snapshots == [True] and daemon.core.owner_live,
          repr((snapshots, daemon.core.owner_live)))

    # Unsupported-only owner B completes during grace: keep and serve A.
    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.clipboard = AsyncClipboard()
    daemon.GLib = FakeGLib
    daemon.core = xclipd.ClipboardCore()
    daemon.core.event("owner-appeared")
    daemon.core.event("snapshot-taken", True)
    daemon.text, daemon.image = "old", None
    daemon._snapshot_generation = 0
    daemon._snapshot_pending = {}
    daemon._serve_generation = 0
    daemon._snapshot_deadline_id = 0
    daemon.in_handler = False
    served = []
    daemon._serve = lambda: served.append((daemon.text, daemon.image))
    daemon._snapshot()
    requests = list(daemon.clipboard.requests)
    daemon._owner_change(None, event)
    for _kind, callback, generation in requests:
        callback(None, None, generation)
    check("unsupported-only clipboard content keeps the last usable snapshot",
          daemon.text == "old" and daemon.core.have_snapshot
          and served == [("old", None)], repr((daemon.text, served)))

    # The very first copy has no older snapshot, so owner-vanished cannot set
    # the core's fallback flag. Completion of that vanished generation must
    # still claim the newly captured content.
    daemon = object.__new__(xclipd.ClipboardDaemon)
    daemon.clipboard = AsyncClipboard()
    daemon.GLib = FakeGLib
    daemon.core = xclipd.ClipboardCore()
    daemon.core.event("owner-appeared")
    daemon.text = daemon.image = None
    daemon._snapshot_generation = 0
    daemon._snapshot_pending = {}
    daemon._serve_generation = 0
    daemon._snapshot_deadline_id = 0
    daemon.in_handler = False
    served = []
    daemon._serve = lambda: served.append((daemon.text, daemon.image))
    daemon._snapshot()
    requests = list(daemon.clipboard.requests)
    daemon._owner_change(None, event)
    for kind, callback, generation in requests:
        callback(None, "first" if kind == "text" else None, generation)
    check("the first async copy survives its owner closing during capture",
          daemon.text == "first" and daemon.core.have_snapshot
          and served == [("first", None)], repr((daemon.text, served)))


GTK_PROGRAM = r'''
import sys, gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
if not Gtk.init_check()[0]: raise SystemExit(77)
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
mode = sys.argv[1]
if mode == "set-text": cb.set_text(sys.argv[2] if len(sys.argv) > 2 else "persist-me-041", -1)
elif mode == "set-image":
    p = Gdk.pixbuf_get_from_surface(__import__("cairo").ImageSurface(
        __import__("cairo").FORMAT_ARGB32, 8, 8), 0, 0, 8, 8)
    p.fill(0x336699ff); cb.set_image(p)
elif mode == "read-text":
    print(cb.wait_for_text() or ""); raise SystemExit(0)
elif mode == "read-image":
    p = cb.wait_for_image(); print("8x8" if p and p.get_width() == 8 and p.get_height() == 8 else "empty"); raise SystemExit(0)
print("READY", flush=True)
GLib.timeout_add(350, Gtk.main_quit); Gtk.main()
'''


def gtk_run(mode, timeout=4, text_value=None):
    argv = [sys.executable, "-c", GTK_PROGRAM, mode]
    if text_value is not None:
        argv.append(text_value)
    return subprocess.run(argv,
                          text=True, capture_output=True, timeout=timeout)


def put_then_read(kind):
    owner = subprocess.Popen([sys.executable, "-c", GTK_PROGRAM, "set-" + kind],
                             text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    ready = owner.stdout.readline().strip()
    owner.wait(timeout=4)
    time.sleep(.2)
    reader = gtk_run("read-" + kind)
    return ready, reader.stdout.strip()


def clipboard_manager_present(value, marker):
    return value == marker


def probe_clipboard_manager(snapshot):
    marker = "xclipd-manager-probe-%d-%d" % (os.getpid(), time.time_ns())
    try:
        owner = gtk_run("set-text", text_value=marker)
        time.sleep(.2)
        value = gtk_run("read-text").stdout.strip()
        return owner.stdout.strip() == "READY" and clipboard_manager_present(
            value, marker)
    finally:
        # Best-effort text-only restoration; an existing manager will retain it.
        gtk_run("set-text", text_value=snapshot)


def display():
    print("\n== DISPLAY ==")
    probe = gtk_run("read-text")
    if probe.returncode == 77:
        print("SKIP: no display available")
        return

    snapshot = probe.stdout.rstrip("\n")
    # The control's "no daemon means death" assumption describes the DISPLAY,
    # not xclipd, so establish it instead of assuming no other manager exists.
    if probe_clipboard_manager(snapshot):
        print("SKIP: a clipboard manager already runs on this display -- daemon behavior indistinguishable here")
        return

    # RED-PROOF: without a manager, the selection promise dies with its owner.
    _ready, value = put_then_read("text")
    check("control: clipboard dies when daemon is not running", value == "",
          "reader got %r" % value)

    daemon = subprocess.Popen([sys.executable, DAEMON])
    try:
        time.sleep(.4)
        ready, value = put_then_read("text")
        check("real X text survives its owner", ready == "READY"
              and value == "persist-me-041", repr(value))
        ready, value = put_then_read("image")
        check("real X image survives its owner", ready == "READY"
              and value == "8x8", repr(value))
    finally:
        daemon.terminate()
        daemon.wait(timeout=4)


def main():
    headless()
    display()
    print("\nRESULT: " + ("ALL PASS" if not FAILED else "FAILED"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
