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
# The fixed rootfs PARTUUID the prebuilt GRUB EFI boots (root=PARTUUID=...).
# Identical to the value tools/mkimage-uefi.sh bakes into grub.cfg.
ROOT_PARTUUID = "b8e5a5f2-1a2b-4c3d-9e8f-000000000042"

# Live-medium payload (mounted read-only at /run/live/medium by the live init).
LIVE_MEDIUM = "/run/live/medium"
INSTALL_DIR = os.path.join(LIVE_MEDIUM, "install")
ROOTFS_TAR = os.path.join(INSTALL_DIR, "rootfs.tar")
BOOT_EFI_SRC = os.path.join(INSTALL_DIR, "BOOTX64.EFI")
KERNEL_SRC = os.path.join(INSTALL_DIR, "bzImage")

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
        self._pulse_on = False   # progress bar pulses during the long extract

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
            # Wrap the row in its own EventBox so an already-completed step can be
            # clicked to jump back to it (the rail reads as navigation, so it must
            # behave like it). Forward/unreached steps and the destructive
            # Install/Done steps stay inert — see _on_rail_click.
            ebox = Gtk.EventBox()
            ebox.set_visible_window(False)   # input-only: catch clicks, no paint
            ebox.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            ebox.connect("button-press-event", self._on_rail_click, i)
            ebox.add(row)
            rail.pack_start(ebox, False, False, 0)
            self._rail_rows.append((row, num, lbl))
        return rail

    def _on_rail_click(self, _w, _ev, i):
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
        if subtitle:
            s = Gtk.Label(label=subtitle, xalign=0)
            s.get_style_context().add_class("inst-sub")
            s.set_line_wrap(True)
            inner.pack_start(s, False, False, 0)
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
            self._para(col,
                       "This copies %s onto a disk inside this machine and "
                       "sets the machine up to start from it. The steps ask "
                       "for a computer name, a keyboard layout and an "
                       "administrator password. When it is finished, remove "
                       "the installer and restart." % OS_PRETTY)
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
        self._disk_erase = self._danger(col, "", top=18)
        self._disk_erase.get_parent().set_no_show_all(True)
        self._disk_erase.get_parent().hide()
        self._disk_group = None
        return outer

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
        self._disk_group = None
        self.cfg["disk"] = None
        self.cfg["disk_contents"] = ""
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
        if not self._closed:
            GLib.idle_add(self._populate_disks, gen, disks)

    def _disk_msg_row(self, card, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("inst-value")
        lbl.set_line_wrap(True)
        row = Gtk.Box()
        row.get_style_context().add_class("inst-item")
        row.pack_start(lbl, True, True, 0)
        card.pack_start(row, False, False, 0)
        return lbl

    def _populate_disks(self, gen, disks):
        # Runs on the main thread via idle_add. Drop the result if a newer scan
        # (or a step change) has superseded it.
        if self._closed or gen != self._scan_gen:
            return False
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
            too_small = self._disk_too_small(size)
            r = Gtk.Box(spacing=12)
            r.get_style_context().add_class("inst-item")
            if i != 0:
                r.get_style_context().add_class("bordered")
            try:
                img = nbicons.image("disk", 20, "#6E695E")
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
                       contents)
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

    def _on_disk_toggle(self, btn, dev, model, size, contents=""):
        if not btn.get_active():
            return
        self.cfg["disk"] = dev
        self.cfg["disk_model"] = model or "Disk"
        self.cfg["disk_size"] = size
        self.cfg["disk_contents"] = contents
        p = self._disk_erase.get_parent()
        p.set_no_show_all(False)
        # No contents here: every row already carries its own "On it now" line
        # a few pixels above, for every disk rather than only the chosen one,
        # which is what lets someone COMPARE before clicking. This banner's job
        # is the consequence. The contents reappear on the Summary and in the
        # final confirmation, where no disk row is on screen to check against.
        #
        # A disk we have READ and found to be blank gets the same panel in
        # paper rather than alarm red, saying the true thing. Nothing is
        # hidden and no confirmation is weakened (the Summary and the final
        # modal are untouched) — but red that appears over an empty disk is red
        # that stops meaning anything on the disk that holds someone's photos.
        empty = (contents == "EMPTY")
        if empty:
            self._disk_erase.set_text(
                "%s is empty. It will be set up from scratch for %s."
                % (dev, OS_PRETTY))
        else:
            self._disk_erase.set_text(
                "Everything on %s will be erased for good: every file, photo "
                "and program on it. Check that this is the right disk." % dev)
        for w, cls in ((p, "inst-danger"), (self._disk_erase,
                                            "inst-danger-txt")):
            ctx = w.get_style_context()
            if empty:
                ctx.add_class("calm")
            else:
                ctx.remove_class("calm")
        p.show_all()
        self._validate()

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
            # Genuinely blank, or a disk whose table we cannot read — either
            # way there is nothing to name, and _contents_line says so.
            return "" if not out.strip() else "EMPTY"
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
        self._field_row(
            oem_card, "Who is this for", self._cb_oem, first=True,
            sub=_t("Set on first start-up instead of here"))

        # -- identity --
        card = self._card(col, top=6)
        self._e_user = Gtk.Entry()
        self._e_user.set_width_chars(20)
        self._e_user.set_placeholder_text(_t("Name"))
        self._e_user.connect("changed", lambda *_: self._validate())
        self._e_user.connect("activate", self._activate_next)
        ctl.add_widget(self._e_user)
        self._field_row(card, "Name", self._e_user, first=True,
                        sub=_t("Shown on the sign-in screen"))

        self._e_host = Gtk.Entry()
        self._e_host.set_text(self.cfg["hostname"])
        self._e_host.set_width_chars(20)
        self._e_host.connect("changed", lambda *_: self._validate())
        self._e_host.connect("activate", self._activate_next)
        ctl.add_widget(self._e_host)
        self._field_row(card, "Computer name", self._e_host)

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
        self._field_row(card2, "Keyboard layout", self._c_kbd, first=True)
        self._c_locale = Gtk.ComboBoxText()
        for label, _code in LOCALES:
            self._c_locale.append_text(label)
        self._c_locale.set_active(0)
        ctl.add_widget(self._c_locale)
        # The sub-label used to say "leave this as Unicode", naming a standard
        # nobody installing a computer has heard of. Point at the choice on the
        # screen instead — the first entry in the list, whose own label already
        # says what it does.
        self._field_row(card2, "Text and characters", self._c_locale,
                        sub="Leave as the first choice unless another is "
                            "required")
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
        card3 = None    # created after the note below, so the note sits above
        self._e_pw = Gtk.Entry()
        self._e_pw.set_visibility(False)
        self._e_pw.set_width_chars(20)
        self._e_pw.connect("changed", lambda *_: self._validate())
        self._e_pw.connect("activate", self._activate_next)
        ctl.add_widget(self._e_pw)
        # The explanation belongs to the PAIR, not to the first box. Hung under
        # "Password" it made that row two lines tall while "Confirm password"
        # was one, so the two boxes sat at different heights and the first read
        # as the bigger control. Same words, once, above both.
        card3 = self._card(col) if card3 is None else card3
        self._grp_note(col, _t("This password is asked for every time the "
                               "computer starts. It cannot be recovered."))
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
        self._field_row(card4, "How much to set aside", self._sp_swap,
                        sub="In megabytes (known as swap).")

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
        for row in getattr(self, "_pw_rows", []):
            row.set_sensitive(not btn.get_active())
        self._validate()

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
            acct = _t("asked for every time this computer starts")

        rows = [
            ("Disk", dtxt),
        ]
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
        for i, (k, v) in enumerate(rows):
            val = Gtk.Label(label=v, xalign=1)
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
        # Keep the footer hint in step with the button whichever way we got
        # here (_validate also sets it, to the same text).
        if hasattr(self, "_foot_hint"):
            self._foot_hint.set_text(reason)
        if ready:
            # The contents live in the "What is on it now" row directly below
            # this banner, where they are scannable beside everything else
            # being reviewed; saying them twice, two centimetres apart, only
            # makes the warning longer to read. The final confirmation repeats
            # them, because there the card is gone.
            self._summary_danger.set_text(
                "Everything on %s will be erased. This cannot be undone."
                % disk)
            self._install_block.set_text("")
            self._install_block.set_no_show_all(True)
            self._install_block.hide()
        else:
            self._summary_danger.set_text(
                _t("This computer cannot be installed to right now."))
            self._install_block.set_text(reason)
            self._install_block.set_no_show_all(False)
            self._install_block.show()

    def _install_ready(self):
        if not self.medium_ok:
            return False, ("The system to install is not here. Start the "
                           "computer from the %s medium and run the installer "
                           "from that desktop." % OS_NAME)
        if self.missing_tools:
            return False, ("This copy of %s is missing the tools that prepare "
                           "a disk, so it cannot install itself." % OS_NAME)
        # Checked HERE, not when the password is finally written: by then the
        # disk has been erased and the whole system extracted onto it.
        if not self.cfg.get("root_passwordless") and not self.can_hash:
            return False, (_t("This installer cannot store a password, so it "
                              "cannot finish an install that asks for one. On "
                              "the Options step, either switch on starting "
                              "without a password, or use a different %s "
                              "installer.") % OS_NAME)
        if not self.cfg.get("disk"):
            return False, "Go back to “Choose the disk” and pick one first."
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
        else:
            self._log_scroll.set_no_show_all(True)
            self._log_scroll.hide()

    def _reset_progress(self):
        self._pulse_on = False
        self._cancel_source("_pulse_source")
        self._log_buf.set_text("")
        self._prog_bar.set_fraction(0.0)
        self._prog_bar.set_text("0%")
        self._prog_status.set_text(_t("Starting…"))
        self._fail_box.set_no_show_all(True)
        self._fail_box.hide()

    # ------------------------------------------------------------------ 6 done
    def _page_done(self):
        outer, col = self._page_scaffold("Installation complete")
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
        btnrow.pack_start(shut, False, False, 0)
        restart = Gtk.Button(label=_t("Restart"))
        restart.get_style_context().add_class("inst-btn")
        restart.connect("clicked", lambda *_: self._confirm_restart())
        btnrow.pack_start(restart, False, False, 0)
        col.pack_start(btnrow, False, False, 0)
        return outer

    def _confirm_shutdown(self):
        self._open_confirm(
            "Switch off now?",
            "The computer will switch off. Remove the installer USB stick or "
            "disc while it is off, then press the power button. It will start "
            "up into %s from the disk just installed to." % OS_PRETTY,
            "Switch off",
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

        # rail state
        for j, (row, num, lbl) in enumerate(self._rail_rows):
            ctx = row.get_style_context()
            for c in ("active", "done"):
                ctx.remove_class(c)
            if j < i:
                ctx.add_class("done")
                # pin the tick to DejaVu Sans — the shipped Nimbus Sans has no
                # U+2713 and would show a tofu box for a completed step
                num.set_markup('<span face="DejaVu Sans">✓</span>')
            elif j == i:
                ctx.add_class("active")
                num.set_text(str(j + 1))
            else:
                num.set_text(str(j + 1))

        # footer visibility per step. The forward button is always the footer's
        # right-hand button; on the Summary step it becomes the destructive
        # primary and says exactly what it will do.
        ctx = self.next_btn.get_style_context()
        if key == "summary":
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
        self._validate()

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
        self._validate()

    def _commit_step(self):
        key = self.STEPS[self._step][0]
        if key == "options":
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
        # keep the summary install button honest if we are on it
        if key == "summary":
            self._refresh_summary()
        self._foot_status.set_text("Step %d of %d" % (self._step + 1,
                                                      len(self.STEPS)))
        if hasattr(self, "_foot_hint"):
            self._foot_hint.set_text(hint)

    def _validate_options(self):
        if self._cb_oem.get_active():
            # Every answer on this page now belongs to the new owner, so there
            # is nothing here left to get wrong.
            return True, ""
        host = self._e_host.get_text().strip()
        pw = self._e_pw.get_text()
        pw2 = self._e_pw2.get_text()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", host):
            return False, "Enter a valid hostname (letters, digits and -)."
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
        adj = self._log_scroll.get_vadjustment()
        if adj is not None:
            adj.set_value(adj.get_upper())
        return False

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
        # What WAS on that disk is now gone — the engine wipes it before it
        # does anything else. Backing up to the Summary from here would
        # otherwise show a "What is on it now: Windows" row, and repeat it in
        # the confirmation, about a disk that no longer holds any of it.
        self.cfg["disk_contents"] = ""
        self._post_progress(self._prog_bar.get_fraction(), "Installation stopped")
        self._fail_box.set_no_show_all(False)
        # Plain English first, the exact reason after it. Be straight about the
        # state of the disk: preparing it is the FIRST thing the engine does, so
        # by the time anything can fail the old contents are already gone —
        # telling the user "nothing was written" would be a comforting lie.
        self._fail_lbl.set_text(
            "The installation stopped part-way through. The disk was already "
            "being erased, so it will not start up as it is. Go back and try "
            "again.\n\n"
            "What went wrong: %s" % msg)
        self._fail_box.show_all()
        # Open the detailed report: this is the moment it earns its place.
        if not self._log_toggle.get_active():
            self._log_toggle.set_active(True)
        self.back_btn.show()
        self.back_btn.set_sensitive(True)
        return False

    def _install_done(self):
        if self._closed:
            return False
        self._working = False
        self._post_progress(1.0, "Complete")
        # Say which disk it went onto. After erasing one, "it worked" is not
        # the reassurance people are looking for — "it is on THAT disk" is.
        disk = self.cfg.get("disk")
        if disk:
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

        # f. Configure the installed tree.
        self._phase(0.90, "Configuring the system")
        self._configure_target(TARGET_MNT)

        # g. Finish: flush and unmount.
        self._phase(0.97, "Finishing up")
        if self.tools.get("sync"):
            self._sh([self.tools["sync"]], allow_fail=True)
        self._sh([umount, espmnt], allow_fail=True)
        self._sh([umount, TARGET_MNT], allow_fail=True)
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
        data = (
            'NAME="%s"\n' % OS_NAME +
            "ID=%s\n" % OS_ID +
            'VERSION="%s"\n' % OS_VERSION +
            "VERSION_ID=%s\n" % OS_VERSION_ID +
            'PRETTY_NAME="%s"\n' % OS_PRETTY)
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
        .inst-rail-sub { font-size: 12px; color: #9A9484; margin-bottom: 22px; }
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

        /* page */
        .inst-page { background: #FCFBF8; padding: 40px 52px 30px; }
        .inst-page * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-h1 { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                   font-size: 30px; font-weight: 600; color: #1A1916;
                   margin-bottom: 6px; }
        .inst-sub { font-size: 14px; color: #6E695E; margin-bottom: 8px; }
        .inst-para { font-size: 14px; color: #2A2620; }
        .inst-note { font-size: 12px; color: #9A9484; }
        .inst-hint { font-size: 13px; color: #C8341E; }
        .inst-blocktxt { font-size: 12px; color: #C8341E; }
        .inst-group { font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
                      color: #9A9484; margin: 22px 2px 8px; }

        /* cards / rows */
        .inst-card { background: #F4F2EC; border: 1px solid #D7D2C5;
                     border-radius: 12px; padding: 2px 22px;
                     box-shadow: 0 1px 3px rgba(26,25,22,0.05); }
        .inst-item { padding: 15px 2px; min-height: 28px; }
        .inst-item.bordered { border-top: 1px solid #D7D2C5; }
        .inst-label { font-size: 14px; color: #1A1916; }
        .inst-sublabel { font-size: 12px; color: #9A9484; }
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
        .inst-foot-status { font-size: 12px; color: #9A9484; }

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
