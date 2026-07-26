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

DESK = "#A8A294"        # the gray "desk" the sheet floats on
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

        doc = self._load_doc()
        self._page = doc.get("page", {"size": "Letter", "orientation": "portrait",
                                      "margins": list(DEFAULT_MARGINS_IN)})
        self._header = doc.get("header", "")
        self._footer = doc.get("footer", "")
        self._page_numbers = bool(doc.get("page_numbers", False))
        self._path = doc.get("path")

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
            self.save_chip.set_text(_t("Not saved to file"))
            self.save_chip.get_style_context().add_class("dirty")
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
            b.add(Gtk.Image.new_from_pixbuf(nbicons.pixbuf(icon, 15, "#2A2620")))
        except Exception:
            b.set_label(cmd[:1].upper())
        return b

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
        self._fmt_btns = {}
        for label, cmd, tip, cls in (("B", "bold", "Bold (Ctrl+B)", "b-bold"),
                                     ("I", "italic", "Italic (Ctrl+I)", "b-ital"),
                                     ("U", "underline", "Underline (Ctrl+U)", "b-under"),
                                     ("S", "strike", "Strikethrough", "b-strike")):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("tbbtn")
            b.get_style_context().add_class(cls)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, c=cmd: self._toggle_char(c))
            self._fmt_btns[cmd] = b
            row.add_item(b)

        # colours
        tc = self._iconbtn("pencil", "textcolor", "Text colour")
        tc.connect("clicked", lambda *_: self._pick_colour("fg"))
        row.add_item(tc)
        hc = self._iconbtn("highlight", "highlight", "Highlight")
        hc.connect("clicked", lambda *_: self._pick_colour("hl"))
        row.add_item(hc)

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
        return self.ruler

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
        cr.set_source_rgb(0xA8 / 255.0, 0xA2 / 255.0, 0x94 / 255.0)
        cr.rectangle(0, 0, alloc.width, alloc.height)
        cr.fill()
        return False

    # ----------------------------------------------------------- find bar -----
    def _build_findbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("findbar")
        self.find_entry = Gtk.SearchEntry()
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
        # a drawn close glyph, not a font "✕" (U+2715 is absent from the shipped
        # Nimbus Sans and would render as a tofu box)
        close.set_image(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf("wclose", 12, "#6E695E")))
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
                kw["paragraph_background"] = "#F3EFE4"
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
    def _toggle_char(self, cmd):
        if not self.buf.get_has_selection():
            # queue for the next typed run (standard word-processor behaviour)
            if cmd in self._pending:
                self._pending.discard(cmd)
            else:
                self._pending.add(cmd)
            self._sync_toolbar()
            return
        s, e = self.buf.get_selection_bounds()
        on = self._range_has_tag(s, e, cmd)
        self._checkpoint()
        if on:
            self.buf.remove_tag_by_name(cmd, s, e)
        else:
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

            for cmd in ("bold", "italic", "underline", "strike"):
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
            if key in ("bold", "italic", "underline", "strike"):
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
            lay.set_text(_t("Start typing — your document is kept as you write."),
                         -1)
            cr.save()
            cr.set_source_rgb(0xA3 / 255.0, 0x9D / 255.0, 0x8F / 255.0)
            cr.move_to(view.get_left_margin(), wy)
            PangoCairo.show_layout(cr, lay)
            cr.restore()

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
                cr.select_font_face("Liberation Sans", cairo.FONT_SLANT_NORMAL,
                                    cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(9)
                cr.move_to(alloc_w - 62, wy - 4)
                cr.show_text("Page %d" % (page + 1))
                cr.restore()
            page += 1
            y += content_h

        # list markers in the gutter
        self._draw_list_markers(view, cr, vy0, vy1)
        return False

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
                self._apply_value_tag("fg:", "fg:" + col)
            else:
                self._apply_value_tag("hl:", "hl:none" if col == "none"
                                      else "hl:" + col)
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
        ok.get_style_context().add_class("tbbtn")
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
        try:
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
        self._img_meta[pb] = {"path": path, "ow": ow}
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

    # =====================================================================
    #  Serialize / deserialize
    # =====================================================================
    SERIAL_TAGS = None   # computed lazily

    def _serial_tag_names(self):
        names = ["bold", "italic", "underline", "strike"]
        names += ["align:" + j for j in ("left", "center", "right", "fill")]
        names += ["style:" + s for s in STYLES]
        names += ["indent:%d" % lv for lv in range(1, 9)]
        names += ["spacing:" + s for s in ("single", "onehalf", "double")]
        names += ["list:bullet", "list:number"]
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
                    images.append({"off": i, "path": meta["path"],
                                   "ow": meta["ow"]})
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
        was_loading = self._loading
        self._loading = True
        try:
            self.buf.set_text("")
            self._img_meta.clear()
            self._tables.clear()
            # v1 back-compat (title/subtitle/body plain)
            if doc.get("version") != 2 and "body" in doc and "runs" not in doc:
                head = ""
                if doc.get("title"):
                    head += doc["title"] + "\n"
                if doc.get("subtitle"):
                    head += doc["subtitle"] + "\n"
                self.buf.set_text(head + doc.get("body", ""))
                return
            body = doc.get("body", "")
            self.buf.set_text(body)
            for s_off, e_off, name in doc.get("runs", []):
                s = self.buf.get_iter_at_offset(s_off)
                e = self.buf.get_iter_at_offset(e_off)
                self.buf.apply_tag(self._tag(name)
                                   if self.buf.get_tag_table().lookup(name) is None
                                   else self.buf.get_tag_table().lookup(name), s, e)
            # replace ￼ placeholders (ascending; length-neutral)
            for img in sorted(doc.get("images", []), key=lambda d: d["off"]):
                self._reinsert_image(img)
            for tb in sorted(doc.get("tables", []), key=lambda d: d["off"]):
                self._reinsert_table(tb)
        finally:
            self._loading = was_loading
        self._update_wordcount()

    def _reinsert_image(self, img):
        off = img["off"]
        it = self.buf.get_iter_at_offset(off)
        if it.get_char() != OBJ:
            return
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(img["path"])
        except Exception:
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
        self._img_meta[pb] = {"path": img["path"], "ow": ow}

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
    def _page_dims_in(self):
        w, h = PAGE_SIZES.get(self._page.get("size", "Letter"),
                              PAGE_SIZES["Letter"])
        if self._page.get("orientation") == "landscape":
            w, h = h, w
        return w, h

    def _apply_page_geometry(self):
        pw_in, ph_in = self._page_dims_in()
        mt, mr, mb, ml = self._page["margins"]
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

        size_c = Gtk.ComboBoxText()
        for s in PAGE_SIZES:
            size_c.append_text(s)
        size_c.set_active(list(PAGE_SIZES).index(self._page.get("size", "Letter")))
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
        ok.get_style_context().add_class("tbbtn")
        dlg.show_all()
        r = dlg.run()
        if r == Gtk.ResponseType.OK:
            self._page = {
                "size": size_c.get_active_text(),
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
    def _load_doc(self):
        try:
            with open(DOC_FILE) as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                return d
        except (OSError, ValueError):
            pass
        return {"version": 2, "body": "", "runs": []}

    def _save_autosave(self):
        try:
            nbapp.atomic_write_json(DOC_FILE, self._serialize())
        except OSError:
            pass

    def _mark_dirty(self, *_):
        if self._loading:
            return
        self._file_dirty = True
        self.save_chip.set_text(_t("Editing"))
        self.save_chip.get_style_context().add_class("dirty")
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
        chars = len(text)
        self.wc_label.set_text("%d word%s · %d chars" %
                               (words, "" if words == 1 else "s", chars))

    def _flash(self, msg):
        self.status.set_text(msg)
        GLib.timeout_add(2600, self._update_status_return)

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
            label=(_t("“%s” has changes you have not saved. They will be "
                      "lost.") % name) if name else
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
        self.save_chip.set_text("")
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
        self._page = doc.get("page", self._page)
        self._header = doc.get("header", "")
        self._footer = doc.get("footer", "")
        self._page_numbers = bool(doc.get("page_numbers", False))
        self._deserialize(doc)
        self._apply_page_geometry()
        self._path = path
        self._file_dirty = False
        self._history = []; self._hi = -1
        self._push_history()
        self.save_chip.set_text("")
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

    def _write_file(self, path):
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
            self._flash("Save failed: %s" % e)
            return
        self._path = path
        self._file_dirty = False
        self.save_chip.set_text(_t("Saved"))
        self.save_chip.get_style_context().remove_class("dirty")
        self._update_status()
        self._save_autosave()

    # =====================================================================
    #  Export / print (PangoCairo renderer)
    # =====================================================================
    def _para_iter(self):
        """Yield (text, attrlist, justification, indent_px, style, list_kind,
        list_index, obj) per paragraph, plus image/table objects inline."""
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
            yield (text, attrs, just, indent, style, lk, list_counter, obj)
            if not line.forward_line():
                break

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
            for nm in names:
                if nm == "bold":
                    b_on = True
                elif nm == "italic":
                    i_on = True
                elif nm == "underline":
                    u_on = True
                elif nm == "strike":
                    st_on = True
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
        for (text, attrs, just, indent, style, lk, li, obj) in self._para_iter():
            x = left + (indent * PT_PER_IN / PX_PER_IN)
            avail_w = content_w - (indent * PT_PER_IN / PX_PER_IN)
            if obj is not None:
                kind, ref = obj
                if kind == "image":
                    h = self._pdf_image(cr, ref, x, state["y"], avail_w)
                    state["y"] += h + 6
                else:
                    h = self._pdf_table(cr, ref, x, state["y"], avail_w)
                    state["y"] += h + 6
                if state["y"] > top + content_h:
                    new_page()
                continue
            layout = PangoCairo.create_layout(cr)
            layout.set_width(int(avail_w * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
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
            _w, lh = layout.get_pixel_size()
            sz, _bold, _ital, above, below, _q = STYLES[style]
            state["y"] += above
            if state["y"] + lh > top + content_h and state["y"] > top:
                new_page()
            cr.save()
            cr.set_source_rgb(0x1A / 255, 0x19 / 255, 0x16 / 255)
            cr.move_to(x, state["y"])
            PangoCairo.show_layout(cr, layout)
            cr.restore()
            state["y"] += lh + below
            if state["y"] > top + content_h:
                new_page()
        surf.finish()

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

    def _pdf_furniture(self, cr, page, PW, PH, left, top, content_w, mb_pt):
        cr.save()
        cr.set_source_rgb(0x6E / 255, 0x69 / 255, 0x5E / 255)
        cr.select_font_face("Liberation Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9)
        if self._header:
            cr.move_to(left, top - 14)
            cr.show_text(self._expand_tokens(self._header, page))
        foot = self._footer or ""
        if self._page_numbers:
            foot = (foot + "     Page {page}").strip()
        if foot:
            cr.move_to(left, PH - mb_pt + 22)
            cr.show_text(self._expand_tokens(foot, page))
        cr.restore()

    def _expand_tokens(self, s, page):
        return (s.replace("{page}", str(page)).replace("{pages}", str(page))
                .replace("{title}", self._doc_title()))

    def _pdf_image(self, cr, pb, x, y, avail_w):
        w = pb.get_width(); h = pb.get_height()
        scale = min(1.0, avail_w / float(w)) if w else 1.0
        dw, dh = w * scale, h * scale
        cr.save()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        cr.restore()
        return dh

    def _pdf_table(self, cr, tbl, x, y, avail_w):
        data = tbl.serialize()
        if not data:
            return 0
        cols = max(len(r) for r in data)
        cw = avail_w / cols
        cr.save()
        cr.set_source_rgb(0x26 / 255, 0x24 / 255, 0x1F / 255)
        cr.set_line_width(0.6)
        ry = y
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
            for c in range(cols):
                cx = x + c * cw
                cr.rectangle(cx, ry, cw, rh)
                cr.stroke()
                cr.save()
                cr.move_to(cx + 4, ry + 4)
                PangoCairo.show_layout(cr, layouts[c])
                cr.restore()
            ry += rh
        cr.restore()
        return ry - y

    def _export_pdf(self):
        path = nbpicker.save_file(
            self, title="Export to PDF", start_dir=self._start_dir(),
            suggested_name=(self._doc_title() or "Untitled") + ".pdf",
            patterns=("*.pdf",), default_ext=".pdf")
        if not path:
            return
        try:
            self._render_pdf(path)
            self._flash("Exported %s" % os.path.basename(path))
        except Exception as e:
            self._flash("Export failed: %s" % e)

    def _print_document(self):
        try:
            nbprint.print_document(self, self._render_pdf,
                                   job_name=self._doc_title() or "Document")
        except Exception as e:
            self._flash("Print failed: %s" % e)

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
            return [
                ("Undo    Ctrl+Z", self._undo),
                ("Redo    Ctrl+Shift+Z", self._redo),
                nbapp.SEP,
                ("Cut    Ctrl+X", lambda: self._clip("cut")),
                ("Copy    Ctrl+C", lambda: self._clip("copy")),
                ("Paste    Ctrl+V", lambda: self._clip("paste")),
                nbapp.SEP,
                ("Find & Replace…    Ctrl+F", lambda: self._toggle_find(True)),
                ("Select All    Ctrl+A", self._select_all),
            ]
        if name == "Format":
            return [
                ("Bold    Ctrl+B", lambda: self._toggle_char("bold")),
                ("Italic    Ctrl+I", lambda: self._toggle_char("italic")),
                ("Underline    Ctrl+U", lambda: self._toggle_char("underline")),
                ("Strikethrough", lambda: self._toggle_char("strike")),
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
            return [
                ("Find & Replace    Ctrl+F", lambda: self._toggle_find(True)),
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
        .toolbar { background: #F4F2EC; border-bottom: 1px solid #D8D2C4;
                   padding: 6px 12px; }
        .tbrow * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .tbbtn { min-width: 28px; min-height: 26px; padding: 0 7px;
                 background: #FCFBF8; border: 1px solid #D8D2C4;
                 border-radius: 2px; box-shadow: none; color: #2A2620;
                 font-size: 14px; }
        .tbbtn:hover { background: #EFEBE0; }
        .tbbtn.on { background: #E7DEC9; border-color: #B3AD9E; }
        .tbbtn.b-bold { font-weight: 700; }
        .tbbtn.b-ital { font-style: italic; }
        .tbbtn.b-under { text-decoration-line: underline; }
        .tbbtn.b-strike { text-decoration-line: line-through; }
        .tbcombo { min-height: 26px; }
        .tbcombo, .tbcombo * { font-size: 13px; color: #1A1916; }
        .tbcombo button, .tbcombo entry { background: #FCFBF8;
                 border: 1px solid #D8D2C4; border-radius: 2px; box-shadow: none; }
        .tbsep { color: #D8D2C4; min-width: 1px; margin: 3px 0; }
        .ruler { background: #9C968B; }
        .desk, scrolledwindow.desk, viewport.desk,
        scrolledwindow.desk viewport, .desk viewport {
            background-color: %(desk)s; background-image: none; }
        .sheet { background: %(sheet)s;
                 border: 1px solid #8F897C;
                 box-shadow: 0 2px 10px rgba(26,25,22,0.35); }
        .hfband { color: #A39D8F; font-size: 12px;
                  font-family: "Liberation Sans",sans-serif; }
        .docbody { background: %(sheet)s; color: #26241F;
                   font-family: "Liberation Serif","DejaVu Serif",serif;
                   font-size: 12pt; caret-color: #C8341E; }
        .docbody text { background: %(sheet)s; color: #26241F; }
        .docbody text selection { background-color: #F1D9D2; color: #1A1916; }
        .wtable { background: %(sheet)s; margin: 6px 0; }
        .wtablegrid { background: #8F897C; }
        .wtcell { background: %(sheet)s; border: 1px solid #B8B2A6; }
        .wtcelltv, .wtcelltv text { background: %(sheet)s; color: #26241F;
                   font-size: 11pt; }
        .findbar { background: #EFEBE0; border-bottom: 1px solid #D8D2C4;
                   padding: 6px 12px; }
        .findbar entry, .findinput { background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 2px; box-shadow: none; color: #1A1916; }
        .findcount { color: #6E695E; font-size: 12px; }
        .statusbar { background: #F4F2EC; border-top: 1px solid #D8D2C4;
                     padding: 5px 16px; }
        .statuslabel { color: #6E695E; font-size: 12.5px;
                       font-family: "Nimbus Sans",sans-serif; }
        .savechip { color: #6E695E; font-size: 12.5px; }
        .savechip.dirty { color: #C8341E; }
        .swatchbox { background: #F8F7F2; padding: 16px 18px; }
        .swatchbox label { color: #2A2620; }
        .swatchtitle { font-weight: 700; margin-bottom: 8px; }
        .fieldcaption { color: #6E695E; font-size: 12px; }
        .swatch { border: 1px solid #C9C4B6; border-radius: 2px; margin: 3px;
                  box-shadow: none; min-width: 34px; min-height: 26px; }
        .dlgmsg { color: #57534B; font-size: 13px; margin: 4px 0 6px; }
        /* Destructive primary: signage red, and the LABEL node needs its own
           colour or the theme's `* { color: ink }` beats the inherited paper
           and paints near-black text on the red button. */
        .dangerbtn, .dangerbtn label { color: #FCFBF8; font-size: 13px; }
        .dangerbtn { min-height: 26px; padding: 0 14px; background: #C8341E;
                     border: 1px solid #C8341E; border-radius: 2px;
                     box-shadow: none; font-weight: 600; }
        .dangerbtn:hover { background: #A82A18; border-color: #A82A18; }
        .ghosthint { color: #A39D8F; }
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
