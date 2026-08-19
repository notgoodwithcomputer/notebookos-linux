#!/usr/bin/env python3
"""
Install Notebook OS — the guided system installer (native GTK).

Run from the Notebook OS live session, this turns the running live medium into a
permanently installed system on a disk of the user's choosing. It is a plain,
honest wizard: a left step rail, a content pane and a Back/Next footer. Nothing
destructive happens until the final Progress step, and only after an explicit
confirmation; every external tool is guarded with shutil.which and every path
with os.path.exists, so the module imports and the window constructs on a host
with no disks and no live medium (the construct-all / selftest case) — there it
opens in a neutral "no install medium" state with the install action disabled.

The install engine runs on a worker thread and streams every command's output
into a live log; a non-zero exit code stops the run and shows the failure. The
release contract (payload paths, the fixed root PARTUUID, the GPT layout, the
ESP bootloader path) is mirrored from tools/mkimage-uefi.sh and pinned in the
constants below so the prebuilt GRUB EFI finds the installed root.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os
import re
import time
import shutil
import tempfile
import threading
import subprocess

import nbapp
import nbicons
import nbi18n
from nbi18n import _t  # noqa: E402

# crypt is a stdlib C module; it is present on the target image but guard the
# import so this module still loads on a host that lacks it (openssl is the
# fallback, see _hash_password).
try:
    import crypt as _crypt
except Exception:
    _crypt = None

# Keyboard layouts — nbi18n's own list, not a private copy. The desktop applies
# the layout from nbi18n.keyboard() at every session start, so a layout offered
# here that nbi18n does not know is a layout the installed machine will never
# use: the installer's five-entry private list is exactly why an install set to
# French came up on US QWERTY. (label, xkb-code), widest-compatible order.
KBD_LAYOUTS = [(lbl, code) for code, lbl in nbi18n.KEYBOARDS]

# System locale. This is an offline appliance built without locale-gen, so only
# C / C.UTF-8 are guaranteed to resolve; the rest are offered honestly and take
# effect only if the target image ships their data.
#
# The LABELS say what the choice means to the person making it. They used to be
# the raw identifiers — "C.UTF-8 (Unicode)", "C (POSIX)", "English (US) UTF-8" —
# which name nothing a person installing a computer has heard of, on the very
# first screen they ever see. The codes on the right are unchanged.
LOCALES = [("Unicode (every language)", "C.UTF-8"),
           ("Basic (English letters only)", "C"),
           ("English (United States)", "en_US.UTF-8"),
           ("English (United Kingdom)", "en_GB.UTF-8")]

# ---- release contract (shared with the ISO builder; do not change) ----
OS_NAME = "Notebook OS"
OS_ID = "notebookos"
OS_VERSION = "1.0"
OS_VERSION_ID = "1.0"
OS_PRETTY = "Notebook OS"
OS_RELEASE_SOURCE = "/etc/os-release"
# The fixed rootfs PARTUUID the prebuilt GRUB EFI boots (root=PARTUUID=...).
# Identical to the value tools/mkimage-uefi.sh bakes into grub.cfg.
ROOT_PARTUUID = "b8e5a5f2-1a2b-4c3d-9e8f-000000000042"

# Live-medium payload (mounted read-only at /run/live/medium by the live init).
LIVE_MEDIUM = "/run/live/medium"
INSTALL_DIR = os.path.join(LIVE_MEDIUM, "install")
ROOTFS_TAR = os.path.join(INSTALL_DIR, "rootfs.tar")
BOOT_EFI_SRC = os.path.join(INSTALL_DIR, "BOOTX64.EFI")
KERNEL_SRC = os.path.join(INSTALL_DIR, "bzImage")

# Add-on map packs, staged on the medium by tools/mkiso.sh (step 5c). They sit
# OUTSIDE /install because they are not part of the root being extracted: the
# ISO already carries that root twice (live squashfs + install tarball), so a
# 2.7 GB continent kept inside it would be stored twice. Copied onto the
# installed system by _copy_map_packs, into the /data/maps that de/maps.py
# scans, so maps keep working once the medium is unplugged.
MAPS_SRC = os.path.join(LIVE_MEDIUM, "maps")
MAPS_DEST = os.path.join("data", "maps")

# UEFI Secure Boot payload (present on Secure-Boot media). When these exist the
# installer writes the shim -> grub -> signed-kernel chain instead of the plain
# unsigned GRUB above; otherwise it falls back to BOOT_EFI_SRC (legacy media).
SB_SHIM_SRC = os.path.join(INSTALL_DIR, "shimx64.efi")     # MS-signed shim -> BOOTX64.EFI
SB_GRUB_SRC = os.path.join(INSTALL_DIR, "grubx64.efi")     # Debian-signed grub
SB_MM_SRC = os.path.join(INSTALL_DIR, "mmx64.efi")         # MokManager (key enrollment)
SB_MOK_SRC = os.path.join(INSTALL_DIR, "MOK.cer")          # our cert (user enrolls once)
SB_GRUBCFG_SRC = os.path.join(INSTALL_DIR, "grub.cfg")     # menu (grub prefix /EFI/debian)

# GPT layout / labels.
ESP_SIZE_MIB = 128
# Headroom over the raw payload size. The install writes the extracted tree
# (about the size of the tar, which is uncompressed) plus the ext4 metadata
# reserve, the journal and the ESP copy of the kernel — and a root filled to
# the last byte is a computer that cannot save a file. 1.2x the payload is the
# smallest figure that leaves the installed machine usable.
PAYLOAD_HEADROOM = 1.2
# A root partition with no room to breathe at all is not an install anyone
# wants, so never claim a disk fits on the payload figure alone.
MIN_FREE_MIB = 256
ESP_LABEL = "NBOS_ESP"
ROOT_LABEL = "notebookos"
# The swap partition is named so /etc/fstab can refer to it by LABEL rather
# than by a device path that changes the moment another disk is plugged in.
# Verified on the target build: busybox swapon is compiled with
# FEATURE_SWAPONOFF_LABEL and resolves LABEL= through blkid.
SWAP_LABEL = "nbos-swap"
TARGET_MNT = "/mnt/nbtarget"
# Where a disk is mounted READ-ONLY just to look at it (see _detect_install).
# Deliberately not TARGET_MNT: that name belongs to the destructive engine, and
# a probe borrowing it would make "is something mounted on the install target?"
# answer yes about a disk nobody has agreed to touch yet.
PROBE_MNT = "/mnt/nbprobe"

# ---- updating a system that is already on a disk ----
# The two working directories an update uses, both on the installed root
# itself. The new system is unpacked into UPDATE_STAGE_DIR BESIDE the old one
# and only then moved into place, so a failure anywhere in the long part of the
# run -- no room, an unreadable medium, a truncated tar -- leaves a machine
# that still starts up. UPDATE_OLD_DIR holds the directories being replaced
# until the swap has finished, which is the only reason the swap can be undone.
#
# Dot-names on purpose: /.nbupdate-new is not something anybody browsing their
# own disk will stop and wonder about, and both are removed when the run ends.
UPDATE_STAGE_DIR = ".nbupdate-new"
UPDATE_OLD_DIR = ".nbupdate-old"
# The top-level directories an update never touches. /root is this appliance's
# HOME DIRECTORY -- session.sh pins NB_HOME=/root and the desktop runs as the
# administrator, so every document, every app's store under .config/notebook,
# the Desktop/Documents/Pictures tree and locale.json all live there. /home is
# empty on a shipped machine and is kept for the same reason. /data is where
# _copy_map_packs puts map packs, which are gigabytes nobody should have to
# fetch again to get a newer system.
#
# This tuple is also what makes the swap safe to undo and UPDATE_OLD_DIR safe
# to delete: these names are never moved into it, so it can only ever hold
# system directories that this run is replacing anyway.
PRESERVED_DIRS = ("root", "home", "data")
# Room an update needs free on the installed root: the whole new system,
# unpacked, sitting beside the old one, plus the same working margin the disk
# step already insists on. Measured against the payload rather than guessed --
# the tarball is uncompressed, so its size is very close to what it extracts to.
UPDATE_STAGE_HEADROOM = 1.05

RED = "#C8341E"

# Tools the destructive engine needs; the install action stays disabled until
# every one of these resolves on PATH.
REQUIRED_TOOLS = ("sgdisk", "wipefs", "mkfs.vfat", "mkfs.ext4",
                  "lsblk", "mount", "umount", "tar")

# What a partition holds, in a word a person recognises. A model name and a
# size cannot tell two disks apart — "Windows" can — and recognising the disk
# is the only real defence against erasing the wrong one, so every candidate's
# contents are read before the list is drawn (see _disk_contents) and repeated
# in the erase warning, the Summary and the final confirmation.
FS_WHAT = {
    "ntfs": "Windows", "ntfs3": "Windows",
    "ext2": "Linux", "ext3": "Linux", "ext4": "Linux",
    "xfs": "Linux", "btrfs": "Linux", "f2fs": "Linux", "reiserfs": "Linux",
    "hfs": "Mac", "hfsplus": "Mac", "apfs": "Mac",
    "vfat": "Files", "exfat": "Files", "msdos": "Files",
    "swap": "Spare memory space",
    "iso9660": "A disc", "udf": "A disc",
}
# The GPT type GUID (and its MBR equivalent) of an EFI System Partition — the
# small FAT partition a modern machine starts from. Naming it keeps it from
# showing up as an anonymous "Files" beside the operating system it starts.
ESP_TYPES = ("c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "0xef")


def run_cmd(argv, timeout=8):
    """Run a probe command and return (rc, combined-output) — never raises."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception:
        return 1, ""


def human_bytes(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%d %s" % (n, u)) if u in ("B", "KB") else ("%.1f %s" % (n, u))
        n /= 1024.0
    return "%.1f TB" % n


class InstallError(Exception):
    """A step failed (non-zero exit or a refused precondition)."""


class PageColumn(Gtk.Box):
    """A wizard page's content column: readable width, still shrinkable.

    Wizard copy is prose, and prose needs a measure. Left to fill the pane the
    Welcome paragraph set as a single ~1600px line on a 1920 screen, and the
    Summary's label-left/value-right rows put a field an ocean away from its
    value. Capping the NATURAL width (and never the minimum, which is what a
    plain set_size_request would do — that is how Settings ended up unable to
    fit a 1366px panel) gives a comfortable column on a big screen and the full
    pane on a small one."""

    # No __gtype_name__ on purpose: a fixed GType name can only be
    # registered ONCE per process, so a second import of this module
    # dies with "could not create new GType". That is not academic —
    # it made three config-resilience checks look like defects in this
    # app for a long time, and it breaks any harness that renders two
    # apps in one process (installer imports settings). writer.py's
    # ReadingColumn documents the same decision.
    MAX_W = 880

    def do_get_preferred_width(self):
        minw, _nat = Gtk.Box.do_get_preferred_width(self)
        return min(minw, self.MAX_W), self.MAX_W


class Installer(nbapp.AppWindow):
    app_name = "Install Notebook OS"
    menus = ()   # only the app-name menu (About / Close); no File/Edit/View

    STEPS = [
        ("welcome", "Welcome"),
        ("target", "Choose the disk"),
        ("options", "Options"),
        ("summary", "Summary"),
        ("progress", "Install"),
        ("done", "Done"),
    ]

    def __init__(self):
        super().__init__()
        self._closed = False
        self._paint_source = 0
        self._pulse_source = 0
        self._install_css()

        # Resolve every external tool once. Missing tools are None and every
        # call site guards on that, degrading to a disabled install action.
        self.tools = {name: shutil.which(name) for name in (
            "sgdisk", "wipefs", "mkfs.vfat", "mkfs.ext4", "mkswap",
            "lsblk", "findmnt", "blkid", "partx", "partprobe",
            "mount", "umount", "tar", "sync", "udevadm", "reboot",
            "poweroff")}

        # Is there anything to install onto? The primary gate is the live-medium
        # payload; without it (construct-all / a normal desktop session) we open
        # in a neutral state and never enable the destructive action.
        self.medium_ok = os.path.exists(ROOTFS_TAR)
        self.missing_tools = [t for t in REQUIRED_TOOLS if not self.tools.get(t)]
        # Can this image turn a password into a stored hash AT ALL? _hash_password
        # is the very last thing the engine does, at 90% — by which point the
        # target has been wiped, partitioned, formatted and had the whole system
        # extracted onto it. "No password hashing available" arriving THERE means
        # the answer came after the user's files were already gone. Ask now, once,
        # on the screens where backing out is still free.
        self.can_hash = self._hashing_available()
        # Filled in when the Summary step is reached: another disk already
        # carrying the fixed root PARTUUID (see _partuuid_clash).
        self._partuuid_other = ""
        # How big the system being installed actually is. The install ERASES the
        # target (wipefs, sgdisk -Z, mkfs) and only then extracts the tar, so a
        # disk that turns out to be too small is discovered after the user's
        # files are already gone. Measure the payload up front and refuse the
        # disk on the screen where it is chosen. 0 when there is no payload —
        # every other gate already refuses to install in that state.
        try:
            self.payload_bytes = os.path.getsize(ROOTFS_TAR)
        except OSError:
            self.payload_bytes = 0

        # Wizard state, filled as the user advances.
        self.cfg = {
            "disk": None, "disk_model": "", "disk_size": 0,
            "disk_contents": "",
            # "install" wipes and partitions the disk; "update" replaces the
            # system on a disk that already carries one and keeps everything
            # that belongs to the person using it. Only ever "update" when
            # _detect_install has actually READ a Notebook OS marker off that
            # disk (see _is_update): an Update offered on a blank disk is a
            # promise to keep files that are not there, made on the one screen
            # whose other button erases everything.
            "mode": "install",
            # What _detect_install found on the chosen disk, or None.
            "existing": None,
            "hostname": "notebook",
            # The name the machine calls its owner. Shown at sign-in and in
            # Settings; it does NOT create a second account, because the
            # desktop runs as the administrator and NB_HOME is /root (see
            # _configure_login's note on the removed _create_user).
            "username": "",
            # The layout the live session is using now, so the installed machine
            # inherits the keyboard the user is demonstrably typing on.
            "kbd": self._kbd_index(nbi18n.keyboard()),
            "locale": 0,
            # No "password2": the confirm field is compared against the
            # password in _validate, straight off the two entries, so a copy
            # here was read by nothing -- it only kept a second plaintext copy
            # of the password alive in a dict for the length of the install.
            "password": "",
            "root_passwordless": False,
            # OEM: prepare the machine, but leave the answers that belong to
            # the person who will USE it for their first start-up.
            "oem": False,
            "swap": False, "swap_mib": 2048,
        }
        self._step = 0
        self._max_reached = 0
        self._working = False
        self._confirm_layer = None
        self._scan_gen = 0
        self._clash_gen = 0      # generation of the Summary's PARTUUID probe
        self._prev_disk = None   # last chosen disk, re-selected after a rescan
        self._prev_mode = ""     # ...and what it had been chosen to do
        self._pulse_on = False   # progress bar pulses during the long extract
        # Set once the engine has started erasing (see _install_failed): from
        # then on the Summary must stop promising that nothing has been written.
        self._disk_dirty = False
        # What _detect_install found, keyed by disk name, for the scan that is
        # on screen now. Cleared by every rescan with the selection it belongs
        # to, so a row can never carry the previous scan's answer.
        self._found_installs = {}
        # One probe mount at a time. _detect_install mounts at the single fixed
        # PROBE_MNT, and a rescan starts a second scan worker while the first
        # may still be between its mount and its umount; two of those
        # interleaved would unmount each other's disk halfway through a read.
        self._probe_lock = threading.Lock()
        # How far an update got, so _install_failed can say what state the
        # machine is ACTUALLY in instead of guessing. One of "safe" (nothing
        # replaced), "broken" (the swap started), "restored" (the swap started
        # and was put back), "finish" (the new system is in place but the run
        # stopped before it was configured). "" until an update starts.
        self._update_state = ""
        # (name, there_was_an_old_one) for every top-level directory this run
        # has swapped, in the order it swapped them -- what _restore_trees
        # walks backwards to undo a swap that stopped part-way.
        self._swapped = []

        # A destructive write must never be interruptible: block the window
        # close (the snail logo, the app-name Close item, the window manager)
        # while the install worker is running, so a stray click cannot tear the
        # window down mid-write.
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)

        # ---- layout: rail | content, then a footer ----
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.get_style_context().add_class("inst-body")
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._build_rail(), False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        body.pack_start(self.stack, True, True, 0)

        self._pages = {}
        for key, _title in self.STEPS:
            builder = getattr(self, "_page_" + key)
            page = builder()
            self._pages[key] = page
            self.stack.add_named(page, key)

        self.content.pack_start(self._build_footer(), False, False, 0)

        self._set_step(0)

    # ------------------------------------------------------------------ chrome
    def _build_rail(self):
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rail.get_style_context().add_class("inst-rail")
        rail.set_size_request(248, -1)

        brand = Gtk.Label(label=_t("Install"), xalign=0)
        brand.get_style_context().add_class("inst-rail-brand")
        rail.pack_start(brand, False, False, 0)
        sub = Gtk.Label(label=OS_PRETTY, xalign=0)
        sub.get_style_context().add_class("inst-rail-sub")
        rail.pack_start(sub, False, False, 0)

        self._rail_rows = []
        for i, (_key, title) in enumerate(self.STEPS):
            row = Gtk.Box(spacing=12)
            row.get_style_context().add_class("inst-step")
            num = Gtk.Label(label=str(i + 1))
            num.get_style_context().add_class("inst-step-num")
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("inst-step-lbl")
            row.pack_start(num, False, False, 0)
            row.pack_start(lbl, False, False, 0)
            # A real toggle exposes focus, activation and current-step state to
            # keyboard and assistive technology. Future/destructive steps are
            # disabled in _set_step; completed steps remain navigable.
            step_btn = Gtk.ToggleButton()
            step_btn.set_relief(Gtk.ReliefStyle.NONE)
            step_btn.get_style_context().add_class("inst-step-hit")
            step_btn.get_accessible().set_name(_t(title))
            step_btn._rail_hid = step_btn.connect(
                "clicked", self._on_rail_click, i)
            step_btn.add(row)
            rail.pack_start(step_btn, False, False, 0)
            self._rail_rows.append((step_btn, row, num, lbl))
        return rail

    def _on_rail_click(self, _w, i):
        # Backward navigation to a step already reached. Blocked while the
        # destructive worker runs and once the run has begun (Install / Done),
        # and never allowed to skip ahead of the furthest validated step.
        if self._working:
            return True
        if self._step >= self._steps_index("progress"):
            return True
        if i == self._step or i > self._max_reached:
            return True
        # Preserve whatever the user has entered on the current step first.
        self._commit_step()
        self._set_step(i)
        return True

    def _build_footer(self):
        # spacing=10: Back and Next were packed flush against each other, so the
        # two buttons read as one joined control -- and the pair that must never
        # be confused for one another is precisely "go back" and "continue" in a
        # wizard whose last step erases a disk.
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        foot.get_style_context().add_class("inst-footer")
        self._foot_status = Gtk.Label(xalign=0)
        self._foot_status.get_style_context().add_class("inst-foot-status")
        foot.pack_start(self._foot_status, False, False, 0)
        # Why the forward button is greyed out, beside the button itself. The
        # hint used to sit at the foot of the (scrolling) Options page, so on a
        # 768px panel it was below the fold: Next went grey with the explanation
        # off-screen and nothing to say what was wrong.
        self._foot_hint = Gtk.Label(xalign=0)
        self._foot_hint.get_style_context().add_class("inst-hint")
        self._foot_hint.set_ellipsize(Pango.EllipsizeMode.END)
        self._foot_hint.set_margin_start(18)
        foot.pack_start(self._foot_hint, True, True, 0)

        self.back_btn = Gtk.Button(label=_t("Back"))
        self.back_btn.get_style_context().add_class("inst-btn")
        self.back_btn.connect("clicked", lambda *_: self._on_back())
        self.next_btn = Gtk.Button(label=_t("Next"))
        self.next_btn.get_style_context().add_class("inst-btn")
        self.next_btn.get_style_context().add_class("inst-next")
        self.next_btn.connect("clicked", lambda *_: self._on_next())
        self.back_btn.set_tooltip_text(_t("This is the first step."))
        self.next_btn.set_tooltip_text(_t("The system to install is not available."))
        foot.pack_end(self.next_btn, False, False, 0)
        foot.pack_end(self.back_btn, False, False, 0)
        return foot

    def _page_scaffold(self, title, subtitle=None):
        """A scrollable content page with a serif heading; returns (outer, col).

        The inset is PADDING on the .inst-page box (in the CSS), NOT margins. A GTK
        margin is transparent, so on the software render stack it exposes the
        ScrolledWindow viewport's unpainted (black) background around the content —
        that was the "black frame on Options and beyond" (it only showed on the
        pages tall enough to scroll). Padding keeps the papertone .inst-page
        background filling the entire allocation. hexpand/vexpand make the box fill
        the viewport so no black edge is ever exposed; NONE shadow drops the frame."""
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.set_shadow_type(Gtk.ShadowType.NONE)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        col.get_style_context().add_class("inst-page")
        col.set_hexpand(True)
        col.set_vexpand(True)
        # `col` stays full-width so the papertone background covers the whole
        # viewport (see the docstring above); `inner` is the capped column the
        # page's own content goes into. Callers get `inner`, so no page builder
        # changes.
        inner = PageColumn(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_hexpand(False)
        inner.set_halign(Gtk.Align.START)
        col.pack_start(inner, False, False, 0)
        h = Gtk.Label(label=title, xalign=0)
        h.get_style_context().add_class("inst-h1")
        inner.pack_start(h, False, False, 0)
        # A page whose STATE can change has to be able to change what it says:
        # the progress page went on reading "Installing" / "keep the computer
        # switched on" over a run that had already stopped. Hand the two labels
        # back on the column so a builder that needs them can keep them.
        inner.page_title = h
        inner.page_sub = None
        if subtitle:
            s = Gtk.Label(label=subtitle, xalign=0)
            s.get_style_context().add_class("inst-sub")
            s.set_line_wrap(True)
            inner.pack_start(s, False, False, 0)
            inner.page_sub = s
        outer.add(col)
        return outer, inner

    def _card(self, parent, top=14):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        c.get_style_context().add_class("inst-card")
        c.set_margin_top(top)
        parent.pack_start(c, False, False, 0)
        return c

    def _danger(self, parent, text, top=18):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.get_style_context().add_class("inst-danger")
        box.set_margin_top(top)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("inst-danger-txt")
        lbl.set_line_wrap(True)
        box.pack_start(lbl, True, True, 0)
        parent.pack_start(box, False, False, 0)
        return lbl

    def _para(self, parent, text, top=16, cls="inst-para"):
        p = Gtk.Label(label=text, xalign=0)
        p.get_style_context().add_class(cls)
        p.set_line_wrap(True)
        p.set_margin_top(top)
        parent.pack_start(p, False, False, 0)
        return p

    def _field_row(self, card, label, widget, first=False, sub=None):
        r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        r.get_style_context().add_class("inst-item")
        if not first:
            r.get_style_context().add_class("bordered")
        lblbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.get_style_context().add_class("inst-label")
        lblbox.pack_start(lbl, False, False, 0)
        if sub:
            sl = Gtk.Label(label=sub, xalign=0)
            sl.get_style_context().add_class("inst-sublabel")
            sl.set_line_wrap(True)
            # Cap the NATURAL width. Without it a long hint asks for its whole
            # length, and since the label column is packed start and the control
            # end, the hint grows straight into the control beside it.
            sl.set_max_width_chars(52)
            lblbox.pack_start(sl, False, False, 0)
        r.pack_start(lblbox, False, False, 0)
        r.pack_end(widget, False, False, 0)
        card.pack_start(r, False, False, 0)
        return r

    # --------------------------------------------------------------- 1 welcome
    def _page_welcome(self):
        outer, col = self._page_scaffold(
            "Install Notebook OS",
            "This installs %s onto a disk in this machine." % OS_PRETTY)
        if self.medium_ok:
            # Starts where the subtitle above stops. It used to open with
            # "This copies %s onto a disk inside this machine", which is the
            # sentence directly above it said twice — the first screen anyone
            # ever sees, spending its first line on nothing new.
            self._para(col,
                       "The steps ask for a computer name, a keyboard layout "
                       "and an administrator password, and set the machine up "
                       "to start from the disk. When it is finished, remove "
                       "the installer and restart.")
            self._danger(col,
                         "The chosen disk will be wiped completely, and "
                         "everything on it is gone for good. Nothing is "
                         "written to any disk before the Summary step is "
                         "confirmed.")
        else:
            # Neutral state: no live medium (construct-all / a normal desktop
            # session). The whole destructive path stays unreachable.
            self._para(col,
                       "There is nothing here to install. This installer runs "
                       "only when the computer is started from a %s installer "
                       "USB stick or disc." % OS_NAME,
                       cls="inst-para")
            note = self._para(col,
                              "Start the machine from the %s installer and open "
                              "this app again from that desktop." % OS_NAME,
                              cls="inst-note")
            note.set_margin_top(10)
        return outer

    # ---------------------------------------------------------------- 2 target
    def _page_target(self):
        outer, col = self._page_scaffold(
            "Choose the disk",
            "Choose the disk to install onto. The disk the installer was "
            "started from is never listed.")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top.set_margin_top(6)
        # "Look again", not "Rescan": the Settings Backup page has always
        # called this same action Look again, and "scan" names the machine's
        # mechanism where the button can name what the person gets.
        rescan = Gtk.Button(label=_t("Look again"))
        rescan.get_style_context().add_class("inst-btn")
        rescan.connect("clicked", lambda *_: self._refresh_disks())
        top.pack_end(rescan, False, False, 0)
        col.pack_start(top, False, False, 0)

        self._disk_card = self._card(col, top=8)

        # The choice between replacing the system and erasing the disk. Built
        # once and shown only when _detect_install has read a Notebook OS
        # marker off the disk the user has just picked, because those are the
        # only disks where both answers exist. It sits between the disk list
        # and the consequence banner, which is the reading order: which disk,
        # what to do to it, what that costs.
        self._mode_card = self._card(col, top=14)
        self._mode_card.set_no_show_all(True)
        self._mode_card.hide()
        # The same hidden-anchor group leader the disk list uses. The first
        # radio in a group is active from birth and its "toggled" never fires,
        # so without an anchor this card could not tell "Update is chosen"
        # from "nobody has chosen anything yet" -- and the second of those,
        # read as the first, is a disk erased by a wizard that said it would
        # be updated.
        self._mode_anchor = Gtk.RadioButton()
        self._rb_update = Gtk.RadioButton.new_from_widget(self._mode_anchor)
        self._rb_update.set_label(_t("Update the system on this disk"))
        self._rb_fresh = Gtk.RadioButton.new_from_widget(self._mode_anchor)
        self._rb_fresh.set_label(_t("Erase this disk and install fresh"))
        self._mode_update_sub = self._mode_row(
            self._mode_card, self._rb_update,
            _t("The home folder, the settings and every file on the disk are "
               "kept. The system, the kernel and the start-up files are "
               "replaced."), first=True)
        self._mode_fresh_sub = self._mode_row(
            self._mode_card, self._rb_fresh,
            _t("Everything on the disk is removed. No file and no setting "
               "on it is kept."))
        self._rb_update.connect("toggled", self._on_mode_toggle, "update")
        self._rb_fresh.connect("toggled", self._on_mode_toggle, "install")

        self._disk_erase = self._danger(col, "", top=18)
        self._disk_erase.get_parent().set_no_show_all(True)
        self._disk_erase.get_parent().hide()
        self._disk_group = None
        return outer

    def _mode_row(self, card, radio, sub, first=False):
        """One choice in the install/update card: the radio, and under it the
        sentence saying what it does to the disk.

        Returns the sub-label rather than the row, because what a choice costs
        depends on the disk: a disk too small to be partitioned for a fresh
        install can still be updated in place, and that row has to be able to
        say so where the choice is made (see _show_mode_card)."""
        row = Gtk.Box()
        row.get_style_context().add_class("inst-item")
        if not first:
            row.get_style_context().add_class("bordered")
        # The sentence is a SIBLING of the radio, not its child, and indented
        # to clear the indicator -- a GtkCheckButton handed a box instead of a
        # label does not offset the child past its circle, and the text draws
        # straight through it. The disk rows above learnt this the same way.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        radio.get_style_context().add_class("inst-disk")
        rlbl = radio.get_child()
        if isinstance(rlbl, Gtk.Label):
            # Translated, either label runs half again as long, and a radio's
            # label neither wraps nor ellipsizes on its own: one long line here
            # would set the whole wizard's minimum width, because a Stack is as
            # wide as its widest page.
            rlbl.set_line_wrap(True)
            rlbl.set_max_width_chars(58)
            rlbl.set_xalign(0)
        box.pack_start(radio, False, False, 0)
        lbl = Gtk.Label(label=sub, xalign=0)
        lbl.get_style_context().add_class("inst-disk-sub")
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(58)
        lbl.set_margin_start(26)      # clears the radio's indicator
        box.pack_start(lbl, False, False, 0)
        row.pack_start(box, True, True, 0)
        card.pack_start(row, False, False, 0)
        return lbl

    def _hide_mode_card(self):
        """Put the install/update choice away, and genuinely un-choose it.

        Lighting the hidden anchor clears BOTH visible radios. Leaving "Update"
        lit from the previous disk would make the set_active in _show_mode_card
        a no-op, its "toggled" would never fire, and cfg["mode"] would keep
        whatever the last disk decided -- which is how a wizard ends up erasing
        a disk it has just told somebody it would update."""
        card = getattr(self, "_mode_card", None)
        if card is None:
            return
        self._mode_anchor.set_active(True)
        card.set_no_show_all(True)
        card.hide()
        self._refresh_rail_titles()

    def _refresh_rail_titles(self):
        """The rail names the step. During an update the fifth step is not an
        install, and a rail that says it is has stopped describing the run the
        user is watching."""
        i = self._steps_index("progress")
        rows = getattr(self, "_rail_rows", [])
        if 0 <= i < len(rows):
            rows[i][3].set_text(_t("Update") if self._is_update()
                                else _t("Install"))

    def _refresh_disks(self):
        # Enumeration shells out to lsblk (several times, incl. per-mount
        # pkname probes), so it runs on a worker thread — the GTK main loop is
        # never blocked on a subprocess. A scan generation guards against a
        # stale result rendering after the user rescanned or navigated away.
        self._scan_gen += 1
        gen = self._scan_gen
        # Remember the current choice so a rescan (or coming back to this step)
        # re-selects the same disk instead of silently forgetting it.
        self._prev_disk = self.cfg.get("disk")
        # Remembered with the disk it belongs to, for the same reason: leaving
        # this step and coming back rescans, and a deliberate "erase this disk
        # and install fresh" that quietly reverted to Update on the way back
        # would be the wizard changing its mind about somebody's disk without
        # saying so.
        self._prev_mode = self.cfg.get("mode") or ""
        self._disk_group = None
        self.cfg["disk"] = None
        self.cfg["disk_contents"] = ""
        # Both halves of "what is on this disk" belong to the selection that is
        # being thrown away, so both go with it. A stale cfg["existing"] would
        # let the Summary describe an update of a disk nobody has picked yet.
        self.cfg["existing"] = None
        self.cfg["mode"] = "install"
        self._found_installs = {}
        self._hide_mode_card()
        card = self._disk_card
        for ch in card.get_children():
            card.remove(ch)
        self._disk_msg_row(card, "Looking for disks…")
        card.show_all()
        self._erase_parent_hide()
        self._validate()
        if not self.tools.get("lsblk"):
            # No enumerator available at all — no subprocess to run; render the
            # honest message inline.
            self._populate_disks(gen, None)
            return
        threading.Thread(target=self._scan_disks_worker, args=(gen,),
                         daemon=True).start()

    def _scan_disks_worker(self, gen):
        try:
            disks = self._list_disks()
        except Exception:
            disks = []
        # Look for an existing install on the SAME worker. The probe mounts a
        # filesystem, which is far too slow for the GTK main loop (the reason
        # the enumeration moved off it in the first place), and doing it here
        # means the disk list can say "Notebook OS is already installed here"
        # in the row where disks are being compared rather than three screens
        # later. Guarded per disk: one unreadable disk must not cost the scan.
        found = {}
        for entry in disks:
            if self._closed or gen != self._scan_gen:
                break
            try:
                info = self._detect_install(entry[0])
            except Exception:                                  # noqa: BLE001
                info = None
            if info:
                found[entry[0]] = info
        if not self._closed:
            GLib.idle_add(self._populate_disks, gen, disks, found)

    def _disk_msg_row(self, card, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("inst-value")
        lbl.set_line_wrap(True)
        row = Gtk.Box()
        row.get_style_context().add_class("inst-item")
        row.pack_start(lbl, True, True, 0)
        card.pack_start(row, False, False, 0)
        return lbl

    def _populate_disks(self, gen, disks, found=None):
        # Runs on the main thread via idle_add. Drop the result if a newer scan
        # (or a step change) has superseded it.
        if self._closed or gen != self._scan_gen:
            return False
        # Defaulted rather than required: several suites call this directly
        # with a disk list and nothing else, and a scan that could not read any
        # disk's marker is exactly the same situation as one that found none --
        # no disk offers an update, and every one of them offers an install.
        found = found or {}
        self._found_installs = found
        card = self._disk_card
        for ch in card.get_children():
            card.remove(ch)
        if not disks:
            # disks is None → lsblk absent; [] → none eligible.
            if disks is None or not self.tools.get("lsblk"):
                self._disk_msg_row(card, "This computer's disks cannot be "
                                         "listed.")
            else:
                self._disk_msg_row(card, "No disk was found that can be "
                                         "installed onto. Attach one and press "
                                         "Look again. The installer's own disk "
                                         "is never offered.")
            card.show_all()
            self._erase_parent_hide()
            self._validate()
            return False
        # A hidden anchor radio is the group leader and stays active, so every
        # VISIBLE disk radio starts genuinely unselected — selecting any one
        # (including the only one) then fires 'toggled' and enables Next. Without
        # this, a sole radio is active-by-default and its toggle never fires, so
        # the disk could not be chosen.
        self._disk_anchor = Gtk.RadioButton()
        radios = []
        need = self._min_disk_bytes()
        usable = 0
        for i, (name, size, model, contents) in enumerate(disks):
            dev = "/dev/" + name
            existing = found.get(name)
            # A disk that already carries Notebook OS is never greyed out for
            # size. An update replaces the system inside the root partition
            # that is already there and never repartitions, so the figure that
            # refuses a disk for a FRESH install says nothing at all about
            # whether it can be updated -- and greying the row would take the
            # update away with it, on precisely the machines that need one.
            too_small = self._disk_too_small(size) and not existing
            r = Gtk.Box(spacing=12)
            r.get_style_context().add_class("inst-item")
            if i != 0:
                r.get_style_context().add_class("bordered")
            try:
                # Muted with the rest of the row when the disk cannot be
                # chosen: a pixbuf takes no state from GTK, so an icon left at
                # full strength is the one part of a greyed row still saying
                # "pick me".
                img = nbicons.image("disk", 20,
                                    "#B3AD9E" if too_small else "#6E695E")
                img.set_valign(Gtk.Align.START)
                img.set_margin_top(3)
                r.pack_start(img, False, False, 0)
            except Exception:
                # icon renderer unavailable — keep the row, drop the glyph.
                pass
            rb = Gtk.RadioButton.new_from_widget(self._disk_anchor)
            rb.set_active(False)
            rb.get_style_context().add_class("inst-disk")
            desc = model or "Disk"
            # Trim the maker's model string, not the whole line: the device
            # path at the end is what actually identifies the disk (it is what
            # every later screen names), so it must never be the part that
            # ellipsizes away.
            if len(desc) > 44:
                desc = desc[:43].rstrip() + "…"
            rb.set_label("%s  —  %s  (%s)" % (desc, human_bytes(size), dev))
            # Real disks carry long model strings ("WD Black SN850X 1TB NVMe
            # SSD"), and a radio's label neither wraps nor ellipsizes: one such
            # disk set the whole wizard's minimum width past a 1024px panel and
            # pushed the Next button off-screen on every step, because a Stack
            # is as wide as its widest page. Cap the natural width and let it
            # ellipsize; the device path is what identifies it, and it is last.
            rb_lbl = rb.get_child()
            if isinstance(rb_lbl, Gtk.Label):
                rb_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                rb_lbl.set_max_width_chars(52)
            rb.connect("toggled", self._on_disk_toggle, dev, model, size,
                       contents, existing)
            # What is on the disk goes UNDER its name, indented to the radio's
            # label. A maker's model number is not how anyone recognises their
            # own computer's disk — what is on it is, and recognising it is the
            # only real defence against erasing the wrong one.
            # (The line is a sibling of the radio, not its child: a GtkCheckButton
            # given a box instead of a label does not offset the child past its
            # indicator, and the text drew straight through the circle.)
            col2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            col2.pack_start(rb, False, False, 0)
            # A disk too small to hold the system cannot be chosen at all. The
            # install erases before it extracts, so "try it and see" costs the
            # user everything that was on the disk. The reason goes in the row
            # itself — a greyed-out line with no explanation is just a bug.
            if too_small:
                line = _t("Too small: needs at least %s") % human_bytes(need)
            else:
                usable += 1
                line = self._contents_line(contents)
                if existing:
                    # On its own line above the contents, for every disk rather
                    # than only the chosen one: this is the fact that decides
                    # which of the two things the wizard can do to it, and
                    # comparing disks is what this screen is for.
                    line = "\n".join(x for x in (self._existing_line(existing),
                                                 line) if x)
            if line:
                sub = Gtk.Label(label=line, xalign=0)
                sub.get_style_context().add_class("inst-disk-sub")
                sub.set_line_wrap(True)
                sub.set_max_width_chars(58)
                sub.set_margin_start(26)   # clears the radio's indicator
                col2.pack_start(sub, False, False, 0)
            r.pack_start(col2, True, True, 0)
            card.pack_start(r, False, False, 0)
            if too_small:
                # Insensitive on the ROW, so neither the radio nor its text can
                # be clicked and the whole line reads as unavailable.
                r.set_sensitive(False)
            radios.append((rb, dev, too_small))
        if not usable:
            self._disk_msg_row(
                card,
                _t("No disk here is big enough for %s. Attach a disk of at "
                   "least %s and press Look again.")
                % (OS_NAME, human_bytes(need)))
        card.show_all()
        self._erase_parent_hide()
        # Re-select the disk chosen before a rescan / step change if it is still
        # present, so the user's choice (and the enabled Next) survives. Done
        # after _erase_parent_hide so the toggle's erase banner stays visible.
        # A now-too-small disk is never re-selected (the swap size can grow
        # between one visit to this step and the next).
        if self._prev_disk:
            for rb, dev, small in radios:
                if dev == self._prev_disk and not small:
                    rb.set_active(True)
                    break
        self._validate()
        return False

    def _erase_parent_hide(self):
        p = self._disk_erase.get_parent()
        p.set_no_show_all(True)
        p.hide()

    def _on_disk_toggle(self, btn, dev, model, size, contents="",
                        existing=None):
        if not btn.get_active():
            return
        self.cfg["disk"] = dev
        self.cfg["disk_model"] = model or "Disk"
        self.cfg["disk_size"] = size
        self.cfg["disk_contents"] = contents
        self.cfg["existing"] = existing
        self._show_mode_card(existing)
        self._refresh_disk_banner()
        self._validate()

    def _show_mode_card(self, existing):
        """Offer the install/update choice for the disk just chosen, or put it
        away when there is nothing on that disk to update.

        Update is what the card lands on. Of the two things this wizard can do
        to a disk only one of them destroys the user's files, and that one must
        never be where a click ends up by default."""
        card = getattr(self, "_mode_card", None)
        if card is None:
            return
        if not existing:
            self.cfg["mode"] = "install"
            self._hide_mode_card()
            self._refresh_row_states()
            return
        # A disk too small to be partitioned for a fresh install can still be
        # updated in place (see _populate_disks), so its row stays selectable
        # and it is the FRESH half that is refused -- with the figure, in the
        # row where the choice is being made, rather than two screens later.
        small = self._disk_too_small(self.cfg.get("disk_size"))
        self._rb_fresh.set_sensitive(not small)
        self._mode_fresh_sub.set_text(
            (_t("Too small for a fresh install: that needs a disk of at "
                "least %s.") % human_bytes(self._min_disk_bytes())) if small
            else _t("Everything on the disk is removed. No file and no "
                    "setting on it is kept."))
        card.set_no_show_all(False)
        card.show_all()
        # Update unless this is the very same disk the user had already chosen
        # to erase. Restoring the other choice is only ever done for a choice
        # they actually made about THIS disk, so the default a click lands on
        # is still the one that destroys nothing.
        if (self._prev_mode == "install"
                and self.cfg.get("disk") == self._prev_disk
                and self._rb_fresh.get_sensitive()):
            self._rb_fresh.set_active(True)
        else:
            self._rb_update.set_active(True)

    def _on_mode_toggle(self, btn, mode):
        if not btn.get_active():
            return
        self.cfg["mode"] = mode
        self._refresh_disk_banner()
        self._refresh_rail_titles()
        # The Options step asks nothing during an update: every answer it
        # collects is already on the disk and is kept.
        self._refresh_row_states()
        self._validate()

    def _refresh_disk_banner(self):
        """The consequence panel under the disk list, for whichever of the two
        things the wizard is now going to do to the chosen disk.

        Three states, and only one of them is red. Red means "this destroys
        something" and nothing else (see the CSS note on .inst-primary): an
        update destroys no file of the user's, and a disk we have READ and
        found blank has nothing to destroy, so both get the same panel shape in
        paper and ink saying the true thing. Red that appears over an empty
        disk is red that has stopped meaning anything on the disk holding
        somebody's photos.

        No contents in any of these: every row already carries its own "On it
        now" line a few pixels above, for every disk rather than only the
        chosen one, which is what lets someone COMPARE before clicking. This
        banner's job is the consequence. The contents reappear on the Summary
        and in the final confirmation, where no disk row is on screen to check
        against."""
        dev = self.cfg.get("disk")
        if not dev:
            self._erase_parent_hide()
            return
        p = self._disk_erase.get_parent()
        p.set_no_show_all(False)
        if self._is_update():
            calm = True
            self._disk_erase.set_text(
                _t("The system on %s is replaced. The home folder, the "
                   "settings and every file on the disk are kept. The machine "
                   "will not start up until the update has finished.") % dev)
        elif self.cfg.get("disk_contents") == "EMPTY":
            calm = True
            self._disk_erase.set_text(
                _t("%s is empty. It will be set up from scratch for %s.")
                % (dev, OS_PRETTY))
        else:
            calm = False
            self._disk_erase.set_text(
                _t("Everything on %s will be erased for good: every file, "
                   "photo and program on it. Check that this is the right "
                   "disk.") % dev)
        self._set_calm(self._disk_erase, calm)
        p.show_all()

    def _set_calm(self, lbl, calm):
        """Paper-and-ink or signage red, on a .inst-danger panel and the text
        inside it.

        Both nodes, always: the panel carries the background and the border and
        the label carries the ink, so a class added to one of them leaves the
        other half of the panel arguing with it."""
        for w in (lbl.get_parent(), lbl):
            ctx = w.get_style_context()
            if calm:
                ctx.add_class("calm")
            else:
                ctx.remove_class("calm")

    def _list_disks(self):
        """Whole disks via `lsblk -dnbr -o NAME,SIZE,MODEL,TYPE`, keeping
        TYPE==disk and dropping the live medium / running-system disks. Each
        entry also carries what is on the disk NOW, so the person choosing can
        recognise it (see _disk_contents)."""
        lsblk = self.tools.get("lsblk")
        if not lsblk:
            return []
        rc, out = run_cmd([lsblk, "-dnbr", "-o", "NAME,SIZE,MODEL,TYPE"])
        if rc != 0:
            return []
        excluded = self._excluded_disks()
        disks = []
        for ln in out.splitlines():
            parts = ln.split()
            if len(parts) < 3:
                continue
            if parts[-1] != "disk":
                continue
            name = parts[0]
            if name in excluded:
                continue
            try:
                size = int(parts[1])
            except ValueError:
                size = 0
            model = ""
            if len(parts) > 3:
                model = " ".join(parts[2:-1]).replace("\\x20", " ").strip()
            disks.append((name, size, model, self._disk_contents(name)))
        return disks

    def _disk_contents(self, name):
        """What is on /dev/<name> right now, in words, or "" if it cannot be
        read. Runs on the scan worker thread (never the GTK main loop).

        `-P` (key="value" pairs), not `-r`: raw output separates fields with a
        space and writes an EMPTY field as nothing at all, so a partition with
        no label silently shifts every later column left and the disk gets
        described as something it is not. On the one screen where being wrong
        destroys a person's files, an ambiguous parse is not acceptable."""
        lsblk = self.tools.get("lsblk")
        if not lsblk:
            return ""
        out = ""
        # PARTTYPE names the EFI partition; it is not in every lsblk, so fall
        # back to the columns that have been there forever rather than losing
        # the whole description over one missing one.
        for cols in ("NAME,SIZE,FSTYPE,LABEL,TYPE,PARTTYPE",
                     "NAME,SIZE,FSTYPE,LABEL,TYPE"):
            rc, out = run_cmd([lsblk, "-Pnb", "-o", cols, "/dev/" + name])
            if rc == 0:
                break
            out = ""
        parts = []
        for ln in out.splitlines():
            d = dict(re.findall(r'([A-Z]+)="([^"]*)"', ln))
            kind = d.get("TYPE")
            if kind not in ("part", "disk"):
                continue
            try:
                sz = int(d.get("SIZE") or 0)
            except ValueError:
                sz = 0
            fstype = (d.get("FSTYPE") or "").lower()
            # A stick or disk formatted WHOLE, with no partition table at all,
            # is extremely common on USB media — and it reports one row of
            # TYPE=disk carrying the filesystem. Counting only partitions would
            # call such a disk empty and tell someone there is nothing on it to
            # lose, which on the one screen that destroys data is the worst
            # thing this app could say.
            if kind == "disk" and not fstype:
                continue
            parts.append((sz, fstype,
                          (d.get("LABEL") or "").replace("\\x20", " ").strip(),
                          (d.get("PARTTYPE") or "").lower()))
        if not parts:
            # No recognised table/filesystem is not proof of blank media: raw
            # data and damaged/encrypted layouts have exactly this shape.
            return "" if not out.strip() else "UNKNOWN"
        # Biggest first: the partition a person recognises is the big one.
        parts.sort(key=lambda p: -p[0])
        bits = []
        for sz, fstype, label, ptype in parts[:3]:
            if ptype in ESP_TYPES:
                what = _t("Start-up files")
            else:
                what = FS_WHAT.get(fstype, "")
                what = _t(what) if what else ""
            size = human_bytes(sz) if sz else ""
            # The kind leads (it is what a person recognises); the volume's own
            # label follows only when it adds something — a Windows partition
            # is very often labelled "Windows", and "Windows "Windows"" is not
            # a sentence anyone should have to read.
            if label and label.lower() == (what or "").lower():
                label = ""
            if what and label:
                bits.append('%s "%s" (%s)' % (what, label, size))
            elif what:
                bits.append("%s (%s)" % (what, size))
            elif label:
                bits.append('"%s" (%s)' % (label, size))
            else:
                # An unrecognised area, stated plainly. "Something we cannot
                # name" apologises for the installer instead of telling the
                # reader the one thing they need — that this too will be wiped.
                bits.append("%s (%s)" % (_t("Something else"), size))
        extra = len(parts) - 3
        if extra > 0:
            bits.append(_t("and %d more") % extra)
        return ", ".join(bits)

    def _partition_rows(self, name):
        """Every PARTITION on /dev/<name>, as lsblk key="value" dicts.

        Separate from _disk_contents, which answers "what would a person call
        this disk?" and deliberately folds the answer down to three phrases.
        Recognising an install needs the raw fields instead, PARTUUID above
        all: that is the one thing this installer STAMPS, since every install
        writes the same fixed root PARTUUID so the prebuilt GRUB can find the
        root it is told to boot (see the release contract at the top of this
        file).

        `-P` (key="value" pairs) for the reason _disk_contents gives: raw
        output writes an EMPTY field as nothing at all, so one unlabelled
        partition shifts every later column left and the row describes
        something it is not."""
        lsblk = self.tools.get("lsblk")
        if not lsblk:
            return []
        out = ""
        # The columns as columns, and the optional one named on its own line.
        # PARTTYPE is missing from older lsblk builds and losing it costs only
        # the start-up partition's type (its label is the fallback), so the
        # query is retried without it rather than the whole probe being lost
        # over one column — and written this way the difference between the two
        # attempts is a word rather than two long strings to diff by eye.
        wanted = ("NAME", "FSTYPE", "LABEL", "PARTUUID", "TYPE")
        for extra in (("PARTTYPE",), ()):
            cols = ",".join(wanted + extra)
            rc, out = run_cmd([lsblk, "-Pn", "-o", cols, "/dev/" + name])
            if rc == 0:
                break
            out = ""
        rows = []
        for ln in out.splitlines():
            d = dict(re.findall(r'([A-Z]+)="([^"]*)"', ln))
            if d.get("TYPE") == "part" and d.get("NAME"):
                rows.append(d)
        return rows

    def _detect_install(self, name):
        """An existing Notebook OS install on /dev/<name>, or None.

        Runs on the scan worker thread, NEVER the GTK main loop: it mounts.

        NO NEW MARKER IS INVENTED HERE. Everything checked is something an
        install already stamps, cheapest first:

          1. the fixed root PARTUUID on a partition of this disk, or failing
             that the ext4 label ROOT_LABEL, which is what an install made
             before the PARTUUID was pinned carries;
          2. ID=notebookos in /etc/os-release, which _write_os_release writes;
          3. /opt/notebook beside it, the directory the whole desktop lives in.

        The last two are the ones that decide, and they are read off a MOUNTED
        filesystem rather than believed from a label, because anybody can
        label a partition "notebookos" and being wrong here means offering to
        keep files that are not there -- on a screen whose other button erases
        a disk. A guess is not good enough on this screen.

        The mount is read-only, and `noload` is tried before plain `ro` so that
        merely LOOKING at a disk never replays its ext4 journal. A probe that
        writes to a disk nobody has agreed to touch has already broken the
        promise this step makes, whatever it writes."""
        rows = self._partition_rows(name)
        if not rows:
            return None
        want = ROOT_PARTUUID.lower()
        rootdev = espdev = ""
        for d in rows:
            dev = "/dev/" + d["NAME"]
            fstype = (d.get("FSTYPE") or "").lower()
            label = (d.get("LABEL") or "").replace("\\x20", " ").strip()
            ptype = (d.get("PARTTYPE") or "").lower()
            if not rootdev:
                if (d.get("PARTUUID") or "").lower() == want:
                    rootdev = dev
                elif label == ROOT_LABEL and fstype.startswith("ext"):
                    rootdev = dev
            if not espdev and (ptype in ESP_TYPES or label == ESP_LABEL):
                espdev = dev
        # No ESP, no update offered. The kernel and the bootloader live on it,
        # and a new system left starting the old kernel is a machine that may
        # not come back at all -- so a disk this installer cannot finish is a
        # disk it does not offer to start. A fresh install, which creates the
        # ESP itself, is still offered for that disk.
        if not rootdev or not espdev:
            return None
        with self._probe_lock:
            if not self._mount_probe(rootdev):
                return None
            try:
                info = self._probe_install(PROBE_MNT)
            finally:
                self._umount_probe()
        if not info:
            return None
        info["disk"] = "/dev/" + name
        info["root"] = rootdev
        info["esp"] = espdev
        return info

    def _mount_probe(self, dev):
        """Mount `dev` read-only at PROBE_MNT. False when it cannot be read.

        Never raises and never reports: a disk that will not mount is simply a
        disk with no install on it as far as this step is concerned, and it is
        still offered for a fresh install like any other."""
        mount = self.tools.get("mount")
        if not mount:
            return False
        try:
            os.makedirs(PROBE_MNT, exist_ok=True)
        except OSError:
            return False
        if self._is_mounted(PROBE_MNT):
            # A probe that died between its mount and its umount would
            # otherwise stack a second filesystem on the same point and hide
            # the one underneath from every later scan.
            self._umount_probe()
        for opts in ("ro,noload", "ro"):
            rc, _out = run_cmd([mount, "-o", opts, dev, PROBE_MNT], timeout=20)
            if rc == 0:
                return True
        return False

    def _umount_probe(self):
        umount = self.tools.get("umount")
        if umount:
            run_cmd([umount, PROBE_MNT], timeout=20)

    def _probe_install(self, mnt):
        """Read the marker off a mounted filesystem: what the wizard needs in
        order to describe the install, or None when this is not one.

        It looks in the normal place first and then inside UPDATE_OLD_DIR,
        because that is exactly where /etc will be if an earlier update stopped
        between moving the old system aside and putting the new one in place.
        Refusing to recognise such a disk would take away the one action that
        repairs it -- and running the update again is precisely what the
        failure page tells its owner to do."""
        base = mnt
        rel = self._read_marker(base)
        if rel is None:
            base = os.path.join(mnt, UPDATE_OLD_DIR)
            rel = self._read_marker(base)
        if rel is None:
            return None
        try:
            free = shutil.disk_usage(mnt).free
        except OSError:
            free = 0
        # halfway: the swap had started, so this machine is NOT startable as it
        # stands. leftover: only the staging tree is there, which means a run
        # stopped while unpacking and the system on the disk is untouched. The
        # two say different things to the person reading the failure page, and
        # only one of them is frightening.
        halfway = os.path.isdir(os.path.join(mnt, UPDATE_OLD_DIR))
        leftover = os.path.isdir(os.path.join(mnt, UPDATE_STAGE_DIR))
        return {"version": rel.get("VERSION", ""),
                "build": rel.get("BUILD_ID", ""),
                "user": self._read_line(os.path.join(base, "etc",
                                                     "notebookos-user")),
                # Where the configuration to carry over lives, relative to the
                # mount point. Stored rather than absolute: the disk is mounted
                # somewhere else entirely when the update actually runs.
                "config_sub": "" if base == mnt else UPDATE_OLD_DIR,
                "halfway": halfway,
                "leftover": leftover,
                "unfinished": halfway or leftover,
                "free": free}

    def _read_marker(self, base):
        """The parsed /etc/os-release under `base` when it says ID=notebookos
        AND the desktop is there beside it, otherwise None.

        Both, not either. os-release is a text file anybody can copy onto a
        disk; /opt/notebook is where the system this installer would replace
        actually is. Requiring the pair is what keeps "there is an install
        here" from being an assertion about a filename."""
        data = {}
        try:
            with open(os.path.join(base, "etc", "os-release"),
                      encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    key, _sep, val = ln.strip().partition("=")
                    if key:
                        data[key] = val.strip().strip('"')
        except OSError:
            return None
        if data.get("ID") != OS_ID:
            return None
        if not os.path.isdir(os.path.join(base, "opt", "notebook")):
            return None
        return data

    def _read_line(self, path):
        """The first line of a small config file, or "" if it is not there."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.readline().strip()
        except OSError:
            return ""

    def _read_text(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def _existing_line(self, info):
        """The one sentence saying a disk already carries this system."""
        if not info:
            return ""
        build = info.get("build") or ""
        if build:
            return (_t("%s is already installed here (build %s).")
                    % (OS_PRETTY, build))
        return _t("%s is already installed here.") % OS_PRETTY

    def _is_update(self):
        """True only when the wizard is going to replace a system it has
        actually FOUND. Mode alone is not enough: an "update" of nothing would
        spend the whole run discovering there was nothing to update, and every
        screen and both engines read this one answer so the words on the
        Summary can never describe a different run from the one that starts."""
        return (self.cfg.get("mode") == "update"
                and bool(self.cfg.get("existing")))

    @staticmethod
    def _kbd_index(code):
        for i, (_lbl, c) in enumerate(KBD_LAYOUTS):
            if c == code:
                return i
        return 0

    def _min_disk_bytes(self, swap_mib=None):
        """The smallest disk this install can land on, in bytes.

        ESP + swap + the payload with headroom. `swap_mib` defaults to what the
        Options step currently holds, so the disk step gates on the swap the
        user has actually asked for rather than a guess.

        Returns 0 when there is no payload to measure: with no rootfs.tar there
        is nothing to install and _install_ready already refuses, and gating
        every disk off a size of zero would grey out the whole list for the
        wrong reason.
        """
        if not self.payload_bytes:
            return 0
        mib = ESP_SIZE_MIB + MIN_FREE_MIB
        if swap_mib is None:
            swap_mib = int(self.cfg.get("swap_mib", 0)) \
                if self.cfg.get("swap") else 0
        mib += max(0, int(swap_mib))
        return int(mib * 1024 * 1024 + self.payload_bytes * PAYLOAD_HEADROOM)

    def _disk_too_small(self, size, swap_mib=None):
        need = self._min_disk_bytes(swap_mib)
        return bool(need) and int(size or 0) < need

    def _update_free_bytes(self):
        """How much room an update needs FREE on the root it is replacing.

        The whole new system is unpacked beside the old one before anything is
        moved, so for a few minutes both are on the partition at once. That is
        what buys the safety: an update that runs out of room, or meets a
        truncated tar, stops with the machine still able to start. Paying for
        it in disk space is the trade, and it is stated on the screen that
        refuses (see _install_ready) rather than discovered at 40%.

        0 when there is no payload to measure, exactly as _min_disk_bytes does:
        with no rootfs.tar there is nothing to install and _install_ready has
        already refused for that reason."""
        if not self.payload_bytes:
            return 0
        return int(self.payload_bytes * UPDATE_STAGE_HEADROOM
                   + MIN_FREE_MIB * 1024 * 1024)

    def _start_clash_probe(self):
        """Ask, on a WORKER THREAD, whether another disk already carries the
        fixed root PARTUUID. Never on the GTK main loop.

        _partuuid_clash shells out to lsblk, which walks every block device on
        the machine — a disk that has spun down, or a stuck USB stick, holds
        that call for the whole run_cmd timeout. Called inline from _set_step it
        held the MAIN LOOP for those seconds, and it ran BEFORE the stack
        switched pages: the click on "Next" had already been taken, so the
        wizard sat frozen on the Options step, repainting nothing, until lsblk
        answered. The disk enumeration on the target step was moved onto a
        worker thread for exactly this reason (see _refresh_disks); this probe
        is the one that was left behind on the main loop.

        Best-effort, as it always was. The warning it produces is reported and
        never blocking, the Summary renders immediately without waiting for it,
        and _confirm_install reads _clash_line() live — so an answer that lands
        while the Summary is on screen still reaches the final confirmation. A
        generation counter, plus the disk the question was asked about, keeps a
        slow answer from attaching itself to a later choice."""
        self._clash_gen += 1
        gen = self._clash_gen
        disk = self.cfg.get("disk") or ""
        # Never leave the previous visit's answer standing while the new one is
        # in flight: it was about whatever disk was chosen then.
        self._partuuid_other = ""
        if not disk or not self.tools.get("lsblk"):
            return
        threading.Thread(target=self._clash_worker, args=(gen, disk),
                         daemon=True).start()

    def _clash_worker(self, gen, disk):
        try:
            other = self._partuuid_clash(disk)
        except Exception:                                      # noqa: BLE001
            other = ""
        if not self._closed:
            GLib.idle_add(self._apply_clash, gen, disk, other)

    def _apply_clash(self, gen, disk, other):
        # Main thread. Drop an answer superseded by a later visit to this step,
        # or one about a disk the user has since chosen away from.
        if (self._closed or gen != self._clash_gen
                or (self.cfg.get("disk") or "") != disk):
            return False
        self._partuuid_other = other
        self._refresh_summary()
        return False

    def _partuuid_clash(self, target=None):
        """Another disk that ALREADY carries the fixed root PARTUUID, or "".

        Every install writes the same root PARTUUID, because the prebuilt GRUB
        boots `root=PARTUUID=<that>` (see the release contract at the top of
        this file). Install onto a second disk while the first still holds a
        Notebook OS root and the machine has two partitions answering to the
        same name: which one it starts from is then whichever the kernel
        happens to enumerate first, and that can change between boots — the
        symptom being a machine that boots the OLD install, with none of the
        user's new work in it, apparently at random.

        Reported, never blocked. The fix (unplug the other disk, or install
        over it instead) is the user's to make, and refusing to install would
        be a worse answer than telling them what is about to happen. Entirely
        best-effort: any failure to probe returns "" and says nothing.

        `target` names the disk the question is being asked about; it is passed
        in by _clash_worker so a probe already running cannot silently change
        which disk it is about halfway through. It defaults to the current
        choice for the callers that ask on the main thread."""
        lsblk = self.tools.get("lsblk")
        target = target or self.cfg.get("disk") or ""
        if not lsblk or not target:
            return ""
        rc, out = run_cmd([lsblk, "-Pn", "-o", "NAME,PARTUUID,TYPE,PKNAME"])
        if rc != 0:
            return ""
        want = ROOT_PARTUUID.lower()
        tname = os.path.basename(target)
        for ln in out.splitlines():
            d = dict(re.findall(r'([A-Z]+)="([^"]*)"', ln))
            if d.get("TYPE") != "part":
                continue
            if (d.get("PARTUUID") or "").lower() != want:
                continue
            parent = (d.get("PKNAME") or "").strip()
            if parent and parent != tname:
                return "/dev/" + parent
        return ""

    def _clash_line(self):
        if not self._partuuid_other:
            return ""
        return (_t("%s already has %s on it. Two installed copies on one "
                   "machine can start up in either order. Detach that disk, or "
                   "install over it instead.")
                % (self._partuuid_other, OS_PRETTY))

    def _contents_line(self, contents):
        """The one sentence that says what is on the chosen disk today."""
        if not contents:
            return ""
        if contents == "EMPTY":
            return _t("This disk is empty.")
        if contents == "UNKNOWN":
            return _t("On it now: %s") % _t("Something else")
        return _t("On it now: %s") % contents

    def _excluded_disks(self):
        """Parent-disk names backing the live medium and the running system, so
        the installer never offers to erase the medium it booted from."""
        ex = set()
        for mp in ("/", "/run/live/medium", "/run/live/ro",
                   "/boot", "/boot/efi"):
            src = self._mount_source(mp)
            d = self._parent_disk(src)
            if d:
                ex.add(d)
        return ex

    def _mount_source(self, mountpoint):
        try:
            with open("/proc/mounts") as fh:
                for ln in fh:
                    p = ln.split()
                    if len(p) >= 2 and p[1] == mountpoint:
                        return p[0]
        except OSError:
            pass
        return ""

    def _parent_disk(self, dev):
        if not dev or not dev.startswith("/dev/"):
            return ""
        base = os.path.basename(dev)
        lsblk = self.tools.get("lsblk")
        if lsblk:
            rc, out = run_cmd([lsblk, "-no", "pkname", dev])
            if rc == 0:
                line = out.strip().splitlines()
                if line and line[0].strip():
                    return line[0].strip()
                # empty pkname → dev is itself a whole disk (or a non-partitioned
                # node); its own basename is the disk.
                return base
        # Fallback partition→disk heuristic when lsblk pkname is unavailable.
        if "nvme" in base or "mmcblk" in base or "loop" in base:
            return re.sub(r"p\d+$", "", base)
        return re.sub(r"\d+$", "", base)

    # --------------------------------------------------------------- 3 options
    def _page_options(self):
        outer, col = self._page_scaffold("Options")
        # One control column: every entry, drop-down and spinner on this page
        # shares a width, so their left edges line up down the page instead of
        # stepping in and out with the length of whatever each one contains.
        ctl = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        # Shown only while the wizard is updating a system already on the disk.
        # Every answer this page collects is on that disk already and is kept,
        # so the page has nothing to ask -- and a page of greyed-out boxes with
        # no sentence saying why reads as broken rather than as answered.
        self._update_note = Gtk.Label(
            label=_t("The system on this disk is being replaced. The name, "
                     "computer name, keyboard, language and password already "
                     "on it are kept, so there is nothing to answer here."),
            xalign=0)
        self._update_note.get_style_context().add_class("inst-callout")
        self._update_note.set_line_wrap(True)
        self._update_note.set_max_width_chars(72)
        self._update_note.set_margin_top(10)
        self._update_note.set_no_show_all(True)
        col.pack_start(self._update_note, False, False, 0)

        # -- who is this for --
        # First, because the answer decides whether the rest of this page is
        # asked at all. Somebody setting a machine up FOR someone else should
        # not have to invent that person's password and then remember it long
        # enough to pass it on.
        oem_card = self._card(col, top=6)
        self._cb_oem = Gtk.CheckButton(
            label=_t("Set this up for someone else"))
        self._cb_oem.set_active(self.cfg["oem"])
        self._cb_oem.connect("toggled", self._on_oem_toggled)
        oem_row = self._field_row(
            oem_card, "Who is this for", self._cb_oem, first=True,
            sub=_t("Set on first start-up instead of here"))

        # -- identity --
        card = self._card(col, top=6)
        self._e_user = Gtk.Entry()
        self._e_user.set_width_chars(20)
        self._e_user.connect("changed", lambda *_: self._validate())
        self._e_user.connect("activate", self._activate_next)
        ctl.add_widget(self._e_user)
        name_row = self._field_row(card, "Name", self._e_user, first=True,
                                   sub=_t("Shown on the sign-in screen"))

        self._e_host = Gtk.Entry()
        self._e_host.set_text(self.cfg["hostname"])
        self._e_host.set_width_chars(20)
        self._e_host.connect("changed", lambda *_: self._validate())
        self._e_host.connect("activate", self._activate_next)
        ctl.add_widget(self._e_host)
        host_row = self._field_row(card, "Computer name", self._e_host)

        # -- region --
        # No time-zone picker. This image ships no zoneinfo database and no
        # tool that can convert one zone to another, so every clock on the
        # machine — the panel, Calendar, Journal, file dates — reads UTC no
        # matter what a picker here claimed. A control that changes nothing is
        # worse than no control; the note below says the true thing instead.
        self._grp(col, "Region")
        card2 = self._card(col)
        self._c_kbd = Gtk.ComboBoxText()
        for label, _code in KBD_LAYOUTS:
            self._c_kbd.append_text(label)
        # Start on the layout this live session is ALREADY typing with: the user
        # has just typed a hostname on it, so it is the one answer we know is
        # right for their keyboard.
        self._c_kbd.set_active(max(0, self.cfg["kbd"]))
        # Connected AFTER set_active, so starting on the layout the live
        # session already uses does not fire and wipe the password fields.
        self._c_kbd.connect("changed", self._on_kbd_changed)
        ctl.add_widget(self._c_kbd)
        kbd_row = self._field_row(card2, "Keyboard layout", self._c_kbd,
                                  first=True)
        self._c_locale = Gtk.ComboBoxText()
        for label, _code in LOCALES:
            self._c_locale.append_text(label)
        self._c_locale.set_active(0)
        ctl.add_widget(self._c_locale)
        # The sub-label used to say "leave this as Unicode", naming a standard
        # nobody installing a computer has heard of. Point at the choice on the
        # screen instead — the first entry in the list, whose own label already
        # says what it does.
        loc_row = self._field_row(card2, "Text and characters", self._c_locale,
                                  sub="Leave as the first choice unless "
                                      "another is required")
        tznote = self._para(col, "This computer keeps time in UTC and carries "
                                 "no time-zone list. Set the clock to local "
                                 "time in Settings after restarting.",
                            top=8, cls="inst-note")
        tznote.set_margin_bottom(4)

        # -- administrator password --
        # NOT a "login account". This is a single-user computer: the desktop
        # always runs as the administrator and everything you make is saved in
        # one place, so a username field here would name an account nothing
        # ever used. The password is very real, though — de/login.py asks for
        # it on the sign-in screen every time the installed machine starts and
        # every time it wakes from sleep.
        #
        # This sub-label used to say "the desktop itself does not ask for it",
        # which was true when it was written and has not been true since the
        # sign-in screen was added. It is the worst sentence on the page to get
        # wrong: somebody who believes nothing will ever ask for this password
        # types something they have no intention of remembering, and there is
        # no way back into an offline machine that will not accept it.
        self._grp(col, "Administrator password")
        self._e_pw = Gtk.Entry()
        self._e_pw.set_visibility(False)
        self._e_pw.set_width_chars(20)
        self._e_pw.connect("changed", lambda *_: self._validate())
        self._e_pw.connect("activate", self._activate_next)
        ctl.add_widget(self._e_pw)
        # The explanation belongs to the PAIR, not to the first box. Hung under
        # "Password" it made that row two lines tall while "Confirm password"
        # was one, so the two boxes sat at different heights and the first read
        # as the bigger control. Same words, once, above both — so the note is
        # packed BEFORE the card, not after it. Packed after (which is what the
        # code did while claiming otherwise) it landed flush under the card's
        # bottom edge, where it read as a caption for the last row in the card:
        # the tick that means no password is ever asked for, over a sentence
        # saying the password is asked for every time.
        self._grp_note(col, _t("This password is asked for every time the "
                               "computer starts. It cannot be recovered."))
        card3 = self._card(col)
        pwrow = self._field_row(card3, "Password", self._e_pw, first=True)
        self._e_pw2 = Gtk.Entry()
        self._e_pw2.set_visibility(False)
        self._e_pw2.set_width_chars(20)
        self._e_pw2.connect("changed", lambda *_: self._validate())
        self._e_pw2.connect("activate", self._activate_next)
        ctl.add_widget(self._e_pw2)
        pw2row = self._field_row(card3, "Confirm password", self._e_pw2)
        # Let a novice confirm what they typed rather than guess behind dots.
        self._chk_showpw = Gtk.CheckButton(label=_t("Show passwords"))
        self._chk_showpw.get_style_context().add_class("inst-check")
        self._chk_showpw.connect("toggled", self._on_showpw_toggle)
        spwrow = Gtk.Box()
        spwrow.get_style_context().add_class("inst-item")
        spwrow.get_style_context().add_class("bordered")
        spwrow.pack_start(self._chk_showpw, True, True, 0)
        card3.pack_start(spwrow, False, False, 0)
        # Greyed out together when the tick below makes the password moot, so
        # the page never asks twice for something it will then throw away.
        self._pw_rows = [pwrow, pw2row, spwrow]
        # The one security decision on the whole install, made by someone who
        # has never heard of a root console. It has to say what it ALLOWS and
        # when not to switch it on — a checkbox nobody can evaluate is not a
        # choice, it is a trap.
        #
        # What it actually does (see _configure_login): no password is set, so
        # the sign-in screen never appears and the machine starts straight into
        # the desktop. The old label promised a passwordless ROOT CONSOLE, and
        # the log line still said "left as shipped" — but as shipped there is
        # no console on tty1 at all and root's account is locked, so the one
        # thing the words named was the one thing it did not do.
        self._chk_rootless = Gtk.CheckButton(
            label=_t("Start straight into the desktop without asking for a "
                     "password"))
        self._chk_rootless.get_style_context().add_class("inst-check")
        self._chk_rootless.connect("toggled", self._on_rootless_toggle)
        # Let the tick's own label wrap: translated it is half again as long,
        # and a CheckButton label never wraps on its own, so it would set the
        # installer's minimum width from one very long line.
        _rl = self._chk_rootless.get_child()
        if isinstance(_rl, Gtk.Label):
            _rl.set_line_wrap(True)
            _rl.set_max_width_chars(58)
            _rl.set_xalign(0)
        rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rbox.pack_start(self._chk_rootless, False, False, 0)
        rsub = Gtk.Label(
            label=_t("Anyone who can reach this computer could then read, "
                     "change or erase everything on it without being asked for "
                     "anything. Leave it switched off unless this machine will "
                     "have a single user."),
            xalign=0)
        rsub.get_style_context().add_class("inst-sublabel")
        rsub.set_line_wrap(True)
        # Cap the measure so the consequence wraps into a readable paragraph
        # instead of one line across a 1920px page (and never widens the card).
        rsub.set_max_width_chars(64)
        # line the consequence up with the tick's LABEL, not with the tick box
        rsub.set_margin_start(22)
        rsub.set_halign(Gtk.Align.START)
        rbox.pack_start(rsub, False, False, 0)
        rrow = Gtk.Box()
        rrow.get_style_context().add_class("inst-item")
        rrow.get_style_context().add_class("bordered")
        rrow.pack_start(rbox, True, True, 0)
        card3.pack_start(rrow, False, False, 0)
        # The rows "Set this up for someone else" defers, greyed whole (see
        # _refresh_row_states): a question that is not being asked is its label
        # as much as its box.
        self._oem_rows = [name_row, host_row, kbd_row, loc_row, rrow]

        # -- swap --
        self._grp(col, "Spare memory space")
        card4 = self._card(col)
        self._chk_swap = Gtk.CheckButton(
            label=_t("Set aside part of the disk as spare memory"))
        self._chk_swap.get_style_context().add_class("inst-check")
        self._chk_swap.connect("toggled", self._on_swap_toggle)
        srow = Gtk.Box()
        srow.get_style_context().add_class("inst-item")
        srow.pack_start(self._chk_swap, True, True, 0)
        card4.pack_start(srow, False, False, 0)
        self._sp_swap = Gtk.SpinButton.new_with_range(256, 65536, 256)
        self._sp_swap.set_value(self.cfg["swap_mib"])
        self._sp_swap.set_sensitive(False)
        self._sp_swap.connect("value-changed", lambda *_: self._validate())
        ctl.add_widget(self._sp_swap)
        swap_size_row = self._field_row(card4, "How much to set aside",
                                        self._sp_swap,
                                        sub="In megabytes (known as swap).")

        # Every row on this page, for the update path. Not the same list as
        # _oem_rows: "set this up for someone else" defers four questions to
        # the new owner, while an update answers ALL of them from the disk --
        # including whether there is spare memory space, which is a partition
        # an update does not touch.
        self._update_rows = [oem_row, name_row, host_row, kbd_row, loc_row,
                             pwrow, pw2row, spwrow, rrow, srow, swap_size_row]

        # -- inline validation hint --
        self._opt_hint = Gtk.Label(xalign=0)
        self._opt_hint.get_style_context().add_class("inst-hint")
        self._opt_hint.set_line_wrap(True)
        self._opt_hint.set_margin_top(16)
        col.pack_start(self._opt_hint, False, False, 0)
        return outer

    def _grp_note(self, parent, text):
        """A sentence that belongs to a whole group of rows rather than to one
        of them. Under a single field it silently makes that field's row taller
        than its neighbours, which reads as the control being a different
        size."""
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("inst-note")
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(72)
        # Never flush against whatever is packed next to it.
        lbl.set_margin_bottom(2)
        parent.pack_start(lbl, False, False, 0)

    def _grp(self, parent, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("inst-group")
        parent.pack_start(lbl, False, False, 0)

    def _on_swap_toggle(self, btn):
        self._sp_swap.set_sensitive(btn.get_active())
        self._validate()

    def _on_showpw_toggle(self, btn):
        vis = btn.get_active()
        self._e_pw.set_visibility(vis)
        self._e_pw2.set_visibility(vis)

    def _on_kbd_changed(self, combo):
        """Put the chosen layout on the RUNNING keyboard, now.

        THE PASSWORD IS TYPED THREE ROWS BELOW THIS DROP-DOWN, AND IT IS
        CHECKED BY A MACHINE THAT DOES NOT EXIST YET. This layout used to be
        written into the installed tree and applied nowhere else, so somebody
        who set the keyboard to French here and then typed their password was
        still typing on the US layout the live medium boots with: the hash
        written to the target is of "qwerty" while the installed machine turns
        those same keys into "azerty". The sign-in screen then rejects the
        password its owner chose, on every boot, for good — no network, no
        getty on tty1, and de/login.py cannot tell a wrong password from a
        wrongly-typed one. Every non-US install was one drop-down away from it.

        Applying it here means the password is typed on the same layout that
        will be asked to check it. The two password fields are cleared with it:
        whatever was typed before the change was made of the OLD layout's
        characters and is no longer what the person meant. Best-effort — a
        machine with no setxkbmap simply keeps the layout it has, and the
        installed system is configured either way.
        """
        i = combo.get_active()
        if 0 <= i < len(KBD_LAYOUTS):
            # By INDEX, never get_active_text(): nbi18n translates what a
            # ComboBoxText shows, so the visible text is not the xkb code the
            # list was built from.
            code = KBD_LAYOUTS[i][1]
            setxkbmap = shutil.which("setxkbmap")
            if setxkbmap:
                try:
                    # nbi18n owns the argv: "ru,us" needs grp:alt_shift_toggle
                    # or its Latin half is unreachable, and a password with a
                    # digit or a Latin letter in it could not be typed at all.
                    run_cmd(nbi18n.xkb_args(code), timeout=10)
                except Exception:                              # noqa: BLE001
                    pass
            self._e_pw.set_text("")
            self._e_pw2.set_text("")
        self._validate()

    def _on_rootless_toggle(self, btn):
        # With no console password wanted, the password fields are dead: grey
        # them rather than keep demanding an answer that is then discarded.
        self._refresh_row_states()
        self._validate()

    def _refresh_row_states(self):
        """Grey the whole ROW of a question that is not being asked.

        Greying the control alone leaves its label and its hint in full ink
        beside a muted box, which reads as a row half broken rather than a row
        deferred. All THREE things that defer a question are applied here, from
        their current state, so none of them can undo another's greying —
        turning "Set this up for someone else" off must not hand back password
        rows the passwordless tick had put away, and neither of them may hand
        back anything at all while an update is answering the whole page from
        the disk.
        """
        oem = self._cb_oem.get_active()
        pw_off = self._chk_rootless.get_active()
        upd = self._is_update()
        for row in getattr(self, "_update_rows", []):
            row.set_sensitive(not upd)
        if not upd:
            for row in getattr(self, "_oem_rows", []):
                row.set_sensitive(not oem)
            for row in getattr(self, "_pw_rows", []):
                row.set_sensitive(not oem and not pw_off)
        note = getattr(self, "_update_note", None)
        if note is not None:
            # set_no_show_all as well as hide(): a later show_all() on the page
            # would otherwise put the note back on a fresh install.
            note.set_no_show_all(not upd)
            if upd:
                note.show()
            else:
                note.hide()

    def _activate_next(self, *_):
        # Enter in a field advances the wizard, but only when Next is actually
        # available (visible + enabled) — so it can never bypass validation or
        # fire on a step the button does not drive. Never on Summary: forward
        # there is the erase, and that must be a deliberate click.
        if (self.next_btn.get_visible() and self.next_btn.get_sensitive()
                and self._step < len(self.STEPS) - 1
                and self.STEPS[self._step][0] != "summary"):
            self._on_next()

    # --------------------------------------------------------------- 4 summary
    def _page_summary(self):
        outer, col = self._page_scaffold(
            "Summary",
            "Review the plan. Nothing is written until it is confirmed.")
        # Both sentences on this page are written in the future tense about a
        # disk that has not been touched. After a run that stopped part-way the
        # disk HAS been touched, and coming back here (the failure page's own
        # advice) met "Nothing is written until it is confirmed" over a disk
        # that had already been erased. _refresh_summary re-words both from
        # _disk_dirty.
        self._summary_sub = col.page_sub
        # The irreversible warning goes ABOVE the review card, not under it. The
        # card is seven rows tall, so on a 768px panel anything below it starts
        # past the fold — and the two things that must never be missed are the
        # "this erases the disk" line and, when the machine cannot be installed
        # to, the reason why.
        self._summary_danger = self._danger(col, "", top=14)
        self._install_block = Gtk.Label(xalign=0)
        self._install_block.get_style_context().add_class("inst-blocktxt")
        self._install_block.set_line_wrap(True)
        self._install_block.set_margin_top(8)
        self._install_block.set_no_show_all(True)   # only when there IS a block
        col.pack_start(self._install_block, False, False, 0)

        # A warning that does not stop the install but changes what the user
        # should do — today, only "another disk already has Notebook OS on it".
        # ABOVE the seven-row card for the same reason the danger banner is:
        # below it, on a 768px panel, it is past the fold and unread. Paper and
        # ink rather than a second red, so the erase warning keeps its weight.
        self._summary_warn = Gtk.Label(xalign=0)
        self._summary_warn.get_style_context().add_class("inst-callout")
        self._summary_warn.set_line_wrap(True)
        self._summary_warn.set_max_width_chars(72)
        self._summary_warn.set_margin_top(12)
        self._summary_warn.set_no_show_all(True)
        col.pack_start(self._summary_warn, False, False, 0)

        # No in-page Install button: on a 768px-tall panel it sat BELOW the fold
        # with the footer's Next hidden, so the one screen that has to offer a
        # way forward appeared to offer none. The action now lives in the footer
        # (see _set_step / _on_next), where every other step's forward control
        # is and where nothing can push it off-screen.
        self._summary_card = self._card(col, top=16)
        # No standing note under the card: it explained that the installer also
        # writes the start-up files, which the "How the disk is divided" row in
        # the card above already states as a fact.

        # The same plan in its exact technical form. Muted and last, but never
        # dropped: anyone recovering a machine that will not start needs these
        # names, and they exist nowhere else the user can reach.
        self._summary_tech = Gtk.Label(xalign=0)
        self._summary_tech.get_style_context().add_class("inst-note")
        self._summary_tech.set_line_wrap(True)
        self._summary_tech.set_margin_top(8)
        col.pack_start(self._summary_tech, False, False, 0)
        return outer

    def _refresh_summary(self):
        card = self._summary_card
        for ch in card.get_children():
            card.remove(ch)

        disk = self.cfg["disk"] or "—"
        dtxt = "%s  (%s)  %s" % (
            self.cfg["disk_model"] or "Disk",
            human_bytes(self.cfg["disk_size"]) if self.cfg["disk_size"] else "—",
            disk)
        # Say what each piece of the disk is FOR. The exact filesystem names are
        # kept on the second line for anyone who needs them, but they are not
        # what the sentence leads with.
        layout = "Start-up files, %d MB" % ESP_SIZE_MIB
        if self.cfg["swap"]:
            layout += ("  ·  Spare memory space, %s"
                       % self._mib_text(int(self.cfg["swap_mib"])))
        layout += "  ·  Notebook OS and files, all the remaining space"

        kbd = KBD_LAYOUTS[self.cfg["kbd"]][0]
        loc = LOCALES[self.cfg["locale"]][0]
        if self.cfg["root_passwordless"]:
            # Say the same thing the checkbox said, in the review that is the
            # last chance to catch it.
            acct = _t("None. The machine starts straight into the desktop.")
        else:
            # Capitalised like every other value in this card: it was the one
            # lowercase fragment in a column of sentences.
            acct = _t("Asked for every time this computer starts")

        if self._is_update():
            rows = self._summary_rows_update(dtxt)
        else:
            rows = self._summary_rows_install(dtxt, layout, kbd, loc, acct)
        for i, (k, v) in enumerate(rows):
            val = Gtk.Label(xalign=1)
            # The last screen before an irreversible erase. The computer name
            # and the person's own name are typed on the page before this one,
            # and "Home", "Work" and "Notes" are all ordinary computer names —
            # a review card that renames them is the one place a person cannot
            # afford to doubt what they are agreeing to.
            if k in ("Name", "Computer name", "Full name", "Device name"):
                nbi18n.set_verbatim(val, v)
            else:
                val.set_text(v)
            val.get_style_context().add_class("inst-value")
            val.set_line_wrap(True)
            val.set_max_width_chars(48)
            self._field_row(card, k, val, first=(i == 0))
        card.show_all()

        clash = self._clash_line()
        if clash:
            self._summary_warn.set_text(clash)
            self._summary_warn.set_no_show_all(False)
            self._summary_warn.show()
        else:
            self._summary_warn.set_text("")
            self._summary_warn.set_no_show_all(True)
            self._summary_warn.hide()
        if self._is_update():
            info = self.cfg.get("existing") or {}
            # The same names in their exact technical form, for anybody
            # recovering a machine that will not start — but in the words the
            # rest of this wizard uses for them ("start-up partition", the way
            # the disk layout row and the progress phases both name it), since
            # the paths and the identifier are the part that has to be exact.
            # TRANSLATE THE PATTERN, THEN SUBSTITUTE. Handing the finished
            # sentence to _t() cannot work here: nbi18n can only recognise an
            # already-substituted string by reverse-matching it against the
            # catalog, and it gives that up past 300 characters -- this one is
            # 342 once the disk names are in it. So the line stayed English in
            # all seventeen languages while the catalog entry for it sat there
            # unused. Every other string in this file already reads
            # _t("...") % (...); this one was the exception, and the exception
            # is the bug.
            tech = _t("Details: the contents of the root partition %s are "
                      "replaced, keeping /%s; the loader and the kernel on the "
                      "start-up partition %s are overwritten. The way the disk "
                      "is divided, the root partition's fixed identifier %s and "
                      "any spare memory space on it are left exactly as they "
                      "are.") % (
                info.get("root") or _t("the root partition"),
                ", /".join(PRESERVED_DIRS),
                info.get("esp") or _t("the start-up partition"),
                ROOT_PARTUUID)
        else:
            tech = ("Details: a %d MiB FAT32 EFI system partition, "
                    "%san ext4 root filesystem labelled \"%s\" with PARTUUID %s, "
                    "and the GRUB loader written to /EFI/BOOT/BOOTX64.EFI."
                    % (ESP_SIZE_MIB,
                       ("a %d MiB swap partition, " % int(self.cfg["swap_mib"]))
                       if self.cfg["swap"] else "",
                       ROOT_LABEL, ROOT_PARTUUID))
        self._summary_tech.set_text(tech)

        ready, reason = self._install_ready()
        self.next_btn.set_sensitive(ready)
        self._refresh_footer_tooltips(ready)
        # Keep the footer hint in step with the button whichever way we got
        # here (_validate also sets it, to the same text).
        if hasattr(self, "_foot_hint"):
            self._foot_hint.set_text(reason)
        if self._summary_sub is not None:
            # Keeps the subtitle's own job — what this page is for — and
            # corrects only the half that stopped being true. The banner below
            # names the disk and the consequence; saying both twice would be
            # two sentences where one is needed. Set on every pass rather than
            # only when it goes wrong: the update path can arrive here with the
            # machine in three different states, and a subtitle that is only
            # ever written once keeps describing the first of them.
            if self._is_update() and self._update_state in ("broken",
                                                            "finish"):
                self._summary_sub.set_text(
                    _t("Review the plan. The update that stopped left this "
                       "machine part-way through, and running it again "
                       "finishes the job."))
            elif self._disk_dirty:
                self._summary_sub.set_text(
                    _t("Review the plan. The disk has already been erased by "
                       "the run that stopped."))
            else:
                self._summary_sub.set_text(
                    _t("Review the plan. Nothing is written until it is "
                       "confirmed."))
        if ready:
            # The contents live in the "What is on it now" row directly below
            # this banner, where they are scannable beside everything else
            # being reviewed; saying them twice, two centimetres apart, only
            # makes the warning longer to read. The final confirmation repeats
            # them, because there the card is gone.
            if self._is_update():
                self._summary_danger.set_text(
                    _t("The system on %s is replaced and every file on it is "
                       "kept. The machine will not start up until the update "
                       "has finished, so leave it switched on.") % disk)
            elif self._disk_dirty:
                self._summary_danger.set_text(
                    _t("%s has already been erased. Installing again starts "
                       "over on it.") % disk)
            else:
                self._summary_danger.set_text(
                    "Everything on %s will be erased. This cannot be undone."
                    % disk)
            self._install_block.set_text("")
            self._install_block.set_no_show_all(True)
            self._install_block.hide()
        else:
            self._summary_danger.set_text(
                _t("This computer cannot be updated right now.")
                if self._is_update()
                else _t("This computer cannot be installed to right now."))
            self._install_block.set_text(reason)
            self._install_block.set_no_show_all(False)
            self._install_block.show()
        # Paper and ink for the update, signage red for the erase. Same rule as
        # the disk step's banner: red means "this destroys something", and a
        # run that keeps every file the user owns does not. A refusal is never
        # calm either way — it is the panel saying this cannot go ahead.
        self._set_calm(self._summary_danger, ready and self._is_update())

    def _summary_rows_install(self, dtxt, layout, kbd, loc, acct):
        """The review card for a fresh install: the disk, what is on it now,
        and every answer the Options step collected."""
        rows = [("Disk", dtxt)]
        # What is being destroyed belongs in the review, directly under the
        # disk it names — this is the row that catches a wrong choice while
        # backing out is still free.
        if self.cfg.get("disk_contents") == "EMPTY":
            rows.append(("What is on it now", _t("Nothing. The disk is empty.")))
        elif self.cfg.get("disk_contents"):
            rows.append(("What is on it now", self.cfg["disk_contents"]))
        rows += [
            ("How the disk is divided", layout),
            ("Name",
             _t("Chosen on first start-up") if self.cfg.get("oem")
             else (self.cfg.get("username") or _t("Not set"))),
            ("Computer name",
             _t("Chosen on first start-up") if self.cfg.get("oem")
             else self.cfg["hostname"]),
            ("Administrator password",
             _t("Chosen on first start-up") if self.cfg.get("oem") else acct),
            ("Keyboard",
             _t("Chosen on first start-up") if self.cfg.get("oem") else kbd),
            ("Text and characters",
             _t("Chosen on first start-up") if self.cfg.get("oem") else loc),
            ("Clock", _t("UTC. Set local time in Settings afterwards.")),
        ]
        return rows

    def _summary_rows_update(self, dtxt):
        """The review card for an update: what is on the disk, what is kept,
        and what is replaced.

        Kept before replaced, deliberately. "Will my files survive this" is the
        only question anybody has on this screen, and a card that answers it in
        its fourth row has made somebody read three rows to find out."""
        info = self.cfg.get("existing") or {}
        build = info.get("build") or ""
        rows = [("Disk", dtxt),
                ("System on this disk",
                 (_t("%s, build %s") % (OS_PRETTY, build)) if build
                 else OS_PRETTY)]
        if info.get("unfinished"):
            # An earlier update stopped part-way. Say so HERE rather than let
            # the state of the machine be a surprise: running this one finishes
            # the job, and that is the only reason the disk still offers it.
            rows.append(("Last update",
                         _t("An earlier update did not finish. This one "
                            "completes it.")))
        kept = _t("Kept from the system on this disk")
        rows += [
            ("Kept", _t("The home folder, the settings, the files and any "
                        "maps on this disk")),
            ("Replaced", _t("The system, the kernel and the start-up files")),
            ("Name", kept),
            ("Computer name", kept),
            ("Administrator password", kept),
            ("Keyboard", kept),
            ("Text and characters", kept),
            ("Clock", _t("UTC. Set local time in Settings afterwards.")),
        ]
        return rows

    def _install_ready(self):
        if not self.medium_ok:
            return False, ("The system to install is not here. Start the "
                           "computer from the %s medium and run the installer "
                           "from that desktop." % OS_NAME)
        if self.missing_tools:
            return False, ("This copy of %s is missing the tools that prepare "
                           "a disk, so it cannot install itself." % OS_NAME)
        # Checked HERE, not when the password is finally written: by then the
        # disk has been erased and the whole system extracted onto it. Not
        # asked of an update at all, which sets no password: the one already on
        # the disk is carried across verbatim (see _restore_root_shadow).
        if (not self._is_update() and not self.cfg.get("root_passwordless")
                and not self.can_hash):
            return False, (_t("This installer cannot store a password, so it "
                              "cannot finish an install that asks for one. On "
                              "the Options step, either switch on starting "
                              "without a password, or use a different %s "
                              "installer.") % OS_NAME)
        if not self.cfg.get("disk"):
            return False, "Go back to “Choose the disk” and pick one first."
        if self._is_update():
            # None of the gates below apply: an update never partitions, never
            # formats and never sets a password, so the figure that says how
            # big a disk must be to be prepared from nothing says nothing about
            # it. What it needs instead is room to unpack the new system BESIDE
            # the old one, which is the whole reason a failed update can leave
            # the machine startable -- so that is the one thing checked, here,
            # where backing out is still free.
            info = self.cfg.get("existing") or {}
            need = self._update_free_bytes()
            free = int(info.get("free") or 0)
            # Never on a machine whose last update stopped part-way: its
            # leftover working directories are counted as used space now and
            # are deleted before the unpack starts, so the figure read off the
            # disk would refuse the one run that repairs it.
            if need and free and not info.get("unfinished") and free < need:
                return False, (_t("There is not enough room on %s to unpack "
                                  "the new system beside the old one: %s is "
                                  "free and %s is needed. Delete some files "
                                  "and press Look again, or erase the disk "
                                  "and install fresh.")
                               % (self.cfg.get("disk") or "",
                                  human_bytes(free), human_bytes(need)))
            return True, ""
        # Last gate before the erase. The disk step already refuses a disk
        # that is too small, but the swap size is chosen AFTER it, so a disk
        # that fitted can stop fitting; this is the check that is true at the
        # moment the install actually starts.
        if self._disk_too_small(self.cfg.get("disk_size")):
            return False, (_t("%s is too small: it needs at least %s. Go back "
                              "to “Choose the disk” and pick a bigger one, "
                              "or set aside less spare memory.")
                           % (self.cfg["disk"],
                              human_bytes(self._min_disk_bytes())))
        return True, ""

    def _mib_text(self, mib):
        """2048 -> '2 GB'; 512 -> '512 MB'. Nobody outside a terminal thinks
        in mebibytes."""
        if mib >= 1024 and mib % 1024 == 0:
            return "%d GB" % (mib // 1024)
        if mib >= 1024:
            return "%.1f GB" % (mib / 1024.0)
        return "%d MB" % mib

    # -------------------------------------------------------------- 5 progress
    def _page_progress(self):
        outer, col = self._page_scaffold(
            "Installing",
            "Writing %s to the disk. Leave the installer in place and keep the "
            "computer switched on." % OS_PRETTY)
        # This page has two states and one set of words. Keep both labels (and
        # what they say while the run is alive) so _install_failed can stop the
        # screen telling somebody to keep the computer on for a run that has
        # already stopped, and _reset_progress can put them back for a retry.
        self._prog_title = col.page_title
        self._prog_sub = col.page_sub
        self._prog_title_run = col.page_title.get_text()
        self._prog_sub_run = col.page_sub.get_text() if col.page_sub else ""
        self._prog_status = Gtk.Label(xalign=0)
        self._prog_status.get_style_context().add_class("inst-progstatus")
        self._prog_status.set_margin_top(6)
        col.pack_start(self._prog_status, False, False, 0)

        self._prog_bar = Gtk.ProgressBar()
        self._prog_bar.set_show_text(True)
        self._prog_bar.set_margin_top(12)
        col.pack_start(self._prog_bar, False, False, 0)

        # The report is a verbatim transcript of every command — invaluable when
        # something fails, alarming when nothing has. Folded away by default so
        # the screen reads as a calm progress screen, and opened automatically
        # if the install stops (see _install_failed).
        self._log_toggle = Gtk.CheckButton(label=_t("Show the detailed report"))
        self._log_toggle.get_style_context().add_class("inst-check")
        self._log_toggle.set_margin_top(18)
        self._log_toggle.connect("toggled", self._on_log_toggle)
        col.pack_start(self._log_toggle, False, False, 0)

        self._log_scroll = Gtk.ScrolledWindow()
        self._log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                    Gtk.PolicyType.AUTOMATIC)
        # A modest min-height that still fits the shortest listed panel (768 px)
        # without forcing the outer page to scroll; it expands on taller screens
        # (packed True/True below). Framed as a warm-paper panel — never a black
        # terminal — so the software-rendered wizard reads as papertone end to end.
        self._log_scroll.set_size_request(-1, 300)
        self._log_scroll.get_style_context().add_class("inst-logframe")
        self._log_scroll.set_margin_top(10)
        self._log_scroll.set_no_show_all(True)   # opened by the toggle above
        self._log_view = Gtk.TextView()
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_monospace(True)
        self._log_view.get_style_context().add_class("inst-log")
        self._log_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self._log_buf = self._log_view.get_buffer()
        self._log_scroll.add(self._log_view)
        _ladj = self._log_scroll.get_vadjustment()
        if _ladj is not None:
            _ladj.connect("changed", self._on_log_extent)
        col.pack_start(self._log_scroll, True, True, 0)

        self._fail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._fail_box.set_no_show_all(True)
        self._fail_lbl = self._danger(self._fail_box, "", top=16)
        col.pack_start(self._fail_box, False, False, 0)
        return outer

    def _on_log_toggle(self, btn):
        # Fold the command transcript in or out. set_no_show_all keeps a later
        # show_all() from re-revealing it behind the user's back.
        if btn.get_active():
            self._log_scroll.set_no_show_all(False)
            self._log_scroll.show_all()
            # Opened by hand mid-run, it opens where the newest line is too.
            self._scroll_log_to_end()
        else:
            self._log_scroll.set_no_show_all(True)
            self._log_scroll.hide()

    def _reset_progress(self):
        self._pulse_on = False
        self._cancel_source("_pulse_source")
        # The two runs this page can show say different things about the disk,
        # and the difference is the whole point of the update path: one erases
        # the disk, the other keeps everything on it that is the user's. The
        # page is built with the install wording and captures it, so the update
        # wording is set here, on the way in, and put back on the way in to a
        # fresh install.
        if self._is_update():
            self._prog_title.set_text(_t("Updating"))
            if self._prog_sub is not None:
                self._prog_sub.set_text(
                    _t("Replacing the system on the disk. The files on it are "
                       "not touched. Leave the installer in place and keep "
                       "the computer switched on."))
        else:
            self._prog_title.set_text(self._prog_title_run)
            if self._prog_sub is not None:
                self._prog_sub.set_text(self._prog_sub_run)
        self._log_buf.set_text("")
        self._prog_bar.set_fraction(0.0)
        self._prog_bar.set_text("0%")
        self._prog_status.set_text(_t("Starting…"))
        self._fail_box.set_no_show_all(True)
        self._fail_box.hide()

    # ------------------------------------------------------------------ 6 done
    def _page_done(self):
        outer, col = self._page_scaffold("Installation complete")
        # Kept because this page has two endings: an install and an update are
        # not the same event, and a heading that says the wrong one is the
        # first thing somebody reads after handing this program their disk.
        self._done_title = col.page_title
        # The disk is named once the run finishes (see _install_done): the one
        # thing someone wants confirmed after erasing a disk is WHICH disk it
        # went to, and this page is built before there is an answer.
        self._done_para = self._para(
            col,
            "%s is installed. Remove the installer USB stick or disc, then "
            "restart. The machine will start up from the disk chosen here."
            % OS_PRETTY)
        # Filled in after a Secure Boot install (see _install_done); hidden
        # otherwise, so the ordinary case reads as a clean two-line finish.
        # It sits ABOVE the Restart button and in readable ink rather than the
        # faint note style: on such a machine this is the difference between
        # starting up and meeting an error screen, so it must be read BEFORE
        # the button is pressed, not skimmed past underneath it.
        self._done_sb = self._para(col, "", top=20, cls="inst-callout")
        self._done_sb.set_no_show_all(True)
        self._done_sb.hide()
        btnrow = Gtk.Box(spacing=12)
        btnrow.set_margin_top(26)
        # Shut Down first, and as the primary. "Restart, but pull the USB stick
        # out during the restart" is a race a novice loses: the firmware reads
        # the stick again before they can reach it and they land back in the
        # installer, convinced the install failed. Switching off, removing the
        # stick with all the time in the world, and pressing the power button
        # cannot go wrong — so that is the path this page recommends.
        shut = Gtk.Button(label=_t("Shut Down"))
        shut.get_style_context().add_class("inst-primary")
        shut.connect("clicked", lambda *_: self._confirm_shutdown())
        self._done_shut = shut
        btnrow.pack_start(shut, False, False, 0)
        restart = Gtk.Button(label=_t("Restart"))
        restart.get_style_context().add_class("inst-btn")
        restart.connect("clicked", lambda *_: self._confirm_restart())
        btnrow.pack_start(restart, False, False, 0)
        col.pack_start(btnrow, False, False, 0)
        return outer

    def _confirm_shutdown(self):
        # The button says Shut Down, so the dialog it opens says Shut Down too
        # — and so does the rest of the OS (the panel's power item and the
        # Settings power page both use exactly this word). Three words for one
        # action ("Shut Down" -> "Switch off now?" -> "Switch off") read as
        # three different things happening.
        self._open_confirm(
            _t("Shut down now?"),
            _t("The computer will shut down. Remove the installer USB stick "
               "or disc while it is off, then press the power button. It will "
               "start up into %s from the disk just installed to.") % OS_PRETTY,
            _t("Shut Down"),
            lambda: self._do_power("poweroff"))

    def _confirm_restart(self):
        # Restarting is not destructive, but a novice who reboots with the medium
        # still inserted lands back in the live installer — so confirm and remind.
        self._open_confirm(
            "Restart now?",
            "Remove the install medium first, so the machine starts from the "
            "disk just installed to. If the medium is still inserted the "
            "machine may start the live installer again.",
            "Restart",
            lambda: self._do_power("reboot"))

    def _do_power(self, action):
        try:
            subprocess.Popen([self.tools.get(action) or action])
        except OSError:
            pass

    def _on_delete(self, *_):
        # Block the window close while the destructive worker runs (returning
        # True cancels the default destroy). Every close path — the snail logo,
        # the app-name Close item, Esc, the window manager — funnels through
        # here, so the install can never be torn down mid-write.
        return bool(self._working)

    def _cancel_source(self, attr):
        source_id = getattr(self, attr, 0)
        setattr(self, attr, 0)
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass

    def _on_destroy(self, *_):
        """Invalidate every main-loop delivery owned by this window.

        The delete-event guard above still prevents ordinary destruction while
        disk writes are active. This handler only makes teardown deterministic
        once destruction is actually allowed (or externally forced).
        """
        if self._closed:
            return False
        self._closed = True
        self._scan_gen += 1
        self._clash_gen += 1
        self._pulse_on = False
        self._cancel_source("_paint_source")
        self._cancel_source("_pulse_source")
        return False

    def menu_items(self, name):
        # The window close is already blocked mid-install; grey the app-name
        # "Close" item to match, so the control never looks live yet do nothing.
        items = super().menu_items(name)
        if getattr(self, "_working", False) and name == self.app_name:
            items = [(lbl, None if isinstance(lbl, str) and lbl.startswith("Close")
                      else cb) for lbl, cb in items]
        return items

    def _on_key(self, w, ev):
        # A confirm overlay is dismissed by Esc first; otherwise defer to the
        # base (About / menu / close) behaviour. A close attempt while the
        # worker runs is a no-op — _on_delete blocks it.
        if ev.keyval == Gdk.KEY_Escape and self._confirm_layer is not None:
            self._close_confirm()
            return True
        return super()._on_key(w, ev)

    # -------------------------------------------------------------- navigation
    def _flush_paint(self):
        """Draw any just-mapped page subwindows NOW (see _set_step). GTK's
        frame clock does not reliably tick on the software EFI-framebuffer stack,
        so we invalidate the whole window and process its update tree — including
        child GdkWindows — synchronously, then flush the display. Best-effort and
        fully guarded; on an accelerated stack it is a cheap no-op."""
        self._paint_source = 0
        if self._closed:
            return False
        win = self.get_window()
        if win is not None:
            try:
                win.invalidate_rect(None, True)
                win.process_updates(True)      # paint children too, right now
            except Exception:
                pass
        dpy = self.get_display()
        if dpy is not None:
            try:
                dpy.flush()
            except Exception:
                pass
        return False   # one-shot idle

    def _set_step(self, i):
        if self._closed:
            return
        i = max(0, min(i, len(self.STEPS) - 1))
        self._step = i
        self._max_reached = max(self._max_reached, i)
        key = self.STEPS[i][0]

        if key == "target":
            self._refresh_disks()
        elif key == "summary":
            # One lsblk, on arrival, cached for the page (_refresh_summary is
            # re-run by every _validate and must not shell out each time) — and
            # on a worker thread, so a slow disk cannot freeze the wizard on the
            # step the user is trying to leave. The page draws now; the warning
            # is filled in by _apply_clash when the answer lands.
            self._start_clash_probe()
            self._refresh_summary()
        elif key == "progress":
            self._reset_progress()

        self.stack.set_visible_child_name(key)
        # Force the freshly-shown page to actually PAINT. On the no-vblank
        # software stack (real hardware boots on the EFI framebuffer, no GPU
        # vblank and no compositor) a Gtk.Stack page-switch maps the new page's
        # child GdkWindows — the form entries, combo boxes and the scroll
        # viewport — but nothing drives a draw of them, so they stay the X root's
        # black. This was the "black background on Options and beyond" report: the
        # Welcome/Disk pages paint during the initial window map, every later page
        # is reached by a Stack switch. Flushing the update tree here draws them
        # now. Scheduled at idle so it runs after GTK has allocated the new page.
        self._cancel_source("_paint_source")
        self._paint_source = GLib.idle_add(self._flush_paint)

        # Put the cursor where the user will type next, so the keyboard works
        # without a mouse click first. The password is the first empty field on
        # the Options step (hostname already carries a sensible default).
        if key == "options" and hasattr(self, "_e_pw"):
            try:
                self._e_pw.grab_focus_without_selecting()
            except Exception:
                self._e_pw.grab_focus()
        elif key == "progress" and hasattr(self, "_log_toggle"):
            self._log_toggle.grab_focus()
        elif key == "done" and hasattr(self, "_done_shut"):
            self._done_shut.grab_focus()

        # rail state
        # Restating the rail is DISPLAY, never navigation. Gtk.ToggleButton's
        # set_active emits "clicked", so lighting the step being entered also
        # fires the rail handler for the step being UNLIT — with the previous
        # index, which is <= _max_reached and so passes every guard in
        # _on_rail_click and navigates straight back. Next then bounced
        # welcome -> target -> welcome and recursed until the stack blew.
        # Block each row's own handler while the rail is restated.
        for entry in self._rail_rows:
            hid = getattr(entry[0], "_rail_hid", None)
            if hid is not None:
                entry[0].handler_block(hid)
        try:
            # A step is reached by finishing the one before it, and the rail
            # has to SAY that rather than going quiet: sensitivity is derived
            # from the reason below, so a row that declines the click cannot
            # exist without one. (j < i already implies j <= _max_reached,
            # since _max_reached is never less than the current step.)
            started = self._working or i >= self._steps_index("progress")
            for j, (step_btn, row, num, lbl) in enumerate(self._rail_rows):
                step_btn.set_active(j == i)
                if j == i:
                    reason = _t("This is the current step.")
                elif started:
                    reason = _t("The installation has started. Steps cannot "
                                "be reopened.")
                elif j > i:
                    reason = _t("This step opens when the step before it is "
                                "finished.")
                else:
                    reason = ""
                step_btn.set_sensitive(not reason)
                step_btn.set_tooltip_text(reason or _t("Back to this step"))
                ctx = row.get_style_context()
                for c in ("active", "done"):
                    ctx.remove_class(c)
                if j < i:
                    ctx.add_class("done")
                    # pin the tick to DejaVu Sans — the shipped Nimbus Sans has
                    # no U+2713 and would show a tofu box for a completed step
                    num.set_markup('<span face="DejaVu Sans">✓</span>')
                elif j == i:
                    ctx.add_class("active")
                    num.set_text(str(j + 1))
                else:
                    num.set_text(str(j + 1))
        finally:
            for entry in self._rail_rows:
                hid = getattr(entry[0], "_rail_hid", None)
                if hid is not None:
                    entry[0].handler_unblock(hid)

        # footer visibility per step. The forward button is always the footer's
        # right-hand button; on the Summary step it becomes the destructive
        # primary and says exactly what it will do.
        ctx = self.next_btn.get_style_context()
        if key == "summary" and self._is_update():
            # Ink, not red: .inst-primary is what turns this button red on the
            # Summary (see the CSS note on .inst-next.inst-primary), and red in
            # this wizard means "this destroys something" and only that. An
            # update destroys nothing of the user's, and a second red would
            # take the meaning off the first.
            self.next_btn.set_label(_t("Update the system"))
            ctx.remove_class("inst-primary")
        elif key == "summary":
            self.next_btn.set_label(_t("Erase disk and install"))
            ctx.add_class("inst-primary")
        else:
            self.next_btn.set_label(_t("Next"))
            ctx.remove_class("inst-primary")
        if key in ("progress", "done"):
            self.back_btn.hide()
            self.next_btn.hide()
        else:
            self.back_btn.show()
            self.next_btn.show()
            self.back_btn.set_sensitive(i > 0)
            self._refresh_footer_tooltips(self.next_btn.get_sensitive())
        self._validate()

    def _refresh_footer_tooltips(self, next_ready):
        """Keep wizard navigation explanations paired with sensitivity."""
        key = self.STEPS[self._step][0]
        self.back_btn.set_tooltip_text(
            _t("Back") if self._step > 0 else _t("This is the first step."))
        if next_ready and key == "summary":
            self.next_btn.set_tooltip_text(
                _t("Update the system") if self._is_update()
                else _t("Erase disk and install"))
        elif next_ready:
            self.next_btn.set_tooltip_text(_t("Next"))
        elif key == "welcome":
            self.next_btn.set_tooltip_text(_t("The system to install is not available."))
        elif key == "target":
            self.next_btn.set_tooltip_text(_t("Choose a disk to continue."))
        elif key == "options":
            self.next_btn.set_tooltip_text(_t("Some installation details need attention."))
        elif key == "summary":
            self.next_btn.set_tooltip_text(
                _t("The update requirements are not met.") if self._is_update()
                else _t("The installation requirements are not met."))
        else:
            self.next_btn.set_tooltip_text(_t("Installation is in progress."))

    def _on_next(self):
        # On the Summary step "forward" is the destructive action itself, and
        # it always goes through the confirmation first.
        if self.STEPS[self._step][0] == "summary":
            self._confirm_install()
            return
        if self._step < len(self.STEPS) - 1:
            # commit the current step's inputs into cfg before advancing
            self._commit_step()
            self._set_step(self._step + 1)

    def _on_back(self):
        if self._working:
            return
        if self._step > 0:
            self._set_step(self._step - 1)

    # Rows the new owner answers on their first start-up. Greyed rather than
    # hidden: the page must not jump about under the pointer, and seeing what
    # WILL be asked is the whole reassurance this option needs to give.
    _OEM_DEFERRED = ("_e_user", "_e_host", "_c_kbd", "_c_locale",
                     "_e_pw", "_e_pw2", "_chk_rootless")

    def _on_oem_toggled(self, _btn=None):
        oem = self._cb_oem.get_active()
        self.cfg["oem"] = oem
        for name in self._OEM_DEFERRED:
            w = getattr(self, name, None)
            if w is not None:
                w.set_sensitive(not oem)
        self._refresh_row_states()
        self._validate()

    def _commit_step(self):
        key = self.STEPS[self._step][0]
        # Nothing is committed from a page that is not being asked. During an
        # update the boxes are greyed but they still HOLD whatever was typed
        # into them before the disk was chosen, and committing that would put a
        # stale hostname and a stale spare-memory size into the config the
        # Summary then describes.
        if key == "options" and not self._is_update():
            self.cfg["oem"] = self._cb_oem.get_active()
            self.cfg["username"] = self._e_user.get_text().strip()
            self.cfg["hostname"] = self._e_host.get_text().strip()
            self.cfg["kbd"] = max(0, self._c_kbd.get_active())
            self.cfg["locale"] = max(0, self._c_locale.get_active())
            self.cfg["password"] = self._e_pw.get_text()
            self.cfg["root_passwordless"] = self._chk_rootless.get_active()
            self.cfg["swap"] = self._chk_swap.get_active()
            self.cfg["swap_mib"] = int(self._sp_swap.get_value())

    # --------------------------------------------------------------- validation
    def _validate(self):
        key = self.STEPS[self._step][0]
        ok = True
        hint = ""
        if key == "welcome":
            ok = self.medium_ok
        elif key == "target":
            ok = bool(self.cfg.get("disk"))
        elif key == "options":
            ok, hint = self._validate_options()
        elif key == "summary":
            # Say beside the greyed-out button why it is greyed out, exactly as
            # the Options step does — the in-page reason can be scrolled away.
            ok, hint = self._install_ready()
        if hasattr(self, "_opt_hint") and key == "options":
            self._opt_hint.set_text(hint)
        if key not in ("summary", "progress", "done"):
            self.next_btn.set_sensitive(ok)
            self._refresh_footer_tooltips(ok)
        # keep the summary install button honest if we are on it
        if key == "summary":
            self._refresh_summary()
        self._foot_status.set_text("Step %d of %d" % (self._step + 1,
                                                      len(self.STEPS)))
        if hasattr(self, "_foot_hint"):
            self._foot_hint.set_text(hint)

    def _validate_options(self):
        if self._is_update():
            # The disk already carries every answer this page collects, and the
            # update keeps all of them. There is nothing here to get wrong, and
            # in particular nothing to size a partition against: an update
            # never repartitions.
            return True, ""
        if self._cb_oem.get_active():
            # Every answer on this page now belongs to the new owner, so there
            # is nothing here left to get wrong.
            return True, ""
        host = self._e_host.get_text().strip()
        pw = self._e_pw.get_text()
        pw2 = self._e_pw2.get_text()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", host):
            # Name the field on the screen. "hostname" is a word that appears
            # nowhere in this wizard: the label above the box says Computer
            # name, so that is what the message refusing it has to say.
            return False, _t("Enter a valid computer name (letters, digits "
                             "and hyphens).")
        # Only when the password is going to be USED. With the passwordless tick
        # on, nothing reads it, so demanding one would be a made-up obstacle.
        if not self._chk_rootless.get_active():
            if len(pw) < 1:
                return False, "Enter an administrator password."
            if pw != pw2:
                return False, "The passwords do not match."
        swap_mib = 0
        if self._chk_swap.get_active():
            swap_mib = int(self._sp_swap.get_value())
            if swap_mib < 256:
                # The page calls this spare memory throughout (and prints
                # every other figure in MB), so the one message that refused a
                # value must not be the only place that says swap and MiB.
                return False, "Spare memory must be at least 256 MB."
        # Swap is chosen here, AFTER the disk. Turning it on (or raising it) can
        # push a disk that fitted a moment ago past what it can hold, and the
        # install would then erase the disk before finding out.
        if self._disk_too_small(self.cfg.get("disk_size"), swap_mib):
            return False, (_t("%s is too small for these choices: it needs at "
                              "least %s. Set aside less spare memory, or go "
                              "back and choose a bigger disk.")
                           % (self.cfg.get("disk") or _t("The chosen disk"),
                              human_bytes(self._min_disk_bytes(swap_mib))))
        return True, ""

    # ---------------------------------------------------------- confirm + start
    def _confirm_install(self):
        ready, reason = self._install_ready()
        if not ready:
            return
        disk = self.cfg["disk"]
        if self._is_update():
            # Kept FIRST. Somebody who has clicked through four screens is no
            # longer reading them; this is the one dialog they do read, so the
            # sentence it opens with is the answer to the only question they
            # have. The two halves are translated separately and then joined
            # for the reason the erase dialog below gives.
            body = (_t("The home folder, the settings and every file on %s "
                       "are kept. The system on it, its kernel and its "
                       "start-up files are replaced with this one.") % disk)
            body += "\n\n" + _t("The machine will not start up until the "
                                 "update has finished. Keep it switched on "
                                 "and leave the installer in place.")
            clash = self._clash_line()
            if clash:
                body += "\n\n" + clash
            self._open_confirm(
                _t("Update the system on %s?") % disk, body,
                _t("Update"), self._start_install)
            return
        # Translated explicitly, then joined: the automatic widget translation
        # keys on the WHOLE label, so appending the contents to an already
        # translated sentence would stop the pair matching any catalog entry
        # and drop this dialog back to English on a Spanish install.
        body = (_t("Everything on %s will be erased for good, and %s will be "
                   "installed in its place. This cannot be undone.")
                % (disk, OS_PRETTY))
        # Name the contents in the last dialog too. Someone who has clicked
        # through four screens is no longer reading them; this is the one they
        # do read, and "Windows" here has stopped more mistakes than any
        # amount of prose earlier on.
        line = self._contents_line(self.cfg.get("disk_contents"))
        if line:
            body += "\n\n" + line
        clash = self._clash_line()
        if clash:
            body += "\n\n" + clash
        self._open_confirm(
            "Erase %s and install?" % disk, body,
            "Erase and install",
            self._start_install)

    def _open_confirm(self, heading, body, ok_label, on_ok):
        self._close_confirm()
        # Size and centre against the real window, not a fixed 1920x1080, so the
        # scrim always covers the whole surface and the card stays centred at any
        # resolution (mirrors nbapp's About overlay).
        alloc = self.get_allocation()
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.get_style_context().add_class("inst-scrim")
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_confirm(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("inst-confirm")
        h = Gtk.Label(label=heading, xalign=0)
        h.get_style_context().add_class("inst-confirm-h")
        h.set_line_wrap(True)
        b = Gtk.Label(label=body, xalign=0)
        b.get_style_context().add_class("inst-confirm-b")
        b.set_line_wrap(True)
        # width_chars as well as max: without a minimum the card shrank to the
        # heading's width and broke the disk name ("/dev/sda") across two lines
        # — the one string in this dialog that must be read at a glance.
        b.set_width_chars(48)
        b.set_max_width_chars(52)
        card.pack_start(h, False, False, 0)
        card.pack_start(b, False, False, 0)
        row = Gtk.Box(spacing=10)
        row.set_halign(Gtk.Align.END)
        row.set_margin_top(8)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("inst-btn")
        cancel.connect("clicked", lambda *_: self._close_confirm())
        okb = Gtk.Button(label=ok_label)
        okb.get_style_context().add_class("inst-primary")
        okb.connect("clicked",
                    lambda *_: (self._close_confirm(), on_ok()))
        row.pack_start(cancel, False, False, 0)
        row.pack_start(okb, False, False, 0)
        card.pack_start(row, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits reliably
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # Centre on the card's measured natural size, then raise its window so it
        # paints above the content on the no-compositor stack.
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 560
        ch = nat.height if nat.height > 1 else 300
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            mw = card_win.get_window()
            if mw is not None:
                mw.raise_()
        except Exception:
            pass
        # Default focus to Cancel: a stray Enter/Space dismisses rather than
        # triggers the primary (safe for the destructive Erase confirmation).
        cancel.grab_focus()
        self._confirm_layer = layer

    def _close_confirm(self, *_):
        if self._confirm_layer is not None:
            try:
                self._overlay.remove(self._confirm_layer)
            except Exception:
                pass
            self._confirm_layer = None
        return True

    def _start_install(self):
        ready, _reason = self._install_ready()
        if not ready or self._working:
            return
        self._working = True
        # advance to the Progress step, then hand the destructive work to a
        # worker thread so the GTK main loop stays responsive.
        self._set_step(self._steps_index("progress"))
        t = threading.Thread(target=self._install_worker, daemon=True)
        t.start()

    def _steps_index(self, key):
        for i, (k, _t) in enumerate(self.STEPS):
            if k == key:
                return i
        return 0

    # ------------------------------------------------------------ install engine
    def _post_log(self, text):
        if not self._closed:
            GLib.idle_add(self._append_log, text)

    def _append_log(self, text):
        if self._closed:
            return False
        buf = self._log_buf
        buf.insert(buf.get_end_iter(),
                   text if text.endswith("\n") else text + "\n")
        self._scroll_log_to_end()
        return False

    def _scroll_log_to_end(self):
        """Put the report's LAST line in view — the one that says what failed.

        Every line is appended while the report is folded away, where the view
        has no allocation and its adjustment no extent, and the old code asked
        for `adj.set_value(adj.get_upper())` there: `upper` is a value set_value
        clamps away (the last page starts at upper - page_size), and against a
        box of size zero it means nothing at all. Opening the report at the
        moment of a failure therefore showed its FIRST line — the wipefs and
        sgdisk chatter — with the command that actually stopped the install off
        the bottom of a box the reader then had to scroll by hand.
        """
        if self._closed:
            return
        adj = self._log_scroll.get_vadjustment()
        if adj is not None:
            self._on_log_extent(adj)

    def _on_log_extent(self, adj):
        """Stay at the end as the report's extent moves.

        A TextView measures itself lazily, line by line, and the box only gets
        a page size when the toggle finally shows it — so "scroll to the end"
        cannot be a single call against an extent that is not final yet. This
        rides the adjustment's own "changed" (extent, never the user's
        scrolling, which is "value-changed"), so the newest line stays the one
        on screen exactly as appending each line already intended.
        """
        if self._closed:
            return
        adj.set_value(max(0.0, adj.get_upper() - adj.get_page_size()))

    def _post_progress(self, frac, status=None):
        if not self._closed:
            GLib.idle_add(self._apply_progress, frac, status)

    def _apply_progress(self, frac, status):
        if self._closed:
            return False
        # Any determinate update ends an indeterminate (pulsing) phase, so the
        # bar snaps back to a real percentage for the next step.
        self._pulse_on = False
        self._cancel_source("_pulse_source")
        frac = max(0.0, min(1.0, frac))
        self._prog_bar.set_fraction(frac)
        self._prog_bar.set_text("%d%%" % int(frac * 100))
        if status is not None:
            self._prog_status.set_text(status)
        return False

    def _phase(self, frac, status):
        self._post_progress(frac, status)
        self._post_log("")
        self._post_log("== %s ==" % status)

    def _phase_pulse(self, status):
        # An indeterminate phase for work with no measurable progress (the whole
        # rootfs extract): the bar pulses so it never looks frozen mid-install.
        if self._closed:
            return
        GLib.idle_add(self._begin_pulse, status)
        self._post_log("")
        self._post_log("== %s ==" % status)

    def _begin_pulse(self, status):
        if self._closed:
            return False
        self._prog_status.set_text(status)
        self._prog_bar.set_text(_t("Working…"))
        self._pulse_on = True
        self._prog_bar.pulse()
        self._cancel_source("_pulse_source")
        self._pulse_source = GLib.timeout_add(140, self._pulse_tick)
        return False

    def _pulse_tick(self):
        if self._closed or not self._pulse_on:
            self._pulse_source = 0
            return False
        self._prog_bar.pulse()
        return True

    def _install_worker(self):
        try:
            # One answer decides which engine runs, and it is the same one
            # every screen has been reading (see _is_update), so the words the
            # user agreed to on the Summary cannot describe a different run
            # from the one that starts here.
            if self._is_update():
                self._do_update()
            else:
                self._do_install()
        except InstallError as e:
            if not self._closed:
                GLib.idle_add(self._install_failed, str(e))
            return
        except Exception as e:   # never let the worker die silently
            if not self._closed:
                GLib.idle_add(self._install_failed,
                              "unexpected error: %s" % e)
            return
        if not self._closed:
            GLib.idle_add(self._install_done)

    def _install_failed(self, msg):
        if self._closed:
            return False
        self._working = False
        update = self._is_update()
        if not update:
            # What WAS on that disk is now gone — the engine wipes it before it
            # does anything else. Backing up to the Summary from here would
            # otherwise show a "What is on it now: Windows" row, and repeat it
            # in the confirmation, about a disk that no longer holds any of it.
            #
            # An update is the opposite case and must not do this: it erased
            # nothing, the contents line is still true, and marking the disk
            # dirty would have the Summary announce an erase that never
            # happened.
            self.cfg["disk_contents"] = ""
            self._disk_dirty = True
        # The page's own heading and subtitle are part of the state, not
        # decoration: left alone they read "Installing" and "Leave the
        # installer in place and keep the computer switched on" directly above
        # "Installation stopped" and a red failure panel.
        self._prog_title.set_text(_t("Update stopped") if update
                                  else _t("Installation stopped"))
        if self._prog_sub is not None:
            self._prog_sub.set_text(
                _t("Nothing more is being written to the disk. What went "
                   "wrong is below."))
        # The status line KEEPS the phase it stopped in ("Formatting
        # partitions"), which is the one thing on the page the heading above
        # does not already say. Re-post the fraction all the same: that is what
        # ends the pulsing phase, so the bar stops moving with the work.
        self._post_progress(self._prog_bar.get_fraction())
        self._fail_box.set_no_show_all(False)
        # Plain English first, the exact reason after it. Be straight about the
        # state of the disk: preparing it is the FIRST thing the install engine
        # does, so by the time anything can fail the old contents are already
        # gone — telling the user "nothing was written" would be a comforting
        # lie. The update engine can honestly say better than that, and how
        # much better depends on how far it got (see _update_failure_state).
        if update:
            self._fail_lbl.set_text("%s\n\n%s"
                                    % (self._update_failure_state(),
                                       _t("What went wrong: %s") % msg))
        else:
            self._fail_lbl.set_text(
                "The installation stopped part-way through. The disk was "
                "already being erased, so it will not start up as it is. Go "
                "back and try again.\n\n"
                "What went wrong: %s" % msg)
        self._fail_box.show_all()
        # Open the detailed report: this is the moment it earns its place, and
        # at its END, where the command that failed is (see _scroll_log_to_end).
        if not self._log_toggle.get_active():
            self._log_toggle.set_active(True)
        self._scroll_log_to_end()
        self.back_btn.show()
        self.back_btn.set_sensitive(True)
        self._refresh_footer_tooltips(self.next_btn.get_sensitive())
        return False

    def _install_done(self):
        if self._closed:
            return False
        self._working = False
        self._post_progress(1.0, "Complete")
        # Say which disk it went onto. After erasing one, "it worked" is not
        # the reassurance people are looking for — "it is on THAT disk" is.
        # After an update the reassurance wanted is a different one again, and
        # it is the promise the whole path was built to keep.
        disk = self.cfg.get("disk")
        if self._is_update():
            self._done_title.set_text(_t("Update complete"))
            self._done_para.set_text(
                _t("%s on %s has been replaced with this version. The files "
                   "and settings on it are as they were. Remove the installer "
                   "USB stick or disc, then start the machine again.")
                % (OS_PRETTY, disk or _t("the disk")))
        elif disk:
            self._done_para.set_text(
                "%s is now installed on %s (%s), at %s. Remove the installer "
                "USB stick or disc, then start the machine again. It will "
                "start up from that disk."
                % (OS_PRETTY, self.cfg.get("disk_model") or "the disk",
                   human_bytes(self.cfg.get("disk_size") or 0), disk))
        if getattr(self, "_secureboot_used", False):
            # A Secure Boot machine needs a one-off key approval on the very
            # first start, and if the user does not know that they meet an
            # error screen with no idea why. Say what they will see and what to
            # press, in the order they will meet it; the exact file names stay
            # because they have to type/choose them.
            self._done_sb.set_text(
                "Secure Boot: the Notebook OS key must be approved once, on "
                "the first start-up. A blue screen appears: choose \"Enroll "
                "key from disk\", pick EFI/BOOT/MOK.cer, confirm, and restart. "
                "If a start-up error appears instead, open the firmware boot "
                "menu and run EFI/BOOT/mmx64.efi to enrol the key there.")
            self._done_sb.set_no_show_all(False)
            self._done_sb.show()
        self._set_step(self._steps_index("done"))
        return False

    # -- command streaming (worker thread only) --
    def _sh(self, argv, allow_fail=False):
        """Run argv, streaming combined output to the log. Raise InstallError on
        a non-zero exit unless allow_fail (used for best-effort unmounts)."""
        self._post_log("$ " + " ".join(argv))
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    bufsize=1)
        except OSError as e:
            if allow_fail:
                self._post_log("  (skipped: %s)" % e)
                return 1
            raise InstallError("cannot run %s: %s" % (argv[0], e))
        try:
            for line in proc.stdout:
                self._post_log(line.rstrip("\n"))
        finally:
            proc.wait()
        if proc.returncode != 0 and not allow_fail:
            raise InstallError("%s exited with status %d"
                               % (os.path.basename(argv[0]), proc.returncode))
        return proc.returncode

    def _tool(self, name):
        path = self.tools.get(name)
        if not path:
            raise InstallError("required tool not found: %s" % name)
        return path

    def _partdev(self, disk, n):
        # /dev/sda -> /dev/sda1 ; /dev/nvme0n1 -> /dev/nvme0n1p1
        return disk + ("p" if disk[-1:].isdigit() else "") + str(n)

    def _wait_for(self, path, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(path):
                return True
            time.sleep(0.3)
        return os.path.exists(path)

    def _do_install(self):
        disk = self.cfg["disk"]
        swap = self.cfg["swap"]
        swap_mib = int(self.cfg["swap_mib"])
        if not disk:
            raise InstallError("no target disk")
        if not os.path.exists(ROOTFS_TAR):
            raise InstallError("install payload missing: %s" % ROOTFS_TAR)

        esp_n = 1
        swap_n = 2 if swap else None
        root_n = 3 if swap else 2

        sgdisk = self._tool("sgdisk")
        wipefs = self._tool("wipefs")
        mkvfat = self._tool("mkfs.vfat")
        mkext4 = self._tool("mkfs.ext4")
        mount = self._tool("mount")
        umount = self._tool("umount")
        tar = self._tool("tar")

        # a. Safety: unmount anything on the target, then clear it.
        self._phase(0.04, "Preparing target disk")
        self._unmount_target(disk, umount)
        self._sh([wipefs, "-a", disk])
        self._sh([sgdisk, "-Z", disk])

        # b. Partition.
        self._phase(0.16, "Creating partitions")
        self._sh([sgdisk, "-n", "1:2048:+%dM" % ESP_SIZE_MIB,
                  "-t", "1:EF00", "-c", "1:EFI System", disk])
        if swap:
            self._sh([sgdisk, "-n", "%d:0:+%dM" % (swap_n, swap_mib),
                      "-t", "%d:8200" % swap_n,
                      "-c", "%d:notebookos-swap" % swap_n, disk])
        self._sh([sgdisk, "-n", "%d:0:0" % root_n,
                  "-t", "%d:8300" % root_n,
                  "-c", "%d:notebookos-root" % root_n,
                  "-u", "%d:%s" % (root_n, ROOT_PARTUUID), disk])
        # reload the kernel partition table
        if self.tools.get("partx"):
            self._sh([self.tools["partx"], "-u", disk], allow_fail=True)
        elif self.tools.get("partprobe"):
            self._sh([self.tools["partprobe"], disk], allow_fail=True)
        if self.tools.get("udevadm"):
            self._sh([self.tools["udevadm"], "settle"], allow_fail=True)
        else:
            time.sleep(1.5)

        esp = self._partdev(disk, esp_n)
        rootpart = self._partdev(disk, root_n)
        swappart = self._partdev(disk, swap_n) if swap else None
        for p in ([esp, rootpart] + ([swappart] if swap else [])):
            if not self._wait_for(p):
                raise InstallError("partition node did not appear: %s" % p)

        # c. Format.
        self._phase(0.30, "Formatting partitions")
        self._sh([mkvfat, "-F32", "-n", ESP_LABEL, esp])
        if swap and self.tools.get("mkswap"):
            # Labelled, so _configure_fstab can name it without hard-coding a
            # device path. Without the fstab line the partition was created,
            # formatted and then never switched on by anything.
            self._sh([self.tools["mkswap"], "-L", SWAP_LABEL, swappart])
        self._sh([mkext4, "-F", "-L", ROOT_LABEL, rootpart])

        # d. Mount root and extract the system. tar reports no progress, so the
        # bar pulses (indeterminate) for the duration rather than sitting still.
        self._phase_pulse("Extracting the system (this can take a few minutes)")
        try:
            os.makedirs(TARGET_MNT, exist_ok=True)
        except OSError as e:
            raise InstallError("cannot create %s: %s" % (TARGET_MNT, e))
        self._sh([mount, rootpart, TARGET_MNT])
        # -p preserves permissions/owners; no -v (a verbose extract of a whole
        # rootfs would post tens of thousands of lines onto the GTK idle queue).
        self._sh([tar, "xpf", ROOTFS_TAR, "-C", TARGET_MNT])

        # e. ESP: mount, then copy the prebuilt bootloader + kernel.
        self._phase(0.80, "Setting up start-up")
        espmnt = os.path.join(TARGET_MNT, "boot", "efi")
        try:
            os.makedirs(espmnt, exist_ok=True)
        except OSError as e:
            raise InstallError("cannot create %s: %s" % (espmnt, e))
        self._sh([mount, esp, espmnt])
        self._write_boot(espmnt)

        # f. Configure the installed tree.
        self._phase(0.90, "Configuring the system")
        self._configure_target(TARGET_MNT)

        # g. Map packs that rode in on the medium, onto the machine.
        #
        # BEST EFFORT, DELIBERATELY. This runs after the system is installed
        # and configured, so nothing in it can cost anybody their install: no
        # room, an unreadable medium or a short read is logged plainly and the
        # installation still completes. That is also why the packs are NOT
        # counted by _min_disk_bytes — an OPTIONAL 2.7 GB map must not raise
        # the smallest disk Notebook OS can be installed onto.
        self._copy_map_packs(TARGET_MNT)

        # h. Finish: flush and unmount.
        self._phase(0.97, "Finishing up")
        if self.tools.get("sync"):
            # A failed final writeback means the installed bytes are not known
            # durable.  Never offer restart/power-off after swallowing it.
            self._sh([self.tools["sync"]])
        # These are the target filesystems just written, not stale preflight
        # mounts.  Failure is part of installation completion and must remain
        # on the error/retry path rather than being labelled Complete.
        self._sh([umount, espmnt])
        self._sh([umount, TARGET_MNT])
        self._post_progress(1.0, "Complete")
        self._post_log("")
        self._post_log("Installation complete.")
        if getattr(self, "_secureboot_used", False):
            # The user-facing version of this lives on the Done step; the log
            # keeps the precise form for anyone reading the transcript.
            self._post_log("")
            self._post_log("Secure Boot: this install is signed. On the FIRST boot with")
            self._post_log("Secure Boot enabled, enroll the Notebook OS key once:")
            self._post_log("  - when MokManager (blue screen) appears, choose")
            self._post_log("    'Enroll key from disk' -> EFI/BOOT/MOK.cer -> Continue,")
            self._post_log("    or 'Enroll hash' and confirm, then reboot.")
            self._post_log("  If it boots straight to a GRUB error instead, launch")
            self._post_log("  EFI/BOOT/mmx64.efi from your firmware's boot menu to enroll.")

    def _write_boot(self, espmnt):
        """Write the bootloader and the kernel onto a MOUNTED EFI system
        partition.

        Shared by both engines on purpose. An install and an update have to put
        exactly the same files in exactly the same places — the firmware looks
        in one place and the prebuilt GRUB boots one PARTUUID — and a second
        copy of this would be a second place for the Secure Boot chain to
        drift, discovered on a machine that will not start."""
        bootdir = os.path.join(espmnt, "EFI", "BOOT")
        try:
            os.makedirs(bootdir, exist_ok=True)
        except OSError as e:
            raise InstallError("cannot create %s: %s" % (bootdir, e))
        secureboot = os.path.exists(SB_SHIM_SRC) and os.path.exists(SB_GRUB_SRC)
        if secureboot:
            # Secure Boot: shim (MS-signed) is the entry point; it loads the
            # Debian-signed grub, which verifies the MOK-signed kernel. The
            # signed grub reads its menu from prefix=/EFI/debian/grub.cfg.
            self._copy(SB_SHIM_SRC, os.path.join(bootdir, "BOOTX64.EFI"))
            self._copy(SB_GRUB_SRC, os.path.join(bootdir, "grubx64.efi"))
            if os.path.exists(SB_MM_SRC):
                self._copy(SB_MM_SRC, os.path.join(bootdir, "mmx64.efi"))
            if os.path.exists(SB_MOK_SRC):
                self._copy(SB_MOK_SRC, os.path.join(bootdir, "MOK.cer"))
            debiandir = os.path.join(espmnt, "EFI", "debian")
            try:
                os.makedirs(debiandir, exist_ok=True)
            except OSError as e:
                raise InstallError("cannot create %s: %s" % (debiandir, e))
            self._copy(SB_GRUBCFG_SRC, os.path.join(debiandir, "grub.cfg"))
        else:
            self._copy(BOOT_EFI_SRC, os.path.join(bootdir, "BOOTX64.EFI"))
        self._copy(KERNEL_SRC, os.path.join(espmnt, "bzImage"))
        self._secureboot_used = secureboot

    # ------------------------------------------------------- update engine
    def _do_update(self):
        """Replace the system on a disk that already carries one, in place.

        THE ORDER IS THE SAFETY. Nothing here partitions, formats or wipes
        anything — no call in this method reaches for any of the tools that
        do, which is the difference the Summary is promising and which
        installer_update_selftest reads off the compiled body rather than off
        this paragraph. Everything that can refuse refuses first, with the
        installed system untouched. The new system is then unpacked into UPDATE_STAGE_DIR
        BESIDE the old one, so a failure anywhere in the long part of the run
        (no room, an unreadable medium, a truncated tar) leaves a machine that
        still starts up exactly as it did. Only once the whole new tree is on
        the disk are the old directories moved aside and the new ones moved in
        — renames inside one filesystem, seconds rather than minutes — and every
        one of those moves is recorded so it can be undone.

        What is never touched at all: the partition table, the root PARTUUID,
        any swap partition, and everything in PRESERVED_DIRS. /root is this
        appliance's home directory (session.sh pins NB_HOME=/root), so keeping
        it is what keeps every document, every app's store under
        .config/notebook, and the Desktop/Documents/Pictures tree. locale.json
        lives there too, which is why the keyboard and the interface language
        survive an update without being copied anywhere at all.

        The three files that do NOT live there — the hostname, the X keyboard
        layout and above all the stored password — are read out of the old
        /etc before it is replaced and written back on top of the new one (see
        _read_carried), because /etc is part of the system this run replaces.
        """
        # FIRST, before anything at all can raise. Every refusal below this
        # line is one the failure page has to be able to describe, and a state
        # left unset would send it to the last and most alarming of the four
        # sentences about a machine nothing had touched.
        self._update_state = "safe"
        self._swapped = []

        info = self.cfg.get("existing") or {}
        rootpart = info.get("root") or ""
        esp = info.get("esp") or ""
        if not rootpart or not esp:
            raise InstallError("no installed system to update")
        if not os.path.exists(ROOTFS_TAR):
            raise InstallError("install payload missing: %s" % ROOTFS_TAR)

        mount = self._tool("mount")
        umount = self._tool("umount")
        tar = self._tool("tar")
        espmnt = os.path.join(TARGET_MNT, "boot", "efi")

        # a. Mount the installed root. Read-write this time, and at the
        #    engine's own mount point rather than the probe's.
        self._phase(0.04, "Checking the system on the disk")
        try:
            os.makedirs(TARGET_MNT, exist_ok=True)
        except OSError as e:
            raise InstallError("cannot create %s: %s" % (TARGET_MNT, e))
        self._unmount_target(self.cfg.get("disk") or rootpart, umount)
        self._sh([mount, rootpart, TARGET_MNT])
        try:
            # b. Read the marker AGAIN. The disk list read it minutes ago, and
            #    this is what stops an update running against a disk that was
            #    unplugged, swapped or reformatted in between — on the one
            #    path whose whole promise is about the files already there.
            found = self._probe_install(TARGET_MNT)
            if not found:
                raise InstallError("%s no longer holds a %s system"
                                   % (rootpart, OS_NAME))
            cfg_root = (os.path.join(TARGET_MNT, found["config_sub"])
                        if found["config_sub"] else TARGET_MNT)
            if found.get("halfway"):
                # An earlier update stopped after its swap had begun, so this
                # machine does NOT start up as it stands and did not before
                # this run began. Say so from the outset: clearing the
                # leftovers below cannot make that worse, but a failure page
                # telling its owner "nothing was changed and it still starts
                # up" would be false about a machine that was already broken.
                self._update_state = "broken"

            # c. Lift out everything a previous install or update configured,
            #    BEFORE anything is removed: those files are inside the tree
            #    about to be replaced.
            self._phase(0.10, "Reading the settings to keep")
            carried = self._read_carried(cfg_root)

            # d. Clear the working directories of a run that stopped. Safe by
            #    construction: PRESERVED_DIRS are never moved into them, so
            #    they can only ever hold system directories this run replaces
            #    anyway — never a file of the user's.
            self._clear_update_dirs(TARGET_MNT)

            # e. Room for the new system beside the old one. Measured, and
            #    refused here where the machine is still whole.
            need = self._update_free_bytes()
            try:
                free = shutil.disk_usage(TARGET_MNT).free
            except OSError as e:
                raise InstallError("cannot measure the free space on %s: %s"
                                   % (rootpart, e))
            self._post_log("free on %s: %s; needed: %s"
                           % (rootpart, human_bytes(free), human_bytes(need)))
            if need and free < need:
                raise InstallError(
                    "not enough room on %s to unpack the new system beside "
                    "the old one (%s free, %s needed)"
                    % (rootpart, human_bytes(free), human_bytes(need)))

            # f. Unpack. The long phase — and the one nothing depends on yet:
            #    the machine on this disk starts up throughout it.
            stage = os.path.join(TARGET_MNT, UPDATE_STAGE_DIR)
            try:
                os.makedirs(stage)
            except OSError as e:
                raise InstallError("cannot create %s: %s" % (stage, e))
            self._phase_pulse("Unpacking the new system (this can take a few "
                              "minutes)")
            # -p preserves permissions/owners; no -v, for the reason the
            # install engine gives (tens of thousands of lines onto the GTK
            # idle queue).
            self._sh([tar, "xpf", ROOTFS_TAR, "-C", stage])
            if self.tools.get("sync"):
                # Durable BEFORE the swap. The rename that follows is the one
                # moment this machine cannot start, and it must not be entered
                # with the new system still sitting in page cache.
                self._sh([self.tools["sync"]])

            # g. The swap.
            self._phase(0.72, "Replacing the system")
            self._swap_trees(TARGET_MNT, stage)
            self._update_state = "finish"

            # h. The machine's own identity and credentials, back on top of the
            #    new /etc.
            self._phase(0.82, "Putting the settings back")
            self._apply_carried(TARGET_MNT, carried)
            self._write_os_release(TARGET_MNT)

            # i. The kernel and the bootloader, onto the ESP that is already
            #    there. Reused, never reformatted: it is the place this
            #    machine's firmware already looks.
            self._phase(0.90, "Setting up start-up")
            try:
                os.makedirs(espmnt, exist_ok=True)
            except OSError as e:
                raise InstallError("cannot create %s: %s" % (espmnt, e))
            self._sh([mount, esp, espmnt])
            self._write_boot(espmnt)

            # No map packs. /data is preserved, so the packs on this machine
            # are still on it — and re-copying gigabytes it already has would
            # add minutes to every update for nothing.

            # j. The old system is deleted only once the new one is complete
            #    and configured.
            self._phase(0.96, "Finishing up")
            self._clear_update_dirs(TARGET_MNT)
            if self.tools.get("sync"):
                # A failed final writeback means the replaced bytes are not
                # known durable. Never report Complete after swallowing it.
                self._sh([self.tools["sync"]])
            self._sh([umount, espmnt])
            self._sh([umount, TARGET_MNT])
        except Exception:
            # Put the old system back if the swap had started, then let go of
            # the disk so a second attempt can mount it. Both best-effort — the
            # failure has already happened — but the ANSWER is not: it is what
            # decides which of the four sentences in _update_failure_state the
            # user reads about their own machine.
            if self._update_state == "broken" and self._restore_trees(
                    TARGET_MNT):
                self._update_state = "restored"
            self._sh([umount, espmnt], allow_fail=True)
            self._sh([umount, TARGET_MNT], allow_fail=True)
            raise
        self._post_progress(1.0, "Complete")
        self._post_log("")
        self._post_log("Update complete. The files and settings on the disk "
                       "were kept.")

    def _swap_trees(self, target, stage):
        """Move the old system aside and the new one into place, one top-level
        name at a time.

        Renames within a single filesystem: no copying and no I/O of
        consequence, so the window in which this machine cannot start is
        seconds rather than the minutes the unpack took. Nothing in
        PRESERVED_DIRS is ever moved, which is both how the user's files
        survive and why UPDATE_OLD_DIR is safe to delete afterwards.

        A name is recorded BEFORE its new tree is moved in, never after, so
        _restore_trees can undo the one state that looks contradictory: the old
        tree moved aside and the new one not yet arrived. And _update_state
        goes to "broken" at the first move rather than before the loop, so a
        run that fails while merely preparing this can still say, truthfully,
        that the machine was not touched.
        """
        old = os.path.join(target, UPDATE_OLD_DIR)
        try:
            os.makedirs(old, exist_ok=True)
        except OSError as e:
            raise InstallError("cannot create %s: %s" % (old, e))
        names = [n for n in sorted(os.listdir(stage))
                 if n not in PRESERVED_DIRS]
        if not names:
            # An empty staging tree means the tar produced nothing usable. It
            # has to stop here: the loop below would move nothing, report
            # success, and hand back the OLD system dressed as a new one.
            raise InstallError("the new system unpacked to nothing")
        for name in names:
            cur = os.path.join(target, name)
            had_old = os.path.lexists(cur)
            try:
                if had_old:
                    os.rename(cur, os.path.join(old, name))
                self._swapped.append((name, had_old))
                self._update_state = "broken"
                os.rename(os.path.join(stage, name), cur)
            except OSError as e:
                raise InstallError("cannot replace /%s: %s" % (name, e))
        self._post_log("replaced: %s" % ", ".join("/" + n for n in names))

    def _restore_trees(self, target):
        """Undo a swap that stopped part-way. True when the machine on this
        disk starts up again.

        Best-effort by nature — it runs on the failure path, where something
        has already gone wrong with this filesystem — but its ANSWER is not
        best-effort at all: it decides whether the failure page tells somebody
        their machine still starts or tells them it does not. Being wrong in
        either direction is the defect this returns a value for, so every move
        that fails is both logged and counted.
        """
        if not self._swapped:
            # This run moved nothing, so it has put nothing back — and the
            # caller only asks at all when the machine is known not to start,
            # which here means it arrived that way (an earlier update stopped
            # after its swap; see the `halfway` branch in _do_update). An
            # empty list is "there was nothing I could undo", and answering
            # True would have the failure page tell somebody their machine
            # starts up when nothing has made it start up.
            return False
        stage = os.path.join(target, UPDATE_STAGE_DIR)
        old = os.path.join(target, UPDATE_OLD_DIR)
        try:
            os.makedirs(stage, exist_ok=True)
        except OSError as e:
            self._post_log("cannot undo the replacement: %s" % e)
            return False
        ok = True
        for name, had_old in reversed(self._swapped):
            cur = os.path.join(target, name)
            try:
                if os.path.lexists(cur):
                    os.rename(cur, os.path.join(stage, name))
                if had_old:
                    os.rename(os.path.join(old, name), cur)
            except OSError as e:
                ok = False
                self._post_log("could not put /%s back: %s" % (name, e))
        self._swapped = []
        if ok:
            self._post_log("the system that was on this disk has been put "
                           "back; it starts up as it did before")
        return ok

    def _clear_update_dirs(self, target):
        """Remove both working directories.

        Never raises. They are scratch, and an update must not stop because
        scratch could not be tidied — if the space they hold is genuinely
        needed, the measured check in _do_update refuses with the figures a few
        lines later, which is a message somebody can act on."""
        for name in (UPDATE_STAGE_DIR, UPDATE_OLD_DIR):
            path = os.path.join(target, name)
            if not os.path.isdir(path):
                continue
            self._post_log("removing %s" % path)
            shutil.rmtree(path, ignore_errors=True)

    def _update_failure_state(self):
        """What state the machine is ACTUALLY in after an update stopped.

        This sentence is what the whole staged design exists to be able to
        write. An update that fails while unpacking has changed nothing, and
        saying "it will not start up" there would frighten somebody into
        reinstalling — erasing the very files this path exists to keep. An
        update that fails after the swap HAS left a half-replaced system, and
        saying anything softer than that would be exactly the comforting lie
        the install engine's own failure text refuses to tell.
        """
        if self._update_state == "safe":
            return _t("Nothing on the disk was replaced. The system already "
                      "on it is untouched and still starts up, and no files "
                      "were moved. Go back and try again.")
        if self._update_state == "restored":
            return _t("The system that was on the disk has been put back, so "
                      "the machine still starts up. No files were moved. Go "
                      "back and try again.")
        if self._update_state == "broken":
            return _t("The system on the disk is half replaced, so the "
                      "machine will not start up until the update is "
                      "finished. The files on it are still there and were "
                      "never moved. Keep this installer, go back and run the "
                      "update again.")
        # "finish": the new system is in place, but the run stopped before the
        # settings were put back or the start-up files were written, so the
        # kernel on the disk may not be the one this system expects.
        return _t("The new system is on the disk but the update did not "
                  "finish, so the machine may not start up. The files on it "
                  "are still there and were never moved. Keep this installer, "
                  "go back and run the update again.")

    def _copy_map_packs(self, target):
        """Copy any .nbm2 packs from the live medium into the installed
        /data/maps, which is one of the directories de/maps.py scans.

        Never raises — see the caller. It does report what happened, including
        when it deliberately did nothing: "this machine has no maps" and "the
        copy failed and said nothing" look identical afterwards, and only one
        of them is something anybody can act on.
        """
        try:
            names = sorted(n for n in os.listdir(MAPS_SRC)
                           if n.endswith(".nbm2"))
        except OSError:
            return          # no packs on this medium — nothing happened, and
        if not names:       # nothing was meant to happen, so say nothing
            return

        total = 0
        for n in names:
            try:
                total += os.path.getsize(os.path.join(MAPS_SRC, n))
            except OSError:
                pass
        dest = os.path.join(target, MAPS_DEST)
        self._phase(0.92, "Copying maps")
        try:
            os.makedirs(dest, exist_ok=True)
            free = shutil.disk_usage(dest).free
        except OSError as e:
            self._post_log("maps: skipped (%s)" % e)
            return
        # Leave the machine room to work in afterwards. A disk filled to its
        # last byte by an optional map is a worse outcome than no map, and
        # MIN_FREE_MIB is the same margin the disk step already gates on.
        if free < total + MIN_FREE_MIB * 1024 * 1024:
            self._post_log(
                "maps: %d MB of maps need more room than this disk has free "
                "(%d MB) — skipped. The system is installed and complete; copy "
                "a pack into /data/maps later to add one."
                % (total // (1 << 20), free // (1 << 20)))
            return

        copied = 0
        for n in names:
            src = os.path.join(MAPS_SRC, n)
            dst = os.path.join(dest, n)
            self._post_log("copy %s -> /%s/%s" % (src, MAPS_DEST, n))
            try:
                copied = self._copy_chunked(src, dst, copied, total)
            except OSError as e:
                # _copy_chunked publishes atomically, so any existing valid
                # destination remains intact and its hidden temporary is the
                # only file cleanup ever needs to remove.
                self._post_log("maps: %s could not be copied (%s) — skipped"
                               % (n, e))
        self._post_log("maps: %d MB copied to /%s" % (copied // (1 << 20),
                                                      MAPS_DEST))

    def _copy_chunked(self, src, dst, done, total):
        """Copy one file, moving the progress bar as it goes; returns the new
        running byte total.

        Not shutil.copy2: a whole continent is minutes of copying, and a bar
        that does not move for minutes is indistinguishable from an installer
        that has hung — at the very end of an install, which is the worst place
        to make somebody wonder whether they can safely reboot."""
        span = 0.97 - 0.92
        dest_dir = os.path.dirname(dst) or "."
        temp_path = None
        with open(src, "rb") as rf:
            fd, temp_path = tempfile.mkstemp(
                prefix=".%s." % os.path.basename(dst), suffix=".tmp",
                dir=dest_dir)
            try:
                with os.fdopen(fd, "wb") as wf:
                    while True:
                        chunk = rf.read(8 << 20)
                        if not chunk:
                            break
                        wf.write(chunk)
                        done += len(chunk)
                        if total:
                            self._post_progress(
                                0.92 + span * min(1.0, done / total))
                    wf.flush()
                    os.fsync(wf.fileno())
                os.replace(temp_path, dst)
                temp_path = None
                try:
                    dir_fd = os.open(dest_dir, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            finally:
                if temp_path is not None:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
        return done

    def _copy(self, src, dst):
        if not os.path.exists(src):
            raise InstallError("missing install payload: %s" % src)
        self._post_log("copy %s -> %s" % (src, dst))
        try:
            shutil.copy2(src, dst)
        except (OSError, shutil.Error) as e:
            raise InstallError("copy failed (%s): %s" % (src, e))

    def _unmount_target(self, disk, umount):
        # Unmount any stale target mounts first (ESP nested under root), then
        # anything mounted from the target disk itself. All best-effort.
        for mp in (os.path.join(TARGET_MNT, "boot", "efi"), TARGET_MNT):
            if self._is_mounted(mp):
                self._sh([umount, mp], allow_fail=True)
        for src, mp in self._mounts_on(disk):
            self._sh([umount, mp], allow_fail=True)

    def _is_mounted(self, mountpoint):
        try:
            with open("/proc/mounts") as fh:
                for ln in fh:
                    p = ln.split()
                    if len(p) >= 2 and p[1] == mountpoint:
                        return True
        except OSError:
            pass
        return False

    def _mounts_on(self, disk):
        out = []
        try:
            with open("/proc/mounts") as fh:
                for ln in fh:
                    p = ln.split()
                    if len(p) >= 2 and p[0].startswith(disk):
                        out.append((p[0], p[1]))
        except OSError:
            pass
        # unmount deepest paths first
        out.sort(key=lambda t: t[1].count("/"), reverse=True)
        return out

    # -- target-tree configuration (Python file writes; guarded) --
    # The marker de/firstrun.py looks for. Under /var: machine state, not a
    # document, and it survives on the installed root.
    OEM_MARKER = "var/lib/notebookos/first-run"

    # The one name this file will write when it is handed something it cannot.
    # Matches the shipped /etc/hostname, so the fallback is the state the image
    # already ships rather than an invention.
    DEFAULT_HOSTNAME = "notebook"

    def _configure_target(self, root):
        # Never write a name the machine cannot carry. The Options step refuses
        # an invalid one — but "set this up for someone else" skips that whole
        # validation (every answer on the page belongs to the new owner), so a
        # blank or half-typed field left behind before the tick went on reached
        # this line unchecked and became an EMPTY /etc/hostname on the installed
        # machine. busybox `hostname -F` on an empty file is an error at every
        # boot, and firstrun.py's own hostname field is validated, so the only
        # way in was through here.
        host = (self.cfg.get("hostname") or "").strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", host):
            host = self.DEFAULT_HOSTNAME
        self._write_file(os.path.join(root, "etc", "hostname"), host + "\n")
        if self.cfg.get("username"):
            self._write_file(os.path.join(root, "etc", "notebookos-user"),
                             self.cfg["username"] + "\n")
        self._write_os_release(root)
        self._configure_fstab(root)
        if self.cfg.get("oem"):
            # Everything impersonal is written; the four answers that belong to
            # the person who will use this machine are left for their first
            # start-up. Root stays LOCKED as the image ships it, so the machine
            # comes up without a sign-in screen and firstrun.py is the first
            # thing they meet -- setting a password here would defeat the point
            # and hand them a secret they did not choose.
            self._write_file(os.path.join(root, self.OEM_MARKER),
                             "Notebook OS: first-run setup is owed.\n"
                             "de/firstrun.py removes this once it is done.\n")
            self._post_log("set up for someone else: they choose the name, "
                           "language, keyboard and password on first start")
            return
        self._configure_keyboard(root)
        self._configure_locale(root)
        self._configure_login(root)

    # ---- what an update carries across a replaced /etc ----
    #
    # These are the files a previous install or update WROTE into the system,
    # as opposed to the ones that came out of the tarball: the machine's own
    # name, its keyboard, its locale, its swap line and — the one that cannot
    # be reconstructed from anything — the password its owner chose. They all
    # live in /etc, which is part of the tree an update replaces, so they are
    # read out first and written back on top of the new one.
    #
    # Deliberately NOT in this list: locale.json. It lives in
    # /root/.config/notebook, which is in PRESERVED_DIRS and is therefore never
    # moved at all. The keyboard and the interface language the DESKTOP reads
    # survive an update by not being part of it, and adding them here would
    # create a second copy of that answer for the two to disagree over.
    def _read_carried(self, root):
        """Read the installer-written configuration out of a system tree."""
        keep = {}
        keep["hostname"] = self._read_line(os.path.join(root, "etc",
                                                        "hostname"))
        keep["username"] = self._read_line(os.path.join(root, "etc",
                                                        "notebookos-user"))
        keep["keyboard"] = self._read_text(
            os.path.join(root, "etc", "X11", "xorg.conf.d",
                         "00-keyboard.conf"))
        # The installer's own lines, lifted back out of files the image also
        # ships its own version of. Copying either file whole would carry an
        # OLD release's /etc/profile or /etc/fstab onto a new system and
        # silently undo whatever the new one changed there.
        keep["locale"] = self._lines_starting(
            os.path.join(root, "etc", "profile"), "export LANG=", "export LC_")
        keep["fstab"] = self._lines_starting(
            os.path.join(root, "etc", "fstab"), "LABEL=%s" % SWAP_LABEL)
        keep["console"] = bool(self._lines_starting(
            os.path.join(root, "etc", "inittab"), "%s::" % self.CONSOLE_TTY))
        keep["firstrun"] = os.path.exists(os.path.join(root, self.OEM_MARKER))
        # THE ONE LINE THAT CANNOT BE RECONSTRUCTED. The whole root entry is
        # carried verbatim, hash and ageing fields and all, because it is the
        # only copy of the password its owner chose and this installer cannot
        # ask for it: the machine being updated is not the one running. A
        # freshly unpacked /etc/shadow has root LOCKED, so an update that
        # failed to read this would hand back a machine that either asks for a
        # password nobody set or asks for nothing at all — a silent change to
        # how the machine is opened, either way. So it stops the run HERE,
        # while the old system is still in place and still starts up.
        keep["shadow"] = self._shadow_root_line(os.path.join(root, "etc",
                                                             "shadow"))
        if not keep["shadow"]:
            raise InstallError("cannot read the stored password from "
                               "%s/etc/shadow" % root)
        return keep

    def _apply_carried(self, root, keep):
        """Write the machine's own identity and credentials back on top of a
        newly unpacked /etc.

        Same files and the same shapes _configure_target writes on a fresh
        install, and through the same helpers, so an update cannot quietly hand
        back a machine configured differently from the one it replaced."""
        host = (keep.get("hostname") or "").strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", host):
            # The same refusal _configure_target makes, for the same reason:
            # busybox `hostname -F` on an empty or invalid file is an error at
            # every boot, and an update must not carry one machine's damaged
            # hostname onto the system replacing it.
            host = self.DEFAULT_HOSTNAME
        self._write_file(os.path.join(root, "etc", "hostname"), host + "\n")
        if keep.get("username"):
            self._write_file(os.path.join(root, "etc", "notebookos-user"),
                             keep["username"] + "\n")
        if keep.get("keyboard"):
            self._write_file(
                os.path.join(root, "etc", "X11", "xorg.conf.d",
                             "00-keyboard.conf"), keep["keyboard"])
        if keep.get("locale"):
            self._append_file(
                os.path.join(root, "etc", "profile"),
                "\n# %s installer — system locale\n%s\n"
                % (OS_NAME, keep["locale"]))
        if keep.get("fstab"):
            self._append_file(
                os.path.join(root, "etc", "fstab"),
                "# %s installer — swap partition\n%s\n"
                % (OS_NAME, keep["fstab"]))
        if keep.get("console"):
            # The machine had a text console, so the replacement gets one too.
            # Rewritten rather than copied: CONSOLE_TTY is where X is not (see
            # its own note), and the new inittab is the one that has to be
            # edited, not the old one that has been thrown away.
            self._rewrite_getty(root)
        self._restore_root_shadow(root, keep["shadow"])
        if keep.get("firstrun"):
            # This machine was set up for somebody else and they have not
            # answered firstrun.py yet. Updating it must not answer for them.
            self._write_file(os.path.join(root, self.OEM_MARKER),
                             "Notebook OS: first-run setup is owed.\n"
                             "de/firstrun.py removes this once it is done.\n")

    def _lines_starting(self, path, *prefixes):
        """Every line of a file that begins with one of `prefixes`, joined.

        A crude filter on purpose: it is only ever asked for lines this
        installer wrote itself, in the exact shape it wrote them, so anything
        cleverer would be a parser for a format nobody else produces."""
        out = [ln for ln in self._read_text(path).splitlines()
               if ln.startswith(prefixes)]
        return "\n".join(out)

    def _shadow_root_line(self, path):
        for ln in self._read_text(path).splitlines():
            if ln.split(":", 1)[0] == "root":
                return ln
        return ""

    def _restore_root_shadow(self, root, line):
        """Put a carried root entry back into a new /etc/shadow, verbatim.

        Not _set_root_password: that one takes a hash and rewrites the
        last-changed date, which would be a lie about a password nobody has
        just changed — and it cannot express a LOCKED account at all, which is
        what a machine set up to start straight into the desktop has. Carrying
        the whole line keeps a locked account locked, a hashed one hashed, and
        every ageing field exactly as it was."""
        path = os.path.join(root, "etc", "shadow")
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except OSError as e:
            raise InstallError("cannot read %s: %s" % (path, e))
        out = []
        found = False
        for ln in lines:
            if ln.split(":", 1)[0] == "root":
                out.append(line.rstrip("\n") + "\n")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(line.rstrip("\n") + "\n")
        try:
            with open(path, "w") as fh:
                fh.writelines(out)
            self._post_log("root: the password already on this machine is "
                           "kept, exactly as it was")
        except OSError as e:
            raise InstallError("cannot update %s: %s" % (path, e))

    def _write_file(self, path, data, mode=None):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(data)
            if mode is not None:
                os.chmod(path, mode)
            self._post_log("wrote %s" % path)
        except OSError as e:
            raise InstallError("cannot write %s: %s" % (path, e))

    def _append_file(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as fh:
                fh.write(data)
            self._post_log("updated %s" % path)
        except OSError as e:
            raise InstallError("cannot update %s: %s" % (path, e))

    def _write_os_release(self, root):
        build_id = ""
        try:
            with open(OS_RELEASE_SOURCE, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("BUILD_ID="):
                        candidate = line.partition("=")[2].strip().strip('"')
                        # The build stamps a UTC date.  Keep a conservative
                        # os-release token here rather than copying arbitrary
                        # source text into the installed system file.
                        if re.fullmatch(r"[A-Za-z0-9._-]+", candidate):
                            build_id = candidate
                        break
        except OSError:
            pass
        data = (
            'NAME="%s"\n' % OS_NAME +
            "ID=%s\n" % OS_ID +
            'VERSION="%s"\n' % OS_VERSION +
            "VERSION_ID=%s\n" % OS_VERSION_ID +
            'PRETTY_NAME="%s"\n' % OS_PRETTY)
        if build_id:
            data += 'BUILD_ID="%s"\n' % build_id
        self._write_file(os.path.join(root, "etc", "os-release"), data)

    def _configure_fstab(self, root):
        """Add the swap partition to the installed /etc/fstab.

        Without this the install created the partition, ran mkswap on it, and
        then nothing ever switched it on: /etc/inittab's `swapon -a` reads
        /etc/fstab, and the shipped fstab has no swap line. The user lost the
        disk space and gained nothing.

        By LABEL, not by device path: /dev/sda2 becomes /dev/sdb2 the day
        another disk is plugged in. Verified on this build — busybox swapon is
        compiled with FEATURE_SWAPONOFF_LABEL and resolves LABEL= via blkid.
        """
        if not self.cfg.get("swap"):
            return
        self._append_file(
            os.path.join(root, "etc", "fstab"),
            "# %s installer — swap partition\n"
            "LABEL=%s\tswap\tswap\tdefaults\t0\t0\n" % (OS_NAME, SWAP_LABEL))

    @staticmethod
    def _xkb_parts(code):
        """An nbi18n layout code -> (layout, variant, options) for xorg.conf.

        nbi18n codes are setxkbmap strings: "jp(kana)" is a VARIANT (writing it
        as XkbLayout produces no keymap at all) and "ru,us" is a dual layout
        that needs a switch key or its Latin half is unreachable — which for
        Russian, Hindi, Greek and Yiddish means no way to type a password.

        nbkeyboard owns these rules now, and it gets the case this body never
        could: a VARIANT INSIDE A MULTI-GROUP CODE. "jp(kana),us" fell through
        the regex whole and was written into the target's xorg.conf as the
        literal layout name "jp(kana),us", which no server can resolve — an
        installed machine with no keymap at all. The old body stays as the
        fallback so the installer still runs off a damaged live medium.
        """
        try:
            import nbkeyboard                                  # noqa: PLC0415
            return nbkeyboard.xorg_parts(code)
        except Exception:                                      # noqa: BLE001
            pass
        variant = ""
        m = re.match(r"^([^(]+)\((.+)\)$", code or "")
        if m:
            code, variant = m.group(1), m.group(2)
        options = "grp:alt_shift_toggle" if "," in (code or "") else ""
        return code or "us", variant, options

    def _configure_keyboard(self, root):
        code = KBD_LAYOUTS[self.cfg["kbd"]][1]
        layout, variant, options = self._xkb_parts(code)
        # Persistent X keyboard layout for anything that starts X before the
        # desktop session does (and for a bare X on this machine).
        conf = ['Section "InputClass"',
                '    Identifier "system-keyboard"',
                '    MatchIsKeyboard "on"',
                '    Option "XkbLayout" "%s"' % layout]
        if variant:
            conf.append('    Option "XkbVariant" "%s"' % variant)
        if options:
            conf.append('    Option "XkbOptions" "%s"' % options)
        conf.append("EndSection")
        self._write_file(
            os.path.join(root, "etc", "X11", "xorg.conf.d", "00-keyboard.conf"),
            "\n".join(conf) + "\n")
        # ...and the file the DESKTOP actually reads. session.sh applies
        # nbi18n.keyboard(), which reads locale.json out of $NB_HOME; the
        # installer never wrote it, so every install fell back to "us" and an
        # AZERTY install came up QWERTY. The UI language goes in the same file:
        # someone installing from a French live session wants a French machine.
        self._write_locale_json(root, code)

    def _write_locale_json(self, root, kbd_code):
        import json
        # NB_HOME is /root on this appliance (session.sh sets it, and there is
        # no second account) — see _configure_login: this is a single-user
        # machine by design.
        path = os.path.join(root, "root", ".config", "notebook", "locale.json")
        data = {"keyboard": kbd_code, "lang": nbi18n.current_lang()}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(data, fh)
            self._post_log("wrote %s (keyboard=%s lang=%s)"
                           % (path, data["keyboard"], data["lang"]))
        except OSError as e:
            raise InstallError("cannot write %s: %s" % (path, e))

    def _configure_locale(self, root):
        code = LOCALES[self.cfg["locale"]][1]
        # Export LANG from /etc/profile so every login shell (and the session it
        # starts) picks it up. Only C / C.UTF-8 are guaranteed on this image.
        block = ("\n# Notebook OS installer — system locale\n"
                 "export LANG=%s\n"
                 "export LC_ALL=%s\n" % (code, code))
        self._append_file(os.path.join(root, "etc", "profile"), block)

    # No _create_user. It wrote /etc/passwd, /etc/shadow, the groups and a
    # home directory for an account that nothing on this machine ever used:
    # session.sh pins NB_HOME=/root and S99notebookos starts the desktop as the
    # administrator, so /home/<user> stayed empty forever while every document
    # lived in /root. This is a single-user computer; the password the Options
    # step collects guards the desktop and the console, and _configure_login
    # sets it.

    def _configure_login(self, root):
        if self.cfg["root_passwordless"]:
            # No password anywhere: root's account stays locked exactly as the
            # image ships it, de/login.py's has_password() therefore answers
            # "nothing to ask for", and the machine starts straight into the
            # desktop. There is no console getty to leave alone either — the
            # shipped inittab deliberately has none.
            self._post_log("no password set: the machine starts straight into "
                           "the desktop, and there is no text console")
            return
        # Secure default: a text console on tty2 with a real login prompt, and
        # root given the password just collected — which is what the desktop
        # sign-in screen (de/login.py) checks as well, so the same password
        # opens the machine both ways and there is exactly one to remember.
        self._rewrite_getty(root)
        self._set_root_password(root, self._hash_password(self.cfg["password"]))

    # The text console goes on tty2, NEVER tty1.
    #
    # X OWNS tty1. S99notebookos starts the desktop with `xinit ... -- :0 vt1`,
    # and the shipped /etc/inittab says so in as many words ("NO GETTY ON tty1.
    # X owns tty1"). This installer used to append a tty1 getty anyway — the
    # shipped file has no `tty1::` line to replace, so the replace loop always
    # fell through to the append — which put busybox init's respawning getty and
    # the X server on the same virtual terminal on every installed machine.
    # Ctrl+Alt+F2 reaches the console; tty1 stays the desktop's.
    CONSOLE_TTY = "tty2"

    def _getty_line(self):
        return "%s::respawn:/sbin/getty 38400 %s\n" % (self.CONSOLE_TTY,
                                                       self.CONSOLE_TTY)

    def _getty_block(self):
        return ("# Text console, added by the %s installer. Reach it with\n"
                "# Ctrl+Alt+F%s; Ctrl+Alt+F1 comes back to the desktop.\n"
                "# NOT tty1: X owns tty1 (S99notebookos runs xinit on vt1).\n"
                % (OS_NAME, self.CONSOLE_TTY[-1])) + self._getty_line() + "\n"

    def _rewrite_getty(self, root):
        path = os.path.join(root, "etc", "inittab")
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except OSError as e:
            raise InstallError("cannot read %s: %s" % (path, e))
        line = self._getty_line()
        out = []
        changed = False
        for ln in lines:
            # Replace an existing console getty on ANY virtual terminal — a
            # second install over the first must not leave two of them — and
            # remove a tty1 getty outright wherever one came from.
            if ln.startswith(("tty1::", "%s::" % self.CONSOLE_TTY)):
                if not changed:
                    out.append(line)
                    changed = True
                continue
            out.append(ln)
        if not changed:
            # Put it at the head of the console section rather than after the
            # ::shutdown: lines at the end of the file, so the installed
            # inittab still reads as something a person could maintain.
            for anchor in ("# Put a getty", "ttyS1::"):
                for i, ln in enumerate(out):
                    if ln.startswith(anchor):
                        out.insert(i, self._getty_block())
                        changed = True
                        break
                if changed:
                    break
        if not changed:
            out.append(line)
        try:
            with open(path, "w") as fh:
                fh.writelines(out)
            self._post_log("%s: text console with a login prompt "
                           "(tty1 is left to the desktop)" % self.CONSOLE_TTY)
        except OSError as e:
            raise InstallError("cannot update %s: %s" % (path, e))

    def _set_root_password(self, root, pwhash):
        path = os.path.join(root, "etc", "shadow")
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except OSError as e:
            raise InstallError("cannot read %s: %s" % (path, e))
        lastchg = int(time.time() // 86400)
        out = []
        found = False
        for ln in lines:
            f = ln.rstrip("\n").split(":")
            if f and f[0] == "root":
                while len(f) < 9:
                    f.append("")
                f[1] = pwhash
                f[2] = str(lastchg)
                out.append(":".join(f) + "\n")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append("root:%s:%d:0:99999:7:::\n" % (pwhash, lastchg))
        try:
            with open(path, "w") as fh:
                fh.writelines(out)
            self._post_log("root: password set")
        except OSError as e:
            raise InstallError("cannot update %s: %s" % (path, e))

    def _hashing_available(self):
        """True when a password typed on the Options step can actually be
        stored. Same two routes _hash_password uses, tried for real rather than
        assumed: `crypt` is absent from Python 3.13 and openssl is not on this
        image at all, so neither can be taken on faith."""
        if _crypt is not None:
            try:
                h = _crypt.crypt("probe", _crypt.mksalt(_crypt.METHOD_SHA512))
                if isinstance(h, str) and h.startswith("$6$") and len(h) > 20:
                    return True
            except Exception:                                  # noqa: BLE001
                pass
        openssl = shutil.which("openssl")
        if openssl:
            rc, out = run_cmd([openssl, "passwd", "-6", "probe"])
            return rc == 0 and out.strip().startswith("$6$")
        return False

    def _hash_password(self, pw):
        if _crypt is not None:
            try:
                return _crypt.crypt(pw, _crypt.mksalt(_crypt.METHOD_SHA512))
            except Exception:
                pass
        openssl = shutil.which("openssl")
        if openssl:
            rc, out = run_cmd([openssl, "passwd", "-6", pw])
            if rc == 0 and out.strip().startswith("$6$"):
                return out.strip()
        raise InstallError("no password hashing available (crypt/openssl "
                           "missing)")

    # ------------------------------------------------------------------- styling
    def _install_css(self):
        css = ("""
        .inst-body { background: #FCFBF8; }
        .inst-body scrolledwindow, .inst-body viewport,
        .inst-body stack, .inst-body scrolledwindow > * { background: #FCFBF8; }
        /* scrollbar: paper, never a black/dark band on the right of a scrolling
           page (the software stack shows an unstyled scrollbar as black). */
        .inst-body scrollbar { background: #FCFBF8; border: none; }
        .inst-body scrollbar trough { background: #F1EEE6; border: none;
                    border-radius: 8px; margin: 2px; }
        .inst-body scrollbar slider { background: #C9C4B6; border-radius: 8px;
                    min-width: 9px; min-height: 30px; border: 2px solid #F1EEE6; }
        .inst-body scrollbar slider:hover { background: #9A9484; }

        /* step rail */
        .inst-rail { background: #F1EEE6; border-right: 1px solid #C9C4B6;
                     padding: 34px 18px 20px; }
        .inst-rail * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-rail-brand { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                     font-size: 20px; font-weight: 600; color: #1A1916; }
        .inst-rail-sub { font-size: 12px; color: #6E695E; margin-bottom: 22px; }
        .inst-step { padding: 9px 8px; margin: 2px 0; border-radius: 6px;
                     border-left: 3px solid transparent; }
        .inst-step-num { min-width: 24px; min-height: 24px;
                     background: #EAE3D2; color: #6E695E; border-radius: 50%;
                     font-size: 12px; font-weight: 700; padding: 2px 0; }
        .inst-step-lbl { font-size: 14px; color: #6E695E; }
        .inst-step.active { border-left: 3px solid #C8341E; background: #EAE3D2; }
        .inst-step.active .inst-step-num { background: #C8341E; color: #FCFBF8; }
        .inst-step.active .inst-step-lbl { color: #1A1916; font-weight: 600; }
        .inst-step.done .inst-step-num { background: #1A1916; color: #FCFBF8; }
        .inst-step.done .inst-step-lbl { color: #6E695E; }
        .inst-step-hit { padding: 0; border: none; background: transparent;
                         background-image: none; box-shadow: none; }

        /* page */
        .inst-page { background: #FCFBF8; padding: 40px 52px 30px; }
        .inst-page * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-h1 { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                   font-size: 30px; font-weight: 600; color: #1A1916;
                   margin-bottom: 6px; }
        .inst-sub { font-size: 14px; color: #6E695E; margin-bottom: 8px; }
        .inst-para { font-size: 14px; color: #2A2620; }
        .inst-note { font-size: 12px; color: #6E695E; }
        .inst-hint { font-size: 13px; color: #C8341E; }
        .inst-blocktxt { font-size: 12px; color: #C8341E; }
        .inst-group { font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
                      color: #6E695E; margin: 22px 2px 8px; }

        /* cards / rows */
        .inst-card { background: #F4F2EC; border: 1px solid #D7D2C5;
                     border-radius: 12px; padding: 2px 22px;
                     box-shadow: 0 1px 3px rgba(26,25,22,0.05); }
        .inst-item { padding: 15px 2px; min-height: 28px; }
        .inst-item.bordered { border-top: 1px solid #D7D2C5; }
        .inst-label { font-size: 14px; color: #1A1916; }
        .inst-sublabel { font-size: 12px; color: #6E695E; }
        .inst-value { font-size: 14px; color: #6E695E; }

        /* danger / red accent */
        .inst-danger { background: #FBEEEB; border: 1px solid #E3B4AC;
                       border-left: 4px solid #C8341E; border-radius: 4px;
                       padding: 12px 16px; }
        .inst-danger-txt { font-size: 13px; color: #8E2417; }
        /* the same panel over a disk we have read and found blank: paper and
           ink, so signage red keeps meaning "there is something here to lose" */
        .inst-danger.calm { background: #F4F2EC; border-color: #D7D2C5;
                       border-left-color: #9A9484; }
        .inst-danger-txt.calm { color: #2A2620; }
        /* an instruction that must be read but is not an alarm — the same
           panel shape as .inst-danger in paper and ink, so it carries weight
           without putting a second red on a screen that already has one. */
        .inst-callout { background: #F4F2EC; border: 1px solid #D7D2C5;
                       border-left: 4px solid #9A9484; border-radius: 4px;
                       padding: 12px 16px; font-size: 13px; color: #2A2620; }

        /* buttons */
        .inst-btn { padding: 8px 22px; background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 8px;
                    box-shadow: none; font-size: 14px; }
        .inst-btn:hover { background: #F1EEE6; }
        .inst-btn:disabled { color: #B3AD9E; background: #F4F2EC; }
        /* RED MEANS "THIS DESTROYS SOMETHING", AND ONLY THAT.
           Both of these used to be the same #C8341E, so the wizard's Next
           button was signage-red on EVERY step -- and then on the Summary step,
           where it becomes "Erase disk and install" and genuinely does wipe a
           disk, it looked exactly like the Next the user had already clicked
           four times. The colour carried no information at the one moment it
           needed to carry all of it.
           So Next is now the ink primary the rest of the OS uses for its
           main action (theme .suggested-action), and red appears exactly ONCE
           in the whole flow: on the button that erases the disk. */
        .inst-primary { padding: 10px 26px; background: #1A1916;
                    background-image: none; color: #FCFBF8; border: 1px solid #1A1916;
                    border-radius: 8px; box-shadow: none; font-size: 15px;
                    font-weight: 600; }
        .inst-primary:hover { background: #2A2620; border-color: #2A2620; }
        .inst-primary:disabled { background: #B3AD9E; border-color: #B3AD9E;
                    color: #FCFBF8; }
        .inst-next { background: #1A1916; background-image: none; color: #FCFBF8;
                    border-color: #1A1916; }
        .inst-next:hover { background: #2A2620; border-color: #2A2620; }
        .inst-next:disabled { background: #B3AD9E; border-color: #B3AD9E;
                    color: #FCFBF8; }
        /* The destructive step. TWO classes, deliberately: .inst-primary is
           added to the SAME button that already carries .inst-next, and
           .inst-next is defined after it, so a single-class rule would lose the
           cascade and the erase button would come out ink like every other
           step. Matching both raises specificity above either alone. */
        .inst-next.inst-primary { background: #C8341E; border-color: #B12D19;
                    color: #FCFBF8; }
        .inst-next.inst-primary:hover { background: #B12D19;
                    border-color: #B12D19; }
        .inst-next.inst-primary:disabled { background: #E0B8B0;
                    border-color: #E0B8B0; color: #FCFBF8; }

        /* footer */
        .inst-footer { background: #F1EEE6; border-top: 1px solid #C9C4B6;
                       padding: 14px 30px; }
        .inst-footer * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-foot-status { font-size: 12px; color: #6E695E; }

        /* form controls */
        .inst-page entry { background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 8px;
                    box-shadow: none; padding: 5px 9px; }
        .inst-page entry:focus { border-color: #9A9484; }
        .inst-page combobox button.combo { background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 8px;
                    box-shadow: none; padding: 4px 10px; }
        .inst-page spinbutton { background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 8px;
                    box-shadow: none; }
        .inst-check { font-size: 14px; color: #1A1916; }
        .inst-disk { font-size: 14px; color: #1A1916; }
        /* a disk row: its name, and under it what is on it today. The second
           line stays muted — the red erase banner carries the alarm, and two
           reds on one screen is not the design language. */
        .inst-disk-name { font-size: 14px; color: #1A1916; }
        .inst-disk-sub { font-size: 12px; color: #6E695E; }

        /* UNAVAILABLE. Every rule above that hard-sets ink or paper on a
           control needs a :disabled twin, because this provider sits at
           APPLICATION+1 and therefore beats the theme's own insensitive
           styling whatever the theme's specificity. Without them a greyed-out
           control came out pixel-identical to a live one — the whole identity
           card under "Set this up for someone else", the password rows under
           the passwordless tick, and a disk row too small to install onto —
           so clicking them simply felt dead. Muted ink on card paper, the
           same pair .inst-btn:disabled already uses. */
        .inst-page entry:disabled { background: #F4F2EC;
                    border-color: #D7D2C5; }
        .inst-page combobox button.combo:disabled { background: #F4F2EC;
                    border-color: #D7D2C5; }
        .inst-page spinbutton:disabled { background: #F4F2EC;
                    border-color: #D7D2C5; }
        /* The ink has to be named on the node that DRAWS the text. Papertone's
           own `* { color: @ink }` matches a label, a cellview and a spinner's
           step buttons directly, and a direct match beats an inherited value
           however high this provider's priority is — the OS-wide button-label
           trap. A colour set on the control alone left every one of these
           reading as live. */
        .inst-page entry:disabled,
        .inst-page combobox button.combo:disabled,
        .inst-page combobox button.combo:disabled label,
        .inst-page combobox button.combo:disabled cellview,
        .inst-page spinbutton:disabled,
        .inst-page spinbutton:disabled text,
        .inst-page spinbutton:disabled button,
        .inst-label:disabled, .inst-sublabel:disabled, .inst-value:disabled,
        .inst-check:disabled, .inst-check:disabled label,
        .inst-disk:disabled, .inst-disk:disabled label,
        .inst-disk-name:disabled, .inst-disk-sub:disabled { color: #B3AD9E; }

        /* progress */
        .inst-progstatus { font-size: 14px; color: #1A1916; font-weight: 600; }
        .inst-page progressbar trough { background: #DED4C2; border: none;
                    border-radius: 100px; min-height: 16px; }
        .inst-page progressbar progress { background: #C8341E; border-radius: 100px;
                    min-height: 16px; }
        .inst-page progressbar text { color: #1A1916; font-size: 12px; }
        /* install report: a warm-paper panel with ink text — NOT a black
           terminal. On the no-compositor software stack a dark surface reads as
           an unpainted (broken) region, so the whole wizard stays papertone. */
        .inst-logframe { background: #F4F2EC; border: 1px solid #D7D2C5;
                    border-radius: 12px; }
        .inst-log { background: #F4F2EC; color: #2A2620; padding: 10px 12px;
                    font-size: 12px; }
        .inst-log text { background: #F4F2EC; color: #2A2620; }
        .inst-log text selection { background: #EAE3D2; color: #1A1916; }

        /* confirm overlay */
        .inst-scrim { background: rgba(26,25,22,0.32); }
        .inst-confirm { background: #FCFBF8; border: 1px solid #1A1916;
                    padding: 26px 30px; }
        .inst-confirm * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-confirm-h { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    font-size: 20px; font-weight: 600; color: #1A1916; }
        .inst-confirm-b { font-size: 14px; color: #2A2620; }
        """).encode()
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    win = Installer()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
