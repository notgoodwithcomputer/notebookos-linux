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
from nbi18n import _t  # noqa: E402

# crypt is a stdlib C module; it is present on the target image but guard the
# import so this module still loads on a host that lacks it (openssl is the
# fallback, see _hash_password).
try:
    import crypt as _crypt
except Exception:
    _crypt = None

# settings.py owns the canonical time-zone table; import it so the installer's
# zone list is byte-identical to the one the live Settings app applies. The
# import is side-effect free (settings builds no window at import time).
try:
    import settings as _settings
    TIMEZONES = list(_settings.TIMEZONES)
except Exception:
    # Degrade to a minimal built-in list if settings can't be imported, so the
    # window still constructs. (label, IANA, POSIX-TZ) — same shape as settings.
    TIMEZONES = [
        ("UTC", "UTC", "UTC0"),
        ("London", "Europe/London", "GMT0BST,M3.5.0/1,M10.5.0"),
        ("Eastern (New York)", "America/New_York", "EST5EDT,M3.2.0,M11.1.0"),
    ]

# Keyboard layouts — copied verbatim from settings.py's Keyboard page so the two
# apps agree on the exact (label, xkb-code) set.
KBD_LAYOUTS = [("English (US)", "us"), ("English (UK)", "gb"),
               ("German", "de"), ("French", "fr"), ("Spanish", "es")]

# System locale. This is an offline appliance built without locale-gen, so only
# C / C.UTF-8 are guaranteed to resolve; the rest are offered honestly and take
# effect only if the target image ships their data.
LOCALES = [("C.UTF-8 (Unicode)", "C.UTF-8"), ("C (POSIX)", "C"),
           ("English (US) UTF-8", "en_US.UTF-8"),
           ("English (UK) UTF-8", "en_GB.UTF-8")]

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
ESP_LABEL = "NBOS_ESP"
ROOT_LABEL = "notebookos"
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
        ("target", "Target disk"),
        ("options", "Options"),
        ("summary", "Summary"),
        ("progress", "Install"),
        ("done", "Done"),
    ]

    def __init__(self):
        super().__init__()
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

        # Wizard state, filled as the user advances.
        self.cfg = {
            "disk": None, "disk_model": "", "disk_size": 0,
            "disk_contents": "",
            "hostname": "notebook",
            "tz": 0, "kbd": 0, "locale": 0,
            "username": "", "password": "", "password2": "",
            "root_passwordless": False,
            "swap": False, "swap_mib": 2048,
        }
        self._step = 0
        self._max_reached = 0
        self._working = False
        self._confirm_layer = None
        self._scan_gen = 0
        self._prev_disk = None   # last chosen disk, re-selected after a rescan
        self._pulse_on = False   # progress bar pulses during the long extract

        # A destructive write must never be interruptible: block the window
        # close (the snail logo, the app-name Close item, the window manager)
        # while the install worker is running, so a stray click cannot tear the
        # window down mid-write.
        self.connect("delete-event", self._on_delete)

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
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
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
                       "sets the machine up to start from it. Along the way "
                       "you name the computer, pick your time zone and "
                       "keyboard, and create the account you sign in with. "
                       "When it is finished, take out the installer and "
                       "restart." % OS_PRETTY)
            self._danger(col,
                         "The disk you choose will be wiped completely, and "
                         "everything on it is gone for good. Nothing is "
                         "written to any disk until you say so on the Summary "
                         "step — you can back out until then.")
        else:
            # Neutral state: no live medium (construct-all / a normal desktop
            # session). The whole destructive path stays unreachable.
            self._para(col,
                       "There is nothing here to install. This installer only "
                       "works when you start the computer from a %s installer "
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
            "Target disk",
            "Choose the disk to install onto. The disk you started the "
            "installer from is never listed, so it cannot be erased by "
            "mistake.")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top.set_margin_top(6)
        rescan = Gtk.Button(label=_t("Rescan"))
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
        self._disk_msg_row(card, "Scanning disks…")
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
        if gen != self._scan_gen:
            return False
        card = self._disk_card
        for ch in card.get_children():
            card.remove(ch)
        if not disks:
            # disks is None → lsblk absent; [] → none eligible.
            if disks is None or not self.tools.get("lsblk"):
                self._disk_msg_row(card, "This computer cannot list its disks, "
                                         "so there is nothing to install onto.")
            else:
                self._disk_msg_row(card, "No disk was found that can be "
                                         "installed onto. Connect one and press "
                                         "Rescan. (The disk you started the "
                                         "installer from is never offered.)")
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
        for i, (name, size, model, contents) in enumerate(disks):
            dev = "/dev/" + name
            r = Gtk.Box(spacing=12)
            r.get_style_context().add_class("inst-item")
            if i != 0:
                r.get_style_context().add_class("bordered")
            try:
                img = Gtk.Image.new_from_pixbuf(
                    nbicons.pixbuf("disk", 20, "#6E695E"))
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
            radios.append((rb, dev))
        card.show_all()
        self._erase_parent_hide()
        # Re-select the disk chosen before a rescan / step change if it is still
        # present, so the user's choice (and the enabled Next) survives. Done
        # after _erase_parent_hide so the toggle's erase banner stays visible.
        if self._prev_disk:
            for rb, dev in radios:
                if dev == self._prev_disk:
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
                "%s will be set up from scratch for %s. Nothing on it is "
                "lost." % (dev, OS_PRETTY))
        else:
            self._disk_erase.set_text(
                "Everything on %s will be erased for good — every file, photo "
                "and program on it. Make sure this is the right disk." % dev)
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
                bits.append("%s (%s)" % (_t("Something we cannot name"), size))
        extra = len(parts) - 3
        if extra > 0:
            bits.append(_t("and %d more") % extra)
        return ", ".join(bits)

    def _contents_line(self, contents):
        """The one sentence that says what is on the chosen disk today."""
        if not contents:
            return ""
        if contents == "EMPTY":
            return _t("This disk is empty — there is nothing on it to lose.")
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

        # -- identity --
        card = self._card(col, top=6)
        self._e_host = Gtk.Entry()
        self._e_host.set_text(self.cfg["hostname"])
        self._e_host.set_width_chars(20)
        self._e_host.connect("changed", lambda *_: self._validate())
        self._e_host.connect("activate", self._activate_next)
        ctl.add_widget(self._e_host)
        self._field_row(card, "Computer name", self._e_host, first=True,
                        sub="What this computer calls itself")

        # -- region --
        self._grp(col, "Region")
        card2 = self._card(col)
        self._c_tz = Gtk.ComboBoxText()
        for label, _iana, _posix in TIMEZONES:
            self._c_tz.append_text(label)
        self._c_tz.set_active(0)
        self._c_tz.connect("changed", lambda *_: self._validate())
        ctl.add_widget(self._c_tz)
        self._field_row(card2, "Time zone", self._c_tz, first=True)
        self._c_kbd = Gtk.ComboBoxText()
        for label, _code in KBD_LAYOUTS:
            self._c_kbd.append_text(label)
        self._c_kbd.set_active(0)
        ctl.add_widget(self._c_kbd)
        self._field_row(card2, "Keyboard layout", self._c_kbd)
        self._c_locale = Gtk.ComboBoxText()
        for label, _code in LOCALES:
            self._c_locale.append_text(label)
        self._c_locale.set_active(0)
        ctl.add_widget(self._c_locale)
        self._field_row(card2, "Text and characters", self._c_locale,
                        sub="Leave this as Unicode unless you have a reason to "
                            "change it")

        # -- account --
        self._grp(col, "Login account")
        card3 = self._card(col)
        self._e_user = Gtk.Entry()
        self._e_user.set_width_chars(20)
        self._e_user.set_placeholder_text(_t("username"))
        self._e_user.connect("changed", lambda *_: self._validate())
        self._e_user.connect("activate", self._activate_next)
        ctl.add_widget(self._e_user)
        self._field_row(card3, "Username", self._e_user, first=True,
                        sub="Lower-case letters, digits, - and _")
        self._e_pw = Gtk.Entry()
        self._e_pw.set_visibility(False)
        self._e_pw.set_width_chars(20)
        self._e_pw.connect("changed", lambda *_: self._validate())
        self._e_pw.connect("activate", self._activate_next)
        ctl.add_widget(self._e_pw)
        self._field_row(card3, "Password", self._e_pw)
        self._e_pw2 = Gtk.Entry()
        self._e_pw2.set_visibility(False)
        self._e_pw2.set_width_chars(20)
        self._e_pw2.connect("changed", lambda *_: self._validate())
        self._e_pw2.connect("activate", self._activate_next)
        ctl.add_widget(self._e_pw2)
        self._field_row(card3, "Confirm password", self._e_pw2)
        # Let a novice confirm what they typed rather than guess behind dots.
        self._chk_showpw = Gtk.CheckButton(label=_t("Show passwords"))
        self._chk_showpw.get_style_context().add_class("inst-check")
        self._chk_showpw.connect("toggled", self._on_showpw_toggle)
        spwrow = Gtk.Box()
        spwrow.get_style_context().add_class("inst-item")
        spwrow.get_style_context().add_class("bordered")
        spwrow.pack_start(self._chk_showpw, True, True, 0)
        card3.pack_start(spwrow, False, False, 0)
        self._chk_rootless = Gtk.CheckButton(
            label=_t("Also leave an administrator console open with no "
                     "password"))
        self._chk_rootless.get_style_context().add_class("inst-check")
        self._chk_rootless.connect("toggled", lambda *_: self._validate())
        rrow = Gtk.Box()
        rrow.get_style_context().add_class("inst-item")
        rrow.get_style_context().add_class("bordered")
        rrow.pack_start(self._chk_rootless, True, True, 0)
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
                        sub="In megabytes. Useful on a machine with little "
                            "memory; leave it off otherwise (known as swap).")

        # -- inline validation hint --
        self._opt_hint = Gtk.Label(xalign=0)
        self._opt_hint.get_style_context().add_class("inst-hint")
        self._opt_hint.set_line_wrap(True)
        self._opt_hint.set_margin_top(16)
        col.pack_start(self._opt_hint, False, False, 0)
        return outer

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
            "Review the plan. Nothing is written until you confirm.")
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

        # No in-page Install button: on a 768px-tall panel it sat BELOW the fold
        # with the footer's Next hidden, so the one screen that has to offer a
        # way forward appeared to offer none. The action now lives in the footer
        # (see _set_step / _on_next), where every other step's forward control
        # is and where nothing can push it off-screen.
        self._summary_card = self._card(col, top=16)
        self._summary_note = Gtk.Label(xalign=0)
        self._summary_note.get_style_context().add_class("inst-note")
        self._summary_note.set_line_wrap(True)
        self._summary_note.set_margin_top(16)
        col.pack_start(self._summary_note, False, False, 0)

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
        layout += "  ·  Notebook OS and your files, all the remaining space"

        tz = TIMEZONES[self.cfg["tz"]][0]
        kbd = KBD_LAYOUTS[self.cfg["kbd"]][0]
        loc = LOCALES[self.cfg["locale"]][0]
        user = self.cfg["username"] or "—"
        if self.cfg["root_passwordless"]:
            acct = ("%s  ·  plus an administrator console that asks for no "
                    "password" % user)
        else:
            acct = "%s  ·  asks for a password to sign in" % user

        rows = [
            ("Disk", dtxt),
        ]
        # What is being destroyed belongs in the review, directly under the
        # disk it names — this is the row that catches a wrong choice while
        # backing out is still free.
        if self.cfg.get("disk_contents") == "EMPTY":
            rows.append(("What is on it now", _t("Nothing — the disk is empty")))
        elif self.cfg.get("disk_contents"):
            rows.append(("What is on it now", self.cfg["disk_contents"]))
        rows += [
            ("How the disk is divided", layout),
            ("Computer name", self.cfg["hostname"]),
            ("Login account", acct),
            ("Time zone", tz),
            ("Keyboard", kbd),
            ("Text and characters", loc),
        ]
        for i, (k, v) in enumerate(rows):
            val = Gtk.Label(label=v, xalign=1)
            val.get_style_context().add_class("inst-value")
            val.set_line_wrap(True)
            val.set_max_width_chars(48)
            self._field_row(card, k, val, first=(i == 0))
        card.show_all()

        self._summary_note.set_text(
            "The installer also sets up the start-up files, so this machine "
            "switches straight on into Notebook OS.")
        tech = ("In technical terms: a %d MiB FAT32 EFI system partition, "
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
                "This computer cannot be installed to right now.")
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
        if not self.cfg.get("disk"):
            return False, "Go back to Target disk and choose a disk first."
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
        self._log_buf.set_text("")
        self._prog_bar.set_fraction(0.0)
        self._prog_bar.set_text("0%")
        self._prog_status.set_text("Starting…")
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
            "%s is installed. Take out the installer USB stick or disc, then "
            "restart — the machine will start up from the disk you chose."
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
        self._para(col,
                   "There is no rush — you can carry on using this session for "
                   "as long as you like. The installed copy will be waiting "
                   "whenever you switch the machine on again.", cls="inst-note")
        return outer

    def _confirm_shutdown(self):
        self._open_confirm(
            "Switch off now?",
            "The computer will switch off. Take the installer USB stick or "
            "disc out while it is off, then press the power button — it will "
            "start up into %s from the disk you installed to." % OS_PRETTY,
            "Switch off",
            lambda: self._do_power("poweroff"))

    def _confirm_restart(self):
        # Restarting is not destructive, but a novice who reboots with the medium
        # still inserted lands back in the live installer — so confirm and remind.
        self._open_confirm(
            "Restart now?",
            "Remove the install medium first, then restart, so the machine boots "
            "from the disk you installed to. If the medium is still inserted the "
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
        i = max(0, min(i, len(self.STEPS) - 1))
        self._step = i
        self._max_reached = max(self._max_reached, i)
        key = self.STEPS[i][0]

        if key == "target":
            self._refresh_disks()
        elif key == "summary":
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
        GLib.idle_add(self._flush_paint)

        # Put the cursor where the user will type next, so the keyboard works
        # without a mouse click first. Username is the first empty field on the
        # Options step (hostname already carries a sensible default).
        if key == "options" and hasattr(self, "_e_user"):
            try:
                self._e_user.grab_focus_without_selecting()
            except Exception:
                self._e_user.grab_focus()

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

    def _commit_step(self):
        key = self.STEPS[self._step][0]
        if key == "options":
            self.cfg["hostname"] = self._e_host.get_text().strip()
            self.cfg["tz"] = max(0, self._c_tz.get_active())
            self.cfg["kbd"] = max(0, self._c_kbd.get_active())
            self.cfg["locale"] = max(0, self._c_locale.get_active())
            self.cfg["username"] = self._e_user.get_text().strip()
            self.cfg["password"] = self._e_pw.get_text()
            self.cfg["password2"] = self._e_pw2.get_text()
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
        host = self._e_host.get_text().strip()
        user = self._e_user.get_text().strip()
        pw = self._e_pw.get_text()
        pw2 = self._e_pw2.get_text()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", host):
            return False, "Enter a valid hostname (letters, digits and -)."
        if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", user):
            return False, ("Enter a valid username (starts with a lower-case "
                           "letter or _, then letters, digits, - or _).")
        if user in ("root", "daemon", "bin", "sys", "nobody"):
            return False, "That username is reserved; choose another."
        if len(pw) < 1:
            return False, "Enter a password for the account."
        if pw != pw2:
            return False, "The passwords do not match."
        if self._chk_swap.get_active():
            sz = int(self._sp_swap.get_value())
            if sz < 256:
                return False, "Swap size must be at least 256 MiB."
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
                   "installed in its place. There is no undo, and no way to "
                   "get the old contents back.") % (disk, OS_PRETTY))
        # Name the contents in the last dialog too. Someone who has clicked
        # through four screens is no longer reading them; this is the one they
        # do read, and "Windows" here has stopped more mistakes than any
        # amount of prose earlier on.
        line = self._contents_line(self.cfg.get("disk_contents"))
        if line:
            body += "\n\n" + line
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
        GLib.idle_add(self._append_log, text)

    def _append_log(self, text):
        buf = self._log_buf
        buf.insert(buf.get_end_iter(),
                   text if text.endswith("\n") else text + "\n")
        adj = self._log_scroll.get_vadjustment()
        if adj is not None:
            adj.set_value(adj.get_upper())
        return False

    def _post_progress(self, frac, status=None):
        GLib.idle_add(self._apply_progress, frac, status)

    def _apply_progress(self, frac, status):
        # Any determinate update ends an indeterminate (pulsing) phase, so the
        # bar snaps back to a real percentage for the next step.
        self._pulse_on = False
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
        GLib.idle_add(self._begin_pulse, status)
        self._post_log("")
        self._post_log("== %s ==" % status)

    def _begin_pulse(self, status):
        self._prog_status.set_text(status)
        self._prog_bar.set_text("Working…")
        self._pulse_on = True
        self._prog_bar.pulse()
        GLib.timeout_add(140, self._pulse_tick)
        return False

    def _pulse_tick(self):
        if not self._pulse_on:
            return False
        self._prog_bar.pulse()
        return True

    def _install_worker(self):
        try:
            self._do_install()
        except InstallError as e:
            GLib.idle_add(self._install_failed, str(e))
            return
        except Exception as e:   # never let the worker die silently
            GLib.idle_add(self._install_failed, "unexpected error: %s" % e)
            return
        GLib.idle_add(self._install_done)

    def _install_failed(self, msg):
        self._working = False
        self._post_progress(self._prog_bar.get_fraction(), "Installation stopped")
        self._fail_box.set_no_show_all(False)
        # Plain English first, the exact reason after it. Be straight about the
        # state of the disk: preparing it is the FIRST thing the engine does, so
        # by the time anything can fail the old contents are already gone —
        # telling the user "nothing was written" would be a comforting lie.
        self._fail_lbl.set_text(
            "The installation stopped part-way through and could not finish. "
            "The disk was already being erased, so it will not start up as it "
            "is — go back and try again.\n\n"
            "What went wrong: %s" % msg)
        self._fail_box.show_all()
        # Open the detailed report: this is the moment it earns its place.
        if not self._log_toggle.get_active():
            self._log_toggle.set_active(True)
        self.back_btn.show()
        self.back_btn.set_sensitive(True)
        return False

    def _install_done(self):
        self._working = False
        self._post_progress(1.0, "Complete")
        # Say which disk it went onto. After erasing one, "it worked" is not
        # the reassurance people are looking for — "it is on THAT disk" is.
        disk = self.cfg.get("disk")
        if disk:
            self._done_para.set_text(
                "%s is now installed on %s (%s), at %s. Take out the installer "
                "USB stick or disc, then start the machine again — it will "
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
                "One thing to do the first time you switch this machine on. "
                "It checks who signed the software it starts, so it will ask "
                "you to approve Notebook OS once. A blue screen appears: "
                "choose \"Enroll key from disk\", pick EFI/BOOT/MOK.cer, "
                "confirm, and restart. If you get a start-up error instead, "
                "open your firmware's boot menu and run EFI/BOOT/mmx64.efi to "
                "do the same thing.")
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
            self._sh([self.tools["mkswap"], swappart])
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
    def _configure_target(self, root):
        self._write_file(os.path.join(root, "etc", "hostname"),
                         self.cfg["hostname"] + "\n")
        self._write_os_release(root)
        self._configure_timezone(root)
        self._configure_keyboard(root)
        self._configure_locale(root)
        self._create_user(root)
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

    def _configure_timezone(self, root):
        _lbl, iana, posix = TIMEZONES[self.cfg["tz"]]
        # POSIX TZ string is tool-free and works without zoneinfo (the appliance
        # ships no tzdata); this is what actually drives the clock.
        self._write_file(os.path.join(root, "etc", "TZ"), posix + "\n")
        # If the target tree does ship the zoneinfo file, point localtime at it.
        zi = os.path.join(root, "usr", "share", "zoneinfo", iana)
        if os.path.exists(zi):
            link = os.path.join(root, "etc", "localtime")
            try:
                if os.path.islink(link) or os.path.exists(link):
                    os.remove(link)
                os.symlink("/usr/share/zoneinfo/" + iana, link)
                self._post_log("linked %s -> /usr/share/zoneinfo/%s"
                               % (link, iana))
            except OSError as e:
                raise InstallError("cannot set localtime: %s" % e)

    def _configure_keyboard(self, root):
        code = KBD_LAYOUTS[self.cfg["kbd"]][1]
        # Persistent X keyboard layout (the desktop runs on X; setxkbmap is the
        # live equivalent, this file is the on-disk form).
        conf = ('Section "InputClass"\n'
                '    Identifier "system-keyboard"\n'
                '    MatchIsKeyboard "on"\n'
                '    Option "XkbLayout" "%s"\n'
                'EndSection\n' % code)
        self._write_file(
            os.path.join(root, "etc", "X11", "xorg.conf.d", "00-keyboard.conf"),
            conf)

    def _configure_locale(self, root):
        code = LOCALES[self.cfg["locale"]][1]
        # Export LANG from /etc/profile so every login shell (and the session it
        # starts) picks it up. Only C / C.UTF-8 are guaranteed on this image.
        block = ("\n# Notebook OS installer — system locale\n"
                 "export LANG=%s\n"
                 "export LC_ALL=%s\n" % (code, code))
        self._append_file(os.path.join(root, "etc", "profile"), block)

    def _create_user(self, root):
        user = self.cfg["username"]
        pw = self.cfg["password"]
        uid = self._next_uid(root)
        gid = uid
        home = "/home/" + user
        pwhash = self._hash_password(pw)
        lastchg = int(time.time() // 86400)

        # /etc/passwd
        self._append_file(
            os.path.join(root, "etc", "passwd"),
            "%s:x:%d:%d:%s:%s:/bin/sh\n" % (user, uid, gid, user, home))
        # /etc/shadow
        self._append_file(
            os.path.join(root, "etc", "shadow"),
            "%s:%s:%d:0:99999:7:::\n" % (user, pwhash, lastchg))
        # primary group
        self._append_file(
            os.path.join(root, "etc", "group"),
            "%s:x:%d:\n" % (user, gid))
        # supplementary groups (only those the image actually has)
        self._add_to_groups(root, user,
                            ("wheel", "audio", "video", "input", "plugdev",
                             "netdev", "cdrom", "dialout"))
        # home directory owned by the new account
        try:
            os.makedirs(os.path.join(root, "home", user), exist_ok=True)
            os.chown(os.path.join(root, "home", user), uid, gid)
            self._post_log("created %s (uid %d)" % (home, uid))
        except OSError as e:
            raise InstallError("cannot create home dir: %s" % e)

    def _next_uid(self, root):
        used = set()
        try:
            with open(os.path.join(root, "etc", "passwd")) as fh:
                for ln in fh:
                    f = ln.split(":")
                    if len(f) >= 3:
                        try:
                            used.add(int(f[2]))
                        except ValueError:
                            pass
        except OSError:
            pass
        uid = 1000
        while uid in used:
            uid += 1
        return uid

    def _add_to_groups(self, root, user, groups):
        path = os.path.join(root, "etc", "group")
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except OSError as e:
            raise InstallError("cannot read %s: %s" % (path, e))
        wanted = set(groups)
        out = []
        for ln in lines:
            raw = ln.rstrip("\n")
            f = raw.split(":")
            if len(f) >= 4 and f[0] in wanted:
                members = [m for m in f[3].split(",") if m]
                if user not in members:
                    members.append(user)
                f[3] = ",".join(members)
                raw = ":".join(f)
            out.append(raw + "\n")
        try:
            with open(path, "w") as fh:
                fh.writelines(out)
            self._post_log("added %s to groups: %s"
                           % (user, ", ".join(sorted(wanted))))
        except OSError as e:
            raise InstallError("cannot update %s: %s" % (path, e))

    def _configure_login(self, root):
        if self.cfg["root_passwordless"]:
            # Keep the live image's convenience: tty1 auto-logs into a root
            # shell. Nothing to change (the tar already ships this form); note
            # it so the log is explicit.
            self._post_log("tty1: passwordless root console (left as shipped)")
            return
        # Secure default: switch tty1 to a normal login prompt, and give root a
        # known password (the account password) so the machine is recoverable
        # instead of relying on the image's empty root password.
        self._rewrite_getty(root)
        self._set_root_password(root, self._hash_password(self.cfg["password"]))

    def _rewrite_getty(self, root):
        path = os.path.join(root, "etc", "inittab")
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except OSError as e:
            raise InstallError("cannot read %s: %s" % (path, e))
        out = []
        changed = False
        for ln in lines:
            if ln.startswith("tty1::"):
                out.append("tty1::respawn:/sbin/getty 38400 tty1\n")
                changed = True
            else:
                out.append(ln)
        if not changed:
            out.append("tty1::respawn:/sbin/getty 38400 tty1\n")
        try:
            with open(path, "w") as fh:
                fh.writelines(out)
            self._post_log("tty1: switched to a login prompt")
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
                     font-size: 22px; font-weight: 600; color: #1A1916; }
        .inst-rail-sub { font-size: 12px; color: #9A9484; margin-bottom: 22px; }
        .inst-step { padding: 9px 8px; margin: 2px 0; border-radius: 3px;
                     border-left: 3px solid transparent; }
        .inst-step-num { min-width: 24px; min-height: 24px;
                     background: #E4DECF; color: #6E695E; border-radius: 50%;
                     font-size: 12px; font-weight: 700; padding: 2px 0; }
        .inst-step-lbl { font-size: 14px; color: #6E695E; }
        .inst-step.active { border-left: 3px solid #C8341E; background: #ECE7DA; }
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
        .inst-para { font-size: 14.5px; color: #2A2620; }
        .inst-note { font-size: 12.5px; color: #9A9484; }
        .inst-hint { font-size: 13px; color: #C8341E; }
        .inst-blocktxt { font-size: 12.5px; color: #C8341E; }
        .inst-group { font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
                      color: #9A9484; margin: 22px 2px 8px; }

        /* cards / rows */
        .inst-card { background: #F4F2EC; border: 1px solid #D7D2C5;
                     border-radius: 6px; padding: 2px 22px;
                     box-shadow: 0 1px 3px rgba(26,25,22,0.05); }
        .inst-item { padding: 15px 2px; min-height: 28px; }
        .inst-item.bordered { border-top: 1px solid #D7D2C5; }
        .inst-label { font-size: 14.5px; color: #1A1916; }
        .inst-sublabel { font-size: 12px; color: #9A9484; }
        .inst-value { font-size: 14px; color: #6E695E; }

        /* danger / red accent */
        .inst-danger { background: #FBEEEB; border: 1px solid #E3B4AC;
                       border-left: 4px solid #C8341E; border-radius: 4px;
                       padding: 12px 16px; }
        .inst-danger-txt { font-size: 13.5px; color: #8E2417; }
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
                       padding: 12px 16px; font-size: 13.5px; color: #2A2620; }

        /* buttons */
        .inst-btn { padding: 8px 22px; background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 4px;
                    box-shadow: none; font-size: 14px; }
        .inst-btn:hover { background: #F1EEE6; }
        .inst-btn:disabled { color: #B3AD9E; background: #F4F2EC; }
        .inst-primary { padding: 10px 26px; background: #C8341E;
                    background-image: none; color: #FCFBF8; border: 1px solid #C8341E;
                    border-radius: 4px; box-shadow: none; font-size: 15px;
                    font-weight: 600; }
        .inst-primary:hover { background: #B12D19; border-color: #B12D19; }
        .inst-primary:disabled { background: #E0B8B0; border-color: #E0B8B0;
                    color: #FCFBF8; }
        .inst-next { background: #C8341E; background-image: none; color: #FCFBF8;
                    border-color: #C8341E; }
        .inst-next:hover { background: #B12D19; border-color: #B12D19; }
        .inst-next:disabled { background: #E0B8B0; border-color: #E0B8B0;
                    color: #FCFBF8; }

        /* footer */
        .inst-footer { background: #F1EEE6; border-top: 1px solid #C9C4B6;
                       padding: 14px 30px; }
        .inst-footer * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-foot-status { font-size: 12.5px; color: #9A9484; }

        /* form controls */
        .inst-page entry { background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 4px;
                    box-shadow: none; padding: 5px 9px; }
        .inst-page entry:focus { border-color: #9A9484; }
        .inst-page combobox button.combo { background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 4px;
                    box-shadow: none; padding: 4px 10px; }
        .inst-page spinbutton { background: #FCFBF8; color: #1A1916;
                    border: 1px solid #C9C4B6; border-radius: 4px;
                    box-shadow: none; }
        .inst-check { font-size: 14px; color: #1A1916; }
        .inst-disk { font-size: 14px; color: #1A1916; }
        /* a disk row: its name, and under it what is on it today. The second
           line stays muted — the red erase banner carries the alarm, and two
           reds on one screen is not the design language. */
        .inst-disk-name { font-size: 14px; color: #1A1916; }
        .inst-disk-sub { font-size: 12.5px; color: #6E695E; }

        /* progress */
        .inst-progstatus { font-size: 14px; color: #1A1916; font-weight: 600; }
        .inst-page progressbar trough { background: #DDD8CB; border: none;
                    border-radius: 4px; min-height: 16px; }
        .inst-page progressbar progress { background: #C8341E; border-radius: 4px;
                    min-height: 16px; }
        .inst-page progressbar text { color: #1A1916; font-size: 12px; }
        /* install report: a warm-paper panel with ink text — NOT a black
           terminal. On the no-compositor software stack a dark surface reads as
           an unpainted (broken) region, so the whole wizard stays papertone. */
        .inst-logframe { background: #F4F2EC; border: 1px solid #D7D2C5;
                    border-radius: 6px; }
        .inst-log { background: #F4F2EC; color: #2A2620; padding: 10px 12px;
                    font-size: 12px; }
        .inst-log text { background: #F4F2EC; color: #2A2620; }
        .inst-log text selection { background: #E4DECF; color: #1A1916; }

        /* confirm overlay */
        .inst-scrim { background: rgba(26,25,22,0.32); }
        .inst-confirm { background: #FCFBF8; border: 1px solid #1A1916;
                    padding: 26px 30px; }
        .inst-confirm * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .inst-confirm-h { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    font-size: 21px; font-weight: 600; color: #1A1916; }
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
