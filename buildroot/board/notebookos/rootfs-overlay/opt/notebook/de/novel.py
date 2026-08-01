#!/usr/bin/env python3
"""
Novel — the Notebook OS long-form manuscript editor (native GTK).

A two-pane writing environment: a manuscript sidebar (title, chapter list,
"New Chapter") beside a serif editor canvas with a format bar (paragraph
style, B/I/U, quote, list) and a live word-count / autosave state.

The File menu performs real file I/O against manuscript JSON files under
$NB_HOME/Documents (New / Open / Save / Save As); the active model is also
auto-persisted to $NB_HOME/.config/notebook/novel.json for session recovery.

Per the no-seed rule it opens on a single empty "Chapter 1" with no
fabricated content.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GLib  # noqa: E402

import os
import json
import time

import nbapp
import nbi18n
import nbpicker
import nbicons
import nbprint
from nbi18n import _t  # noqa: E402

# ---- book page geometry (5.5x8.5in half-letter, in points) -----------------
# The whole publish workflow is oriented around this single page size: both
# publish routes (Export to PDF, Zine Print) share ONE draw_page() renderer at
# these dimensions, so what a writer exports is exactly what the zine imposes.
PAGE_W = nbprint.HALF_W_PT          # 396 pt
PAGE_H = nbprint.HALF_H_PT          # 612 pt
MARGIN_X = 52                        # left/right text margin
BODY_TOP = 58                        # first body line on a continuation page
BODY_BOT = PAGE_H - 60               # last usable y (folio sits below this)
COL_W = PAGE_W - 2 * MARGIN_X        # text column width (292 pt)
CH_OPEN_TOP = 150                    # chapter-opening block starts this far down
PARA_GAP = 6                         # vertical gap between body paragraphs

# ---- on-screen writing column ---------------------------------------------
# The measure the design wants, the narrowest it falls back to on a small
# panel, and the .nvpage padding either side (see _fit_page).
COL_MAX = 620
COL_MIN = 360
COL_PAD = 48

# Bundled families only (Liberation / DejaVu / Nimbus Sans) so nothing renders as
# tofu on real hardware — no exotic face is ever pinned.
_SERIF = "Liberation Serif"
_SANS = "Liberation Sans"
F_BODY = _SERIF + " 10.5"
F_QUOTE = _SERIF + " Italic 10.5"
F_SUBHEAD = _SERIF + " Bold 13"
F_CHNUM = _SANS + " 8.5"
F_CHTITLE = _SERIF + " 22"
F_TITLE = _SERIF + " 30"
F_TOCHEAD = _SERIF + " 20"
F_TOCROW = _SERIF + " 11.5"
F_TOCPART = _SANS + " 8.5"
F_FOLIO = _SANS + " 8.5"

# Canonical papertone hexes used by the PDF renderer.
C_BG = "#FCFBF8"
C_INK = "#1A1916"
C_SEC = "#6E695E"
C_MUT = "#9A9484"
C_RED = "#C8341E"

# Session recovery: the full manuscript (title, every chapter, the active
# index, the bound file path) is written to $NB_HOME/.config/notebook/novel.json
# so the active model survives closing the app or rebooting the machine. The
# File menu, by contrast, reads/writes user-chosen manuscript files under
# $NB_HOME/Documents.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
NOVEL_FILE = os.path.join(CFG_DIR, "novel.json")
DOCS_DIR = os.path.join(HOME, "Documents")

# The default manuscript name. It is built with _t() into the sidebar label,
# and that label IS the model — _serialize() reads the title straight back out
# of it — so on a Spanish install the default title is the SPANISH string.
# Every later "is this still the default?" test and every fallback therefore
# has to go through here rather than repeat the English literal, or it stops
# matching the moment the OS is not in English. (UNTITLED_EN is kept for the
# comparisons, so a manuscript saved on an English install is still recognised
# as untitled when it is opened on a translated one.)
UNTITLED_EN = "Untitled Novel"


def _untitled():
    return _t(UNTITLED_EN)

# Straight quotes and hyphen-hyphen dashes become real typography as the
# writer types (nbapp.smart_replacement, shared with Writer and Journal).


class Novel(nbapp.AppWindow):
    app_name = "Novel"
    menus = ("File", "Edit", "Format", "Insert", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        self.chapters = []      # list of dicts: num, title, buffer, part
        # Parts group consecutive chapters. Each is {"name": str}; the visible
        # label is derived ("PART ONE — ARRIVALS"). Default: one unnamed part.
        self.parts = [{"name": ""}]
        self.active = 0
        # Running manuscript word total; each chapter caches its own count in
        # ch["wc"]. The typing path adjusts both incrementally (see _on_change)
        # instead of re-summing every chapter on each keystroke.
        self._total_words = 0
        self._save_timer = None
        # Set while a structural rewrite edits a chapter buffer programmatically
        # (e.g. re-sequencing headings after a delete). It suppresses _on_change
        # so such an edit — possibly of a NON-active buffer — is never
        # mis-attributed to the active chapter.
        self._suppress_change = False
        # Guards the smart-quote re-insert against re-entering its own handler.
        self._smart_busy = False
        # The user file the File menu is bound to (under $NB_HOME/Documents),
        # distinct from the always-on session-recovery file. None until the
        # manuscript is opened from, or saved to, a chosen file.
        self.doc_path = None
        # Book pagination status, computed off the 5.5x8.5 page. Kept lazily in
        # sync (on a settled edit / chapter switch, never per-keystroke) so the
        # sidebar can show a live page total and each chapter's start page.
        self._page_count = 0
        self._chapter_pages = {}   # chapter index -> 1-based book page
        self._render_pages = None  # last imposed page model (shared by both routes)
        self._page_timer = None    # debounce for the page-total refresh
        self._paginate_ms = 0.0    # what the last pagination actually cost
        # Manuscript-wide find: matches as (chapter_index, start_off, end_off)
        # across EVERY chapter, not just the open one.
        self._find_hits = []
        self._find_i = -1
        # Read any persisted manuscript up front (plain data; no widgets yet).
        saved = self._load_state()

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._sidebar(), False, False, 0)
        body.pack_start(self._editor(), True, True, 0)

        # Build the chapter model: restore the saved manuscript if we have one,
        # otherwise fall back to the original empty single-chapter state.
        if saved:
            self._restore(saved)
        else:
            self._new_chapter(select=True)
        # Seed the per-chapter count cache + running total once, up front, so
        # the sidebar rows and header read cached values from here on.
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        # Undo/redo over the WHOLE manuscript, not a single chapter buffer:
        # Delete Chapter, Delete Part and File ▸ New/Open all rewrite the
        # chapter list itself, and those are the operations that actually lose
        # a book. Built here, so its baseline is the manuscript as restored.
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()

        # Flush the final edit when the window goes away so nothing is lost.
        self.connect("destroy", self._on_destroy)
        if not saved:
            # First run: persist the seed so the "Saved" state is truthful and
            # the empty manuscript reopens instead of being silently re-seeded.
            self._save_state()
        # Compute the initial page total AFTER the window is up, so paginating a
        # restored manuscript never delays first paint (launch-perf sensitive).
        # Deliberately on the same debounce the typing path uses rather than an
        # immediate idle: on a full-length book the layout takes about a second,
        # and running it the instant the window appeared froze the app just as
        # the writer reached for the keyboard.
        self._arm_pagestat()

    # ============================ MENUS ============================
    def menu_items(self, name):
        """Wire this app's own menus to its real editor actions; defer to the
        base for Edit / app-name (Cut/Copy/Paste/Close/About)."""
        if name == "File":
            return [("New    Ctrl+N", self._on_file_new),
                    ("Open…    Ctrl+O", self._on_file_open),
                    nbapp.SEP,
                    ("Save    Ctrl+S", self._on_file_save),
                    ("Save As…    Ctrl+Shift+S", self._on_file_save_as),
                    nbapp.SEP,
                    ("Export to PDF…", self._on_export_pdf),
                    ("Zine Print…", self._on_zine_print),
                    nbapp.SEP,
                    ("New Chapter", lambda: self._on_new_chapter()),
                    ("New Part…", lambda: self._on_new_part()),
                    # Guarded so one chapter / one part always remains: a lone
                    # chapter or the sole part renders the item disabled.
                    ("Delete Chapter…",
                     (lambda: self._on_delete_chapter())
                     if len(self.chapters) > 1 else None),
                    ("Delete Part…",
                     (lambda: self._on_delete_part())
                     if len(self.parts) > 1 else None),
                    nbapp.SEP,
                    ("Close    Esc", self.close)]
        if name == "Edit":
            # Undo/redo lead the menu, as they do in every editor — and they
            # have to be VISIBLE, not just bound to a key nobody can discover.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit")
        if name == "Format":
            return [("Bold    Ctrl+B", lambda: self._on_fmt(None, "bold")),
                    ("Italic    Ctrl+I", lambda: self._on_fmt(None, "italic")),
                    ("Underline    Ctrl+U",
                     lambda: self._on_fmt(None, "underline")),
                    nbapp.SEP,
                    ("Body Text", lambda: self._apply_para_style("Body")),
                    ("Heading", lambda: self._apply_para_style("Heading")),
                    ("Quote", lambda: self._apply_para_style("Quote"))]
        if name == "Insert":
            return [("Block Quote", lambda: self._on_fmt(None, "quote")),
                    ("Bullet List", lambda: self._on_fmt(None, "bullet")),
                    nbapp.SEP,
                    ("New Chapter", lambda: self._on_new_chapter()),
                    ("New Part…", lambda: self._on_new_part())]
        if name == "View":
            return [("Find in Manuscript    Ctrl+F",
                     lambda: self._toggle_find(True)),
                    nbapp.SEP,
                    ("Next Chapter", self._next_chapter),
                    ("Previous Chapter", self._prev_chapter),
                    nbapp.SEP,
                    ("Focus Editor", self._focus_editor)]
        return super().menu_items(name)

    def _next_chapter(self):
        if self.chapters:
            self._select_chapter(min(self.active + 1, len(self.chapters) - 1))

    def _prev_chapter(self):
        if self.chapters:
            self._select_chapter(max(self.active - 1, 0))

    def _focus_editor(self):
        try:
            self.view.grab_focus()
        except Exception:
            pass

    # ============================ SIDEBAR ============================
    def _sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # The sidebar SCALES with the panel instead of always taking 336px.
        # At a fixed 336 this app's minimum came to exactly 1024: it "fits" a
        # 1024 panel with nothing whatsoever to spare, so the manuscript column
        # sat edge-to-edge with no margin and any drift in a font or a padding
        # would push it off the screen. Same rule Academics uses for the same
        # reason; at 1920 the divisor still yields the full 336.
        sw, _sh = nbapp.screen_size()
        side.set_size_request(min(336, max(240, sw // 5)), -1)
        side.get_style_context().add_class("nvside")

        # --- header ---
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("nvhead")

        ey = Gtk.Label(label=_t("MANUSCRIPT"), xalign=0)
        ey.get_style_context().add_class("nveyebrow")
        head.pack_start(ey, False, False, 0)

        # The manuscript title is click-to-rename (same idiom as a part header),
        # so it is a real, editable field rather than a frozen label — its value
        # round-trips through persistence and seeds the Save As filename.
        title_ev = Gtk.EventBox()
        title_ev.get_style_context().add_class("nvtitlebtn")
        title_ev.set_tooltip_text(_t("Rename manuscript"))
        title = Gtk.Label(xalign=0)
        title.get_style_context().add_class("nvtitle")
        title.set_line_wrap(True)
        # Break inside an over-long unbroken title so a pathological name can't
        # push past the fixed sidebar width.
        title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Cap the measure, or the label's natural width is the WHOLE title on
        # one line and the sidebar (packed at its natural width) widens with it
        # — a 45-character manuscript name stretched the column from 336 to
        # 528px and shoved the editor sideways.
        title.set_max_width_chars(22)
        # ...and cap the HEIGHT too. Capping only the width left the label free
        # to grow DOWNWARD: it is packed expand=False, so it always takes its
        # full natural height and the chapter ScrolledWindow below it gets
        # whatever is left. A ~500-character title wrapped to 30-odd lines,
        # pushed the app's minimum height to 915px on a 740px panel, squeezed
        # the chapter list to a ~26px sliver (every chapter row unreachable) and
        # shoved "New Chapter" off the bottom.
        #
        # IDIOM — "wrap, but at most N lines". There is no other set_lines() in
        # de/, against 118 set_line_wrap() calls, so this is the pattern to copy
        # anywhere a WRAPPING label sits in a fixed-height column: set_lines(N)
        # bounds the block and set_ellipsize(END) is what makes GTK honour it
        # (Pango only applies a line limit when it has an ellipsize mode to
        # truncate with — set_lines alone silently does nothing). The full title
        # still lives in the model, in the file and in the rename dialog; only
        # this one display is clipped.
        title.set_lines(3)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_lbl = title
        # The title the writer typed. THE MODEL IS THIS STRING, not the
        # label: nbi18n auto-translates whatever is set on a Gtk.Label, and
        # _serialize used to read the manuscript title straight back out of
        # the widget — so a book named "Notes" on a Spanish install was
        # stored, displayed and saved-as "Notas". Every write goes through
        # _set_title (verbatim), every read through self._title.
        self._set_title(_untitled())
        title_ev.add(title)
        title_ev.connect("button-press-event",
                         lambda *_a: (self._rename_manuscript(), True)[1])
        head.pack_start(title_ev, False, False, 0)

        # Plain running word total for the manuscript. No goal / progress bar:
        # the word count is informational only, with no target to track toward.
        stat = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        stat.get_style_context().add_class("nvstatrow")
        self.total_lbl = Gtk.Label(label=_t("0 words"), xalign=0)
        self.total_lbl.get_style_context().add_class("nvtotal")
        self.total_lbl.set_tooltip_text(_t("Words in the whole manuscript"))
        stat.pack_start(self.total_lbl, False, False, 0)
        head.pack_start(stat, False, False, 0)

        side.pack_start(head, False, False, 0)

        # --- chapter list ---
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("nvsidescroll")
        self.chaplist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chaplist.get_style_context().add_class("nvchaplist")

        # Part section headers are rendered dynamically per part inside
        # rows_box (see _refresh_chapter_list); there is no hard-coded part.
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chaplist.pack_start(self.rows_box, False, False, 0)
        scroll.add(self.chaplist)
        side.pack_start(scroll, True, True, 0)

        # --- new chapter ---
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        foot.get_style_context().add_class("nvfoot")
        newbtn = Gtk.Button()
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("nvnewbtn")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        inner.set_halign(Gtk.Align.CENTER)
        inner.pack_start(
            Gtk.Image.new_from_pixbuf(nbicons.pixbuf("plus", 16, "#2A2620")),
            False, False, 0)
        inner.pack_start(Gtk.Label(label=_t("New Chapter")), False, False, 0)
        newbtn.add(inner)
        newbtn.connect("clicked", lambda *_: self._on_new_chapter())
        foot.pack_start(newbtn, False, False, 0)
        side.pack_start(foot, False, False, 0)
        return side

    # ============================ EDITOR ============================
    def _editor(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # --- format bar ---
        fbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        fbar.get_style_context().add_class("nvformatbar")

        stylebtn = Gtk.Button()
        stylebtn.set_relief(Gtk.ReliefStyle.NONE)
        stylebtn.get_style_context().add_class("nvstylebtn")
        stylebtn.set_tooltip_text(_t("Paragraph style"))
        self.stylebtn = stylebtn
        sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        # The pill reflects the CURRENT paragraph's style; "Body" is the default.
        slab = Gtk.Label(label=_t("Body"))
        slab.get_style_context().add_class("nvstylelab")
        self.style_lbl = slab
        sb.pack_start(slab, False, False, 0)
        car = Gtk.Label(label="▾")
        car.get_style_context().add_class("nvcaret")
        sb.pack_start(car, False, False, 0)
        stylebtn.add(sb)
        stylebtn.connect("clicked", self._on_style)
        fbar.pack_start(stylebtn, False, False, 0)
        fbar.pack_start(self._sep(), False, False, 10)

        for label, cls, cmd, tip in (
                ("B", "bold", "bold", "Bold (Ctrl+B)"),
                ("I", "ital", "italic", "Italic (Ctrl+I)"),
                ("U", "under", "underline", "Underline (Ctrl+U)")):
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("nvfmtbtn")
            b.get_style_context().add_class(cls)
            b.set_tooltip_text(tip)
            b.connect("clicked", self._on_fmt, cmd)
            fbar.pack_start(b, False, False, 2)
        fbar.pack_start(self._sep(), False, False, 10)

        for icon, cmd, tip in (("quote", "quote", "Block quote"),
                               ("viewlist", "bullet", "Bullet list")):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("nvfmtbtn")
            b.set_tooltip_text(tip)
            b.add(Gtk.Image.new_from_pixbuf(
                nbicons.pixbuf(icon, 19, "#2A2620")))
            b.connect("clicked", self._on_fmt, cmd)
            fbar.pack_start(b, False, False, 2)

        # right cluster: word count + save state
        self.save_lbl = Gtk.Label()
        self.save_lbl.get_style_context().add_class("nvsave")
        self.save_lbl.set_markup(
            '<span foreground="#7FA98C">● </span>Saved %s'
            % time.strftime("%H:%M"))
        fbar.pack_end(self.save_lbl, False, False, 0)
        fbar.pack_end(self._sep(), False, False, 16)
        self.count_lbl = Gtk.Label(label=_t("0 words"))
        self.count_lbl.get_style_context().add_class("nvcount")
        self.count_lbl.set_tooltip_text(_t("Words in this chapter"))
        fbar.pack_end(self.count_lbl, False, False, 0)

        col.pack_start(fbar, False, False, 0)
        col.pack_start(self._findbar(), False, False, 0)

        # --- canvas ---
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("nvcanvas")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_halign(Gtk.Align.CENTER)
        # The writing column takes the design's measure where the canvas has
        # room for it and shrinks toward COL_MIN where it does not — measured
        # off the canvas ACTUALLY allocated, not off the screen. Deriving it
        # from the screen stranded a 368px column in a 688px canvas on a 1024px
        # panel, while the same code showed 620px on the developer's 1920px
        # monitor, so the narrow case never appeared in a render. The column
        # starts at its minimum and _fit_page widens it on the first allocate.
        self._page_px = COL_MIN
        page.set_size_request(COL_MIN, -1)
        page.get_style_context().add_class("nvpage")
        self.page = page

        # Populated by _recount() (chapter number · derived part label).
        self.eyebrow = Gtk.Label(label="", xalign=0)
        self.eyebrow.get_style_context().add_class("nvcaneyebrow")
        # Ellipsize so an over-long part name in the eyebrow can't force the
        # centered page wider than its column.
        self.eyebrow.set_ellipsize(Pango.EllipsizeMode.END)
        self.eyebrow.set_max_width_chars(52)
        page.pack_start(self.eyebrow, False, False, 0)

        self.view = Gtk.TextView()
        # WORD_CHAR, not WORD: a word longer than the writing column (a pasted
        # URL, a long compound) cannot break under WORD, so it runs off the
        # column AND raises the TextView's minimum width — with a 78-character
        # word in the prose the whole window's minimum grew past a 1024px
        # panel, putting the save state out of reach.
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.get_style_context().add_class("nvbody")
        self.view.set_pixels_below_lines(14)
        self.view.set_pixels_inside_wrap(10)
        # Portrait page proportion (5.5:8.5) as a minimum height hint.
        self.view.set_size_request(COL_MIN, int(COL_MIN * PAGE_H / PAGE_W))

        # Empty-state: a serif ghost prompt beneath the chapter heading that
        # shows only while the active chapter has no body text. It sits in an
        # overlay so clicks/focus pass straight through to the writing surface.
        overlay = Gtk.Overlay()
        overlay.add(self.view)
        self.placeholder = Gtk.Label(label=_t("Empty chapter"))
        self.placeholder.get_style_context().add_class("nvplaceholder")
        self.placeholder.set_halign(Gtk.Align.START)
        self.placeholder.set_valign(Gtk.Align.START)
        self.placeholder.set_margin_top(62)
        self.placeholder.set_margin_start(2)
        self.placeholder.set_no_show_all(True)
        overlay.add_overlay(self.placeholder)
        if hasattr(overlay, "set_overlay_pass_through"):
            overlay.set_overlay_pass_through(self.placeholder, True)
        page.pack_start(overlay, True, True, 0)

        scroll.add(page)
        # Track the viewport (not the scroller — its width includes the
        # scrollbar) so the column always matches the space actually available.
        vp = scroll.get_child()
        if vp is not None:
            vp.connect("size-allocate", self._fit_page)
        col.pack_start(scroll, True, True, 0)
        return col

    # ============================ FIND ============================
    # A novel is the one document you genuinely cannot read through to find
    # something. There was no search of any kind: the only way back to the scene
    # with the blue coat in a thirty-chapter manuscript was to open chapters one
    # at a time and read. This bar searches EVERY chapter and walks the writer
    # to each hit, switching chapters as it goes.
    def _findbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("nvfindbar")
        self.find_entry = Gtk.SearchEntry()
        nbicons.style_search_entry(self.find_entry)
        self.find_entry.set_placeholder_text(_t("Find in manuscript"))
        self.find_entry.set_width_chars(26)
        self.find_entry.get_style_context().add_class("nvfindentry")
        self.find_entry.connect("search-changed", lambda *_: self._do_find())
        self.find_entry.connect("activate", lambda *_: self._find_step(1))
        bar.pack_start(self.find_entry, False, False, 0)

        prev = Gtk.Button(label="‹")
        prev.get_style_context().add_class("nvfindbtn")
        prev.set_tooltip_text(_t("Previous match"))
        prev.connect("clicked", lambda *_: self._find_step(-1))
        bar.pack_start(prev, False, False, 0)
        nxt = Gtk.Button(label="›")
        nxt.get_style_context().add_class("nvfindbtn")
        nxt.set_tooltip_text(_t("Next match"))
        nxt.connect("clicked", lambda *_: self._find_step(1))
        bar.pack_start(nxt, False, False, 0)

        self.find_count = Gtk.Label(label="", xalign=0)
        self.find_count.get_style_context().add_class("nvfindcount")
        bar.pack_start(self.find_count, False, False, 6)

        close = Gtk.Button(label=_t("Done"))
        close.get_style_context().add_class("nvfindbtn")
        close.connect("clicked", lambda *_: self._toggle_find(False))
        bar.pack_end(close, False, False, 0)
        # Show the controls ONCE, then take the bar out of show_all()'s reach and
        # drive it by hand — show_all() returns immediately on a widget with
        # no-show-all set, so the children have to be shown first (the same trap
        # Writer's find bar fell into, where it opened as an empty strip).
        bar.show_all()
        bar.set_no_show_all(True)
        bar.hide()
        self._findbar_w = bar
        return bar

    def _toggle_find(self, show=None):
        vis = self._findbar_w.get_visible()
        show = (not vis) if show is None else show
        self._findbar_w.set_visible(show)
        if show:
            self.find_entry.grab_focus()
            self._do_find()
        else:
            self._find_hits = []
            self.find_count.set_text("")
            self.view.grab_focus()

    def _do_find(self):
        """Collect every match in every chapter for the current search text."""
        needle = self.find_entry.get_text().strip().lower()
        self._find_hits = []
        self._find_i = -1
        if not needle:
            self.find_count.set_text("")
            return
        chapters = 0
        for ci, ch in enumerate(self.chapters):
            hay = self._buffer_text(ch["buffer"]).lower()
            i = hay.find(needle)
            if i != -1:
                chapters += 1
            while i != -1:
                self._find_hits.append((ci, i, i + len(needle)))
                i = hay.find(needle, i + len(needle))
        n = len(self._find_hits)
        if not n:
            self.find_count.set_text(_t("No matches"))
            return
        self.find_count.set_text(
            (_t("1 match") if n == 1 else _t("%d matches") % n)
            + (" " + (_t("in 1 chapter") if chapters == 1
                      else _t("in %d chapters") % chapters)))
        self._find_step(1)

    def _find_step(self, direction):
        """Move to the next/previous match, opening its chapter if needed and
        selecting the words so the writer lands exactly on them."""
        if not self._find_hits:
            self._do_find()
            if not self._find_hits:
                return
        # A hit names a CHAPTER, and the chapter list can have changed under it:
        # nothing re-runs the find, so deleting a chapter (or File > Open, or an
        # undo) with the find bar still open left hits pointing past the end of
        # self.chapters, and the next press of "Next match" raised IndexError
        # inside the handler — the find buttons went dead for the rest of the
        # session with nothing on screen to explain it. Re-find against the
        # manuscript as it stands now.
        if any(ci >= len(self.chapters) for ci, _s, _e in self._find_hits):
            # _do_find ends by stepping to the first match, so it has already
            # moved the selection — returning here is what stops one press of
            # "Next" from counting twice.
            self._do_find()
            return
        self._find_i = (self._find_i + direction) % len(self._find_hits)
        ci, s_off, e_off = self._find_hits[self._find_i]
        if ci != self.active:
            self._select_chapter(ci)
        buf = self.chapters[ci]["buffer"]
        n = buf.get_char_count()
        s = buf.get_iter_at_offset(max(0, min(s_off, n)))
        e = buf.get_iter_at_offset(max(0, min(e_off, n)))
        buf.select_range(s, e)
        # scroll_to_mark, not scroll_to_iter: a mark survives the layout pass, so
        # the jump still lands even when the target line has not been measured
        # yet (a chapter opened a moment ago).
        self.view.scroll_to_mark(buf.get_insert(), 0.25, False, 0, 0)
        self.find_count.set_text(
            _t("%d of %d") % (self._find_i + 1, len(self._find_hits)))

    def _fit_page(self, _w, alloc):
        """Size the writing column to the canvas: the design's measure where it
        fits, the canvas width where it does not. Writes only when the value
        actually changes, so it cannot loop with the resize it triggers."""
        w = max(COL_MIN, min(COL_MAX, alloc.width - COL_PAD))
        if w == self._page_px:
            return
        self._page_px = w
        self.page.set_size_request(w, -1)
        self.view.set_size_request(w, int(w * PAGE_H / PAGE_W))

    # ============================ CHAPTERS ============================
    def _make_buffer(self, num, body=None, ranges=None):
        buf = Gtk.TextBuffer()
        tt = buf.get_tag_table()
        # The heading inherits the editor's serif font from CSS (Newsreader →
        # "Liberation Serif" → serif); it must NOT pin family="Newsreader",
        # which isn't installed and would make Pango substitute a sans face for
        # every chapter heading. Only size/weight/spacing are set here.
        h = Gtk.TextTag(name="heading")
        h.set_property("size-points", 32)
        h.set_property("weight", Pango.Weight.MEDIUM)
        h.set_property("pixels-below-lines", 22)
        tt.add(h)
        # Block-quote paragraph style: indented, italic serif, muted ink.
        q = Gtk.TextTag(name="quote")
        q.set_property("style", Pango.Style.ITALIC)
        q.set_property("left-margin", 26)
        q.set_property("foreground", "#615C51")
        tt.add(q)
        for name, prop, val in (("bold", "weight", Pango.Weight.BOLD),
                                ("italic", "style", Pango.Style.ITALIC),
                                ("underline", "underline",
                                 Pango.Underline.SINGLE)):
            t = Gtk.TextTag(name=name)
            t.set_property(prop, val)
            tt.add(t)
        # Seed the buffer body: a fresh chapter starts with just its "Chapter N"
        # heading line; a restored chapter is rebuilt from its saved text. This
        # insert runs BEFORE connecting "changed" so creating/loading a chapter
        # never triggers a spurious autosave.
        if body is None:
            body = "Chapter %s\n" % num
        buf.insert(buf.get_start_iter(), body)
        if ranges is None:
            # Brand-new chapter with no saved spans: seed just the "Chapter N"
            # first line as the 32pt heading.
            line1 = buf.get_iter_at_line(0)
            line1_end = buf.get_iter_at_line(1)
            buf.apply_tag_by_name("heading", line1, line1_end)
        else:
            # Restored chapter: reinstate exactly the spans (B/I/U + heading)
            # the writer saved. Never force line 1 to a heading — prose that
            # replaced the seeded "Chapter N" line must stay prose.
            self._apply_ranges(buf, ranges)
        buf.connect("changed", self._on_change)
        # Connected here, AFTER the body above has been seeded, so restoring a
        # saved chapter never rewrites the writer's own punctuation.
        buf.connect("insert-text", self._on_insert_before)
        # Track cursor movement so the format-bar style pill reflects whichever
        # paragraph the caret currently sits in.
        buf.connect("notify::cursor-position", self._on_cursor_moved)
        return buf

    def _on_insert_before(self, buf, it, text, length):
        """Turn typewriter marks into real typography as they are typed: " and '
        become the matching curly quotes, -- becomes an em dash. Only ever fires
        for a single typed character, so pasted prose is left exactly as it is."""
        if self._suppress_change or self._smart_busy:
            return
        prev = ""
        probe = it.copy()
        if probe.backward_char():
            prev = probe.get_char()
        repl = nbapp.smart_replacement(prev, text)
        if repl is None:
            return
        self._smart_busy = True
        try:
            buf.stop_emission_by_name("insert-text")
            if text == "-":               # swallow the first hyphen of the pair
                buf.delete(probe, it)
                it = buf.get_iter_at_mark(buf.get_insert())
            buf.insert(it, repl)
        finally:
            self._smart_busy = False

    def _new_chapter(self, select=False, part=None):
        num = str(len(self.chapters) + 1)
        buf = self._make_buffer(num)
        if part is None:
            part = len(self.parts) - 1 if self.parts else 0
        ch = {"num": num, "title": "Chapter " + num, "buffer": buf,
              "part": part}
        # Cache this chapter's word count and fold it into the running total so
        # later edits only adjust the delta (a fresh chapter is 0 body words).
        ch["wc"] = self._count_buffer(buf)
        self.chapters.append(ch)
        self._total_words += ch["wc"]
        if select:
            self.active = len(self.chapters) - 1
            self._show_buffer(buf)
            self._place_cursor_body(buf)

    def _on_new_chapter(self):
        self.undo.checkpoint("New Chapter")
        self._new_chapter(select=True)
        self._refresh_chapter_list()
        self._recount()
        self.undo.commit()

    def _select_chapter(self, i):
        if i == self.active:
            return
        self.active = i
        self._show_buffer(self.chapters[i]["buffer"])
        self._place_cursor_body(self.chapters[i]["buffer"])
        self._refresh_chapter_list()
        self._recount()
        # Cheap label refresh from the cached pagination — the current chapter's
        # start page is already known, so no re-layout is needed here.

    def _refresh_chapter_list(self):
        for c in self.rows_box.get_children():
            self.rows_box.remove(c)
        # Emit a part section header each time the part changes, then that
        # part's chapter rows — reproducing the design's multi-part sidebar.
        # A lone unnamed part shows no header (see _parts_visible).
        show_parts = self._parts_visible()
        last_part = None
        for i, ch in enumerate(self.chapters):
            pi = ch.get("part", 0)
            if show_parts and pi != last_part:
                self.rows_box.pack_start(self._part_header(pi), False, False, 0)
                last_part = pi
            self.rows_box.pack_start(self._chapter_row(i, ch), False, False, 0)
        self.rows_box.show_all()

    # Ordinal words for part labels ("PART ONE", "PART TWO", …); beyond the
    # table we fall back to the numeral. (No stdlib helpers — de/calendar.py
    # shadows the stdlib, so nothing here may import it.)
    _ORDINALS = ("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
                 "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN")

    def _ordinal(self, i):
        return self._ORDINALS[i] if 0 <= i < len(self._ORDINALS) else str(i + 1)

    def _parts_visible(self):
        """Whether the part chrome (sidebar section headers + the eyebrow's
        part suffix) is shown. A pristine manuscript sits in a single unnamed
        part; surfacing a lone 'PART ONE' only clutters the chapter list and
        imposes a structure the writer never asked for, so parts appear once
        there is more than one, or the sole part has been named."""
        return len(self.parts) > 1 or bool(
            self.parts and self.parts[0].get("name", "").strip())

    def _part_label(self, pi):
        """The visible label for part `pi`: "PART TWO — THE LONG WINTER",
        or just "PART TWO" when the part has no name yet."""
        ordw = self._ordinal(pi)
        name = self.parts[pi]["name"].strip() if 0 <= pi < len(self.parts) else ""
        return "PART {} — {}".format(ordw, name) if name else "PART {}".format(ordw)

    def _part_header(self, pi):
        ev = Gtk.EventBox()
        ev.get_style_context().add_class("nvparthdr")
        ev.set_tooltip_text(_t("Rename part"))
        lab = Gtk.Label(label=self._part_label(pi), xalign=0)
        lab.get_style_context().add_class("nvpart")
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        ev.add(lab)
        ev.connect("button-press-event",
                   lambda *_a, idx=pi: (self._rename_part(idx), True)[1])
        return ev

    def _chapter_row(self, i, ch):
        act = (i == self.active)
        ev = Gtk.EventBox()
        # The hover tint stays on the EventBox: only the widget the pointer is
        # actually over gets GTK's PRELIGHT flag, and GTK3 does not propagate it
        # to children, so a :hover rule on the inner box would never fire. The
        # box sits flush inside the EventBox, so the tint lands in the same
        # place; the active row paints its own fill on top of it.
        ev.get_style_context().add_class("nvrowhit")
        ev.connect("button-press-event",
                   lambda *_a, idx=i: self._select_chapter(idx))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)
        # .nvrow lives on the BOX, not on the EventBox: a GtkEventBox paints a
        # CSS background but ignores CSS padding and margin, so with the class
        # on it every chapter row collapsed to 31px of bare text with none of
        # the 11px breathing room the style asks for. (Journal's entry rows
        # already put their row class on the inner box for this reason.)
        row.get_style_context().add_class("nvrow")
        if act:
            row.get_style_context().add_class("active")

        numlab = Gtk.Label(label=ch["num"])
        numlab.get_style_context().add_class("nvnum")
        if act:
            numlab.get_style_context().add_class("active")
        numlab.set_size_request(26, 26)
        # Keep the badge a CIRCLE: under the default FILL alignment it stretches
        # to the row's height and border-radius:50% draws it as an ellipse.
        numlab.set_valign(Gtk.Align.CENTER)
        row.pack_start(numlab, False, False, 0)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        txt.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=ch["title"], xalign=0)
        t.get_style_context().add_class("nvrowtitle")
        t.set_ellipsize(Pango.EllipsizeMode.END)
        # Read the cached count (kept current by _init_counts / _on_change);
        # never re-scan the buffer here — this runs for every row on rebuild.
        w = ch.get("wc")
        if w is None:
            w = self._count_buffer(ch["buffer"])
            ch["wc"] = w
        wl = Gtk.Label(label=self._wordstr(w), xalign=0)
        wl.get_style_context().add_class("nvrowwords")
        txt.pack_start(t, False, False, 0)
        txt.pack_start(wl, False, False, 0)
        row.pack_start(txt, True, True, 0)
        ev.add(row)
        # Keep live handles so the typing path can update THIS chapter's row
        # title + word-count in place, without rebuilding the whole list.
        ch["_row_title"] = t
        ch["_row_words"] = wl
        return ev

    # ============================ COUNTING ============================
    def _count_buffer(self, buf):
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        # drop the leading "Chapter N" heading line from the count
        parts = txt.split("\n", 1)
        rest = parts[1] if len(parts) > 1 else ""
        return len(rest.split())

    def _wordstr(self, n):
        # Pluralize the word-count label so a single word reads "1 word",
        # not the ungrammatical "1 words".
        return "1 word" if n == 1 else "{:,} words".format(n)

    def _pagestr(self, n):
        return "1 page" if n == 1 else "{:,} pages".format(n)

    def _init_counts(self):
        """(Re)build the per-chapter word-count cache and the running total
        from scratch. Called once at startup/restore; the typing path then
        keeps ch["wc"] and self._total_words current incrementally."""
        total = 0
        for c in self.chapters:
            c["wc"] = self._count_buffer(c["buffer"])
            total += c["wc"]
        self._total_words = total

    def _recount(self):
        # Read the cached running total + active chapter count; never re-sum
        # every buffer here (the typing path keeps both current).
        total = self._total_words
        cur = self.chapters[self.active]["wc"]
        # Sidebar total pairs the running word count with the book's page total
        # at 5.5x8.5 (once paginated), so the writer sees the manuscript's real
        # printed length, not just a word figure.
        total_txt = self._wordstr(total)
        # Only claim a page count once something has actually been written. An
        # empty manuscript still imposes a title page, a Contents page and the
        # opening of Chapter 1, so a brand-new book announced itself as
        # "0 words · 3 pages" — three pages of nothing.
        if self._page_count and total:
            total_txt += " · " + self._pagestr(self._page_count)
        self.total_lbl.set_text(total_txt)
        self.count_lbl.set_text(self._wordstr(cur))
        ch = self.chapters[self.active]
        # Eyebrow echoes the chapter number, plus the active chapter's part
        # label only once parts are actually in play (see _parts_visible), and
        # the chapter's start page in the paginated book when it is known.
        if self._parts_visible():
            eb = "CHAPTER {} · {}".format(
                ch["num"], self._part_label(ch.get("part", 0)))
        else:
            eb = "CHAPTER {}".format(ch["num"])
        startp = self._chapter_pages.get(self.active)
        if startp and total:          # see the page-count note above
            eb += " · PAGE {}".format(startp)
        self.eyebrow.set_text(eb)
        # ghost prompt only while this chapter's body is still empty
        self.placeholder.set_visible(cur == 0)
        self._update_style_pill()

    def _on_change(self, buf):
        # Ignore programmatic structural rewrites (see _rewrite_heading); those
        # keep the caches current themselves and must not be re-attributed to
        # whichever chapter happens to be active.
        if self._suppress_change:
            return
        # Track the sidebar row name against the edited serif heading: the
        # chapter's first line IS its heading, so mirror it into ch["title"]
        # (falling back to "Chapter N" when the writer clears it).
        ch = self.chapters[self.active]
        first = self._buffer_text(buf).split("\n", 1)[0].strip()
        ch["title"] = first if first else "Chapter " + str(ch["num"])
        # Recount ONLY the active chapter and adjust the running total by the
        # delta — no re-summing of every chapter on each keystroke.
        new = self._count_buffer(buf)
        self._total_words += new - ch.get("wc", 0)
        ch["wc"] = new
        # Update THIS chapter's sidebar row in place (title + word count) rather
        # than tearing down and rebuilding the whole chapter list. Structural
        # ops (new/select/part changes) still go through _refresh_chapter_list.
        tlbl = ch.get("_row_title")
        if tlbl is not None:
            tlbl.set_text(ch["title"])
        wlbl = ch.get("_row_words")
        if wlbl is not None:
            wlbl.set_text(self._wordstr(new))
        self._recount()
        self._trigger_save()

    def _trigger_save(self):
        """Show the 'Editing…' state and (re)arm the debounced disk write. Used
        by both text edits and paragraph-style changes so nothing is lost."""
        # One undo step per burst of typing. Only re-arms a timer, so it costs
        # nothing measurable per keystroke (see nbapp.UndoHistory). Sitting HERE
        # rather than in _on_change also covers the paragraph-style path, which
        # emits no "changed" signal of its own.
        self.undo.touch()
        self.save_lbl.set_markup(
            '<span foreground="#C8341E">● </span>Editing…')
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(900, self._mark_saved)
        # Whatever the writer just typed changes the pagination, and re-laying
        # the book out is the most expensive thing this app does — so drop any
        # pending page-total refresh and let _mark_saved re-arm it once the
        # keyboard has actually gone quiet.
        if self._page_timer:
            GLib.source_remove(self._page_timer)
            self._page_timer = None

    def _mark_saved(self):
        # The debounce has fired: perform the REAL disk write, and only claim
        # "Saved" once the bytes have actually reached the file.
        if self._save_state():
            self.save_lbl.set_markup(
                '<span foreground="#7FA98C">● </span>Saved %s'
                % time.strftime("%H:%M"))
        else:
            self.save_lbl.set_markup(
                '<span foreground="#C8341E">● </span>Not saved')
        self._save_timer = None
        # Typing has settled, so line the page total up again — on its own,
        # longer debounce (see _arm_pagestat), never here. Re-laying the whole
        # book out takes about a second on a finished novel, and doing it 900ms
        # after the last keystroke meant every pause to think froze the window.
        self._arm_pagestat()
        return False

    def _arm_pagestat(self):
        """(Re)arm the page-total refresh, on a delay that scales with what the
        last pagination actually cost.

        A short manuscript lays out in a few milliseconds and its page figure
        catches up within seconds, so it still feels live; a 700-page book waits
        for a real lull instead of interrupting the writer. Any edit cancels the
        pending refresh (see _trigger_save), so it can never run mid-sentence."""
        if self._page_timer:
            GLib.source_remove(self._page_timer)
        delay = int(min(30000, max(2500, self._paginate_ms * 20)))
        self._page_timer = GLib.timeout_add(delay, self._pagestat_tick)

    def _pagestat_tick(self):
        self._page_timer = None
        self._refresh_pagestat()
        return False

    # ============================ PERSISTENCE ============================
    def _buffer_text(self, buf):
        """Full plain text of a chapter buffer (heading line included)."""
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    # Formatting is persisted per chapter as {tag_name: [[start_off, end_off]]}
    # offset ranges so bold / italic / underline AND the heading survive a save
    # and reload — before, only plain body text was written, so every span was
    # dropped and line 1 was blindly re-forced into a heading on load.
    _RANGE_TAGS = ("bold", "italic", "underline", "heading", "quote")

    def _buffer_ranges(self, buf):
        """Snapshot a chapter buffer's tag spans into serializable offsets."""
        tbl = buf.get_tag_table()
        end_off = buf.get_char_count()
        ranges = {}
        for name in self._RANGE_TAGS:
            tag = tbl.lookup(name)
            if tag is None:
                continue
            spans = []
            it = buf.get_start_iter()
            # A span open at offset 0 has no preceding toggle, so seed from
            # has_tag.
            start_off = 0 if it.has_tag(tag) else None
            while it.forward_to_tag_toggle(tag):
                off = it.get_offset()
                if it.begins_tag(tag):
                    start_off = off
                elif it.ends_tag(tag) and start_off is not None:
                    spans.append([start_off, off])
                    start_off = None
            if start_off is not None:
                spans.append([start_off, end_off])
            if spans:
                ranges[name] = spans
        return ranges

    def _apply_ranges(self, buf, ranges):
        """Re-apply serialized tag spans; tolerates old files with no ranges."""
        if not isinstance(ranges, dict):
            return
        n = buf.get_char_count()
        for name in self._RANGE_TAGS:
            spans = ranges.get(name)
            if not isinstance(spans, list):
                continue
            for span in spans:
                try:
                    s, e = int(span[0]), int(span[1])
                except (TypeError, ValueError, IndexError):
                    continue
                s = max(0, min(s, n))
                e = max(0, min(e, n))
                if e <= s:
                    continue
                buf.apply_tag_by_name(
                    name, buf.get_iter_at_offset(s), buf.get_iter_at_offset(e))

    def _load_state(self):
        """Read the session-recovery manuscript from disk, or None so the
        caller falls back to the app's empty single-chapter default.

        A file that parses but holds no manuscript is QUARANTINED here, before
        __init__'s `if not saved: self._save_state()` can write the seeded empty
        Chapter 1 over it. nbapp's one .bak cannot save this: a seeded blank
        manuscript is a title, a chapter number and a "Chapter 1" heading line,
        which OUTWEIGHS a store holding a whole book under an unexpected key, so
        _bak_would_shrink sees no regression and the second open overwrites the
        only remaining copy. See nbapp.quarantine_unrecognized."""
        try:
            with open(NOVEL_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return None                  # missing / unreadable: nothing to lose
        state = self._parse_state(data)
        if state is None:
            nbapp.quarantine_unrecognized(NOVEL_FILE)
        return state

    def _parse_state(self, data):
        """Validate a decoded manuscript document into a normalized state dict
        {title, parts, chapters:[{num,title,body,ranges,part}], active,
        doc_path}, or None when it is not a usable manuscript. Shared by the
        session-recovery loader and the File ▸ Open path."""
        if not isinstance(data, dict):
            return None
        raw = data.get("chapters")
        if not isinstance(raw, list) or not raw:
            return None
        # Restore the part model. Old files predate parts → one unnamed
        # part that every chapter falls back into.
        raw_parts = data.get("parts")
        parts = []
        if isinstance(raw_parts, list):
            for p in raw_parts:
                if isinstance(p, dict):
                    parts.append({"name": str(p.get("name", "")).strip()})
        if not parts:
            parts = [{"name": ""}]
        chapters = []
        for ch in raw:
            if not isinstance(ch, dict):
                continue
            n = str(len(chapters) + 1)
            raw_ranges = ch.get("ranges")
            pt = ch.get("part", 0)
            if not isinstance(pt, int) or not (0 <= pt < len(parts)):
                pt = 0
            chapters.append({
                "num": str(ch.get("num", n)),
                "title": str(ch.get("title", "Chapter " + n)),
                "body": str(ch.get("body", "")),
                "ranges": raw_ranges if isinstance(raw_ranges, dict) else {},
                "part": pt,
            })
        if not chapters:
            return None
        active = data.get("active", 0)
        if not isinstance(active, int) or not (0 <= active < len(chapters)):
            active = 0
        dp = data.get("doc_path")
        if not isinstance(dp, str) or not dp:
            dp = None
        return {"title": str(data.get("title", _untitled())),
                "parts": parts, "chapters": chapters, "active": active,
                "doc_path": dp}

    def _serialize(self):
        """The full editable model as a JSON-serializable dict. Shared by the
        session-recovery writer and the File ▸ Save / Save As writers."""
        return {
            "title": self._title,
            "active": self.active,
            "doc_path": self.doc_path,
            "parts": [{"name": p.get("name", "")} for p in self.parts],
            "chapters": [{"num": c["num"], "title": c["title"],
                          "body": self._buffer_text(c["buffer"]),
                          "ranges": self._buffer_ranges(c["buffer"]),
                          "part": c.get("part", 0)}
                         for c in self.chapters],
        }

    def _save_state(self):
        """Persist the whole editable model to the session-recovery file.
        Never raises — a failed write must not crash the editor. True on OK."""
        try:
            nbapp.atomic_write_json(NOVEL_FILE, self._serialize())
            return True
        except Exception:
            return False

    # ---- undo / redo ----
    # The snapshot IS the autosave document: one serialise covers every chapter's
    # text and formatting, the part list, the title and the selection, so a
    # single mechanism can reverse typing, a deleted chapter and File ▸ Open
    # alike. _serialize costs ~1ms on a 31-chapter, 90,000-word manuscript and
    # only ever runs on the 600ms typing debounce or a structural edit — never
    # on the keystroke path. See nbapp.UndoHistory.
    def _undo_snapshot(self):
        snap = self._serialize()
        buf = self.view.get_buffer()
        snap["_caret"] = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        return snap

    def _undo_restore(self, state):
        self._restore(state)
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        buf = self.view.get_buffer()
        caret = min(max(0, state.get("_caret", 0)), buf.get_char_count())
        buf.place_cursor(buf.get_iter_at_offset(caret))
        self._save_state()
        self._arm_pagestat()
        self._focus_editor()

    def _restore(self, state):
        """Rebuild the chapter list + text buffers from a parsed state dict.
        Replaces any existing model, so it is safe both at startup and when
        the File ▸ Open / New actions swap the whole document."""
        # `outgoing` holds the chapters we are replacing until the editor has
        # been pointed at the new ones — see _show_buffer. Assigning
        # self.chapters = [] first dropped the last reference to the buffer the
        # TextView was still displaying, and File ▸ Open then took the whole app
        # down inside GTK's own line btree.
        outgoing = self.chapters
        self.chapters = []
        self._set_title(state["title"])
        # Copied, not adopted: an undo snapshot is restored through here too, and
        # the live self.parts is later appended to and renamed in place — which
        # would edit the stored history out from under itself.
        self.parts = [dict(p) for p in state["parts"]]
        self.doc_path = state.get("doc_path")
        for ch in state["chapters"]:
            buf = self._make_buffer(ch["num"], body=ch["body"],
                                    ranges=ch["ranges"])
            self.chapters.append({"num": ch["num"], "title": ch["title"],
                                  "buffer": buf, "part": ch.get("part", 0)})
        self.active = (state["active"]
                       if state["active"] < len(self.chapters) else 0)
        self._show_buffer(self.chapters[self.active]["buffer"])
        self._place_cursor_body(self.chapters[self.active]["buffer"])
        del outgoing

    def _show_buffer(self, buf):
        """Point the editor at `buf`, keeping the OUTGOING buffer alive across
        the swap.

        Every route that replaces the manuscript — File ▸ Open, File ▸ New,
        Delete Chapter — rebuilt self.chapters BEFORE re-pointing the view, so
        the buffer the TextView was still showing lost its last reference while
        the view held it. GTK then aborted the process the moment anything
        touched the replacement:

            Gtk:ERROR gtktextbtree.c: find_line_top_in_line_list:
            code should not be reached

        Opening a manuscript killed the app outright. Taking a reference here
        and releasing it only after set_buffer has returned keeps the swap in
        the order GTK expects."""
        keep = self.view.get_buffer()      # noqa: F841 — held across the swap
        self.view.set_buffer(buf)

    def _on_destroy(self, *_):
        # Final flush on window close so the last (possibly still-debounced)
        # edit is written before we exit.
        self.undo.cancel()
        for attr in ("_save_timer", "_page_timer"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._save_state()
        return False

    # ============================ FILE MENU ============================
    def _on_file_new(self):
        """File ▸ New — discard the current model and start a blank manuscript:
        one empty Chapter 1, one unnamed part, no bound file.

        Data-loss guard: a manuscript that holds content but has never been
        written to a user file exists ONLY in the session-recovery file, which
        the blank below immediately overwrites. In that case confirm before
        discarding; a manuscript that is empty, or already bound to a user
        file, is discarded without prompting."""
        self._close_style()
        self._close_prompt()
        if self.doc_path is None and self._has_content():
            self._confirm(
                "Discard this manuscript?",
                "This manuscript has not been saved to a file. Starting a new "
                "one will discard it.",
                "Discard", self._do_file_new)
            return
        self._do_file_new()

    def _do_file_new(self):
        """Blank the model to the empty single-chapter default and overwrite
        session recovery. Reached only once File ▸ New has cleared its
        data-loss guard."""
        # Undoable: the confirm only catches the manuscript the guard thinks is
        # worth keeping, and blanking overwrites the recovery file that is the
        # ONLY copy of an unsaved book.
        self.undo.checkpoint("New Manuscript")
        self.doc_path = None
        self.parts = [{"name": ""}]
        outgoing = self.chapters                         # noqa: F841
        self.chapters = []
        self._total_words = 0
        self.active = 0
        self._set_title(_untitled())
        self._new_chapter(select=True)
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        self._save_state()
        self.undo.commit()
        del outgoing

    def _has_content(self):
        """True when the manuscript holds anything worth preserving: body text,
        an added chapter, an added or named part, a non-default title, or a
        typed chapter heading. Errs toward True so File ▸ New confirms rather
        than silently discarding an unsaved manuscript."""
        if self._total_words > 0:
            return True
        if len(self.chapters) > 1:
            return True
        if len(self.parts) > 1 or (self.parts
                                   and self.parts[0]["name"].strip()):
            return True
        if self._title.strip() not in ("", UNTITLED_EN,
                                                     _untitled()):
            return True
        # The heading line IS the chapter title and is excluded from the word
        # count, so a typed-but-bodyless heading still counts as content.
        for ch in self.chapters:
            first = self._buffer_text(ch["buffer"]).split("\n", 1)[0].strip()
            if first and first != "Chapter " + str(ch["num"]):
                return True
        return False

    def _on_file_open(self):
        """File ▸ Open — Finder-style picker under $NB_HOME/Documents, unified
        with the Finder engine via nbpicker."""
        self._close_style()
        self._close_prompt()
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
        except Exception:
            pass
        path = nbpicker.open_file(self, title="Open Manuscript",
                                  start_dir=DOCS_DIR, patterns=("*.json",))
        if path and os.path.isfile(path):
            self._open_path(path)

    def _on_open_pick(self, _btn, path):
        self._close_prompt()
        self._open_path(path)

    def _open_path(self, path):
        """Load a manuscript JSON file, confirming first when doing so would
        discard an unsaved, unbound manuscript (whose only copy is the
        session-recovery file that opening overwrites)."""
        if self.doc_path is None and self._has_content():
            self._confirm(
                "Discard this manuscript?",
                "This manuscript has not been saved to a file. Opening another "
                "will discard it.",
                "Discard", lambda: self._do_open_path(path))
            return
        self._do_open_path(path)

    def _do_open_path(self, path):
        """Load a manuscript JSON file from disk and make it the active
        document (bound for subsequent File ▸ Save).

        Every app writes JSON into the same $NB_HOME/Documents folder, so the
        chosen file is validated as a manuscript BEFORE anything is mutated:
        opening another app's file must not replace the model, adopt the path,
        or trigger a recovery write. On any mismatch we flash and change
        nothing."""
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception:
            self._set_save_error("Open failed")
            return
        if not self._is_manuscript_document(data):
            self._set_save_error("Unrecognized file")
            return
        state = self._parse_state(data)
        if not state:
            self._set_save_error("Unrecognized file")
            return
        state["doc_path"] = path
        # Same reason as File ▸ New: opening replaces the manuscript AND the
        # recovery snapshot, so the book that was on screen must be recoverable.
        self.undo.checkpoint("Open Manuscript")
        self._restore(state)
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        self._save_state()
        self.undo.commit()

    def _is_manuscript_document(self, data):
        """True only when `data` carries this app's recognizable manuscript
        shape: a top-level dict whose non-empty "chapters" list holds at least
        one chapter object with a novel chapter's own fields (a "body", or a
        "num"/"title" pair). This rejects a foreign file that merely shares the
        generic "chapters" key, which _parse_state alone would tolerate."""
        if not isinstance(data, dict):
            return False
        chapters = data.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            return False
        for ch in chapters:
            if isinstance(ch, dict) and ("body" in ch
                                         or ("num" in ch and "title" in ch)):
                return True
        return False

    def _on_file_save(self):
        """File ▸ Save — write to the bound file, or fall through to Save As
        when the manuscript has never been written to a user file."""
        if not self.doc_path:
            self._on_file_save_as()
            return
        self._write_document(self.doc_path)

    def _on_file_save_as(self):
        """File ▸ Save As — Finder-style save picker under Documents. nbpicker
        handles the .json default + overwrite confirmation; _finish_save_as
        writes the document and binds it."""
        if self.doc_path:
            suggested = os.path.basename(self.doc_path)
        else:
            suggested = (self._title.strip() or _untitled())
        path = nbpicker.save_file(self, title="Save Manuscript As",
                                  start_dir=DOCS_DIR, suggested_name=suggested,
                                  patterns=("*.json",), default_ext=".json")
        if path:
            self._finish_save_as(path)

    def _commit_save_as(self, name):
        # Keep it a bare filename inside Documents (no path traversal); default
        # the extension so every manuscript reopens through File ▸ Open.
        name = os.path.basename(name.strip())
        if not name:
            return
        if not name.endswith(".json"):
            name += ".json"
        path = os.path.join(DOCS_DIR, name)
        # Confirm before clobbering a different existing file (re-saving the
        # bound file over itself needs no prompt).
        already = self.doc_path and os.path.abspath(path) == os.path.abspath(
            self.doc_path)
        if not already and os.path.exists(path):
            self._confirm(
                "Replace file?",
                "“%s” already exists in Documents. Replace it?" % name,
                "Replace", lambda: self._finish_save_as(path))
            return
        self._finish_save_as(path)

    def _finish_save_as(self, path):
        if self._write_document(path):
            self.doc_path = path
            self._save_state()

    def _write_document(self, path):
        """Serialize the whole model to `path` as JSON and reflect the outcome
        in the format-bar save indicator. Never raises. True on success."""
        try:
            nbapp.atomic_write_json(path, self._serialize())
        except Exception:
            self._set_save_error("Not saved")
            return False
        self.save_lbl.set_markup(
            '<span foreground="#7FA98C">● </span>Saved %s'
            % time.strftime("%H:%M"))
        return True

    def _set_save_error(self, msg):
        self.save_lbl.set_markup(
            '<span foreground="#C8341E">● </span>%s' % msg)

    # ======================== BOOK LAYOUT / PDF ========================
    # The manuscript is imposed onto a 5.5x8.5in (396x612pt) page. ONE
    # draw_page(cr, page_no, w, h) renderer backs BOTH publish routes, so the
    # PDF you export is byte-for-byte the page the zine imposition folds — the
    # renderer just replays a pre-computed page model built by _paginate().

    @staticmethod
    def _rgb(hex_):
        h = hex_.lstrip("#")
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)

    @staticmethod
    def _esc(s):
        """Escape text for Pango markup (used by the PDF layout)."""
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _range_markup(self, buf, s_it, e_it):
        """Pango markup for the buffer text in [s_it, e_it) with inline
        bold/italic/underline preserved, so exported prose keeps its emphasis.
        Walks tag toggles (not char-by-char) so it stays cheap on a long book."""
        tbl = buf.get_tag_table()
        tb, ti, tu = (tbl.lookup("bold"), tbl.lookup("italic"),
                      tbl.lookup("underline"))
        out = []
        it = s_it.copy()
        while it.compare(e_it) < 0:
            b = bool(tb and it.has_tag(tb))
            i = bool(ti and it.has_tag(ti))
            u = bool(tu and it.has_tag(tu))
            nxt = it.copy()
            if not nxt.forward_to_tag_toggle(None) or nxt.compare(e_it) > 0:
                nxt = e_it.copy()
            seg = self._esc(buf.get_text(it, nxt, True))
            if u:
                seg = "<u>%s</u>" % seg
            if i:
                seg = "<i>%s</i>" % seg
            if b:
                seg = "<b>%s</b>" % seg
            out.append(seg)
            it = nxt
        return "".join(out)

    def _chapter_paras(self, ch):
        """The chapter's body as a list of (style, markup) paragraphs — every
        line below the heading line, tagged 'quote'/'subhead'/'body'. The
        heading line itself is excluded; it is rendered as the chapter opener."""
        buf = ch["buffer"]
        tbl = buf.get_tag_table()
        thead, tquote = tbl.lookup("heading"), tbl.lookup("quote")
        paras = []
        n = buf.get_line_count()
        for ln in range(1, n):            # skip line 0 (the chapter heading)
            ls = buf.get_iter_at_line(ln)
            le = ls.copy()
            le.forward_to_line_end()
            markup = self._range_markup(buf, ls, le)
            if not markup.strip():
                paras.append(("blank", ""))
                continue
            if tquote and ls.has_tag(tquote):
                style = "quote"
            elif thead and ls.has_tag(thead):
                style = "subhead"
            else:
                style = "body"
            paras.append((style, markup))
        return paras

    def _measure_ctx(self):
        """A throwaway cairo context for measuring text during pagination —
        a RecordingSurface needs no file and no printer."""
        import cairo
        surf = cairo.RecordingSurface(cairo.Content.COLOR_ALPHA, None)
        return cairo.Context(surf)

    def _mk_layout(self, cr, markup, font, width, align=None):
        """Build a Pango layout identically for measuring and drawing, so page
        breaks computed off a scratch context match what is finally drawn."""
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(font))
        if width:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        if align is not None:
            layout.set_alignment(align)
        layout.set_markup(markup, -1)
        return layout

    @staticmethod
    def _layout_lines(layout):
        """Per-line (top, bottom) y in pixels, for splitting a paragraph across
        pages without disturbing its inline markup."""
        it = layout.get_iter()
        rows = []
        while True:
            y0, y1 = it.get_line_yrange()
            rows.append((y0 / Pango.SCALE, y1 / Pango.SCALE))
            if not it.next_line():
                break
        return rows

    def _para_font(self, style):
        if style == "quote":
            return F_QUOTE
        if style == "subhead":
            return F_SUBHEAD
        return F_BODY

    def _paginate(self):
        """Impose the whole manuscript onto ordered 5.5x8.5 pages and return
        (pages, chapter_start). `pages` is a list of page dicts, each with an
        'items' draw-list and a 'folio' flag; `chapter_start` maps chapter index
        -> its 1-based book page. Front matter (title page + Table of Contents)
        precedes the chapters, and the ToC page numbers are the real book pages
        each chapter opens on, so Export == Zine and the ToC stays in sync."""
        mcr = self._measure_ctx()
        pages = []

        def new_page(folio=True):
            pg = {"folio": folio, "items": []}
            pages.append(pg)
            return pg

        # cursor state for the flowing body
        state = {"pg": None, "y": BODY_TOP}

        def flow_new_page():
            state["pg"] = new_page(True)
            state["y"] = BODY_TOP

        def place_para(style, markup):
            if style == "blank":
                state["y"] += PARA_GAP + 5
                return
            font = self._para_font(style)
            if style == "quote":
                x, w = MARGIN_X + 16, COL_W - 16
                markup = '<span foreground="%s">%s</span>' % (C_SEC, markup)
            else:
                x, w = MARGIN_X, COL_W
            layout = self._mk_layout(mcr, markup, font, w)
            rows = self._layout_lines(layout)
            start = 0
            while start < len(rows):
                base = rows[start][0]
                avail = BODY_BOT - state["y"]
                last = start
                while (last + 1 < len(rows)
                       and (rows[last + 1][1] - base) <= avail):
                    last += 1
                frag_h = rows[last][1] - base
                # If not even the first line fits on a partially-used page, spill
                # to a fresh one (on a fresh page it always places, avoiding a
                # loop). Then continue the same paragraph there.
                if frag_h > avail and state["y"] > BODY_TOP:
                    flow_new_page()
                    continue
                state["pg"]["items"].append(
                    ("frag", markup, font, x, state["y"], w, base, frag_h))
                state["y"] += frag_h + PARA_GAP
                start = last + 1
                if start < len(rows):
                    flow_new_page()

        # --- 1. title page -------------------------------------------------
        tp = new_page(folio=False)
        title = (self._title.strip() or _untitled())
        tlayout = self._mk_layout(mcr, self._esc(title), F_TITLE,
                                  PAGE_W - 80, Pango.Alignment.CENTER)
        _tw, th = tlayout.get_pixel_size()
        ty = max(120, (PAGE_H - th) // 2 - 30)
        tp["items"].append(
            ("text", self._esc(title), F_TITLE, 40, ty, PAGE_W - 80,
             "c", C_INK))
        tp["items"].append(
            ("rule", (PAGE_W - 60) / 2.0, ty + th + 20, 60, 2, C_RED))

        # --- 2. Table of Contents (reserve the pages; fill after body) -----
        show_parts = self._parts_visible()
        toc_rows = []                     # (kind, text, chapter_index_or_part)
        last_part = None
        for ci, ch in enumerate(self.chapters):
            pi = ch.get("part", 0)
            if show_parts and pi != last_part:
                toc_rows.append(("part", self._part_label(pi), pi))
                last_part = pi
            title_txt = (ch.get("title", "").strip()
                         or ("Chapter " + str(ch["num"])))
            toc_rows.append(("chapter", title_txt, ci))

        # lay ToC rows out into page slots (positions only; numbers fill later)
        toc_layout = []                   # list of pages, each [(row, y), ...]
        cur, y = [], BODY_TOP + 44        # first ToC page carries the heading
        for row in toc_rows:
            rh = 30 if row[0] == "part" else 22
            if y + rh > BODY_BOT:
                toc_layout.append(cur)
                cur, y = [], BODY_TOP
            cur.append((row, y))
            y += rh
        toc_layout.append(cur)
        toc_start = len(pages)            # index of first reserved ToC page
        for _ in toc_layout:
            new_page(True)

        # --- 3. body chapters (each opens on a fresh page) -----------------
        chapter_start = {}
        for ci, ch in enumerate(self.chapters):
            flow_new_page()
            chapter_start[ci] = len(pages)          # 1-based book page
            pg = state["pg"]
            top = CH_OPEN_TOP
            if show_parts:
                pg["items"].append(
                    ("text", self._esc(self._part_label(ch.get("part", 0))),
                     F_TOCPART, MARGIN_X, top, COL_W, "l", C_MUT))
                top += 15
            pg["items"].append(
                ("text", "CHAPTER " + str(ch["num"]), F_CHNUM, MARGIN_X,
                 top, COL_W, "l", C_SEC))
            top += 18
            ctitle = (ch.get("title", "").strip()
                      or ("Chapter " + str(ch["num"])))
            clay = self._mk_layout(mcr, self._esc(ctitle), F_CHTITLE, COL_W)
            _cw, chh = clay.get_pixel_size()
            pg["items"].append(
                ("frag", self._esc(ctitle), F_CHTITLE, MARGIN_X, top, COL_W,
                 0, chh))
            top += chh + 12
            pg["items"].append(("rule", MARGIN_X, top, 40, 2, C_RED))
            top += 24
            state["y"] = top
            for style, markup in self._chapter_paras(ch):
                place_para(style, markup)

        # --- 4. fill the reserved ToC pages now chapter pages are known ----
        for pnum, rowpage in enumerate(toc_layout):
            pg = pages[toc_start + pnum]
            if pnum == 0:
                pg["items"].append(
                    ("text", "Contents", F_TOCHEAD, MARGIN_X, BODY_TOP,
                     COL_W, "l", C_INK))
                pg["items"].append(
                    ("rule", MARGIN_X, BODY_TOP + 34, 40, 2, C_RED))
            for row, y in rowpage:
                kind, text, ref = row
                if kind == "part":
                    pg["items"].append(
                        ("text", self._esc(text), F_TOCPART, MARGIN_X, y + 6,
                         COL_W, "l", C_MUT))
                else:
                    pg["items"].append(
                        ("text", self._esc(text), F_TOCROW, MARGIN_X, y,
                         COL_W - 34, "l", C_INK))
                    num = chapter_start.get(ref)
                    if num:
                        pg["items"].append(
                            ("text", str(num), F_TOCROW, PAGE_W - MARGIN_X - 30,
                             y, 30, "r", C_SEC))

        # --- 5. folios (absolute page numbers, title page excepted) --------
        for idx, pg in enumerate(pages):
            if pg.get("folio"):
                pg["items"].append(
                    ("text", str(idx + 1), F_FOLIO, 0, PAGE_H - 40, PAGE_W,
                     "c", C_MUT))
        return pages, chapter_start

    def _draw_page(self, cr, page_no, w, h):
        """Render one imposed 5.5x8.5 book page. Backs BOTH publish routes.
        Always fills an OPAQUE papertone ground first (no-compositor safe)."""
        cr.save()
        cr.set_source_rgb(*self._rgb(C_BG))
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.restore()
        pages = self._render_pages
        if not pages or not (1 <= page_no <= len(pages)):
            return
        aligns = {"l": Pango.Alignment.LEFT, "c": Pango.Alignment.CENTER,
                  "r": Pango.Alignment.RIGHT}
        for item in pages[page_no - 1]["items"]:
            kind = item[0]
            if kind == "frag":
                _, markup, font, x, y_top, width, src_y0, clip_h = item
                layout = self._mk_layout(cr, markup, font, width)
                cr.save()
                cr.rectangle(x, y_top, width, clip_h)
                cr.clip()
                cr.set_source_rgb(*self._rgb(C_INK))
                cr.move_to(x, y_top - src_y0)
                PangoCairo.show_layout(cr, layout)
                cr.restore()
            elif kind == "text":
                _, text, font, x, y, width, align, color = item
                layout = self._mk_layout(cr, text, font, width,
                                         aligns.get(align, Pango.Alignment.LEFT))
                cr.save()
                cr.set_source_rgb(*self._rgb(color))
                cr.move_to(x, y)
                PangoCairo.show_layout(cr, layout)
                cr.restore()
            elif kind == "rule":
                _, x, y, ww, th, color = item
                cr.save()
                cr.set_source_rgb(*self._rgb(color))
                cr.rectangle(x, y, ww, th)
                cr.fill()
                cr.restore()

    def _prepare_render(self):
        """(Re)build the shared page model and cache it for _draw_page. Returns
        the book page count. Used by both publish routes and status."""
        pages, chapter_start = self._paginate()
        self._render_pages = pages
        self._chapter_pages = chapter_start
        self._page_count = len(pages)
        return len(pages)

    def _refresh_pagestat(self):
        """Recompute the page total / chapter start pages and refresh labels.
        Never raises — a layout hiccup must not disturb the editor. Records what
        the layout cost so _arm_pagestat can keep it out of the writer's way."""
        try:
            t0 = time.time()
            self._prepare_render()
            self._paginate_ms = (time.time() - t0) * 1000.0
        except Exception:
            pass
        try:
            self._recount()
        except Exception:
            pass

    # ---- publish route (a): Export to PDF -----------------------------
    def _on_export_pdf(self):
        """File ▸ Export to PDF — write the whole book at 5.5x8.5 as plain
        sequential pages (title, Contents, chapters) to a shareable PDF."""
        self._close_style()
        self._close_prompt()
        initial = (self._title.strip() or _untitled())
        self._prompt_text("Export to PDF", initial, "Filename", "Export",
                          self._commit_export_pdf)

    def _commit_export_pdf(self, name):
        name = os.path.basename(name.strip())
        if not name:
            return
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        path = os.path.join(DOCS_DIR, name)
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            count = self._prepare_render()
            nbprint.simple_pdf(path, count, self._draw_page, PAGE_W, PAGE_H)
        except Exception:
            self._set_save_error("Export failed")
            return
        self.save_lbl.set_markup(
            '<span foreground="#7FA98C">● </span>Exported %s'
            % time.strftime("%H:%M"))

    # ---- publish route (b): Zine Print (saddle-stitch booklet) --------
    def _on_zine_print(self):
        """File ▸ Zine Print — impose the same book 2-up on letter sheets in
        saddle-stitch folding order and send it to the printer (duplex)."""
        self._close_style()
        self._close_prompt()
        try:
            self._prepare_render()
        except Exception:
            self._set_save_error("Layout failed")
            return
        count = self._page_count

        def make_pdf(path):
            return nbprint.booklet_pdf(path, count, self._draw_page)
        nbprint.print_booklet(self, make_pdf, "Novel")

    # ============================ FORMATTING ============================
    def _on_fmt(self, _btn, cmd):
        buf = self.view.get_buffer()
        bounds = buf.get_selection_bounds()
        if cmd in ("bold", "italic", "underline"):
            if not bounds:
                return
            start, end = bounds
            self.undo.checkpoint("Formatting")
            if self._has_tag(buf, cmd, start, end):
                buf.remove_tag_by_name(cmd, start, end)
            else:
                buf.apply_tag_by_name(cmd, start, end)
            # Tag edits emit no "changed" signal, so nothing else would record
            # them - and the next typing checkpoint would silently absorb them.
            self._trigger_save()
            self.undo.commit()
        elif cmd == "quote":
            # A real block-quote toggle on the current paragraph (matching the
            # design's formatBlock and Format ▸ Quote), not a stray "“" glyph.
            cur = self._current_para_style(buf)
            self._apply_para_style("Body" if cur == "Quote" else "Quote")
            return  # _apply_para_style already refocuses + persists
        elif cmd == "bullet":
            self._insert_prefix(buf, "• ")
        self.view.grab_focus()

    def _has_tag(self, buf, name, start, end):
        tag = buf.get_tag_table().lookup(name)
        it = start.copy()
        while it.compare(end) < 0:
            if not it.has_tag(tag):
                return False
            it.forward_char()
        return True

    def _insert_prefix(self, buf, text):
        it = buf.get_iter_at_mark(buf.get_insert())
        it.set_line_offset(0)
        buf.insert(it, text)

    # ---- paragraph style (Body / Heading / Quote) ----
    def _para_bounds(self, buf):
        """Iters bounding EXACTLY the current paragraph: line start → start of
        the next line (matching the seeded-heading range in _make_buffer)."""
        it = buf.get_iter_at_mark(buf.get_insert())
        ls = it.copy(); ls.set_line_offset(0)
        le = it.copy(); le.set_line_offset(0)
        if not le.forward_line():
            le = buf.get_end_iter()
        return ls, le

    def _current_para_style(self, buf):
        """Name of the current paragraph's style: Heading, Quote, or Body."""
        ls, le = self._para_bounds(buf)
        if ls.compare(le) < 0:
            if self._has_tag(buf, "heading", ls, le):
                return "Heading"
            if self._has_tag(buf, "quote", ls, le):
                return "Quote"
        return "Body"

    def _apply_para_style(self, style):
        """Apply Body / Heading / Quote to the current paragraph (the two are
        mutually exclusive; Body clears both) and reflect it in the pill."""
        buf = self.view.get_buffer()
        ls, le = self._para_bounds(buf)
        self.undo.checkpoint("Style")
        buf.remove_tag_by_name("heading", ls, le)
        buf.remove_tag_by_name("quote", ls, le)
        if style == "Heading":
            buf.apply_tag_by_name("heading", ls, le)
        elif style == "Quote":
            buf.apply_tag_by_name("quote", ls, le)
        self._set_style_pill(style)
        # Tag edits don't emit "changed", so persist the style change ourselves.
        self._trigger_save()
        self.undo.commit()
        self.view.grab_focus()

    def _set_style_pill(self, style):
        self.style_lbl.set_text(style)

    def _update_style_pill(self):
        try:
            self._set_style_pill(self._current_para_style(self.view.get_buffer()))
        except Exception:
            self._set_style_pill("Body")

    def _on_cursor_moved(self, _buf, _pspec):
        self._update_style_pill()

    def _place_cursor_body(self, buf):
        """Drop the caret at the start of the body (line 1) when a chapter is
        activated, so the pill reads 'Body' and the writer can start typing."""
        try:
            it = (buf.get_iter_at_line(1) if buf.get_line_count() > 1
                  else buf.get_end_iter())
            buf.place_cursor(it)
        except Exception:
            pass

    def _on_style(self, btn):
        """Open the Body / Heading / Quote paragraph-style dropdown beneath the
        pill. Drawn as an in-window overlay layer (no popup window) per the
        no-compositor idiom shared with nbapp's menus."""
        self._close_style()
        if btn is None:
            btn = self.stylebtn
        W, H = self._live_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_style(), True)[1])
        layer.put(scrim, 0, 0)

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        menu.get_style_context().add_class("nvstylemenu")
        current = self._current_para_style(self.view.get_buffer())
        for style in ("Body", "Heading", "Quote"):
            item = Gtk.Button()
            item.set_relief(Gtk.ReliefStyle.NONE)
            item.get_style_context().add_class("nvstyleitem")
            if style == current:
                item.get_style_context().add_class("active")
            lab = Gtk.Label(label=style, xalign=0)
            lab.get_style_context().add_class("nvstyleitemlab")
            item.add(lab)
            item.connect("clicked", self._on_style_pick, style)
            menu.pack_start(item, False, False, 0)

        try:
            xy = btn.translate_coordinates(self._overlay, 0, 0)
            a = btn.get_allocation()
            bx, by = max(xy[0], 0), xy[1] + a.height + 2
        except Exception:
            bx, by = 190, 92
        menu_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        menu_win.add(menu)
        layer.put(menu_win, bx, by)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._style_layer = layer

    def _on_style_pick(self, _btn, style):
        self._close_style()
        self._apply_para_style(style)

    def _close_style(self):
        layer = getattr(self, "_style_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._style_layer = None
            return True
        return False

    # ---- manuscript title: the model, and the one place it is written ----
    def _set_title(self, text):
        """Record the manuscript title and show it, EXACTLY as typed.

        nbi18n.set_verbatim, not label.set_text: the auto-translate layer
        rewrites any label text that happens to be a catalog key, and this
        label is the manuscript's name — 'Notes' came back 'Notas' in Spanish,
        'Contents' came back 'Indice', and because _serialize read the title
        out of the widget the rewrite was saved to the file and used as the
        Save As filename. The default IS localised (see _untitled), so it is
        passed in already translated."""
        self._title = text if isinstance(text, str) else str(text or "")
        nbi18n.set_verbatim(self.title_lbl, self._title)

    # ---- manuscript title: rename ----
    def _rename_manuscript(self):
        self._prompt_text("Rename manuscript", self._title,
                          "Manuscript title", "Save",
                          self._commit_manuscript_name)

    def _commit_manuscript_name(self, name):
        # Empty falls back to the neutral default so the header is never blank.
        name = name.strip()
        self.undo.checkpoint("Rename manuscript")
        self._set_title(name if name else _untitled())
        self._save_state()
        self.undo.commit()

    # ---- parts: add / rename ----
    def _on_new_part(self):
        self._prompt_text("Name the new part", "", "Part name", "Save",
                          self._commit_new_part)

    def _commit_new_part(self, name):
        self.undo.checkpoint("New Part…")
        self.parts.append({"name": name})
        pi = len(self.parts) - 1
        self._new_chapter(select=True, part=pi)
        self._refresh_chapter_list()
        self._recount()
        self._save_state()
        self.undo.commit()

    def _rename_part(self, pi):
        if not (0 <= pi < len(self.parts)):
            return
        self._prompt_text("Name this part", self.parts[pi]["name"],
                          "Part name", "Save",
                          lambda name: self._commit_part_name(pi, name))

    def _commit_part_name(self, pi, name):
        if 0 <= pi < len(self.parts):
            self.undo.checkpoint("Name this part")
            self.parts[pi]["name"] = name
            self._refresh_chapter_list()
            self._recount()
            self._save_state()
            self.undo.commit()

    # ---- chapters / parts: delete ----
    def _on_delete_chapter(self):
        """Delete the active chapter after a confirm. The last remaining chapter
        is guarded (the menu item is already disabled at one chapter) so the
        manuscript always holds at least one."""
        if len(self.chapters) <= 1:
            return
        idx = self.active
        ch = self.chapters[idx]
        title = ch.get("title", "").strip() or ("Chapter " + str(ch["num"]))
        self._confirm(
            "Delete chapter?",
            # Undoable now, and the confirm says so — the old wording sent a
            # writer looking for a backup that does not exist.
            _t("Delete “%s”? Its text will be removed.") % title,
            "Delete", lambda: self._delete_chapter(idx))

    def _delete_chapter(self, i):
        """Remove chapter `i`, drop its words from the running total, keep the
        active selection valid, and re-sequence the remaining chapter numbers.
        One chapter is always kept so the editor never holds an empty model."""
        if len(self.chapters) <= 1 or not (0 <= i < len(self.chapters)):
            return
        self.undo.checkpoint("Delete Chapter…")
        self._total_words -= self.chapters[i].get("wc", 0)
        # Hold the removed chapter until the editor has been re-pointed: if it
        # was the one on screen, dropping it here frees the buffer the TextView
        # is still displaying (see _show_buffer).
        removed = self.chapters[i]                       # noqa: F841
        del self.chapters[i]
        # Keep the active index pointing at a surviving chapter: shift down when
        # a chapter before it went, then clamp if the deleted one was last.
        if self.active > i:
            self.active -= 1
        if self.active >= len(self.chapters):
            self.active = len(self.chapters) - 1
        self._renumber_chapters()
        self._show_buffer(self.chapters[self.active]["buffer"])
        self._place_cursor_body(self.chapters[self.active]["buffer"])
        self._refresh_chapter_list()
        self._recount()
        self._save_state()
        self.undo.commit()
        del removed

    def _renumber_chapters(self):
        """Re-sequence chapter numbers to their 1..N positions after a delete.
        A chapter whose heading is still the untouched default ('Chapter <old>')
        is rewritten to track its new number; a writer-set chapter title is
        preserved — only its ordinal badge moves."""
        for i, ch in enumerate(self.chapters):
            new = str(i + 1)
            old = ch["num"]
            if old == new:
                continue
            was_default = ch.get("title", "") == "Chapter " + old
            ch["num"] = new
            if was_default:
                self._rewrite_heading(ch, "Chapter " + new)

    def _rewrite_heading(self, ch, text):
        """Replace a chapter buffer's first (heading) line with `text`, keeping
        the heading tag and the body below it intact. The 'changed' handler is
        suppressed so this rewrite of a possibly NON-active buffer is never
        mis-attributed to the active chapter (see _on_change)."""
        buf = ch["buffer"]
        self._suppress_change = True
        try:
            s = buf.get_iter_at_line(0)
            e = s.copy()
            e.forward_to_line_end()
            buf.delete(s, e)
            s = buf.get_iter_at_line(0)
            buf.insert(s, text)
            hs = buf.get_iter_at_line(0)
            he = (buf.get_iter_at_line(1) if buf.get_line_count() > 1
                  else buf.get_end_iter())
            buf.apply_tag_by_name("heading", hs, he)
        finally:
            self._suppress_change = False
        ch["title"] = text
        # A default heading carries no body words, so wc / the running total are
        # unchanged; only the title/badge move (reflected by _refresh_chapter_list).

    def _on_delete_part(self):
        """Delete the part that contains the active chapter after a confirm.
        Its chapters are reassigned to a neighbouring part (as Cookbook moves a
        removed category's recipes) — no chapters are deleted — and the sole
        part is guarded so the model always keeps one."""
        if len(self.parts) <= 1:
            return
        pi = self.chapters[self.active].get("part", 0)
        if not (0 <= pi < len(self.parts)):
            return
        n = sum(1 for c in self.chapters if c.get("part", 0) == pi)
        target = pi - 1 if pi > 0 else 1
        msg = ("Delete %s? Its %d chapter%s will move to %s; no chapters are "
               "deleted." % (self._part_label(pi), n, "" if n == 1 else "s",
                             self._part_label(target)))
        self._confirm("Delete part?", msg, "Delete",
                      lambda: self._remove_part(pi))

    def _remove_part(self, pi):
        """Delete part index `pi`, reassigning its chapters to the neighbouring
        part so nothing is lost, then compact the remaining part indices."""
        if not (0 <= pi < len(self.parts)) or len(self.parts) <= 1:
            return
        self.undo.checkpoint("Delete Part…")
        target = pi - 1 if pi > 0 else 1
        for c in self.chapters:
            cp = c.get("part", 0)
            if cp == pi:
                cp = target
            if cp > pi:
                cp -= 1
            c["part"] = cp
        del self.parts[pi]
        self._refresh_chapter_list()
        self._recount()
        self._save_state()
        self.undo.commit()

    def _prompt_text(self, title, initial, placeholder, ok_label, on_ok):
        """A small in-window text-entry card (naming a part, a Save As filename,
        …). `on_ok` is called with the trimmed entry text; cancel/scrim/Esc
        dismiss it."""
        self._close_style()
        self._close_prompt()
        W, H = self._live_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_prompt(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("nvprompt")
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("nvprompttitle")
        card.pack_start(head, False, False, 0)

        entry = Gtk.Entry()
        entry.get_style_context().add_class("nvpromptentry")
        entry.set_text(initial)
        entry.set_width_chars(26)
        entry.set_placeholder_text(placeholder)
        card.pack_start(entry, False, False, 0)

        def _commit(*_a):
            val = entry.get_text().strip()
            self._close_prompt()
            on_ok(val)

        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("nvpromptcancel")
        cancel.connect("clicked", lambda *_: self._close_prompt())
        ok = Gtk.Button(label=ok_label)
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("nvpromptok")
        ok.connect("clicked", _commit)
        entry.connect("activate", _commit)
        btnrow.pack_start(cancel, False, False, 0)
        btnrow.pack_start(ok, False, False, 0)
        card.pack_start(btnrow, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._center_card(layer, card_win, W, H)
        entry.grab_focus()
        self._prompt_layer = layer

    def _close_prompt(self):
        layer = getattr(self, "_prompt_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._prompt_layer = None
            return True
        return False

    def _confirm(self, title, message, ok_label, on_ok):
        """A small in-window confirmation card for a destructive action.
        `on_ok` runs only when the user accepts; cancel / scrim / Esc dismiss
        it and change nothing. Shares _prompt_text's overlay idiom and the
        _prompt_layer / _close_prompt lifecycle (no popup window)."""
        self._close_style()
        self._close_prompt()
        W, H = self._live_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_prompt(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("nvprompt")
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("nvprompttitle")
        card.pack_start(head, False, False, 0)

        msg = Gtk.Label(label=message, xalign=0)
        msg.get_style_context().add_class("nvpromptmsg")
        msg.set_line_wrap(True)
        # width_chars as well as max: the card is only as wide as its widest
        # child, so with a maximum alone the message wrapped at the width of
        # the button row (~24 characters) into a tall, cramped column.
        msg.set_width_chars(34)
        msg.set_max_width_chars(34)
        card.pack_start(msg, False, False, 0)

        def _accept(*_a):
            self._close_prompt()
            on_ok()

        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("nvpromptcancel")
        cancel.connect("clicked", lambda *_: self._close_prompt())
        ok = Gtk.Button(label=ok_label)
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("nvpromptok")
        ok.get_style_context().add_class("danger")
        ok.connect("clicked", _accept)
        btnrow.pack_start(cancel, False, False, 0)
        btnrow.pack_start(ok, False, False, 0)
        card.pack_start(btnrow, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._center_card(layer, card_win, W, H)
        # Rest focus on Cancel: a destructive card must never let a stray
        # Space/Enter fire the destructive button by default.
        cancel.grab_focus()
        self._prompt_layer = layer

    def _on_key(self, w, ev):
        # Esc dismisses an open style dropdown / part prompt before falling
        # through to the base handling (menu close / quit).
        if ev.keyval == Gdk.KEY_Escape:
            if self._close_prompt():
                return True
            if self._close_style():
                return True
            if self._findbar_w.get_visible():
                self._toggle_find(False)
                return True
            return super()._on_key(w, ev)
        # Ctrl+S save, Ctrl+Shift+S save as, Ctrl+O open, Ctrl+N new, and
        # Ctrl+B/I/U inline formatting — the shortcuts a writer reaches for
        # (mirroring writer/screenplay/sequencer). Skipped while a text-entry
        # card is open so its own field keeps its keys; the format toggles are
        # no-ops without a selection, so they simply do nothing when nothing is
        # selected.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and getattr(self, "_prompt_layer", None) is None):
            shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
            if ev.keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self._on_file_save_as() if shift else self._on_file_save()
                return True
            if ev.keyval in (Gdk.KEY_o, Gdk.KEY_O) and not shift:
                self._on_file_open()
                return True
            if ev.keyval in (Gdk.KEY_n, Gdk.KEY_N) and not shift:
                self._on_file_new()
                return True
            if ev.keyval in (Gdk.KEY_f, Gdk.KEY_F) and not shift:
                self._toggle_find(True)
                return True
            # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y, handled at the window level so
            # they work from the chapter sidebar as well as the editor.
            if nbapp.undo_keys(self.undo, ev):
                return True
            if ev.keyval in (Gdk.KEY_b, Gdk.KEY_B):
                self._on_fmt(None, "bold")
                return True
            if ev.keyval in (Gdk.KEY_i, Gdk.KEY_I):
                self._on_fmt(None, "italic")
                return True
            if ev.keyval in (Gdk.KEY_u, Gdk.KEY_U):
                self._on_fmt(None, "underline")
                return True
        return super()._on_key(w, ev)

    # ============================ HELPERS ============================
    def _live_size(self):
        """The live window size for sizing full-screen scrims / centring cards —
        the real allocation, falling back to the true panel size, never a
        hardcoded 1920x1080 that would overflow a smaller screen."""
        alloc = self.get_allocation()
        sw, sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else sw
        H = alloc.height if alloc.height > 1 else sh
        return W, H

    def _center_card(self, layer, card_win, W, H):
        """Center an overlay card on the live window using its measured natural
        size, so it stays centred at any resolution."""
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 400
        ch = nat.height if nat.height > 1 else 260
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))

    def _sep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("nvsep")
        return s

    # ============================ CSS ============================
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        .nvside { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        /* The Viewport that GTK inserts inside a ScrolledWindow paints its own
           background OVER .nvside, so the chapter list came out paper-white
           below the sidebar's beige header. Name the viewport node itself. */
        .nvsidescroll, .nvsidescroll viewport { background: #F1EEE6; }
        .nvhead { padding: 26px 26px 22px; border-bottom: 1px solid #D7D2C5; }
        .nveyebrow { font-size: 11px; letter-spacing: 2px; color: #A39D8F;
                     font-weight: 700; margin-bottom: 10px; }
        .nvtitle { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 25px; color: #1A1916; margin-bottom: 14px; }
        .nvtitlebtn { border-radius: 2px; }
        .nvtitlebtn:hover { background: #E6DFCE; }
        .nvstatrow { margin-bottom: 2px; }
        .nvtotal { font-size: 13px; color: #6E695E; }

        .nvchaplist { padding: 18px 14px; }
        .nvpart { font-size: 11px; letter-spacing: 1.7px; color: #A39D8F;
                  font-weight: 700; padding: 0 10px; margin: 6px 0 10px; }
        .nvparthdr { margin: 4px 0 2px; }
        .nvparthdr:hover { background: #E6DFCE; border-radius: 2px; }
        .nvrow { padding: 11px 10px; border-radius: 2px; margin-bottom: 2px;
                 border-left: 3px solid transparent; }
        .nvrowhit:hover { background: #E6DFCE; border-radius: 2px; }
        .nvrow.active { background: #EAE3D2; border-left: 3px solid #C8341E; }
        .nvnum { font-size: 13px; color: #9A958A; border: 1px solid #D7D2C5;
                 border-radius: 50%; }
        /* Active chapter marker: a soft warm disc (the OS's selected-control
           tone), not a black slab. The row already signals "active" with its
           red edge + beige fill, so the number just firms up in ink on a gently
           deeper disc rather than inverting to black-on-white. */
        .nvnum.active { background: #E0D8C4; color: #1A1916; font-weight: 600;
                        border: 1px solid #B3AD9E; }
        .nvrowtitle { font-size: 15px; color: #1A1916; font-weight: 500; }
        .nvrowwords { font-size: 12px; color: #9A958A; margin-top: 2px; }

        .nvfoot { border-top: 1px solid #D7D2C5; padding: 14px 18px; }
        .nvnewbtn { min-height: 40px; border: 1px solid #C4BFB1;
                    border-radius: 2px; background: #FCFBF8; color: #2A2620;
                    box-shadow: none; }
        .nvnewbtn:hover { background: #ECE8DD; }
        .nvnewbtn label { font-size: 15px; font-weight: 500; color: #2A2620; }

        .nvformatbar { min-height: 54px; padding: 0 36px; background: #FCFBF8;
                       border-bottom: 1px solid #E6E1D4; }
        .nvstylebtn { min-height: 34px; padding: 0 13px;
                      border: 1px solid #DCD7C9; border-radius: 2px;
                      background: #FCFBF8; box-shadow: none; }
        .nvstylebtn:hover { background: #F1EEE6; }
        .nvstylelab { font-family: "Newsreader","Liberation Serif",serif;
                      font-size: 16px; color: #1A1916; }
        .nvcaret { font-size: 11px; color: #8A857A; }

        /* paragraph-style dropdown (in-window overlay, no popup window) */
        .nvstylemenu { background: #FCFBF8; border: 1px solid #C9C4B6;
                       padding: 4px 0; min-width: 156px; }
        .nvstyleitem { padding: 7px 22px 7px 16px; background: transparent;
                       border: none; box-shadow: none; border-radius: 0; }
        .nvstyleitem:hover { background: #EAE3D2; }
        .nvstyleitemlab { font-family: "Newsreader","Liberation Serif",serif;
                          font-size: 16px; color: #1A1916; }
        .nvstyleitem.active .nvstyleitemlab { color: #C8341E; }
        .nvstyleitem:hover .nvstyleitemlab { color: #1A1916; }

        /* part name-entry card */
        .nvprompt { background: #FCFBF8; border: 1px solid #C9C4B6;
                    padding: 26px 30px; }
        .nvprompttitle { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 20px; color: #1A1916; }
        .nvpromptmsg { font-size: 14px; color: #6E695E; }
        .nvpromptentry { font-size: 15px; padding: 7px 10px; color: #1A1916;
                         border: 1px solid #C4BFB1; border-radius: 2px;
                         background: #FCFBF8; }
        .nvpromptentry:focus { border: 1px solid #1A1916; }
        /* The label needs its OWN colour rule. The theme's `* { color: ink }`
           lands directly on the button's label node, and a direct declaration
           beats a colour inherited from the button, so setting paper on the
           button alone painted ink-on-ink: every confirm/prompt card's primary
           button (Delete, Save, Export) was a black slab with no visible text. */
        .nvpromptok, .nvpromptok label { color: #FCFBF8; font-size: 14px;
                      font-weight: 600; }
        .nvpromptok { min-height: 34px; padding: 0 18px; border: 1px solid #1A1916;
                      border-radius: 2px; background: #1A1916;
                      box-shadow: none; }
        .nvpromptok:hover { background: #2A2620; }
        /* Destructive variant. Ink is the primary action everywhere in this app
           (Save, Export, Rename), so deleting a chapter or a part must not look
           identical to saving one: the design system reserves signage red for
           exactly this, and Journal's Delete Entry already uses it. Only
           _confirm() adds .danger; _prompt_text stays ink. */
        .nvpromptok.danger { background: #C8341E; border-color: #C8341E; }
        .nvpromptok.danger:hover { background: #A62A17; border-color: #A62A17; }
        .nvpromptcancel { min-height: 34px; padding: 0 16px; color: #2A2620;
                          border: 1px solid #C4BFB1; border-radius: 2px;
                          background: #FCFBF8; box-shadow: none; font-size: 14px; }
        .nvpromptcancel:hover { background: #ECE8DD; }
        .nvpromptempty { font-size: 13px; color: #A39D8F; }
        .nvfileitem { padding: 8px 12px; background: transparent; border: none;
                      box-shadow: none; border-radius: 2px; }
        .nvfileitem:hover { background: #EAE3D2; }
        .nvfileitemlab { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 15px; color: #1A1916; }
        .nvfileitem:hover .nvfileitemlab { color: #1A1916; }
        .nvfmtbtn { min-width: 34px; min-height: 34px; padding: 0; border: none;
                    border-radius: 2px; background: transparent;
                    box-shadow: none; color: #2A2620; font-size: 17px; }
        .nvfmtbtn:hover { background: #EFEBE0; }
        .nvfmtbtn.bold { font-weight: 700; }
        .nvfmtbtn.ital { font-style: italic;
                         font-family: "Newsreader","Liberation Serif",serif; }
        .nvfmtbtn.under { text-decoration-line: underline; font-size: 16px; }
        .nvsep { color: #DCD7C9; min-width: 1px; }
        .nvfindbar { background: #F4F2EC; border-bottom: 1px solid #E6E1D4;
                     padding: 8px 36px; }
        .nvfindentry { font-size: 13px; color: #1A1916; background: #FCFBF8;
                       border: 1px solid #C4BFB1; border-radius: 2px;
                       box-shadow: none; min-height: 30px; }
        .nvfindentry:focus { border: 1px solid #8A857A; }
        .nvfindbtn { min-height: 30px; padding: 0 12px; font-size: 13px;
                     color: #2A2620; background: #FCFBF8;
                     border: 1px solid #D7D2C5; border-radius: 2px;
                     box-shadow: none; }
        .nvfindbtn:hover { background: #ECE8DD; }
        .nvfindcount { font-size: 13px; color: #6E695E; }
        .nvcount { font-size: 13px; color: #8A857A; }
        .nvsave { font-size: 13px; color: #8A857A; }

        .nvcanvas { background: #FCFBF8; }
        .nvpage { padding: 80px 24px 160px; }
        .nvcaneyebrow { font-size: 12px; letter-spacing: 2px; color: #A39D8F;
                        font-weight: 700; margin-bottom: 24px; }
        .nvbody { font-family: "Newsreader","Liberation Serif",serif;
                  font-size: 20px; color: #2A2620; background: #FCFBF8;
                  caret-color: #C8341E; }
        .nvbody text { background: #FCFBF8; }
        .nvbody text selection { background-color: #F1D9D2; color: #1A1916; }
        .nvplaceholder { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 20px; font-style: italic; color: #A39D8F; }
        """
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
    nbapp.run(Novel)
