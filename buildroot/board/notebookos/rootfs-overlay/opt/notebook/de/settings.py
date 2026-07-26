#!/usr/bin/env python3
"""
Settings — the Notebook OS control centre (native GTK).

A two-pane control centre in the GNOME / System-Settings lineage: a scrollable
sidebar of sections and a content pane that drives the machine for real —
System info + hostname, displays (xrandr), sound (amixer), network (ip / sysfs),
power + battery, appearance/backdrop
(xsetroot), keyboard (setxkbmap + xset), mouse (xinput), date & time, region,
users (/etc/passwd), storage (statvfs / df), accessibility and default
applications. Every control either applies live, persists to
$NB_HOME/.config/notebook/settings.json, or both; persisted preferences are
re-applied on launch. Everything degrades gracefully when the underlying tool
or device isn't present (this is an offline appliance).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os
import re
import json
import shutil
import threading
import time
import subprocess

import nbapp
import nbicons
import nbprint
import nbi18n
from nbi18n import _t

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "settings.json")

# Time zones as (label, IANA name, POSIX TZ string). This is an offline
# appliance built WITHOUT tzdata (no /usr/share/zoneinfo), so the IANA name is
# only usable as a symlink target on the rare build that does ship the data;
# the POSIX string is what actually drives the clock here — libc parses it with
# no zoneinfo files at all, so the selected zone takes effect immediately.
TIMEZONES = [
    ("UTC", "UTC", "UTC0"),
    ("London", "Europe/London", "GMT0BST,M3.5.0/1,M10.5.0"),
    ("Central Europe (Paris, Berlin)", "Europe/Paris", "CET-1CEST,M3.5.0,M10.5.0/3"),
    ("Eastern (New York)", "America/New_York", "EST5EDT,M3.2.0,M11.1.0"),
    ("Central (Chicago)", "America/Chicago", "CST6CDT,M3.2.0,M11.1.0"),
    ("Mountain (Denver)", "America/Denver", "MST7MDT,M3.2.0,M11.1.0"),
    ("Pacific (Los Angeles)", "America/Los_Angeles", "PST8PDT,M3.2.0,M11.1.0"),
    ("India (Kolkata)", "Asia/Kolkata", "IST-5:30"),
    ("Japan (Tokyo)", "Asia/Tokyo", "JST-9"),
    ("Sydney", "Australia/Sydney", "AEST-10AEDT,M10.1.0,M4.1.0/3"),
]

# Papertone backdrop swatches (label, hex). #DED4C2 is the session default set by
# session.sh; the rest are tones from the same warm neutral family plus a dark
# option. Selecting one runs `xsetroot -solid <hex>`, which repaints the X root
# window — the real desktop background — immediately.
BACKDROPS = [
    ("Papertone", "#DED4C2"),
    ("Warm sand", "#E7DCC8"),
    ("Oat", "#D8CBB4"),
    ("Sage clay", "#CBC9B4"),
    ("Slate", "#8C8577"),
    ("Ink", "#2B2A26"),
]

# Default-application categories. Each maps a human category to a list of file
# extensions and the module (de/<name>.py) that opens them — the same shape as
# Finder's FILE_APPS. The user's choice persists to settings["default_apps"] as
# {ext: module}, ready for Finder's open-by-extension to consult.
# Only apps that actually open a document handed in as argv[1] belong here:
# Finder honours the stored choice (see finder.FILE_OPENERS), so offering an
# app that ignores the file (Novel/Academic open only their own manuscripts)
# would be a dead choice — the file would silently fail to open.
APP_CHOICES = [
    ("writer", "Writer"), ("ebook", "E-book Reader"),
    ("media", "Media Viewer"),
]
DEFAULT_APP_CATEGORIES = [
    ("Plain text (.txt)", [".txt"], "writer"),
    ("Markdown (.md)", [".md"], "writer"),
    ("Images (.png, .jpg)", [".png", ".jpg", ".jpeg", ".gif"], "media"),
    ("E-books (.epub, .pdf)", [".epub", ".pdf"], "ebook"),
]

# ---- Backup ----
# This machine has no network and one disk. Everything a person makes on it
# lives in exactly these places, and a USB stick is the only way any of it can
# leave. The list is written out rather than derived, so a backup is a stable,
# nameable set someone can check by eye afterwards.
BACKUP_DIRS = ("Desktop", "Documents", "Music", "Pictures", "Videos")
# What the apps themselves keep: journals, recipes, tasks, contacts, ledgers,
# saved games, settings. None of it is a file the user ever picked a name for,
# so it is the part they would never think to copy — and the part they would
# miss most.
APP_DATA_DIR = os.path.join(".config", "notebook")
BACKUP_PREFIX = "Notebook Backup"


def run(cmd, timeout=4):
    """Run a command, return (rc, stdout) — never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception:
        return 1, ""


def have(binary):
    """True if `binary` resolves on PATH (via `command -v`)."""
    rc, _ = run(["sh", "-c", "command -v " + binary])
    return rc == 0


def human_kb(kb):
    n = kb * 1024.0
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.1f %s" % (n, u) if u not in ("B", "KB") else "%d %s" % (n, u)
        n /= 1024.0


# Region & Language help text. Kept as module constants because the "you just
# switched" line has to be looked up in the language the user picked, not the
# one this window is running in, so both strings are needed as catalog keys.
REGION_NOTE = ("Apps you open from now on use the language you pick here. Apps "
               "that are already open, and the desktop itself, keep the "
               "language they started in — restart the computer to change "
               "everything at once. The keyboard and the time zone change "
               "straight away. Chinese is typed with the Pinyin input method: "
               "press Ctrl+Space in any text box to turn it on or off.")
REGION_SET = ("%s is set. Open an app to see it, or restart the computer to "
              "put the whole desktop in this language.")
# Shown only while a DUAL layout is chosen (Russian and Hindi are "ru,us" and
# "in,us" — a Cyrillic-only or Devanagari-only keyboard cannot type a file name
# or a password, so both ship with English alongside). Without this sentence
# the English half is there but unreachable as far as the user knows, which is
# indistinguishable from a broken keyboard.
KBD_DUAL_NOTE = ("This layout has two halves: the one you picked and English. "
                 "Press Alt+Shift to swap between them, so you can still type "
                 "a file name or a password.")
KBD_NOTE = ("Every change here happens straight away and is remembered next "
            "time you start up.")

# (label, icon) — icons reuse the nbicons set; an unknown name falls back to a
# neutral glyph, so the row is always drawn.
SECTIONS = [
    ("System", "sys"),
    ("Displays", "desktop"),
    ("Appearance", "brush"),
    ("Sound", "vol"),
    ("Network", "signal"),
    ("Printers", "inbox"),
    ("Power", "update"),
    ("Keyboard", "toc"),
    ("Mouse", "picker"),
    ("Date & Time", "calendar"),
    ("Region & Language", "library"),
    ("Users", "contacts"),
    ("Storage", "disk"),
    ("Backup", "duplicate"),
    ("Accessibility", "eye"),
    ("Default Apps", "packages"),
    ("About", "sources"),
]


class ReadingColumn(Gtk.Box):
    """The page's content column: a comfortable reading width, but shrinkable.

    Settings pages are label-left / control-right rows, so on a wide screen an
    uncapped column strands a label and its control an ocean apart (Apple and
    GNOME both cap theirs). A plain set_size_request(1040) capped it — but a
    size request is a MINIMUM as well as a natural size, so it also made the
    whole window 1372px wide at minimum: on a 1366x768 laptop panel, which
    matchbox maximises us onto, the right edge of every page was simply
    unreachable. Reporting MAX_W as the NATURAL width and the rows' own
    requirement as the MINIMUM gives real max-width behaviour: 1040 wherever
    there is room, narrower where there is not, and never a horizontal clip."""

    # No __gtype_name__ on purpose: a fixed GType name can only be
    # registered ONCE per process, so a second import of this module
    # dies with "could not create new GType". That is not academic —
    # it made three config-resilience checks look like defects in this
    # app for a long time, and it breaks any harness that renders two
    # apps in one process (installer imports settings). writer.py's
    # ReadingColumn documents the same decision.
    MAX_W = 1040

    def do_get_preferred_width(self):
        minw, _nat = Gtk.Box.do_get_preferred_width(self)
        # Never demand more than the cap: a single long row must wrap/ellipsize
        # rather than push the window past a small panel.
        return min(minw, self.MAX_W), self.MAX_W


class Settings(nbapp.AppWindow):
    app_name = "Settings"
    menus = ("View",)

    def __init__(self):
        super().__init__()
        self._install_css()
        self._settings = self._load_settings()
        self._confirm_layer = None
        self._dt_source = None
        # Cleared on destroy, so a worker thread that finishes after the window
        # has gone (the backup copy, the size measurement) drops its result
        # instead of poking widgets that no longer exist.
        self._alive = True
        self._bk_working = False
        self.connect("destroy", self._on_destroy)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.get_style_context().add_class("setbody")
        self.content.pack_start(body, True, True, 0)

        # sidebar — the pane list scrolls when it's taller than the window.
        sb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sb.get_style_context().add_class("setsidebar")
        # Wide enough for the longest row in its SELECTED (bold) form. The
        # selected row is heavier than the others, so a sidebar sized to its
        # content grew by 10px the moment "Region & Language" was picked and
        # shunted the whole page pane sideways; a fixed width the widest label
        # cannot exceed keeps the pane still. The labels also ellipsize (below)
        # so a longer translated section name can never re-open that gap.
        sb.set_size_request(244, -1)
        body.pack_start(sb, False, False, 0)

        seclbl = Gtk.Label(label=_t("SECTIONS"), xalign=0)
        seclbl.get_style_context().add_class("setseclabel")
        sb.pack_start(seclbl, False, False, 0)

        sbscroll = Gtk.ScrolledWindow()
        sbscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sbscroll.get_style_context().add_class("setsidescroll")
        rowbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sbscroll.add(rowbox)
        sb.pack_start(sbscroll, True, True, 0)

        self.stack = Gtk.Stack()
        # Instant switch (no crossfade): a frame-clock-driven transition stalls
        # under the no-compositor swrast fallback (the new page never finishes
        # fading in) and only adds latency on virgl. Impatient users want the
        # section to change the instant they click.
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        body.pack_start(self.stack, True, True, 0)

        self._rows = []
        for name, icon in SECTIONS:
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("setrow")
            hb = Gtk.Box(spacing=12)
            try:
                icon_img = Gtk.Image.new_from_pixbuf(
                    nbicons.pixbuf(icon, 18, "#6E695E"))
            except Exception:
                # icon renderer unavailable — keep the row, drop the glyph.
                icon_img = Gtk.Image()
            hb.pack_start(icon_img, False, False, 0)
            rl = Gtk.Label(label=_t(name), xalign=0)
            rl.set_ellipsize(Pango.EllipsizeMode.END)
            hb.pack_start(rl, True, True, 0)
            row.add(hb)
            row.connect("clicked", self._select, name)
            rowbox.pack_start(row, False, False, 0)
            self._rows.append((name, row))

        # Pages are built LAZILY. Registering a page adds only an empty
        # placeholder holder to the stack; the real widget tree — and the
        # blocking xrandr/amixer/ip/df/… probes several builders
        # run at construction time — is built the first time that page is
        # shown, then cached. This keeps launch from stalling the UI thread on
        # ~10 subprocess calls and 17 pages of widgets: only the initially
        # visible System page is built up front, so the window opens instantly
        # on real content.
        self._built = set()
        self._page_holders = {}
        self._page_builders = {
            "System": self._page_system,
            "Displays": self._page_display,
            "Appearance": self._page_appearance,
            "Sound": self._page_sound,
            "Network": self._page_network,
            "Printers": self._page_printers,
            "Power": self._page_power,
            "Keyboard": self._page_keyboard,
            "Mouse": self._page_mouse,
            "Date & Time": self._page_datetime,
            "Region & Language": self._page_region,
            "Users": self._page_users,
            "Storage": self._page_storage,
            "Backup": self._page_backup,
            "Accessibility": self._page_accessibility,
            "Default Apps": self._page_defaultapps,
            "About": self._page_about,
        }
        for name in self._page_builders:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            # Paint the page pane paper. A Gtk.Stack owns its GdkWindow but does
            # NOT draw its own CSS background on this software stack, so wherever
            # a section page is shorter or narrower than the pane the bare stack
            # window showed through BLACK (a hard frame around the content card).
            # The holder is a Gtk.Box, which does paint its background and is
            # sized to the full pane by the stack, so paper here covers the gap.
            holder.get_style_context().add_class("setpane")
            # The empty placeholder must be visible or the stack will refuse to
            # switch to it (and the build-on-view signal below never fires).
            holder.show()
            self._page_holders[name] = holder
            self.stack.add_named(holder, name)
        # Build the target page the first time it becomes visible. Wiring this
        # to the stack (not just _select) catches every switch path: sidebar
        # clicks, the "View" menu, and any direct set_visible_child_name call.
        self.stack.connect("notify::visible-child-name", self._on_page_switch)
        # Build the first page eagerly so the window opens on real content.
        self._ensure_built("System")
        self._select(None, "System")

        # Re-apply persisted preferences so they actually take effect this
        # session (each guarded — a missing tool is a no-op, never a crash).
        self._apply_saved_prefs()

    def _select(self, _btn, name):
        self.stack.set_visible_child_name(name)
        for n, row in self._rows:
            ctx = row.get_style_context()
            if n == name:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _on_page_switch(self, *_):
        # Fired by the stack whenever the visible page changes — build the
        # newly-shown page on first view.
        name = self.stack.get_visible_child_name()
        if name:
            self._ensure_built(name)

    def _ensure_built(self, name):
        # Build a page's content into its placeholder the first time it is
        # shown, then cache it (the flag is set before building so a page is
        # never built twice, even if a switch re-enters).
        if name in self._built:
            return
        builder = self._page_builders.get(name)
        holder = self._page_holders.get(name)
        if builder is None or holder is None:
            return
        self._built.add(name)
        content = builder()
        if content is not None:
            holder.pack_start(content, True, True, 0)
            holder.show_all()

    # ---- menu bar ----
    def menu_items(self, name):
        # "View" jumps straight to any settings section — same code path as
        # clicking a sidebar row (self._select), so it's always safe.
        if name == "View":
            items = [("Go to Section", None), nbapp.SEP]
            for sec, _icon in SECTIONS:
                items.append((sec, lambda s=sec: self._select(None, s)))
            return items
        return super().menu_items(name)

    # ---- page scaffolding ----
    def _page(self, title, subtitle=None):
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        col.get_style_context().add_class("setpage")
        # Inset via PADDING, not margins: padding lives inside the element's
        # paper background so the page fills the pane edge-to-edge, whereas
        # margins are transparent and exposed the (non-painting) viewport/stack
        # window as a black frame around the content on this software stack.
        col.set_vexpand(True)
        # Cap the text column at a comfortable reading width, centred. col stays
        # full-width paper (so the black-frame fix holds); `content` carries the
        # rows so that on a wide screen a label and its value don't sit an ocean
        # apart (as Apple/GNOME settings avoid). ReadingColumn does the capping
        # in its own width request — deterministic, with no size-allocate
        # handler whose firing order could leave one page capped and the next
        # full-width, and unlike a plain set_size_request it still SHRINKS onto
        # a 1366 or 1024 panel instead of forcing the window wider than one.
        content = ReadingColumn(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.set_hexpand(False)
        content.set_halign(Gtk.Align.CENTER)
        col.pack_start(content, False, False, 0)

        h = Gtk.Label(label=title, xalign=0)
        h.get_style_context().add_class("settitle")
        content.pack_start(h, False, False, 0)
        if subtitle:
            s = Gtk.Label(label=subtitle, xalign=0)
            s.get_style_context().add_class("setsubtitle")
            content.pack_start(s, False, False, 0)
        rule = Gtk.Box()
        rule.get_style_context().add_class("setrule")
        content.pack_start(rule, False, False, 0)
        outer.add(col)
        # Callers pack their rows/cards into the width-capped `content`, not the
        # full-width `col` (which just carries the paper); the signature is
        # unchanged so no page builder needs editing.
        return outer, content

    def _card(self, parent, top=0):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        c.get_style_context().add_class("setcard")
        if top:
            c.set_margin_top(top)
        parent.pack_start(c, False, False, 0)
        return c

    def _grouplabel(self, parent, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("setgroup")
        parent.pack_start(lbl, False, False, 0)

    def _row_widget(self, card, label, widget, first=False, sub=None):
        r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        r.get_style_context().add_class("setitem")
        if not first:
            r.get_style_context().add_class("bordered")
        lblbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.get_style_context().add_class("setlabel")
        lblbox.pack_start(lbl, False, False, 0)
        if sub:
            sl = Gtk.Label(label=sub, xalign=0)
            sl.get_style_context().add_class("setsublabel")
            # A one-line hint is the widest thing in its row, so an unwrapped
            # one sets the page's MINIMUM width — a couple of helpful sentences
            # was all it took to push the window past a 1024px panel. Cap the
            # measure and let it wrap under the label instead.
            sl.set_line_wrap(True)
            sl.set_max_width_chars(48)
            sl.set_halign(Gtk.Align.START)
            lblbox.pack_start(sl, False, False, 0)
        r.pack_start(lblbox, False, False, 0)
        r.pack_end(widget, False, False, 0)
        card.pack_start(r, False, False, 0)
        return r

    def _value_row(self, card, label, value, first=False):
        val = Gtk.Label(label=value, xalign=1)
        val.get_style_context().add_class("setvalue")
        val.set_line_wrap(True)
        val.set_max_width_chars(46)
        return self._row_widget(card, label, val, first=first)

    def _note(self, col, text):
        note = Gtk.Label(xalign=0)
        note.get_style_context().add_class("setnote")
        note.set_line_wrap(True)
        # Wrapping alone is not a measure: a label in a vertical box defaults to
        # halign FILL, so it stretched to the full 1040px column and set every
        # explanation as one ~145-character line. Pinning halign START lets the
        # natural width — capped here — decide, so help text reads as a proper
        # paragraph at any window size.
        note.set_max_width_chars(92)
        note.set_halign(Gtk.Align.START)
        note.set_text(text)
        col.pack_start(note, False, False, 0)
        return note

    def _field_status(self):
        # A small inline label placed beside an Apply control so a click (or
        # Enter) never leaves the user guessing whether it worked. Empty until
        # something is applied; a problem shows in the alert red, a success in
        # the neutral sublabel tone.
        lbl = Gtk.Label(xalign=0)
        lbl.get_style_context().add_class("setsublabel")
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(22)
        return lbl

    def _set_status(self, lbl, text, warn=False):
        if lbl is None:
            return
        lbl.set_text(text)
        ctx = lbl.get_style_context()
        if warn:
            ctx.add_class("setwarn")
        else:
            ctx.remove_class("setwarn")

    def _pref_switch(self, card, label, key, default, first=False,
                     sub=None, on_change=None):
        sw = Gtk.Switch()
        sw.set_active(bool(self._settings.get(key, default)))
        sw.connect("state-set", self._on_pref_switch, key, on_change)
        self._row_widget(card, label, sw, first=first, sub=sub)
        return sw

    def _on_pref_switch(self, _sw, state, key, on_change):
        self._settings[key] = bool(state)
        self._save_settings()
        if on_change:
            try:
                on_change(bool(state))
            except Exception:
                pass
        return False

    # ---- persistence ----
    def _load_settings(self):
        try:
            with open(CFG_FILE) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_settings(self):
        try:
            nbapp.atomic_write_json(CFG_FILE, self._settings)
        except Exception:
            pass

    def _on_destroy(self, *_):
        self._alive = False
        # Stop the Date & Time clock ticker so nothing fires after teardown.
        src = getattr(self, "_dt_source", None)
        if src is not None:
            try:
                GLib.source_remove(src)
            except Exception:
                pass
            self._dt_source = None
        self._save_settings()

    # A garbage or hand-edited settings.json can hold the wrong type for any
    # key; these coerce to a numeric value without ever raising, so a bad file
    # opens a safe default rather than crashing a page (or launch).
    def _cfg_int(self, key, default):
        try:
            return int(self._settings.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _cfg_float(self, key, default):
        try:
            return float(self._settings.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    # ---- destructive-action confirmation ----
    # An in-window overlay (reusing nbapp's self._overlay) rather than a popup
    # Gtk.Dialog: separate popup windows do not reliably paint on the
    # no-compositor swrast stack, so the whole desktop draws its confirms this
    # way (see installer.py). Fail-safe: if no overlay host exists the caller's
    # destructive action simply does not run.
    def _confirm(self, heading, body, ok_label, on_ok):
        self._close_confirm()
        overlay = getattr(self, "_overlay", None)
        if overlay is None:
            return
        # Size and centre against the ACTUAL window, never a fixed 1920x1080 —
        # real panels are smaller (1366x768, 1280x800, …) and the Displays page
        # can change the resolution/scale, so a hard-coded scrim overflows the
        # panel and a hard-coded centre drops the card off-screen. Fall back to
        # the live primary-monitor size (nbapp.screen_size()) when the window
        # isn't allocated yet — never a literal 1920x1080.
        alloc = self.get_allocation()
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("setconfirm-scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_confirm(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("setconfirm")
        h = Gtk.Label(label=heading, xalign=0)
        h.get_style_context().add_class("setconfirm-h")
        b = Gtk.Label(label=body, xalign=0)
        b.get_style_context().add_class("setconfirm-b")
        b.set_line_wrap(True)
        # A minimum as well as a maximum. With only a max, the card's MINIMUM
        # width was the heading's, so it was allocated far narrower than the
        # natural size the centring below is computed from — the card ended up
        # left of centre with the message cramped into ~28 characters.
        b.set_width_chars(42)
        b.set_max_width_chars(46)
        card.pack_start(h, False, False, 0)
        card.pack_start(b, False, False, 0)
        row = Gtk.Box(spacing=10)
        row.set_halign(Gtk.Align.END)
        row.set_margin_top(8)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("setbtn")
        cancel.connect("clicked", lambda *_: self._close_confirm())
        okb = Gtk.Button(label=ok_label)
        okb.get_style_context().add_class("setprimary")
        okb.connect("clicked", lambda *_: (self._close_confirm(), on_ok()))
        row.pack_start(cancel, False, False, 0)
        row.pack_start(okb, False, False, 0)
        card.pack_start(row, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits reliably
        card_win.add(card)
        layer.put(card_win, 0, 0)
        overlay.add_overlay(layer)
        layer.show_all()
        # Centre on the measured card size so it stays centred at any resolution.
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 480
        ch = nat.height if nat.height > 1 else 200
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
        # Focus Cancel so a stray Enter/Space dismisses rather than confirms a
        # destructive action.
        try:
            cancel.grab_focus()
        except Exception:
            pass
        self._confirm_layer = layer

    def _close_confirm(self, *_):
        layer = getattr(self, "_confirm_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._confirm_layer = None
        return True

    def _apply_saved_prefs(self):
        # Backdrop
        bg = self._settings.get("background")
        if isinstance(bg, str) and bg:
            run(["xsetroot", "-solid", bg])
        # Time zone — re-point /etc/localtime and export TZ so the saved zone
        # actually takes effect this session. _apply_tz degrades gracefully
        # when tzdata/localtime is absent (falls back to the POSIX TZ string).
        tz = self._settings.get("tz")
        if tz:
            for _lbl, iana, posix in TIMEZONES:
                if iana == tz:
                    self._apply_tz(iana, posix)
                    break
        # Render scale — re-apply the saved xrandr supersample factor so the
        # value shown on the Displays page is genuinely active (the 1.0 native
        # default is a no-op; a missing xrandr is silently ignored).
        scale = str(self._settings.get("display_scale", "1.0"))
        if scale in ("1.25", "1.5", "2.0"):
            out = self._x_output()
            if out:
                run(["xrandr", "--output", out, "--scale",
                     "%sx%s" % (scale, scale)])
        # Screen-blank timeout
        if "blank_timeout" in self._settings:
            self._apply_blank(self._cfg_int("blank_timeout", 0))
        # Keyboard repeat
        if "kbd_delay" in self._settings or "kbd_rate" in self._settings:
            self._apply_repeat(self._cfg_int("kbd_delay", 500),
                               self._cfg_int("kbd_rate", 25))
        # Mouse
        if "pointer_speed" in self._settings:
            self._apply_pointer_speed(self._cfg_float("pointer_speed", 0.0))
        if "natural_scroll" in self._settings:
            self._apply_natural_scroll(self._settings["natural_scroll"])
        # Accessibility is NOT re-applied here: nbapp reads the same two keys
        # as it is imported, which is what makes the choice reach every app
        # rather than this one. Doing it again would only re-parse stylesheets
        # that are already correct.

    # ---- System ----
    def _page_system(self):
        outer, col = self._page("System", "Device information and name")

        card = self._card(col)
        host = self._hostname()
        self._host_entry = Gtk.Entry()
        self._host_entry.set_text(host)
        self._host_entry.set_width_chars(18)
        # Enter in the field applies too — a novice shouldn't have to hunt for
        # the button. Typing again clears the last result.
        self._host_entry.connect("activate", self._on_hostname)
        self._host_entry.connect(
            "changed", lambda *_: self._set_status(self._host_status, ""))
        hostbtn = Gtk.Button(label=_t("Apply"))
        hostbtn.get_style_context().add_class("setbtn")
        hostbtn.connect("clicked", self._on_hostname)
        self._host_status = self._field_status()
        hostbox = Gtk.Box(spacing=8)
        hostbox.pack_start(self._host_entry, False, False, 0)
        hostbox.pack_start(hostbtn, False, False, 0)
        hostbox.pack_start(self._host_status, False, False, 0)
        self._row_widget(card, "Device name", hostbox, first=True,
                         sub="What this computer calls itself")

        card2 = self._card(col, top=16)
        rows = self._system_rows()
        for i, (k, v) in enumerate(rows):
            self._value_row(card2, k, v, first=(i == 0))

        self._note(col, "The new name takes effect straight away and is "
                        "remembered after you restart.")
        return outer

    def _hostname(self):
        try:
            with open("/proc/sys/kernel/hostname") as fh:
                return fh.readline().strip() or "notebook"
        except OSError:
            return "notebook"

    def _on_hostname(self, _w=None):
        name = self._host_entry.get_text().strip()
        # Accept a conservative hostname charset; tell the user why rather than
        # silently swallowing an empty or invalid name.
        if not name:
            self._set_status(self._host_status, "Enter a device name", warn=True)
            return
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", name):
            self._set_status(self._host_status,
                             "Letters, numbers and hyphens only", warn=True)
            return
        # Persistent file first, then apply live. hostnamectl if present does
        # both; fall back to the plain `hostname` command + file write.
        try:
            with open("/etc/hostname", "w") as fh:
                fh.write(name + "\n")
        except OSError:
            pass
        if have("hostnamectl"):
            run(["hostnamectl", "set-hostname", name])
        else:
            run(["hostname", name])
        self._set_status(self._host_status, "Saved", warn=False)

    def _system_rows(self):
        rows = []
        rows.append(("Operating system", self._os_name()))
        rc, kern = run(["uname", "-sr"])
        if rc != 0 or not kern.strip():
            kern = self._first_line("/proc/sys/kernel/osrelease")
        rows.append(("Kernel", kern.strip() or "—"))
        rc, arch = run(["uname", "-m"])
        # "x86_64" on its own means nothing to most people; lead with the plain
        # fact (Windows says "64-bit operating system") and keep the exact name
        # after it for anyone who needs to match a download.
        arch = arch.strip()
        if arch in ("x86_64", "amd64", "aarch64", "arm64"):
            arch = "64-bit  (%s)" % arch
        rows.append(("System type", arch or "—"))
        cpu, cores = self._cpu_info()
        rows.append(("Processor", cpu))
        rows.append(("Processor cores", str(cores) if cores else "—"))
        total, used = self._mem_info()
        if total:
            rows.append(("Memory", "%s used of %s"
                         % (human_kb(used), human_kb(total))))
        else:
            rows.append(("Memory", "—"))
        rows.append(("Uptime", self._uptime()))
        return rows

    def _os_name(self):
        try:
            with open("/etc/os-release") as fh:
                for ln in fh:
                    if ln.startswith("PRETTY_NAME="):
                        return ln.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "Notebook OS"

    def _first_line(self, path, d=""):
        try:
            with open(path) as fh:
                return fh.readline().strip()
        except OSError:
            return d

    def _cpu_info(self):
        model, cores = "", 0
        try:
            with open("/proc/cpuinfo") as fh:
                for ln in fh:
                    if ln.startswith("processor"):
                        cores += 1
                    elif not model and (ln.startswith("model name")
                                        or ln.startswith("Model")
                                        or ln.startswith("Hardware")):
                        model = ln.split(":", 1)[1].strip()
        except OSError:
            pass
        return (model or "Unknown processor", cores)

    def _mem_info(self):
        total = avail = 0
        try:
            with open("/proc/meminfo") as fh:
                for ln in fh:
                    if ln.startswith("MemTotal:"):
                        total = int(ln.split()[1])
                    elif ln.startswith("MemAvailable:"):
                        avail = int(ln.split()[1])
        except (OSError, ValueError, IndexError):
            return (0, 0)
        used = total - avail if total and avail else 0
        return (total, used)

    def _uptime(self):
        try:
            with open("/proc/uptime") as fh:
                secs = int(float(fh.read().split()[0]))
            d, rem = divmod(secs, 86400)
            h, rem = divmod(rem, 3600)
            m = rem // 60
            if d:
                return "%dd %dh %dm" % (d, h, m)
            return "%dh %dm" % (h, m)
        except (OSError, ValueError, IndexError):
            return "—"

    # ---- Displays ----
    def _page_display(self):
        outer, col = self._page("Displays", "Screen resolution and size")
        card = self._card(col)
        # One xrandr probe drives all three reads (output/modes/current) — no
        # need to spawn the subprocess three times while building the page.
        xr = self._xrandr()
        out = self._x_output(xr)
        modes = self._x_modes(out, xr)
        cur = self._x_current(out, xr)
        combo = Gtk.ComboBoxText()
        for m in modes:
            combo.append_text(m)
        if cur in modes:
            combo.set_active(modes.index(cur))
        elif modes:
            combo.set_active(0)
        combo.connect("changed", self._on_res, out)
        # Both drop-downs share a width so their left edges line up.
        ctl = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        ctl.add_widget(combo)
        self._row_widget(card, "Resolution", combo, first=True)

        # Render scale (xrandr --scale): a higher factor draws the desktop at a
        # larger size and shrinks it onto the panel, so everything gets smaller
        # and finer; 1.0 is the panel's own size. The user-facing labels say
        # that, and the factor is kept in brackets for anyone who wants it.
        scombo = Gtk.ComboBoxText()
        self._scale_vals = ["1.0", "1.25", "1.5", "2.0"]
        for label in ("Normal (1.0×)", "Smaller (1.25×)",
                      "Smaller still (1.5×)", "Smallest (2.0×)"):
            scombo.append_text(label)
        saved_scale = str(self._settings.get("display_scale", "1.0"))
        scombo.set_active(self._scale_vals.index(saved_scale)
                          if saved_scale in self._scale_vals else 0)
        scombo.connect("changed", self._on_scale, out)
        ctl.add_widget(scombo)
        self._row_widget(card, "Size of everything", scombo,
                         sub="Smaller settings fit more on screen and draw "
                             "text more finely")

        note = ("These settings apply to the screen connected as %s." % out
                if out else
                "The screen on this computer cannot be adjusted from here.")
        self._note(col, note)
        return outer

    def _xrandr(self):
        _rc, o = run(["xrandr"])
        return o

    def _x_output(self, o=None):
        if o is None:
            _rc, o = run(["xrandr"])
        for line in o.splitlines():
            if " connected" in line:
                return line.split()[0]
        return ""

    def _x_modes(self, out, o=None):
        if o is None:
            _rc, o = run(["xrandr"])
        modes, grab = [], False
        for line in o.splitlines():
            if line.startswith(out + " ") or line.startswith(out + "\t"):
                grab = True
                continue
            if grab:
                if line and not line[0].isspace():
                    break
                m = line.strip().split()
                if m and "x" in m[0]:
                    modes.append(m[0])
        seen, uniq = set(), []
        for m in modes:
            if m not in seen:
                seen.add(m); uniq.append(m)
        if uniq:
            return uniq
        # No xrandr/RandR mode list (a plain EFI/simpledrm framebuffer has none)
        # — offer the panel's ACTUAL current resolution, never a hardcoded
        # 1920x1080 that wouldn't match a smaller real panel.
        sw, sh = nbapp.screen_size()
        return ["%dx%d" % (sw, sh)]

    def _x_current(self, out, o=None):
        if o is None:
            _rc, o = run(["xrandr"])
        for line in o.splitlines():
            if line.startswith(out) and "*" in line:
                for tok in line.split():
                    if "x" in tok and tok[0].isdigit():
                        return tok
        for line in o.splitlines():
            if "*" in line:
                return line.strip().split()[0]
        return ""

    def _on_res(self, combo, out):
        mode = combo.get_active_text()
        if mode and out:
            run(["xrandr", "--output", out, "--mode", mode])

    def _on_scale(self, combo, out):
        i = combo.get_active()
        if not (0 <= i < len(self._scale_vals)) or not out:
            return
        f = self._scale_vals[i]
        run(["xrandr", "--output", out, "--scale", "%sx%s" % (f, f)])
        self._settings["display_scale"] = f
        self._save_settings()

    # ---- Appearance ----
    def _page_appearance(self):
        outer, col = self._page("Appearance", "Desktop backdrop and highlight colour")
        self._grouplabel(col, "Desktop backdrop")
        card = self._card(col)
        cur = self._settings.get("background")
        if not isinstance(cur, str):
            cur = "#DED4C2"
        self._bg_swatches = []
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(6)
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_top(14); grid.set_margin_bottom(14)
        grid.set_margin_start(4); grid.set_margin_end(4)
        for label, hexcol in BACKDROPS:
            btn = Gtk.Button()
            btn.get_style_context().add_class("swatch")
            if hexcol.lower() == cur.lower():
                btn.get_style_context().add_class("selected")
            # Sized so all six swatches sit on ONE line inside the page's
            # narrowest pane (a 1024px panel), instead of wrapping five-plus-one
            # and leaving an orphan on a second row.
            btn.set_size_request(96, 62)
            btn.set_tooltip_text(label)
            self._swatch_style(btn, hexcol)
            btn.connect("clicked", self._on_backdrop, hexcol)
            self._bg_swatches.append((hexcol, btn))
            grid.add(btn)
        card.pack_start(grid, False, False, 0)

        self._grouplabel(col, "Accent")
        card2 = self._card(col)
        self._value_row(card2, "Highlight colour", "Signal red", first=True)

        self._note(col, "The backdrop changes as soon as you pick one, and "
                        "comes back the same way every time you start up. The "
                        "highlight colour — used for today's date, alerts and "
                        "the main button on a screen — is part of the Notebook "
                        "look and stays the same.")
        return outer

    def _swatch_style(self, btn, hexcol):
        prov = Gtk.CssProvider()
        prov.load_from_data(
            (".swcolor-%s { background: %s; }"
             % (hexcol.lstrip("#"), hexcol)).encode())
        btn.get_style_context().add_provider(
            prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        btn.get_style_context().add_class("swcolor-" + hexcol.lstrip("#"))

    def _on_backdrop(self, _btn, hexcol):
        run(["xsetroot", "-solid", hexcol])
        self._settings["background"] = hexcol
        self._save_settings()
        for hc, b in self._bg_swatches:
            ctx = b.get_style_context()
            if hc == hexcol:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    # ---- Sound ----
    def _page_sound(self):
        outer, col = self._page("Sound", "Volume for speakers and the microphone")
        # Without a Master mixer control there is nothing to drive — show an
        # honest empty state rather than a slider and switch that do nothing.
        if not self._has_ctl("Master"):
            card = self._card(col)
            self._value_row(card, "Sound", "No speakers or sound card found",
                            first=True)
            self._note(col, "Nothing on this computer can play sound at the "
                            "moment. Plug in speakers or headphones, or switch "
                            "on the sound device, then open Sound again.")
            return outer
        self._grouplabel(col, "Speakers and headphones")
        card = self._card(col)
        vol = self._get_volume("Master")
        adj = Gtk.Adjustment(value=vol, lower=0, upper=100, step_increment=5)
        scale = self._percent_scale(adj, self._on_vol)
        self._row_widget(card, "Volume", scale, first=True)
        mute = Gtk.Switch()
        mute.set_active(self._get_mute())
        mute.connect("state-set", self._on_mute)
        self._row_widget(card, "Silence all sound", mute)

        # Input (capture) level, only if the device exposes a Capture control.
        if self._has_ctl("Capture"):
            self._grouplabel(col, "Microphone")
            card2 = self._card(col)
            cvol = self._get_volume("Capture")
            cadj = Gtk.Adjustment(value=cvol, lower=0, upper=100,
                                  step_increment=5)
            cscale = self._percent_scale(cadj, self._on_capvol)
            self._row_widget(card2, "Recording level", cscale, first=True)
        self._note(col, "Changes take effect as you move the slider.")
        return outer

    def _percent_scale(self, adj, on_change):
        # A 0-100 slider whose readout is a whole percentage. The GTK default
        # draws one decimal place, so every volume read "100.0" — a number with
        # no unit and a decimal nobody asked for.
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        scale.set_size_request(280, -1)
        scale.set_draw_value(True)
        scale.set_digits(0)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.connect("format-value", lambda _s, v: "%d%%" % round(v))
        scale.connect("value-changed", on_change)
        return scale

    def _has_ctl(self, ctl):
        rc, _ = run(["amixer", "sget", ctl])
        return rc == 0

    def _get_volume(self, ctl):
        rc, o = run(["amixer", "sget", ctl])
        if rc != 0:
            return 50
        m = re.search(r"\[(\d+)%\]", o)
        return int(m.group(1)) if m else 50

    def _get_mute(self):
        rc, o = run(["amixer", "sget", "Master"])
        if rc != 0:
            return False
        m = re.search(r"\[(on|off)\]", o)
        return m.group(1) == "off" if m else False

    def _on_vol(self, scale):
        run(["amixer", "sset", "Master", "%d%%" % int(scale.get_value())])

    def _on_capvol(self, scale):
        run(["amixer", "sset", "Capture", "%d%%" % int(scale.get_value())])

    def _on_mute(self, _sw, state):
        run(["amixer", "sset", "Master", "mute" if state else "unmute"])
        return False

    # ---- Network ----
    def _page_network(self):
        outer, col = self._page("Network", "Connections in and out of this computer")
        ifaces = self._net_interfaces()
        card = self._card(col)
        if ifaces:
            for i, (name, state, ip) in enumerate(ifaces):
                val = "%s  ·  %s" % (state, ip) if ip else state
                self._value_row(card, name, val, first=(i == 0))
        else:
            self._value_row(card, "Connections", "None found", first=True)

        self._note(col, "This computer has no Wi-Fi and no way to reach the "
                        "internet — that is deliberate. Anything listed above "
                        "only carries traffic inside the machine itself or "
                        "along a cable you plug in.")
        return outer

    def _net_interfaces(self):
        # Names + operstate from sysfs (always present); IPv4 from `ip` if
        # available. Skip nothing — loopback is shown honestly.
        result = []
        ips = {}
        rc, o = run(["ip", "-o", "-4", "addr", "show"])
        if rc == 0:
            for ln in o.splitlines():
                parts = ln.split()
                if len(parts) >= 4 and parts[2] == "inet":
                    ips[parts[1]] = parts[3].split("/")[0]
        try:
            names = sorted(os.listdir("/sys/class/net"))
        except OSError:
            names = []
        # operstate is a kernel word ("up" / "down" / "unknown"); shouting it in
        # capitals told the user nothing. Say plainly whether it is carrying
        # traffic, and name the loopback device for what it is.
        plain = {"up": "Connected", "down": "Not connected",
                 "dormant": "Idle", "lowerlayerdown": "No cable",
                 "unknown": "Ready"}
        for n in names:
            state = self._first_line("/sys/class/net/%s/operstate" % n, "unknown")
            # Say what each one IS. "eth0"/"enp3s0" is the kernel's name for the
            # socket a network cable plugs into, and nothing to a normal reader.
            if n == "lo":
                label = "%s (inside this computer)" % n
            elif n.startswith(("eth", "en")):
                label = "%s (network cable)" % n
            else:
                label = n
            result.append((label, plain.get(state.lower(), state),
                           ips.get(n, "")))
        return result

    def _page_printers(self):
        outer, col = self._page("Printers", "Add and manage USB printers")

        # Nothing to drive without the CUPS command-line tools — show an honest
        # empty state (mirrors the Sound/Bluetooth 'unavailable' pattern).
        if not (have("lpstat") and have("lpadmin")):
            card = self._card(col)
            self._value_row(card, "Printing", "Not available on this computer",
                            first=True)
            self._note(col, "This copy of Notebook OS was built without "
                            "printing, so a printer cannot be set up here. You "
                            "can still save any document as a PDF from its File "
                            "menu and print it from another computer.")
            return outer

        # ---- configured printers ----
        self._grouplabel(col, "Your printers")
        self._pr_list_card = self._card(col)
        self._pr_status = None
        self._printers_refresh()

        # ---- add a USB printer ----
        self._grouplabel(col, "Add a printer")
        self._pr_add_card = self._card(col)
        self._pr_dev_combo = None
        self._pr_drv_combo = None
        self._pr_name_entry = None
        self._pr_add_status = None
        self._printers_build_add()

        self._note(col, "Connect a printer by USB and switch it on, then use "
                        "Find printers. Notebook OS matches a driver "
                        "automatically; if in doubt, keep the recommended one. "
                        "A test page confirms everything works.")
        return outer

    # ---- listing ----
    def _printers_refresh(self):
        card = getattr(self, "_pr_list_card", None)
        if card is None:
            return
        for ch in card.get_children():
            card.remove(ch)
        printers, default = nbprint.list_printers()
        if not printers:
            self._value_row(card, "Printers",
                            "No printers yet — add one below", first=True)
            card.show_all()
            return
        for i, p in enumerate(printers):
            self._printer_row(card, p, default, first=(i == 0))
        card.show_all()

    def _printer_row(self, card, p, default, first=False):
        name = p.get("name", "")
        is_default = (name == default)
        ready = bool(p.get("ready", True))
        info = p.get("info") or name
        bits = []
        if info and info != name:
            bits.append(info)
        bits.append("Ready" if ready else "Paused")
        sub = "  ·  ".join(bits)

        box = Gtk.Box(spacing=6)
        if is_default:
            tag = Gtk.Label(label=_t("Default"))
            tag.get_style_context().add_class("setbadge")
            tag.set_valign(Gtk.Align.CENTER)
            box.pack_start(tag, False, False, 4)
        else:
            db = Gtk.Button(label=_t("Set default"))
            db.get_style_context().add_class("setbtn")
            db.connect("clicked", self._on_printer_default, name)
            box.pack_start(db, False, False, 0)
        tb = Gtk.Button(label=_t("Test page"))
        tb.get_style_context().add_class("setbtn")
        tb.connect("clicked", self._on_printer_test, name)
        box.pack_start(tb, False, False, 0)
        xb = Gtk.Button(label=_t("Remove"))
        xb.get_style_context().add_class("setbtn")
        xb.connect("clicked", self._on_printer_remove, name)
        box.pack_start(xb, False, False, 0)
        self._row_widget(card, name, box, first=first, sub=sub)

    # ---- add flow ----
    def _printers_build_add(self):
        card = getattr(self, "_pr_add_card", None)
        if card is None:
            return
        for ch in card.get_children():
            card.remove(ch)

        devices = self._printers_scan_usb()
        self._pr_devices = devices

        # Device picker + a re-scan button so a printer switched on late is found.
        self._pr_dev_combo = Gtk.ComboBoxText()
        if devices:
            for _uri, label in devices:
                self._pr_dev_combo.append_text(label)
            self._pr_dev_combo.set_active(0)
        else:
            self._pr_dev_combo.append_text("No USB printer detected")
            self._pr_dev_combo.set_active(0)
            self._pr_dev_combo.set_sensitive(False)
        self._pr_dev_combo.connect("changed", self._on_printer_dev_changed)
        find = Gtk.Button(label=_t("Find printers"))
        find.get_style_context().add_class("setbtn")
        find.connect("clicked", lambda *_: self._printers_build_add())
        devbox = Gtk.Box(spacing=8)
        devbox.pack_start(self._pr_dev_combo, False, False, 0)
        devbox.pack_start(find, False, False, 0)
        self._row_widget(card, "USB printer", devbox, first=True,
                         sub="Connect the printer and switch it on")

        if not have("lpinfo"):
            # lpadmin is present but device discovery is not — say so plainly
            # rather than offer an Add that can't find a device.
            self._note_in_card(card)
            card.show_all()
            return

        if not devices:
            card.show_all()
            return

        # Name — prefilled from the device, sanitised to a CUPS-legal queue name.
        self._pr_name_entry = Gtk.Entry()
        self._pr_name_entry.set_width_chars(20)
        self._pr_name_entry.set_text(self._printer_default_name(devices[0][1]))
        self._pr_name_entry.connect(
            "changed", lambda *_: self._set_status(self._pr_add_status, ""))
        self._row_widget(card, "Name", self._pr_name_entry,
                         sub="Letters, numbers, hyphen and underscore")

        # Driver — best matches for the selected device, plus the driverless
        # option; the recommended choice is pre-selected for a novice.
        self._pr_drv_combo = Gtk.ComboBoxText()
        self._pr_drv_note = self._field_status()
        self._pr_drv_note.set_line_wrap(True)
        self._pr_drv_note.set_max_width_chars(44)
        self._printers_fill_drivers(devices[0][1], devices[0][0])
        drvbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        drvbox.pack_start(self._pr_drv_combo, False, False, 0)
        drvbox.pack_start(self._pr_drv_note, False, False, 0)
        self._row_widget(card, "Driver", drvbox,
                         sub="Recommended driver is pre-selected")

        addbtn = Gtk.Button(label=_t("Add printer"))
        addbtn.get_style_context().add_class("setprimary")
        addbtn.connect("clicked", self._on_printer_add)
        self._pr_add_status = self._field_status()
        addrow = Gtk.Box(spacing=8)
        addrow.pack_start(addbtn, False, False, 0)
        addrow.pack_start(self._pr_add_status, False, False, 0)
        self._row_widget(card, "", addrow)
        card.show_all()

    def _note_in_card(self, card):
        lbl = Gtk.Label(
            label="This computer cannot search for printers by itself. Any "
                  "printer that has already been set up still appears above.",
            xalign=0)
        lbl.get_style_context().add_class("setsublabel")
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(46)
        row = Gtk.Box()
        row.get_style_context().add_class("setitem")
        row.get_style_context().add_class("bordered")
        row.pack_start(lbl, False, False, 0)
        card.pack_start(row, False, False, 0)

    def _on_printer_dev_changed(self, combo):
        devices = getattr(self, "_pr_devices", [])
        i = combo.get_active()
        if not (0 <= i < len(devices)):
            return
        label = devices[i][1]
        if self._pr_name_entry is not None:
            self._pr_name_entry.set_text(self._printer_default_name(label))
        if self._pr_drv_combo is not None:
            self._printers_fill_drivers(label, devices[i][0])

    def _printers_scan_usb(self):
        # `lpinfo -l -v` lists devices as blocks carrying the IEEE-1284 device-id
        # and make-and-model the printer reports about ITSELF. Those are the only
        # reliable keys for picking a driver — the URI label is just a decorated
        # product name and matching on it picks wrong drivers (see
        # _printers_match_drivers). The extras are stashed in _pr_devinfo keyed by
        # URI so the returned list stays (uri, label) pairs for existing callers.
        # The scan can be slow while a device wakes, so it is generously timed out
        # (and never raises — run() swallows everything).
        if not have("lpinfo"):
            return []
        import urllib.parse
        self._pr_devinfo = {}
        devices = []
        seen = set()
        rc, out = run(["lpinfo", "-l", "-v"], timeout=12)
        if rc == 0:
            uri = did = mm = info = None

            def _flush():
                if uri and uri.lower().startswith("usb:") and uri not in seen:
                    seen.add(uri)
                    label = (info or mm or "").strip() or \
                        self._printer_uri_label(uri, urllib.parse)
                    self._pr_devinfo[uri] = (did or "", mm or "")
                    devices.append((uri, label))

            for ln in out.splitlines():
                s = ln.strip()
                if s.startswith("Device:"):
                    _flush()
                    uri = did = mm = info = None
                    if "=" in s:
                        uri = s.split("=", 1)[1].strip()
                elif "=" in s:
                    k, v = s.split("=", 1)
                    k, v = k.strip().lower(), v.strip()
                    if k == "uri":
                        uri = v
                    elif k == "device-id":
                        did = v
                    elif k == "make-and-model":
                        mm = v
                    elif k == "info":
                        info = v
            _flush()
        if devices:
            return devices
        # Fall back to the bare two-field form if -l gave us nothing.
        rc, out = run(["lpinfo", "-v"], timeout=8)
        if rc != 0:
            return []
        for ln in out.splitlines():
            parts = ln.split(None, 1)
            if len(parts) != 2:
                continue
            _cls, uri = parts[0], parts[1].strip()
            if not uri.lower().startswith("usb:"):
                continue
            if uri in seen:
                continue
            seen.add(uri)
            self._pr_devinfo[uri] = ("", "")
            devices.append((uri, self._printer_uri_label(uri, urllib.parse)))
        return devices

    def _printer_uri_label(self, uri, urlparse):
        # Turn usb://Make/Model?serial=... into a friendly "Make Model".
        try:
            body = uri.split("://", 1)[1]
            body = body.split("?", 1)[0]
            segs = [urlparse.unquote(s) for s in body.split("/") if s]
            label = " ".join(segs).strip()
            return label or uri
        except Exception:
            return uri

    def _printer_default_name(self, label):
        # A CUPS queue name allows no spaces, '/', '#' or control chars; fold to
        # a safe token and never return empty.
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_")
        return name[:40] or "Printer"

    # Generic drivers shipped on the image, in the order they are offered as a
    # fallback. These are matched by ppd-name SUFFIX because cups-driverd names
    # /usr/share/ppd entries by their path relative to that directory
    # ("cupsfilters/pxlmono.ppd", not "pxlmono.ppd"), and only ones actually
    # present in `lpinfo -m` are offered — a hard-coded name that is not in the
    # model list turns Add into an outright failure.
    _GENERIC_PPDS = [
        ("cupsfilters/pxlmono.ppd", "Generic laser printer (PCL 6)"),
        ("cupsfilters/pxlcolor.ppd", "Generic colour laser printer (PCL 6)"),
        ("sample.drv/generic.ppd", "Generic PostScript printer"),
        ("sample.drv/generpcl.ppd", "Generic PCL laser printer"),
        ("sample.drv/laserjet.ppd", "Generic LaserJet (PCL 4/5)"),
    ]

    def _printers_fill_drivers(self, label, uri=None):
        combo = self._pr_drv_combo
        if combo is None:
            return
        combo.remove_all()
        self._pr_driver_ppds = []
        matches, confident = self._printers_match_drivers(label, uri)
        for ppd, desc in matches:
            combo.append_text(desc)
            self._pr_driver_ppds.append(ppd)
        if not self._pr_driver_ppds:
            # Nothing at all to offer — say so rather than leaving an empty combo
            # that silently adds a driverless queue a USB device can never use.
            combo.append_text("No driver found for this printer")
            self._pr_driver_ppds.append("")
        combo.set_active(0)
        # Be honest when the driver is a guess. Picking the wrong one produces a
        # queue that accepts jobs and prints nothing, which is the single most
        # confusing failure this page can cause.
        note = getattr(self, "_pr_drv_note", None)
        if note is not None:
            if confident:
                note.set_text("")
            else:
                note.set_text("Could not identify this printer exactly — a "
                              "generic driver is selected. Print a test page "
                              "to check it.")

    def _printers_match_drivers(self, label, uri=None):
        # Return (list of (ppd, description), confident). Ordering is:
        #   1. exact matches on the printer's own IEEE-1284 device-id
        #   2. exact matches on its reported make-and-model
        #   3. fuzzy matches that hit a MODEL token, not just the manufacturer
        #   4. the generic PCL/PostScript drivers shipped on the image
        #
        # The manufacturer-only fuzzy match this used to do is worse than no
        # match: "Brother HL-L2350DW" shares only the token "brother" with the
        # Gutenprint list, so ~40 models tied on score 1 and the old
        # `len(desc)` tiebreak picked the SHORTEST description — reliably the
        # oldest model in the family (a 1998 "Brother HL-10V"). That builds a
        # perfectly valid queue whose every filter succeeds while the printer
        # receives a command language it cannot parse and prints nothing.
        if not have("lpinfo"):
            return [], False
        did, mm = ("", "")
        if uri:
            did, mm = getattr(self, "_pr_devinfo", {}).get(uri, ("", ""))

        def _parse(text):
            got = []
            for ln in (text or "").splitlines():
                parts = ln.split(None, 1)
                if len(parts) == 2:
                    got.append((parts[0], parts[1].strip()))
            return got

        results = []
        seen = set()

        def _add(items):
            added = 0
            for ppd, desc in items:
                if ppd in seen:
                    continue
                seen.add(ppd)
                results.append((ppd, desc))
                added += 1
            return added

        # The unfiltered list first — it is also the yardstick for whether the
        # filtered queries below did anything at all.
        rc, out = run(["lpinfo", "-m"], timeout=20)
        all_models = _parse(out) if rc == 0 else []
        total = len(all_models)

        # 1/2. Ask CUPS to match against what the printer reports about itself.
        #
        # VERIFIED ON THIS IMAGE: cups-driverd accepts --device-id and
        # --make-and-model but does NOT filter — it returns the complete
        # ~3500-model list unchanged, even for a deliberately absurd device-id.
        # Taking that at face value would "exactly match" every driver in
        # existence and preselect whatever sorts first (an Apollo P-2100),
        # which is worse than not matching at all. So a filtered result is only
        # trusted when it actually NARROWED the list. The queries are kept
        # rather than deleted because they are correct where driverd honours
        # them, and the guard makes them harmless where it does not.
        exact = 0
        for flag, val in (("--device-id", did), ("--make-and-model", mm)):
            if not val:
                continue
            rc, out = run(["lpinfo", "-m", flag, val], timeout=12)
            if rc != 0:
                continue
            got = _parse(out)
            if got and (not total or len(got) < total):
                exact += _add(got)

        # 3. Fuzzy, CONSTRAINED TO THE MANUFACTURER.
        #
        # The manufacturer is the one token that must never be crossed: a driver
        # from the wrong vendor speaks the wrong language, full stop. Matching on
        # model digits alone does exactly that — "EPSON ET-2850" and "Canon PIXMA
        # iP2850" share "2850", and an unconstrained scorer happily pairs them.
        # (The previous scorer was worse still: it scored a bare manufacturer hit
        # and broke ties by shortest description, so every Brother became a 1998
        # HL-10V.)
        #
        # So: candidates must share the brand; among those, a model-token hit
        # (a token containing a digit) is what makes the choice trustworthy.
        # A brand-only match is still offered — an old Canon inkjet driver is a
        # far better guess for a new Canon inkjet than a generic laser one — but
        # it is NOT reported as confident, so the UI tells the user to test it.
        import difflib
        tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", label or "")
                  if t]
        # Single characters count as model tokens ("HP LaserJet 4") but never as
        # the brand.
        model_tokens = [t for t in tokens if any(c.isdigit() for c in t)]
        # Brand = the first token that is not a generic marketing word. Matched
        # against the driver list itself, so an unknown vendor finds nothing at
        # all rather than something from the wrong manufacturer.
        _NOISE = {"series", "printer", "print", "inc", "corp", "co", "ltd",
                  "the", "usb", "and", "mfp", "pro", "plus"}
        brand = next((t for t in tokens
                      if len(t) > 1 and t not in _NOISE), "")
        fuzzy = 0
        if brand and all_models:
            same_brand = [(p, d) for p, d in all_models if brand in d.lower()]
            low_label = (label or "").lower()
            scored = []
            for ppd, desc in same_brand:
                low = desc.lower()
                hits = sum(1 for t in model_tokens if t in low)
                # Overall string similarity against the model name (the CUPS
                # description minus its " - CUPS+Gutenprint v5.2.14" suffix).
                # This is what puts a TS3350 next to a TS3170 and a ZD421 on the
                # ZPL driver, instead of the alphabetically-first entry in the
                # brand — which landed a dot-matrix driver on an inkjet.
                ratio = difflib.SequenceMatcher(
                    None, low_label, low.split(" - ")[0]).ratio()
                scored.append((hits, ratio, ppd, desc))
            # Model-number hits first, then similarity. NEVER len(desc): that is
            # a proxy for how old the model name is, and sorting by it is what
            # made every Brother resolve to a 1998 HL-10V.
            scored.sort(key=lambda x: (-x[0], -x[1], x[3]))
            if scored:
                fuzzy = _add([(p, d) for _h, _r, p, d in scored[:8]])
                # Sharing only the brand is a guess, not an identification.
                if scored[0][0] == 0:
                    fuzzy = 0

        # 4. Generic fallbacks, always offered so there is a working choice even
        # for a printer no PPD names.
        if all_models:
            by_suffix = {}
            for ppd, desc in all_models:
                for suf, _friendly in self._GENERIC_PPDS:
                    if ppd.endswith(suf):
                        by_suffix.setdefault(suf, (ppd, desc))
            _add([(by_suffix[suf][0], friendly)
                  for suf, friendly in self._GENERIC_PPDS if suf in by_suffix])

        return results[:12], bool(exact or fuzzy)

    def _on_printer_add(self, _btn=None):
        entry = getattr(self, "_pr_name_entry", None)
        devcombo = getattr(self, "_pr_dev_combo", None)
        drvcombo = getattr(self, "_pr_drv_combo", None)
        devices = getattr(self, "_pr_devices", [])
        if entry is None or devcombo is None:
            return
        name = entry.get_text().strip()
        if not name:
            self._set_status(self._pr_add_status, "Enter a name", warn=True)
            return
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$", name):
            self._set_status(self._pr_add_status,
                             "Letters, numbers, - and _ only", warn=True)
            return
        di = devcombo.get_active()
        if not (0 <= di < len(devices)):
            self._set_status(self._pr_add_status, "No printer selected",
                             warn=True)
            return
        device = devices[di][0]
        ppds = getattr(self, "_pr_driver_ppds", [])
        gi_ = drvcombo.get_active() if drvcombo is not None else -1
        driver = ppds[gi_] if 0 <= gi_ < len(ppds) else ""
        if not driver:
            self._set_status(self._pr_add_status,
                             "No driver is available for this printer",
                             warn=True)
            return
        cmd = ["lpadmin", "-p", name, "-E", "-v", device, "-m", driver]
        rc, out = run(cmd, timeout=20)
        if rc != 0:
            # Keep the whole message: lpadmin's failures ("IPP Everywhere driver
            # requires an IPP connection.") are unintelligible truncated to 40.
            msg = out.strip().splitlines()[-1] if out.strip() else \
                "Could not add the printer"
            self._set_status(self._pr_add_status, msg, warn=True)
            return
        self._set_status(self._pr_add_status, "Added", warn=False)
        self._printers_refresh()

    # ---- per-printer actions ----
    def _on_printer_default(self, _btn, name):
        if have("lpoptions"):
            run(["lpoptions", "-d", name])
        else:
            run(["lpadmin", "-d", name])
        self._printers_refresh()

    def _on_printer_remove(self, _btn, name):
        self._confirm(
            "Remove printer",
            "Remove \"%s\"? You can add it again at any time." % name,
            "Remove", lambda: self._do_printer_remove(name))

    def _do_printer_remove(self, name):
        run(["lpadmin", "-x", name])
        self._printers_refresh()

    def _on_printer_test(self, btn, name):
        # Print a themed test page through the shared nbprint pipeline (falls
        # back to nothing but a status line if the spool fails). Non-blocking to
        # the extent that submit is a quick `lp` hand-off.
        import tempfile
        try:
            fd, path = tempfile.mkstemp(suffix=".pdf", prefix="nbtest-")
            os.close(fd)
            nbprint.simple_pdf(path, 1, self._draw_testpage,
                               w_pt=612, h_pt=792)
        except Exception:
            btn.set_label(_t("Test failed"))
            return
        ok, _msg = nbprint.submit_pdf(path, printer=name,
                                      job_name="Notebook OS test page")
        try:
            os.unlink(path)
        except OSError:
            pass
        btn.set_label(_t("Sent") if ok else "Test failed")
        GLib.timeout_add_seconds(4, self._reset_test_btn, btn)

    def _reset_test_btn(self, btn):
        try:
            btn.set_label(_t("Test page"))
        except Exception:
            pass
        return False

    def _draw_testpage(self, cr, _page_no, w, h):
        # An opaque page on white stock; only guaranteed Latin glyphs and a
        # cairo-drawn tonal bar (no exotic Unicode). Cheap: a handful of ops.
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgb(0.10, 0.10, 0.086)
        # hairline frame
        cr.set_line_width(1)
        cr.rectangle(48, 48, w - 96, h - 96)
        cr.stroke()
        # serif title
        try:
            cr.select_font_face("Liberation Serif",
                                0, 0)  # NORMAL slant/weight
        except Exception:
            pass
        cr.set_font_size(28)
        cr.move_to(72, 120)
        cr.show_text("Notebook OS")
        cr.set_font_size(18)
        cr.move_to(72, 152)
        cr.show_text("Printer Test Page")
        # sans body lines
        try:
            cr.select_font_face("Nimbus Sans", 0, 0)
        except Exception:
            pass
        cr.set_font_size(12)
        import time
        lines = [
            "If you can read this, your printer is working.",
            "Printed: " + time.strftime("%Y-%m-%d %H:%M"),
            "Alignment marks and a tonal bar are shown below.",
        ]
        y = 200
        for ln in lines:
            cr.move_to(72, y)
            cr.show_text(ln)
            y += 22
        # tonal bar: ten steps from light to dark to check greyscale output
        bx, by, bw, bh = 72, 260, w - 144, 26
        steps = 10
        for i in range(steps):
            g = 1.0 - (i / float(steps - 1))
            cr.set_source_rgb(g, g, g)
            cr.rectangle(bx + i * (bw / steps), by, bw / steps, bh)
            cr.fill()
        cr.set_source_rgb(0.10, 0.10, 0.086)
        cr.set_line_width(1)
        cr.rectangle(bx, by, bw, bh)
        cr.stroke()
        # signage-red accent rule, drawn (not a glyph)
        cr.set_source_rgb(0.784, 0.204, 0.118)  # #C8341E
        cr.rectangle(72, 172, 120, 3)
        cr.fill()

    # ---- Power ----
    def _page_power(self):
        outer, col = self._page("Power", "Battery, screen and switching off")

        bat = self._battery()
        # Head this group "Battery" only when there IS one. On a desktop the
        # single row already reads "Power — runs from the mains", and a "Power"
        # heading over it, on a page called Power, said the same word three
        # times; the card stands on its own instead (as on Keyboard).
        if bat[0][0].startswith("Battery"):
            self._grouplabel(col, "Battery")
        card = self._card(col)
        for i, (k, v) in enumerate(bat):
            self._value_row(card, k, v, first=(i == 0))

        self._grouplabel(col, "Screen")
        card2 = self._card(col)
        blank = Gtk.ComboBoxText()
        self._blank_opts = [("Never", 0), ("1 minute", 60), ("5 minutes", 300),
                            ("10 minutes", 600), ("30 minutes", 1800)]
        for label, _s in self._blank_opts:
            blank.append_text(label)
        saved = self._cfg_int("blank_timeout", 0)
        idx = next((i for i, (_l, s) in enumerate(self._blank_opts)
                    if s == saved), 0)
        blank.set_active(idx)
        blank.connect("changed", self._on_blank)
        self._row_widget(card2, "Blank screen after", blank, first=True)

        self._grouplabel(col, "Finish up")
        card3 = self._card(col)
        # Each row says what the button will DO — the label used to be the same
        # word as the button beside it, which told the user nothing twice.
        for i, (label, sub, action) in enumerate([
                ("Sleep", "Turn the screen off. Press any key to come back.",
                 "sleep"),
                ("Restart", "Close everything down and start the computer "
                            "again.", "reboot"),
                ("Shut Down", "Close everything down and switch the computer "
                              "off.", "poweroff")]):
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("setbtn")
            btn.connect("clicked", self._on_power, action)
            self._row_widget(card3, label, btn, first=(i == 0), sub=sub)

        self._note(col, "The screen normally stays on, so the computer is ready "
                        "the moment you walk up to it. Nothing is lost when the "
                        "screen goes dark — press any key to carry on.")
        return outer

    def _battery(self):
        base = "/sys/class/power_supply"
        try:
            entries = os.listdir(base)
        except OSError:
            return [("Power source", "AC power")]
        batteries, ac_online = [], None
        for e in sorted(entries):
            p = os.path.join(base, e)
            typ = self._first_line(os.path.join(p, "type"))
            if typ == "Battery":
                # Skip the batteries INSIDE peripherals — a wireless mouse or
                # keyboard reports scope=Device, and listing those put two
                # "Battery (hidpp_battery_0)" rows on the page of a desktop that
                # has no battery of its own, burying the real one on a laptop.
                # (shell.py's panel indicator filters the same way.)
                if self._first_line(os.path.join(p, "scope")) == "Device":
                    continue
                cap = self._first_line(os.path.join(p, "capacity"))
                status = self._first_line(os.path.join(p, "status"))
                plain = {"Charging": "Charging", "Discharging": "In use",
                         "Full": "Fully charged", "Not charging": "Not charging"}
                bits = ["%s%%" % cap] if cap else []
                if status:
                    bits.append(plain.get(status, status))
                batteries.append(("  ·  ".join(bits) or "Level unknown"))
            elif typ == "Mains":
                on = self._first_line(os.path.join(p, "online"))
                ac_online = (on == "1")
        rows = []
        for i, val in enumerate(batteries):
            # One battery is just "Battery"; a machine with two gets numbers,
            # never the kernel's device name.
            rows.append(("Battery" if len(batteries) == 1
                         else "Battery %d" % (i + 1), val))
        if ac_online is not None:
            rows.append(("Power lead", "Plugged in" if ac_online
                         else "Not plugged in"))
        if not rows:
            rows.append(("Power", "Runs from the mains — no battery fitted"))
        return rows

    def _on_blank(self, combo):
        i = combo.get_active()
        if not (0 <= i < len(self._blank_opts)):
            return
        secs = self._blank_opts[i][1]
        self._apply_blank(secs)
        self._settings["blank_timeout"] = secs
        self._save_settings()

    def _apply_blank(self, secs):
        try:
            secs = int(secs)
        except (TypeError, ValueError):
            secs = 0
        if secs > 0:
            run(["xset", "s", str(secs)])
            run(["xset", "+dpms"])
            run(["xset", "dpms", str(secs), str(secs), str(secs)])
        else:
            run(["xset", "s", "off"])
            run(["xset", "-dpms"])

    def _open_apps(self):
        """The apps that are open right now, by name.

        Every app runs as `python3 /opt/notebook/de/<app>.py`, so the machine
        can see exactly what is on the screen. "Unsaved work in any open app
        will be lost" is a warning nobody can act on — they would have to go
        and look. "Writer and Journal are open" is one they can."""
        try:
            import packages
            names = dict(packages._APP_NAMES)
        except Exception:
            names = {}
        me = os.getpid()
        # The desktop's own furniture runs for the whole session (see
        # session.sh); it is not something the user "has open", and Settings
        # itself is the window they are standing in.
        skip = ("finder", "widgets", "shell", "xflushd", "splash",
                "nbmediakeys", "desktopbg", "xrootbg", "xflush", "xnudge",
                "settings")
        found = []
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return found
        for p in pids:
            if int(p) == me:
                continue
            try:
                with open("/proc/%s/cmdline" % p, "rb") as fh:
                    args = [a for a in fh.read().split(b"\0") if a]
            except OSError:
                continue
            script = next((a for a in args if a.endswith(b".py")), None)
            if script is None:
                continue
            mod = os.path.basename(script.decode("utf-8", "replace"))[:-3]
            if mod in skip:
                continue
            name = names.get(mod)
            if name and name not in found:
                found.append(name)
        found.sort()
        return found

    def _open_apps_line(self):
        apps = self._open_apps()
        if not apps:
            return _t("Nothing else is open.")
        if len(apps) == 1:
            return _t("%s is open. Anything you have not saved in it will be "
                      "lost.") % apps[0]
        # Joined through whole-phrase keys, never a bare " and " fragment: a
        # key with edge spaces bakes the English spacing into the catalog and
        # stops matching the moment the spacing changes.
        if len(apps) > 4:
            listed = _t("%s and %d more") % (", ".join(apps[:4]), len(apps) - 4)
        else:
            listed = _t("%s and %s") % (", ".join(apps[:-1]), apps[-1])
        return _t("%s are open. Anything you have not saved in them will be "
                  "lost.") % listed

    def _on_power(self, _b, action):
        if action == "sleep":
            run(["xset", "dpms", "force", "off"])
        elif action == "reboot":
            self._confirm(
                "Restart", "%s %s" % (_t("Restart the computer now?"),
                                      self._open_apps_line()), "Restart",
                lambda: self._do_power("reboot"))
        elif action == "poweroff":
            self._confirm(
                "Shut Down", "%s %s" % (_t("Shut down the computer now?"),
                                        self._open_apps_line()), "Shut Down",
                lambda: self._do_power("poweroff"))

    def _do_power(self, action):
        try:
            subprocess.Popen([action])
        except OSError:
            pass

    # ---- Keyboard ----
    def _page_keyboard(self):
        outer, col = self._page("Keyboard", "Layout and key repeat")
        card = self._card(col)
        # The SAME layout list Region & Language offers (nbi18n owns it), so the
        # two pages can never disagree about which layout is in use — and so a
        # layout picked here is SAVED. This page used to keep its own list and
        # only ran setxkbmap, so the choice was silently forgotten on restart
        # and the other page went on showing the old one.
        self._kbd_combo = Gtk.ComboBoxText()
        self._kbd_codes = [c for c, _lbl in nbi18n.KEYBOARDS]
        for _c, lbl in nbi18n.KEYBOARDS:
            self._kbd_combo.append_text(lbl)
        cur = nbi18n.keyboard() or self._get_kbd_layout()
        self._kbd_combo.set_active(self._kbd_codes.index(cur)
                                   if cur in self._kbd_codes else 0)
        self._kbd_combo.connect("changed", self._on_kbd)
        self._row_widget(card, "Layout", self._kbd_combo, first=True,
                         sub="Which letters and symbols the keys type")

        self._grouplabel(col, "Holding a key down")
        card2 = self._card(col)
        # Repeat delay (ms before repeat starts) and rate (repeats/sec).
        self._kdelay = Gtk.SpinButton.new_with_range(100, 1000, 50)
        self._kdelay.set_value(self._cfg_int("kbd_delay", 500))
        self._kdelay.connect("value-changed", self._on_repeat)
        self._row_widget(card2, "Wait before repeating", self._kdelay,
                         first=True,
                         sub="Thousandths of a second — a bigger number waits "
                             "longer")
        self._krate = Gtk.SpinButton.new_with_range(5, 60, 1)
        self._krate.set_value(self._cfg_int("kbd_rate", 25))
        self._krate.connect("value-changed", self._on_repeat)
        self._row_widget(card2, "How fast it repeats", self._krate,
                         sub="Letters per second once it starts")

        self._kbd_note = self._note(col, _t(KBD_NOTE))
        self._sync_dual_note()
        return outer

    def _get_kbd_layout(self):
        rc, o = run(["setxkbmap", "-query"])
        if rc != 0:
            return ""
        for line in o.splitlines():
            if line.startswith("layout:"):
                return line.split(":", 1)[1].strip().split(",")[0]
        return ""

    def _on_kbd(self, combo):
        if getattr(self, "_suppress_kb", False):
            return
        i = combo.get_active()
        if not (0 <= i < len(self._kbd_codes)):
            return
        code = self._kbd_codes[i]
        nbi18n.set_keyboard(code)          # persist, so it survives a restart
        self._apply_keyboard(code)
        self._sync_dual_note()
        # keep the Region & Language page's picker (if built) in agreement
        rk = getattr(self, "_region_kb", None)
        if rk is not None and rk.get_active() != i:
            self._suppress_kb = True
            rk.set_active(i)
            self._suppress_kb = False

    def _on_repeat(self, _spin):
        delay = self._kdelay.get_value_as_int()
        rate = self._krate.get_value_as_int()
        self._apply_repeat(delay, rate)
        self._settings["kbd_delay"] = delay
        self._settings["kbd_rate"] = rate
        self._save_settings()

    def _apply_repeat(self, delay, rate):
        try:
            delay = int(delay)
            rate = int(rate)
        except (TypeError, ValueError):
            return
        run(["xset", "r", "rate", str(delay), str(rate)])

    # ---- Mouse ----
    def _page_mouse(self):
        outer, col = self._page("Mouse & Touchpad", "Pointer speed and scrolling")
        card = self._card(col)
        adj = Gtk.Adjustment(
            value=self._cfg_float("pointer_speed", 0.0),
            lower=-1.0, upper=1.0, step_increment=0.1)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        scale.set_size_request(280, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        # The raw number this slider carries runs -1 … +1, which reads as a
        # meaningless "0.0" beside the track. Show the setting in words instead.
        scale.connect("format-value", lambda _s, v: self._speed_word(v))
        scale.connect("value-changed", self._on_pointer_speed)
        self._row_widget(card, "Pointer speed", scale, first=True,
                         sub="How far the pointer travels when you move the "
                             "mouse")
        nat = Gtk.Switch()
        nat.set_active(bool(self._settings.get("natural_scroll", False)))
        nat.connect("state-set", self._on_natural_scroll)
        self._row_widget(card, "Reverse scrolling", nat,
                         sub="Turn this on to make the page move the opposite "
                             "way to the wheel")

        if not have("xinput"):
            self._note(col, "This computer cannot adjust the pointer directly, "
                            "but your choice is saved and will be used as soon "
                            "as it can.")
        else:
            self._note(col, "Applies to every mouse and touchpad you connect, "
                            "and is remembered next time you start up.")
        return outer

    def _speed_word(self, v):
        # Every word must be no longer than the two END words: a Gtk.Scale
        # reserves its value column from the formatted MINIMUM and MAXIMUM only,
        # so a longer word in the middle ("Standard") is drawn clipped.
        if v <= -0.6:
            return "Slowest"
        if v < -0.15:
            return "Slower"
        if v <= 0.15:
            return "Normal"
        if v < 0.6:
            return "Faster"
        return "Fastest"

    def _xinput_pointer_ids(self):
        rc, o = run(["xinput", "list"])
        if rc != 0:
            return []
        ids = []
        for ln in o.splitlines():
            if "pointer" not in ln.lower():
                continue
            if "master" in ln.lower():
                continue
            m = re.search(r"id=(\d+)", ln)
            if m:
                ids.append(m.group(1))
        return ids

    def _on_pointer_speed(self, scale):
        v = round(scale.get_value(), 2)
        self._apply_pointer_speed(v)
        self._settings["pointer_speed"] = v
        self._save_settings()

    def _apply_pointer_speed(self, v):
        for i in self._xinput_pointer_ids():
            run(["xinput", "set-prop", i, "libinput Accel Speed", str(v)])

    def _on_natural_scroll(self, _sw, state):
        self._apply_natural_scroll(bool(state))
        self._settings["natural_scroll"] = bool(state)
        self._save_settings()
        return False

    def _apply_natural_scroll(self, on):
        for i in self._xinput_pointer_ids():
            run(["xinput", "set-prop", i,
                 "libinput Natural Scrolling Enabled", "1" if on else "0"])

    # ---- Date & Time ----
    def _page_datetime(self):
        import time
        outer, col = self._page("Date & Time", "The clock and your time zone")

        card = self._card(col)
        self._dt_lbl = Gtk.Label(xalign=1)
        self._dt_lbl.get_style_context().add_class("setvalue")
        self._row_widget(card, "Current time", self._dt_lbl, first=True)

        self._tz_combo = Gtk.ComboBoxText()
        for label, _iana, _posix in TIMEZONES:
            self._tz_combo.append_text(label)
        idx = self._tz_index()
        self._tz_combo.set_active(idx)
        self._tz_combo.connect("changed", self._on_tz)
        self._row_widget(card, "Time zone", self._tz_combo)

        setcard = self._card(col, top=16)
        now = time.localtime()

        self._cal = Gtk.Calendar()
        self._cal.select_month(now.tm_mon - 1, now.tm_year)  # month is 0-based
        self._cal.select_day(now.tm_mday)
        # A labelled row like every other one on the page. Centred with no label
        # it left a wide blank column beside it and broke the label-left /
        # control-right rhythm the Time row below it keeps.
        self._row_widget(setcard, "Date", self._cal, first=True)

        timebox = Gtk.Box(spacing=8)
        self._hspin = Gtk.SpinButton.new_with_range(0, 23, 1)
        self._hspin.set_value(now.tm_hour)
        self._mspin = Gtk.SpinButton.new_with_range(0, 59, 1)
        self._mspin.set_value(now.tm_min)
        # Pad both boxes to two digits: a clock reading "19 : 5" is not a time
        # anyone recognises. (The "output" signal formats the display only; the
        # value stays a plain number.)
        for sp in (self._hspin, self._mspin):
            sp.connect("output", self._spin_two_digits)
        colon = Gtk.Label(label=":")
        colon.get_style_context().add_class("setlabel")
        setbtn = Gtk.Button(label=_t("Set Clock"))
        setbtn.get_style_context().add_class("setbtn")
        setbtn.connect("clicked", self._apply_datetime)
        for w in (self._hspin, colon, self._mspin, setbtn):
            timebox.pack_start(w, False, False, 0)
        self._row_widget(setcard, "Time", timebox)

        self._note(col, "This computer has no internet, so it cannot set its "
                        "own clock. Set the date and time here and it will keep "
                        "them — the time at the top confirms the change.")

        _lbl, iana, posix = TIMEZONES[idx]
        self._apply_tz(iana, posix)
        # One ticker for the live clock; torn down in _on_destroy so nothing
        # fires after the window is gone.
        if self._dt_source is None:
            self._dt_source = GLib.timeout_add_seconds(1, self._dt_tick)
        return outer

    def _spin_two_digits(self, spin):
        spin.set_text("%02d" % int(spin.get_value()))
        return True   # we drew the text; skip the default rendering

    def _dt_tick(self):
        import time
        lbl = getattr(self, "_dt_lbl", None)
        if lbl is None:
            self._dt_source = None
            return False
        try:
            lbl.set_text(time.strftime("%A %-d %B %Y  ·  %H:%M:%S"))
        except Exception:
            self._dt_source = None
            return False
        return True

    def _dt_resync_inputs(self):
        # After a zone change the "Set Clock" calendar/spinners should show the
        # NEW local time, so a subsequent Set doesn't write yesterday's hour.
        cal = getattr(self, "_cal", None)
        hs = getattr(self, "_hspin", None)
        ms = getattr(self, "_mspin", None)
        if cal is None or hs is None or ms is None:
            return
        import time
        now = time.localtime()
        try:
            cal.select_month(now.tm_mon - 1, now.tm_year)
            cal.select_day(now.tm_mday)
            hs.set_value(now.tm_hour)
            ms.set_value(now.tm_min)
        except Exception:
            pass

    def _tz_index(self):
        want = self._settings.get("tz") or self._detect_tz()
        for i, (_lbl, iana, _posix) in enumerate(TIMEZONES):
            if iana == want:
                return i
        return 0

    def _detect_tz(self):
        tz = os.environ.get("TZ", "")
        if tz:
            return tz.lstrip(":")
        try:
            if os.path.islink("/etc/localtime"):
                return os.readlink("/etc/localtime").split("zoneinfo/")[-1]
        except OSError:
            pass
        return "UTC"

    def _apply_tz(self, iana, posix):
        zi = "/usr/share/zoneinfo/" + iana
        if os.path.exists(zi):
            run(["ln", "-sf", zi, "/etc/localtime"])
            os.environ["TZ"] = iana
        else:
            os.environ["TZ"] = posix
        try:
            import time
            time.tzset()
        except Exception:
            pass
        if getattr(self, "_dt_lbl", None) is not None:
            self._dt_tick()
        self._dt_resync_inputs()

    def _on_tz(self, combo):
        i = combo.get_active()
        if not (0 <= i < len(TIMEZONES)):
            return
        _lbl, iana, posix = TIMEZONES[i]
        self._apply_tz(iana, posix)
        self._settings["tz"] = iana
        self._save_settings()
        # keep the Region page's zone combo (if built) in agreement
        rc = getattr(self, "_region_tz", None)
        if rc is not None and rc.get_active() != i:
            rc.set_active(i)

    def _apply_datetime(self, _btn=None):
        y, mon, day = self._cal.get_date()  # month is 0-based
        h = self._hspin.get_value_as_int()
        mi = self._mspin.get_value_as_int()
        stamp = "%04d-%02d-%02d %02d:%02d:00" % (y, mon + 1, day, h, mi)
        run(["date", "-s", stamp])
        self._dt_tick()

    # ---- Region & Language ----
    def _page_region(self):
        outer, col = self._page(_t("Region & Language"),
                                _t("Language, keyboard, and time zone"))
        card = self._card(col)
        # Three drop-downs stacked in one card: without a shared width their
        # left edges stepped in and out with whatever each one happened to
        # contain. One control column instead.
        ctl = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        # ---- UI language ----
        self._region_lang = Gtk.ComboBoxText()
        self._region_lang_codes = list(nbi18n.SUPPORTED)
        for code in self._region_lang_codes:
            self._region_lang.append_text(nbi18n.LANG_NAMES.get(code, code))
        cur = nbi18n.current_lang()
        self._region_lang.set_active(self._region_lang_codes.index(cur)
                                     if cur in self._region_lang_codes else 0)
        self._region_lang.connect("changed", self._on_region_lang)
        ctl.add_widget(self._region_lang)
        self._row_widget(card, _t("Language"), self._region_lang, first=True)
        # ---- keyboard layout ----
        self._region_kb = Gtk.ComboBoxText()
        self._region_kb_codes = [c for c, _lbl in nbi18n.KEYBOARDS]
        for _c, lbl in nbi18n.KEYBOARDS:
            self._region_kb.append_text(lbl)
        curkb = nbi18n.keyboard()
        self._region_kb.set_active(self._region_kb_codes.index(curkb)
                                   if curkb in self._region_kb_codes else 0)
        self._region_kb.connect("changed", self._on_region_kb)
        ctl.add_widget(self._region_kb)
        self._row_widget(card, _t("Keyboard layout"), self._region_kb)

        # Third row of the same card, not a card of its own under a "Time zone"
        # heading — that put the words "Time zone" twice in a row down the page.
        self._region_tz = Gtk.ComboBoxText()
        for label, _iana, _posix in TIMEZONES:
            self._region_tz.append_text(label)
        self._region_tz.set_active(self._tz_index())
        self._region_tz.connect("changed", self._on_region_tz)
        ctl.add_widget(self._region_tz)
        self._row_widget(card, _t("Time zone"), self._region_tz)

        self._region_note = self._note(col, _t(REGION_NOTE))
        self._sync_dual_note()
        return outer

    def _sync_dual_note(self):
        """Add (or drop) the Alt+Shift sentence to whichever notes are built.

        Appended to the existing note rather than shown in a label of its own:
        both halves are translated before they are joined, and one label that
        changes text cannot leave a blank strip on the page when it has nothing
        to say."""
        dual = "," in (nbi18n.keyboard() or "")
        for attr, base in (("_region_note", REGION_NOTE),
                           ("_kbd_note", KBD_NOTE)):
            lbl = getattr(self, attr, None)
            if lbl is None:
                continue
            text = _t(base)
            if dual:
                text += "  " + _t(KBD_DUAL_NOTE)
            lbl.set_text(text)

    def _apply_keyboard(self, code):
        # Through nbi18n, never a bare `setxkbmap <code>`: Russian ("ru,us")
        # and Hindi ("in,us") are DUAL layouts, and a two-layout string with no
        # switch key leaves the second half unreachable — which for those two
        # means no way to type a file name or a password. xkb_args() adds
        # Alt+Shift. session.sh applies the saved layout the same way.
        try:
            subprocess.Popen(nbi18n.xkb_args(code))
        except OSError:
            pass

    def _on_region_lang(self, combo):
        i = combo.get_active()
        codes = self._region_lang_codes
        if not (0 <= i < len(codes)):
            return
        nbi18n.set_lang(codes[i])
        kb = nbi18n.DEFAULT_KB.get(codes[i])
        kc = self._region_kb_codes
        if kb in kc:                          # follow language with its keyboard
            nbi18n.set_keyboard(kb)
            self._apply_keyboard(kb)
            self._suppress_kb = True
            self._region_kb.set_active(kc.index(kb))
            self._suppress_kb = False
            self._sync_kbd_combo(kc.index(kb))
        # Nothing on screen changes when the language is picked — every window
        # that is already open, this one included, keeps the language it was
        # started in. Say so on the spot, in the language just chosen, so the
        # choice visibly registered and the user knows what to do next.
        note = getattr(self, "_region_note", None)
        if note is not None:
            name = nbi18n.LANG_NAMES.get(codes[i], codes[i])
            cat = nbi18n._load_catalog(codes[i])
            text = ((cat.get(REGION_SET, REGION_SET) % name) + "\n\n"
                    + cat.get(REGION_NOTE, REGION_NOTE))
            # Picking Russian or Hindi is the moment the two-halves keyboard
            # appears, so the switch key is explained right there — and in the
            # language just chosen, like the rest of this message.
            if "," in (kb or ""):
                text += "  " + cat.get(KBD_DUAL_NOTE, KBD_DUAL_NOTE)
            note.set_text(text)
        kn = getattr(self, "_kbd_note", None)
        if kn is not None:
            self._sync_dual_note()

    def _on_region_kb(self, combo):
        if getattr(self, "_suppress_kb", False):
            return
        i = combo.get_active()
        codes = self._region_kb_codes
        if 0 <= i < len(codes):
            nbi18n.set_keyboard(codes[i])
            self._apply_keyboard(codes[i])
            self._sync_kbd_combo(i)
            self._sync_dual_note()

    def _sync_kbd_combo(self, i):
        # Both pickers list nbi18n.KEYBOARDS in the same order, so the index
        # carries straight across. Guarded so echoing the change back does not
        # re-fire the handler that set it.
        kc = getattr(self, "_kbd_combo", None)
        if kc is not None and kc.get_active() != i:
            self._suppress_kb = True
            kc.set_active(i)
            self._suppress_kb = False

    def _locale_lang(self):
        return (os.environ.get("LANG") or os.environ.get("LC_ALL")
                or "C (POSIX)")

    def _locale_charset(self):
        lang = os.environ.get("LANG", "")
        if "." in lang:
            return lang.split(".", 1)[1]
        rc, o = run(["locale", "charmap"])
        return o.strip() if rc == 0 and o.strip() else "ANSI_X3.4-1968"

    def _locale_full(self):
        return (os.environ.get("LC_ALL") or os.environ.get("LANG")
                or "C")

    def _on_region_tz(self, combo):
        i = combo.get_active()
        if not (0 <= i < len(TIMEZONES)):
            return
        _lbl, iana, posix = TIMEZONES[i]
        self._apply_tz(iana, posix)
        self._settings["tz"] = iana
        self._save_settings()
        tc = getattr(self, "_tz_combo", None)
        if tc is not None and tc.get_active() != i:
            tc.set_active(i)

    # ---- Users ----
    def _page_users(self):
        outer, col = self._page("Users", "Accounts on this computer")

        self._grouplabel(col, "Signed in as")
        card = self._card(col)
        cur = self._current_user()
        self._value_row(card, "Username", cur, first=True)

        # Full name (GECOS) — an editable field only when chfn is present;
        # otherwise it is shown read-only rather than as a permanently greyed
        # control, which would look half-baked.
        fullname = self._gecos(cur)
        if have("chfn"):
            self._fn_entry = Gtk.Entry()
            self._fn_entry.set_text(fullname)
            self._fn_entry.set_width_chars(20)
            self._fn_entry.connect("activate", self._on_fullname, cur)
            self._fn_entry.connect(
                "changed", lambda *_: self._set_status(self._fn_status, ""))
            fnbtn = Gtk.Button(label=_t("Apply"))
            fnbtn.get_style_context().add_class("setbtn")
            fnbtn.connect("clicked", self._on_fullname, cur)
            self._fn_status = self._field_status()
            fnbox = Gtk.Box(spacing=8)
            fnbox.pack_start(self._fn_entry, False, False, 0)
            fnbox.pack_start(fnbtn, False, False, 0)
            fnbox.pack_start(self._fn_status, False, False, 0)
            self._row_widget(card, "Full name", fnbox)
        else:
            self._value_row(card, "Full name", fullname or "—")

        self._grouplabel(col, "Everyone who can sign in")
        card2 = self._card(col)
        users = self._passwd_users()
        if users:
            for i, (name, _uidn, full) in enumerate(users):
                bits = []
                if full and full != name:
                    bits.append(full)
                if name == "root":
                    bits.append("Administrator")
                if name == cur:
                    bits.append("This is you")
                self._value_row(card2, name, "  ·  ".join(bits) or "—",
                                first=(i == 0))
        else:
            self._value_row(card2, "Accounts", "None", first=True)

        self._note(col, "Everyone listed here can sign in to this computer. "
                        "The administrator account can change anything on it, "
                        "so keep its password to yourself.")
        return outer

    def _current_user(self):
        rc, o = run(["id", "-un"])
        if rc == 0 and o.strip():
            return o.strip()
        return os.environ.get("USER") or os.environ.get("LOGNAME") or "root"

    def _gecos(self, user):
        try:
            with open("/etc/passwd") as fh:
                for ln in fh:
                    f = ln.split(":")
                    if len(f) >= 5 and f[0] == user:
                        return f[4].split(",")[0]
        except OSError:
            pass
        return ""

    def _on_fullname(self, _w, user):
        name = self._fn_entry.get_text().strip()
        # A colon would corrupt the /etc/passwd GECOS field — reject it plainly.
        if ":" in name:
            self._set_status(self._fn_status, "No colons in a name", warn=True)
            return
        # chfn -f sets the GECOS full-name field; report whether it took.
        rc, _ = run(["chfn", "-f", name, user])
        if rc == 0:
            self._set_status(self._fn_status, "Saved", warn=False)
        else:
            self._set_status(self._fn_status, "Could not update", warn=True)

    def _passwd_users(self):
        out = []
        try:
            with open("/etc/passwd") as fh:
                for ln in fh:
                    f = ln.split(":")
                    if len(f) < 7:
                        continue
                    try:
                        uid = int(f[2])
                    except ValueError:
                        continue
                    # uid 0 (the administrator) plus real people. 65534 is
                    # "nobody", a placeholder the system uses for unprivileged
                    # work — listing it as an account someone could sign in
                    # with was simply wrong.
                    if uid == 0 or 1000 <= uid < 60000:
                        out.append((f[0], str(uid), f[4].split(",")[0]))
        except OSError:
            pass
        return out

    # ---- Storage ----
    def _page_storage(self):
        outer, col = self._page("Storage", "How much space is left on this computer")

        self._grouplabel(col, "This computer's disk")
        card = self._card(col)
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            frac = (used / total) if total else 0
            bar = Gtk.ProgressBar()
            bar.set_fraction(frac)
            # Neutral fill until the disk is nearly full, then the bar turns red
            # as a genuine alert (red is never merely decorative).
            if frac >= 0.9:
                bar.get_style_context().add_class("nearfull")
            bar.set_show_text(True)
            bar.set_text("%s used of %s  ·  %d%%"
                         % (human_kb(used // 1024), human_kb(total // 1024),
                            int(frac * 100)))
            barrow = Gtk.Box()
            barrow.get_style_context().add_class("setitem")
            bar.set_hexpand(True)
            barrow.pack_start(bar, True, True, 0)
            card.pack_start(barrow, False, False, 0)
        except OSError:
            self._value_row(card, "Disk", "Cannot be read", first=True)

        mounts = self._mounts()
        if mounts:
            self._grouplabel(col, "Other drives and memory sticks")
            card2 = self._card(col)
            for i, (mp, used_kb, total_kb) in enumerate(mounts):
                # Same sentence and same units as the disk bar above it: df's
                # own "9.0M (1%) of 975M" put two different number styles on
                # one page.
                pct = int(used_kb * 100 / total_kb) if total_kb else 0
                self._value_row(card2, mp,
                                "%s used of %s  ·  %d%%"
                                % (human_kb(used_kb), human_kb(total_kb), pct),
                                first=(i == 0))

        self._note(col, "These figures are read fresh every time you open this "
                        "page. A memory stick or external drive appears here "
                        "while it is plugged in.")
        return outer

    def _mounts(self):
        # -k (1K blocks), not -h: the page formats its own sizes with human_kb
        # so every figure on it reads the same way.
        rc, o = run(["df", "-k"])
        if rc != 0:
            # plain df is 1K blocks anyway on both busybox and coreutils; only
            # the flag could be refused, and losing the whole "other drives"
            # list over a flag would hide a plugged-in memory stick.
            rc, o = run(["df"])
        if rc != 0:
            return []
        out = []
        for ln in o.splitlines()[1:]:
            f = ln.split()
            if len(f) < 6:
                continue
            src, size, used, mp = f[0], f[1], f[2], f[5]
            # skip pseudo/virtual filesystems and the root (already shown)
            if mp == "/" or src in ("tmpfs", "devtmpfs", "proc", "sysfs",
                                    "none", "overlay"):
                continue
            # /boot* is the machine's own start-up partition, not a drive the
            # user plugged in — it does not belong under "Other drives".
            if mp.startswith(("/proc", "/sys", "/dev", "/run", "/boot")):
                continue
            try:
                out.append((mp, int(used), int(size)))
            except ValueError:
                continue
        return out

    # ---- Backup ----
    # There is no network and there is one disk, so a USB stick is the only way
    # a person's work can ever leave this machine — and until now nothing in
    # the system offered to put it there, or to say afterwards whether it
    # arrived. This page copies the lot and then checks it, which is the half
    # people usually have to take on faith.
    #
    # It only ever COPIES. Nothing already on the stick is touched, and each
    # run writes its own dated folder, so a backup can never quietly replace a
    # better one — and the folders inside it are the user's own folder names,
    # so putting something back is a drag in the Finder, not a restore tool.
    def _page_backup(self):
        outer, col = self._page("Backup",
                                "Copy your files to a USB stick")

        self._grouplabel(col, "What gets copied")
        card = self._card(col)
        self._bk_what = self._value_row(
            card, "Your folders and app data", _t("Working out the size…"),
            first=True)
        self._bk_what_lbl = self._bk_what.get_children()[-1]
        self._bk_total = 0
        self._bk_files = 0
        self._measure_backup()

        self._grouplabel(col, "Where to copy it")
        self._bk_dest_card = self._card(col)
        self._bk_dest = None
        self._bk_rows = []

        act = Gtk.Box(spacing=12)
        act.set_margin_top(20)
        self._bk_btn = Gtk.Button(label=_t("Copy my files"))
        self._bk_btn.get_style_context().add_class("setprimary")
        self._bk_btn.connect("clicked", self._on_backup_start)
        act.pack_start(self._bk_btn, False, False, 0)
        rescan = Gtk.Button(label=_t("Look again"))
        rescan.get_style_context().add_class("setbtn")
        rescan.connect("clicked", lambda *_: self._refresh_backup_dests())
        act.pack_start(rescan, False, False, 0)
        col.pack_start(act, False, False, 0)

        # Progress and outcome share one slot, rebuilt when it changes: they
        # are two views of the same thing (what the copy is doing / what it
        # did), only one is ever true at a time, and a page that grows a
        # permanent empty strip for a bar nobody is watching is untidy. Same
        # rebuild-then-show_all idiom as the Printers list.
        self._bk_bar = None
        self._bk_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._bk_status.set_margin_top(16)
        col.pack_start(self._bk_status, False, False, 0)

        self._note(col, "Every copy goes into its own dated folder, so an "
                        "older backup is never written over. Nothing already "
                        "on the stick is changed or removed. To put something "
                        "back later, open the stick in Finder and drag it "
                        "across.")
        self._refresh_backup_dests()
        return outer

    def _bk_clear_status(self):
        for ch in self._bk_status.get_children():
            self._bk_status.remove(ch)
        self._bk_bar = None

    def _usb_media(self):
        """USB sticks and other removable volumes, read live from /proc/mounts.

        automount.sh mounts them under /media/<label>, so that prefix is what
        makes a volume removable — never the system disk, which must not be
        offered as somewhere to keep the only copy of anything."""
        out = []
        real_fs = {"vfat", "exfat", "ntfs", "ntfs3", "msdos", "udf",
                   "ext2", "ext3", "ext4", "f2fs"}
        try:
            with open("/proc/mounts") as fh:
                for ln in fh:
                    p = ln.split()
                    if len(p) < 4 or not p[1].startswith("/media/"):
                        continue
                    if p[2] not in real_fs:
                        continue
                    # /proc/mounts octal-escapes a space in a volume label
                    mnt = (p[1].replace("\\040", " ").replace("\\011", "\t")
                           .replace("\\134", "\\"))
                    readonly = "ro" in p[3].split(",")
                    out.append((os.path.basename(mnt) or mnt, mnt, readonly))
        except OSError:
            pass
        out.sort()
        return out

    def _refresh_backup_dests(self):
        card = self._bk_dest_card
        for ch in card.get_children():
            card.remove(ch)
        self._bk_rows = []
        media = self._usb_media()
        if not media:
            # The empty state IS the instruction: say the one thing to do.
            self._value_row(card, "No USB stick found",
                            _t("Plug one in, then press Look again"),
                            first=True)
            self._bk_dest = None
        else:
            anchor = Gtk.RadioButton()      # hidden group leader, see installer
            for i, (label, mnt, readonly) in enumerate(media):
                rb = Gtk.RadioButton.new_from_widget(anchor)
                rb.set_active(False)
                rb.set_label(label)
                lbl = rb.get_child()
                if isinstance(lbl, Gtk.Label):
                    lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    lbl.set_max_width_chars(28)
                rb.connect("toggled", self._on_backup_dest, mnt, readonly)
                if readonly:
                    rb.set_sensitive(False)
                    free = _t("Write-protected — nothing can be copied to it")
                else:
                    # _t() explicitly: the automatic pass needs about five
                    # letters of fixed text to be sure it has recognised a
                    # substituted phrase, and "free" is four.
                    free = _t("%s free") % human_kb(self._free_kb(mnt))
                # Built by hand rather than through _row_widget: the stick's
                # name IS the row's label, so it belongs on the left with the
                # radio, and the free space is the value on the right.
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                row.get_style_context().add_class("setitem")
                if i:
                    row.get_style_context().add_class("bordered")
                row.pack_start(rb, False, False, 0)
                fl = Gtk.Label(label=free, xalign=1)
                fl.get_style_context().add_class("setvalue")
                fl.set_line_wrap(True)
                fl.set_max_width_chars(34)
                row.pack_end(fl, False, False, 0)
                card.pack_start(row, False, False, 0)
                self._bk_rows.append((rb, mnt))
            if len(media) == 1 and not media[0][2]:
                # one stick, one obvious answer: choose it, so the common case
                # is a single click on the button and nothing else
                self._bk_rows[0][0].set_active(True)
        card.show_all()
        self._update_backup_button()

    def _free_kb(self, path):
        try:
            st = os.statvfs(path)
            return st.f_bavail * st.f_frsize // 1024
        except OSError:
            return 0

    def _on_backup_dest(self, btn, mnt, readonly):
        if btn.get_active() and not readonly:
            self._bk_dest = mnt
            self._update_backup_button()

    def _update_backup_button(self):
        ready = bool(self._bk_dest) and not getattr(self, "_bk_working", False)
        self._bk_btn.set_sensitive(ready)

    # -- measuring (worker thread: a Pictures folder can hold thousands) --
    def _measure_backup(self):
        threading.Thread(target=self._measure_worker, daemon=True).start()

    def _measure_worker(self):
        files, total = self._walk_size()
        GLib.idle_add(self._measure_done, files, total)

    def _walk_size(self):
        files = total = 0
        for src in self._backup_sources():
            for root, _dirs, names in os.walk(src):
                for n in names:
                    p = os.path.join(root, n)
                    try:
                        if os.path.islink(p):
                            continue
                        total += os.path.getsize(p)
                        files += 1
                    except OSError:
                        continue
        return files, total

    def _backup_sources(self):
        out = []
        for d in BACKUP_DIRS + (APP_DATA_DIR,):
            p = os.path.join(HOME, d)
            if os.path.isdir(p):
                out.append(p)
        return out

    def _measure_done(self, files, total):
        if not getattr(self, "_alive", True):
            return False
        self._bk_files, self._bk_total = files, total
        if files:
            self._bk_what_lbl.set_text(
                _t("%d files  ·  %s") % (files, human_kb(total // 1024)))
        else:
            self._bk_what_lbl.set_text(
                _t("Nothing to copy yet — no files saved on this computer"))
        self._update_backup_button()
        return False

    # -- the copy itself --
    def _on_backup_start(self, _btn=None):
        if getattr(self, "_bk_working", False) or not self._bk_dest:
            return
        free = self._free_kb(self._bk_dest) * 1024
        # Check for room BEFORE writing anything: half a backup that stopped
        # when the stick filled up is worse than being told it will not fit,
        # because it looks finished.
        if self._bk_total and free and free < self._bk_total * 1.02:
            self._show_backup_result(
                _t("There is not enough room on that stick. Your files need "
                   "%s and it has %s free.")
                % (human_kb(self._bk_total // 1024), human_kb(free // 1024)),
                warn=True)
            return
        self._bk_working = True
        self._update_backup_button()
        self._bk_clear_status()
        self._bk_bar = Gtk.ProgressBar()
        self._bk_bar.set_show_text(True)
        self._bk_bar.set_fraction(0.0)
        self._bk_bar.set_text(_t("Starting…"))
        self._bk_status.pack_start(self._bk_bar, False, False, 0)
        self._bk_status.show_all()
        threading.Thread(target=self._backup_worker,
                         args=(self._bk_dest,), daemon=True).start()

    def _backup_dest_dir(self, mnt):
        # A dated folder per run, never a name that could already hold someone
        # else's backup. A second copy on the same day gets "(2)", so two runs
        # an hour apart can never merge into one confusing folder.
        base = "%s %s" % (BACKUP_PREFIX, time.strftime("%Y-%m-%d"))
        dest = os.path.join(mnt, base)
        n = 2
        while os.path.exists(dest) and n < 100:
            dest = os.path.join(mnt, "%s (%d)" % (base, n))
            n += 1
        return dest

    def _backup_worker(self, mnt):
        copied = done_bytes = 0
        last_pct = -1
        failed = []
        try:
            dest = self._backup_dest_dir(mnt)
            os.makedirs(dest)
        except OSError as e:
            GLib.idle_add(self._backup_failed,
                          _t("Could not start: %s") % (e.strerror or e), 0)
            return
        for src in self._backup_sources():
            top = os.path.join(dest, os.path.basename(src)
                               if not src.endswith(APP_DATA_DIR)
                               else "App data")
            for root, _dirs, names in os.walk(src):
                rel = os.path.relpath(root, src)
                outdir = top if rel == "." else os.path.join(top, rel)
                try:
                    os.makedirs(outdir, exist_ok=True)
                except OSError as e:
                    failed.append((root, e))
                    continue
                for n in names:
                    sp = os.path.join(root, n)
                    if os.path.islink(sp):
                        continue
                    try:
                        sz = os.path.getsize(sp)
                        shutil.copy2(sp, os.path.join(outdir, n))
                        copied += 1
                        done_bytes += sz
                    except OSError as e:
                        failed.append((sp, e))
                        # A full or disconnected stick fails on every remaining
                        # file; stop and say so once rather than 900 times.
                        if e.errno in (28, 30, 5, 6):   # ENOSPC EROFS EIO ENXIO
                            GLib.idle_add(self._backup_failed,
                                          self._copy_error(e), copied)
                            return
                    # One update per whole percent, not per file. A home
                    # folder with 40,000 photos in it would otherwise queue
                    # 40,000 idle callbacks onto the main loop and make the
                    # window that is meant to be showing progress stop
                    # answering the mouse.
                    if self._bk_total:
                        frac = done_bytes / float(self._bk_total)
                        pct = int(frac * 100)
                        if pct != last_pct:
                            last_pct = pct
                            GLib.idle_add(self._backup_progress, frac, n)
        # Flush to the device before claiming anything landed on it.
        run(["sync"], timeout=60)
        GLib.idle_add(self._backup_verify, dest, copied, done_bytes,
                      len(failed))

    def _copy_error(self, e):
        if e.errno == 28:
            return _t("The stick ran out of room part-way through.")
        if e.errno == 30:
            return _t("That stick is write-protected, so nothing can be "
                      "copied onto it.")
        return _t("The stick stopped responding — it may have been pulled "
                  "out.")

    def _backup_progress(self, frac, name):
        if not getattr(self, "_alive", True) or self._bk_bar is None:
            return False
        frac = max(0.0, min(1.0, frac))
        self._bk_bar.set_fraction(frac)
        self._bk_bar.set_text("%d%%  ·  %s" % (int(frac * 100), name))
        return False

    def _backup_failed(self, why, copied):
        if not getattr(self, "_alive", True):
            return False
        self._bk_working = False
        self._update_backup_button()
        self._show_backup_result(
            "%s %s" % (why, _t("%d files were copied before it stopped.")
                       % copied), warn=True)
        return False

    def _backup_verify(self, dest, copied, done_bytes, failed):
        """Read back what was written and compare it with what was sent.

        "It said it copied them" is not the same as "they are on the stick",
        and this is the only chance anyone gets to find out before the machine
        they came from is gone."""
        if not getattr(self, "_alive", True):
            return False
        self._bk_working = False
        self._update_backup_button()
        there = there_bytes = 0
        for root, _dirs, names in os.walk(dest):
            for n in names:
                try:
                    there_bytes += os.path.getsize(os.path.join(root, n))
                    there += 1
                except OSError:
                    continue
        where = os.path.basename(dest)
        if there == copied and there_bytes == done_bytes and not failed:
            self._show_backup_result(
                _t("Copied and checked: %d files (%s) are on the stick, in a "
                   "folder called \"%s\". You can take it out now.")
                % (there, human_kb(there_bytes // 1024), where))
        else:
            self._show_backup_result(
                _t("%d of %d files were copied. Some could not be read or did "
                   "not arrive, so this is not a complete backup — try again, "
                   "or copy the missing folders by hand in Finder.")
                % (there, copied + failed), warn=True)
        return False

    def _show_backup_result(self, text, warn=False):
        # Replaces the progress bar in the shared status slot (see
        # _page_backup): the outcome is the whole point of the page, so it is
        # ink rather than one more grey footnote, and signage red when the news
        # is bad.
        self._bk_clear_status()
        lbl = Gtk.Label(label=text, xalign=0)
        ctx = lbl.get_style_context()
        ctx.add_class("setresult")
        if warn:
            ctx.add_class("setwarn")
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(92)
        lbl.set_halign(Gtk.Align.START)
        self._bk_status.pack_start(lbl, False, False, 0)
        self._bk_status.show_all()

    # ---- Accessibility ----
    def _page_accessibility(self):
        outer, col = self._page("Accessibility",
                                "Text size and contrast, in every app")
        card = self._card(col)
        self._pref_switch(card, "Large text", "large_text", False, first=True,
                          sub="Make the smallest text bigger, in every app",
                          on_change=lambda _v: self._apply_accessibility())
        self._pref_switch(card, "High contrast", "high_contrast", False,
                          sub="Deepen faint text and strengthen lines",
                          on_change=lambda _v: self._apply_accessibility())
        self._note(col, "Both settings apply to every app, not only this "
                        "window. An app that is already open keeps the look it "
                        "started with until you close it and open it again.")
        return outer

    def _apply_accessibility(self):
        # nbapp owns the mechanism, and deliberately: it reads these same two
        # keys as EVERY app starts, so the choice reaches Writer, the Finder and
        # Journal instead of only this window. (It used to be applied here, as
        # a stylesheet scoped to .setlabel / .setvalue / .settitle — class names
        # that exist nowhere but Settings, so the one place the setting had no
        # effect on was every place the user actually reads.)
        #
        # The call also restyles THIS process on the spot, which is the point of
        # doing it on toggle rather than only at startup: a text size you cannot
        # see is one you cannot judge.
        nbapp.a11y_set(bool(self._settings.get("large_text", False)),
                       bool(self._settings.get("high_contrast", False)))

    # ---- Default Applications ----
    def _page_defaultapps(self):
        outer, col = self._page("Default Applications",
                                "Which app opens each file type")
        card = self._card(col)
        current = self._settings.get("default_apps", {})
        if not isinstance(current, dict):
            current = {}
        self._da_combos = []
        for i, (label, exts, default_mod) in enumerate(DEFAULT_APP_CATEGORIES):
            combo = Gtk.ComboBoxText()
            for mod, disp in APP_CHOICES:
                combo.append_text(disp)
            # current choice = the stored module for the first extension, else
            # the built-in default for this category.
            chosen = current.get(exts[0], default_mod)
            mods = [m for m, _d in APP_CHOICES]
            combo.set_active(mods.index(chosen) if chosen in mods else 0)
            combo.connect("changed", self._on_defaultapp, exts)
            self._row_widget(card, label, combo, first=(i == 0))
            self._da_combos.append((exts, combo))
        self._note(col, "Double-click a file in Finder and it opens in the app "
                        "you choose here.")
        return outer

    def _on_defaultapp(self, combo, exts):
        i = combo.get_active()
        mods = [m for m, _d in APP_CHOICES]
        if not (0 <= i < len(mods)):
            return
        mod = mods[i]
        mapping = self._settings.get("default_apps", {})
        if not isinstance(mapping, dict):
            mapping = {}
        for ext in exts:
            mapping[ext] = mod
        self._settings["default_apps"] = mapping
        self._save_settings()

    # ---- About ----
    def _page_about(self):
        outer, col = self._page("About This Notebook", "System information")

        # Brand mark: the snail logo (the same asset the menu bar carries)
        # beside the OS name, per the design language.
        brand = Gtk.Box(spacing=14)
        brand.get_style_context().add_class("setabout")
        try:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                "/opt/notebook/logo.png", 46, 24, True)
            brand.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
        except Exception:
            pass
        nm = Gtk.Label(label=self._os_name(), xalign=0)
        nm.get_style_context().add_class("setabout-name")
        brand.pack_start(nm, False, False, 0)
        col.pack_start(brand, False, False, 0)

        card = self._card(col, top=10)
        rows = self._about_rows()
        for i, (k, v) in enumerate(rows):
            self._value_row(card, k, v, first=(i == 0))
        return outer

    def _about_rows(self):
        # The OS name is shown in the masthead above, so it is not repeated here.
        kernel = self._first_line("/proc/sys/kernel/osrelease")
        host = self._hostname()
        total, _used = self._mem_info()
        disk = ""
        try:
            st = os.statvfs("/")
            disk = "%s free of %s" % (
                human_kb(st.f_bavail * st.f_frsize // 1024),
                human_kb(st.f_blocks * st.f_frsize // 1024))
        except OSError:
            pass
        # No "Network" row: this kernel has no IP stack at all, so there is
        # nothing to report. The row used to read "Offline by design", which is
        # product copy rather than a machine fact — the System page states what
        # this machine IS, not what it is meant to be.
        #
        # Version leads: it is the one thing people come to an About page for,
        # and it was the one thing missing. "Device name" matches what the
        # System page calls the very same setting (it said "Hostname" here).
        rows = [("Version", nbapp.nb_version()),
                ("Kernel", kernel or "—"), ("Device name", host),
                ("Memory", human_kb(total) if total else "—"),
                ("Storage", disk or "—"), ("Switched on for", self._uptime())]
        built = nbapp.os_release_field("BUILD_ID")
        if built:
            rows.append(("Built", built))
        return rows

    # ---- css ----
    def _install_css(self):
        css = b"""
        /* ---- layout surfaces ---- */
        .setbody { background: #FCFBF8; }
        .setbody scrolledwindow, .setbody viewport { background: #FCFBF8; }
        /* each Gtk.Stack page pane (see _build_page): paper, because the Stack
           itself will not paint and its bare window is black on this stack. */
        .setpane { background: #FCFBF8; }
        /* The page column carries the paper for the whole pane; its padding
           (was widget margins) keeps that paper filling edge-to-edge so no
           black viewport/stack window peeks around the content. */
        .setpage { padding: 30px 40px; }

        /* ---- sidebar ---- */
        .setsidebar { background: #EFEBE0; border-right: 1px solid #D7D2C5;
                      padding: 24px 14px 16px; }
        .setsidebar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .setsidescroll { background: transparent; }
        .setseclabel { font-size: 11px; font-weight: 700; letter-spacing: 0.16em;
                       color: #9A9484; margin: 0 0 10px 12px; }
        /* 8px, not 9: seventeen sections have to sit in the 650px a 1024x740
           panel gives the sidebar, and one clipped row at the bottom of an
           otherwise complete list reads as breakage. 36px rows are still a
           comfortable target and match the Finder's sidebar density. */
        .setrow { padding: 8px 12px; margin: 1px 0; border-radius: 2px;
                  background: transparent; border: none;
                  border-left: 3px solid transparent; box-shadow: none;
                  font-size: 14px; color: #1A1916; }
        .setrow:hover { background: #EAE5D8; }
        .setrow.selected { background: #EAE3D2; color: #1A1916; font-weight: 600;
                           border-left: 3px solid #C8341E; }

        /* ---- page ---- */
        .setpage { background: #FCFBF8; }
        .setpage * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        /* editorial serif headings, per the design language + mockup */
        .settitle { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    font-size: 34px; font-weight: 500; color: #1A1916;
                    letter-spacing: -0.01em; margin-bottom: 6px; }
        .setsubtitle { font-size: 14px; color: #79736A; margin-bottom: 2px; }
        .setrule { background: #1A1916; min-height: 1px; margin-top: 14px;
                   margin-bottom: 22px; }
        .setgroup { font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
                    color: #9A9484; margin: 20px 2px 8px; }

        /* ---- About masthead ---- */
        .setabout { margin: 2px 2px 0; }
        .setabout-name { font-family: "Nimbus Sans","Helvetica",sans-serif;
                    font-size: 22px; font-weight: 700; color: #1A1916; }

        /* ---- card / rows ---- */
        .setcard { background: #F4F2EC; border: 1px solid #D7D2C5;
                   border-radius: 2px; padding: 2px 22px;
                   box-shadow: 0 1px 3px rgba(26,25,22,0.05); }
        .setitem { padding: 16px 2px; min-height: 30px; }
        .setitem.bordered { border-top: 1px solid #D7D2C5; }
        .setlabel { font-size: 14.5px; color: #1A1916; }
        .setsublabel { font-size: 12px; color: #9A9484; }
        .setvalue { font-size: 14.5px; color: #6E695E; }
        /* inline apply-feedback: alert red only when something's wrong */
        .setwarn { color: #C8341E; }
        /* 'Default' printer badge - signage red, opaque, safe on no-compositor */
        .setbadge { background: #C8341E; color: #FCFBF8; font-size: 11px;
                    font-weight: 600; padding: 2px 8px; border-radius: 2px; }
        .setnote  { font-size: 12.5px; color: #9A9484; margin-top: 16px;
                    margin-left: 2px; }
        /* the outcome of something the user asked for and waited on: ink, not
           the grey of the explanatory notes it sits beside. .setwarn recolours
           it to signage red when the news is bad. */
        .setresult { font-size: 14px; color: #1A1916; margin-left: 2px; }
        /* both are single-class rules, so without this the later one silently
           wins and a genuine problem is reported in calm ink */
        .setresult.setwarn { color: #C8341E; }

        /* ---- secondary buttons ---- */
        .setbtn { padding: 6px 18px; background: #FCFBF8; color: #1A1916;
                  border: 1px solid #C9C4B6; border-radius: 2px;
                  box-shadow: none; font-size: 13.5px; }
        .setbtn:hover  { background: #F1EEE6; }
        .setbtn:active { background: #EAE5D8; }

        /* ---- destructive-confirm overlay ---- */
        /* The scrim MUST carry a background: an EventBox owns a GdkWindow, and
           an unstyled one paints nothing. The page behind stayed fully lit (so
           the card read as a floating glitch rather than a modal), and on the
           no-compositor stack an unpainted child window shows through black.
           Same value the installer's .inst-scrim uses. */
        .setconfirm-scrim { background: rgba(26,25,22,0.32); }
        .setconfirm { background: #FCFBF8; border: 1px solid #1A1916;
                  padding: 26px 30px;
                  font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .setconfirm-h { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                  font-size: 22px; color: #1A1916; }
        .setconfirm-b { font-size: 14px; color: #6E695E; }
        .setprimary { padding: 6px 18px; background: #C8341E; color: #FCFBF8;
                  border: 1px solid #C8341E; border-radius: 2px;
                  box-shadow: none; font-size: 13.5px; }
        .setprimary:hover  { background: #B12F1B; }
        .setprimary:active { background: #9C2917; }
        /* a primary that cannot be pressed yet must not still read as the
           full-strength action (the Backup button before a stick is chosen) */
        .setprimary:disabled { background: #E0B8B0; border-color: #E0B8B0;
                    color: #FCFBF8; }

        /* ---- backdrop swatches ---- */
        .swatch { border: 1px solid #C9C4B6; border-radius: 2px;
                  box-shadow: none; padding: 0; }
        .swatch:hover { border-color: #9A9484; }
        .swatch.selected { border: 2px solid #C8341E; }

        /* ---- native controls ---- */
        .setpage combobox button.combo { background: #FCFBF8; color: #1A1916;
                  border: 1px solid #C9C4B6; border-radius: 2px;
                  box-shadow: none; padding: 4px 10px; }
        .setpage combobox button.combo:hover { background: #F1EEE6; }
        .setpage entry { background: #FCFBF8; color: #1A1916;
                  border: 1px solid #C9C4B6; border-radius: 2px;
                  box-shadow: none; padding: 4px 8px; }
        .setpage entry:focus { border-color: #9A9484; }

        .setpage scale { margin-top: 0; margin-bottom: 0; }
        .setpage scale trough { background: #DDD8CB; border: none;
                  border-radius: 2px; min-height: 5px; }
        .setpage scale highlight { background: #1A1916; border-radius: 2px; }
        .setpage scale slider { background: #FCFBF8; border: 1px solid #C9C4B6;
                  border-radius: 50%; min-width: 16px; min-height: 16px;
                  box-shadow: 0 1px 2px rgba(26,25,22,0.14); }
        .setpage scale value { color: #6E695E; font-size: 13px; }

        .setpage switch { background: #D7D2C5; border: 1px solid #C9C4B6;
                  box-shadow: none; }
        .setpage switch:checked { background: #C8341E; border-color: #C8341E; }
        .setpage switch slider { background: #FCFBF8; }

        .setpage progressbar trough { background: #DDD8CB; border: none;
                  border-radius: 2px; min-height: 16px; }
        /* Neutral fill by default; red is reserved for the near-full alert.
           The fill is the muted-text taupe, NOT ink: a mostly-full disk bar in
           #1A1916 drew a heavy black slab across the page, the one thing the
           papertone language never does (System Monitor's gauges were moved off
           ink for exactly this reason - keep the two apps in step). */
        .setpage progressbar progress { background: #6E695E; border-radius: 2px;
                  min-height: 16px; }
        .setpage progressbar.nearfull progress { background: #C8341E; }
        .setpage progressbar text { color: #1A1916; font-size: 12.5px; }

        /* ---- date & time controls ---- */
        .setpage spinbutton { background: #FCFBF8; color: #1A1916;
                  border: 1px solid #C9C4B6; border-radius: 2px;
                  box-shadow: none; }
        .setpage spinbutton entry { background: transparent; color: #1A1916;
                  border: none; box-shadow: none; min-width: 30px; }
        .setpage calendar { background: #FCFBF8; color: #1A1916;
                  border: 1px solid #C9C4B6; border-radius: 2px; padding: 4px; }
        .setpage calendar:selected { background: #C8341E; color: #FCFBF8; }
        """
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(Settings)
