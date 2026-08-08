#!/usr/bin/env python3
"""Headless ownership tests plus real-X clipboard persistence round trips."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time

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
