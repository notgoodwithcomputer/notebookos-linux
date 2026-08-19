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
import hashlib
import shutil
import subprocess
import tempfile

import nbapp
import nbpicker
import nbicons
import nbgame
import nbjobs
import nbi18n
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
# CFG_DIR is still where the vbam log lives (_log_path). There is no
# gbaemu.json any more: it held only `fullscreen` and `scale`, neither of which
# could act on anything.
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_PATH = os.path.join(CFG_DIR, "gbaemu.json")
STATE_SLOTS = (1, 2, 3)

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
# The library grid is built from one tile size, so the same games make the
# same grid in every language. 200px puts four cartridges on the 1024px panel
# (4x200 + 3x16 of column spacing + 2x24 of flow padding = 896), and the row
# only re-flows when the window itself changes -- not when a translation is
# longer than the English it replaced.
CARD_WIDTH = 200
MAX_ROMS = 600
MAX_LIBRARY_ROM_BYTES = 64 * 1024 * 1024
MAX_EMULATOR_LOG_VIEW = 1024 * 1024
GAME_DATA_DIR = os.path.join(HOME, ".local", "share", "notebook", "gbaemu")
IDENTITY_CACHE = os.path.join(GAME_DATA_DIR, "rom-identities.json")


def _read_log_tail(path, limit=MAX_EMULATOR_LOG_VIEW):
    """Read only the useful recent end of an emulator-owned diagnostic log."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - limit), os.SEEK_SET)
        return fh.read(limit).decode("utf-8", "replace").strip()
_identity_cache = None


def _saved_text(stamp):
    """When a slot was last written, phrased the way the rest of the OS phrases
    dates ("15 Aug 2026, 14:05" -- finder.py uses the same call), so nbi18n's
    date rule can translate the month and reorder it per language. The
    "%Y-%m-%d %H:%M" stamp this replaced was a machine's way of writing a date
    and, being all digits, unreachable by that rule in all 17 languages."""
    if stamp is None:
        return _t("Not saved")
    return _t(time.strftime("%d %b %Y, %H:%M", time.localtime(stamp)))


def _fit_caption(label):
    """Keep a card's caption inside the tile: wrap onto a second line rather
    than push the column wider. Ellipsis is wrong here -- both captions end in
    the part that matters (which F-key, which date) -- and a wrapped card is
    only taller, which the homogeneous grid absorbs for the whole row."""
    label.set_line_wrap(True)
    label.set_max_width_chars(30)
    label.set_justify(Gtk.Justification.CENTER)


def _same_game_key(path):
    """game_key(path), or None for a row whose file cannot be read now. Used
    to find the OTHER cards backed by one record, where a raising sibling must
    not stop the card the person actually clicked from being restated."""
    try:
        return game_key(path)
    except (OSError, ValueError):
        return None


def _is_rom(path):
    return os.path.splitext(path)[1].lower() in ROM_EXT


def game_key(path):
    """Stable per-game key based on cartridge bytes, not its current name."""
    global _identity_cache
    real = os.path.realpath(os.path.abspath(path))
    try:
        st = os.stat(real)
        token = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
    except OSError:
        # Missing library rows are never launched; retain a deterministic key
        # so rendering their stale metadata still cannot raise.
        return "path:" + real
    if _identity_cache is None:
        try:
            with open(IDENTITY_CACHE, encoding="utf-8") as fh:
                raw = json.load(fh)
            _identity_cache = raw if isinstance(raw, dict) else {}
        except Exception:
            _identity_cache = {}
    rec = _identity_cache.get(real)
    if isinstance(rec, dict) and rec.get("stat") == token:
        digest = rec.get("sha256")
        if isinstance(digest, str) and len(digest) == 64:
            return "sha256:" + digest
    # A rename on the same filesystem keeps device/inode. Reuse that cached
    # digest without rereading a large cartridge, then remember its new path.
    digest = None
    for old in _identity_cache.values():
        if isinstance(old, dict) and old.get("stat") == token:
            candidate = old.get("sha256")
            if isinstance(candidate, str) and len(candidate) == 64:
                digest = candidate
                break
    if digest is None:
        h = hashlib.sha256()
        with open(real, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
    _identity_cache[real] = {"stat": token, "sha256": digest}
    try:
        os.makedirs(GAME_DATA_DIR, exist_ok=True)
        nbapp.atomic_write_json(IDENTITY_CACHE, _identity_cache)
    except Exception:
        pass
    return "sha256:" + digest


def state_path(path, slot):
    """State name used by SDL.cpp:sdlStateName in durable user storage."""
    if slot not in STATE_SLOTS:
        raise ValueError("save-state slot must be 1, 2, or 3")
    return os.path.join(game_storage_dir(path),
                        os.path.basename(path) + str(slot) + ".sgm")


def game_storage_dir(path):
    identity = game_key(path).encode("utf-8", errors="surrogateescape")
    return os.path.join(GAME_DATA_DIR, hashlib.sha256(identity).hexdigest()[:20])


def _legacy_storage_dir(path):
    identity = os.path.realpath(os.path.abspath(path)).encode(
        "utf-8", errors="surrogateescape")
    return os.path.join(GAME_DATA_DIR, hashlib.sha256(identity).hexdigest()[:20])


def prepare_game_storage(path):
    """Create writable save/battery storage and preserve legacy sidecars."""
    dest = game_storage_dir(path)
    os.makedirs(dest, exist_ok=True)
    old_dest = _legacy_storage_dir(path)
    if old_dest != dest and os.path.isdir(old_dest):
        for name in os.listdir(old_dest):
            old = os.path.join(old_dest, name)
            new = os.path.join(dest, name)
            if os.path.isfile(old) and not os.path.exists(new):
                try:
                    shutil.copy2(old, new)
                except OSError:
                    pass
    base = os.path.basename(path)
    legacy = [base + str(slot) + ".sgm" for slot in STATE_SLOTS]
    legacy.append(base + ".sav")
    for name in legacy:
        old = os.path.join(os.path.dirname(path), name)
        new = os.path.join(dest, name)
        if os.path.isfile(old) and not os.path.exists(new):
            try:
                shutil.copy2(old, new)
            except OSError:
                pass
    # VBA-M names files from the ROM basename even when --save-dir is stable.
    # After a Finder rename, carry the one prior basename forward so the core
    # opens the same battery/slot bytes under the new requested name.
    for suffix in (["%d.sgm" % slot for slot in STATE_SLOTS] + [".sav"]):
        new = os.path.join(dest, base + suffix)
        if os.path.exists(new):
            continue
        candidates = [os.path.join(dest, n) for n in os.listdir(dest)
                      if n.endswith(suffix) and os.path.isfile(
                          os.path.join(dest, n))]
        if len(candidates) == 1:
            try:
                shutil.copy2(candidates[0], new)
            except OSError:
                pass
    return dest


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
        self._scan_source = 0           # the pending first library scan idle
        self._launch_source = 0         # the pending command-line launch idle
        self._jobs = nbjobs.JobOwner(name="gbaemu")

        # This file is metadata, not emulator configuration: vbam owns the
        # binary .sgm files and its SDL frontend owns the F-key bindings.
        self._state_meta = self._load_state_meta()
        self._roms = []                 # [{path, name, system, ext}]
        self._launch_time = 0.0
        self._session = None            # the running nbgame.GameSession, if any
        self._active_rom = None
        self._flashed = False           # a message about what just happened
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

        # First paint beats first scan: even the bounded walk belongs after
        # the window is on screen, not between construct and map.
        self._scan_source = GLib.idle_add(self._first_scan)

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

        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)

    def _first_scan(self):
        """Populate the library once, unless its window has already closed."""
        self._scan_source = 0
        if self._closed:
            return False
        self._request_scan()
        return False

    def _request_scan(self):
        """Discover and hash cartridges off the GTK thread, then redraw."""
        def work(job):
            return self._scan_roms(job.token, apply=False)

        def done(found):
            self._apply_scan(found)
            self._render_library()
            self._render_controllers()

        self._jobs.start("scan", work, on_done=done)

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
    def _scan_roms(self, token=None, apply=True):
        """Scan Home (recursively; hidden dirs skipped) for real ROM files.

        BOUNDED: the walk itself is capped by directories visited and by
        wall time, because MAX_ROMS only capped what was FOUND — on a home
        with a big mounted tree and few ROMs the old walk traversed
        everything, on the GTK thread, and the app hung at open (audit
        #10). Look for New Games reruns the same bounded scan."""
        found = []
        seen = set()
        deadline = time.monotonic() + 2.5
        dirs_left = 4000
        try:
            for root, dirs, files in os.walk(HOME):
                if token is not None:
                    token.checkpoint()
                dirs_left -= 1
                if dirs_left <= 0 or time.monotonic() > deadline:
                    raise StopIteration
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in ROM_EXT:
                        continue
                    p = os.path.join(root, f)
                    try:
                        if os.path.getsize(p) > MAX_LIBRARY_ROM_BYTES:
                            continue
                    except OSError:
                        continue
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
        if not apply:
            # Populate the content-identity cache in the worker. Reconciliation
            # on the GTK thread below then does no cartridge-sized I/O.
            for item in found:
                if token is not None:
                    token.checkpoint()
                try:
                    game_key(item["path"])
                except (OSError, ValueError):
                    pass
            return found
        self._apply_scan(found)
        return found

    def _apply_scan(self, found):
        """Publish a completed scan and reconcile its small metadata."""
        self._roms = found
        changed = False
        for item in found:
            changed = self._reconcile_states(item["path"], save=False) or changed
        if changed:
            self._save_state_meta()

    # ================= save-state metadata =================
    def _load_state_meta(self):
        self._meta_quarantine_pending = False
        try:
            with open(CFG_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                nbapp.quarantine_unrecognized(CFG_PATH)
                self._meta_quarantine_pending = os.path.exists(CFG_PATH)
                data = {}
        except (ValueError, UnicodeDecodeError):
            nbapp.preserve_damaged(CFG_PATH)
            self._meta_quarantine_pending = os.path.exists(CFG_PATH)
            data = {}
        except (OSError, TypeError):
            data = {}
        games = data.get("games")
        if not isinstance(games, dict):
            games = {}
        # A newer emulator may add root metadata beside `games`. This version
        # does not need to understand it to keep it: rebuilding only the two
        # known keys erased those fields on the first slot selection or close.
        out = dict(data)
        out.update({"version": 1, "games": games})
        return out

    def _save_state_meta(self):
        try:
            if getattr(self, "_meta_quarantine_pending", False):
                nbapp.quarantine_unrecognized(CFG_PATH)
                if os.path.exists(CFG_PATH):
                    raise OSError("could not preserve damaged emulator metadata")
                self._meta_quarantine_pending = False
            os.makedirs(CFG_DIR, exist_ok=True)
            nbapp.atomic_write_json(CFG_PATH, self._state_meta)
            return True
        except Exception as exc:                                  # noqa: BLE001
            nbapp.note_save_failure(self, exc, CFG_PATH)
            return False

    def _game_state(self, path):
        games = self._state_meta["games"]
        key = game_key(path)
        rec = games.get(key)
        legacy_key = os.path.realpath(os.path.abspath(path))
        if not isinstance(rec, dict) and isinstance(games.get(legacy_key), dict):
            rec = games.pop(legacy_key)
            games[key] = rec
        if not isinstance(rec, dict):
            # Damage in one ROM's metadata must not prevent the whole library
            # from rendering or discard healthy records belonging to siblings.
            rec = {}
            games[key] = rec
        slot = rec.get("last_slot", 1)
        rec["last_slot"] = slot if slot in STATE_SLOTS else 1
        if not isinstance(rec.get("last_saved"), dict):
            rec["last_saved"] = {}
        return rec

    def _select_slot(self, path, slot):
        if slot not in STATE_SLOTS:
            return
        rec = self._game_state(path)
        previous = rec["last_slot"]
        rec["last_slot"] = slot
        if not self._save_state_meta():
            rec["last_slot"] = previous
        # ONE record can be behind SEVERAL cards: the key is the cartridge's
        # bytes, so the same game in Documents and on the Desktop shares its
        # slot deliberately. Restating only the clicked card left the sibling
        # showing the old slot -- and then changing to the new one at the next
        # redraw, with nobody having touched it. Restate them together.
        # (game_key is cached by dev/ino/size/mtime: no cartridge is re-read.)
        key = _same_game_key(path)
        for other in [path] + [p for p in getattr(self, "_slot_widgets", {})
                               if p != path and key is not None
                               and _same_game_key(p) == key]:
            self._update_slot_widgets(other)

    def _update_slot_widgets(self, path):
        """Refresh one card's slot chrome without rebuilding the library."""
        widgets = getattr(self, "_slot_widgets", {}).get(path)
        if not widgets:
            return
        rec = self._game_state(path)
        selected = rec["last_slot"]
        # The slots are a radio group; lighting one deactivates its sibling
        # with set_active(FALSE), which emits "clicked"/"toggled" on the
        # sibling too. Restating the row from inside _select_slot therefore
        # re-entered _select_slot for the slot being unlit, which relit it, …
        # until the stack blew (every slot click printed a RecursionError).
        # choose_segment restates the row with every handler blocked.
        nbapp.choose_segment(widgets["buttons"].items(), selected, None)
        widgets["keys"].set_text(
            (_t("Load F%d") % selected) + "  ·  "
            + (_t("Save Shift+F%d") % selected))
        widgets["last"].set_text(
            _t("Last saved: %s") % _saved_text(rec["last_saved"].get(
                str(selected))))

    def _reconcile_states(self, path, save=True):
        rec = self._game_state(path)
        changed = False
        for slot in STATE_SLOTS:
            try:
                stamp = os.path.getmtime(state_path(path, slot))
            except OSError:
                continue
            key = str(slot)
            if rec["last_saved"].get(key) != stamp:
                rec["last_saved"][key] = stamp
                changed = True
        if changed and save:
            self._save_state_meta()
        return changed

    def _render_library(self):
        self._slot_widgets = {}
        for c in self._lib_body.get_children():
            self._lib_body.remove(c)
        if not self._vbam_path():
            # pack tight: as an expanding child this warning stretched into a
            # tall pink slab with two lines of text floating at the top of it.
            #
            # Both sentences go through _t(). The body used to be a bare
            # literal with no catalog entry in ANY of the 17 languages, so a
            # French library showed a French heading over an English sentence;
            # it now says what _play() already flashes when the core is
            # missing, which every catalog carries.
            self._lib_body.pack_start(self._notice(
                _t("Games can’t be played on this system"),
                _t("The emulator core isn’t installed.")),
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
        # fill=True, or GtkBox CENTRES the flow in the height this expanding
        # body has left: a library of one to four games hung ~103px lower than
        # the first row of a library that filled the window, with a blank band
        # under the notice. (valign START above is not enough -- the centring
        # is the box's, in the child's allocation, not the widget's own.)
        self._lib_body.pack_start(flow, False, True, 0)
        self._lib_body.show_all()

    def _rom_card(self, m):
        # TWO activatable regions, never nested. The launch button wraps the
        # artwork and titles ONLY; the slot buttons are its SIBLINGS below.
        #
        # They were inside it, and a GtkButton containing GtkToggleButtons is
        # a broken control: picking a save slot could activate the outer
        # button and launch the game instead, and keyboard focus inside an
        # activatable parent behaves differently again. That is the shape of
        # "the emulator breaks sometimes" -- it only misfires when the pointer
        # or focus lands on the inner control, so it looks intermittent.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.get_style_context().add_class("romcard")
        # The tile has a width of its own. Without it the FlowBox sized every
        # column by the widest card, which is the widest LABEL in it, so the
        # grid changed shape with the language: four games per row in English,
        # three in Spanish, two in French. Everything below is kept inside this
        # width (the numbered slot chips, the two captions capped and wrapping)
        # so a longer translation makes a taller card, never a wider grid.
        outer.set_size_request(CARD_WIDTH, -1)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        art = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        art.get_style_context().add_class("romart")
        art.set_size_request(120, 120)
        img = nbicons.image("cartridge", 52, "#6E695E")
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        img.set_vexpand(True)
        art.pack_start(img, True, True, 0)
        card.pack_start(art, False, False, 0)
        # The ROM's filename with its extension stripped. A game exported
        # from the SDK with the default project name lands here as "Game",
        # which is a catalog key.
        nm = Gtk.Label()
        nbi18n.set_verbatim(nm, m["name"])
        nm.get_style_context().add_class("romname")
        nm.set_ellipsize(Pango.EllipsizeMode.END)
        nm.set_max_width_chars(15)
        nm.set_justify(Gtk.Justification.CENTER)
        card.pack_start(nm, False, False, 0)
        sysl = Gtk.Label(label=m["system"])
        sysl.get_style_context().add_class("romsys")
        card.pack_start(sysl, False, False, 0)
        rec = self._game_state(m["path"])
        selected = rec["last_slot"]
        slots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        slots.set_halign(Gtk.Align.CENTER)
        slot_buttons = {}
        slot_group = None
        for slot in STATE_SLOTS:
            # The chip shows the NUMBER and names itself "Slot 2" to the
            # pointer and to assistive technology. Spelled out three times
            # across the card, the word was the widest thing in the library
            # and sized every column by its longest translation -- three
            # "Emplacement 1" chips are 333px, and French cards came out
            # 462px wide with two games per row where English had four.
            sb = Gtk.RadioButton.new_with_label_from_widget(
                slot_group, "%d" % slot)
            # Drawn as a chip, not as a radio: the bordered box lit by
            # .stateslot:checked already says which slot is chosen, and the
            # indicator's filled dot would state it a second time in the
            # blackest ink on a card whose game name should carry that
            # weight. set_mode(False) is only how the group LOOKS -- one
            # slot at a time, arrow keys between them, all still the radio's.
            sb.set_mode(False)
            sb.set_tooltip_text(_t("Slot %d") % slot)
            try:
                sb.get_accessible().set_name(_t("Slot %d") % slot)
            except Exception:                                     # noqa: BLE001
                pass
            if slot_group is None:
                slot_group = sb
            sb.set_active(slot == selected)
            sb.set_relief(Gtk.ReliefStyle.NONE)
            sb.get_style_context().add_class("stateslot")
            # "toggled" and only when this slot became ACTIVE: a radio
            # sibling being unlit is not a choice (see _update_slot_widgets)
            sb.connect("toggled", lambda w, p=m["path"], s=slot:
                       w.get_active() and self._select_slot(p, s))
            slots.pack_start(sb, False, False, 0)
            slot_buttons[slot] = sb
        outer.pack_start(slots, False, False, 0)
        keys = Gtk.Label(label=(_t("Load F%d") % selected) + "  ·  " +
                         (_t("Save Shift+F%d") % selected))
        keys.get_style_context().add_class("statekeys")
        _fit_caption(keys)
        outer.pack_start(keys, False, False, 0)
        last = Gtk.Label(label=_t("Last saved: %s") % _saved_text(
            rec["last_saved"].get(str(selected))))
        last.get_style_context().add_class("statetime")
        _fit_caption(last)
        outer.pack_start(last, False, False, 0)
        self._slot_widgets[m["path"]] = {
            "buttons": slot_buttons, "keys": keys, "last": last}
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
        outer.pack_start(button, False, False, 0)
        outer.reorder_child(button, 0)     # artwork above the slot controls
        return outer

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
        # The SAME name as the File menu item that runs the same callback.
        # It said "Rescan" here and "Look for New Games" there, so one window
        # offered one action under two names -- and the card raised when a
        # game has been deleted sends the reader to the menu while the button
        # sat in the same window under the other word. The menu's wording is
        # the one that keeps: an action names its outcome, not the machine's
        # word for how it looks (docs/MENU-CONVENTIONS.md section 6).
        rb = Gtk.Button(label=_t("Look for New Games"))
        rb.set_relief(Gtk.ReliefStyle.NONE)
        rb.get_style_context().add_class("emutoggle")
        rb.connect("clicked", lambda *_: self._request_scan())
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

    def _render_controllers(self, force=False):
        """Write the status line's RESTING text: which controllers are here.

        It must not overwrite a message about what the person just did. Opening
        a game from the browser starts a library scan and then plays it, and
        the scan finishing ~0.1s later called this and wiped "Playing Zelda —
        press Ctrl+Esc to exit" (and, when the launch had failed, wiped the
        only sentence that said so, after one frame)."""
        pads = self._detect_controllers()
        if pads:
            txt = "Controller ready: " + ", ".join(pads[:2])
            if len(pads) > 2:
                txt += " +%d more" % (len(pads) - 2)
        else:
            txt = "No controller detected. Keyboard: arrow keys, Z, X."
        if getattr(self, "_flashed", False) and not force:
            return
        try:
            self._ctrl_label.set_text(txt)
        except Exception:                                         # noqa: BLE001
            pass

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
        try:
            save_dir = prepare_game_storage(rompath)
        except OSError as exc:
            self._flash(nbapp.save_failure_reason(exc, GAME_DATA_DIR))
            return
        self._launch_time = time.monotonic()
        self._active_rom = rompath
        self._flash("Playing %s — press Ctrl+Esc to exit."
                    % os.path.basename(rompath))
        # Run the game inside a fullscreen "stage": a raw vbam window is unmapped
        # by the single-app WM, so nbgame reparents vbam into a fullscreen app
        # window (which also puts the desktop menu bar behind the game), and
        # wires a global Ctrl+Esc to quit. See de/nbgame.py.
        try:
            self._session = nbgame.GameSession(
                self, vbam, rompath, self._on_game_end,
                extra_args=["--save-dir", save_dir,
                            "--battery-dir", save_dir])
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
        # Lifecycle tests and emergency teardown may exercise this callback on
        # a deliberately skeletal instance that never reached __init__.
        active_rom = getattr(self, "_active_rom", None)
        self._active_rom = None
        if active_rom:
            self._reconcile_states(active_rom)
            self._render_library()
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
        """Say what just happened, over the resting controller line, until
        something newer replaces it. An EMPTY flash is not an empty status
        line: it returns the line to what it rests on."""
        self._flashed = bool(text)
        if not text:
            self._render_controllers(force=True)
            return
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
                    self._request_scan()
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
        jobs = getattr(self, "_jobs", None)
        if jobs is not None:
            jobs.close()

        # Clear the id before removing the source, so a failed removal still
        # leaves nothing armed to fire against a destroyed widget tree.
        scan_sid = self._scan_source
        self._scan_source = 0
        if scan_sid:
            try:
                GLib.source_remove(scan_sid)
            except Exception:
                pass
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
    def close(self, *_args):
        # Menu/Escape calls this override; the window manager emits
        # delete-event. Keep both on the same active-game decision.
        if self._on_delete():
            return
        self.destroy()

    def _on_delete(self, *_args):
        """Veto every close path until active-game loss is confirmed."""
        return bool(self._session is not None
                    and not self._confirm_stop_game())

    def _confirm_stop_game(self):
        """An interactive close must not silently discard game progress."""
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=_t("Stop"))
        # The name of the game that is running, which this window knows.
        # It used to substitute the literal word "Game", so a destructive
        # prompt read End “Game”? -- which reads as a substitution that
        # failed. _t("Game") stays as the fallback for the one case where
        # there is no name to give.
        running = getattr(self, "_active_rom", None)
        dlg.format_secondary_text(
            _t("End “%s”? Anything it has not saved will be lost.")
            % (os.path.basename(running) if running else _t("Game")))
        dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        stop = dlg.add_button(_t("Stop"), Gtk.ResponseType.OK)
        stop.get_style_context().add_class("destructive-action")
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        answer = dlg.run()
        dlg.destroy()
        return answer == Gtk.ResponseType.OK

    def menu_items(self, name):
        if name == "File":
            return [
                ("Open Game…", lambda: self._open_rom()),
                # Names the outcome — find games added since this window opened,
                # e.g. one just exported from the GBA SDK — rather than the
                # machine's word for how it looks ("Rescan").
                ("Look for New Games", self._request_scan),
                ("Emulator Log…", self._show_log),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        return super().menu_items(name)

    def _show_log(self):
        try:
            text = _read_log_tail(self._log_path())
        except OSError:
            text = ""
        if not text:
            # Through a TextView buffer, which nbi18n's automatic translation
            # never sees (it hooks labels, buttons and menu items), so this
            # sentence has to be looked up here. Every catalog carries it.
            text = _t("The log is empty.")
        # The same card as _alert: this window has no title bar to name it, so
        # a bare panel put the log text against the very top-left corner of a
        # box with no heading, no padding and a raw theme button.
        dlg = Gtk.Dialog(title=_t("Emulator Log"), transient_for=self,
                         modal=True)
        dlg.set_decorated(False)
        dlg.set_default_size(560, 380)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("emualert")
        head = Gtk.Label(label=_t("Emulator Log"), xalign=0)
        head.get_style_context().add_class("alerttitle")
        box.pack_start(head, False, False, 0)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        sw.get_style_context().add_class("logview")
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(True)
        tv.get_style_context().add_class("logtext")
        tv.set_left_margin(10)
        tv.set_right_margin(10)
        tv.set_top_margin(8)
        tv.set_bottom_margin(8)
        tv.get_buffer().set_text(text)
        sw.add(tv)
        box.pack_start(sw, True, True, 0)
        done = Gtk.Button(label=_t("Close"))
        done.set_relief(Gtk.ReliefStyle.NONE)
        done.get_style_context().add_class("emubtn")
        done.set_halign(Gtk.Align.END)
        done.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CLOSE))
        box.pack_start(done, False, False, 0)
        dlg.get_content_area().add(box)
        try:                     # the card carries its own button
            area = dlg.get_action_area()
            area.set_no_show_all(True)
            area.hide()
        except Exception:
            pass
        # Esc must dismiss it: there is no title bar to close.
        def _escape(_w, ev):
            if ev.keyval != Gdk.KEY_Escape:
                return False
            dlg.response(Gtk.ResponseType.CLOSE)
            return True

        dlg.connect("key-press-event", _escape)
        dlg.show_all()
        done.grab_focus()
        dlg.run()
        dlg.destroy()

    # ================= css =================
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .emuhead { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                   padding: 16px 22px; }
        .emutitle { font-size: 17px; font-weight: 600; color: #1A1916; }
        .emusub { font-size: 12px; color: #6E695E; letter-spacing: 0.03em; }
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
        .romsys { font-size: 11px; color: #6E695E; }
        .stateslot { padding: 2px 5px; min-height: 20px; min-width: 0;
                     border: 1px solid #C9C4B6; background: #FCFBF8;
                     box-shadow: none; font-size: 10px; color: #6E695E; }
        .stateslot:checked { background: #EAE3D2; border-color: #6E695E;
                            color: #1A1916; }
        .statekeys { font-size: 10px; color: #6E695E; }
        .statetime { font-size: 10px; color: #6E695E; }
        .emptytitle { font-size: 15px; font-weight: 600; color: #6E695E; }
        .emptysub { font-size: 13px; color: #6E695E; }
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
        .logview, .logtext, .logtext text { background: #FCFBF8;
                    color: #1A1916; }
        .logtext, .logtext text {
                    font-family: "Liberation Mono","DejaVu Sans Mono",monospace;
                    font-size: 11px; }
        .logview { border: 1px solid #C9C4B6; border-radius: 4px; }
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
