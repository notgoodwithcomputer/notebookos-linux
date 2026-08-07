#!/usr/bin/env python3
"""
GBA Emulator — Notebook OS front-end for the bundled VisualBoyAdvance-M core.

A cartridge library: it scans the Home folder for Game Boy / Game Boy Color /
Game Boy Advance ROMs and shows them as a grid of cartridges; selecting one (or
opening a ROM from the browser, or double-clicking a ROM in the Finder) launches
the vbam emulator. Detected USB game controllers — Logitech pads and friends —
are listed and work in-game through vbam's SDL joystick support. The library and
settings persist; nothing here fabricates a game that is not really on disk.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import sys
import json
import time
import shutil
import subprocess

import nbapp
import nbpicker
import nbicons
import nbgame
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
# CFG_DIR is still where the vbam log lives (_log_path). There is no
# gbaemu.json any more: it held only `fullscreen` and `scale`, neither of which
# could act on anything.
CFG_DIR = os.path.join(HOME, ".config", "notebook")

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
GHOST = "#9A9484"
RED = "#C8341E"

# ROM extension -> the system it runs on (for the card's kind line).
ROM_EXT = {
    ".gba": "Game Boy Advance", ".gb": "Game Boy",
    ".gbc": "Game Boy Color", ".sgb": "Super Game Boy",
}
# ROMs are also accepted zipped (vbam reads .zip directly).
ARCHIVE_EXT = {".zip"}
MAX_ROMS = 600


def _is_rom(path):
    return os.path.splitext(path)[1].lower() in ROM_EXT


# The smallest a file can be and still hold the cartridge header its system
# boots from: 0xC0 bytes for a GBA cartridge, 0x150 for a Game Boy one.
_MIN_SIZE = {".gba": 0xC0, ".gb": 0x150, ".gbc": 0x150, ".sgb": 0x150}
# The boot logo every cartridge carries, at 0x04 on a GBA and 0x104 on a Game
# Boy. Only the first 16 bytes are compared: that is more than enough to tell a
# cartridge from a text file or a half-copied download, and it does not reject a
# ROM whose logo has been patched further in.
_GBA_LOGO16 = bytes.fromhex("24ffae51699aa2213d84820a84e409ad")
_GB_LOGO16 = bytes.fromhex("ceed6666cc0d000b03730083000c000d")


def rom_problem(path):
    """Why `path` cannot be played, as a sentence to show the person — or None
    when it looks like a real cartridge.

    THE BUG THIS EXISTS FOR: every file whose name ended in .gba was handed
    straight to vbam. A game exported onto a USB stick and pulled out before it
    finished copying, a download that stopped half way, a text file somebody
    renamed — all of them launched, flashed a black screen, and came back with
    "the game closed right away, see the emulator log", which is a developer's
    answer to a question the player did not ask. Refuse it up front instead, and
    say which of the two things it is: not a game, or a broken copy of one.

    Deliberately permissive about everything else. Homebrew is a first-class
    citizen here — the GBA SDK next door makes it — so a cartridge that carries
    its logo or starts with a legal ARM branch is accepted whatever else is in
    it, and an unknown extension (.zip) is left for vbam to judge."""
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in _MIN_SIZE:
        return None                        # .zip and friends: not ours to judge
    try:
        size = os.path.getsize(path)
    except OSError:
        return _t("This file cannot be read, so it cannot be played.")
    if size == 0:
        return _t("This file is empty — there is no game in it. If it came "
                  "from a USB stick or the GBA SDK, copy or export it again.")
    if size < _MIN_SIZE[ext]:
        return _t("This file is far too small to be a game — only part of it "
                  "arrived. Copy it again, or export it again from the GBA SDK.")
    try:
        with open(path, "rb") as fh:
            head = fh.read(0x150)
    except OSError:
        return _t("This file cannot be read, so it cannot be played.")
    if ext == ".gba":
        # A cartridge either carries the boot logo (gbafix, and every commercial
        # ROM) or begins with the ARM branch a GBA jumps to (0xEA in the top
        # byte of the little-endian first word).
        if head[0x04:0x14] != _GBA_LOGO16 and head[3] != 0xEA:
            return _t("This does not look like a Game Boy Advance game. It may "
                      "be a different kind of file, or a copy that did not "
                      "finish.")
    elif head[0x104:0x114] != _GB_LOGO16:
        return _t("This does not look like a Game Boy game. It may be a "
                  "different kind of file, or a copy that did not finish.")
    return None


class GbaEmu(nbapp.AppWindow):
    app_name = "GBA Emulator"
    menus = ("File",)

    def __init__(self):
        super().__init__()
        self._install_css()

        # Set before anything can arm an idle or touch a widget: the deferred
        # command-line launch below reads this to decide whether the window it
        # belongs to is still there. Same gate accounting.py and contacts.py
        # carry.
        self._closed = False
        self._launch_source = 0         # the pending command-line launch idle

        # No settings file. There were two keys and neither could act: a game
        # ALWAYS runs fullscreen, because nbgame has to reparent vbam into a
        # fullscreen app window or the single-app WM unmaps it (see
        # nbgame._build_stage), and `scale` had no control at all — it was
        # loaded, range-checked to 1..6, and read by nothing. A Fullscreen
        # toggle that remembers your choice across reboots and changes nothing
        # is the quietest way for a control to lie, so it is gone rather than
        # unified or explained.
        self._roms = []                 # [{path, name, system, ext}]
        self._launch_time = 0.0
        self._session = None            # the running nbgame.GameSession, if any
        # NB: the vbam process, its stdout log and the Ctrl+Esc watch all live in
        # nbgame.GameSession now — gbaemu only owns the library + the session.

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        self.content.pack_start(outer, True, True, 0)

        outer.pack_start(self._header(), False, False, 0)

        # library (scrolls)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("libscroll")
        self._lib_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._lib_body.set_vexpand(True)
        scroll.add(self._lib_body)
        outer.pack_start(scroll, True, True, 0)

        outer.pack_start(self._controller_bar(), False, False, 0)

        self._scan_roms()
        self._render_library()
        self._render_controllers()

        # A ROM passed on the command line (Finder double-click) plays at once.
        rompath = next((a for a in sys.argv[1:]
                        if not a.startswith("-") and os.path.isfile(a)
                        and _is_rom(a)), None)
        # Deferred by one idle so the library window maps before the game's
        # fullscreen stage goes over it — but OWNED: the source id is kept so a
        # window closed inside that idle can cancel it, and the callback itself
        # checks the gate for the dispatch GLib had already committed to.
        if rompath:
            self._launch_source = GLib.idle_add(self._launch_pending, rompath)

        self.connect("destroy", self._on_destroy)

    def _launch_pending(self, rompath):
        """Play the command-line ROM, once, if the window is still alive."""
        self._launch_source = 0        # clear ownership before anything can fail
        if self._closed:
            return False   # window torn down inside the idle — launch nothing
        self._play(rompath)
        return False                   # one-shot

    # ================= header =================
    def _header(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.get_style_context().add_class("emuhead")

        icon = nbicons.image("gamepad", 26, INK)
        icon.set_valign(Gtk.Align.CENTER)
        bar.pack_start(icon, False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        t = Gtk.Label(label=_t("GBA Emulator"), xalign=0)
        t.get_style_context().add_class("emutitle")
        titles.pack_start(t, False, False, 0)
        s = Gtk.Label(label=_t("Game Boy · Color · Advance"), xalign=0)
        s.get_style_context().add_class("emusub")
        titles.pack_start(s, False, False, 0)
        bar.pack_start(titles, False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)

        openb = Gtk.Button()
        openb.set_relief(Gtk.ReliefStyle.NONE)
        openb.get_style_context().add_class("emubtn")
        oh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        oh.pack_start(nbicons.image("plus", 13, INK),
                      False, False, 0)
        oh.pack_start(Gtk.Label(label=_t("Open Game")), False, False, 0)
        openb.add(oh)
        openb.connect("clicked", lambda *_: self._open_rom())
        bar.pack_end(openb, False, False, 0)
        return bar

    # ================= library =================
    def _scan_roms(self):
        """Scan Home (recursively; hidden dirs skipped) for real ROM files."""
        found = []
        seen = set()
        try:
            for root, dirs, files in os.walk(HOME):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in ROM_EXT:
                        continue
                    p = os.path.join(root, f)
                    if p in seen:
                        continue
                    seen.add(p)
                    found.append({"path": p, "name": os.path.splitext(f)[0],
                                  "system": ROM_EXT[ext], "ext": ext})
                    if len(found) >= MAX_ROMS:
                        raise StopIteration
        except StopIteration:
            pass
        except Exception:
            pass
        found.sort(key=lambda m: m["name"].lower())
        self._roms = found

    def _render_library(self):
        for c in self._lib_body.get_children():
            self._lib_body.remove(c)
        if not self._vbam_path():
            # pack tight: as an expanding child this warning stretched into a
            # tall pink slab with two lines of text floating at the top of it
            self._lib_body.pack_start(self._notice(
                "Games can’t be played on this system",
                "The emulator is not installed. Games cannot be started."),
                False, False, 0)
        if not self._roms:
            self._lib_body.pack_start(self._empty_state(), True, True, 0)
            self._lib_body.show_all()
            return
        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_max_children_per_line(6)
        flow.set_min_children_per_line(2)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_row_spacing(16)
        flow.set_column_spacing(16)
        flow.get_style_context().add_class("libflow")
        for m in self._roms:
            flow.add(self._rom_card(m))
        self._lib_body.pack_start(flow, False, False, 0)
        self._lib_body.show_all()

    def _rom_card(self, m):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("romcard")
        art = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        art.get_style_context().add_class("romart")
        art.set_size_request(120, 120)
        img = nbicons.image("cartridge", 52, "#6E695E")
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        img.set_vexpand(True)
        art.pack_start(img, True, True, 0)
        card.pack_start(art, False, False, 0)
        nm = Gtk.Label(label=m["name"])
        nm.get_style_context().add_class("romname")
        nm.set_ellipsize(Pango.EllipsizeMode.END)
        nm.set_max_width_chars(15)
        nm.set_justify(Gtk.Justification.CENTER)
        card.pack_start(nm, False, False, 0)
        sysl = Gtk.Label(label=m["system"])
        sysl.get_style_context().add_class("romsys")
        card.pack_start(sysl, False, False, 0)
        # The card is wrapped in a real button, not an EventBox. An EventBox
        # takes no focus and answers no key, so the library was reachable by
        # pointer only: Tab skipped every game and there was no way to start
        # one from the keyboard at all. A button focuses, activates on Space
        # and Enter, and reports itself to assistive technology as a control.
        # .rombutton strips the button's own chrome so the card still looks
        # and measures exactly as it did.
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("rombutton")
        button.add(card)
        button.set_tooltip_text(_t("Play %s") % m["name"])
        button.connect("clicked", lambda _w, p=m["path"]: self._play(p))
        return button

    def _empty_state(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_start(40)
        box.set_margin_end(40)
        g = nbicons.image("cartridge", 52, GHOST)
        g.set_halign(Gtk.Align.CENTER)
        box.pack_start(g, False, False, 0)
        t = Gtk.Label(label=_t("No games"))
        t.get_style_context().add_class("emptytitle")
        box.pack_start(t, False, False, 0)
        s = Gtk.Label(
            label="Open Game adds a .gba, .gbc, .gb or .sgb file. "
                  "The GBA SDK writes games here when it exports.")
        s.set_justify(Gtk.Justification.CENTER)
        s.set_line_wrap(True)
        s.set_max_width_chars(54)
        # max_width_chars only caps the NATURAL width; a box child defaults to
        # halign FILL and would be stretched to the whole window, wrapping this
        # into one 1300px line. Centring it makes the cap actually bind.
        s.set_halign(Gtk.Align.CENTER)
        s.get_style_context().add_class("emptysub")
        box.pack_start(s, False, False, 0)
        return box

    def _notice(self, title, body):
        n = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        n.get_style_context().add_class("emunotice")
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("noticetitle")
        n.pack_start(t, False, False, 0)
        b = Gtk.Label(label=body, xalign=0)
        b.set_line_wrap(True)
        b.set_max_width_chars(60)
        b.get_style_context().add_class("noticebody")
        n.pack_start(b, False, False, 0)
        return n

    # ================= controllers =================
    def _controller_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("ctrlbar")
        ico = nbicons.image("gamepad", 16, MUTED)
        ico.set_valign(Gtk.Align.CENTER)
        bar.pack_start(ico, False, False, 0)
        self._ctrl_label = Gtk.Label(label="", xalign=0)
        self._ctrl_label.get_style_context().add_class("ctrllabel")
        self._ctrl_label.set_ellipsize(Pango.EllipsizeMode.END)
        bar.pack_start(self._ctrl_label, True, True, 0)
        rb = Gtk.Button(label=_t("Rescan"))
        rb.set_relief(Gtk.ReliefStyle.NONE)
        rb.get_style_context().add_class("emutoggle")
        rb.connect("clicked", lambda *_: (self._scan_roms(),
                   self._render_library(), self._render_controllers()))
        bar.pack_end(rb, False, False, 0)
        return bar

    # Devices that carry absolute axes but are NOT game controllers.
    _NOT_PAD = ("keyboard", "mouse", "touchpad", "trackpad", "tablet",
                "consumer control", "system control", "video bus",
                "power button", "sleep button", "pc speaker", "webcam",
                "wmi hotkeys")
    # Names that positively identify a game controller.
    # Only words that describe what the device IS. Brand names are deliberately
    # NOT here: "logitech" used to be, and it matched the Logitech M705 *mouse*,
    # which the app then announced as a ready game controller.
    _PAD_HINT = ("gamepad", "game pad", "controller", "joystick", "joypad",
                 "dualshock", "dualsense", "rumblepad", "gamecube")

    # The button codes the kernel assigns to a device it has classified as a
    # pad. A mouse reports BTN_MOUSE (0x110) and a keyboard reports ordinary
    # keycodes, so testing for these is what actually separates a controller
    # from every other USB thing plugged into the machine.
    _BTN_JOYSTICK = 0x120
    _BTN_GAMEPAD = 0x130

    @staticmethod
    def _bit_set(mask, bit):
        """Is `bit` set in a /proc/bus/input/devices bitmask?

        The kernel prints these as space-separated hex words, most significant
        word first, the last word holding bits 0..63 (BITS_PER_LONG on the
        64-bit kernel this OS ships)."""
        words = mask.split()
        idx = bit // 64
        if not words or idx >= len(words):
            return False
        try:
            word = int(words[len(words) - 1 - idx], 16)
        except ValueError:
            return False
        return bool((word >> (bit % 64)) & 1)

    def _detect_controllers(self):
        """Names of connected game controllers, read from
        /proc/bus/input/devices. This kernel exposes gamepads through evdev
        (/dev/input/event*) via the generic-HID driver — which is exactly what
        vbam's SDL layer binds.

        A device counts as a pad when it has an evdev node and the kernel gave
        it joystick/gamepad buttons — or, as a fallback for an oddly-described
        pad, when its own name says it is one. Mice, keyboards and the rest are
        excluded FIRST and unconditionally: a name hint must never be able to
        promote a pointer, which is how a Logitech mouse and a USB microphone
        both ended up announced as ready controllers."""
        pads = []
        try:
            with open("/proc/bus/input/devices") as fh:
                blob = fh.read()
        except Exception:
            return pads
        for block in blob.split("\n\n"):
            if not block.strip():
                continue
            name, handlers, keymask = "", "", ""
            for line in block.splitlines():
                if line.startswith("N: Name="):
                    name = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("H: Handlers="):
                    handlers = line.split("Handlers=", 1)[1]
                elif line.startswith("B: KEY="):
                    keymask = line.split("KEY=", 1)[1].strip()
            if not name or "event" not in handlers:
                continue
            low = name.lower()
            # a pointer is never a game controller, whatever brand made it
            if "mouse" in handlers or any(x in low for x in self._NOT_PAD):
                continue
            if not (self._bit_set(keymask, self._BTN_GAMEPAD)
                    or self._bit_set(keymask, self._BTN_JOYSTICK)
                    or any(h in low for h in self._PAD_HINT)):
                continue
            pads.append(name)
        return pads

    def _render_controllers(self):
        pads = self._detect_controllers()
        if pads:
            txt = "Controller ready: " + ", ".join(pads[:2])
            if len(pads) > 2:
                txt += " +%d more" % (len(pads) - 2)
        else:
            txt = "No controller detected. Keyboard: arrow keys, Z, X."
        self._ctrl_label.set_text(txt)

    # ================= launch =================
    def _vbam_path(self):
        try:
            return shutil.which("vbam") or (
                "/usr/bin/vbam" if os.path.exists("/usr/bin/vbam") else None)
        except Exception:
            return None

    def _log_path(self):
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
        except OSError:
            pass
        return os.path.join(CFG_DIR, "vbam.log")

    def _play(self, rompath):
        if self._closed:
            return   # the window is gone; there is nothing to flash it against
        vbam = self._vbam_path()
        if not vbam:
            self._flash("The emulator core isn’t installed.")
            return
        if not (rompath and os.path.isfile(rompath)):
            self._alert(_t("That game is no longer there"),
                        _t("The file has been moved or deleted since the "
                           "library was last read. Choose Look for New Games "
                           "in the File menu to bring the list up to date."))
            return
        # A file that cannot be a cartridge is refused HERE, with a sentence
        # about the file, rather than launched and reported afterwards as "the
        # game closed right away — see the emulator log".
        why = rom_problem(rompath)
        if why:
            self._alert(_t("%s cannot be played") % os.path.basename(rompath),
                        why)
            return
        if self._session is not None:       # a game is already running
            return
        self._launch_time = time.monotonic()
        self._flash("Playing %s — press Ctrl+Esc to exit."
                    % os.path.basename(rompath))
        # Run the game inside a fullscreen "stage": a raw vbam window is unmapped
        # by the single-app WM, so nbgame reparents vbam into a fullscreen app
        # window (which also puts the desktop menu bar behind the game), and
        # wires a global Ctrl+Esc to quit. See de/nbgame.py.
        try:
            self._session = nbgame.GameSession(self, vbam, rompath,
                                               self._on_game_end)
            self._session.run()
        except Exception:
            self._session = None
            self._flash("Couldn’t start the emulator — see File ▸ Emulator Log.")
            try:                       # make the failure diagnosable on-device
                import traceback
                with open(self._log_path(), "a") as fh:
                    fh.write("\n[gbaemu] game session failed:\n")
                    fh.write(traceback.format_exc())
            except Exception:
                pass

    def _on_game_end(self):
        self._session = None
        if self._closed:
            # The launcher went away while the game was running (teardown ends
            # the session itself): there is no status line to write and no
            # window to raise.
            return
        # A game that dies within ~2s never really started — point at the log.
        quick = (time.monotonic() - self._launch_time) < 2.0
        self._flash("The game closed right away — see File ▸ Emulator Log."
                    if quick else "")
        try:
            self.present()              # bring the launcher back to the front
        except Exception:
            pass

    def _flash(self, text):
        try:
            self._ctrl_label.set_text(text)
        except Exception:
            pass

    def _alert(self, heading, body):
        """Say why something did not happen, in a card the reader cannot miss.

        The status line at the foot of the window ellipsizes to one line, so a
        two-sentence explanation of a broken file was cut off mid-word there —
        the one place it had to be readable."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("emualert")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.pack_start(nbicons.image("cartridge", 24, MUTED), False, False, 0)
        ht = Gtk.Label(label=heading, xalign=0)
        ht.set_line_wrap(True)
        ht.set_max_width_chars(34)
        ht.get_style_context().add_class("alerttitle")
        head.pack_start(ht, True, True, 0)
        box.pack_start(head, False, False, 0)
        msg = Gtk.Label(label=body, xalign=0)
        msg.set_line_wrap(True)
        msg.set_width_chars(40)
        msg.set_max_width_chars(44)
        msg.get_style_context().add_class("alertbody")
        box.pack_start(msg, False, False, 0)
        ok = Gtk.Button(label=_t("Done"))
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("emubtn")
        ok.set_halign(Gtk.Align.END)
        ok.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        box.pack_start(ok, False, False, 0)
        dlg.get_content_area().add(box)
        try:                     # the card carries its own button
            area = dlg.get_action_area()
            area.set_no_show_all(True)
            area.hide()
        except Exception:
            pass
        # Esc must dismiss it: there is no title bar to close.
        dlg.connect("key-press-event",
                    lambda _w, e: (dlg.response(Gtk.ResponseType.CANCEL) or True)
                    if e.keyval == Gdk.KEY_Escape else False)
        dlg.show_all()
        ok.grab_focus()
        dlg.run()
        dlg.destroy()

    def _open_rom(self):
        start = HOME
        for d in ("Documents", "Desktop"):
            p = os.path.join(HOME, d)
            if os.path.isdir(p):
                start = p
                break
        patterns = tuple("*" + e for e in sorted(ROM_EXT)) + ("*.zip",)
        path = nbpicker.open_file(self, title="Open Game", start_dir=start,
                                  patterns=patterns)
        if path and os.path.isfile(path):
            if _is_rom(path):
                # bring a newly-opened ROM into the library, then play it
                if not any(m["path"] == path for m in self._roms):
                    self._scan_roms()
                    self._render_library()
                self._play(path)
            else:
                self._play(path)     # e.g. a .zip — let vbam try

    def _on_destroy(self, *_):
        # Idempotent, and the gate is raised FIRST: "destroy" can reach this
        # handler more than once (File ▸ Close on an already-closing window, a
        # second teardown pass at Shut Down), and the session teardown and the
        # final write below must each happen exactly once. Marking closed first
        # also means a launch idle GLib had already dispatched, and the session
        # end this teardown itself provokes, both find a dead window and touch
        # no widgets.
        if self._closed:
            return False
        self._closed = True

        # Clear the id before removing the source, so a failed removal still
        # leaves nothing armed to fire against a destroyed widget tree.
        sid = self._launch_source
        self._launch_source = 0
        if sid:
            try:
                GLib.source_remove(sid)
            except Exception:
                pass

        # Tear down a running game so it can't orphan a stage/vbam over the desktop.
        if self._session is not None:
            try:
                self._session.stop()
                self._session._finish()
            except Exception:
                pass
            self._session = None
        return False

    # ================= menu =================
    def menu_items(self, name):
        if name == "File":
            return [
                ("Open Game…", lambda: self._open_rom()),
                # Names the outcome — find games added since this window opened,
                # e.g. one just exported from the GBA SDK — rather than the
                # machine's word for how it looks ("Rescan").
                ("Look for New Games", lambda: (self._scan_roms(),
                 self._render_library(), self._render_controllers())),
                ("Emulator Log…", self._show_log),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        return super().menu_items(name)

    def _show_log(self):
        try:
            with open(self._log_path()) as fh:
                text = fh.read().strip()
        except OSError:
            text = ""
        if not text:
            text = "The log is empty."
        dlg = Gtk.Dialog(title="Emulator Log", transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.set_default_size(560, 380)
        box = dlg.get_content_area()
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(True)
        tv.get_buffer().set_text(text)
        sw.add(tv)
        box.pack_start(sw, True, True, 0)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    # ================= css =================
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .emuhead { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                   padding: 16px 22px; }
        .emutitle { font-size: 17px; font-weight: 600; color: #1A1916; }
        .emusub { font-size: 12px; color: #9A9484; letter-spacing: 0.03em; }
        .emubtn { min-height: 30px; padding: 0 14px; border: 1px solid #C9C4B6;
                  background: #FCFBF8; border-radius: 8px; box-shadow: none;
                  font-size: 12px; font-weight: 600; color: #1A1916; }
        .emubtn:hover { background: #F4F2EC; }
        .emutoggle { padding: 6px 12px; border: 1px solid #C9C4B6;
                     background: #FCFBF8; border-radius: 8px; box-shadow: none;
                     font-size: 12px; color: #1A1916; }
        .emutoggle:hover { background: #F4F2EC; }
        .emutoggle:checked { background: #EAE3D2; border-color: #B3AD9E; }
        .libscroll, .libscroll viewport { background: #FCFBF8; }
        .libflow { padding: 24px; }
        /* The ROM card's button is invisible BY DESIGN: it exists to be
           focusable and activatable, not to be seen. Every property here
           removes something the theme's button would otherwise add -- the
           theme gives a button 5px 14px of padding, a hairline border and a
           radius, which would have grown and re-boxed every card in the
           library. Nothing sets `outline`, so the global focus ring still
           draws on the card when it is tabbed to. The hover feedback stays
           where it always was, on the art tile. */
        .rombutton { padding: 0; margin: 0; border: none; border-radius: 0;
                     background: transparent; background-image: none;
                     box-shadow: none; min-width: 0; min-height: 0; }
        .rombutton:hover, .rombutton:active, .rombutton:checked {
                     background: transparent; background-image: none;
                     box-shadow: none; }
        .romcard { padding: 4px; }
        .romart { background: #F1EEE6; border: 1px solid #C9C4B6;
                  border-radius: 4px; }
        .romcard:hover .romart { background: #F4F2EC; border-color: #C8341E; }
        .rombutton:hover .romart { background: #F4F2EC;
                                   border-color: #C8341E; }
        .romname { font-size: 13px; color: #1A1916; font-weight: 600; }
        .romsys { font-size: 11px; color: #9A9484; }
        .emptytitle { font-size: 15px; font-weight: 600; color: #6E695E; }
        .emptysub { font-size: 13px; color: #9A9484; }
        .emunotice { margin: 20px 24px 0; padding: 12px 16px;
                     background: #F4F2EC; border: 1px solid #C9C4B6;
                     border-radius: 12px; }
        .noticetitle { font-size: 13px; font-weight: 600; color: #B12D19; }
        .noticebody { font-size: 12px; color: #6E695E; }
        .ctrlbar { background: #F1EEE6; border-top: 1px solid #C9C4B6;
                   padding: 10px 22px; }
        .ctrllabel { font-size: 12px; color: #6E695E; }
        .emualert { background: #FCFBF8; border: 1px solid #C9C4B6;
                    padding: 22px 26px 16px; }
        .alerttitle { font-size: 16px; font-weight: 700; color: #1A1916; }
        .alertbody { font-size: 13px; color: #6E695E; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass


if __name__ == "__main__":
    nbapp.run(GbaEmu)
