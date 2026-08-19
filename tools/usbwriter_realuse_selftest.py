#!/usr/bin/env python3
"""USB Writer, driven the way a person uses it: pick an image, pick a stick,
write it, stop one, break one, and read what the screen says afterwards.

    tools/guestrun.sh python3 tools/usbwriter_realuse_selftest.py

The other three suites check the app's safety functions in isolation. What
they cannot see is the SCREEN: every defect this suite pins was
a sentence the app got right for a moment and then painted over, or a device it
refused to show, or a menu item that raised on its way to a handler. So this
one hosts the real window (tools/appdrive), gives it a private /sys /proc /dev
holding sticks that do not exist, and looks at the labels afterwards.

Nothing here touches a real block device: `usbwriter.os`, its module-global
`open` and its `subprocess` are redirected into a temporary tree, and the
"stick" that gets written is an ordinary file.

Set NB_UW_SHOTS=<dir> to also drop PNGs of each state for a human to look at.
"""
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

WORK = tempfile.mkdtemp(prefix="nbuw-realuse-")
os.environ["NB_DRIVE_HOME_ROOT"] = os.path.join(WORK, "homes")
SHOTS = os.environ.get("NB_UW_SHOTS", "")
if SHOTS:
    os.makedirs(SHOTS, exist_ok=True)

import appdrive                                              # noqa: E402
from gi.repository import Gtk                                # noqa: E402
import nbpicker                                              # noqa: E402

HOME = os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], "usbwriter")
SPOOL = os.path.join(HOME, ".config", "notebook", "notifications")

FAILED = []
RUN = []


def check(name, cond, detail=""):
    RUN.append(name)
    print("%-4s %s%s" % ("PASS" if cond else "FAIL", name,
                         ("   " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def case(fn):
    """Run one drive-through. A crash fails the checks it never reached BY
    NAME, so this suite never reports a defect as a traceback."""
    def wrapped():
        print("\n-- %s" % fn.__name__)
        try:
            fn()
        except Exception:                                    # noqa: BLE001
            traceback.print_exc()
            check(fn.__name__.upper().replace("_", "-") + "-RAN", False,
                  "the drive-through raised before its checks")
    return wrapped


# ---------------------------------------------------------------------------
# a machine with sticks in it
# ---------------------------------------------------------------------------
SYS = os.path.join(WORK, "sys")
PROC = os.path.join(WORK, "proc")
DEV = os.path.join(WORK, "dev")
GB = 1000 ** 3
PCI = "devices/pci0000:00/0000:00:14.0"
NVME = "devices/pci0000:00/0000:00:1d.0/0000:3b:00.0/nvme/nvme0"


def usbpath(port):
    # The fake root must not itself contain a component starting with "usb":
    # _is_usb looks for one anywhere in the resolved sysfs path.
    return "%s/usb2/2-%d/2-%d:1.0/host%d/target%d:0:0/%d:0:0:0" % (
        PCI, port, port, port, port, port)


def disk(sectors, devpath, vendor="", model="", serial="", parts=()):
    return dict(sectors=sectors, devpath=devpath, vendor=vendor, model=model,
                serial=serial, parts=list(parts))


MACHINE = {
    "nvme0n1": disk(500 * GB // 512, NVME, "", "Samsung SSD 980", "S4EW1",
                    ["nvme0n1p1", "nvme0n1p2"]),
    "sdc": disk(8 * GB // 512, usbpath(3), "SanDisk", "Cruzer Blade",
                "4C530001", []),
    "sdd": disk(2 * GB // 512, usbpath(4), "Generic", "Flash Disk",
                "AA00BB11", []),
}
MOUNTS = [
    "/dev/nvme0n1p2 / ext4 rw,relatime 0 0",
    "/dev/nvme0n1p1 /boot/efi vfat rw 0 0",
    "proc /proc proc rw 0 0",
]


def build(disks=None, mounts=None):
    disks = MACHINE if disks is None else disks
    mounts = MOUNTS if mounts is None else mounts
    for d in (SYS, PROC, DEV):
        if os.path.isdir(d):
            shutil.rmtree(d)
    os.makedirs(SYS + "/block")
    os.makedirs(PROC)
    os.makedirs(DEV)
    for name, spec in disks.items():
        d = SYS + "/block/" + name
        os.makedirs(d)
        with open(d + "/size", "w") as fh:
            fh.write("%d\n" % spec["sectors"])
        real = os.path.join(SYS, spec["devpath"])
        os.makedirs(real, exist_ok=True)
        os.symlink(os.path.relpath(real, d), d + "/device")
        for key in ("vendor", "model", "serial"):
            if spec.get(key):
                with open(os.path.join(real, key), "w") as fh:
                    fh.write(spec[key] + "\n")
        for p in spec["parts"]:
            os.makedirs(d + "/" + p)
        with open(DEV + "/" + name, "wb") as fh:
            fh.write(b"WHATEVER-WAS-ON-IT" * 16)
    write_mounts(mounts)
    with open(PROC + "/swaps", "w") as fh:
        fh.write("Filename\tType\tSize\tUsed\tPriority\n")


def write_mounts(mounts):
    with open(PROC + "/mounts", "w") as fh:
        fh.write("\n".join(mounts) + "\n")


def remap(p):
    if not isinstance(p, str):
        return p
    for pre, to in (("/sys", SYS), ("/proc", PROC), ("/dev", DEV)):
        if p == pre:
            return to
        if p.startswith(pre + "/"):
            return to + p[len(pre):]
    return p


class _Path(object):
    def __getattr__(self, n):
        return getattr(os.path, n)

    def realpath(self, p, *a, **k):
        return os.path.realpath(remap(p), *a, **k)

    def isdir(self, p):
        return os.path.isdir(remap(p))

    def exists(self, p):
        return os.path.exists(remap(p))

    def getsize(self, p):
        return os.path.getsize(remap(p))


class _Os(object):
    path = _Path()

    def __getattr__(self, n):
        return getattr(os, n)

    def listdir(self, p="."):
        return os.listdir(remap(p))

    def stat(self, p, *a, **k):
        return os.stat(remap(p), *a, **k)


class Target(object):
    """The stick's file object: short writes (as write(2) may), a throttle so
    a write can be watched, and an injectable mid-write failure."""
    sleep = 0.0
    short_at = 512 * 1024
    fail_after = None
    fail_exc = None
    log = []

    def __init__(self, fh):
        self._fh = fh
        self.written = 0

    def write(self, b):
        b = bytes(b)
        if Target.fail_after is not None and self.written >= Target.fail_after:
            raise Target.fail_exc
        if Target.sleep:
            time.sleep(Target.sleep)
        n = self._fh.write(b[:Target.short_at])
        self.written += n
        Target.log.append(("write", n))
        return n

    def flush(self):
        return self._fh.flush()

    def fileno(self):
        return self._fh.fileno()

    def close(self):
        Target.log.append(("close",))
        return self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


_real_open = open


def fake_open(path, mode="r", *a, **k):
    if isinstance(path, str) and path.startswith("/dev/") and "w" in mode:
        return Target(_real_open(remap(path), mode, *a, **k))
    return _real_open(remap(path), mode, *a, **k)


class _Completed(object):
    returncode = 0
    stdout = b""
    stderr = b""


class _Subprocess(object):
    calls = []

    def run(self, argv, **k):
        _Subprocess.calls.append(list(argv))
        if argv[:1] == ["umount"] and len(argv) > 1:
            # A real umount leaves /proc/mounts, and the write re-reads it
            # immediately afterwards and refuses a target that is still
            # mounted. A fixture that kept the line would fail the write for
            # a reason the machine would not have.
            with open(os.path.join(PROC, "mounts")) as fh:
                keep = [ln for ln in fh.read().splitlines()
                        if ln.split()[:1] != [argv[1]]]
            write_mounts(keep)
        return _Completed()


class Answer(object):
    """Answer the app's real modal dialog without a nested main loop."""

    def __init__(self, response):
        self.response = response
        self.seen = []

    def __enter__(self):
        self._real = (Gtk.Dialog.run, Gtk.Dialog.show_all, Gtk.Dialog.show)
        me = self

        def run(dlg):
            labels = []

            def walk(w):
                if isinstance(w, Gtk.Label):
                    labels.append(w.get_text())
                if isinstance(w, Gtk.Container):
                    for c in w.get_children():
                        walk(c)
            walk(dlg)
            me.seen.append(labels)
            return me.response

        Gtk.Dialog.run = run
        Gtk.Dialog.show_all = lambda s, *a: None
        Gtk.Dialog.show = lambda s, *a: None
        return self

    def __exit__(self, *a):
        Gtk.Dialog.run, Gtk.Dialog.show_all, Gtk.Dialog.show = self._real
        return False


# ---------------------------------------------------------------------------
# images and drives
# ---------------------------------------------------------------------------
IMGS = os.path.join(WORK, "images")
os.makedirs(IMGS, exist_ok=True)


def make_image(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        block = b"IMAGEDATA" * 1000
        written = 0
        while written < size:
            n = min(len(block), size - written)
            fh.write(block[:n])
            written += n
        fh.seek(510)
        fh.write(b"\x55\xaa")               # a boot record, so no extra note
    return path


OK_IMG = make_image(os.path.join(IMGS, "notebookos-1.0-live.iso"), 3 * 1000 * 1000)
BIG_IMG = make_image(os.path.join(IMGS, "ubuntu-24.04-desktop-amd64.iso"),
                     2500 * 1000 * 1000 // 1000)      # size is faked below
LONG_IMG = make_image(os.path.join(
    IMGS, "media/BIG STICK/downloads/linux/distributions/2026/spring/archive",
    "ubuntu-24.04.1-desktop-amd64.iso"), 3 * 1000 * 1000)

OPEN_DRIVES = []


def drive(disks=None, mounts=None):
    build(disks, mounts)
    d = appdrive.Drive("usbwriter", home=HOME)
    uw = sys.modules["usbwriter"]
    uw.os = _Os()
    uw.open = fake_open
    uw.subprocess = _Subprocess()
    uw.CHUNK = 512 * 1024
    Target.log = []
    Target.fail_after = None
    Target.sleep = 0.0
    d.status_log = []
    d.app.status.connect("notify::label",
                         lambda w, _p: d.status_log.append(w.get_text()))
    d.app._rescan()
    d.pump(0.1)
    OPEN_DRIVES.append(d)
    return d, uw


def wait_idle(d, limit=40.0):
    end = time.time() + limit
    while d.app.busy and time.time() < end:
        d.pump(0.05)
    d.pump(0.3)
    return not d.app.busy


def shot(d, name, note=""):
    if SHOTS:
        d.shot(os.path.join(SHOTS, name), note)


def spool():
    try:
        return sorted(os.listdir(SPOOL))
    except OSError:
        return []


def spool_titles(names):
    import json
    out = []
    for n in names:
        try:
            with open(os.path.join(SPOOL, n)) as fh:
                r = json.load(fh)
            out.append((r.get("title"), r.get("body")))
        except Exception:                                    # noqa: BLE001
            out.append(("?", "?"))
    return out


def ready(d, node="/dev/sdc", image=None):
    nbpicker.open_file = lambda *a, **k: (image or OK_IMG)
    d.click("Choose…")
    d.app._drive_buttons[node].clicked()
    d.pump(0.1)


def on_screen(d):
    """Every visible label, the way a person reads the window."""
    return [t for t in d.texts() if t]


# ---------------------------------------------------------------------------
# F1 — a finished, stopped or failed write must SAY so, and keep saying it
# ---------------------------------------------------------------------------
@case
def finished_write_says_so():
    d, _uw = drive()
    ready(d)
    Target.sleep = 0.01
    n0 = spool()
    with Answer(Gtk.ResponseType.ACCEPT):
        d.click("Write to the stick")
    wait_idle(d)
    shot(d, "01-after-finish.png", "immediately after a completed write")
    status = d.app.status.get_text()
    seen = on_screen(d)
    check("F1-FINISHED-WRITE-SAYS-FINISHED",
          status == "Finished. The stick can be unplugged.",
          "status is %r" % status)
    check("F1-FINISHED-WRITE-SAYS-IT-IN-THE-WINDOW",
          any(t.startswith("Finished.") for t in seen),
          "labels: %r" % seen)
    check("F1-FINISHED-WRITE-DROPS-THE-ERASE-WARNING",
          not any("will be erased" in t for t in seen),
          "labels: %r" % seen)
    d.pump(2.0)
    shot(d, "02-finish-2s.png", "two seconds later")
    check("F1-THE-OUTCOME-IS-STILL-THERE-TWO-SECONDS-LATER",
          d.app.status.get_text() == "Finished. The stick can be unplugged.",
          "status is %r" % d.app.status.get_text())
    check("F1-FINISHED-WRITE-IS-IN-THE-NOTIFICATION-CENTRE",
          any(t[0] == "Finished writing the stick"
              for t in spool_titles([n for n in spool() if n not in n0])))
    check("F1-THE-IMAGE-REALLY-REACHED-THE-STICK",
          os.path.getsize(os.path.join(DEV, "sdc"))
          == os.path.getsize(OK_IMG))

    # ...and the screen goes back to being about the next write as soon as
    # the person does anything: a sticky sentence must not be a wedge.
    d.click("Look again")
    d.pump(0.2)
    shot(d, "03-after-look-again.png", "after Look again")
    seen = on_screen(d)
    check("F1-MOVING-ON-BRINGS-THE-ERASE-WARNING-BACK",
          any("will be erased" in t for t in seen)
          and not any(t.startswith("Finished.") for t in seen),
          "labels: %r" % seen)
    check("F1-MOVING-ON-CLEARS-THE-FINISHED-BAR",
          not d.app.prog.get_visible())
    d.close()


@case
def stopped_write_says_so():
    d, _uw = drive()
    ready(d)
    Target.sleep = 0.25
    with Answer(Gtk.ResponseType.ACCEPT):
        d.click("Write to the stick")
    d.pump(1.0)
    with Answer(Gtk.ResponseType.ACCEPT) as a:
        d.click("Stop")
    stopped = wait_idle(d)
    shot(d, "04-after-stop.png", "after Stop")
    status = d.app.status.get_text()
    seen = on_screen(d)
    check("F1-STOP-ASKS-BEFORE-IT-STOPS", len(a.seen) == 1 and stopped)
    check("F1-STOPPED-WRITE-SAYS-STOPPED",
          status.startswith("Stopped."), "status is %r" % status)
    check("F1-STOPPED-WRITE-SAYS-IT-IN-THE-WINDOW",
          any(t.startswith("Stopped.") for t in seen), "labels: %r" % seen)
    check("F1-STOPPED-WRITE-DROPS-THE-ERASE-WARNING",
          not any("will be erased" in t for t in seen), "labels: %r" % seen)
    d.close()


@case
def failed_write_says_so():
    d, _uw = drive()
    ready(d)
    Target.sleep = 0.01
    Target.fail_after = 1024 * 1024
    Target.fail_exc = OSError(28, "No space left on device")
    with Answer(Gtk.ResponseType.ACCEPT):
        d.click("Write to the stick")
    wait_idle(d)
    shot(d, "05-after-enospc.png", "after the stick filled up")
    status = d.app.status.get_text()
    seen = on_screen(d)
    check("F1-FAILED-WRITE-SAYS-WHY",
          status == "The stick ran out of room before the image finished.",
          "status is %r" % status)
    check("F1-FAILED-WRITE-SAYS-IT-IN-THE-WINDOW",
          any("ran out of room" in t for t in seen), "labels: %r" % seen)
    check("F1-A-FAILED-WRITE-DOES-NOT-LOOK-LIKE-AN-UNSTARTED-ONE",
          not any("will be erased" in t for t in seen), "labels: %r" % seen)
    d.close()


# ---------------------------------------------------------------------------
# F2 — File > Choose an Image…
# ---------------------------------------------------------------------------
@case
def file_menu_opens_the_picker():
    d, _uw = drive()
    called = []
    nbpicker.open_file = lambda *a, **k: (called.append(1), OK_IMG)[1]
    items = dict((i[0], i[1]) for i in d.app.menu_items("File")
                 if isinstance(i, tuple))
    try:
        items["Choose an Image…"]()          # exactly how nbapp calls it
        raised = ""
    except TypeError as exc:
        raised = str(exc)
    d.pump(0.2)
    check("F2-CHOOSE-AN-IMAGE-SURVIVES-A-ZERO-ARGUMENT-CALL",
          not raised, raised)
    check("F2-CHOOSE-AN-IMAGE-OPENS-THE-PICKER",
          bool(called) and d.app.image is not None,
          "picker called: %s, image: %r" % (bool(called), d.app.image))
    # and through the real dropdown a person actually clicks
    d2, _ = drive()
    called[:] = []
    layer = d2.open_menu("File")
    hits = [w for w in d2.walk(layer)
            if isinstance(w, Gtk.Button) and w.get_label() == "Choose an Image…"]
    if hits:
        hits[0].clicked()
    d2.pump(0.3)
    shot(d2, "06-after-menu-choose.png", "after File > Choose an Image…")
    check("F2-THE-DROPDOWN-ITEM-CHOOSES-AN-IMAGE",
          bool(hits) and bool(called) and d2.app.image is not None,
          "item found: %s, picker called: %s" % (bool(hits), bool(called)))
    # the other item on the same menu still works (it always did)
    layer = d2.open_menu("File")
    hits = [w for w in d2.walk(layer)
            if isinstance(w, Gtk.Button)
            and w.get_label() == "Look for Drives Again"]
    before = len(d2.app.drives)
    if hits:
        hits[0].clicked()
    d2.pump(0.2)
    check("F2-LOOK-FOR-DRIVES-AGAIN-STILL-WORKS",
          bool(hits) and len(d2.app.drives) == before)
    d.close()
    d2.close()


# ---------------------------------------------------------------------------
# F3 — the OS automounts every stick; a mounted stick is still a stick
# ---------------------------------------------------------------------------
@case
def an_automounted_stick_is_offered():
    disks = dict(MACHINE)
    disks["sdb"] = disk(15 * GB // 512, usbpath(1), "Kingston",
                        "DataTraveler 3.0", "0019E06B", ["sdb1"])
    mounts = list(MOUNTS) + ["/dev/sdb1 /media/PHOTOS vfat rw,sync 0 0"]
    only = {"nvme0n1": disks["nvme0n1"], "sdb": disks["sdb"]}
    d, uw = drive(only, mounts)
    shot(d, "07-automounted-stick.png", "a stick automounted at /media/PHOTOS")
    offered = [x["name"] for x in d.app.drives]
    seen = on_screen(d)
    check("F3-AN-AUTOMOUNTED-STICK-IS-OFFERED", offered == ["sdb"],
          "offered: %r / %r" % (offered, seen))
    check("F3-AN-AUTOMOUNTED-STICK-IS-NOT-CALLED-ABSENT",
          not any("No USB drive is plugged in." in t for t in seen),
          "labels: %r" % seen)
    check("F3-THE-DISK-THIS-MACHINE-RUNS-FROM-IS-STILL-HIDDEN",
          "nvme0n1" not in offered and "nvme0n1" in uw._system_disks())

    # ...and a USB disk mounted ANYWHERE ELSE is still a system disk. This is
    # the half of rule 1 that must not have moved: /run/live/medium is where
    # the live ISO keeps the stick the machine booted from.
    write_mounts(list(MOUNTS)
                 + ["/dev/sdb1 /run/live/medium iso9660 ro 0 0"])
    d.click("Look again")
    d.pump(0.2)
    check("F3-A-USB-DISK-THE-MACHINE-RUNS-FROM-IS-STILL-HIDDEN",
          [x["name"] for x in d.app.drives] == []
          and "sdb" in uw._system_disks(),
          "offered: %r" % [x["name"] for x in d.app.drives])
    write_mounts(list(MOUNTS) + ["/dev/sdb1 /home ext4 rw 0 0"])
    d.click("Look again")
    d.pump(0.2)
    check("F3-A-USB-DISK-HOLDING-HOME-IS-STILL-HIDDEN",
          [x["name"] for x in d.app.drives] == [],
          "offered: %r" % [x["name"] for x in d.app.drives])
    d.close()


@case
def an_automounted_stick_is_unmounted_before_writing():
    disks = {"nvme0n1": MACHINE["nvme0n1"],
             "sdb": disk(15 * GB // 512, usbpath(1), "Kingston",
                         "DataTraveler 3.0", "0019E06B", ["sdb1"])}
    mounts = list(MOUNTS) + ["/dev/sdb1 /media/PHOTOS vfat rw,sync 0 0"]
    d, _uw = drive(disks, mounts)
    _Subprocess.calls = []
    ready(d, "/dev/sdb")
    Target.sleep = 0.0
    with Answer(Gtk.ResponseType.ACCEPT):
        d.click("Write to the stick")
    wait_idle(d)
    umounts = [c for c in _Subprocess.calls if c[:1] == ["umount"]]
    # The write's own unmount loop was unreachable while a mounted stick could
    # never be offered; it has to be the thing that releases it now.
    check("F3-THE-WRITE-RELEASES-THE-MOUNTED-STICK-ITSELF",
          umounts == [["umount", "/dev/sdb1"]], "calls: %r" % _Subprocess.calls)
    check("F3-THE-MOUNTED-STICK-REALLY-GETS-WRITTEN",
          d.app.status.get_text() == "Finished. The stick can be unplugged.",
          "status is %r" % d.app.status.get_text())
    d.close()


# ---------------------------------------------------------------------------
# F4 — the status line and the warning cannot disagree
# ---------------------------------------------------------------------------
@case
def too_large_image_status():
    d, uw = drive()
    real_getsize = uw.os.path.getsize
    big = 2500 * 1000 * 1000

    class Sizes(_Path):
        def getsize(self, p):
            return big if p == BIG_IMG else real_getsize(p)
    uw.os.path = Sizes()
    ready(d, "/dev/sdd", image=BIG_IMG)          # the 2.0 GB stick
    shot(d, "08-too-large.png", "a 2.5 GB image on a 2.0 GB stick")
    status = d.app.status.get_text()
    seen = on_screen(d)
    check("F4-TOO-LARGE-IS-NOT-CALLED-READY",
          not status.startswith("Ready to write"), "status is %r" % status)
    check("F4-THE-STATUS-LINE-SAYS-THE-IMAGE-DOES-NOT-FIT",
          status == "The image is too large for this stick.",
          "status is %r" % status)
    check("F4-THE-WARNING-AND-THE-BUTTON-STILL-AGREE",
          any("Choose a larger stick." in t for t in seen)
          and not d.app.go_btn.get_sensitive(), "labels: %r" % seen)
    uw.os.path = _Path()
    d.close()


# ---------------------------------------------------------------------------
# F5 — "about 1 second left"
# ---------------------------------------------------------------------------
@case
def one_second_is_singular():
    d, _uw = drive()
    words = [d.app._mins(s) for s in (0, 1, 2, 59, 60, 120, 3600)]
    check("F5-ONE-SECOND-IS-SINGULAR",
          words[:3] == ["1 second", "1 second", "2 seconds"],
          "_mins(0,1,2) = %r" % words[:3])
    check("F5-THE-OTHER-UNITS-ARE-UNCHANGED",
          words[3:] == ["59 seconds", "1 minute", "2 minutes", "1 hour"],
          "%r" % words[3:])
    # and on the screen, at the end of a real write
    ready(d)
    Target.sleep = 0.12
    with Answer(Gtk.ResponseType.ACCEPT):
        d.click("Write to the stick")
    lines = []
    end = time.time() + 30
    while d.app.busy and time.time() < end:
        d.pump(0.05)
        t = d.app.status.get_text()
        if " left" in t and (not lines or lines[-1] != t):
            lines.append(t)
    wait_idle(d)
    check("F5-NO-PROGRESS-LINE-SAYS-1-SECONDS",
          not any("about 1 seconds" in t for t in lines),
          "lines: %r" % lines[-3:])
    d.close()


# ---------------------------------------------------------------------------
# F6 — an empty list has to say which kind of empty it is
# ---------------------------------------------------------------------------
@case
def empty_state_says_which_empty():
    only = {"nvme0n1": MACHINE["nvme0n1"],
            "sdx": disk(8 * GB // 512, usbpath(5), "NoName", "USB DISK", "",
                        [])}
    d, uw = drive(only)
    shot(d, "09-unidentified-stick.png", "a stick with no serial or WWID")
    seen = on_screen(d)
    check("F6-AN-UNIDENTIFIABLE-STICK-IS-STILL-NOT-A-TARGET",
          [x["name"] for x in uw._drives()] == [])
    check("F6-AN-UNIDENTIFIABLE-STICK-IS-NOT-CALLED-ABSENT",
          not any("No USB drive is plugged in." in t for t in seen),
          "labels: %r" % seen)
    check("F6-THE-EMPTY-STATE-SAYS-IT-CANNOT-BE-IDENTIFIED",
          any("cannot be identified" in t for t in seen),
          "labels: %r" % seen)
    # nothing plugged in at all still gets the plain sentence
    d2, _ = drive({"nvme0n1": MACHINE["nvme0n1"]})
    seen = on_screen(d2)
    check("F6-NOTHING-PLUGGED-IN-STILL-SAYS-NOTHING-IS-PLUGGED-IN",
          any(t == "No USB drive is plugged in." for t in seen),
          "labels: %r" % seen)
    d.close()
    d2.close()


# ---------------------------------------------------------------------------
# F7 — closing the window mid-write still reports the write
# ---------------------------------------------------------------------------
@case
def closing_mid_write_reports_it():
    d, _uw = drive()
    ready(d)
    Target.sleep = 0.25
    with Answer(Gtk.ResponseType.ACCEPT):
        d.click("Write to the stick")
    d.pump(0.8)
    was_busy = d.app.busy
    n0 = spool()
    with Answer(Gtk.ResponseType.ACCEPT) as a:
        d.key("Escape")
        d.pump(0.3)
    d.pump(1.5)
    new = spool_titles([n for n in spool() if n not in n0])
    check("F7-CLOSING-MID-WRITE-ASKS-FIRST", was_busy and len(a.seen) == 1)
    check("F7-THE-WRITE-REALLY-STOPS",
          d.app._jobs.running_keys() == [], "%r" % d.app._jobs.running_keys())
    check("F7-CLOSING-MID-WRITE-REPORTS-THE-STOPPED-WRITE",
          [t for t in new if t[0] == "The write was stopped"],
          "spool delta: %r" % new)
    check("F7-THE-STOPPED-WRITE-IS-REPORTED-ONCE",
          len([t for t in new if t[0] == "The write was stopped"]) == 1,
          "spool delta: %r" % new)
    d.close()


@case
def closing_mid_write_keeps_its_guard():
    """The three neighbours of the fix above. Reporting the stopped write is
    what F7 was about; these say that saying it did not cost the guard that
    was already there — the window still refuses to go when the warning is
    declined, an idle window still closes without one, and a write that had
    already delivered its result is not announced as stopped."""
    d, uw = drive()

    class Jobs(object):
        def __init__(self, took):
            self.cancelled = []
            self.took = took

        def cancel(self, key):
            # JobOwner.cancel returns True only when it really cancelled a
            # RUNNING job; False means the write had already finished, and
            # calling that one "stopped" would be a wrong answer.
            self.cancelled.append(key)
            return self.took

    class Probe(object):
        _on_delete = uw.UsbWriter._on_delete

        def __init__(self, busy, confirm, took=True):
            self.busy = busy
            self.confirm = confirm
            self.prompts = 0
            self.posted = []
            self._stop_notified = False
            self._jobs = Jobs(took)

        def _confirm_stop(self):
            self.prompts += 1
            return self.confirm

        def notify(self, title, body=""):
            self.posted.append((title, body))

    declined, idle = Probe(True, False), Probe(False, False)
    finished = Probe(True, True, took=False)
    vetoed = declined._on_delete()
    idle_closed = idle._on_delete()
    finished._on_delete()
    check("F7-DECLINING-THE-WARNING-KEEPS-THE-WINDOW",
          vetoed is True and declined.prompts == 1
          and not declined._jobs.cancelled and not declined.posted,
          "returned %r, prompts %d" % (vetoed, declined.prompts))
    check("F7-AN-IDLE-WINDOW-CLOSES-WITHOUT-A-WARNING",
          idle_closed is False and idle.prompts == 0 and not idle.posted)
    check("F7-A-WRITE-THAT-ALREADY-FINISHED-IS-NOT-CALLED-STOPPED",
          not finished.posted and not finished._stop_notified,
          "posted %r" % (finished.posted,))
    d.close()


# ---------------------------------------------------------------------------
# F8 — a long path must not drag the reading column to the window edges
# ---------------------------------------------------------------------------
@case
def a_long_path_keeps_the_column():
    d, _uw = drive()

    def column():
        inner = d.app.img_name.get_parent().get_parent().get_parent()
        d.pump(0.1)
        a = inner.get_allocation()
        return a.x, a.width

    ready(d, image=OK_IMG)
    short = column()
    shot(d, "10-short-path.png", "a short image path")
    ready(d, image=LONG_IMG)
    long_ = column()
    shot(d, "11-long-path.png", "a %d-character image path" % len(LONG_IMG))
    check("F8-A-LONG-PATH-KEEPS-THE-READING-COLUMN",
          long_ == short and long_[1] <= 640,
          "short %r vs long(%d chars) %r" % (short, len(LONG_IMG), long_))
    check("F8-THE-LONG-PATH-IS-STILL-SHOWN-ELLIPSIZED",
          d.app.img_meta.get_text().endswith(os.path.basename(LONG_IMG)))
    d.close()


# ---------------------------------------------------------------------------
def main():
    for fn in (finished_write_says_so, stopped_write_says_so,
               failed_write_says_so, file_menu_opens_the_picker,
               an_automounted_stick_is_offered,
               an_automounted_stick_is_unmounted_before_writing,
               too_large_image_status, one_second_is_singular,
               empty_state_says_which_empty, closing_mid_write_reports_it,
               closing_mid_write_keeps_its_guard,
               a_long_path_keeps_the_column):
        fn()
    print("\n%d checks, %d failed" % (len(RUN), len(FAILED)))
    for f in FAILED:
        print("  - %s" % f)
    print("RESULT: %s" % ("FAILED" if FAILED else "PASS"))
    shutil.rmtree(WORK, ignore_errors=True)
    return bool(FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
