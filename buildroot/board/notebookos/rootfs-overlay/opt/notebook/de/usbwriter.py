#!/usr/bin/env python3
"""
USB Writer — put a disk image onto a USB stick.

This is how a Notebook OS ISO gets onto a stick so it can be installed on
another machine, and how any .img or .iso is written to removable media. It is
the one app in the OS that DESTROYS data as its normal operation, so almost all
of it is about making sure the right device is chosen and that the person
choosing knows what is about to happen to it.

THE SAFETY RULES, in the order they matter:

  1. A disk this machine is RUNNING FROM is never listed. Not greyed out, not
     listed-with-a-warning: absent. `_system_disks()` collects the whole-disk
     parent of every mounted filesystem, of every swap device and of the live
     medium, and `_drives()` drops them. A picker that shows the system disk at
     all is one mis-click from erasing the computer.
  2. Only removable, USB-attached block devices are offered, so an internal
     second hard disk cannot be picked either.
  3. The device is named in full — make, model, size and node — in the
     confirmation, which has to be answered by pressing a button that says what
     it will do, not "OK".
  4. Everything on the target is unmounted before the write, and the write ends
     with a real fsync, so pulling the stick when it says it is finished is
     safe. (The rest of the OS mounts removable media -o sync for the same
     reason — see automount.sh.)

The write itself runs on a shared nbjobs worker: a 2GB image at USB2 speeds
takes minutes, and a frozen window with no progress is indistinguishable from a
crash. nbjobs is what keeps that safe rather than merely non-blocking — one job
key means a second write can never start on top of a running one, cancellation
is a token the worker checkpoints instead of a flag it has to remember to read,
and every callback passes the owner's closed/superseded gate, so a write that
finishes after the window is gone delivers nothing at all.
"""
import os
import subprocess
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango                 # noqa: E402

import nbapp                                               # noqa: E402
import nbicons                                             # noqa: E402
import nbjobs                                              # noqa: E402
import nbpicker                                            # noqa: E402
from nbi18n import _t                                      # noqa: E402

SECTOR = 512
CHUNK = 4 * 1024 * 1024          # 4MB: big enough to keep USB2 saturated
COLUMN_W = 640

IMAGE_EXTS = (".iso", ".img", ".raw", ".bin")


# ---- reading the machine's block devices ------------------------------------
def _read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def _listdir(path):
    """os.listdir that yields nothing rather than raising. Every caller here is
    reading /sys, which can be absent (a container) or momentarily unreadable,
    and none of them has anything useful to do with the exception."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _fmt_size(n):
    """A size in the units the stick's box is printed in."""
    if n <= 0:
        return "unknown size"
    for unit, div in (("TB", 1000 ** 4), ("GB", 1000 ** 3), ("MB", 1000 ** 2)):
        if n >= div:
            v = n / float(div)
            return "%.0f %s" % (v, unit) if v >= 10 else "%.1f %s" % (v, unit)
    return "%d bytes" % n


def _disk_of(part):
    """The whole disk a partition node belongs to: 'sda2' -> 'sda'.

    Done through /sys rather than by stripping digits, because that guess is
    wrong for exactly the devices where being wrong is worst (nvme0n1p2,
    mmcblk0p1)."""
    part = os.path.basename(part)
    for disk in _listdir("/sys/block"):
        if part == disk or os.path.isdir("/sys/block/%s/%s" % (disk, part)):
            return disk
    return part


def _system_disks():
    """Every whole disk this machine is currently running from.

    Mounted filesystems, swap, and the loop-mounted live medium all count. A
    device in here is never offered to be written to."""
    out = set()

    def note(node):
        node = (node or "").strip()
        if not node.startswith("/dev/"):
            return
        out.add(_disk_of(node[5:]))

    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            note(parts[0])
    for line in _read("/proc/swaps").splitlines()[1:]:
        note(line.split()[0] if line.split() else "")

    # The live ISO is a loop device whose backing file lives on the medium the
    # machine booted from; /proc/mounts names the loop, not the stick. Follow
    # each loop back to the filesystem holding its backing file.
    #
    # An unreadable /sys must not raise: this set is the app's SAFETY list, and
    # a raise here would take the whole drive list down with it — which reads
    # as "no drives" rather than as "could not check", and the difference
    # matters when the answer decides whether a disk gets erased.
    for name in _listdir("/sys/block"):
        if not name.startswith("loop"):
            continue
        back = _read("/sys/block/%s/loop/backing_file" % name)
        if not back:
            continue
        try:
            st = os.stat(back)
        except OSError:
            continue
        for line in _read("/proc/mounts").splitlines():
            p = line.split()
            if len(p) >= 2 and p[0].startswith("/dev/"):
                try:
                    if os.stat(p[1]).st_dev == st.st_dev:
                        note(p[0])
                except OSError:
                    pass
    return out


def _is_usb(name):
    """True if this block device hangs off a USB controller. Resolved through
    the sysfs device link rather than trusted from `removable` alone: a card
    reader reports removable=1 for a slot that may hold the system's own boot
    media on some machines, and a USB SSD reports removable=0."""
    try:
        real = os.path.realpath("/sys/block/%s/device" % name)
    except OSError:
        return False
    paths = (real, os.path.realpath("/sys/block/%s" % name))
    return any(part.startswith("usb") for path in paths
               for part in path.split("/") if part)


def _drives():
    """Removable USB disks that are safe to offer, largest first.

    Returns [{node, name, label, size, bytes}]. Never raises: an unreadable
    sysfs entry costs that one device its row, not the whole list."""
    system = _system_disks()
    found = []
    for name in _listdir("/sys/block"):
        if name.startswith(("loop", "ram", "zram", "dm-", "md", "sr")):
            continue
        if name in system:
            continue                      # rule 1: never the running system
        if not _is_usb(name):
            continue                      # rule 2: removable USB only
        try:
            nblocks = int(_read("/sys/block/%s/size" % name, "0") or 0)
        except ValueError:
            nblocks = 0
        size = nblocks * SECTOR
        if size <= 0:
            continue                      # an empty card slot
        vendor = _read("/sys/block/%s/device/vendor" % name)
        model = _read("/sys/block/%s/device/model" % name)
        title = (" ".join(x for x in (vendor, model) if x)).strip() \
            or _t("USB drive")
        found.append({
            "node": "/dev/" + name,
            "name": name,
            "label": title,
            "size": _fmt_size(size),
            "bytes": size,
        })
    found.sort(key=lambda d: -d["bytes"])
    return found


def _image_fits(image_bytes, drive_bytes):
    """Byte-exact capacity gate; block images may end mid-sector."""
    try:
        return int(image_bytes) <= int(drive_bytes) and int(image_bytes) > 0
    except (TypeError, ValueError):
        return False


def _target_still_safe(snapshot):
    """Revalidate a confirmed target against a fresh, fail-closed scan."""
    if not isinstance(snapshot, dict):
        return False
    return any(d.get("name") == snapshot.get("name")
               and d.get("node") == snapshot.get("node")
               and d.get("bytes") == snapshot.get("bytes")
               for d in _drives())


def image_boot_note(path):
    """A line about whether this image will BOOT once it is on a stick, or ""
    when there is nothing worth saying.

    Writing an image to a stick is a byte-for-byte copy, so the stick boots
    only if the image already contains a boot record — an "isohybrid" ISO, or a
    real disk image with a partition table. A plain ISO9660 built for optical
    media has neither: it copies perfectly, and then the firmware skips the
    stick with no message, which is impossible to tell apart from a bad write
    or a bad stick. Worth one line before the write rather than a mystery
    after it.

    Only ever informational — a filesystem image with no boot record is a
    perfectly good thing to write, so this never blocks anything.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
            gpt = fh.read(8)
            fh.seek(32769)
            iso9660 = fh.read(5) == b"CD001"
    except OSError:
        return ""
    if len(head) < 512:
        return ""
    # 0x55AA at 510 is the MBR boot signature; "EFI PART" at LBA 1 is a GPT.
    if head[510:512] == b"\x55\xaa" or gpt == b"EFI PART":
        return ""
    if iso9660:
        return _t("No boot record: a computer will not start from this disc "
                  "image. It will still copy.")
    return _t("No boot record: a computer will not start from this image.")


def _mounted_parts(name):
    """Mount points of anything on this disk that is currently mounted."""
    out = []
    for line in _read("/proc/mounts").splitlines():
        p = line.split()
        if len(p) >= 2 and p[0].startswith("/dev/"):
            if _disk_of(p[0][5:]) == name:
                out.append((p[0], p[1].replace("\\040", " ")))
    return out


def _write_all(dst, buf):
    """Put every byte of `buf` into `dst`, returning how many were written.

    The target is opened unbuffered, so its write() is a single write(2): it is
    allowed to take fewer bytes than it was handed and to report that in its
    return value, which is ordinary behaviour for a block device under memory
    pressure or a signal. Handing the rest back is this function's whole job —
    the caller counts progress from what comes back, so a dropped tail would
    otherwise be counted as written and the write would end by saying it had
    finished over a truncated image, which is the one wrong answer this app
    must never give.
    """
    view = memoryview(buf)
    sent = 0
    while sent < len(view):
        n = dst.write(view[sent:])
        if not n:
            # Neither an error nor progress: retrying forever would hang the
            # write with a moving clock and no moving bar.
            raise OSError("the drive accepted none of the data")
        sent += n
    return sent


class _OutOfRoom(OSError):
    """The stick filled up mid-write. A CLASS rather than a message, because
    the message is translated and the code that reacts to it is not."""


class _NotPermitted(OSError):
    """The kernel refused the write. See _OutOfRoom for why this is a class."""


class UsbWriter(nbapp.AppWindow):
    app_name = "USB Writer"
    menus = ("File",)

    def __init__(self):
        super().__init__()
        self.image = None
        self.drives = []
        self.busy = False
        # One owner, one key ("write"): the owner is what rejects a second
        # write, discards a finished write's callbacks once the window has
        # closed, and gives the worker a cancel token to checkpoint.
        self._jobs = nbjobs.JobOwner(name="usbwriter")
        self.connect("destroy", lambda *_: self._jobs.close())
        self._install_css()
        self._build()
        self._rescan()

    # -- ui -------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app.
        css = b"""
        .uw-main { background: #FCFBF8; }
        .uw-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .uw-title { font-size: 24px; font-weight: 700; color: #1A1916; }
        .uw-lede { font-size: 14px; color: #6E695E; }
        .uw-step { font-size: 11px; letter-spacing: 0.14em; font-weight: 700;
                   color: #9A9484; }
        .uw-rule { background: #D7D2C5; }
        .uw-field { background: #F4F2EC; border: 1px solid #D7D2C5;
                    padding: 12px 14px; }
        .uw-value { font-size: 15px; color: #1A1916; }
        .uw-hint { font-size: 12px; color: #6E695E; }
        .uw-btn { padding: 7px 16px; background: #FCFBF8;
                  border: 1px solid #C9C4B6; border-radius: 8px;
                  box-shadow: none; font-size: 14px; color: #1A1916; }
        .uw-btn:hover { background: #F1EEE6; }
        .uw-btn:disabled { color: #B3AD9E; background: #F8F7F2; }
        .uw-go { padding: 9px 22px; background: #C8341E; background-image: none;
                 color: #FCFBF8; border: 1px solid #C8341E; border-radius: 8px;
                 box-shadow: none; font-size: 14px; font-weight: 600; }
        .uw-go:hover { background: #B12D19; border-color: #B12D19; }
        .uw-go:disabled { background: #C9C4B6; border-color: #C9C4B6;
                          color: #FCFBF8; }
        .uw-row { padding: 11px 12px; }
        .uw-row-sep { border-top: 1px solid #D7D2C5; }
        .uw-row-hot { background: #F4F2EC; }
        .uw-row-on { background: #F1EEE6; }
        /* The drive row is a button, so it has to be flattened back to
           nothing: the theme's own button chrome inside a bordered field
           would draw a second frame around every stick. Nothing here
           touches outline, so the focus ring still shows on Tab. */
        .uw-rowhit { padding: 0; margin: 0; border: none;
                     background-color: transparent; background-image: none;
                     box-shadow: none; border-radius: 0;
                     min-width: 0; min-height: 0; }
        .uw-rowhit:hover .uw-row { background: #F4F2EC; }
        /* declared after the hover rule so the chosen stick keeps its own
           background while the pointer is over it */
        .uw-rowhit:hover .uw-row-on { background: #F1EEE6; }
        .uw-dname { font-size: 15px; color: #1A1916; }
        .uw-dmeta { font-size: 12px; color: #6E695E; }
        .uw-warn { font-size: 13px; color: #C8341E; }
        .uw-empty { font-size: 14px; color: #6E695E; }
        .uw-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #D7D2C5; background: #F8F7F2; }
        .uw-prog { min-height: 10px; }
        .uw-prog trough { min-height: 10px; background: #DED4C2;
                          border: 1px solid #D7D2C5; border-radius: 100px; }
        .uw-prog progress { min-height: 10px; background-image: none;
                            background: #C8341E; border-radius: 100px;
                            border: none; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                   # noqa: BLE001
            pass          # styling is cosmetic; never block launch

    @staticmethod
    def _wrap(label):
        """Wrap inside the reading column: a wrapping GtkLabel still asks for
        its whole unwrapped line as its natural width, which drags the column
        wider than it was set to."""
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(1)
        return label

    def _step(self, box, n, text):
        lbl = Gtk.Label(label=_t("STEP %d   %s") % (n, _t(text)), xalign=0)
        lbl.get_style_context().add_class("uw-step")
        lbl.set_margin_top(26)
        lbl.set_margin_bottom(9)
        box.pack_start(lbl, False, False, 0)

    def _build(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("uw-main")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        sw, _sh = nbapp.screen_size()
        inner.set_size_request(max(360, min(COLUMN_W, sw - 80)), -1)
        inner.set_margin_top(30)
        inner.set_margin_bottom(30)

        title = Gtk.Label(label=_t("USB Writer"), xalign=0)
        title.get_style_context().add_class("uw-title")
        inner.pack_start(title, False, False, 0)
        lede = Gtk.Label(
            label=_t("Write a disk image onto a USB stick. Everything already "
                     "on the stick is erased."),
            xalign=0)
        lede.get_style_context().add_class("uw-lede")
        self._wrap(lede)
        lede.set_margin_top(4)
        inner.pack_start(lede, False, False, 0)

        # ---- step 1: the image ----
        self._step(inner, 1, "The image to write")
        field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        field.get_style_context().add_class("uw-field")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.img_name = Gtk.Label(label=_t("No image chosen"), xalign=0)
        self.img_name.get_style_context().add_class("uw-value")
        self.img_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.img_meta = Gtk.Label(label=_t("Choose an .iso or .img file"),
                                  xalign=0)
        self.img_meta.get_style_context().add_class("uw-hint")
        self.img_meta.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        col.pack_start(self.img_name, False, False, 0)
        col.pack_start(self.img_meta, False, False, 0)
        field.pack_start(col, True, True, 0)
        self.pick_btn = Gtk.Button(label=_t("Choose…"))
        self.pick_btn.get_style_context().add_class("uw-btn")
        self.pick_btn.set_valign(Gtk.Align.CENTER)
        self.pick_btn.connect("clicked", self._on_pick)
        field.pack_end(self.pick_btn, False, False, 0)
        inner.pack_start(field, False, False, 0)

        # ---- step 2: the drive ----
        self._step(inner, 2, "The USB stick to write it to")
        self.drive_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=0)
        self.drive_box.get_style_context().add_class("uw-field")
        inner.pack_start(self.drive_box, False, False, 0)

        again = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        again.set_margin_top(10)
        self.rescan_btn = Gtk.Button(label=_t("Look again"))
        self.rescan_btn.get_style_context().add_class("uw-btn")
        self.rescan_btn.connect("clicked", lambda *_: self._rescan())
        again.pack_start(self.rescan_btn, False, False, 0)
        note = Gtk.Label(
            label=_t("Only removable USB drives are listed. The disk this "
                     "computer runs from is never shown."), xalign=0)
        note.get_style_context().add_class("uw-hint")
        self._wrap(note)
        note.set_valign(Gtk.Align.CENTER)
        again.pack_start(note, True, True, 0)
        inner.pack_start(again, False, False, 0)

        # ---- step 3: write ----
        self._step(inner, 3, "Write")
        self.warn = Gtk.Label(xalign=0)
        self.warn.get_style_context().add_class("uw-warn")
        self._wrap(self.warn)
        inner.pack_start(self.warn, False, False, 0)

        self.prog = Gtk.ProgressBar()
        self.prog.get_style_context().add_class("uw-prog")
        self.prog.set_show_text(False)
        self.prog.set_no_show_all(True)
        self.prog.set_margin_top(12)
        inner.pack_start(self.prog, False, False, 0)

        act = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        act.set_margin_top(14)
        self.go_btn = Gtk.Button(label=_t("Write to the stick"))
        self.go_btn.get_style_context().add_class("uw-go")
        self.go_btn.connect("clicked", self._on_go)
        act.pack_start(self.go_btn, False, False, 0)
        self.stop_btn = Gtk.Button(label=_t("Stop"))
        self.stop_btn.get_style_context().add_class("uw-btn")
        self.stop_btn.set_no_show_all(True)
        self.stop_btn.connect("clicked", self._on_stop)
        act.pack_start(self.stop_btn, False, False, 0)
        inner.pack_start(act, False, False, 0)

        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        holder.pack_start(inner, True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(holder)
        main.pack_start(scroll, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("uw-status")
        self.status.set_vexpand(False)
        main.pack_start(self.status, False, False, 0)

        self.content.pack_start(main, True, True, 0)
        self.selected = None

    # -- drives ----------------------------------------------------------------

    def _rescan(self):
        if self.busy:
            return
        keep = self.selected["node"] if self.selected else None
        self.drives = _drives()
        self.selected = next((d for d in self.drives if d["node"] == keep),
                             None)
        if self.selected is None and len(self.drives) == 1:
            self.selected = self.drives[0]     # one stick: nothing to choose
        for ch in self.drive_box.get_children():
            self.drive_box.remove(ch)
        if not self.drives:
            empty = Gtk.Label(label=_t("No USB drive is plugged in."),
                              xalign=0)
            empty.get_style_context().add_class("uw-empty")
            self.drive_box.pack_start(empty, False, False, 0)
        else:
            for i, d in enumerate(self.drives):
                self.drive_box.pack_start(self._drive_row(d, first=(i == 0)),
                                          False, False, 0)
        self.drive_box.show_all()
        self._refresh()

    def _drive_row(self, d, first=False):
        # The row is a relief-less BUTTON, not an EventBox. An EventBox takes
        # no focus and answers no key, so the one choice in this app that
        # decides which disk gets erased used to be reachable by mouse only —
        # a keyboard user could Tab to "Look again" and to "Write to the
        # stick" but never to the stick itself. A button focuses, answers
        # Enter and Space, and reports itself to assistive technology.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ctx = row.get_style_context()
        ctx.add_class("uw-row")
        if not first:
            ctx.add_class("uw-row-sep")
        if self.selected and self.selected["node"] == d["node"]:
            ctx.add_class("uw-row-on")

        try:
            row.pack_start(nbicons.image("usbwriter", 18, "#3A362E"), False, False, 0)
        except Exception:                                   # noqa: BLE001
            pass          # an icon is never worth failing a row for

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        nm = Gtk.Label(label=d["label"], xalign=0)
        nm.get_style_context().add_class("uw-dname")
        nm.set_ellipsize(Pango.EllipsizeMode.END)
        meta = Gtk.Label(label="%s  ·  %s" % (d["size"], d["node"]), xalign=0)
        meta.get_style_context().add_class("uw-dmeta")
        text.pack_start(nm, False, False, 0)
        text.pack_start(meta, False, False, 0)
        row.pack_start(text, True, True, 0)

        tick = Gtk.Label(label="✓" if (self.selected and
                                       self.selected["node"] == d["node"])
                         else "")
        tick.get_style_context().add_class("uw-dname")
        tick.set_valign(Gtk.Align.CENTER)
        row.pack_end(tick, False, False, 0)

        hit = Gtk.Button()
        hit.set_relief(Gtk.ReliefStyle.NONE)
        hit.get_style_context().add_class("uw-rowhit")
        hit.add(row)
        # Name the drive the way the confirmation names it: make, size AND
        # node. GTK would derive this button's accessible name from the first
        # label it finds inside, which is the make alone — and two identically
        # named sticks are exactly the case where reading the interface aloud
        # has to be able to tell them apart. The label is the device's own
        # text, so it is never translated; the lead-ins are, and both are
        # phrases the catalogs already carry in all seventeen languages —
        # a new English-only string here would be read aloud in English.
        ident = "%s  ·  %s  ·  %s" % (d["label"], d["size"], d["node"])
        hit.set_tooltip_text("%s\n%s"
                             % (_t("Choose the USB stick to write it to"),
                                ident))
        nbapp.name_control(hit, "%s  %s" % (_t("USB drive"), ident))
        hit.connect("clicked", lambda _w, dd=d: self._choose(dd))
        return hit

    def _choose(self, d):
        if self.busy:
            return True
        self.selected = d
        self._rescan()
        return True

    # -- image -----------------------------------------------------------------

    def _on_pick(self, _btn):
        if self.busy:
            return
        path = nbpicker.open_file(self, title=_t("Choose a disk image"),
                                  patterns=["*" + e for e in IMAGE_EXTS])
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            self._say(_t("That file could not be read."))
            return
        if size <= 0:
            self._say(_t("That file is empty."))
            return
        self.image = {"path": path, "bytes": size,
                      "note": image_boot_note(path)}
        self.img_name.set_text(os.path.basename(path))
        meta = "%s  ·  %s" % (_fmt_size(size), path)
        self.img_meta.set_text(meta)
        self._refresh()

    # -- state -----------------------------------------------------------------

    def _refresh(self):
        ready = bool(self.image) and self.selected is not None and not self.busy
        self.go_btn.set_sensitive(ready)
        if self.busy:
            return
        if self.image and self.selected:
            if not _image_fits(self.image["bytes"], self.selected["bytes"]):
                self.warn.set_text(
                    _t("This image is %s and the stick holds %s. Choose a "
                       "larger stick.")
                    % (_fmt_size(self.image["bytes"]), self.selected["size"]))
                self.go_btn.set_sensitive(False)
            else:
                msg = _t("Everything on %s (%s, %s) will be erased.") % (
                    self.selected["label"], self.selected["size"],
                    self.selected["node"])
                note = self.image.get("note")
                if note:
                    msg = "%s\n%s" % (note, msg)
                self.warn.set_text(msg)
        elif not self.image:
            self.warn.set_text("")
        else:
            self.warn.set_text("")
        self._say(self._summary())

    def _summary(self):
        if not self.image:
            return _t("Choose an image to write")
        if self.selected is None:
            return _t("Choose the USB stick to write it to")
        return _t("Ready to write %s to %s") % (
            os.path.basename(self.image["path"]), self.selected["node"])

    def _say(self, text):
        self.status.set_text(text)

    # -- writing ---------------------------------------------------------------

    def _on_go(self, _btn):
        if self.busy or not self.image or self.selected is None:
            return
        d = self.selected
        # Rule 3: the confirmation names the device in full and its button says
        # what it does. "OK" on a dialog is not consent to erase a disk.
        if not self._confirm(
                _t("Erase %s?") % d["label"],
                _t("Everything on %s (%s, %s) will be erased and replaced "
                   "with %s. This cannot be undone.")
                % (d["label"], d["size"], d["node"],
                   os.path.basename(self.image["path"])),
                _t("Erase and write")):
            return
        self._start()

    def _confirm(self, heading, body, action):
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbapp")
        box = dlg.get_content_area()
        box.set_border_width(22)
        box.set_spacing(10)
        h = Gtk.Label(label=heading, xalign=0)
        h.get_style_context().add_class("uw-title")
        box.pack_start(h, False, False, 0)
        b = Gtk.Label(label=body, xalign=0)
        b.get_style_context().add_class("uw-lede")
        b.set_line_wrap(True)
        b.set_max_width_chars(46)
        box.pack_start(b, False, False, 0)
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("uw-btn")
        go = dlg.add_button(action, Gtk.ResponseType.ACCEPT)
        go.get_style_context().add_class("uw-go")
        # The destructive button is never the default: a stray Return must not
        # erase a disk.
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.ACCEPT

    def _on_stop(self, _btn):
        if not self.busy:
            return
        if not self._confirm_stop():
            return
        # on_cancel puts the window back in its idle state; the worker's own
        # checkpoint is what actually ends the write.
        self._jobs.cancel("write")

    def _confirm_stop(self):
        return self._confirm(
            _t("Stop writing?"),
            _t("The stick is only part-written. Stopping now leaves it "
               "unusable until it is written again."),
            _t("Stop writing"))

    def _start(self):
        self.busy = True
        self.go_btn.set_sensitive(False)
        self.pick_btn.set_sensitive(False)
        self.rescan_btn.set_sensitive(False)
        self.prog.set_fraction(0.0)
        self.prog.show()
        self.stop_btn.show()
        self.warn.set_text("")
        self._say(_t("Preparing the stick…"))
        drive = dict(self.selected)
        image = dict(self.image)
        started = self._jobs.start(
            "write", lambda job: self._write_job(job, drive, image),
            on_done=lambda _value: self._finished("done", ""),
            on_error=self._write_error,
            on_cancel=lambda: self._finished("stopped", ""),
            on_progress=self._job_progress,
            policy=nbjobs.REJECT)
        if started is None:
            self._finished("error", _t("A write is already in progress."))

    def _write_job(self, job, d, img):
        try:
            # Selection and confirmation are snapshots. A stick can be pulled,
            # or become the boot/system disk, before the worker runs. Repeat
            # both safety guards immediately before opening any target.
            if not _target_still_safe(d):
                raise OSError("The selected USB target is no longer safe")
            actual_image_bytes = os.path.getsize(img["path"])
            if not _image_fits(actual_image_bytes, d.get("bytes")):
                raise _OutOfRoom(_t("The image is larger than the stick."))
            img["bytes"] = actual_image_bytes
            # Anything mounted off the target has to go first, or the kernel is
            # writing to the same sectors we are and the result is neither the
            # old filesystem nor the new image.
            for node, mnt in _mounted_parts(d["name"]):
                job.checkpoint()
                subprocess.run(["umount", node], capture_output=True,
                               timeout=30)
            total = img["bytes"]
            done = 0
            started = time.time()
            last = 0.0
            with open(img["path"], "rb", buffering=0) as src, \
                    open(d["node"], "wb", buffering=0) as dst:
                while True:
                    job.checkpoint()
                    buf = src.read(CHUNK)
                    if not buf:
                        break
                    done += _write_all(dst, buf)
                    now = time.time()
                    if now - last > 0.25:
                        last = now
                        job.progress(done / float(total) if total else 0.0,
                                     (done, total, now - started))
                job.progress(1.0, _t("Finishing the write…"))
                dst.flush()
                os.fsync(dst.fileno())
            # The stick is only safe to pull once the kernel's own queues are
            # empty too, not just our file's.
            subprocess.run(["sync"], capture_output=True, timeout=120)
        except PermissionError as exc:
            raise _NotPermitted(_t("This computer would not allow writing to that "
                             "drive.")) from exc
        except OSError as exc:
            # errno.ENOSPC on a stick that reports more capacity than it has is
            # the common one, and worth saying plainly.
            if getattr(exc, "errno", None) == 28:
                raise _OutOfRoom(_t("The stick ran out of room before the image "
                                    "finished.")) from exc
            raise
        return True

    def _write_error(self, error):
        # Matched on the exception's CLASS, not on its words. This used to read
        # `"ran out of room" in error.message` — and the message it was reading
        # is built with _t(), so it only matched while those strings happened to
        # be missing from the catalogs. Once they were translated, a German
        # reader whose stick filled up was told it may have been unplugged.
        # JobError carries `kind` for exactly this, and a class name is the same
        # in every language.
        kind = getattr(error, "kind", "")
        safe = (_t("The stick ran out of room before the image finished.")
                if kind == "_OutOfRoom" else
                _t("This computer would not allow writing to that drive.")
                if kind == "_NotPermitted" else
                _t("The write stopped before it finished. The stick may have "
                   "been unplugged."))
        self._finished("error", safe)

    def _job_progress(self, fraction, phase):
        if isinstance(phase, tuple) and len(phase) == 3:
            self._progress(*phase)
        elif isinstance(phase, str):
            self.prog.set_fraction(max(0.0, min(1.0, fraction or 0.0)))
            self._say(phase)

    def _say_idle(self, text):
        self._say(text)
        return False

    def _progress(self, done, total, elapsed):
        frac = min(1.0, done / float(total)) if total else 0.0
        self.prog.set_fraction(frac)
        rate = done / elapsed if elapsed > 0.5 else 0
        if rate > 0:
            left = max(0, (total - done) / rate)
            self._say(_t("%d%%   %s of %s   %s/s   about %s left")
                      % (int(frac * 100), _fmt_size(done), _fmt_size(total),
                         _fmt_size(int(rate)), self._mins(left)))
        else:
            self._say("%d%%" % int(frac * 100))
        return False

    @staticmethod
    def _mins(secs):
        secs = int(secs)
        if secs < 60:
            return _t("%d seconds") % max(1, secs)
        m = secs // 60
        if m < 60:
            return _t("%d minutes") % m if m != 1 else _t("1 minute")
        return _t("%d hours") % (m // 60) if m // 60 != 1 else _t("1 hour")

    def _finished(self, how, message):
        self.busy = False
        self.stop_btn.hide()
        self.pick_btn.set_sensitive(True)
        self.rescan_btn.set_sensitive(True)
        # A write runs for minutes and nobody watches a progress bar for
        # minutes, so its outcome also goes to the menu bar's notification
        # centre — which outlives this window and is on screen whatever the
        # person moved on to. The status line below still says the same thing
        # for anyone who did stay.
        wrote = os.path.basename((self.image or {}).get("path") or "")
        if how == "done":
            self.prog.set_fraction(1.0)
            self._say(_t("Finished. The stick can be unplugged."))
            self.warn.set_text("")
            self.notify(_t("Finished writing the stick"), wrote)
        elif how == "stopped":
            self.prog.hide()
            self._say(_t("Stopped. The stick is not usable until it is "
                         "written again."))
            self.notify(_t("The write was stopped"),
                        _t("The stick is not usable until it is written "
                           "again."))
        else:
            self.prog.hide()
            self._say(message or _t("The write did not finish."))
            self.notify(_t("The write did not finish."), message or "")
        self._rescan()
        return False

    # -- chrome ----------------------------------------------------------------

    def menu_items(self, name):
        if name == "File":
            return [
                ("Choose an Image…",
                 self._on_pick if not self.busy else None),
                ("Look for Drives Again",
                 self._rescan if not self.busy else None),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        return super().menu_items(name)

    def close(self, *a):
        # Esc while a write is running would leave a half-written stick with
        # nothing on screen to say so. Ask first; the write itself is stopped
        # cleanly by the worker's own cancel flag.
        if self.busy:
            if not self._confirm_stop():
                return True
            self._jobs.cancel("write")
        return super().close(*a)


if __name__ == "__main__":
    nbapp.run(UsbWriter)
