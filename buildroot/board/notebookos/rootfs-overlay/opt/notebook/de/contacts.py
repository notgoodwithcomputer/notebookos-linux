#!/usr/bin/env python3
"""
Contacts — the Notebook OS address book (native GTK).

A two-pane surface: a searchable, alphabetically sorted contact list on the
left and a card detail view on the right (phone, email, address, birthday,
notes). The book ships EMPTY — the first run shows a "No contacts" state and no
records are ever seeded. The address book auto-persists to
$NB_HOME/.config/notebook/contacts.json on every add / edit / delete and on
close, so cards survive quit and reboot. File ▸ Export to PDF writes a
read-only copy of the whole book into $NB_HOME/Documents; there is no
file open/save (the book is a single always-saved store, like Journal).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import re
import time
import subprocess
from datetime import date, timedelta

import cairo

import nbapp
import nbicons
import nbprint
from nbi18n import _t  # noqa: E402

DE_DIR = os.path.dirname(os.path.abspath(__file__))

# Auto-persistence — the address book auto-saves to
# $NB_HOME/.config/notebook/contacts.json on every add / edit / delete and on
# close, matching writer.py / journal.py. The book ships EMPTY; no records are
# ever seeded. File ▸ Export to PDF writes a copy into $NB_HOME/Documents.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CONTACTS_FILE = os.path.join(CFG_DIR, "contacts.json")
DOCS_DIR = os.path.join(HOME, "Documents")

# papertone palette
INK = "#1A1916"
PAPER = "#FCFBF8"
PANEL = "#F1EEE6"
HAIR = "#D7D2C5"
MUTED = "#A39D8F"
MUTED2 = "#8A857A"
SEL = "#EAE3D2"
ACCENT = "#C8341E"

AVATAR_COLORS = ["#8A857A", "#6E7B57", "#9A6B4F", "#5B6B7B", "#7B5B6E", "#6B7B6E"]

# Standard address-book fields (label, storage key). Every field is free-text
# and editable; there is no radio/network metadata on a card.
FIELDS = [
    ("Phone", "phone"),
    ("Email", "email"),
    ("Address", "address"),
    ("Birthday", "bday"),
]

# The canonical set of string keys every card carries (color is added
# separately). Kept in one place so load / new / normalize agree.
FIELD_KEYS = ("name", "role", "phone", "email", "address", "bday", "notes")

# The contact card is a fixed reading measure centred in the detail pane —
# the width the design draws it at, and the width it keeps until the pane is
# too narrow to hold it (see _fit_card).
CARD_W = 760
CARD_MARGIN = 64

# Top-to-bottom order of the single-line edit fields, used so Enter advances
# to the next field (Tab-like) instead of dropping out of edit mid-form. Notes
# is a multi-line area (its own widget) and is deliberately not in this list.
EDIT_ORDER = ("name", "role", "phone", "email", "address", "bday")

# Letter dividers only appear once a book is long enough that scanning it needs
# them; a handful of contacts reads better as the flat list the design draws.
GROUP_FROM = 40
# How far ahead a birthday counts as "soon" — near enough to still get a card
# in the post, not so far that the banner is permanently on screen.
BIRTHDAY_SOON = 30

MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_BDAY_NUM_RE = re.compile(r"^(\d{1,4})\D+(\d{1,2})(?:\D+(\d{1,4}))?\D*$")
_WORD_RE = re.compile(r"[A-Za-z]+")
_DIGITS_RE = re.compile(r"\d+")


def _month_index(text):
    """A month named in words -> 1-12, in English or the running language."""
    low = text.lower()
    for i, name in enumerate(MONTH_NAMES):
        if low == name or low == name[:3]:
            return i + 1
    for i, name in enumerate(MONTH_NAMES):
        trans = _t(name.capitalize()).lower()
        if low == trans or low == trans[:3]:
            return i + 1
    return None


def parse_birthday(text):
    """(month, day) from whatever somebody typed in the Birthday field, or None.

    The field is free text and always has been, so this reads the shapes people
    actually write — "14/03/1948", "14 March", "March 14, 1948", "1948-03-14" —
    and gives up quietly on anything else rather than guessing. Day-first is
    assumed when both numbers could be either, matching the date format the rest
    of the OS writes ("25 July 2026")."""
    text = (text or "").strip()
    if not text:
        return None
    word = _WORD_RE.search(text)
    if word:
        m = _month_index(word.group(0))
        if m is None:
            return None
        nums = [int(n) for n in _DIGITS_RE.findall(text)]
        days = [n for n in nums if 1 <= n <= 31]
        if not days:
            return None
        return _valid(m, days[0])
    m = _BDAY_NUM_RE.match(text)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    c = m.group(3)
    if a > 31:                                   # a leading year: 1948-03-14
        return _valid(b, int(c)) if c else None
    if a > 12:                                   # day first, unambiguously
        return _valid(b, a)
    if b > 12 and c is None:                     # "03/14" written month-first
        return _valid(a, b)
    return _valid(b, a)                          # ambiguous -> day first


def _valid(month, day):
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        date(2024, month, day)                   # a leap year, so 29 Feb passes
    except ValueError:
        return None
    return (month, day)


def days_until_birthday(text, today=None):
    """How many days until the next anniversary of `text` (0 = today), or None
    when it carries no readable date. A 29 February birthday falls back to the
    1st of March in a year that has no 29th."""
    got = parse_birthday(text)
    if got is None:
        return None
    month, day = got
    today = today or date.today()
    for year in (today.year, today.year + 1):
        try:
            nxt = date(year, month, day)
        except ValueError:
            nxt = date(year, 3, 1)               # 29 Feb in a common year
        if nxt >= today:
            return (nxt - today).days
    return None


def birthday_phrase(days):
    """When a birthday falls, as a whole sentence — 'Birthday today', 'Birthday
    on Tuesday', 'Birthday in 12 days'. Written out in full rather than glued
    together from fragments so each one can be translated as a phrase."""
    if days == 0:
        return _t("Birthday today")
    if days == 1:
        return _t("Birthday tomorrow")
    if days < 7:
        return _t("Birthday on %s") % _t(
            DAY_NAMES[(date.today() + timedelta(days=days)).weekday()])
    if days == 7:
        return _t("Birthday in a week")
    return _t("Birthday in %d days") % days

class Contacts(nbapp.AppWindow):
    app_name = "Contacts"
    menus = ("File", "Edit", "Card", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        # Load the saved address book (empty on a fresh device — nothing is
        # seeded). Normalization guarantees every render / edit / delete /
        # search path sees the same full-key shape _new_contact() produces.
        self.people = self._load_people()
        self.active = 0
        self.editing = False
        self.search_text = ""
        self._entries = {}    # key -> Gtk.Entry, live only while editing
        self._notes_view = None       # multi-line Notes TextView, live while editing
        self._pending_new = False     # active card is a just-created, untouched one
        self._search_timer = 0        # pending search-debounce source (0 = none)
        self._status_timer = 0        # pending status-line clear source (0 = none)
        self._avatar_css_cache = {}   # (color,size,fontsize) -> Gtk.CssProvider
        self._card_col = None         # the card column, refitted on resize
        self._card_w = 0              # its current width (0 = not fitted yet)
        self._row_widgets = {}        # index -> row button, for in-place select

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._build_list_pane(), False, False, 0)
        body.pack_start(self._build_detail_pane(), True, True, 0)

        self._rebuild_list()
        self._rebuild_detail()

        # Final flush on close so the last add/edit/delete is never lost — File
        # ▸ Close / the logo / Esc all route through "destroy".
        self.connect("destroy", self._on_destroy)

    # -------------------------------------------------------- persistence
    def _load_people(self):
        """Return the saved address book, normalized to the canonical card
        shape _new_contact() produces (every field key present, plus a palette
        colour). Ships EMPTY: a missing file (fresh device), an empty file, or
        a malformed file all load as the "No contacts" state — no records are
        ever seeded."""
        try:
            with open(CONTACTS_FILE) as fh:
                data = json.load(fh)
        except Exception:
            # missing / unreadable / malformed -> empty state
            return []
        raw = data.get("people") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        return [self._normalize_person(p, i)
                for i, p in enumerate(raw) if isinstance(p, dict)]

    @staticmethod
    def _normalize_person(p, i):
        """Coerce one loaded record into the canonical card shape so every field
        key is present (as a string) and a palette colour is guaranteed."""
        person = {k: str(p.get(k, "") or "") for k in FIELD_KEYS}
        color = p.get("color")
        if not isinstance(color, str) or not color:
            color = AVATAR_COLORS[i % len(AVATAR_COLORS)]
        person["color"] = color
        return person

    def _save(self):
        """Persist the full address book. Swallows I/O errors so a bad write
        never crashes the app."""
        try:
            nbapp.atomic_write_json(CONTACTS_FILE, {"people": self.people})
        except Exception:
            pass

    def _on_destroy(self, *_):
        # Capture any in-progress edit before the final write. The inline field
        # entries only push into self.people via _commit_edits (Done / switch /
        # New / Enter); a value typed but not yet committed lives solely in the
        # Gtk.Entry. Those entries are still alive while this destroy handler
        # runs, so commit them here — otherwise closing the window (or Shut
        # Down) mid-edit would silently discard the last keystrokes even though
        # Contacts always-saves.
        if self.editing:
            try:
                self._commit_edits()   # also writes to disk
            except Exception:
                pass
        try:
            self._finish_new_card()    # don't persist an untouched New Contact
        except Exception:
            pass
        self._save()

    # ---------------------------------------------------------------- list
    def _build_list_pane(self):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.set_size_request(360, -1)
        pane.get_style_context().add_class("listpane")

        # search header — a boxed field with a quiet "add" affordance beside it
        sbwrap = Gtk.Box(spacing=8)
        sbwrap.get_style_context().add_class("searchheader")
        searchbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        searchbox.get_style_context().add_class("searchbox")
        icon = Gtk.Image.new_from_pixbuf(nbicons.pixbuf("search", 16, MUTED2))
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_margin_start(10)
        searchbox.pack_start(icon, False, False, 0)
        self.search = Gtk.Entry()
        self.search.set_has_frame(False)
        self.search.set_placeholder_text(_t("Search contacts"))
        self.search.get_style_context().add_class("searchentry")
        self.search.connect("changed", self._on_search)
        self.search.connect("activate", self._on_search_activate)
        searchbox.pack_start(self.search, True, True, 0)
        sbwrap.pack_start(searchbox, True, True, 0)

        # new-contact affordance — quiet borderless glyph
        newbtn = Gtk.Button()
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("newbtn")
        newbtn.set_tooltip_text(_t("New contact"))
        newbtn.set_valign(Gtk.Align.CENTER)
        newbtn.add(Gtk.Image.new_from_pixbuf(nbicons.pixbuf("plus", 18, MUTED2)))
        newbtn.connect("clicked", self._new_contact)
        sbwrap.pack_start(newbtn, False, False, 0)
        pane.pack_start(sbwrap, False, False, 0)

        # Whose birthday is coming. An address book that quietly knows this and
        # never says so is a filing cabinet; this is the one thing it can tell
        # you without being asked. Empty (and takes no space) when nobody's is
        # near, so it is never permanent furniture.
        self.bday_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.bday_bar.set_no_show_all(True)
        pane.pack_start(self.bday_bar, False, False, 0)

        # scrollable list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_box.set_margin_start(10); self.list_box.set_margin_end(10)
        self.list_box.set_margin_top(8); self.list_box.set_margin_bottom(16)
        scroll.add(self.list_box)
        pane.pack_start(scroll, True, True, 0)

        # transient status line (empty at rest). File ▸ Export to PDF surfaces
        # its result here — "Exported to Documents" on success, a neutral
        # error otherwise — then clears after a moment.
        self.status_lbl = Gtk.Label(label="", xalign=0)
        self.status_lbl.get_style_context().add_class("statusline")
        self.status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        # Hidden until something is actually reported: an empty status line
        # still drew its rule and padding, so the list sat above a blank strip.
        self.status_lbl.set_no_show_all(True)
        pane.pack_start(self.status_lbl, False, False, 0)
        return pane

    def _rebuild_list(self):
        # A direct (structural) rebuild supersedes any pending debounced search
        # rebuild, so the two never race into a redundant double teardown.
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        for c in self.list_box.get_children():
            self.list_box.remove(c)
        self._row_widgets = {}
        self._rebuild_bday_bar()

        entries = self._visible_order_pairs()

        if not entries:
            empty = Gtk.Label(
                label=_t("No contacts") if not self.people
                else _t("Nobody matches that"))
            empty.get_style_context().add_class("listempty")
            empty.set_margin_top(40); empty.set_margin_bottom(40)
            self.list_box.pack_start(empty, False, False, 0)
            self.list_box.show_all()
            return

        # Alphabetical, flat as the design draws it — with letter dividers once
        # the book is long enough that a flat run of hundreds of names cannot be
        # scanned (and never while a search is narrowing it anyway).
        grouped = (len(entries) >= GROUP_FROM and not self.search_text.strip())
        letter = None
        for i, p in entries:
            if grouped:
                first = self._sort_letter(p)
                if first != letter:
                    letter = first
                    self.list_box.pack_start(self._letter_head(first),
                                             False, False, 0)
            row = self._contact_row(i, p)
            self._row_widgets[i] = row
            self.list_box.pack_start(row, False, False, 0)
        self.list_box.show_all()

    @staticmethod
    def _sort_letter(p):
        """The divider a card files under: its initial, or '#' for a name that
        starts with a digit, a symbol or nothing at all."""
        name = (p.get("name") or "").strip()
        return name[0].upper() if name[:1].isalpha() else "#"

    def _letter_head(self, letter):
        lbl = Gtk.Label(label=letter, xalign=0)
        lbl.get_style_context().add_class("letterhead")
        return lbl

    # ------------------------------------------------------------- birthdays
    def _birthdays_soon(self):
        """(days away, index) for everyone whose birthday falls within the next
        BIRTHDAY_SOON days, soonest first."""
        out = []
        for i, p in enumerate(self.people):
            days = days_until_birthday(p.get("bday", ""))
            if days is not None and days <= BIRTHDAY_SOON:
                out.append((days, i))
        out.sort()
        return out

    def _rebuild_bday_bar(self):
        for c in self.bday_bar.get_children():
            self.bday_bar.remove(c)
        soon = self._birthdays_soon()
        if not soon:
            self.bday_bar.hide()
            return
        days, idx = soon[0]
        person = self.people[idx]
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("bdayrow")
        btn.connect("clicked", lambda *_: self._select(idx))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=person.get("name") or _t("Unnamed"), xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class("bdayname")
        col.pack_start(name, False, False, 0)
        note = birthday_phrase(days)
        if len(soon) > 1:
            note += "  ·  " + _t("%d more soon") % (len(soon) - 1)
        sub = Gtk.Label(label=note, xalign=0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.get_style_context().add_class("bdaynote")
        col.pack_start(sub, False, False, 0)
        btn.add(col)
        self.bday_bar.pack_start(btn, False, False, 0)
        self.bday_bar.show()
        btn.show_all()

    def _matches(self, p, q):
        """True when card `p` matches the lowercase query `q` across any of its
        text fields (name, role, phone, email, address, birthday, notes) — a
        novice searching a phone number or email expects a hit, not just names.
        An empty query matches everything."""
        return not q or any(q in (p.get(k) or "").lower() for k in FIELD_KEYS)

    def _visible_order_pairs(self):
        """(index, card) pairs for the current filter, sorted alphabetically by
        name — the single source of truth for the list and for step/jump."""
        q = self.search_text.strip().lower()
        pairs = [(i, p) for i, p in enumerate(self.people) if self._matches(p, q)]
        pairs.sort(key=lambda ip: ip[1]["name"].lower())
        return pairs

    def _contact_row(self, i, p):
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("contactrow")
        if i == self.active:
            row.get_style_context().add_class("selected")
        row.connect("clicked", lambda *_: self._select(i))

        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hb.pack_start(self._avatar(p, 34, 13), False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=p["name"] or "Unnamed", xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class("rowname")
        col.pack_start(name, False, False, 0)
        if p.get("role"):
            role = Gtk.Label(label=p["role"], xalign=0)
            role.set_ellipsize(Pango.EllipsizeMode.END)
            role.get_style_context().add_class("rowrole")
            col.pack_start(role, False, False, 0)
        hb.pack_start(col, True, True, 0)
        row.add(hb)
        return row

    def _avatar(self, p, size, fontsize):
        lbl = Gtk.Label(label=self._initials(p["name"]))
        lbl.get_style_context().add_class("avatar")
        lbl.set_size_request(size, size)
        lbl.set_halign(Gtk.Align.CENTER); lbl.set_valign(Gtk.Align.CENTER)
        lbl.get_style_context().add_provider(
            self._avatar_css(p.get("color", "#8A857A"), size, fontsize),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        return lbl

    def _avatar_css(self, color, size, fontsize):
        """Cached CssProvider for one avatar style. A provider is safe to share
        across widgets, so load_from_data (a fresh CSS parse) runs ~once per
        palette colour instead of once per row on every list rebuild."""
        key = (color, size, fontsize)
        prov = self._avatar_css_cache.get(key)
        if prov is None:
            prov = Gtk.CssProvider()
            prov.load_from_data(
                ("label { background:%s; color:#fff; border-radius:%dpx; "
                 "font-size:%dpx; font-weight:600; }"
                 % (color, size, fontsize)).encode())
            self._avatar_css_cache[key] = prov
        return prov

    @staticmethod
    def _initials(name):
        parts = [w for w in name.split() if w and w[0].isalpha()]
        return "".join(w[0] for w in parts[:2]).upper() or "?"

    # -------------------------------------------------------------- detail
    def _build_detail_pane(self):
        scroll = Gtk.ScrolledWindow()
        # EXTERNAL, not NEVER, horizontally: NEVER hands the card's full width
        # up as the window's minimum width, which on a 1024-wide screen pushed
        # the Edit button off the right edge with no way to reach it. The card
        # is fitted to the pane instead (_fit_card), so nothing is ever
        # scrolled out of sight sideways.
        scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("detailwrap")
        self.detail_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.detail_holder.set_hexpand(True); self.detail_holder.set_vexpand(True)
        scroll.add(self.detail_holder)
        scroll.connect("size-allocate", self._fit_card)
        return scroll

    def _fit_card(self, _w, alloc):
        """Keep the contact card at its 760px reading measure while the pane is
        wide enough, and narrow it to fit on a small screen. Writing the same
        width twice is a no-op, so this settles instead of looping."""
        w = max(300, min(CARD_W, alloc.width - 2 * CARD_MARGIN))
        if w != self._card_w:
            self._card_w = w
            col = self._card_col
            if col is not None:
                col.set_size_request(w, -1)

    def _rebuild_detail(self):
        for c in self.detail_holder.get_children():
            self.detail_holder.remove(c)
        self._entries = {}    # rebuilt fresh; populated below when editing
        self._notes_view = None
        self._card_col = None  # re-set below when a card (not the empty state)

        if not self.people:
            empty = Gtk.Label(label=_t("No contacts yet — click + to add one"))
            empty.get_style_context().add_class("detailempty")
            empty.set_hexpand(True); empty.set_vexpand(True)
            empty.set_halign(Gtk.Align.CENTER); empty.set_valign(Gtk.Align.CENTER)
            self.detail_holder.pack_start(empty, True, True, 0)
            self.detail_holder.show_all()
            return

        a = self.people[self.active]
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_halign(Gtk.Align.CENTER)
        self._card_col = col
        col.set_size_request(self._card_w or CARD_W, -1)
        col.set_margin_top(56); col.set_margin_bottom(80)
        col.set_margin_start(CARD_MARGIN); col.set_margin_end(CARD_MARGIN)

        # header
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=32)
        head.set_margin_bottom(44)
        head.pack_start(self._avatar(a, 96, 34), False, False, 0)

        idcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        idcol.set_valign(Gtk.Align.START)
        idcol.set_margin_top(6)
        if self.editing:
            nm = Gtk.Entry()
            nm.set_text(a.get("name", "") or "")
            nm.set_placeholder_text(_t("Name"))
            nm.get_style_context().add_class("nameentry")
            self._entries["name"] = nm
            nm.connect("activate", self._entry_activated)
            idcol.pack_start(nm, False, False, 0)
            rl = Gtk.Entry()
            rl.set_text(a.get("role", "") or "")
            rl.set_placeholder_text(_t("Role (optional)"))
            rl.get_style_context().add_class("roleentry")
            rl.set_margin_top(8)
            self._entries["role"] = rl
            rl.connect("activate", self._entry_activated)
            idcol.pack_start(rl, False, False, 0)
        else:
            nm = Gtk.Label(label=a["name"] or "Unnamed", xalign=0)
            nm.get_style_context().add_class("bigname")
            nm.set_line_wrap(True)                 # a long name wraps, never
            nm.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)  # overflows the pane
            idcol.pack_start(nm, False, False, 0)
            if a.get("role"):
                rl = Gtk.Label(label=a["role"], xalign=0)
                rl.get_style_context().add_class("bigrole")
                rl.set_line_wrap(True)
                rl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                rl.set_margin_top(8)
                idcol.pack_start(rl, False, False, 0)
        head.pack_start(idcol, True, True, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_valign(Gtk.Align.START)
        edit = Gtk.Button(label=_t("Done") if self.editing else "Edit")
        edit.set_relief(Gtk.ReliefStyle.NONE)
        edit.get_style_context().add_class("editbtn")
        if self.editing:
            edit.get_style_context().add_class("editon")
        edit.connect("clicked", self._toggle_edit)
        btns.pack_start(edit, False, False, 0)
        head.pack_start(btns, False, False, 0)
        col.pack_start(head, False, False, 0)

        # fields
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid.get_style_context().add_class("fieldgrid")
        for label, key in FIELDS:
            grid.pack_start(self._field_row(a, label, key), False, False, 0)
        col.pack_start(grid, False, False, 0)

        # notes
        nb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nb.set_margin_top(34)
        nlbl = Gtk.Label(label=_t("NOTES"), xalign=0)
        nlbl.get_style_context().add_class("fieldlabel")
        nlbl.set_margin_bottom(12)
        nb.pack_start(nlbl, False, False, 0)
        if self.editing:
            # A real multi-line area: the read view and the PDF already lay
            # notes out over several lines, so the editor has to let a novice
            # actually type them (a single-line Entry silently flattened newlines).
            notes = Gtk.TextView()
            notes.set_wrap_mode(Gtk.WrapMode.WORD)
            notes.set_accepts_tab(False)   # Tab leaves the field, never indents
            notes.get_style_context().add_class("notesedit")
            notes.get_buffer().set_text(a.get("notes", "") or "")
            self._notes_view = notes
            # A bare TextView asks for its whole text as its MINIMUM width and
            # never shrinks, so a wordy note made the card wider than the pane
            # and carried the Done button off the right edge, with no scrollbar
            # to reach it. Inside a ScrolledWindow the field can shrink to the
            # card measure and wrap, exactly like the read view.
            nscroll = Gtk.ScrolledWindow()
            # NEVER would re-propagate that whole minimum; AUTOMATIC lets the
            # view shrink and wrap to the card measure instead.
            nscroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                               Gtk.PolicyType.AUTOMATIC)
            nscroll.set_size_request(-1, 84)
            nscroll.add(notes)
            # GTK draws no border for a TextView's own CSS, so the notes area
            # was an invisible field: the heading, then blank paper. Frame it in
            # a box that carries the same outline the entries above have.
            nframe = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            nframe.get_style_context().add_class("notesframe")
            nframe.pack_start(nscroll, True, True, 0)
            nb.pack_start(nframe, False, False, 0)
        else:
            has_notes = bool((a.get("notes", "") or "").strip())
            notes = Gtk.Label(label=a.get("notes", "") or "—", xalign=0)
            notes.set_line_wrap(True)
            notes.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            notes.set_max_width_chars(48)
            # max_width_chars caps only what the label ASKS for; filling the
            # card it wrapped at the card's width instead of the 48-character
            # measure, so a long note ran the full width of the page.
            notes.set_halign(Gtk.Align.START)
            notes.get_style_context().add_class("notestext")
            if not has_notes:
                # keep the empty state honest instead of a blank gap under NOTES,
                # matching the "—" the other fields show when empty
                notes.get_style_context().add_class("fieldempty")
            nb.pack_start(notes, False, False, 0)
        col.pack_start(nb, False, False, 0)

        self.detail_holder.pack_start(col, False, False, 0)
        self.detail_holder.show_all()

    def _field_row(self, a, label, key):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.get_style_context().add_class("fieldrow")
        lbl = Gtk.Label(label=label.upper(), xalign=0)
        lbl.get_style_context().add_class("fieldlabel")
        lbl.set_size_request(200, -1)
        lbl.set_valign(Gtk.Align.START)
        row.pack_start(lbl, False, False, 0)
        if self.editing:
            ent = Gtk.Entry()
            ent.set_text(a.get(key, "") or "")
            ent.set_placeholder_text(label)
            ent.get_style_context().add_class("fieldentry")
            self._entries[key] = ent
            ent.connect("activate", self._entry_activated)
            row.pack_start(ent, True, True, 0)
        else:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            val = Gtk.Label(label=a.get(key, "") or "—", xalign=0)
            val.get_style_context().add_class("fieldval")
            val.set_line_wrap(True)                 # long email / address wraps
            val.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)  # instead of clipping
            if not (a.get(key, "") or ""):
                val.get_style_context().add_class("fieldempty")
            col.pack_start(val, False, False, 0)
            # A date on its own does not answer the question anyone actually
            # has about a birthday, which is how long they have got.
            if key == "bday":
                days = days_until_birthday(a.get("bday", ""))
                if days is not None and days <= BIRTHDAY_SOON:
                    note = Gtk.Label(label=birthday_phrase(days), xalign=0)
                    note.get_style_context().add_class("fieldnote")
                    col.pack_start(note, False, False, 0)
            row.pack_start(col, True, True, 0)
        return row

    # -------------------------------------------------------------- events
    def _on_search(self, entry):
        # Coalesce keystroke bursts: on swrast, tearing the whole list down and
        # re-realizing it on every character causes visible typing lag. Track
        # the query eagerly (so activate/step read the latest filter), but defer
        # the actual list rebuild until typing settles, cancelling any pending
        # rebuild first so only the final query is rendered.
        self.search_text = entry.get_text()
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(130, self._search_timeout)

    def _search_timeout(self):
        self._search_timer = 0
        self._rebuild_list()
        return False   # one-shot

    def _on_search_activate(self, *_):
        """Enter in the search box jumps to the first matching card, using the
        same filter/sort order as the visible list. No-op when nothing matches."""
        order = self._visible_order_pairs()
        if order:
            self._select(order[0][0])

    def _select(self, i):
        # If an edit is in progress, persist it into the CURRENT card before
        # switching away — the same write "Done" performs — so moving to another
        # contact never silently discards the uncommitted edit. Commit while
        # self.active/self._entries still point at the card being left, then drop
        # back to the read view: another contact should read as a card, not a form.
        structural = self.editing        # a committed edit can re-sort the list
        if self.editing:
            self._commit_edits()
            self.editing = False
        # An untouched New Contact left behind here would linger as a blank
        # "Unnamed" row; drop it. The pending card is always the last-appended
        # one, so its removal only ever shifts indexes at/after it.
        dropped = self._finish_new_card()
        if dropped is not None:
            structural = True
            if dropped < i:
                i -= 1
        was = self.active
        if self.people:
            self.active = max(0, min(i, len(self.people) - 1))
        else:
            self.active = 0
        # Picking a name out of the list changes which row is highlighted and
        # nothing else, so move the highlight instead of rebuilding every row.
        # With 500 contacts the rebuild cost 114 ms on every single click; this
        # is two style-class changes.
        if structural or not self._move_highlight(was):
            self._rebuild_list()
        self._rebuild_detail()

    def _move_highlight(self, was):
        """Shift the .selected class from row `was` to the active one. False
        when the rows on screen are not the ones we hold handles for (a fresh
        pane, a filtered list that no longer holds the target), in which case
        the caller falls back to a full rebuild."""
        rows = self._row_widgets
        if not rows or self.active not in rows:
            return False
        old = rows.get(was)
        if old is not None:
            old.get_style_context().remove_class("selected")
        rows[self.active].get_style_context().add_class("selected")
        return True

    def _toggle_edit(self, *_):
        if self.editing:            # leaving edit mode: persist the entries
            self._commit_edits()
            self.editing = False
            self._finish_new_card()  # a New Contact tapped Done blank is dropped
        else:
            self.editing = True
        self._rebuild_detail()
        # Only a FINISHED edit can have changed a name or role, and so the
        # rows; opening the form changes nothing in the list, and rebuilding it
        # there cost ~95 ms of nothing on a five-hundred-contact book.
        if not self.editing:
            self._rebuild_list()
        if self.editing:
            # Land the cursor in the name field so editing starts by typing,
            # not by hunting for a field to click.
            nm = self._entries.get("name")
            if nm is not None:
                try:
                    nm.grab_focus()
                    nm.set_position(-1)
                except Exception:
                    pass

    def _commit_edits(self):
        if not (0 <= self.active < len(self.people)):
            return
        a = self.people[self.active]
        for key, ent in self._entries.items():
            a[key] = ent.get_text()
        nv = self._notes_view
        if nv is not None:
            buf = nv.get_buffer()
            a["notes"] = buf.get_text(
                buf.get_start_iter(), buf.get_end_iter(), False)
        self._save()   # every edit (Done / switch-away / new / activate) sticks

    def _is_blank(self, p):
        """True when a card carries no user content at all (every text field
        empty). Used to recognise an untouched New Contact."""
        return not any((p.get(k) or "").strip() for k in FIELD_KEYS)

    def _finish_new_card(self):
        """Drop a just-created card the user never filled in, so navigating away
        from an untouched New Contact leaves no blank 'Unnamed' record behind.
        Only ever removes a card born via New Contact (self._pending_new) — never
        a pre-existing card the user cleared, which would be silent data loss.
        Returns the removed index, or None."""
        dropped = None
        if (self._pending_new and 0 <= self.active < len(self.people)
                and self._is_blank(self.people[self.active])):
            dropped = self.active
            del self.people[self.active]
            if self.active >= len(self.people):
                self.active = max(0, len(self.people) - 1)
            self._save()
        self._pending_new = False
        return dropped

    def _entry_activated(self, entry, *_):
        """Enter advances to the next field (Tab-like) so a card fills in
        top-to-bottom without dropping out of edit mid-form; from the last
        single-line field it steps into the multi-line Notes area. Only the true
        end-of-form (no next field, no notes) commits — deferred to idle so the
        entry finishes emitting 'activate' before the pane is rebuilt under it."""
        if not self.editing:
            return
        order = [k for k in EDIT_ORDER if k in self._entries]
        cur = next((k for k in order if self._entries.get(k) is entry), None)
        if cur is not None:
            i = order.index(cur)
            if i + 1 < len(order):
                nxt = self._entries.get(order[i + 1])
                if nxt is not None:
                    nxt.grab_focus()
                    try:
                        nxt.set_position(-1)
                    except Exception:
                        pass
                    return
            if self._notes_view is not None:
                self._notes_view.grab_focus()
                return
        GLib.idle_add(self._commit_and_finish)

    def _commit_and_finish(self):
        if self.editing:
            self._toggle_edit()
        return False   # one-shot idle

    def _new_contact(self, *_):
        # If an edit is already in progress, persist it into the current card
        # before creating a new one — the same write "Done" performs — so making
        # a new contact never silently discards the uncommitted edit. Commit
        # while self.active/self._entries still point at the card being left.
        if self.editing:
            self._commit_edits()
        # Replace an untouched New Contact rather than stacking a second blank
        # one, so a novice tapping "+" twice never ends up with duplicate empty
        # cards to clean up afterwards.
        self._finish_new_card()
        # Start blank with a "Name" placeholder — no fabricated "New Contact"
        # string is ever written to disk (per the no-seed / no-placeholder rule);
        # an unnamed card reads as "Unnamed" until the user types.
        color = AVATAR_COLORS[len(self.people) % len(AVATAR_COLORS)]
        person = {k: "" for k in FIELD_KEYS}
        person["color"] = color
        self.people.append(person)
        self.active = len(self.people) - 1
        self.editing = True
        self._pending_new = True
        self._save()   # the new card exists on disk immediately
        # clear any active search so the new card is visible in the list
        self.search_text = ""
        if self.search.get_text():
            self.search.set_text("")   # triggers _on_search -> _rebuild_list
        self._rebuild_list()
        self._rebuild_detail()
        # Put the cursor in the (empty) name field — the user is here to type a name.
        nm = self._entries.get("name")
        if nm is not None:
            try:
                nm.grab_focus()
            except Exception:
                pass

    def _delete_contact(self, *_):
        """Confirm, then remove the active card. Destructive, so it always asks
        first; Cancel is the default so a stray Return never deletes."""
        if not (0 <= self.active < len(self.people)):
            return
        name = (self.people[self.active].get("name") or "").strip() or "this contact"
        self._confirm(
            "Delete Contact",
            "Delete “%s”? This cannot be undone." % name,
            "Delete", self._do_delete)

    def _confirm(self, title, message, ok_label, on_yes):
        """Modal confirmation for a destructive action, drawn as a papertone card
        in the app's own design language (not a stock system dialog): darker-beige
        border, signage-red only on the destructive primary button, Cancel focused
        by default so a stray Space/Return never deletes. Esc cancels. Runs
        `on_yes` only on the primary button; crash-safe if the dialog can't build."""
        try:
            dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
            dlg.set_decorated(False)
            dlg.get_style_context().add_class("cdlg")
            area = dlg.get_content_area()
            area.set_spacing(0)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.get_style_context().add_class("cdlgbox")
            hd = Gtk.Label(label=title, xalign=0)
            hd.get_style_context().add_class("cdlgtitle")
            msg = Gtk.Label(label=message, xalign=0)
            msg.set_line_wrap(True)
            msg.set_line_wrap_mode(Pango.WrapMode.WORD)
            msg.set_max_width_chars(40)
            msg.get_style_context().add_class("cdlgmsg")
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            btns.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("cdlgcancel")
            ok = Gtk.Button(label=ok_label)
            ok.get_style_context().add_class("cdlgok")
            btns.pack_start(cancel, False, False, 0)
            btns.pack_start(ok, False, False, 0)
            box.pack_start(hd, False, False, 0)
            box.pack_start(msg, False, False, 0)
            box.pack_start(btns, False, False, 0)
            area.add(box)
            cancel.connect("clicked", lambda *_: dlg.destroy())
            ok.connect("clicked", lambda *_: (dlg.destroy(), on_yes()))
            # The modal dialog is a separate window with its own key focus, so the
            # app-window Esc handler never sees these events — wire Esc here or it
            # would be dead inside the dialog.
            dlg.connect(
                "key-press-event",
                lambda _w, e: (dlg.destroy() or True)
                if e.keyval == Gdk.KEY_Escape else False)
            dlg.show_all()
            cancel.grab_focus()   # focus the safe default
        except Exception:
            pass

    def _do_delete(self):
        if not (0 <= self.active < len(self.people)):
            return
        del self.people[self.active]
        if self.active >= len(self.people):
            self.active = max(0, len(self.people) - 1)
        self.editing = False
        self._pending_new = False
        self._save()   # the deletion sticks (and an emptied book stays empty)
        self._rebuild_list()
        self._rebuild_detail()

    # -------------- File menu: Export to PDF under $NB_HOME/Documents ------
    # No file open/save. contacts.json is the always-saved store (the source of
    # truth); Export renders the whole book as a read-only PDF into Documents.
    def _export_pdf(self, *_a):
        """Render the whole address book to a paginated PDF under Documents.
        Commits any in-progress edit first so the export reflects on-screen
        text. Reports a neutral status line; never crashes on a bad write."""
        if self.editing:
            self._commit_edits()
        if not self.people:
            self._flash("No contacts to export")
            return
        name = "contacts-" + time.strftime("%Y-%m-%d") + ".pdf"
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            self._make_pdf(os.path.join(DOCS_DIR, name))
        except Exception:
            self._flash("Export failed")
            return
        self._flash("Exported to Documents")

    def _print(self, *_a):
        """File ▸ Print — hand the SAME PDF Export produces to the shared print
        dialog. Commits any in-progress edit first so the printout matches what
        is on screen. The no-printer case is handled inside nbprint."""
        if self.editing:
            self._commit_edits()
        if not self.people:
            self._flash("No contacts to print")
            return
        try:
            nbprint.print_document(self, self._make_pdf, job_name="Contacts")
        except Exception:
            self._flash("Printing is unavailable")

    def _make_pdf(self, path):
        """Write the address-book PDF to `path` — the exact document File ▸
        Export to PDF produces. Shared by Export (into Documents) and Print
        (into a temp file for the spooler)."""
        self._render_pdf(path)

    def _render_pdf(self, path):
        """Draw every contact (name, role, fields, notes) onto a cairo PDF at
        `path`, paginating when the cursor overflows the page."""
        PW, PH = 612.0, 792.0            # US Letter, points
        ML, MR, MT, MB = 64.0, 64.0, 72.0, 64.0
        text_w = PW - ML - MR
        surf = cairo.PDFSurface(path, PW, PH)
        cr = cairo.Context(surf)
        y = [MT]                         # cursor top, boxed so helpers can bump it

        def ink(hexc):
            r, g, b = nbicons._hex(hexc)
            cr.set_source_rgb(r, g, b)

        def face(bold, serif=False):
            cr.select_font_face(
                "Serif" if serif else "Sans", cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)

        def wrap(text, size, bold, serif):
            face(bold, serif)
            cr.set_font_size(size)
            lines, cur = [], ""
            for w in text.split(" "):
                trial = w if not cur else cur + " " + w
                if not cur or cr.text_extents(trial)[2] <= text_w:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            lines.append(cur)
            return lines

        def emit(text, size, bold, color, serif=False,
                 gap_before=0.0, gap_after=0.0):
            lh = size * 1.4
            y[0] += gap_before
            for ln in (wrap(text, size, bold, serif) if text else [""]):
                if y[0] + lh > PH - MB:
                    surf.show_page()
                    y[0] = MT
                face(bold, serif)
                cr.set_font_size(size)
                ink(color)
                cr.move_to(ML, y[0] + size)
                cr.show_text(ln)
                y[0] += lh
            y[0] += gap_after

        def rule():
            if y[0] + 1 <= PH - MB:
                ink("#ECE7DA")
                cr.set_line_width(1.0)
                cr.move_to(ML, y[0])
                cr.line_to(PW - MR, y[0])
                cr.stroke()
            y[0] += 18

        people = sorted(self.people, key=lambda p: p.get("name", "").lower())
        # Cover header, then each contact alphabetically.
        emit("CONTACTS", 9.5, False, "#6E695E", gap_after=6)
        emit("%d %s" % (len(people),
                        "contact" if len(people) == 1 else "contacts"),
             26, True, "#1A1916", serif=True, gap_after=3)
        rule()
        for idx, p in enumerate(people):
            if idx:
                y[0] += 8
                rule()
            emit(p.get("name", "") or "Unnamed", 20, True, "#1A1916",
                 serif=True, gap_before=6, gap_after=2)
            role = p.get("role", "")
            if role:
                emit(role, 11, False, "#79736A", gap_after=6)
            else:
                y[0] += 4
            for label, key in FIELDS:
                val = p.get(key, "")
                if val:
                    emit("%s   %s" % (label.upper(), val), 10.5, False,
                         "#26241F", gap_after=2)
            notes = p.get("notes", "")
            if notes:
                emit("NOTES", 9, False, "#9A958A", gap_before=6, gap_after=2)
                for raw in notes.split("\n"):
                    emit(raw, 11, False, "#26241F", serif=True)

        surf.finish()

    def _flash(self, text):
        """Surface a transient status/result in the list pane's status line,
        then clear it after a moment. Crash-safe: a UI failure never propagates
        out of the action that called it."""
        try:
            self.status_lbl.set_text(text)
            self.status_lbl.show()
        except Exception:
            pass
        if self._status_timer:
            GLib.source_remove(self._status_timer)
        self._status_timer = GLib.timeout_add_seconds(3, self._clear_status)

    def _clear_status(self):
        self._status_timer = 0
        try:
            self.status_lbl.set_text("")
            self.status_lbl.hide()
        except Exception:
            pass
        return False   # one-shot

    def _step(self, delta):
        """Select the next/previous card in the visible (filtered, sorted)
        order. Safe when the list is empty."""
        order = [i for i, _ in self._visible_order_pairs()]
        if not order:
            return
        pos = order.index(self.active) if self.active in order else 0
        self._select(order[(pos + delta) % len(order)])

    def _on_key(self, w, ev):
        # Esc while editing finishes the edit (committing, exactly like Done)
        # and returns to the read view, rather than quitting Contacts out from
        # under someone mid-card. Only when nothing is being edited does Esc fall
        # through to the base handler and close the app. An open menu / About
        # card is dismissed by the base first.
        if (ev.keyval == Gdk.KEY_Escape and self.editing
                and self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            self._toggle_edit()
            return True
        return super()._on_key(w, ev)

    # ------------------------------------------------------------- menus
    def menu_items(self, name):
        if name == "File":
            return [
                ("New Contact", lambda: self._new_contact()),
                nbapp.SEP,
                ("Export to PDF", self._export_pdf),
                ("Print…", self._print),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Card":
            has = bool(self.people)
            return [
                ("New Contact", lambda: self._new_contact()),
                ("Done Editing" if self.editing else "Edit Card",
                 (lambda: self._toggle_edit()) if has else None),
                nbapp.SEP,
                ("Delete Card…", (lambda: self._delete_contact()) if has else None),
            ]
        if name == "View":
            has_text = bool(self.search.get_text())
            return [
                ("Find", lambda: self.search.grab_focus()),
                ("Clear Search",
                 (lambda: self.search.set_text("")) if has_text else None),
                nbapp.SEP,
                ("Next Contact", lambda: self._step(1)),
                ("Previous Contact", lambda: self._step(-1)),
            ]
        return super().menu_items(name)

    # ------------------------------------------------------------------ css
    def _install_css(self):
        css = ("""
        .listpane, .listpane *, .detailwrap, .detailwrap * {
                     font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .listpane { background: %(panel)s; border-right: 1px solid %(hair)s; }
        .searchheader { padding: 18px 18px 14px; }
        .searchbox { background: %(paper)s; border: 1px solid #C4BFB1;
                     border-radius: 2px; min-height: 38px; }
        .searchentry { background: transparent; border: none; box-shadow: none;
                       font-size: 14px; color: #2A2620; }
        .searchentry image { color: %(muted2)s; }
        .listempty, .detailempty { color: %(muted)s; font-size: 13.5px; }
        .detailempty { font-size: 14px; }
        .contactrow { padding: 10px 12px; border-radius: 2px; margin-bottom: 2px;
                      background: transparent; border: none; box-shadow: none; }
        .contactrow:hover { background: #E6DFCE; }
        .contactrow.selected { background: %(sel)s;
                               box-shadow: inset 3px 0 0 %(accent)s; }
        .rowname { font-size: 15px; color: %(ink)s; font-weight: 500; }
        .rowrole { font-size: 12px; color: %(muted2)s; margin-top: 2px; }
        /* Letter dividers, only drawn on a long book (see GROUP_FROM). */
        .letterhead { font-size: 11px; letter-spacing: 0.14em; font-weight: 700;
                      color: %(muted)s; padding: 14px 12px 5px; }
        /* The one thing the book volunteers. Signage-red hairline on the
           leading edge, the same marker the selected row uses, because this
           IS the alert case the accent is reserved for. */
        .bdayrow { padding: 12px 18px; background: #F6F2E8; border: none;
                   border-top: 1px solid #C9C4B6;
                   border-bottom: 1px solid #C9C4B6; border-radius: 0;
                   box-shadow: inset 3px 0 0 %(accent)s; }
        .bdayrow:hover { background: #EFE9DA; }
        .bdayname { font-size: 14px; color: %(ink)s; font-weight: 600; }
        .bdaynote { font-size: 12px; color: %(muted2)s; }
        .fieldnote { font-size: 12.5px; color: %(muted2)s; }
        .statusline { padding: 12px 16px; font-size: 12px; color: %(muted2)s;
                      border-top: 1px solid #C9C4B6; }
        .detailwrap { background: %(paper)s; }
        .detailwrap > * { background: %(paper)s; }
        .bigname { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 40px; font-weight: 500; color: %(ink)s;
                   letter-spacing: -0.01em; }
        .bigrole { font-size: 17px; color: #79736A; }
        .editbtn { min-height: 40px; padding: 0 16px; border-radius: 2px;
                   background: %(paper)s; border: 1px solid #C4BFB1;
                   box-shadow: none; color: #3A362E; font-size: 14px; }
        .editbtn:hover { background: #ECE8DD; }
        .editbtn.editon { background: %(ink)s; color: %(paper)s;
                          border: 1px solid %(ink)s; font-weight: 600; }
        /* The theme's `* { color: ink }` matches a button's label node itself,
           so a colour set on the button never reaches its text: Done rendered
           as an ink slab with an ink label on it. Name the label too. */
        .editbtn label { color: #3A362E; }
        .editbtn.editon label { color: %(paper)s; font-weight: 600; }
        .fieldgrid { border-top: 1px solid #C9C4B6; }
        .fieldrow { padding: 18px 0; border-bottom: 1px solid #ECE7DA; }
        .fieldlabel { font-size: 12px; letter-spacing: 0.1em; color: %(muted)s;
                      font-weight: 600; }
        .fieldval { font-size: 17px; color: %(ink)s; }
        .fieldempty { color: %(muted)s; }
        .notestext { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 19px; color: #26241F; }
        /* .notestext is declared after .fieldempty and would otherwise win,
           printing the "no notes" dash in ink while every other empty field
           shows a muted one. */
        .notestext.fieldempty { color: %(muted)s; }
        .newbtn { min-width: 30px; min-height: 30px; padding: 0 6px;
                  border-radius: 2px; background: transparent;
                  border: none; box-shadow: none; }
        .newbtn:hover { background: %(sel)s; }
        .fieldentry, .nameentry, .roleentry, .notesframe {
                  background: %(paper)s; border: 1px solid #C4BFB1;
                  border-radius: 2px; box-shadow: none; padding: 4px 8px;
                  color: %(ink)s; }
        .fieldentry { font-size: 17px; }
        .nameentry { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 30px; font-weight: 500; color: %(ink)s; }
        .roleentry { font-size: 16px; color: #79736A; }
        .notesedit { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 18px; color: #26241F; background: %(paper)s; }
        .notesedit text { background: %(paper)s; color: #26241F; }
        /* confirm dialog for destructive actions — papertone card, darker-beige
           border; signage-red ONLY on the destructive primary button */
        .cdlg { background: %(paper)s; border: 1px solid #C4BFB1; }
        .cdlgbox { padding: 24px 28px 20px; }
        .cdlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .cdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 20px; color: %(ink)s; }
        .cdlgmsg { font-size: 13px; color: #57534B; }
        .cdlgcancel { font-size: 13px; color: #2A2620; padding: 6px 16px;
                      background: %(paper)s; border: 1px solid #C4BFB1;
                      border-radius: 2px; box-shadow: none; }
        .cdlgcancel:hover { background: #ECE8DD; }
        .cdlgok { font-size: 13px; color: %(paper)s; padding: 6px 16px;
                  background: %(accent)s; border: 1px solid %(accent)s;
                  border-radius: 2px; box-shadow: none; }
        .cdlgok:hover { background: #A82C19; border-color: #A82C19; }
        .cdlgok label { color: %(paper)s; }
        .cdlgcancel label { color: #2A2620; }
        """ % dict(panel=PANEL, hair=HAIR, paper=PAPER, muted=MUTED,
                   muted2=MUTED2, ink=INK, sel=SEL, accent=ACCENT)).encode()
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # styling is cosmetic; a bad screen/provider must not stop launch
            pass


if __name__ == "__main__":
    nbapp.run(Contacts)
