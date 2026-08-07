#!/usr/bin/env python3
"""
Writer — Notebook OS word processor (native GTK, papertone).

A full word processor built on a single Gtk.TextView whose rich formatting is
carried entirely by Gtk.TextTags, laid out as a Word/Pages-style PAGE SHEET: a
Letter/A4 sheet with real margins centered on a gray desk, a ruler, page-break
guides, and optional header/footer + page numbers. Capabilities:

  * Rich text — font family & size, bold/italic/underline/strikethrough, text &
    highlight colour, alignment, line spacing, indent/outdent, bullet & numbered
    lists, and a Title/Heading 1-3/Body/Quote paragraph-style gallery.
  * Find & Replace (with match highlighting) and a checkpoint Undo/Redo history.
  * Inline tables (add/remove rows & columns).
  * Page furniture — page setup (size/orientation/margins), header/footer, page
    numbers.
  * Files under $NB_HOME/Documents: native .writer (rich JSON), plus .txt/.md;
    Export to PDF and Print via a PangoCairo renderer that honours formatting.

BLACK-BACKGROUND RULE: on this software-rendering GTK stack an *unstyled* surface
paints BLACK (the viewport GTK inserts, an untouched TextView `text` node, ...).
So this app explicitly backgrounds EVERY surface at STYLE_PROVIDER_PRIORITY_
APPLICATION (600, which beats the Papertone theme) — window, scrolledwindow,
viewport, the sheet, the textview widget node AND its `text` subnode, entries,
the ruler and the find bar. Nothing is left to inherit a default.

An optional document path may be passed as sys.argv[1] (the Finder opens
.txt/.md/.writer this way).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GdkPixbuf, GLib  # noqa: E402

import base64
import errno
import os
import sys
import json
import time

import cairo

import nbapp
import nbpicker
import nbicons
import nbprint
from nbi18n import _t  # noqa: E402


HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
DOC_FILE = os.path.join(CFG_DIR, "writer.json")   # session-recovery autosave
DOCS_DIR = os.path.join(HOME, "Documents")

# ---- page geometry (96 px per inch on screen; 72 pt per inch for PDF) --------
PX_PER_IN = 96.0
# On-screen magnification. The paper's PIXEL size scales by this, and so does
# the Pango resolution the TextView lays text out at — which is what makes a
# heading tagged "22 points" grow with everything else. Scaling only the widget
# would have left every point-sized tag at its original size.
ZOOM_STEPS = (0.75, 1.0, 1.25, 1.5, 2.0)
# Lines that must stay together at a page break: at least this many left at the
# foot of a sheet (orphan) and this many carried to the next (widow).
ORPHAN_MIN = 2
WIDOW_MIN = 2
DEFAULT_ZOOM = 1.0
PT_PER_IN = 72.0
PAGE_SIZES = {           # inches, portrait
    "Letter": (8.5, 11.0),
    "Legal":  (8.5, 14.0),
    "A4":     (8.27, 11.69),
}
DEFAULT_MARGINS_IN = (1.0, 1.0, 1.0, 1.0)   # top, right, bottom, left

# ---- fonts that actually ship (see tools; no Newsreader on the guest) --------
FONT_FAMILIES = [
    "Liberation Serif", "Liberation Sans", "Liberation Mono",
    "DejaVu Serif", "DejaVu Sans", "DejaVu Sans Mono", "Nimbus Sans",
]
# Families this app used to offer, mapped to the face that replaced them. A
# document saved before the swap still carries "Helvetica" in its font: tags;
# fontconfig aliases the NAME so it renders correctly either way, but the
# toolbar combo only highlights a family it can find in FONT_FAMILIES — without
# this the picker would sit blank on an older document.
LEGACY_FAMILIES = {"Helvetica": "Nimbus Sans", "Helvetica Neue": "Nimbus Sans"}
FONT_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64]
DEFAULT_FAMILY = "Liberation Serif"
DEFAULT_SIZE = 12


# ---- failure messages --------------------------------------------------------
# A message about a failed save is read at the worst moment there is: the user
# has just been told their work did not go where they put it, and the only
# question they actually have is WHETHER THE WORK STILL EXISTS. So none of
# these lead with the failure — they lead with what is still true, and they
# never contain an errno, a Python repr or an absolute path. The status bar used
# to read
#     Save failed: [Errno 28] No space left on device: '/root/Documents/Letter.txt'
# which answers nothing and reads as the machine breaking.
#
# The saving itself goes through nbapp.atomic_write_text / atomic_write_json —
# temp file, fsync, rename — so "nothing on disk was changed" is a promise the
# code actually keeps: a save that cannot finish leaves the previous file
# exactly as it was, and the live document is still in the window.
def _save_problem(exc):
    """One calm sentence for a save that did not happen."""
    err = getattr(exc, "errno", None)
    if err == errno.ENOSPC:
        return _t("Not enough space to save. Free up some space and try "
                  "again. The document is still open.")
    if err in (errno.EACCES, errno.EPERM):
        return _t("This document could not be saved to that location. Try "
                  "the Documents folder.")
    if err == errno.EROFS:
        return _t("That location cannot be written to. Try the Documents "
                  "folder.")
    return _t("The document could not be saved. The file on disk is "
              "unchanged.")


# ---- reading a document we did not write -------------------------------------
# EVERY dict that reaches _deserialize / _apply_page_geometry goes through here
# first. Two of them are not ours to trust: the session-recovery autosave and
# any .writer file the writer picks in File > Open. Both are plain JSON under
# the user's own home, so a half-finished write, a hand edit, a file copied off
# a failing USB stick or another app's document can hand us valid JSON whose
# FIELDS are the wrong type.
#
# THE BUG THIS EXISTS FOR (release blocker, found by driving the real window):
# none of that was checked. `for s_off, e_off, name in doc["runs"]` raised
# ValueError on a runs field that was a string or a dict; a body that was a
# number raised TypeError inside set_text; a "page" that was a string raised
# AttributeError inside _apply_page_geometry; an image record with no "off"
# raised KeyError. All four ran inside Writer.__init__ with no guard, so the
# WINDOW NEVER FINISHED BUILDING: eight of nine damaged writer.json shapes left
# Writer dead on every launch, for good, on a machine with no shell to repair
# it with. Down the same path, File > Open on a damaged .writer blanked the
# buffer (set_text("") is the first thing _deserialize does) and then raised,
# so the document that was on screen was gone and the autosave timer wrote the
# blank over the recovery file.
#
# The rule here is SALVAGE, not reject: the body text is the user's actual
# writing, so it is always recovered (coerced to text if it has to be), and only
# the individual formatting records that make no sense are dropped.
def _sane_page(page):
    """A page dict with a usable size, orientation and four numeric margins."""
    out = {"size": "Letter", "orientation": "portrait",
           "margins": list(DEFAULT_MARGINS_IN)}
    if not isinstance(page, dict):
        return out
    size = page.get("size")
    if isinstance(size, str) and size in PAGE_SIZES:
        out["size"] = size
    if page.get("orientation") == "landscape":
        out["orientation"] = "landscape"
    m = page.get("margins")
    if isinstance(m, (list, tuple)) and len(m) == 4:
        vals = []
        for v in m:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                vals = None
                break
            # A negative or absurd margin is not a crash, but it does put the
            # text off the sheet; keep it inside the paper.
            vals.append(min(4.0, max(0.0, float(v))))
        if vals:
            out["margins"] = vals
    return out


def _sane_text(v):
    """`v` as display text. A non-string field is shown rather than silently
    dropped — it is still whatever the user (or a broken writer) put there."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    return str(v)


def _sane_doc(doc):
    """Normalise a decoded document so nothing downstream can raise on it.

    Always returns a dict with a string body, a list of well-formed
    [start, end, tag-name] runs, image/table records that carry an integer
    offset, a usable page dict and string header/footer. Unknown extra keys are
    passed through untouched (v1 back-compat reads `title`/`subtitle`)."""
    if not isinstance(doc, dict):
        doc = {}
    out = dict(doc)
    out["body"] = _sane_text(doc.get("body", ""))
    runs = []
    raw_runs = doc.get("runs")
    if isinstance(raw_runs, (list, tuple)):
        for r in raw_runs:
            if isinstance(r, str) or not isinstance(r, (list, tuple)):
                continue
            if len(r) < 3 or not isinstance(r[2], str) or not r[2]:
                continue
            try:
                s, e = int(r[0]), int(r[1])
            except (TypeError, ValueError):
                continue
            runs.append([s, e, r[2]])
    out["runs"] = runs
    for key in ("images", "tables"):
        recs = []
        raw = doc.get(key)
        if isinstance(raw, (list, tuple)):
            for rec in raw:
                if not isinstance(rec, dict):
                    continue
                try:
                    off = int(rec.get("off"))
                except (TypeError, ValueError):
                    continue
                rec = dict(rec, off=off)
                if key == "images":
                    rec["path"] = _sane_text(rec.get("path", ""))
                recs.append(rec)
        out[key] = recs
    out["page"] = _sane_page(doc.get("page"))
    out["header"] = _sane_text(doc.get("header", ""))
    out["footer"] = _sane_text(doc.get("footer", ""))
    out["page_numbers"] = bool(doc.get("page_numbers", False))
    p = doc.get("path")
    out["path"] = p if isinstance(p, str) and p else None
    return out


def _export_problem(exc):
    """One calm sentence for a PDF export that did not happen. An export is a
    copy, so the document itself is never at risk — say so."""
    err = getattr(exc, "errno", None)
    if err == errno.ENOSPC:
        return _t("Not enough space to write the PDF. Free up some space "
                  "and try again.")
    if err in (errno.EACCES, errno.EPERM, errno.EROFS):
        return _t("The PDF could not be written to that location. Try the "
                  "Documents folder.")
    return _t("The PDF could not be written. The document is unchanged.")

# paragraph-style gallery: name -> (size_pt, weight_bold, italic, space_above,
# space_below, is_quote)
STYLES = {
    "Title":     (30, True,  False, 4, 10, False),
    "Heading 1": (22, True,  False, 16, 6, False),
    "Heading 2": (17, True,  False, 12, 4, False),
    "Heading 3": (14, True,  True,  10, 3, False),
    "Body":      (DEFAULT_SIZE, False, False, 0, 8, False),
    "Quote":     (13, False, True,  6, 8, True),
}
STYLE_ORDER = ["Body", "Title", "Heading 1", "Heading 2", "Heading 3", "Quote"]

# text / highlight colour swatches (papertone-friendly, with real ink colours)
TEXT_SWATCHES = ["#1A1916", "#4A463E", "#6E695E", "#C8341E", "#B24A18",
                 "#2F6B4F", "#33567F", "#5B3E7A", "#8A1C2B", "#FCFBF8"]
HL_SWATCHES = ["none", "#FBE7A0", "#CDE9C4", "#CFE4F2", "#F6CAD2", "#E6DAF2",
               "#EDE6D4", "#DCD7C9"]

# links render as an underlined ink-blue span; the href travels in the tag name
LINK_PREFIX = "link\x1f"
LINK_INK = "#33567F"
IMG_MAX_W = 560
OBJ = "￼"   # object-replacement char GtkTextBuffer uses per pixbuf/anchor

DESK = "#B3AD9E"        # the gray "desk" the sheet floats on
SHEET_SHADOW = 9        # px of falloff painted around the paper (see _draw_desk)
# Superscript/subscript are drawn at this fraction of the run's own size, and
# raised/lowered by SCRIPT_RISE_PT of it, so they track the paragraph style
# instead of pinning a size of their own.
SCRIPT_SCALE = 0.66
SCRIPT_RISE_PT = 0.34   # of the run's point size, up for super, down for sub
SHEET_SHADOW_DROP = 3   # how far the shadow sits below the sheet
SHEET = "#FCFBF8"       # warm paper

# ---- typographic defaults ----------------------------------------------------
# A keyboard has only the typewriter marks, so a document handed to someone else
# was full of "straight" quotes and hyphen-hyphen dashes. Every word processor
# fixes this as you type; this one now does too. The rule is the standard one: a
# quote opens after a space, a line start or an opening bracket, and closes
# otherwise. Left as typed inside Screenplay (Courier scripts and Fountain files
# want the typewriter marks) and inside Academic Notes (technical text).
SMART_QUOTES = {'"': ("“", "”"), "'": ("‘", "’")}
OPENS_AFTER = " \t\n ([{—–“‘/"


def smart_replacement(prev_char, text):
    """The typographic form of `text` typed after `prev_char`, or None to leave
    it alone. `prev_char` is "" at the very start of the document."""
    if len(text) != 1:
        return None                       # a paste, not a keystroke
    if text in SMART_QUOTES:
        opening, closing = SMART_QUOTES[text]
        return opening if (prev_char == "" or prev_char in OPENS_AFTER) \
            else closing
    if text == "-" and prev_char == "-":
        return "—"                   # -- becomes an em dash
    return None


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _rgb(hexcol):
    """(r, g, b) floats from '#RRGGBB', for cairo. Black on anything it cannot
    read — a colour is never worth failing a repaint for."""
    try:
        h = str(hexcol).lstrip("#")
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except (ValueError, IndexError, TypeError):
        return (0.0, 0.0, 0.0)


# ---- embedded pictures -------------------------------------------------------
# A .writer document carries the picture ITSELF, base64 in the JSON, not a path
# to it. A path is a promise about someone else's disk: delete the original, or
# pull out the USB stick it came from, and the document silently came back with
# an invisible object character where the photograph had been — including in
# every autosave and every undo step, so nothing could get it back.
def _b64_of(raw):
    try:
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return ""


def _pixbuf_from_b64(b64):
    """Decode an embedded picture. None (never an exception) if the data is
    missing or damaged, so one bad image can never fail a whole document."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
        loader = GdkPixbuf.PixbufLoader()
        loader.write(raw)
        loader.close()
        return loader.get_pixbuf()
    except Exception:
        return None


# =============================================================================
#  Wrapping toolbar row
# =============================================================================
class WrapRow(Gtk.Fixed):
    """A toolbar row whose control groups WRAP onto further lines when the
    window is too narrow to hold them side by side.

    A plain Gtk.Box cannot shrink below the sum of its children, so the full
    formatting toolbar made Writer's minimum window 1321px wide — on a 1024 or
    1366px panel the right-hand controls were simply off-screen and
    unreachable, because a window can never be smaller than its minimum. GTK3
    ships no wrapping container (Gtk.FlowBox spreads its children across the
    line), so this one does the arithmetic itself: minimum width = the widest
    single GROUP, natural width = everything on one line. A line only ever
    breaks at a group separator, so a group is never split in half."""

    # No __gtype_name__ on purpose: the audit harness re-imports this module
    # per render, and a fixed GType name would fail to re-register.

    def __init__(self, spacing=4, row_spacing=6):
        super().__init__()
        self._sp = spacing
        self._rsp = row_spacing
        self._items = []              # [(widget, padding), ...] in pack order

    def add_item(self, widget, pad=0):
        """Append a control (pad mirrors Gtk.Box's per-child padding)."""
        self._items.append((widget, pad))
        self.put(widget, 0, 0)

    # -- layout arithmetic -------------------------------------------------
    def _groups(self):
        """Visible children batched into groups; a separator starts a group."""
        groups, cur = [], []
        for w, pad in self._items:
            if not w.get_visible():
                continue
            if cur and isinstance(w, Gtk.Separator):
                groups.append(cur)
                cur = []
            cur.append((w, pad, w.get_preferred_width()[1]))
        if cur:
            groups.append(cur)
        return groups

    def _group_w(self, group):
        return sum(cw + 2 * pad + (self._sp if i else 0)
                   for i, (_w, pad, cw) in enumerate(group))

    def _lines(self, width, groups):
        lines, cur, x = [], [], 0
        for g in groups:
            gw = self._group_w(g)
            if cur and x + self._sp + gw > width:
                lines.append(cur)
                cur, x = [], 0
            x += gw + (self._sp if cur else 0)
            cur = cur + g
        if cur:
            lines.append(cur)
        return lines

    @staticmethod
    def _line_h(line):
        return max([w.get_preferred_height()[1] for w, _p, _cw in line] or [0])

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_width(self):
        groups = self._groups()
        if not groups:
            return 0, 0
        return (max(self._group_w(g) for g in groups),
                sum(self._group_w(g) for g in groups)
                + self._sp * (len(groups) - 1))

    def do_get_preferred_height_for_width(self, width):
        lines = self._lines(width, self._groups())
        h = sum(self._line_h(ln) for ln in lines)
        h += self._rsp * max(0, len(lines) - 1)
        return h, h

    def do_get_preferred_height(self):
        return self.do_get_preferred_height_for_width(
            self.do_get_preferred_width()[1])

    def do_size_allocate(self, alloc):
        self.set_allocation(alloc)
        y = alloc.y
        for line in self._lines(alloc.width, self._groups()):
            rh = self._line_h(line)
            x = alloc.x
            for i, (w, pad, cw) in enumerate(line):
                if i:
                    x += self._sp
                x += pad
                r = Gdk.Rectangle()
                r.x, r.y, r.width, r.height = x, y, cw, rh
                w.size_allocate(r)
                x += cw + pad
            y += rh + self._rsp


# =============================================================================
#  Inline table widget (anchored into the text flow)
# =============================================================================
class Table(Gtk.Box):
    """A simple bordered table embedded inline via a Gtk.TextChildAnchor. Cells
    are wrapped Gtk.TextViews; the surrounding editor talks to it through
    serialize()/from_data() and the row/column mutators."""

    def __init__(self, data=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_style_context().add_class("wtable")
        self._grid = Gtk.Grid()
        self._grid.get_style_context().add_class("wtablegrid")
        self.pack_start(self._grid, False, False, 0)
        self._cells = []           # list of rows; each a list of Gtk.TextView
        rows = data or [["", ""], ["", ""]]
        for r, row in enumerate(rows):
            crow = []
            for c, txt in enumerate(row):
                cell = self._make_cell(txt)
                self._grid.attach(cell["frame"], c, r, 1, 1)
                crow.append(cell)
            self._cells.append(crow)
        self.show_all()

    def _make_cell(self, text):
        frame = Gtk.Box()
        frame.get_style_context().add_class("wtcell")
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)   # a long word must not widen
        tv.get_style_context().add_class("wtcelltv")
        tv.set_size_request(120, -1)
        tv.set_left_margin(6)
        tv.set_right_margin(6)
        tv.set_pixels_above_lines(3)
        tv.set_pixels_below_lines(3)
        tv.get_buffer().set_text(text or "")
        frame.pack_start(tv, True, True, 0)
        return {"frame": frame, "tv": tv}

    def n_rows(self):
        return len(self._cells)

    def n_cols(self):
        return len(self._cells[0]) if self._cells else 0

    def add_row(self):
        c = self.n_cols()
        r = self.n_rows()
        crow = []
        for j in range(c):
            cell = self._make_cell("")
            self._grid.attach(cell["frame"], j, r, 1, 1)
            crow.append(cell)
        self._cells.append(crow)
        self.show_all()

    def add_col(self):
        c = self.n_cols()
        for r, crow in enumerate(self._cells):
            cell = self._make_cell("")
            self._grid.attach(cell["frame"], c, r, 1, 1)
            crow.append(cell)
        self.show_all()

    def del_row(self):
        if self.n_rows() <= 1:
            return
        r = self.n_rows() - 1
        for cell in self._cells[r]:
            self._grid.remove(cell["frame"])
        self._cells.pop()

    def del_col(self):
        if self.n_cols() <= 1:
            return
        c = self.n_cols() - 1
        for crow in self._cells:
            self._grid.remove(crow[c]["frame"])
            crow.pop()

    def serialize(self):
        out = []
        for crow in self._cells:
            out.append([cell["tv"].get_buffer().get_text(
                cell["tv"].get_buffer().get_start_iter(),
                cell["tv"].get_buffer().get_end_iter(), False)
                for cell in crow])
        return out


# =============================================================================
#  Writer
# =============================================================================
class Writer(nbapp.AppWindow):
    app_name = "Writer"
    menus = ("File", "Edit", "Format", "Insert", "Table", "View")

    # ---------------------------------------------------------------- init ----
    def __init__(self):
        self._zoom = DEFAULT_ZOOM
        super().__init__()
        self._install_css()

        self._loading = True
        self._file_dirty = False
        self._save_timer = None
        self._undo_timer = None
        self._count_timer = None          # debounced live word count
        self._restoring = False           # guard: rebuild must not checkpoint
        self._pending = set()             # queued char styles for the next run
        self._syncing = False             # guard the caret->toolbar sync
        self._smart_busy = False          # guard the smart-quote re-insert
        self._img_meta = {}               # pixbuf -> {"path":..., "ow":int}
        self._tables = {}                 # child-anchor -> Table
        self._find_matches = []           # (start_off, end_off) of find hits
        self._history = []
        self._hi = -1

        # _load_doc returns a _sane_doc, so every field below is already the
        # type it claims to be (see _sane_doc: an unchecked one used to kill the
        # constructor outright and leave Writer unlaunchable).
        doc = self._load_doc()
        self._page = doc["page"]
        self._header = doc["header"]
        self._footer = doc["footer"]
        self._page_numbers = doc["page_numbers"]
        self._path = doc["path"]

        # ---- chrome: two toolbar rows, ruler, sheet, find bar, status --------
        self.content.pack_start(self._build_toolbar(), False, False, 0)
        self.content.pack_start(self._build_ruler(), False, False, 0)
        self._findbar = self._build_findbar()
        self.content.pack_start(self._findbar, False, False, 0)
        self.content.pack_start(self._build_sheet(), True, True, 0)
        self.content.pack_start(self._build_statusbar(), False, False, 0)

        # seed the document from disk
        self._setup_base_tags()
        self._deserialize(doc)
        self._apply_page_geometry()

        self._loading = False
        self._push_history()              # baseline snapshot for undo
        self._sync_toolbar()
        self._update_status()
        # Reopening after edits that were autosaved but never written to the
        # named file used to look completely up to date — the document came back
        # with the file's name and a blank save chip, while the file on disk was
        # still the older version. Carry the unsaved state across the restart and
        # say so, so Ctrl+S is an obvious thing to do.
        if self._path and doc.get("dirty"):
            self._file_dirty = True
            self._set_save_chip(_t("Not saved to file"), ok=False)
        self.connect("destroy", self._on_destroy)

        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self._open_file(sys.argv[1])

        GLib.idle_add(self._first_focus)

    def _first_focus(self):
        try:
            self.body.grab_focus()
        except Exception:
            pass
        return False

    # ----------------------------------------------------------- toolbar ------
    def _tb_sep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("tbsep")
        return s

    def _iconbtn(self, icon, cmd, tip, style_extra=None):
        b = Gtk.Button()
        b.get_style_context().add_class("tbbtn")
        if style_extra:
            b.get_style_context().add_class(style_extra)
        b.set_tooltip_text(tip)
        try:
            b.add(nbicons.image(icon, 15, "#2A2620"))
        except Exception:
            b.set_label(cmd[:1].upper())
        return b

    def _colour_btn(self, which, tip):
        """A toolbar button that draws its own mark over a bar of the colour it
        will apply: a letter A for text colour, a highlighter nib for
        highlight. Drawn rather than iconed because the bar has to restate the
        current colour every time it changes."""
        b = Gtk.Button()
        b.get_style_context().add_class("tbbtn")
        b.set_tooltip_text(_t(tip))
        area = Gtk.DrawingArea()
        area.set_size_request(17, 18)
        area.connect("draw", self._draw_colour_btn, which)
        b.add(area)
        b.connect("clicked", lambda *_: self._pick_colour(which))
        b.nb_area = area
        return b

    def _draw_colour_btn(self, area, cr, which):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        ink = "#2A2620"
        cr.select_font_face("Nimbus Sans", 0, 1 if which == "fg" else 0)
        if which == "fg":
            cr.set_source_rgb(*_rgb(ink))
            cr.set_font_size(12)
            ext = cr.text_extents("A")
            cr.move_to((w - ext.width) / 2 - ext.x_bearing, h - 7)
            cr.show_text("A")
            colour = self._last_fg
        else:
            # a nib: a slanted marker body with a chisel tip
            cr.set_source_rgb(*_rgb(ink))
            cr.set_line_width(1.3)
            cr.move_to(w * 0.28, h - 8)
            cr.line_to(w * 0.60, 2.5)
            cr.line_to(w * 0.86, 5.0)
            cr.line_to(w * 0.54, h - 8)
            cr.close_path()
            cr.stroke()
            colour = self._last_hl
        if colour and colour != "none":
            cr.set_source_rgb(*_rgb(colour))
            cr.rectangle(1.5, h - 4.5, w - 3, 3.5)
            cr.fill()
        else:
            # "none" is a real choice for a highlight; show it as an empty slot
            cr.set_source_rgb(*_rgb("#B3AD9E"))
            cr.set_line_width(1)
            cr.rectangle(2, h - 4.5, w - 4, 3)
            cr.stroke()
        return False

    def _build_toolbar(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("toolbar")
        # WrapRow, not a Gtk.Box: the full toolbar is wider than a 1024 or 1366
        # panel, and its groups wrap onto a second line there instead of pushing
        # the window (and the controls at its right end) off the screen.
        row = WrapRow(spacing=4, row_spacing=6)
        row.get_style_context().add_class("tbrow")
        outer.pack_start(row, False, False, 0)

        # paragraph style gallery
        self.style_combo = Gtk.ComboBoxText()
        for s in STYLE_ORDER:
            self.style_combo.append_text(s)
        self.style_combo.set_active(0)
        self.style_combo.set_tooltip_text(_t("Paragraph style"))
        self.style_combo.get_style_context().add_class("tbcombo")
        self.style_combo.connect("changed", self._on_style_combo)
        row.add_item(self.style_combo)

        # font family
        self.font_combo = Gtk.ComboBoxText()
        for f in FONT_FAMILIES:
            self.font_combo.append_text(f)
        self.font_combo.set_active(FONT_FAMILIES.index(DEFAULT_FAMILY))
        self.font_combo.set_tooltip_text(_t("Font"))
        self.font_combo.get_style_context().add_class("tbcombo")
        self.font_combo.connect("changed", self._on_font_combo)
        row.add_item(self.font_combo)

        # font size (editable)
        self.size_combo = Gtk.ComboBoxText.new_with_entry()
        for s in FONT_SIZES:
            self.size_combo.append_text(str(s))
        self.size_combo.get_child().set_width_chars(3)
        self.size_combo.set_tooltip_text(_t("Size"))
        self.size_combo.get_style_context().add_class("tbcombo")
        self.size_combo.get_child().set_text(str(DEFAULT_SIZE))
        self.size_combo.connect("changed", self._on_size_combo)
        row.add_item(self.size_combo)

        row.add_item(self._tb_sep(), 4)

        # B / I / U / S
        #
        # The letters are marked up, not styled by CSS class. GTK3's
        # text-decoration-line is not an inherited property, so setting it on
        # the BUTTON node never reached the label inside it: the U and the S
        # rendered as a plain U and a plain S, which is the whole of what those
        # two buttons had to say for themselves. (font-weight and font-style
        # ARE inherited, which is why B and I looked right and hid the fault.)
        self._fmt_btns = {}
        for markup, cmd, tip in (
                ("<b>B</b>", "bold", "Bold (Ctrl+B)"),
                ("<i>I</i>", "italic", "Italic (Ctrl+I)"),
                ("<u>U</u>", "underline", "Underline (Ctrl+U)"),
                ("<s>S</s>", "strike", "Strikethrough"),
                ("x<sup>2</sup>", "super", "Superscript"),
                ("x<sub>2</sub>", "sub", "Subscript")):
            b = Gtk.Button()
            lab = Gtk.Label()
            lab.set_markup(markup)
            b.add(lab)
            b.get_style_context().add_class("tbbtn")
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, c=cmd: self._toggle_char(c))
            self._fmt_btns[cmd] = b
            row.add_item(b)

        # colours
        #
        # These were two of the same pencil glyph side by side — nothing
        # distinguished text colour from highlight, and neither showed what
        # colour it would apply. Both now draw their own mark over a bar of the
        # colour last chosen, the way every word processor shows them.
        self._last_fg = TEXT_SWATCHES[0]
        self._last_hl = HL_SWATCHES[1]
        self._fg_btn = self._colour_btn("fg", "Text colour")
        row.add_item(self._fg_btn)
        self._hl_btn = self._colour_btn("hl", "Highlight")
        row.add_item(self._hl_btn)

        row.add_item(self._tb_sep(), 4)

        # alignment
        for icon, just, tip in (("alignleft", "left", "Align left"),
                                ("aligncenter", "center", "Center"),
                                ("alignright", "right", "Align right"),
                                ("alignjustify", "fill", "Justify")):
            b = self._iconbtn(icon, just, tip)
            b.connect("clicked", lambda _b, j=just: self._set_align(j))
            self._fmt_btns["align:" + just] = b
            row.add_item(b)

        row.add_item(self._tb_sep(), 4)

        # lists + indent
        b = self._iconbtn("bullet", "bullet", "Bulleted list")
        b.connect("clicked", lambda *_: self._toggle_list("bullet"))
        self._fmt_btns["list:bullet"] = b
        row.add_item(b)
        b = self._iconbtn("number", "number", "Numbered list")
        b.connect("clicked", lambda *_: self._toggle_list("number"))
        self._fmt_btns["list:number"] = b
        row.add_item(b)
        b = self._iconbtn("outdent", "outdent", "Decrease indent")
        b.connect("clicked", lambda *_: self._indent(-1))
        row.add_item(b)
        b = self._iconbtn("indent", "indent", "Increase indent")
        b.connect("clicked", lambda *_: self._indent(1))
        row.add_item(b)

        row.add_item(self._tb_sep(), 4)

        # line spacing
        self.spacing_combo = Gtk.ComboBoxText()
        for s in ("Single", "1.5", "Double"):
            self.spacing_combo.append_text(s)
        self.spacing_combo.set_active(0)
        self.spacing_combo.set_tooltip_text(_t("Line spacing"))
        self.spacing_combo.get_style_context().add_class("tbcombo")
        self.spacing_combo.connect("changed", self._on_spacing_combo)
        row.add_item(self.spacing_combo)

        # insert link / image / table
        row.add_item(self._tb_sep(), 4)
        b = self._iconbtn("link", "link", "Insert link (Ctrl+K)")
        b.connect("clicked", lambda *_: self._insert_link())
        row.add_item(b)
        b = self._iconbtn("media", "image", "Insert image")
        b.connect("clicked", lambda *_: self._insert_image())
        row.add_item(b)
        b = self._iconbtn("table", "table", "Insert table")
        b.connect("clicked", lambda *_: self._insert_table())
        row.add_item(b)
        return outer

    # ------------------------------------------------------------- ruler ------
    def _build_ruler(self):
        self.ruler = Gtk.DrawingArea()
        self.ruler.set_size_request(-1, 22)
        self.ruler.get_style_context().add_class("ruler")
        self.ruler.connect("draw", self._draw_ruler)
        # A ruler you cannot set a tab on is a picture of a ruler. Click the
        # strip to put a stop where the pointer is; click an existing stop to
        # take it away.
        self.ruler.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.ruler.connect("button-press-event", self._on_ruler_press)
        self.ruler.set_tooltip_text(
            _t("Click to set a tab stop; click a stop to remove it"))
        return self.ruler

    # ---- tab stops -----------------------------------------------------------
    # Stored in inches from the text column's left edge, on the document (they
    # belong to the page, like its margins), so one set applies throughout and
    # both the screen and the PDF read the same list.
    TAB_HIT_IN = 0.06        # how close a click has to be to remove a stop

    def _tabs(self):
        t = self._page.get("tabs")
        if not isinstance(t, list):
            return []
        # de-duplicated as well as sorted: two stops at the same place are one
        # stop, and a document loaded from disk has not been through the
        # ruler's own set().
        out = set()
        for x in t:
            try:
                out.add(round(float(x), 4))
            except (TypeError, ValueError):
                pass
        return sorted(out)

    def _on_ruler_press(self, _w, ev):
        try:
            if ev.button != 1:
                return False
        except AttributeError:
            return False
        ppi = self._px_per_in()
        pw_in, _ph = self._page_dims_in()
        x0 = self._sheet_x(self.ruler.get_allocated_width(), pw_in * ppi)
        _mt, mr, _mb, ml = self._page["margins"]
        inches = (ev.x - x0) / ppi - ml
        col_in = pw_in - ml - mr
        if not (0.0 < inches < col_in):
            return True         # outside the text column: not a tab position
        stops = self._tabs()
        hit = [t for t in stops if abs(t - inches) <= self.TAB_HIT_IN]
        if hit:
            stops = [t for t in stops if t not in hit]
        else:
            stops.append(round(inches * 8) / 8.0)     # snap to 1/8 inch
        self._page["tabs"] = sorted(set(stops))
        self._apply_tabs()
        self._mark_dirty()
        self.ruler.queue_draw()
        return True

    def _clear_tabs(self):
        self._page["tabs"] = []
        self._apply_tabs()
        self._mark_dirty()
        self.ruler.queue_draw()

    def _apply_tabs(self):
        """Push the document's stops into the TextView. With none set the view
        keeps its own default interval, which is what a fresh document wants."""
        stops = self._tabs()
        ppi = self._px_per_in()
        if not stops:
            # PyGObject will not accept None here, so "no stops of our own"
            # is expressed as an even half-inch interval — which is what the
            # view's own default is anyway.
            ta = Pango.TabArray.new(12, True)
            for i in range(12):
                ta.set_tab(i, Pango.TabAlign.LEFT,
                           int(round((i + 1) * 0.5 * ppi)))
            self.body.set_tabs(ta)
            return
        ta = Pango.TabArray.new(len(stops), True)   # True = positions in pixels
        for i, inches in enumerate(stops):
            ta.set_tab(i, Pango.TabAlign.LEFT, int(round(inches * ppi)))
        self.body.set_tabs(ta)

    def _sheet_x(self, ruler_w, sheet_px):
        """The paper's left edge, in ruler coordinates.

        Measured from the sheet itself rather than assumed to be centred in the
        window: the canvas is inset by its vertical scrollbar (which used to
        leave the ruler's margin band ~10px out of step with the page), and a
        page wider than the window can be scrolled sideways under it."""
        try:
            xy = self.sheet.translate_coordinates(self.ruler, 0, 0)
            if xy is not None:
                return xy[0]
        except Exception:
            pass
        return max(0, (ruler_w - sheet_px) / 2.0)

    def _draw_ruler(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        PX_PER_IN = self._px_per_in()      # zoom-scaled, shadows the module
        cr.set_source_rgb(0x9C / 255, 0x96 / 255, 0x8B / 255)
        cr.paint()
        pw_in, _ph_in = self._page_dims_in()
        sheet_px = pw_in * PX_PER_IN
        x0 = self._sheet_x(w, sheet_px)
        # sheet band
        cr.set_source_rgb(0xEF / 255, 0xEC / 255, 0xE2 / 255)
        cr.rectangle(x0, 0, sheet_px, h)
        cr.fill()
        mt, mr, mb, ml = self._page["margins"]
        # margin region (paper column) brighter
        cr.set_source_rgb(0xFC / 255, 0xFB / 255, 0xF8 / 255)
        cr.rectangle(x0 + ml * PX_PER_IN, 0,
                     (pw_in - ml - mr) * PX_PER_IN, h)
        cr.fill()
        # inch ticks
        cr.set_source_rgb(0x6E / 255, 0x69 / 255, 0x5E / 255)
        cr.set_line_width(1)
        cr.select_font_face("Liberation Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(8)
        i = 0
        while i <= pw_in + 0.001:
            x = x0 + i * PX_PER_IN
            cr.move_to(x, h - 6)
            cr.line_to(x, h)
            cr.stroke()
            if i > 0 and i < pw_in:
                cr.move_to(x + 2, 9)
                cr.show_text(str(int(i)))
            i += 1
        # half-inch ticks, so the ruler can be read between the numbers
        i = 0.5
        while i < pw_in:
            x = x0 + i * PX_PER_IN
            cr.move_to(x, h - 4)
            cr.line_to(x, h)
            cr.stroke()
            i += 1.0
        # the tab stops themselves: a small filled marker at each, drawn in the
        # ink colour so they read as something set rather than as furniture
        cr.set_source_rgb(0x1A / 255, 0x19 / 255, 0x16 / 255)
        for t in self._tabs():
            x = x0 + (ml + t) * PX_PER_IN
            cr.move_to(x - 4, h - 1)
            cr.line_to(x + 4, h - 1)
            cr.line_to(x, h - 7)
            cr.close_path()
            cr.fill()
        return False

    # ------------------------------------------------------------ sheet -------
    def _build_sheet(self):
        scroll = Gtk.ScrolledWindow()
        # Horizontal AUTOMATIC: a Landscape or Legal page is 1056-1344px wide,
        # more than a 1024 or 1366px panel, and with NEVER the canvas forced the
        # whole window that wide — putting the right side of the paper (and of
        # the app) permanently off-screen. The scrollbar only appears when the
        # page really is wider than the window; when it is not, the viewport
        # still hands the desk column the full width, so no strip of unpainted
        # bin-window shows through.
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("desk")
        self._scroll = scroll

        # full-width WINDOWLESS box: its draw renders straight onto the viewport's
        # bin-window, so painting it fills the whole desk (incl. the strips beside
        # the centered sheet). This is what actually kills the black background.
        self._deskcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._deskcol.get_style_context().add_class("desk")
        self._deskcol.connect("draw", self._draw_desk)

        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sheet.get_style_context().add_class("sheet")
        self.sheet.set_halign(Gtk.Align.CENTER)
        self.sheet.set_valign(Gtk.Align.START)
        self.sheet.set_margin_top(28)
        self.sheet.set_margin_bottom(40)

        # header band (shown inside the top margin — see _apply_page_geometry)
        self.header_lbl = Gtk.Label(xalign=0)
        self.header_lbl.get_style_context().add_class("hfband")
        self.sheet.pack_start(self.header_lbl, False, False, 0)
        # Out of show_all()'s reach, then driven by hand from _refresh_hf_labels.
        # nbapp.run() calls show_all() AFTER the app is built, which un-hid these
        # two bands again: a document with no header still carried an empty 52px
        # strip at the top of the paper and another at the bottom, so the first
        # line sat half an inch lower on screen than in the exported PDF and the
        # page-break guides were out of step with the real page breaks.
        self.header_lbl.set_no_show_all(True)

        self.body = Gtk.TextView()
        # WORD_CHAR, not WORD: a word longer than the text column (a pasted
        # URL, a German compound, a big display size) cannot be broken under
        # WORD, so it runs off the paper — and the TextView's minimum width
        # grows with it, dragging the whole window wider than the screen. This
        # also matches the PDF/print renderer, which already wraps WORD_CHAR.
        self.body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.body.get_style_context().add_class("docbody")
        self.body.set_accepts_tab(True)
        # Match the Body style's own space-below on untagged text. Typed prose
        # carries no style: tag until a style is picked, yet the toolbar already
        # reads "Body" — so paragraphs in a fresh document ran together with no
        # gap while the app claimed they were Body (which sets 8px below).
        self.body.set_pixels_below_lines(STYLES["Body"][4])
        self.buf = self.body.get_buffer()
        self.buf.connect("changed", self._on_changed)
        self.buf.connect("insert-text", self._on_insert_before)
        self.buf.connect_after("insert-text", self._on_inserted)
        self.buf.connect("mark-set", self._on_mark_set)
        self.body.connect("button-release-event", lambda *_: (self._sync_toolbar(), False)[1])
        self.body.connect("key-press-event", self._on_body_key)
        self.body.connect_after("draw", self._draw_overlay)
        self.sheet.pack_start(self.body, True, True, 0)

        self.footer_lbl = Gtk.Label(xalign=0)
        self.footer_lbl.get_style_context().add_class("hfband")
        self.footer_lbl.set_no_show_all(True)
        self.sheet.pack_start(self.footer_lbl, False, False, 0)

        self._deskcol.pack_start(self.sheet, True, True, 0)
        scroll.add(self._deskcol)       # GTK wraps this in a Viewport
        vp = scroll.get_child()
        if vp is not None:
            vp.get_style_context().add_class("desk")
        # Keep the ruler over the paper as the canvas is scrolled sideways or
        # the sheet is re-centred (see _sheet_x).
        for adj in (scroll.get_hadjustment(),):
            if adj is not None:
                adj.connect("value-changed", lambda *_: self.ruler.queue_draw())
                adj.connect("changed", lambda *_: self.ruler.queue_draw())
        self.sheet.connect("size-allocate", lambda *_: self.ruler.queue_draw())
        return scroll

    def _draw_desk(self, widget, cr):
        # paint the whole desk-column allocation; the sheet draws over it
        alloc = widget.get_allocation()
        cr.set_source_rgb(0xB3 / 255.0, 0xAD / 255.0, 0x9E / 255.0)
        cr.rectangle(0, 0, alloc.width, alloc.height)
        cr.fill()

        # The paper's drop shadow. .sheet asks for one in CSS and never got it:
        # GTK clips a widget's drawing to its own allocation, and an outer
        # box-shadow falls entirely outside that, so the page met the desk on a
        # hard 1px line and read as a white rectangle rather than as paper.
        # Drawn here instead, under where the sheet is about to draw itself.
        sheet = getattr(self, "sheet", None)
        if sheet is None or not sheet.get_mapped():
            return False
        # Both boxes are windowless children of the same bin-window, so their
        # allocations share a coordinate space.
        sa = sheet.get_allocation()
        x, y = sa.x - alloc.x, sa.y - alloc.y
        w, h = sa.width, sa.height
        cr.save()
        # Clip the paper itself out, so the falloff is drawn on the desk only
        # and the sheet's area is not overdrawn N times on a machine with no
        # graphics acceleration.
        cr.rectangle(0, 0, alloc.width, alloc.height)
        cr.rectangle(x, y, w, h)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.clip()
        for i in range(SHEET_SHADOW, 0, -1):
            cr.set_source_rgba(0.10, 0.09, 0.08, 0.045)
            cr.rectangle(x - i, y - i + SHEET_SHADOW_DROP,
                         w + 2 * i, h + 2 * i)
            cr.fill()
        cr.restore()
        return False

    # ----------------------------------------------------------- find bar -----
    def _build_findbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("findbar")
        self.find_entry = Gtk.SearchEntry()
        nbicons.style_search_entry(self.find_entry)
        self.find_entry.set_placeholder_text(_t("Find"))
        self.find_entry.set_width_chars(24)
        self.find_entry.connect("search-changed", lambda *_: self._do_find())
        self.find_entry.connect("activate", lambda *_: self._find_next(1))
        bar.pack_start(self.find_entry, False, False, 0)
        prev = Gtk.Button(label="‹")
        prev.get_style_context().add_class("tbbtn")
        prev.set_tooltip_text(_t("Previous match"))
        prev.connect("clicked", lambda *_: self._find_next(-1))
        bar.pack_start(prev, False, False, 0)
        nxt = Gtk.Button(label="›")
        nxt.get_style_context().add_class("tbbtn")
        nxt.set_tooltip_text(_t("Next match"))
        nxt.connect("clicked", lambda *_: self._find_next(1))
        bar.pack_start(nxt, False, False, 0)
        self.find_count = Gtk.Label(label="")
        self.find_count.get_style_context().add_class("findcount")
        bar.pack_start(self.find_count, False, False, 0)
        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
                       False, False, 4)
        self.repl_entry = Gtk.Entry()
        self.repl_entry.set_placeholder_text(_t("Replace with"))
        self.repl_entry.set_width_chars(20)
        self.repl_entry.get_style_context().add_class("findinput")
        bar.pack_start(self.repl_entry, False, False, 0)
        rb = Gtk.Button(label=_t("Replace"))
        rb.get_style_context().add_class("tbbtn")
        rb.connect("clicked", lambda *_: self._replace_one())
        bar.pack_start(rb, False, False, 0)
        ra = Gtk.Button(label=_t("Replace all"))
        ra.get_style_context().add_class("tbbtn")
        ra.connect("clicked", lambda *_: self._replace_all())
        bar.pack_start(ra, False, False, 0)
        close = Gtk.Button()
        close.get_style_context().add_class("tbbtn")
        close.set_tooltip_text(_t("Close Find"))
        # a drawn close glyph, not a font "✕" (U+2715 is absent from the shipped
        # Nimbus Sans and would render as a tofu box)
        close.set_image(nbicons.image("wclose", 12, "#6E695E"))
        close.connect("clicked", lambda *_: self._toggle_find(False))
        bar.pack_end(close, False, False, 0)
        # Mark every control visible ONCE, then take the BAR itself out of
        # show_all()'s reach and drive it by hand from _toggle_find. Order
        # matters: gtk_widget_show_all() returns immediately on a widget with
        # no-show-all set, so showing the bar's children after that flag is on
        # is impossible — which is exactly why Find & Replace used to open as an
        # empty 13px strip with none of its controls in it.
        bar.show_all()
        bar.set_no_show_all(True)
        bar.hide()
        return bar

    # ---------------------------------------------------------- status --------
    def _build_statusbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        bar.get_style_context().add_class("statusbar")
        self.status = Gtk.Label(label="", xalign=0)
        self.status.get_style_context().add_class("statuslabel")
        bar.pack_start(self.status, True, True, 0)
        self.wc_label = Gtk.Label(label=_t("0 words"))
        self.wc_label.get_style_context().add_class("statuslabel")
        bar.pack_end(self.wc_label, False, False, 0)
        self.save_chip = Gtk.Label(label="")
        self.save_chip.get_style_context().add_class("savechip")
        bar.pack_end(self.save_chip, False, False, 0)
        return bar

    # =====================================================================
    #  Tag system
    # =====================================================================
    def _setup_base_tags(self):
        t = self.buf.get_tag_table()
        if t.lookup("bold"):
            return
        self.buf.create_tag("bold", weight=Pango.Weight.BOLD)
        self.buf.create_tag("italic", style=Pango.Style.ITALIC)
        self.buf.create_tag("underline", underline=Pango.Underline.SINGLE)
        self.buf.create_tag("strike", strikethrough=True)
        # Footnote markers, ordinals, formulae and citations all need these,
        # and a word processor without them makes the writer paste a "²" and
        # hope the font has one. `rise` is in Pango units and `scale` shrinks
        # the run relative to whatever size is in effect, so both follow the
        # paragraph style rather than pinning a point size of their own.
        self.buf.create_tag("super", rise=6 * Pango.SCALE,
                            scale=SCRIPT_SCALE)
        self.buf.create_tag("sub", rise=-4 * Pango.SCALE,
                            scale=SCRIPT_SCALE)
        for j, gj in (("left", Gtk.Justification.LEFT),
                      ("center", Gtk.Justification.CENTER),
                      ("right", Gtk.Justification.RIGHT),
                      ("fill", Gtk.Justification.FILL)):
            self.buf.create_tag("align:" + j, justification=gj)
        for name, (sz, bold, ital, above, below, quote) in STYLES.items():
            kw = {"size_points": float(sz),
                  "pixels_above_lines": above, "pixels_below_lines": below}
            if bold:
                kw["weight"] = Pango.Weight.BOLD
            if ital:
                kw["style"] = Pango.Style.ITALIC
            if quote:
                kw["left_margin"] = 34
                kw["paragraph_background"] = "#F1EEE6"
                kw["style"] = Pango.Style.ITALIC
            self.buf.create_tag("style:" + name, **kw)
        # NB: a TextTag's left-margin REPLACES the view's left margin (it does not
        # add to it), so these page-relative values are (re)set in
        # _apply_page_geometry once the page's own left margin is known.
        for lv in range(1, 9):
            self.buf.create_tag("indent:%d" % lv, left_margin=36 * lv)
        for name, below, inside in (("single", 8, 0), ("onehalf", 8, 10),
                                    ("double", 8, 22)):
            self.buf.create_tag("spacing:" + name,
                                pixels_below_lines=below, pixels_inside_wrap=inside)
        # A paragraph the writer has asked to begin a new sheet. It carries no
        # visual property of its own: the screen draws it in the overlay and
        # the PDF acts on it in _render_pdf, because "start a new page" is not
        # something a TextTag can express.
        self.buf.create_tag("pagebreak")
        self.buf.create_tag("list:bullet", left_margin=136)
        self.buf.create_tag("list:number", left_margin=142)

    def _tag(self, key):
        """Look up or lazily create a value-carrying tag (font/size/fg/hl/link)."""
        t = self.buf.get_tag_table()
        tag = t.lookup(key)
        if tag is not None:
            return tag
        if key.startswith("font:"):
            return self.buf.create_tag(key, family=key[5:])
        if key.startswith("size:"):
            return self.buf.create_tag(key, size_points=float(key[5:]))
        if key.startswith("fg:"):
            return self.buf.create_tag(key, foreground=key[3:])
        if key.startswith("hl:"):
            return self.buf.create_tag(key, background=key[3:])
        if key.startswith(LINK_PREFIX):
            return self.buf.create_tag(key, foreground=LINK_INK,
                                       underline=Pango.Underline.SINGLE)
        # unknown -> empty tag (keeps load robust)
        return self.buf.create_tag(key)

    def _sel_or_word(self):
        """The current selection, or an insertion-point empty range."""
        bounds = self.buf.get_selection_bounds()
        if bounds:
            return bounds
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        return (it.copy(), it.copy())

    def _para_bounds(self):
        """The full paragraph range covering the selection (or caret line)."""
        s, e = self._sel_or_word()
        s = s.copy(); e = e.copy()
        s.set_line_offset(0)
        if not e.ends_line():
            e.forward_to_line_end()
        # include the trailing newline so the para tag covers the break
        e.forward_char()
        return s, e

    # ---- character formatting -------------------------------------------
    # A run cannot be raised and lowered at once, so these two clear each other
    # the way the alignment buttons do. Without it the tags simply stacked and
    # the rises cancelled to roughly nothing, which reads as the buttons being
    # broken rather than as a conflict.
    SCRIPT_PAIR = {"super": "sub", "sub": "super"}

    def _toggle_char(self, cmd):
        other = self.SCRIPT_PAIR.get(cmd)
        if not self.buf.get_has_selection():
            # queue for the next typed run (standard word-processor behaviour)
            if cmd in self._pending:
                self._pending.discard(cmd)
            else:
                if other:
                    self._pending.discard(other)
                self._pending.add(cmd)
            self._sync_toolbar()
            return
        s, e = self.buf.get_selection_bounds()
        on = self._range_has_tag(s, e, cmd)
        self._checkpoint()
        if on:
            self.buf.remove_tag_by_name(cmd, s, e)
        else:
            if other:
                self.buf.remove_tag_by_name(other, s, e)
            self.buf.apply_tag_by_name(cmd, s, e)
        self._mark_dirty()
        self._sync_toolbar()

    def _range_has_tag(self, s, e, name):
        """True when the WHOLE of [s, e) already carries `name`.

        Walks to the tag's next toggle rather than over every character: the
        per-character loop this replaced made Select All + Ctrl+B pause for
        about half a second on a long document before the bold appeared."""
        tag = self.buf.get_tag_table().lookup(name)
        if tag is None:
            return False
        it = s.copy()
        if not it.has_tag(tag):
            return False
        if not it.forward_to_tag_toggle(tag):
            return True              # tagged through to the end of the buffer
        return it.compare(e) >= 0    # the run ends at/after the selection

    def _apply_value_tag(self, group_prefix, key):
        """Apply a value tag (font/size/fg/hl), first clearing others in its
        group across the range so only one wins."""
        if not self.buf.get_has_selection():
            self._pending = {p for p in self._pending
                             if not p.startswith(group_prefix)}
            self._pending.add(key)
            return
        s, e = self.buf.get_selection_bounds()
        self._checkpoint()
        self._clear_group(s, e, group_prefix)
        if not key.endswith(":none"):
            self.buf.apply_tag(self._tag(key), s, e)
        self._mark_dirty()

    def _clear_group(self, s, e, prefix):
        for tag in self._tags_with_prefix(prefix):
            self.buf.remove_tag(tag, s, e)

    def _tags_with_prefix(self, prefix):
        out = []
        self.buf.get_tag_table().foreach(
            lambda tg, _d: out.append(tg)
            if tg.get_property("name") and tg.get_property("name").startswith(prefix)
            else None, None)
        return out

    # ---- paragraph formatting -------------------------------------------
    def _set_style(self, name):
        s, e = self._para_bounds()
        self._checkpoint()
        self._clear_group(s, e, "style:")
        # Direct size overrides have to go, or the style does not take.
        #
        # GtkTextBuffer resolves two tags that set the same property by
        # PRIORITY, and priority is creation order. The "size:N" tags are made
        # lazily the first time the size dropdown is used, so they are created
        # after every "style:" tag and outrank all of them for ever after. The
        # visible result was that picking Heading 1 on text whose size had ever
        # been set turned it bold and left it at 12pt — the style appeared to do
        # nothing, and because the size tag is serialised, the document stayed
        # broken across save and reopen.
        #
        # Clearing them is also just what a word processor does: a paragraph
        # style is an instruction, not a suggestion, and choosing one is the
        # normal way to get a stray size off a line. Deliberate sizing after the
        # fact still wins, because the size tag is applied later and outranks
        # the style — which is the right way round.
        self._clear_group(s, e, "size:")
        self.buf.apply_tag_by_name("style:" + name, s, e)
        self._mark_dirty()
        self._sync_toolbar()

    def _set_align(self, j):
        s, e = self._para_bounds()
        self._checkpoint()
        self._clear_group(s, e, "align:")
        self.buf.apply_tag_by_name("align:" + j, s, e)
        self._mark_dirty()
        self._sync_toolbar()

    def _set_spacing(self, name):
        s, e = self._para_bounds()
        self._checkpoint()
        self._clear_group(s, e, "spacing:")
        self.buf.apply_tag_by_name("spacing:" + name, s, e)
        self._mark_dirty()

    def _indent(self, delta):
        s, e = self._para_bounds()
        self._checkpoint()
        cur = self._para_indent_level(s)
        new = _clamp(cur + delta, 0, 8)
        self._clear_group(s, e, "indent:")
        if new > 0:
            self.buf.apply_tag_by_name("indent:%d" % new, s, e)
        self._mark_dirty()

    def _para_indent_level(self, it):
        for lv in range(8, 0, -1):
            tag = self.buf.get_tag_table().lookup("indent:%d" % lv)
            if tag and it.has_tag(tag):
                return lv
        return 0

    def _toggle_list(self, kind):
        s, e = self._para_bounds()
        self._checkpoint()
        tag = self.buf.get_tag_table().lookup("list:" + kind)
        on = s.has_tag(tag)
        self._clear_group(s, e, "list:")
        if not on:
            self.buf.apply_tag_by_name("list:" + kind, s, e)
        self._mark_dirty()
        self.body.queue_draw()
        self._sync_toolbar()

    # =====================================================================
    #  Toolbar handlers + caret sync
    # =====================================================================
    def _on_style_combo(self, combo):
        if self._syncing:
            return
        # Select by INDEX, never by the visible text. nbi18n translates what a
        # combo shows, so get_active_text() returns "शीर्षक 1" on a Hindi system
        # and the tag lookup became apply_tag_by_name("style:शीर्षक 1") -> a
        # Gtk warning and a dropdown that silently did nothing. This was broken
        # in every non-English language, not just the new ones.
        i = combo.get_active()
        if 0 <= i < len(STYLE_ORDER):
            self._set_style(STYLE_ORDER[i])
            self.body.grab_focus()

    def _on_font_combo(self, combo):
        if self._syncing:
            return
        fam = combo.get_active_text()
        if fam:
            self._apply_value_tag("font:", "font:" + fam)
            self.body.grab_focus()

    def _on_size_combo(self, combo):
        if self._syncing:
            return
        txt = combo.get_active_text() or ""
        try:
            sz = int(float(txt))
        except (TypeError, ValueError):
            return
        sz = _clamp(sz, 6, 200)
        self._apply_value_tag("size:", "size:%d" % sz)

    # matches the order the spacing combo is built in (see _toolbar)
    SPACING_ORDER = ("single", "onehalf", "double")

    def _on_spacing_combo(self, combo):
        if self._syncing:
            return
        # by index, for the same reason as _on_style_combo
        i = combo.get_active()
        if 0 <= i < len(self.SPACING_ORDER):
            self._set_spacing(self.SPACING_ORDER[i])
            self.body.grab_focus()

    def _on_mark_set(self, buf, it, mark):
        if mark is buf.get_insert():
            self._pending.clear()
            self._sync_toolbar()

    def _sync_toolbar(self):
        if self._loading:
            return
        self._syncing = True
        try:
            it = self.buf.get_iter_at_mark(self.buf.get_insert())
            probe = it.copy()
            # get_selection_bounds() can return () even while get_has_selection()
            # is briefly True mid-mutation, so trust the tuple, not the flag.
            bounds = self.buf.get_selection_bounds()
            if bounds:
                probe = bounds[0].copy()
            elif not probe.starts_line():
                probe.backward_char()

            def active(name):
                tag = self.buf.get_tag_table().lookup(name)
                return bool(tag and probe.has_tag(tag)) or name in self._pending

            for cmd in ("bold", "italic", "underline", "strike",
                        "super", "sub"):
                self._flag(self._fmt_btns[cmd], active(cmd))
            for j in ("left", "center", "right", "fill"):
                self._flag(self._fmt_btns["align:" + j], active("align:" + j))
            for k in ("bullet", "number"):
                self._flag(self._fmt_btns["list:" + k], active("list:" + k))

            # paragraph style
            cur_style = "Body"
            for name in STYLES:
                tag = self.buf.get_tag_table().lookup("style:" + name)
                if tag and probe.has_tag(tag):
                    cur_style = name
                    break
            if cur_style in STYLE_ORDER:
                self.style_combo.set_active(STYLE_ORDER.index(cur_style))

            # font + size (from the value tags at the caret)
            fam = self._value_at(probe, "font:") or DEFAULT_FAMILY
            fam = LEGACY_FAMILIES.get(fam, fam)
            if fam in FONT_FAMILIES:
                self.font_combo.set_active(FONT_FAMILIES.index(fam))
            sz = self._value_at(probe, "size:")
            self.size_combo.get_child().set_text(sz or str(DEFAULT_SIZE))

            # spacing
            sp = "Single"
            for name, lbl in (("onehalf", "1.5"), ("double", "Double")):
                tag = self.buf.get_tag_table().lookup("spacing:" + name)
                if tag and probe.has_tag(tag):
                    sp = lbl
            idx = {"Single": 0, "1.5": 1, "Double": 2}[sp]
            self.spacing_combo.set_active(idx)
        finally:
            self._syncing = False

    def _value_at(self, it, prefix):
        for tag in it.get_tags():
            name = tag.get_property("name") or ""
            if name.startswith(prefix):
                return name[len(prefix):]
        return None

    def _flag(self, btn, on):
        ctx = btn.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("on")

    # =====================================================================
    #  Typing: pending styles + auto-lists + dirty/word-count
    # =====================================================================
    def _on_insert_before(self, buf, it, text, length):
        """Turn typewriter marks into real typography as they are typed: " and '
        become the matching curly quotes, -- becomes an em dash. Only ever fires
        for a single typed character, so pasted text is left exactly as it is."""
        if self._loading or self._restoring or self._smart_busy:
            return
        prev = ""
        probe = it.copy()
        if probe.backward_char():
            prev = probe.get_char()
        repl = smart_replacement(prev, text)
        if repl is None:
            return
        self._smart_busy = True
        try:
            buf.stop_emission_by_name("insert-text")
            if text == "-":                # swallow the first hyphen of the pair
                buf.delete(probe, it)
                it = buf.get_iter_at_mark(buf.get_insert())
            buf.insert(it, repl)
        finally:
            self._smart_busy = False

    def _on_inserted(self, buf, it, text, length):
        if self._loading or self._restoring:
            return
        if not self._pending:
            return
        start = it.copy()
        start.backward_chars(len(text))
        for key in list(self._pending):
            if key in ("bold", "italic", "underline", "strike",
                       "super", "sub"):
                buf.apply_tag_by_name(key, start, it)
            else:
                buf.apply_tag(self._tag(key), start, it)

    def _on_body_key(self, _w, ev):
        # Enter continues a list; Enter on an empty list item ends the list.
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                not (ev.state & Gdk.ModifierType.SHIFT_MASK):
            it = self.buf.get_iter_at_mark(self.buf.get_insert())
            for kind in ("bullet", "number"):
                tag = self.buf.get_tag_table().lookup("list:" + kind)
                if tag and it.has_tag(tag):
                    ls = it.copy(); ls.set_line_offset(0)
                    le = it.copy()
                    if not le.ends_line():
                        le.forward_to_line_end()
                    line_txt = self.buf.get_text(ls, le, False).strip()
                    if not line_txt:      # empty item -> drop out of the list
                        self._checkpoint()
                        self.buf.remove_tag(tag, ls, le)
                        return False
                    # let Return insert the break, then extend the tag onto it
                    GLib.idle_add(self._extend_list_tag, kind)
                    return False
        return False

    def _extend_list_tag(self, kind):
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        s = it.copy(); s.set_line_offset(0)
        e = it.copy()
        if not e.ends_line():
            e.forward_to_line_end()
        e.forward_char()
        self.buf.apply_tag_by_name("list:" + kind, s, e)
        self.body.queue_draw()
        return False

    def _on_changed(self, _buf):
        if self._loading:
            return
        self._mark_dirty()
        # Keep the whole-buffer read OFF the keystroke path: counting words means
        # copying the entire document out of the buffer, which grows with the
        # writer's own text. Coalesce it the way Journal does, so it settles once
        # a burst of typing ends instead of running on every keypress.
        if self._count_timer is None:
            self._count_timer = GLib.timeout_add(150, self._count_tick)
        self.body.queue_draw()
        if self._undo_timer:
            GLib.source_remove(self._undo_timer)
        self._undo_timer = GLib.timeout_add(600, self._undo_checkpoint_fire)

    def _count_tick(self):
        self._count_timer = None
        self._update_wordcount()
        return False

    def _undo_checkpoint_fire(self):
        self._undo_timer = None
        self._push_history()
        return False

    # =====================================================================
    #  Overlay draw: page breaks + list markers
    # =====================================================================
    def _draw_hard_breaks(self, view, cr):
        """A rule above every paragraph carrying a page break, labelled so it
        cannot be mistaken for the automatic page guides."""
        tag = self.buf.get_tag_table().lookup("pagebreak")
        if tag is None:
            return
        rect = view.get_visible_rect()
        it = view.get_iter_at_location(0, rect.y)[1]
        it.set_line_offset(0)
        w = view.get_allocated_width()
        while True:
            if it.has_tag(tag):
                y = view.get_line_yrange(it)[0]
                wy = view.buffer_to_window_coords(
                    Gtk.TextWindowType.TEXT, 0, y)[1]
                cr.save()
                cr.set_source_rgb(0xC8 / 255.0, 0x34 / 255.0, 0x1E / 255.0)
                cr.set_line_width(1.0)
                cr.move_to(0, wy - 5.5)
                cr.line_to(w, wy - 5.5)
                cr.stroke()
                self._page_label(cr, view.get_left_margin(), wy - 9,
                                 _t("Page break"))
                cr.restore()
            if not it.forward_line():
                break
            if view.get_line_yrange(it)[0] > rect.y + rect.height:
                break

    def _draw_overlay(self, view, cr):
        # Empty document: a ghost line where the first word will go. A blank
        # sheet is the hardest screen in a word processor to start on, and this
        # one said nothing at all — not even that the work is already being kept.
        # Drawn rather than packed, so it cannot affect the page's layout, and
        # laid out with Pango so a translated prompt gets proper glyph fallback.
        if self.buf.get_char_count() == 0 and not self._loading:
            wy = view.buffer_to_window_coords(Gtk.TextWindowType.TEXT, 0, 0)[1]
            lay = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription()
            fd.set_family(DEFAULT_FAMILY)
            fd.set_style(Pango.Style.ITALIC)
            fd.set_size(int(DEFAULT_SIZE * Pango.SCALE))
            lay.set_font_description(fd)
            lay.set_text(_t("Empty document"),
                         -1)
            cr.save()
            cr.set_source_rgb(0xA3 / 255.0, 0x9D / 255.0, 0x8F / 255.0)
            cr.move_to(view.get_left_margin(), wy)
            PangoCairo.show_layout(cr, lay)
            cr.restore()

        # Breaks the writer asked for, drawn where they are. Distinct from the
        # automatic guides below: those are where the paper happens to run out,
        # this is a decision, so it is a solid rule with a label rather than a
        # dotted hint.
        self._draw_hard_breaks(view, cr)

        # page-break guide lines
        pw_in, ph_in = self._page_dims_in()
        mt, mr, mb, ml = self._page["margins"]
        content_h = (ph_in - mt - mb) * PX_PER_IN
        if content_h < 60:
            return False
        _tx, ty = view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, 0, 0)
        vy0 = ty
        vy1 = ty + view.get_visible_rect().height
        alloc_w = view.get_allocated_width()
        page = 1
        y = content_h
        while y < vy1 + content_h:
            if y > vy0 - 4:
                wy = view.buffer_to_window_coords(
                    Gtk.TextWindowType.TEXT, 0, int(y))[1]
                cr.save()
                cr.set_source_rgba(0x6E / 255, 0x69 / 255, 0x5E / 255, 0.55)
                cr.set_line_width(1)
                cr.set_dash([5, 4])
                cr.move_to(0, wy + 0.5)
                cr.line_to(alloc_w, wy + 0.5)
                cr.stroke()
                cr.set_dash([])
                cr.set_source_rgba(0x6E / 255, 0x69 / 255, 0x5E / 255, 0.85)
                # translated AND drawn through Pango: as a raw literal on the
                # toy API this read "Page 2" in English in all seventeen
                # languages, and would have been invisible in the five whose
                # script Liberation Sans does not carry
                self._page_label(cr, alloc_w - 62, wy - 4,
                                 _t("Page %d") % (page + 1))
                cr.restore()
            page += 1
            y += content_h

        # list markers in the gutter
        self._draw_list_markers(view, cr, vy0, vy1)
        return False

    @staticmethod
    def _page_label(cr, x, y, text, size=9):
        """One small label on the page canvas, drawn with its BASELINE at y."""
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription("Liberation Sans")
        fd.set_absolute_size(size * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_text(text, -1)
        cr.move_to(x, y - lay.get_baseline() / Pango.SCALE)
        PangoCairo.show_layout(cr, lay)

    def _draw_list_markers(self, view, cr, vy0, vy1):
        bt = self.buf.get_tag_table().lookup("list:bullet")
        nt = self.buf.get_tag_table().lookup("list:number")
        if not (bt or nt):
            return
        pl = view.get_left_margin()
        it = self.buf.get_start_iter()
        num = 0
        prev_numbered = False
        while True:
            it.set_line_offset(0)
            is_b = bt and it.has_tag(bt)
            is_n = nt and it.has_tag(nt)
            if is_n:
                num = num + 1 if prev_numbered else 1
                prev_numbered = True
            else:
                prev_numbered = False
            if is_b or is_n:
                yb, hh = view.get_line_yrange(it)
                # get_line_yrange's height INCLUDES the paragraph's space-below,
                # so sitting the marker at a fraction of it drops the bullet
                # under its own line. Measure the glyph box instead and fall
                # back to the line height only if it is not laid out yet.
                th = view.get_iter_location(it).height or hh
                if vy0 - hh <= yb <= vy1 + hh:
                    wy = view.buffer_to_window_coords(
                        Gtk.TextWindowType.TEXT, 0, yb)[1]
                    cr.save()
                    cr.set_source_rgb(0x26 / 255, 0x24 / 255, 0x1F / 255)
                    cr.select_font_face("Liberation Serif",
                                        cairo.FONT_SLANT_NORMAL,
                                        cairo.FONT_WEIGHT_NORMAL)
                    cr.set_font_size(15)
                    mark = "•" if is_b else ("%d." % num)
                    # sit in the hanging gutter just left of the list text
                    cr.move_to(pl + 14, wy + th * 0.82)
                    cr.show_text(mark)
                    cr.restore()
            if not it.forward_line():
                break

    # =====================================================================
    #  Colours + link + image + table insert
    # =====================================================================
    def _pick_colour(self, which):
        swatches = TEXT_SWATCHES if which == "fg" else HL_SWATCHES
        title = "Text colour" if which == "fg" else "Highlight"
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbapp")
        box = dlg.get_content_area()
        box.get_style_context().add_class("swatchbox")
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.get_style_context().add_class("swatchtitle")
        box.pack_start(lbl, False, False, 0)
        grid = Gtk.FlowBox()
        grid.set_max_children_per_line(6)
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        for col in swatches:
            b = Gtk.Button()
            b.get_style_context().add_class("swatch")
            if col == "none":
                b.set_label(_t("None"))
            else:
                b.set_size_request(34, 26)
                prov = Gtk.CssProvider()
                prov.load_from_data((".sw%s{background:%s;}" %
                                     (col.strip("#"), col)).encode())
                b.get_style_context().add_provider(
                    prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                b.get_style_context().add_class("sw" + col.strip("#"))
            b.connect("clicked", lambda _b, c=col: dlg.response(
                1000 + swatches.index(c)))
            grid.add(b)
        box.pack_start(grid, False, False, 0)
        cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("tbbtn")
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp >= 1000:
            col = swatches[resp - 1000]
            if which == "fg":
                self._last_fg = col
                self._apply_value_tag("fg:", "fg:" + col)
            else:
                self._last_hl = col
                self._apply_value_tag("hl:", "hl:none" if col == "none"
                                      else "hl:" + col)
            # the button's bar states the colour it would apply next
            btn = self._fg_btn if which == "fg" else self._hl_btn
            btn.nb_area.queue_draw()
            self.body.grab_focus()

    def _insert_link(self):
        sel = ""
        if self.buf.get_has_selection():
            s, e = self.buf.get_selection_bounds()
            sel = self.buf.get_text(s, e, False)
        text, url = self._link_dialog(sel)
        if not url:
            return
        self._checkpoint()
        if self.buf.get_has_selection():
            s, e = self.buf.get_selection_bounds()
            self.buf.delete(s, e)
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        off = it.get_offset()
        self.buf.insert(it, text or url)
        s = self.buf.get_iter_at_offset(off)
        e = self.buf.get_iter_at_offset(off + len(text or url))
        self.buf.apply_tag(self._tag(LINK_PREFIX + url), s, e)
        self._mark_dirty()

    def _link_dialog(self, sel):
        dlg = Gtk.Dialog(title="Insert link", transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbapp")
        box = dlg.get_content_area()
        box.get_style_context().add_class("swatchbox")
        head = Gtk.Label(label=_t("Insert link"), xalign=0)   # undecorated: name it
        head.get_style_context().add_class("swatchtitle")
        box.pack_start(head, False, False, 0)
        te = Gtk.Entry(); te.set_placeholder_text(_t("Text")); te.set_text(sel)
        ue = Gtk.Entry(); ue.set_placeholder_text(_t("https://…"))
        te.get_style_context().add_class("findinput")
        ue.get_style_context().add_class("findinput")
        for cap, entry in ((_t("Link text"), te), (_t("URL"), ue)):
            lbl = Gtk.Label(label=cap, xalign=0)
            lbl.get_style_context().add_class("fieldcaption")
            lbl.set_margin_top(8)
            box.pack_start(lbl, False, False, 0)
            box.pack_start(entry, False, False, 0)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Insert", Gtk.ResponseType.OK)
        # The action, not a toolbar control — same fix as Page setup's Apply.
        ok.get_style_context().add_class("suggested-action")
        ue.connect("activate", lambda *_: dlg.response(Gtk.ResponseType.OK))
        dlg.show_all()
        r = dlg.run()
        text, url = te.get_text().strip(), ue.get_text().strip()
        dlg.destroy()
        return (text, url) if r == Gtk.ResponseType.OK else ("", "")

    def _insert_image(self):
        path = nbpicker.open_file(
            self, title="Insert image", start_dir=self._start_dir(),
            patterns=("*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp"))
        if not path:
            return
        # Read the FILE, not just a pixbuf: the bytes are what gets embedded in
        # the document (see _serialize). A .writer that only remembered a path
        # lost every picture the moment the original moved — and inserting from
        # a USB stick, which this OS invites, guarantees the original goes away.
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
        except Exception:
            self._flash("Couldn't load that image.")
            return
        ow = pb.get_width()
        if ow > IMG_MAX_W:
            scale = IMG_MAX_W / float(ow)
            pb = pb.scale_simple(IMG_MAX_W, int(pb.get_height() * scale),
                                 GdkPixbuf.InterpType.BILINEAR)
        self._checkpoint()
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        self.buf.insert_pixbuf(it, pb)
        self._img_meta[pb] = {"path": path, "ow": ow,
                              "b64": _b64_of(raw)}
        self._mark_dirty()

    def _insert_table(self, data=None):
        self._checkpoint()
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        if not it.starts_line():
            self.buf.insert(it, "\n")
            it = self.buf.get_iter_at_mark(self.buf.get_insert())
        anchor = self.buf.create_child_anchor(it)
        tbl = Table(data)
        self.body.add_child_at_anchor(tbl, anchor)
        tbl.show_all()
        self._tables[anchor] = tbl
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        self.buf.insert(it, "\n")
        self._mark_dirty()

    def _current_table(self):
        """The table whose cell (or anchor) the caret is nearest — best effort."""
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        probe = it.copy()
        probe.backward_char()
        anch = probe.get_child_anchor() or it.get_child_anchor()
        if anch and anch in self._tables:
            return self._tables[anch]
        # fall back to the last inserted table
        return next(iter(self._tables.values())) if self._tables else None

    def _table_op(self, op):
        tbl = self._current_table()
        if not tbl:
            self._flash("Put the cursor by a table first.")
            return
        self._checkpoint()
        {"add_row": tbl.add_row, "add_col": tbl.add_col,
         "del_row": tbl.del_row, "del_col": tbl.del_col}[op]()
        self._mark_dirty()

    # =====================================================================
    #  Find & Replace
    # =====================================================================
    def _toggle_find(self, show=None):
        vis = self._findbar.get_visible()
        show = (not vis) if show is None else show
        self._findbar.set_visible(show)
        if show:
            self.find_entry.grab_focus()
            self._do_find()
        else:
            self._clear_find_highlight()
            self.body.grab_focus()

    def _clear_find_highlight(self):
        tag = self.buf.get_tag_table().lookup("findhit")
        if tag:
            self.buf.remove_tag(tag, self.buf.get_start_iter(),
                                self.buf.get_end_iter())
        self._find_matches = []
        self.find_count.set_text("")

    def _do_find(self):
        if not self.buf.get_tag_table().lookup("findhit"):
            self.buf.create_tag("findhit", background="#FBE7A0")
        self._clear_find_highlight()
        needle = self.find_entry.get_text()
        if not needle:
            return
        hay = self.buf.get_text(self.buf.get_start_iter(),
                                self.buf.get_end_iter(), False).lower()
        n = needle.lower()
        i = hay.find(n)
        while i != -1:
            s = self.buf.get_iter_at_offset(i)
            e = self.buf.get_iter_at_offset(i + len(needle))
            self.buf.apply_tag_by_name("findhit", s, e)
            self._find_matches.append((i, i + len(needle)))
            i = hay.find(n, i + max(1, len(n)))
        self.find_count.set_text("%d match%s" %
                                 (len(self._find_matches),
                                  "" if len(self._find_matches) == 1 else "es"))
        self._find_i = -1
        if self._find_matches:
            self._find_next(1)

    def _find_next(self, direction):
        if not self._find_matches:
            self._do_find()
            if not self._find_matches:
                return
        self._find_i = (getattr(self, "_find_i", -1) + direction) % \
            len(self._find_matches)
        s_off, e_off = self._find_matches[self._find_i]
        s = self.buf.get_iter_at_offset(s_off)
        e = self.buf.get_iter_at_offset(e_off)
        self.buf.select_range(s, e)
        self.body.scroll_to_iter(s, 0.2, False, 0, 0)

    def _replace_one(self):
        if not self.buf.get_has_selection():
            self._find_next(1)
            return
        repl = self.repl_entry.get_text()
        self._checkpoint()
        s, e = self.buf.get_selection_bounds()
        off = s.get_offset()
        self.buf.delete(s, e)
        self.buf.insert(self.buf.get_iter_at_offset(off), repl)
        self._mark_dirty()
        self._do_find()

    def _replace_all(self):
        needle = self.find_entry.get_text()
        if not needle:
            return
        repl = self.repl_entry.get_text()
        # Recompute against the LIVE buffer before touching anything.
        # _find_matches is a list of character offsets captured when the find
        # last ran, and NOTHING re-runs it when the document changes: with the
        # find bar open, typing a word into the page (or one press of Replace)
        # moves every offset after the edit, and replacing at the stale ones cut
        # the prose apart mid-word in places the writer had not searched for.
        # It is also what makes the button work on the first press —
        # SearchEntry's "search-changed" is delayed, so a needle typed and
        # immediately replaced had no matches yet and this reported
        # "Replaced 0." while doing nothing at all.
        self._do_find()
        self._checkpoint()
        # work back-to-front so offsets stay valid
        matches = list(self._find_matches)
        for s_off, e_off in reversed(matches):
            s = self.buf.get_iter_at_offset(s_off)
            e = self.buf.get_iter_at_offset(e_off)
            self.buf.delete(s, e)
            self.buf.insert(self.buf.get_iter_at_offset(s_off), repl)
        self._flash("Replaced %d." % len(matches))
        self._mark_dirty()
        self._do_find()

    # =====================================================================
    #  Undo / redo (checkpoint history)
    # =====================================================================
    def _snapshot(self):
        snap = self._serialize()
        snap["_caret"] = self.buf.get_iter_at_mark(
            self.buf.get_insert()).get_offset()
        return snap

    def _push_history(self):
        snap = self._snapshot()
        if self._hi >= 0 and self._history[self._hi].get("body") == snap.get("body") \
                and self._history[self._hi].get("runs") == snap.get("runs"):
            self._history[self._hi] = snap    # coalesce (caret move only)
            return
        del self._history[self._hi + 1:]
        self._history.append(snap)
        if len(self._history) > 100:
            self._history.pop(0)
        self._hi = len(self._history) - 1

    def _checkpoint(self):
        """Flush any pending typing checkpoint before a structural edit so the
        edit is its own undo step."""
        if self._undo_timer:
            GLib.source_remove(self._undo_timer)
            self._undo_timer = None
        self._push_history()

    def _undo(self):
        if self._undo_timer:
            GLib.source_remove(self._undo_timer)
            self._undo_timer = None
            self._push_history()
        if self._hi <= 0:
            return
        self._hi -= 1
        self._restore(self._history[self._hi])

    def _redo(self):
        if self._hi >= len(self._history) - 1:
            return
        self._hi += 1
        self._restore(self._history[self._hi])

    def _restore(self, snap):
        self._restoring = True
        try:
            self._deserialize(snap)
            caret = snap.get("_caret", 0)
            it = self.buf.get_iter_at_offset(_clamp(caret, 0,
                                             self.buf.get_char_count()))
            self.buf.place_cursor(it)
        finally:
            self._restoring = False
        self._update_wordcount()
        self._sync_toolbar()
        self.body.queue_draw()
        # An undo changes the document exactly as much as typing does, so it has
        # to leave the same trail. The rebuild above goes through _deserialize,
        # which suppresses the buffer's "changed" handler on purpose, so this was
        # the one edit in Writer that never reached _mark_dirty: no autosave was
        # armed, the chip still read "● Saved 14:32" over a page that no longer
        # matched the file, and _confirm_discard — which asks _file_dirty and
        # nothing else — let File > New and File > Open throw the undone work
        # away without a word.
        self._mark_dirty()

    # =====================================================================
    #  Serialize / deserialize
    # =====================================================================
    SERIAL_TAGS = None   # computed lazily

    def _serial_tag_names(self):
        names = ["bold", "italic", "underline", "strike", "super", "sub"]
        names += ["align:" + j for j in ("left", "center", "right", "fill")]
        names += ["style:" + s for s in STYLES]
        names += ["indent:%d" % lv for lv in range(1, 9)]
        names += ["spacing:" + s for s in ("single", "onehalf", "double")]
        names += ["list:bullet", "list:number", "pagebreak"]
        # value tags + links currently in the table
        extra = []
        self.buf.get_tag_table().foreach(
            lambda tg, _d: extra.append(tg.get_property("name"))
            if tg.get_property("name") and (
                tg.get_property("name").startswith(("font:", "size:", "fg:", "hl:"))
                or tg.get_property("name").startswith(LINK_PREFIX)) else None,
            None)
        return names + extra

    def _serialize(self):
        start = self.buf.get_start_iter()
        end = self.buf.get_end_iter()
        body = self.buf.get_slice(start, end, True)   # ￼ for objects
        runs = []
        for name in self._serial_tag_names():
            tag = self.buf.get_tag_table().lookup(name)
            if tag is None:
                continue
            it = start.copy()
            if not it.has_tag(tag) and not it.forward_to_tag_toggle(tag):
                continue                       # tag never appears
            while True:
                if it.has_tag(tag):            # an ON region starts here
                    s_off = it.get_offset()
                    it.forward_to_tag_toggle(tag)   # -> OFF point (or end)
                    runs.append([s_off, it.get_offset(), name])
                    if it.compare(end) >= 0:
                        break
                elif not it.forward_to_tag_toggle(tag):
                    break
        # Images + tables sit at the object-replacement characters get_slice()
        # already put in `body`, so find them with a Python string scan. Stepping
        # the buffer character by character instead was O(document length) in
        # Python — on a 40,000-word document that one loop cost ~0.5s, and since
        # _serialize backs BOTH the undo checkpoint and the autosave, typing
        # stalled for half a second every second. Now it is O(objects).
        images, tables = [], []
        if self._img_meta or self._tables:
            i = body.find(OBJ)
            while i != -1:
                it = self.buf.get_iter_at_offset(i)
                pb = it.get_pixbuf()
                if pb is not None and pb in self._img_meta:
                    meta = self._img_meta[pb]
                    # "data" carries the picture itself. It is the SAME cached
                    # string object every time, so an undo snapshot costs a
                    # pointer rather than another copy of the image.
                    rec = {"off": i, "path": meta["path"], "ow": meta["ow"]}
                    if meta.get("b64"):
                        rec["data"] = meta["b64"]
                    images.append(rec)
                anch = it.get_child_anchor()
                if anch is not None and anch in self._tables:
                    tables.append({"off": i,
                                   "data": self._tables[anch].serialize()})
                i = body.find(OBJ, i + 1)
        return {"version": 2, "body": body, "runs": runs, "images": images,
                "tables": tables, "page": self._page, "header": self._header,
                "footer": self._footer, "page_numbers": self._page_numbers,
                "path": self._path, "dirty": self._file_dirty}

    def _deserialize(self, doc):
        """Rebuild the buffer from a document dict.

        THE ONE CHOKE POINT for every document that reaches the buffer — the
        autosave read at launch, File > Open, and an undo snapshot — so the
        normalising happens HERE and nowhere else. It must never raise: the
        first thing it does is empty the buffer, so a mid-way exception leaves
        the writer looking at a blank page with the real text nowhere on screen,
        and the autosave then writes that blank page to disk."""
        doc = _sane_doc(doc)
        was_loading = self._loading
        self._loading = True
        try:
            self.buf.set_text("")
            self._img_meta.clear()
            self._tables.clear()
            # v1 back-compat (title/subtitle/body plain). Tested on the CONTENT
            # rather than on `"runs" not in doc`, which _sane_doc always fills
            # in; a v1 document has no runs and carries its heading separately.
            if doc.get("version") != 2 and not doc["runs"] and (
                    doc.get("title") or doc.get("subtitle")):
                head = ""
                if doc.get("title"):
                    head += _sane_text(doc["title"]) + "\n"
                if doc.get("subtitle"):
                    head += _sane_text(doc["subtitle"]) + "\n"
                self.buf.set_text(head + doc["body"])
                return
            self.buf.set_text(doc["body"])
            n = self.buf.get_char_count()
            table = self.buf.get_tag_table()
            for s_off, e_off, name in doc["runs"]:
                # Offsets from a file are clamped to the text: get_iter_at_offset
                # already pins them, but a reversed pair would apply the tag
                # backwards, and a tag NAME we cannot build (say "size:abc")
                # must cost only itself.
                s_off = _clamp(s_off, 0, n)
                e_off = _clamp(e_off, 0, n)
                if e_off <= s_off:
                    continue
                try:
                    tag = table.lookup(name) or self._tag(name)
                except Exception:
                    continue
                if tag is None:
                    continue
                self.buf.apply_tag(tag, self.buf.get_iter_at_offset(s_off),
                                   self.buf.get_iter_at_offset(e_off))
            # replace ￼ placeholders (ascending; length-neutral)
            for img in sorted(doc["images"], key=lambda d: d["off"]):
                try:
                    self._reinsert_image(img)
                except Exception:
                    continue
            for tb in sorted(doc["tables"], key=lambda d: d["off"]):
                try:
                    self._reinsert_table(tb)
                except Exception:
                    continue
        finally:
            self._loading = was_loading
        self._update_wordcount()

    def _reinsert_image(self, img):
        off = img["off"]
        it = self.buf.get_iter_at_offset(off)
        if it.get_char() != OBJ:
            return
        # The embedded bytes come first; the path is only the fallback that
        # keeps documents written by the old path-only format readable.
        b64 = img.get("data") or ""
        pb = _pixbuf_from_b64(b64) if b64 else None
        if pb is None:
            b64 = ""
            try:
                with open(img["path"], "rb") as fh:
                    raw = fh.read()
                pb = GdkPixbuf.Pixbuf.new_from_file(img["path"])
                b64 = _b64_of(raw)     # embed it from now on
            except Exception:
                pb = None
        if pb is None:
            # Neither the bytes nor the original file: leave a visible
            # placeholder instead of an invisible ￼ nobody can select or
            # delete, and say so once.
            self._img_placeholder(off)
            return
        ow = img.get("ow", pb.get_width())
        if pb.get_width() > IMG_MAX_W:
            sc = IMG_MAX_W / float(pb.get_width())
            pb = pb.scale_simple(IMG_MAX_W, int(pb.get_height() * sc),
                                 GdkPixbuf.InterpType.BILINEAR)
        e = it.copy(); e.forward_char()
        self.buf.delete(it, e)
        it = self.buf.get_iter_at_offset(off)
        self.buf.insert_pixbuf(it, pb)
        self._img_meta[pb] = {"path": img.get("path", ""), "ow": ow,
                              "b64": b64}

    def _img_placeholder(self, off):
        """Replace the object character at `off` with a small grey card, so a
        picture that could not be restored is something the writer can see and
        delete rather than an invisible character."""
        try:
            pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                      160, 96)
            pb.fill(0xE4DFD2FF)
            it = self.buf.get_iter_at_offset(off)
            e = it.copy(); e.forward_char()
            self.buf.delete(it, e)
            it = self.buf.get_iter_at_offset(off)
            self.buf.insert_pixbuf(it, pb)
            self._img_meta[pb] = {"path": "", "ow": 160, "b64": ""}
        except Exception:
            pass

    def _reinsert_table(self, tb):
        off = tb["off"]
        it = self.buf.get_iter_at_offset(off)
        if it.get_char() != OBJ:
            return
        e = it.copy(); e.forward_char()
        self.buf.delete(it, e)
        it = self.buf.get_iter_at_offset(off)
        anchor = self.buf.create_child_anchor(it)
        tbl = Table(tb.get("data"))
        self.body.add_child_at_anchor(tbl, anchor)
        tbl.show_all()
        self._tables[anchor] = tbl

    # =====================================================================
    #  Page geometry + header/footer
    # =====================================================================
    def _px_per_in(self):
        """Screen pixels per inch at the current magnification."""
        return PX_PER_IN * getattr(self, "_zoom", DEFAULT_ZOOM)

    def _set_zoom(self, z):
        """Magnify the page. Pango's resolution carries the text (so point-sized
        tags scale too) and _apply_page_geometry carries the paper."""
        z = min(ZOOM_STEPS[-1], max(ZOOM_STEPS[0], float(z)))
        self._zoom = z
        try:
            gi.require_version("PangoCairo", "1.0")
            from gi.repository import PangoCairo
            PangoCairo.context_set_resolution(self.body.get_pango_context(),
                                              PX_PER_IN * z)
        except Exception:                       # noqa: BLE001
            pass        # magnification is not worth failing the app for
        self._apply_page_geometry()
        self.body.queue_resize()
        self._update_status()

    def _zoom_step(self, delta):
        cur = getattr(self, "_zoom", DEFAULT_ZOOM)
        # nearest step, then move
        idx = min(range(len(ZOOM_STEPS)),
                  key=lambda i: abs(ZOOM_STEPS[i] - cur))
        self._set_zoom(ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1,
                                             idx + delta))])

    def _page_dims_in(self):
        w, h = PAGE_SIZES.get(self._page.get("size", "Letter"),
                              PAGE_SIZES["Letter"])
        if self._page.get("orientation") == "landscape":
            w, h = h, w
        return w, h

    def _apply_page_geometry(self):
        pw_in, ph_in = self._page_dims_in()
        mt, mr, mb, ml = self._page["margins"]
        PX_PER_IN = self._px_per_in()          # zoom-scaled, shadows the module
        sheet_px = int(pw_in * PX_PER_IN)
        self.sheet.set_size_request(sheet_px, int(ph_in * PX_PER_IN))
        pl = int(ml * PX_PER_IN)
        self.body.set_left_margin(pl)
        self.body.set_right_margin(int(mr * PX_PER_IN))
        # The header/footer bands live INSIDE the page margin, exactly as the PDF
        # renderer draws them (_pdf_furniture puts the header 14pt above the text
        # top and the footer 22pt below the text bottom). They are packed above
        # and below the body, so their height has to come OUT of the body's own
        # margin — otherwise every band pushed the text further down the paper
        # than the exported page, and the first line no longer sat one margin
        # from the paper's edge.
        self._refresh_hf_labels()
        mt_px, mb_px = int(mt * PX_PER_IN), int(mb * PX_PER_IN)
        hb = self._band_height(self.header_lbl)
        fb = self._band_height(self.footer_lbl)
        h_gap = max(0, mt_px - hb - 19) if hb else 0   # 19px ~ the PDF's 14pt
        f_gap = max(0, mb_px - fb - 29) if fb else 0   # 29px ~ the PDF's 22pt
        self.header_lbl.set_margin_top(h_gap)
        self.footer_lbl.set_margin_bottom(f_gap)
        self.body.set_top_margin(max(4, mt_px - h_gap - hb))
        self.body.set_bottom_margin(max(4, mb_px - f_gap - fb))
        # list/indent/quote margins are page-relative (tag left-margin replaces the
        # view margin, so bake the page's left margin into each here).
        tt = self.buf.get_tag_table()
        for name, extra in (("list:bullet", 40), ("list:number", 46)):
            t = tt.lookup(name)
            if t:
                t.set_property("left-margin", pl + extra)
        for lv in range(1, 9):
            t = tt.lookup("indent:%d" % lv)
            if t:
                t.set_property("left-margin", pl + 36 * lv)
        q = tt.lookup("style:Quote")
        if q:
            q.set_property("left-margin", pl + 34)
        # header/footer bands are inset to the page's own side margins
        self.header_lbl.set_margin_start(pl)
        self.header_lbl.set_margin_end(int(mr * PX_PER_IN))
        self.footer_lbl.set_margin_start(pl)
        self.footer_lbl.set_margin_end(int(mr * PX_PER_IN))
        self._apply_tabs()          # stops are in pixels, so they follow zoom
        self.ruler.queue_draw()
        self.body.queue_draw()

    @staticmethod
    def _band_height(lbl):
        """The vertical space a header/footer band actually takes, or 0 when it
        is empty and hidden (a hidden child of a Gtk.Box takes no room)."""
        if not lbl.get_visible():
            return 0
        try:
            return max(1, lbl.get_preferred_height()[1])
        except Exception:
            return 15

    def _refresh_hf_labels(self):
        self.header_lbl.set_text(self._header or "")
        self.header_lbl.set_visible(bool(self._header))
        foot = self._footer or ""
        if self._page_numbers:
            foot = (foot + "     Page {page}").strip()
        self.footer_lbl.set_text(foot.replace("{page}", "1").replace("{pages}", "1")
                                 .replace("{title}", self._doc_title()))
        self.footer_lbl.set_visible(bool(foot))

    def _doc_title(self):
        if self._path:
            return os.path.splitext(os.path.basename(self._path))[0]
        first = self.buf.get_start_iter()
        e = first.copy()
        if not e.ends_line():
            e.forward_to_line_end()
        return self.buf.get_text(first, e, False)[:60] or "Untitled"

    def _page_setup(self):
        dlg = Gtk.Dialog(title="Page setup", transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbapp")
        box = dlg.get_content_area()
        box.get_style_context().add_class("swatchbox")
        # The dialog is undecorated, so its window title never shows — name it
        # inside the card, as the colour picker does.
        head = Gtk.Label(label=_t("Page setup"), xalign=0)
        head.get_style_context().add_class("swatchtitle")
        box.pack_start(head, False, False, 0)
        grid = Gtk.Grid(row_spacing=10, column_spacing=10)
        box.pack_start(grid, False, False, 0)

        # The rows are appended in PAGE_SIZES order and read back by INDEX
        # through this list — never by get_active_text(). nbi18n translates
        # what a ComboBoxText shows ("Letter" is "Carta" in Spanish), so the
        # visible text came back translated and was stored as the page size:
        # the geometry silently fell back to Letter, and the NEXT open of this
        # dialog died on list(PAGE_SIZES).index("Carta"). Same defect class as
        # the style/spacing combos above.
        size_keys = list(PAGE_SIZES)
        size_c = Gtk.ComboBoxText()
        for s in size_keys:
            size_c.append_text(s)
        cur_size = self._page.get("size", "Letter")
        size_c.set_active(size_keys.index(cur_size) if cur_size in size_keys
                          else size_keys.index("Letter"))
        orient_c = Gtk.ComboBoxText()
        orient_c.append_text("Portrait"); orient_c.append_text("Landscape")
        orient_c.set_active(1 if self._page.get("orientation") == "landscape" else 0)
        grid.attach(Gtk.Label(label=_t("Size"), xalign=0), 0, 0, 1, 1)
        grid.attach(size_c, 1, 0, 3, 1)
        grid.attach(Gtk.Label(label=_t("Orientation"), xalign=0), 0, 1, 1, 1)
        grid.attach(orient_c, 1, 1, 3, 1)
        # Margins: four spin boxes, each under its own name — unlabelled they
        # were four identical fields with no way to tell which edge is which.
        grid.attach(Gtk.Label(label=_t("Margins (inches)"), xalign=0), 0, 2, 1, 1)
        mbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        spins = []
        for i, (m, name) in enumerate(zip(self._page["margins"],
                                          ("Top", "Right", "Bottom", "Left"))):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            cap = Gtk.Label(label=_t(name), xalign=0)
            cap.get_style_context().add_class("fieldcaption")
            sp = Gtk.SpinButton.new_with_range(0.2, 3.0, 0.1)
            sp.set_value(m)
            sp.set_tooltip_text(_t(name) + " " + _t("margin"))
            spins.append(sp)
            cell.pack_start(cap, False, False, 0)
            cell.pack_start(sp, False, False, 0)
            mbox.pack_start(cell, False, False, 0)
        grid.attach(mbox, 1, 2, 3, 1)

        he = Gtk.Entry(); he.set_text(self._header)
        fe = Gtk.Entry(); fe.set_text(self._footer)
        he.get_style_context().add_class("findinput")
        fe.get_style_context().add_class("findinput")
        grid.attach(Gtk.Label(label=_t("Header"), xalign=0), 0, 3, 1, 1)
        grid.attach(he, 1, 3, 3, 1)
        grid.attach(Gtk.Label(label=_t("Footer"), xalign=0), 0, 4, 1, 1)
        grid.attach(fe, 1, 4, 3, 1)
        # The header/footer accept {page} and {title}; nothing on screen said so.
        hint = Gtk.Label(
            label=_t("Type {page} for the page number, {title} for the "
                     "document name."), xalign=0)
        hint.get_style_context().add_class("fieldcaption")
        grid.attach(hint, 1, 5, 3, 1)
        pn = Gtk.CheckButton(label=_t("Page numbers"))
        pn.set_active(self._page_numbers)
        grid.attach(pn, 1, 6, 3, 1)

        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Apply", Gtk.ResponseType.OK)
        # The action, not a toolbar control: .tbbtn made Apply identical to
        # Cancel. suggested-action is the shared primary treatment.
        ok.get_style_context().add_class("suggested-action")
        dlg.show_all()
        r = dlg.run()
        if r == Gtk.ResponseType.OK:
            si = size_c.get_active()
            self._page = {
                "size": size_keys[si] if 0 <= si < len(size_keys) else cur_size,
                "orientation": "landscape" if orient_c.get_active() else "portrait",
                "margins": [round(sp.get_value(), 2) for sp in spins]}
            self._header = he.get_text()
            self._footer = fe.get_text()
            self._page_numbers = pn.get_active()
            self._apply_page_geometry()
            self._mark_dirty()
        dlg.destroy()

    # =====================================================================
    #  Persistence + files
    # =====================================================================
    @staticmethod
    def _is_writer_store(d):
        """Whether `d` is recognisably the file this app writes.

        _serialize ALWAYS emits a string "body" and a list "runs" — a document
        with no text at all still emits "" and [] — so this cannot misfire on
        our own store, including a brand-new empty one. Anything else is a file
        we did not write (or one a failed write left half-formed), and
        _load_doc moves it aside rather than saving over it."""
        return (isinstance(d, dict) and isinstance(d.get("body"), str)
                and isinstance(d.get("runs"), list))

    def _load_doc(self):
        """The session-recovery document, normalised by _sane_doc so no field can
        raise on the way into the constructor.

        A store that parses but is not this app's shape is QUARANTINED here,
        before the first autosave can replace it. See
        nbapp.quarantine_unrecognized for why the .bak alone loses that file on
        the second open."""
        try:
            with open(DOC_FILE) as fh:
                d = json.load(fh)
            if self._is_writer_store(d):
                return _sane_doc(d)
            # Not ours. Salvage whatever text is in there onto the page anyway —
            # it is still the user's writing — but keep the original bytes.
            nbapp.quarantine_unrecognized(DOC_FILE)
            if isinstance(d, dict):
                return _sane_doc(d)
        except (OSError, ValueError):
            pass
        return _sane_doc({"version": 2, "body": "", "runs": []})

    def _save_autosave(self):
        try:
            nbapp.atomic_write_json(DOC_FILE, self._serialize())
        except OSError:
            pass

    def _mark_dirty(self, *_):
        if self._loading:
            return
        self._file_dirty = True
        self._set_save_chip(_t("Editing"), ok=False)
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(900, self._autosave_fire)

    def _autosave_fire(self):
        self._save_timer = None
        self._save_autosave()
        return False

    def _start_dir(self):
        return DOCS_DIR if os.path.isdir(DOCS_DIR) else HOME

    def _update_status(self):
        if self._path:
            self.status.set_text(os.path.basename(self._path))
        else:
            self.status.set_text(_t("Unsaved document"))

    def _update_wordcount(self):
        text = self.buf.get_text(self.buf.get_start_iter(),
                                 self.buf.get_end_iter(), False)
        words = len(text.split())
        # Words only. The character half read "1 chars" on a one-letter
        # document, and "chars" is an abbreviation that appears nowhere else in
        # the OS — Academics, Novel and Screenplay all show a word count and
        # stop there.
        self.wc_label.set_text(_t("%d word%s") %
                               (words, "" if words == 1 else "s"))

    # The save indicator, in the one form the rest of the OS already uses: a
    # coloured dot and a time (Journal, Novel, Cookbook, Screenplay all read
    # "● Saved 14:32"). Writer alone showed a bare word with no dot and no time,
    # so "Saved" gave no clue whether it meant a moment ago or an hour ago —
    # which is the only thing that word is there to tell you.
    _CHIP_OK = "#7FA98C"
    _CHIP_BAD = "#C8341E"

    def _set_save_chip(self, text, ok):
        self.save_chip.set_markup(
            '<span foreground="%s">● </span>%s'
            % (self._CHIP_OK if ok else self._CHIP_BAD,
               GLib.markup_escape_text(text)))
        ctx = self.save_chip.get_style_context()
        (ctx.remove_class if ok else ctx.add_class)("dirty")

    def _clear_save_chip(self):
        self.save_chip.set_text("")
        self.save_chip.get_style_context().remove_class("dirty")

    def _flash(self, msg, secs=2.6):
        self.status.set_text(msg)
        GLib.timeout_add(int(secs * 1000), self._update_status_return)

    def _update_status_return(self):
        self._update_status()
        return False

    def _confirm_discard(self):
        """Ask before replacing a document that has unsaved changes.

        A papertone card, like every other dialog in this app and in the OS —
        it used to be a stock Gtk.MessageDialog, which arrived wearing the
        window manager's title bar and the host's button chrome, the one screen
        in Writer that did not look like Notebook OS. It also says WHICH
        document is at stake and what happens, rather than a bare question."""
        if not self._file_dirty:
            return True
        name = os.path.basename(self._path) if self._path else None
        dlg = Gtk.Dialog(title="Discard changes?", transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbapp")
        box = dlg.get_content_area()
        box.get_style_context().add_class("swatchbox")
        head = Gtk.Label(label=_t("Discard changes?"), xalign=0)
        head.get_style_context().add_class("swatchtitle")
        box.pack_start(head, False, False, 0)
        msg = Gtk.Label(
            label=(_t("“%s” has unsaved changes. They will be lost.")
                   % name) if name else
            _t("This document has never been saved. Its text will be lost."),
            xalign=0)
        msg.set_line_wrap(True)
        msg.set_width_chars(38)
        msg.set_max_width_chars(40)
        msg.get_style_context().add_class("dlgmsg")
        box.pack_start(msg, False, False, 0)
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("tbbtn")
        ok = dlg.add_button(_t("Discard"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("dangerbtn")
        dlg.show_all()
        cancel.grab_focus()      # a stray Return must never discard the work
        r = dlg.run()
        dlg.destroy()
        return r == Gtk.ResponseType.OK

    def _file_new(self):
        if not self._confirm_discard():
            return
        self._restoring = True
        try:
            self.buf.set_text("")
            self._img_meta.clear(); self._tables.clear()
        finally:
            self._restoring = False
        self._path = None
        self._file_dirty = False
        self._history = []; self._hi = -1
        self._push_history()
        self._clear_save_chip()
        self._update_status(); self._update_wordcount()

    def _file_open(self):
        if not self._confirm_discard():
            return
        path = nbpicker.open_file(
            self, title="Open", start_dir=self._start_dir(),
            patterns=("*.writer", "*.txt", "*.md"))
        if path:
            self._open_file(path)

    def _open_file(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            self._flash("Couldn't open that file.")
            return
        if path.endswith(".writer"):
            try:
                doc = json.loads(raw)
                if not isinstance(doc, dict):
                    # valid JSON but not a document object (a bare list/number/
                    # string from a corrupt or foreign file) — fall through to
                    # plain text so the open never crashes on doc.get(...)
                    raise ValueError("not a document object")
            except ValueError:
                doc = {"version": 2, "body": raw, "runs": []}
        else:
            doc = {"version": 2, "body": raw, "runs": []}
        # Normalised BEFORE anything on screen changes: a .writer whose fields
        # are the wrong type used to blank the buffer and then raise, losing the
        # open document as well as failing to load the chosen one.
        had_page = isinstance(doc.get("page"), dict)
        doc = _sane_doc(doc)
        # A plain .txt/.md carries no page setup, so the sheet the writer has
        # already set up is left alone rather than reset to the default.
        if had_page:
            self._page = doc["page"]
        self._header = doc["header"]
        self._footer = doc["footer"]
        self._page_numbers = doc["page_numbers"]
        self._deserialize(doc)
        self._apply_page_geometry()
        self._path = path
        self._file_dirty = False
        self._history = []; self._hi = -1
        self._push_history()
        self._clear_save_chip()
        self._update_status(); self._update_wordcount(); self._sync_toolbar()

    def _file_save(self):
        if not self._path:
            return self._file_save_as()
        self._write_file(self._path)

    def _file_save_as(self):
        path = nbpicker.save_file(
            self, title="Save As", start_dir=self._start_dir(),
            suggested_name=(self._doc_title() or "Untitled") + ".writer",
            patterns=("*.writer", "*.txt", "*.md"), default_ext=".writer")
        if path:
            self._write_file(path)

    def _plain_text_losses(self):
        """What a .txt/.md write would throw away, as a list of counted phrases
        ("3 pictures"). Empty when the document is already plain text."""
        doc = self._serialize()
        bits = []
        n = len(doc.get("runs") or [])
        if n:
            bits.append(_t("%d formatting run%s") % (n, "" if n == 1 else "s"))
        n = len(doc.get("tables") or [])
        if n:
            bits.append(_t("%d table%s") % (n, "" if n == 1 else "s"))
        n = len(doc.get("images") or [])
        if n:
            bits.append(_t("%d picture%s") % (n, "" if n == 1 else "s"))
        return bits

    def _confirm_plain_text(self, path):
        """Ask before writing this document to a plain-text file that cannot
        hold its formatting.

        .txt and .md sit in the Save As picker as equal choices, so a writer
        could pick one, read the same "Saved 19:43" a lossless save gives, and
        only discover months later that the bold, the tables and the pictures
        had been dropped on the way to disk. Novel and Screenplay both guard
        this; Writer said nothing. Returns True when it is safe to write."""
        losses = self._plain_text_losses()
        if not losses:
            return True                # nothing to lose: no card, no friction
        name = os.path.basename(path)
        if len(losses) == 1:
            what = losses[0]
        else:
            what = _t(", ").join(losses[:-1]) + _t(" and ") + losses[-1]
        dlg = Gtk.Dialog(title="Save as plain text?", transient_for=self,
                         modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbapp")
        box = dlg.get_content_area()
        box.get_style_context().add_class("swatchbox")
        head = Gtk.Label(label=_t("Save as plain text?"), xalign=0)
        head.get_style_context().add_class("swatchtitle")
        box.pack_start(head, False, False, 0)
        msg = Gtk.Label(
            label=_t("“%s” is a plain text file. The %s in this document "
                     "will not be saved. Save as a Writer document to keep "
                     "them.") % (name, what),
            xalign=0)
        msg.set_line_wrap(True)
        msg.set_width_chars(38)
        msg.set_max_width_chars(40)
        msg.get_style_context().add_class("dlgmsg")
        box.pack_start(msg, False, False, 0)
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("tbbtn")
        ok = dlg.add_button(_t("Save as Text"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("dangerbtn")
        dlg.show_all()
        cancel.grab_focus()      # a stray Return must never drop the formatting
        r = dlg.run()
        dlg.destroy()
        return r == Gtk.ResponseType.OK

    def _write_file(self, path):
        if not path.endswith(".writer") and not self._confirm_plain_text(path):
            return
        try:
            if path.endswith(".writer"):
                doc = self._serialize()
                doc["path"] = path
                nbapp.atomic_write_json(path, doc, ensure_ascii=False)
            else:
                text = self.buf.get_text(self.buf.get_start_iter(),
                                         self.buf.get_end_iter(), False)
                # atomic_write_text, never open(path, "w"): a plain open
                # TRUNCATES the destination before the first new byte arrives,
                # so a save that cannot finish (a full disk, a USB stick pulled
                # mid-write — exactly how .txt/.md leave this machine) left an
                # 11k finished document as 8k of nothing with the original gone,
                # while the status line said only "Save failed". The temp +
                # fsync + rename below leaves either the old file or the new
                # one, never a ruined one.
                nbapp.atomic_write_text(path, text)
        except OSError as e:
            self._flash(_save_problem(e), secs=9)
            self._set_save_chip(_t("Not saved"), ok=False)
            return
        self._path = path
        self._file_dirty = False
        # Name the shape that reached the disk. "Saved 19:43" on a .txt read
        # exactly like a lossless save, which is how the dropped formatting
        # stayed invisible.
        if path.endswith(".writer"):
            self._set_save_chip(_t("Saved %s") % time.strftime("%H:%M"),
                                ok=True)
        else:
            self._set_save_chip(_t("Saved as text %s") % time.strftime("%H:%M"),
                                ok=True)
        self._update_status()
        self._save_autosave()

    # =====================================================================
    #  Export / print (PangoCairo renderer)
    # =====================================================================
    def _para_iter(self):
        """Yield (text, attrlist, justification, indent_px, style, list_kind,
        list_index, obj, hard_break) per paragraph, plus image/table objects
        inline. hard_break is True when the writer asked this paragraph to
        start a new sheet."""
        start = self.buf.get_start_iter()
        end = self.buf.get_end_iter()
        line = start.copy()
        list_counter = 0
        prev_num = False
        while line.compare(end) < 0:
            le = line.copy()
            if not le.ends_line():
                le.forward_to_line_end()
            # object paragraph? (image / table only child)
            text = self.buf.get_slice(line, le, True)
            obj = None
            if OBJ in text:
                it = line.copy()
                while it.compare(le) < 0:
                    pb = it.get_pixbuf()
                    anch = it.get_child_anchor()
                    if pb is not None and pb in self._img_meta:
                        obj = ("image", pb)
                        break
                    if anch is not None and anch in self._tables:
                        obj = ("table", self._tables[anch])
                        break
                    it.forward_char()
            style = self._line_style(line)
            just = self._line_just(line)
            indent = self._line_indent_px(line)
            lk = self._line_list(line)
            if lk == "number":
                list_counter = list_counter + 1 if prev_num else 1
                prev_num = True
            else:
                prev_num = False
            attrs = self._line_attrs(line, le) if obj is None else None
            yield (text, attrs, just, indent, style, lk, list_counter, obj,
                   self._line_break(line))
            if not line.forward_line():
                break

    def _line_break(self, it):
        """True when this paragraph was asked to begin a new sheet."""
        tag = self.buf.get_tag_table().lookup("pagebreak")
        if tag is None:
            return False
        s = it.copy()
        s.set_line_offset(0)
        return s.has_tag(tag)

    def _toggle_page_break(self):
        """Put a page break before the paragraph the caret is in, or take one
        away. A toggle rather than an insert: a break is a property of a
        paragraph here, not a character, so there is nothing to select and
        delete afterwards and no invisible mark to hunt for."""
        s, e = self._para_bounds()
        self._checkpoint()
        if self._line_break(s):
            self.buf.remove_tag_by_name("pagebreak", s, e)
            self._flash(_t("Page break removed"))
        else:
            self.buf.apply_tag_by_name("pagebreak", s, e)
            self._flash(_t("Page break added"))
        self._mark_dirty()
        self.body.queue_draw()

    def _line_style(self, it):
        for name in STYLES:
            tag = self.buf.get_tag_table().lookup("style:" + name)
            if tag and it.has_tag(tag):
                return name
        return "Body"

    def _line_just(self, it):
        for j in ("center", "right", "fill", "left"):
            tag = self.buf.get_tag_table().lookup("align:" + j)
            if tag and it.has_tag(tag):
                return j
        return "left"

    def _line_indent_px(self, it):
        lv = self._para_indent_level(it)
        return int(36 * lv * PX_PER_IN / 96) + \
            (34 if self._line_style(it) == "Quote" else 0)

    def _line_list(self, it):
        for k in ("bullet", "number"):
            tag = self.buf.get_tag_table().lookup("list:" + k)
            if tag and it.has_tag(tag):
                return k
        return None

    def _line_attrs(self, s, e):
        """Build a Pango.AttrList for a paragraph's char runs, in PANGO units
        relative to the paragraph's own text.

        Steps RUN BY RUN — to the next offset where any tag toggles — not
        character by character. The old per-character walk called _char_index
        (itself a slice of the paragraph so far) twice per character, which is
        quadratic in paragraph length: exporting a 40,000-word document to PDF
        took about nine seconds of frozen window."""
        al = Pango.AttrList()
        # style baseline (size/weight/italic)
        sz, bold, ital, _a, _b, _q = STYLES[self._line_style(s)]
        it = s.copy()
        while it.compare(e) < 0:
            nxt = it.copy()
            if not nxt.forward_to_tag_toggle(None) or nxt.compare(e) > 0:
                nxt = e.copy()
            if nxt.compare(it) <= 0:        # belt and braces: always advance
                nxt = e.copy()
            ba = self._char_index(s, it.get_offset())
            bb = self._char_index(s, nxt.get_offset())
            tags = it.get_tags()
            names = {t.get_property("name") for t in tags if t.get_property("name")}
            # size
            size_pt = sz
            fam = None
            fg = None
            hl = None
            b_on = bold
            i_on = ital
            u_on = False
            st_on = False
            script = 0            # +1 raised, -1 lowered
            for nm in names:
                if nm == "bold":
                    b_on = True
                elif nm == "italic":
                    i_on = True
                elif nm == "underline":
                    u_on = True
                elif nm == "strike":
                    st_on = True
                elif nm == "super":
                    script = 1
                elif nm == "sub":
                    script = -1
                elif nm.startswith("size:"):
                    size_pt = float(nm[5:])
                elif nm.startswith("font:"):
                    fam = nm[5:]
                elif nm.startswith("fg:"):
                    fg = nm[3:]
                elif nm.startswith("hl:"):
                    hl = nm[3:]
                elif nm.startswith(LINK_PREFIX):
                    fg = LINK_INK
                    u_on = True
            def add(attr):
                attr.start_index = ba
                attr.end_index = bb
                al.insert(attr)
            # The printed page has to raise and shrink these exactly as the
            # screen does, or a footnote marker that looked right on screen
            # comes out full size and on the baseline in the PDF.
            if script:
                add(Pango.attr_rise_new(
                    int(script * SCRIPT_RISE_PT * size_pt * Pango.SCALE)))
                size_pt *= SCRIPT_SCALE
            add(Pango.attr_size_new(int(size_pt * Pango.SCALE)))
            if fam:
                add(Pango.attr_family_new(fam))
            if b_on:
                add(Pango.attr_weight_new(Pango.Weight.BOLD))
            if i_on:
                add(Pango.attr_style_new(Pango.Style.ITALIC))
            if u_on:
                add(Pango.attr_underline_new(Pango.Underline.SINGLE))
            if st_on:
                add(Pango.attr_strikethrough_new(True))
            if fg:
                r, g, bl = self._rgb16(fg)
                add(Pango.attr_foreground_new(r, g, bl))
            if hl and hl != "none":
                r, g, bl = self._rgb16(hl)
                add(Pango.attr_background_new(r, g, bl))
            it = nxt
        return al

    def _char_index(self, para_start, offset):
        """Byte index within the paragraph text for a buffer offset."""
        s = self.buf.get_iter_at_offset(para_start.get_offset())
        e = self.buf.get_iter_at_offset(offset)
        return len(self.buf.get_slice(s, e, True).encode("utf-8"))

    def _rgb16(self, hexcol):
        hexcol = hexcol.lstrip("#")
        r = int(hexcol[0:2], 16) * 257
        g = int(hexcol[2:4], 16) * 257
        b = int(hexcol[4:6], 16) * 257
        return r, g, b

    def _render_pdf(self, path):
        pw_in, ph_in = self._page_dims_in()
        PW = pw_in * PT_PER_IN
        PH = ph_in * PT_PER_IN
        mt, mr, mb, ml = self._page["margins"]
        top = mt * PT_PER_IN
        left = ml * PT_PER_IN
        content_w = (pw_in - ml - mr) * PT_PER_IN
        content_h = (ph_in - mt - mb) * PT_PER_IN
        surf = cairo.PDFSurface(path, PW, PH)
        cr = cairo.Context(surf)
        state = {"y": top, "page": 1}

        def new_page():
            self._pdf_furniture(cr, state["page"], PW, PH, left, top, content_w,
                                mb * PT_PER_IN)
            surf.show_page()
            state["page"] += 1
            state["y"] = top
            self._pdf_furniture(cr, state["page"], PW, PH, left, top, content_w,
                                mb * PT_PER_IN)

        self._pdf_furniture(cr, 1, PW, PH, left, top, content_w, mb * PT_PER_IN)
        first_para = True
        for (text, attrs, just, indent, style, lk, li, obj, brk) \
                in self._para_iter():
            # A hard break before the very first paragraph would emit a blank
            # opening sheet, which is never what was meant.
            if brk and not first_para:
                new_page()
            first_para = False
            x = left + (indent * PT_PER_IN / PX_PER_IN)
            avail_w = content_w - (indent * PT_PER_IN / PX_PER_IN)
            if obj is not None:
                # Objects used to be drawn at the current y and only THEN
                # checked against the page bottom, so anything crossing the
                # boundary was cut off by the edge of the paper and the rest
                # of it never appeared anywhere. Text has always broken
                # correctly (below); now objects do too — a picture moves
                # whole to the next sheet, a table continues row by row.
                bottom = top + content_h
                kind, ref = obj
                if kind == "image":
                    h = self._pdf_image_h(ref, avail_w, content_h)
                    if state["y"] + h > bottom and state["y"] > top:
                        new_page()
                    self._pdf_image(cr, ref, x, state["y"], avail_w, content_h)
                    state["y"] += h + 6
                else:
                    self._pdf_table(cr, ref, x, avail_w, state, top, bottom,
                                    new_page)
                    state["y"] += 6
                continue
            layout = PangoCairo.create_layout(cr)
            layout.set_width(int(avail_w * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            # The same tab stops the ruler shows, in POINTS here rather than
            # screen pixels — a tab that lands somewhere else on paper than it
            # did on screen is the whole reason to have stops at all.
            stops = self._tabs()
            if stops:
                ta = Pango.TabArray.new(len(stops), False)
                for ti, inches in enumerate(stops):
                    ta.set_tab(ti, Pango.TabAlign.LEFT,
                               int(inches * PT_PER_IN * Pango.SCALE))
                layout.set_tabs(ta)
            layout.set_alignment({"left": Pango.Alignment.LEFT,
                                  "center": Pango.Alignment.CENTER,
                                  "right": Pango.Alignment.RIGHT,
                                  "fill": Pango.Alignment.LEFT}[just])
            layout.set_justify(just == "fill")
            fd = Pango.FontDescription()
            fd.set_family(DEFAULT_FAMILY)
            layout.set_font_description(fd)
            prefix = ""
            if lk == "bullet":
                prefix = "•  "
            elif lk == "number":
                prefix = "%d.  " % li
            layout.set_text(prefix + text, -1)
            if attrs is not None:
                if prefix:
                    attrs = self._shift_attrs(attrs, len(prefix.encode("utf-8")))
                layout.set_attributes(attrs)
            sz, _bold, _ital, above, below, _q = STYLES[style]
            state["y"] += above
            bottom = top + content_h

            # Break the paragraph BY LINE. It used to be drawn as one block and
            # moved whole to the next sheet if it did not fit — so a paragraph
            # taller than a page had no page to fit on and simply ran off the
            # bottom of the paper, losing every line past the edge.
            rows = []
            li_ = layout.get_iter()
            while True:
                y0, y1 = li_.get_line_yrange()
                rows.append((y0 / Pango.SCALE, y1 / Pango.SCALE))
                if not li_.next_line():
                    break
            nrows = len(rows)

            start = 0
            while start < nrows:
                avail = bottom - state["y"]
                base = rows[start][0]
                last = start
                while last + 1 < nrows and (rows[last + 1][1] - base) <= avail:
                    last += 1

                if last < nrows - 1:
                    # A break is going to happen here, so mind the widow and
                    # the orphan: a single line stranded at the foot of one
                    # sheet, or carried alone to the top of the next, is the
                    # thing typesetters have always moved a line to avoid.
                    kept = last - start + 1
                    carried = nrows - (last + 1)
                    if kept < ORPHAN_MIN and state["y"] > top:
                        new_page()          # take the whole paragraph over
                        continue
                    if carried < WIDOW_MIN:
                        last = max(start, nrows - 1 - WIDOW_MIN)

                frag_h = rows[last][1] - base
                if frag_h > avail and state["y"] > top:
                    new_page()
                    continue                # retry on a fresh sheet

                cr.save()
                cr.set_source_rgb(0x1A / 255, 0x19 / 255, 0x16 / 255)
                # Clip to this run and shift the layout so the run lands here.
                # Measuring and drawing share this one context, so the window
                # cannot drift off a line boundary.
                cr.rectangle(x, state["y"], avail_w, frag_h)
                cr.clip()
                cr.move_to(x, state["y"] - base)
                PangoCairo.show_layout(cr, layout)
                cr.restore()

                state["y"] += frag_h
                start = last + 1
                if start < nrows:
                    new_page()

            state["y"] += below
            if state["y"] > bottom:
                new_page()
        surf.finish()
        # How many sheets came out. Cairo writes PDF object streams compressed,
        # so this is the only honest way to know from outside — counting
        # "/Type /Page" in the bytes finds nothing.
        return state["page"]

    def _shift_attrs(self, attrs, delta):
        out = Pango.AttrList()

        def keep(attr):
            a = attr.copy()
            a.start_index += delta
            a.end_index += delta
            out.insert(a)
            return False        # False = don't remove from the source list
        attrs.filter(keep)
        return out

    def _pdf_show(self, cr, x, y, text, size=9):
        """Draw one line of PDF furniture with its BASELINE at y.

        Through Pango, never cr.show_text: the header and the footer are text
        the AUTHOR typed, and cairo's toy API binds one face with no
        per-character fallback. Liberation Sans carries no CJK, no Devanagari
        and no Hebrew, so a header typed in any of those printed as .notdef —
        invisible, not even a box, with the rest of the page correct. PDF user
        units are points, so Pango's resolution is pinned to 72dpi and a size
        of N here means the same N cr.set_font_size(N) meant."""
        lay = PangoCairo.create_layout(cr)
        PangoCairo.context_set_resolution(lay.get_context(), 72.0)
        fd = Pango.FontDescription()
        fd.set_family("Liberation Sans")
        fd.set_size(int(size * Pango.SCALE))
        lay.set_font_description(fd)
        lay.set_text(text, -1)
        cr.move_to(x, y - lay.get_baseline() / Pango.SCALE)
        PangoCairo.show_layout(cr, lay)

    def _pdf_furniture(self, cr, page, PW, PH, left, top, content_w, mb_pt):
        cr.save()
        cr.set_source_rgb(0x6E / 255, 0x69 / 255, 0x5E / 255)
        if self._header:
            self._pdf_show(cr, left, top - 14,
                           self._expand_tokens(self._header, page))
        foot = self._footer or ""
        if self._page_numbers:
            # "Page %d" is already carried by every catalogue, and as a whole
            # string it puts the number where each language puts it; the old
            # raw "Page {page}" printed English into all seventeen.
            foot = (foot + "     " + (_t("Page %d") % page)).strip()
        if foot:
            self._pdf_show(cr, left, PH - mb_pt + 22,
                           self._expand_tokens(foot, page))
        cr.restore()

    def _expand_tokens(self, s, page):
        return (s.replace("{page}", str(page)).replace("{pages}", str(page))
                .replace("{title}", self._doc_title()))

    @staticmethod
    def _pdf_image_scale(pb, avail_w, max_h=None):
        """Print scale for a picture: never wider than the text column and,
        when a page height is given, never taller than one whole page — an
        image taller than the sheet can never be shown by paginating."""
        w = pb.get_width(); h = pb.get_height()
        scale = min(1.0, avail_w / float(w)) if w else 1.0
        if max_h and h and h * scale > max_h:
            scale = max_h / float(h)
        return scale

    def _pdf_image_h(self, pb, avail_w, max_h=None):
        return pb.get_height() * self._pdf_image_scale(pb, avail_w, max_h)

    def _pdf_image(self, cr, pb, x, y, avail_w, max_h=None):
        w = pb.get_width(); h = pb.get_height()
        scale = self._pdf_image_scale(pb, avail_w, max_h)
        dw, dh = w * scale, h * scale
        cr.save()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        cr.restore()
        return dh

    def _pdf_table(self, cr, tbl, x, avail_w, state, top, bottom, new_page):
        """Draw a table, breaking BETWEEN rows whenever the next one would not
        fit on the sheet. state["y"] is left just below the last row drawn.

        A table used to be drawn in one go from wherever the cursor happened
        to be, so a 12-row table starting near the foot of a page simply lost
        rows 8-12 off the bottom of the paper."""
        data = tbl.serialize()
        if not data:
            return
        cols = max(len(r) for r in data)
        cw = avail_w / cols
        for row in data:
            rh = 18
            layouts = []
            for c in range(cols):
                txt = row[c] if c < len(row) else ""
                lay = PangoCairo.create_layout(cr)
                lay.set_width(int((cw - 8) * Pango.SCALE))
                lay.set_wrap(Pango.WrapMode.WORD_CHAR)
                lay.set_text(txt, -1)
                layouts.append(lay)
                rh = max(rh, lay.get_pixel_size()[1] + 8)
            if state["y"] + rh > bottom and state["y"] > top:
                new_page()
            ry = state["y"]
            cr.save()
            cr.set_source_rgb(0x26 / 255, 0x24 / 255, 0x1F / 255)
            cr.set_line_width(0.6)
            for c in range(cols):
                cx = x + c * cw
                cr.rectangle(cx, ry, cw, rh)
                cr.stroke()
                cr.save()
                cr.move_to(cx + 4, ry + 4)
                PangoCairo.show_layout(cr, layouts[c])
                cr.restore()
            cr.restore()
            state["y"] = ry + rh

    def _export_pdf(self):
        path = nbpicker.save_file(
            self, title="Export to PDF", start_dir=self._start_dir(),
            suggested_name=(self._doc_title() or "Untitled") + ".pdf",
            patterns=("*.pdf",), default_ext=".pdf")
        if not path:
            return
        try:
            self._render_pdf(path)
            self._flash(_t("Exported %s") % os.path.basename(path))
        except Exception as e:
            self._flash(_export_problem(e), secs=9)

    def _print_document(self):
        try:
            # Spool at the size Page Setup chose. Without this every job went
            # out as Letter, so an A4 or Legal document was scaled by CUPS to
            # fit letter paper and none of its margins survived.
            nbprint.print_document(self, self._render_pdf,
                                   job_name=self._doc_title() or "Document",
                                   media=self._page.get("size", "Letter"))
        except Exception:
            # Nothing has left the machine and nothing has been altered — the
            # only useful thing to say is that, not the exception's text.
            self._flash(_t("The document could not be sent to the printer."),
                        secs=9)

    # =====================================================================
    #  Menus + keys
    # =====================================================================
    def menu_items(self, name):
        if name == "File":
            return [
                ("New    Ctrl+N", self._file_new),
                ("Open…    Ctrl+O", self._file_open),
                nbapp.SEP,
                ("Save    Ctrl+S", self._file_save),
                ("Save As…    Ctrl+Shift+S", self._file_save_as),
                nbapp.SEP,
                ("Page Setup…", self._page_setup),
                ("Export to PDF…", self._export_pdf),
                ("Print…    Ctrl+P", self._print_document),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            # Undo/Redo grey out with nothing to take back, as they do in every
            # other editor in the OS — they used to stay live and silently do
            # nothing, which teaches the reader the command is broken rather
            # than that the history is empty. A pending typing checkpoint still
            # counts as undoable: _undo flushes it before stepping back, so the
            # first sentence typed into a fresh document IS reversible.
            can_undo = self._hi > 0 or bool(self._undo_timer)
            can_redo = self._hi < len(self._history) - 1
            return [
                ("Undo    Ctrl+Z", self._undo if can_undo else None),
                ("Redo    Ctrl+Shift+Z", self._redo if can_redo else None),
                nbapp.SEP,
                ("Cut    Ctrl+X", lambda: self._clip("cut")),
                ("Copy    Ctrl+C", lambda: self._clip("copy")),
                ("Paste    Ctrl+V", lambda: self._clip("paste")),
                nbapp.SEP,
                # Find & Replace is NOT listed here. It used to be in both Edit
                # and View, under two different labels, one of them carrying an
                # ellipsis for what is an inline bar rather than a dialog. No
                # other app in the OS puts Find in two menus, and the two apps
                # closest to this one (Novel, Screenplay) both keep it in View.
                ("Select All    Ctrl+A", self._select_all),
            ]
        if name == "Format":
            return [
                ("Bold    Ctrl+B", lambda: self._toggle_char("bold")),
                ("Italic    Ctrl+I", lambda: self._toggle_char("italic")),
                ("Underline    Ctrl+U", lambda: self._toggle_char("underline")),
                ("Strikethrough", lambda: self._toggle_char("strike")),
                nbapp.SEP,
                ("Superscript", lambda: self._toggle_char("super")),
                ("Subscript", lambda: self._toggle_char("sub")),
                nbapp.SEP,
                ("Text Colour…", lambda: self._pick_colour("fg")),
                ("Highlight…", lambda: self._pick_colour("hl")),
                nbapp.SEP,
                ("Align Left", lambda: self._set_align("left")),
                ("Center", lambda: self._set_align("center")),
                ("Align Right", lambda: self._set_align("right")),
                ("Justify", lambda: self._set_align("fill")),
            ]
        if name == "Insert":
            return [
                ("Link…    Ctrl+K", self._insert_link),
                ("Image…", self._insert_image),
                ("Table", lambda: self._insert_table()),
                nbapp.SEP,
                ("Page Break    Ctrl+Return", self._toggle_page_break),
                nbapp.SEP,
                ("Bulleted List", lambda: self._toggle_list("bullet")),
                ("Numbered List", lambda: self._toggle_list("number")),
            ]
        if name == "Table":
            return [
                ("Insert Table", lambda: self._insert_table()),
                nbapp.SEP,
                ("Add Row", lambda: self._table_op("add_row")),
                ("Add Column", lambda: self._table_op("add_col")),
                ("Delete Row", lambda: self._table_op("del_row")),
                ("Delete Column", lambda: self._table_op("del_col")),
            ]
        if name == "View":
            # Same shape as Novel's and Screenplay's View menus: find first,
            # then a rule, then Focus Editor. No ellipsis — the find bar slides
            # in under the toolbar, it is not a window.
            z = getattr(self, "_zoom", DEFAULT_ZOOM)
            return [
                ("Find & Replace    Ctrl+F", lambda: self._toggle_find(True)),
                nbapp.SEP,
                ("Zoom In    Ctrl+Plus",
                 (lambda: self._zoom_step(1)) if z < ZOOM_STEPS[-1] else None),
                ("Zoom Out    Ctrl+Minus",
                 (lambda: self._zoom_step(-1)) if z > ZOOM_STEPS[0] else None),
                ("Actual Size    Ctrl+0",
                 (lambda: self._set_zoom(1.0)) if z != 1.0 else None),
                nbapp.SEP,
                ("Clear Tab Stops",
                 self._clear_tabs if self._tabs() else None),
                nbapp.SEP,
                ("Focus Editor", lambda: self.body.grab_focus()),
            ]
        return super().menu_items(name)

    def _select_all(self):
        self.buf.select_range(self.buf.get_start_iter(), self.buf.get_end_iter())

    def _clip(self, op):
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if op == "copy":
            self.buf.copy_clipboard(cb)
        elif op == "cut":
            self._checkpoint()
            self.buf.cut_clipboard(cb, True)
        else:
            self._checkpoint()
            self.buf.paste_clipboard(cb, None, True)

    def _on_key(self, w, ev):
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        kv = ev.keyval
        if ctrl:
            if kv in (Gdk.KEY_s, Gdk.KEY_S):
                self._file_save_as() if shift else self._file_save()
                return True
            if kv in (Gdk.KEY_o, Gdk.KEY_O):
                self._file_open(); return True
            if kv in (Gdk.KEY_n, Gdk.KEY_N):
                self._file_new(); return True
            if kv in (Gdk.KEY_p, Gdk.KEY_P):
                self._print_document(); return True
            if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._toggle_page_break(); return True
            if kv in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self._zoom_step(1); return True
            if kv in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self._zoom_step(-1); return True
            if kv in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self._set_zoom(1.0); return True
            if kv in (Gdk.KEY_f, Gdk.KEY_F):
                self._toggle_find(True); return True
            if kv in (Gdk.KEY_z, Gdk.KEY_Z):
                self._redo() if shift else self._undo(); return True
            if kv in (Gdk.KEY_y, Gdk.KEY_Y):
                self._redo(); return True
            if kv in (Gdk.KEY_b, Gdk.KEY_B):
                self._toggle_char("bold"); return True
            if kv in (Gdk.KEY_i, Gdk.KEY_I):
                self._toggle_char("italic"); return True
            if kv in (Gdk.KEY_u, Gdk.KEY_U):
                self._toggle_char("underline"); return True
            if kv in (Gdk.KEY_k, Gdk.KEY_K):
                self._insert_link(); return True
        if kv == Gdk.KEY_Escape and self._findbar.get_visible():
            self._toggle_find(False); return True
        return super()._on_key(w, ev)

    def _on_destroy(self, *_):
        for attr in ("_save_timer", "_undo_timer", "_count_timer"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._save_autosave()
        return False

    # =====================================================================
    #  CSS — every surface explicitly backgrounded (no black leaks)
    # =====================================================================
    def _install_css(self):
        css = ("""
        .toolbar { background: #F4F2EC; border-bottom: 1px solid #D7D2C5;
                   padding: 6px 12px; }
        .tbrow * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .tbbtn { min-width: 28px; min-height: 26px; padding: 0 7px;
                 background: #FCFBF8; border: 1px solid #D7D2C5;
                 border-radius: 8px; box-shadow: none; color: #2A2620;
                 font-size: 14px; }
        .tbbtn:hover { background: #EFEBE0; }
        .tbbtn.on { background: #EAE3D2; border-color: #B3AD9E; }
        .tbbtn.b-bold { font-weight: 700; }
        .tbbtn.b-ital { font-style: italic; }
        .tbbtn.b-under { text-decoration-line: underline; }
        .tbbtn.b-strike { text-decoration-line: line-through; }
        .tbcombo { min-height: 26px; }
        .tbcombo, .tbcombo * { font-size: 13px; color: #1A1916; }
        .tbcombo button, .tbcombo entry { background: #FCFBF8;
                 border: 1px solid #D7D2C5; border-radius: 8px; box-shadow: none; }
        .tbsep { color: #D7D2C5; min-width: 1px; margin: 3px 0; }
        .ruler { background: #9A9484; }
        .desk, scrolledwindow.desk, viewport.desk,
        scrolledwindow.desk viewport, .desk viewport {
            background-color: %(desk)s; background-image: none; }
        .sheet { background: %(sheet)s;
                 border: 1px solid #8A857A;
                 box-shadow: 0 2px 10px rgba(26,25,22,0.35); }
        .hfband { color: #9A9484; font-size: 12px;
                  font-family: "Liberation Sans",sans-serif; }
        .docbody { background: %(sheet)s; color: #2A2620;
                   font-family: "Liberation Serif","DejaVu Serif",serif;
                   font-size: 12pt; caret-color: #C8341E; }
        .docbody text { background: %(sheet)s; color: #2A2620; }
        .docbody text selection { background-color: #EAE3D2; color: #1A1916; }
        .wtable { background: %(sheet)s; margin: 6px 0; }
        .wtablegrid { background: #8A857A; }
        .wtcell { background: %(sheet)s; border: 1px solid #B3AD9E; }
        .wtcelltv, .wtcelltv text { background: %(sheet)s; color: #2A2620;
                   font-size: 11pt; }
        .findbar { background: #EFEBE0; border-bottom: 1px solid #D7D2C5;
                   padding: 6px 12px; }
        .findbar entry, .findinput { background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; box-shadow: none; color: #1A1916; }
        .findcount { color: #6E695E; font-size: 12px; }
        /* .statusbar is Papertone's - see the theme. This app had its own
           background, hairline and text size; there is nothing about a word
           count that justifies a different strip from every other app. */
        .statuslabel { color: #6E695E; font-size: 12px;
                       font-family: "Nimbus Sans",sans-serif; }
        .savechip { color: #6E695E; font-size: 12px; }
        .savechip.dirty { color: #C8341E; }
        .swatchbox { background: #F8F7F2; padding: 16px 18px; }
        .swatchbox label { color: #2A2620; }
        /* Dialog headings share the OS-wide .dlghead size/weight (Papertone); this
           only adds the spacing below them. Before, these were bold but at the
           body size, so Writer's dialogs were the only ones whose heading did
           not read as a heading. */
        .swatchtitle { font-size: 17px; font-weight: 700; color: #1A1916;
                       margin-bottom: 8px; }
        .fieldcaption { color: #6E695E; font-size: 12px; }
        .swatch { border: 1px solid #C9C4B6; border-radius: 4px; margin: 3px;
                  box-shadow: none; min-width: 34px; min-height: 26px; }
        .dlgmsg { color: #6E695E; font-size: 13px; margin: 4px 0 6px; }
        /* Destructive primary: signage red, and the LABEL node needs its own
           colour or the theme's `* { color: ink }` beats the inherited paper
           and paints near-black text on the red button. */
        .dangerbtn, .dangerbtn label { color: #FCFBF8; font-size: 13px; }
        .dangerbtn { min-height: 26px; padding: 0 14px; background: #C8341E;
                     border: 1px solid #C8341E; border-radius: 8px;
                     box-shadow: none; font-weight: 600; }
        .dangerbtn:hover { background: #B12D19; border-color: #B12D19; }
        .ghosthint { color: #9A9484; }
        """ % {"desk": DESK, "sheet": SHEET}).encode()
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass


if __name__ == "__main__":
    nbapp.run(Writer)
