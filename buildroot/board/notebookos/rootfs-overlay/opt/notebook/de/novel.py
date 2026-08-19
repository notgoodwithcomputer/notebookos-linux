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
import re
import json
import time
import copy

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
TOC_NUMERAL_W = 34    # the Contents' numeral column
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
MAX_MANUSCRIPT_BYTES = 64 * 1024 * 1024

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
# The author on the cover: the title's face, much smaller, so the two
# read as one piece of setting rather than two unrelated labels.
F_AUTHOR = _SERIF + " 13"
F_TOCHEAD = _SERIF + " 20"
F_TOCROW = _SERIF + " 11.5"
F_TOCPART = _SANS + " 8.5"
F_FOLIO = _SANS + " 8.5"

# Canonical papertone hexes used by the PDF renderer.
# The PRINTED page's palette — used only by the page model and _draw_page,
# which back Export to PDF and Zine Print and nothing on screen.
#
# Every value here is NEUTRAL on purpose. These pages exist to come out of a
# printer, and a warm off-white or a warm near-black is not free: a colour
# printer reproduces #FCFBF8 and #1A1916 as composites, laying down cyan,
# magenta and yellow on every sheet to make what should be bare paper and plain
# black text. The page ground is now the paper itself, the ink is black, and the
# two greys are true greys, so a page costs black only.
#
# The screen's own papertone and signage red are unaffected — they are written
# literally in the CSS and the save chip, not taken from here.
C_BG = "#FFFFFF"       # bare paper: no full-bleed wash on every sheet
C_INK = "#000000"
C_SEC = "#555555"
C_MUT = "#777777"
# Was signage red (#C8341E). The rules under the title and each chapter heading
# were the only colour on the page, and a decorative bar is the last thing worth
# opening a colour cartridge for.
C_RULE = "#000000"


def read_manuscript_json(path, limit=MAX_MANUSCRIPT_BYTES):
    """Decode a selected manuscript without an unbounded UI-thread read."""
    with open(path, "rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("manuscript is too large")
    return json.loads(raw.decode("utf-8-sig"))

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
NOVEL_FORMAT_VERSION = 2


def _count_body_words(text):
    """Count prose only. Chapter titles live outside the body in format 2.

    A token is a word only when it carries a letter or a digit. The editor puts
    marks of its OWN into the buffer — Insert ▸ Bullet List writes a literal
    "• " at the head of the line, and the smart-typography pass turns a typed
    "--" into a standalone " — " (see _on_insert_before) — so a plain split
    counted the app's own glyphs as words the writer never wrote: "one — two"
    read as three words, and every bullet added one more to the chapter and to
    the manuscript total."""
    return sum(1 for token in text.split()
               if any(c.isalnum() for c in token))


def placeholder_offsets(left_margin, top_margin, body_ascent, ghost_ascent):
    """Return the ghost label origin which shares the body's first baseline.

    Inputs are integer Pango pixel metrics, which keeps the layout equation
    headless-testable while the widget adapter below supplies the real fonts.
    """
    return (int(left_margin),
            int(top_margin) + int(body_ascent) - int(ghost_ascent))


def roman(n):
    """1 -> "I", 4 -> "IV", 40 -> "XL". Chapter headings and the Contents both
    call this, so the numeral beside a chapter is the same in each place.
    Falls back to the plain number outside the range it can express, which is
    better than a heading that says nothing."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if not 1 <= n < 4000:
        return str(n)
    out = []
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
                       (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                       (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


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
        # A pending "put the caret back on screen" idle (see _keep_caret_in_view).
        self._caret_scroll_idle = None
        self._closed = False
        # Does the model on screen still differ from the copy in the
        # session-recovery file? Set the moment an edit is made and cleared
        # only by a write that actually reached the disk, so the close guard
        # below can tell "everything is durable" from "the last save failed and
        # this window is the only place that work exists". _save_error keeps
        # the exception behind a failure so the guard can say why.
        self._recovery_dirty = False
        self._save_error = None
        # Set once the user has accepted a close that cannot be saved, so the
        # final flush honours what the button promised and the guard does not
        # ask twice.
        self._discarded = False
        # A damaged/unrecognized recovery store is moved aside intact and this
        # session may inspect a blank model, but must never replace those bytes.
        self._store_read_only = False
        # The confirm card the close guard is currently showing, if any. Held
        # so a second close attempt re-uses the open card instead of stacking
        # another one on top of it.
        self._closeprompt = None
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
        self._find_chapters = 0    # chapters the current hits are spread over
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

        # Flush the final edit when the window goes away so nothing is lost —
        # and, one step earlier, refuse to go away at all while the last save
        # is still refused by the disk (see _on_delete).
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)
        if not saved and not self._store_read_only:
            # First run: persist the seed so the "Saved" state is truthful and
            # the empty manuscript reopens instead of being silently re-seeded.
            self._save_state()
        elif self._store_read_only:
            # THE BLANK BOOK NEEDS EXPLAINING. A manuscript that could not be
            # read leaves this window showing a seeded Chapter 1 — which looks
            # exactly like a new book, and is the most alarming thing this app
            # can show someone who had a book here yesterday. The bytes were
            # kept and the session refuses to write over them, but neither of
            # those facts is visible: the only signal was "Not saved" appearing
            # after the first sentence they typed.
            #
            # Deferred to idle because __init__ is still building the window
            # the card has to sit inside.
            GLib.idle_add(self._say_store_unreadable)
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
                    ("Author…", self._on_set_author),
                    nbapp.SEP,
                    ("Export to PDF…", self._on_export_pdf),
                    ("Zine Print…", self._on_zine_print),
                    nbapp.SEP,
                    ("New Chapter", lambda: self._on_new_chapter()),
                    ("New Part…", lambda: self._on_new_part()),
                    # Guarded so one chapter / one part always remains: a lone
                    # chapter or the sole part renders the item disabled.
                    # No ellipsis: both act at once and are reversed from
                    # Edit ▸ Undo (the OS-wide decision that retired the
                    # confirmation here). An ellipsis promises a question that
                    # is no longer asked — see docs/MENU-CONVENTIONS.md rule 1.
                    ("Delete Chapter",
                     (lambda: self._on_delete_chapter())
                     if len(self.chapters) > 1 else None),
                    ("Delete Part",
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
        title_ev = Gtk.Button()
        title_ev.set_relief(Gtk.ReliefStyle.NONE)
        title_ev.get_style_context().add_class("nvflatbtn")
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
        # Whose book it is, printed under the title on the cover.
        # Empty until set, and an empty author prints nothing at
        # all rather than a blank line or a placeholder name.
        self._author = ""
        title_ev.add(title)
        title_ev.connect("clicked", lambda *_a: self._rename_manuscript())
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
            nbicons.image("plus", 16, "#2A2620"),
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
            b.add(nbicons.image(icon, 19, "#2A2620"))
            b.connect("clicked", self._on_fmt, cmd)
            fbar.pack_start(b, False, False, 2)

        # right cluster: word count + save state
        self.save_lbl = Gtk.Label()
        self.save_lbl.get_style_context().add_class("nvsave")
        # A session that will never write (the store could not be read and was
        # kept aside) must not open under a green "Saved": nothing has been
        # saved and nothing will be.
        self._show_save_state(not getattr(self, "_store_read_only", False))
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

        # The title is manuscript content in its own control, rather than a
        # specially-tagged first body line. It occupies the old opening-heading
        # position and uses the same 32pt medium serif letterpress treatment.
        self.chapter_title = Gtk.Entry()
        self.chapter_title.get_style_context().add_class("nvchaptertitle")
        self.chapter_title.set_has_frame(False)
        self.chapter_title.set_placeholder_text(_t("Chapter title"))
        self.chapter_title.connect("changed", self._on_title_change)
        # The one other place in the canvas that takes the keyboard. The body
        # follows its caret (see _keep_caret_in_view); the title sits at the top
        # of the page, so bringing the top back is what showing it means.
        # notify::is-focus, not focus-in-event: the second only arrives once the
        # WINDOW is the active one, so a title reached while another window had
        # focus would be typed into off-screen.
        self.chapter_title.connect(
            "notify::is-focus",
            lambda entry, _p: entry.is_focus() and self._show_page_top())
        page.pack_start(self.chapter_title, False, False, 0)

        self.view = Gtk.TextView()
        # WORD_CHAR, not WORD: a word longer than the writing column (a pasted
        # URL, a long compound) cannot break under WORD, so it runs off the
        # column AND raises the TextView's minimum width — with a 78-character
        # word in the prose the whole window's minimum grew past a 1024px
        # panel, putting the save state out of reach.
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.get_style_context().add_class("nvbody")
        self.view.set_left_margin(2)
        self.view.set_right_margin(2)
        self.view.set_top_margin(0)
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
        self._sync_placeholder_position()
        self.placeholder.set_no_show_all(True)
        overlay.add_overlay(self.placeholder)
        if hasattr(overlay, "set_overlay_pass_through"):
            overlay.set_overlay_pass_through(self.placeholder, True)
        page.pack_start(overlay, True, True, 0)

        scroll.add(page)
        self._canvas = scroll
        # Track the viewport (not the scroller — its width includes the
        # scrollbar) so the column always matches the space actually available.
        vp = scroll.get_child()
        if vp is not None:
            vp.connect("size-allocate", self._fit_page)
            # THE PAGE MUST NOT MOVE BECAUSE SOMETHING TOOK FOCUS. The writing
            # surface is a page-tall widget inside this viewport, so GTK's
            # focus-vadjustment clamped its top to the top of the canvas the
            # instant the writer clicked into the body: the chapter eyebrow and
            # title scrolled out of sight before a single word was typed, and
            # stayed hidden for the whole of writing. Point that clamp at an
            # adjustment nothing is watching (GTK refuses None here), and let
            # _keep_caret_in_view below do the only scrolling this canvas needs.
            vp.set_focus_vadjustment(Gtk.Adjustment())
        col.pack_start(scroll, True, True, 0)
        return col

    def _show_page_top(self):
        """Put the top of the page — eyebrow, chapter title — back on screen."""
        scroll = getattr(self, "_canvas", None)
        if scroll is not None:
            scroll.get_vadjustment().set_value(0)
        return False

    # Breathing room kept above/below the caret when the canvas follows it.
    CARET_PAD = 24

    def _keep_caret_in_view(self):
        """Follow the caret, once the layout it will be measured against has
        settled.

        Deferred to idle for the same reason the caret placement above is: the
        text that moved the caret has not been laid out yet when the cursor
        signal arrives, so the canvas would be sized and clamped against the
        page as it was BEFORE the edit — measurable as a paste that lands the
        caret off the bottom of the screen. Coalesced: a burst of typing asks
        many times and scrolls once."""
        if self._closed or self._caret_scroll_idle is not None:
            return
        self._caret_scroll_idle = GLib.idle_add(self._scroll_to_caret)

    def _scroll_to_caret(self):
        """Scroll the canvas the smallest amount that keeps the caret visible.

        The TextView is NOT the scrolled window's own child — the page box
        (eyebrow, chapter title, body) is — so the view is always allocated its
        full height and its own scroll_to_mark moves an adjustment nobody is
        looking at. Nothing followed the caret: past the first screenful the
        writer typed into a line they could not see, and a find hit deep in a
        chapter was selected off-screen. Drive the CANVAS adjustment from the
        caret's position instead, and only when the caret has actually left the
        visible band, so ordinary typing never jumps the page."""
        self._caret_scroll_idle = None
        if self._closed:
            return False
        scroll = getattr(self, "_canvas", None)
        if scroll is None or not self.view.get_realized():
            return False
        adj = scroll.get_vadjustment()
        visible = adj.get_page_size()
        if visible <= 0:
            return False
        buf = self.view.get_buffer()
        try:
            rect = self.view.get_iter_location(
                buf.get_iter_at_mark(buf.get_insert()))
            _wx, wy = self.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x, rect.y)
            here = self.view.translate_coordinates(self.page, 0, wy)
        except Exception:                                     # noqa: BLE001
            return False
        if here is None:
            return False
        top = here[1] - self.CARET_PAD
        bottom = here[1] + rect.height + self.CARET_PAD
        value = adj.get_value()
        if bottom > value + visible:
            value = bottom - visible
        if top < value:
            value = top
        value = max(adj.get_lower(),
                    min(value,
                        max(adj.get_lower(), adj.get_upper() - visible)))
        # Compare against the value actually on the adjustment rather than
        # trusting a flag: set_value re-enters GTK's layout, and a scroll that
        # is already where it wants to be must not ask for another one.
        if abs(value - adj.get_value()) > 0.5:
            adj.set_value(value)
        return False

    def _sync_placeholder_position(self):
        """Align the ghost prompt's baseline with the first body character.

        THE FONT MUST COME FROM THE PANGO CONTEXT, NEVER FROM THE STYLE
        CONTEXT. `style_context.get_property("font", state)` hands back a
        PangoFontDescription that is not safe to touch on the stack the image
        ships: on the guest's Pango 1.50 merely calling .to_string() on it --
        let alone passing it to get_metrics() -- is an immediate
        `Segmentation fault`, no traceback, no window. The host builds against
        1.56, where the same call is harmless, so EVERY host-side gate stayed
        green while Novel would not open at all on the real machine. Measured
        on target: the style-context description crashes, and
        `pango_context.get_font_description()` returns "Sans 10" and metrics
        of 10240 quite happily. The Pango context is also the font GTK will
        actually render with, so this is the more correct source anyway.

        The language argument is explicit for the same family of reason: 1.50
        carries no (nullable) annotation on it, so a None becomes a NULL the C
        function dereferences."""
        context = self.view.get_pango_context()
        ghost_context = self.placeholder.get_pango_context()
        body_font = context.get_font_description()
        ghost_font = ghost_context.get_font_description()
        if body_font is None or ghost_font is None:
            return                      # no resolved font yet: nothing to align
        lang = context.get_language() or Pango.Language.get_default()
        body = context.get_metrics(body_font, lang)
        ghost = ghost_context.get_metrics(ghost_font, lang)
        scale = Pango.SCALE
        left, top = placeholder_offsets(
            self.view.get_left_margin(), self.view.get_top_margin(),
            (body.get_ascent() + scale - 1) // scale,
            (ghost.get_ascent() + scale - 1) // scale)
        self.placeholder.set_margin_start(left)
        self.placeholder.set_margin_top(top)

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
        # How many chapters the hits are spread over, for the count label. It
        # used to be written straight into that label here and then overwritten
        # by _find_step's "k of n" inside this very call, so the writer never
        # saw it; _find_step now carries it.
        self._find_chapters = chapters
        if not n:
            self.find_count.set_text(_t("No matches"))
            return
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
        label = _t("%d of %d") % (self._find_i + 1, len(self._find_hits))
        if getattr(self, "_find_chapters", 0) > 1:
            label += " · " + _t("in %d chapters") % self._find_chapters
        self.find_count.set_text(label)

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
        # Body paragraphs may still use the editor's Heading style; this tag is
        # no longer special-cased onto line one.
        h = Gtk.TextTag(name="heading")
        h.set_property("size-points", 32)
        h.set_property("weight", Pango.Weight.MEDIUM)
        h.set_property("pixels-below-lines", 22)
        tt.add(h)
        # Block-quote paragraph style: indented, italic serif, muted ink.
        q = Gtk.TextTag(name="quote")
        q.set_property("style", Pango.Style.ITALIC)
        q.set_property("left-margin", 26)
        q.set_property("foreground", "#6E695E")
        tt.add(q)
        for name, prop, val in (("bold", "weight", Pango.Weight.BOLD),
                                ("italic", "style", Pango.Style.ITALIC),
                                ("underline", "underline",
                                 Pango.Underline.SINGLE)):
            t = Gtk.TextTag(name=name)
            t.set_property(prop, val)
            tt.add(t)
        # Seed the prose buffer. The chapter title is a separate field. This
        # insert runs BEFORE connecting "changed" so creating/loading a chapter
        # never triggers a spurious autosave.
        if body is None:
            body = ""
        buf.insert(buf.get_start_iter(), body)
        if ranges is not None:
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
            self._show_chapter_title(self._display_chapter_title(ch))
            self._show_buffer(buf)
            self._place_cursor_body(buf)

    def _show_chapter_title(self, text):
        self._suppress_title_change = True
        try:
            nbi18n.set_verbatim(self.chapter_title, text)
        finally:
            self._suppress_title_change = False

    def _on_title_change(self, entry):
        """Bind the dedicated title field directly to the active chapter."""
        if getattr(self, "_suppress_title_change", False) or not self.chapters:
            return
        ch = self.chapters[self.active]
        ch["title"] = entry.get_text()
        label = ch.get("_row_title")
        if label is not None:
            # The same fallback the entry placeholder, the eyebrow, the
            # Contents and the printed opener use: clearing a title left the
            # sidebar row blank until something rebuilt the list, and only then
            # did it read "Chapter 3".
            nbi18n.set_verbatim(label, self._display_chapter_title(ch))
        # Same debounced UndoHistory checkpoint used by prose typing.
        self._trigger_save()

    def _on_new_chapter(self):
        self.undo.checkpoint("New Chapter")
        self._new_chapter(select=True)
        self._refresh_chapter_list()
        self._recount()
        # A blank chapter is still authored structure.  Mark it recoverable
        # immediately; otherwise closing before typing relied on the
        # irreversible destroy-time flush and a failed write lost the chapter
        # without ever engaging the close veto.
        self._trigger_save()
        self.undo.commit()

    def _select_chapter(self, i):
        if i == self.active:
            return
        self.active = i
        self._show_chapter_title(self._display_chapter_title(self.chapters[i]))
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
        prefix = (_t("Part %s") % (pi + 1)).upper()
        name = self.parts[pi]["name"].strip() if 0 <= pi < len(self.parts) else ""
        return prefix + " — " + name if name else prefix

    def _display_chapter_title(self, chapter):
        """Translate only the generated default; user titles stay verbatim."""
        num = str(chapter.get("num", ""))
        title = str(chapter.get("title", "") or "").strip()
        if not title or title == "Chapter " + num:
            return _t("Chapter %s") % num
        return title

    def _part_header(self, pi):
        ev = Gtk.Button()
        ev.set_relief(Gtk.ReliefStyle.NONE)
        ev.get_style_context().add_class("nvflatbtn")
        ev.get_style_context().add_class("nvparthdr")
        ev.set_tooltip_text(_t("Rename part"))
        lab = Gtk.Label(label=self._part_label(pi), xalign=0)
        lab.get_style_context().add_class("nvpart")
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        ev.add(lab)
        ev.connect("clicked", lambda *_a, idx=pi: self._rename_part(idx))
        return ev

    def _chapter_row(self, i, ch):
        act = (i == self.active)
        ev = Gtk.Button()
        ev.set_relief(Gtk.ReliefStyle.NONE)
        ev.get_style_context().add_class("nvflatbtn")
        ev.get_style_context().add_class("nvrowhit")
        ev.set_tooltip_text(_t("Open chapter %s: %s") %
                            (ch["num"], self._display_chapter_title(ch)))
        ev.connect("clicked", lambda *_a, idx=i: self._select_chapter(idx))
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
        # Every REBUILD comes through here — launch, Open, New Chapter,
        # Delete, restore — and rebuilds this row from the constructor, which
        # the set_verbatim on the typing path (_on_title_change) never sees.
        # A chapter called "Notes" was right while it was being typed and wrong
        # the moment the list was rebuilt.
        t = Gtk.Label(xalign=0)
        nbi18n.set_verbatim(t, self._display_chapter_title(ch))
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
        return _count_body_words(txt)

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
            eb = (_t("Chapter %s") % ch["num"]).upper() + " · " + \
                self._part_label(ch.get("part", 0))
        else:
            eb = (_t("Chapter %s") % ch["num"]).upper()
        startp = self._chapter_pages.get(self.active)
        if startp and total:          # see the page-count note above
            eb += " · " + (_t("Page %d") % startp).upper()
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
        ch = self.chapters[self.active]
        # Recount ONLY the active chapter and adjust the running total by the
        # delta — no re-summing of every chapter on each keystroke.
        new = self._count_buffer(buf)
        self._total_words += new - ch.get("wc", 0)
        ch["wc"] = new
        # Update THIS chapter's sidebar word count in place rather
        # than tearing down and rebuilding the whole chapter list. Structural
        # ops (new/select/part changes) still go through _refresh_chapter_list.
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
        # From here until a write succeeds, this window holds the only copy of
        # the edit. See _on_delete.
        self._recovery_dirty = True
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

    def _show_save_state(self, saved):
        """Put the manuscript's real save state in the chip.

        Every route that writes the recovery store says what happened through
        HERE, so the chip can never be left describing an older manuscript than
        the one on screen."""
        if saved:
            self.save_lbl.set_markup(
                '<span foreground="#7FA98C">● </span>Saved %s'
                % time.strftime("%H:%M"))
        else:
            self.save_lbl.set_markup(
                '<span foreground="#C8341E">● </span>Not saved')

    def _mark_saved(self):
        # The debounce has fired: perform the REAL disk write, and only claim
        # "Saved" once the bytes have actually reached the file.
        self._save_timer = None
        if self._closed:
            return False
        self._show_save_state(self._save_state())
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
        if self._closed:
            return False
        self._refresh_pagestat()
        return False

    # ============================ PERSISTENCE ============================
    def _buffer_text(self, buf):
        """Full prose text of a chapter buffer; the title is stored apart."""
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
                except (TypeError, ValueError, OverflowError, IndexError):
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
        damaged = nbapp.preserve_damaged(NOVEL_FILE)
        if damaged:
            self._store_read_only = True
            return None
        try:
            with open(NOVEL_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return None                  # missing / unreadable: nothing to lose
        state = self._parse_state(data)
        if state is None:
            nbapp.quarantine_unrecognized(NOVEL_FILE)
            self._store_read_only = True
        return state

    def _parse_state(self, data):
        """Validate a decoded manuscript document into a normalized state dict
        {title, author, parts, chapters:[{num,title,body,ranges,part}], active,
        doc_path}, or None when it is not a usable manuscript. Shared by the
        session-recovery loader and the File ▸ Open path."""
        if not isinstance(data, dict):
            return None
        version = data.get("format_version")
        if version is not None and version != NOVEL_FORMAT_VERSION:
            return None
        legacy = version is None
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
                    part = {k: copy.deepcopy(v) for k, v in p.items()
                            if k != "name"}
                    part["name"] = str(p.get("name", "")).strip()
                    parts.append(part)
        if not parts:
            parts = [{"name": ""}]
        chapters = []
        for ch in raw:
            if not isinstance(ch, dict):
                continue
            n = str(len(chapters) + 1)
            raw_ranges = ch.get("ranges")
            pt = ch.get("part", 0)
            # bool is an int subclass in Python, but JSON true is not a valid
            # part index. Accepting it silently filed the chapter under part 2
            # and the next autosave made that accidental move permanent.
            if isinstance(pt, bool) or not isinstance(pt, int) \
                    or not (0 <= pt < len(parts)):
                pt = 0
            title = str(ch.get("title", "Chapter " + n))
            body = str(ch.get("body", ""))
            ranges = raw_ranges if isinstance(raw_ranges, dict) else {}
            if legacy:
                body, ranges = Novel._migrate_legacy_body(title, body, ranges)
            known_chapter = {"num", "title", "body", "ranges", "part"}
            chapter = {k: copy.deepcopy(v) for k, v in ch.items()
                       if k not in known_chapter}
            chapter.update({
                "num": str(ch.get("num", n)),
                "title": title,
                "body": body,
                "ranges": ranges,
                "part": pt,
            })
            chapters.append(chapter)
        if not chapters:
            return None
        active = data.get("active", 0)
        # Likewise, JSON true must not mean chapter index 1.
        if isinstance(active, bool) or not isinstance(active, int) \
                or not (0 <= active < len(chapters)):
            active = 0
        dp = data.get("doc_path")
        if not isinstance(dp, str) or not dp:
            dp = None
        # The author has to be carried through HERE. _restore reads it off the
        # state dict this builds, so leaving it out did not merely fail to show
        # the name — it set self._author back to "" on every load, and the very
        # next autosave wrote that empty author over the stored one. A name set
        # through File ▸ Author… survived only until the app was closed, and the
        # copy on disk was gone one debounce later. (The undo path already
        # worked, because it restores a _serialize() dict directly.)
        au = data.get("author", "")
        known_top = {"format_version", "title", "author", "parts", "chapters",
                     "active", "doc_path"}
        return {"_extra": {k: copy.deepcopy(v) for k, v in data.items()
                            if k not in known_top},
                "title": str(data.get("title", _untitled())),
                "author": au if isinstance(au, str) else "",
                "parts": parts, "chapters": chapters, "active": active,
                "doc_path": dp, "format_version": NOVEL_FORMAT_VERSION}

    @staticmethod
    def _migrate_legacy_body(title, body, ranges):
        """Lift an old mirrored first line into the dedicated title field."""
        first, sep, _rest = body.partition("\n")
        if first != title:
            return body, dict(ranges)
        cut = len(first) + (1 if sep else 0)
        shifted = {}
        for name, spans in ranges.items():
            if not isinstance(spans, list):
                continue
            out = []
            for span in spans:
                try:
                    start, end = int(span[0]), int(span[1])
                except (TypeError, ValueError, OverflowError, IndexError):
                    continue
                start, end = max(start, cut) - cut, end - cut
                if end > start:
                    out.append([start, end])
            if out:
                shifted[name] = out
        return body[cut:], shifted

    def _serialize(self):
        """The full editable model as a JSON-serializable dict. Shared by the
        session-recovery writer and the File ▸ Save / Save As writers."""
        out = copy.deepcopy(getattr(self, "_extra", {}))
        out.update({
            "format_version": NOVEL_FORMAT_VERSION,
            "title": self._title,
            "author": self._author,
            "active": self.active,
            "doc_path": self.doc_path,
            "parts": [dict({k: copy.deepcopy(v) for k, v in p.items()
                            if k != "name"}, name=p.get("name", ""))
                      for p in self.parts],
            "chapters": [dict(
                          {k: copy.deepcopy(v) for k, v in c.items()
                           if k not in {"num", "title", "buffer", "part"}
                           and not k.startswith("_")},
                          num=c["num"], title=c["title"],
                          body=self._buffer_text(c["buffer"]),
                          ranges=self._buffer_ranges(c["buffer"]),
                          part=c.get("part", 0))
                         for c in self.chapters],
        })
        return out

    def _save_state(self):
        """Persist the whole editable model to the session-recovery file.
        Never raises — a failed write must not crash the editor. True on OK.

        The two flags are what make the close guard possible: until a write
        actually returns, the manuscript exists nowhere but this window, and
        _recovery_dirty is the only record of that. It is cleared HERE, on the
        write that reached the disk, and nowhere else."""
        if getattr(self, "_store_read_only", False):
            return False
        try:
            nbapp.atomic_write_json(NOVEL_FILE, self._serialize())
        except Exception as exc:
            self._save_error = exc
            self._recovery_dirty = True
            return False
        self._save_error = None
        self._recovery_dirty = False
        return True

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
        before = self._undo_snapshot()
        self._restore(state)
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        buf = self.view.get_buffer()
        caret = min(max(0, state.get("_caret", 0)), buf.get_char_count())
        if not self._save_state():
            error = self._save_error
            self._restore(before)
            self._init_counts()
            self._refresh_chapter_list()
            self._recount()
            old_buf = self.view.get_buffer()
            old_caret = min(max(0, before.get("_caret", 0)),
                            old_buf.get_char_count())
            self._place_caret_deferred(old_buf, old_caret)
            # Repair best-effort if a writer failed after publishing. Retain
            # the original failure flags: the manuscript is safe only when the
            # requested operation itself reached disk.
            self._save_state()
            self._save_error = error
            self._recovery_dirty = True
            self._arm_pagestat()
            self._focus_editor()
            return False
        self._place_caret_deferred(buf, caret)
        self._arm_pagestat()
        self._focus_editor()
        return True

    def _place_caret_deferred(self, buf, offset):
        """place_cursor, one idle later.

        Synchronous placement inside an undo/redo restore parked GTK in a
        place_cursor that never returned while the view was still
        re-anchoring the just-swapped buffer (independent stack samples at
        both call sites, all inside the C call; Ctrl+Y froze the app). By
        idle time the view has settled. The guard keeps a stale idle from
        poking a buffer the view no longer shows."""
        def later():
            self._caret_idle = None
            try:
                if buf is self.view.get_buffer():
                    off = min(max(0, int(offset)), buf.get_char_count())
                    buf.place_cursor(buf.get_iter_at_offset(off))
            except Exception:
                pass
            return False
        # Recorded and cancelled on close like every other source here. The
        # callback already guards against a buffer the view has moved on from,
        # so this was not a route to lost work — but "every source is recorded"
        # is the rule that makes the other two provable, and an exception this
        # small is how it stops being true.
        self._cancel_caret_idle()
        self._caret_idle = GLib.idle_add(later)

    def _cancel_caret_idle(self):
        tid = getattr(self, "_caret_idle", None)
        if tid:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
        self._caret_idle = None

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
        _a = state.get("author", "")
        self._author = _a if isinstance(_a, str) else ""
        self._extra = copy.deepcopy(state.get("_extra", {}))
        # Copied, not adopted: an undo snapshot is restored through here too, and
        # the live self.parts is later appended to and renamed in place — which
        # would edit the stored history out from under itself.
        self.parts = copy.deepcopy(state["parts"])
        self.doc_path = state.get("doc_path")
        for ch in state["chapters"]:
            buf = self._make_buffer(ch["num"], body=ch["body"],
                                    ranges=ch["ranges"])
            known = {"num", "title", "body", "ranges", "part"}
            live = {k: copy.deepcopy(v) for k, v in ch.items()
                    if k not in known}
            live.update({"num": ch["num"], "title": ch["title"],
                         "buffer": buf, "part": ch.get("part", 0)})
            self.chapters.append(live)
        self.active = (state["active"]
                       if state["active"] < len(self.chapters) else 0)
        self._show_buffer(self.chapters[self.active]["buffer"])
        self._show_chapter_title(
            self._display_chapter_title(self.chapters[self.active]))
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
        # A chapter opens at its opening: the eyebrow, the title and the first
        # words, not wherever the previous chapter happened to be scrolled to.
        self._show_page_top()

    def _on_delete(self, *_a):
        """Close guard: never destroy this window while it is the only place
        the manuscript exists.

        Every exit route — Esc, Ctrl+W, the red logo dot — goes through
        AppWindow.close(), which emits delete-event, so this one handler covers
        all of them. _on_destroy's final flush is not enough on its own: by the
        time destroy runs the window is already going away, so a flush that
        fails there has nowhere to report and nothing to stop. The failure that
        matters is a full or read-only disk, where every autosave since the
        last good write has been silently refused and only this window still
        holds the afternoon's work.

        Returning True vetoes the close and keeps the window (and the work) on
        screen; False lets it proceed."""
        if not self._recovery_dirty:
            return False              # already durable: close, no questions
        # One retry, silent when it works: a full disk is often a passing
        # condition, and a close that just saves is better than a card.
        if self._save_state():
            return False
        # The retry failed too. If the card is already up, leave it up rather
        # than building a second one over it — a repeated Esc must not stack
        # cards or re-enter this path.
        if (self._closeprompt is not None
                and self._closeprompt is getattr(self, "_prompt_layer", None)):
            return True
        if getattr(self, "_store_read_only", False):
            # NOT A DISK PROBLEM, so do not send the writer to clear space and
            # try again — this session deliberately refuses to write over the
            # manuscript it could not read, and closing again can never save.
            # Say the two things that are true instead.
            message = (_t("Your writing was kept, and nothing typed here will "
                          "be saved over it.")
                       + " " + _t("Closing now loses what was typed here."))
        else:
            message = (_t(nbapp.save_failure_reason(self._save_error,
                                                    NOVEL_FILE))
                       + " "
                       + _t("Closing now loses the writing since the last "
                            "save. Make room and close again to try once "
                            "more."))
        self._confirm(
            _t("Not saved"), message,
            _t("Close Without Saving"), self._discard_and_close)
        self._closeprompt = getattr(self, "_prompt_layer", None)
        return True

    def _say_store_unreadable(self):
        """Tell the writer why the book is blank, once, at open.

        Says what was kept and what this session will not do, and nothing
        else: the quarantine path is a dated name they cannot act on, and an
        errno is not a fact about their manuscript."""
        self._confirm(
            _t("This manuscript could not be read"),
            _t("Your writing was kept, and nothing typed here will be saved "
               "over it."),
            _t("Continue"), lambda: None, cancel=False, danger=False)
        return False

    def _discard_and_close(self):
        """The user accepted the loss. Destroy directly: destroy does not emit
        delete-event, so the guard cannot ask a second time."""
        self._discarded = True
        self._closeprompt = None
        self.destroy()

    def _on_destroy(self, *_):
        # Final flush on window close so the last (possibly still-debounced)
        # edit is written before we exit. Timers are removed HERE and not in
        # the guard above: a vetoed close leaves the window alive and still
        # typing, and an editor whose autosave timer had been cancelled out
        # from under it would stop saving entirely.
        if self._closed:
            return False
        self._closed = True
        self.undo.cancel()
        self._cancel_caret_idle()
        for attr in ("_save_timer", "_page_timer", "_caret_scroll_idle"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        # "Close Without Saving" said it would not save. Honour that rather
        # than quietly writing the work the user just chose to let go.
        if not self._discarded:
            self._save_state()
        return False

    # ============================ FILE MENU ============================
    def _on_file_new(self):
        """Start a blank manuscript as one full, reversible undo step."""
        self._close_style()
        self._close_prompt()
        self._do_file_new()

    def _keep_outgoing(self):
        """Put an unsaved, unbound manuscript somewhere it survives, and say so.

        THE HOLE THIS FILLS. New and Open replace the model AND the recovery
        store, and the campaign retired the "discard?" question in favour of
        undo (8ddfd945; confirm_undo_adversarial_selftest FORBIDS a confirm
        here). But undo only lives as long as the window: press New by
        mistake, close the app, and an afternoon's writing is gone with no
        question asked and nothing on disk to go back to. The drive found it
        (novel F3) and an independent verifier confirmed it as DATA LOSS.
        So the answer is not a question, it is a floor: write the outgoing
        book into Documents as a real manuscript, under its own title, and
        name the file on the way past. Undo still puts it back on screen; the
        file is what makes closing survivable.

        A BOUND MANUSCRIPT IS NOT AUTOMATICALLY SAFE. "It has a file, so it
        is already on disk" was wrong the moment the writer typed one more
        sentence after Save: File > New then replaced the model AND the
        recovery store, and the words written since the last save existed
        nowhere. So the test is not whether a file EXISTS, it is whether that
        file already holds what is on screen — asked by reading it back, not
        by trusting a flag that can drift.

        Only for a manuscript that holds something and is not already on disk
        byte for byte. Returns the basename kept, or None when there was
        nothing to keep."""
        if not self._has_content():
            return None
        if self.doc_path and not self._file_behind():
            return None
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            stem = (self._title or "").strip() or _untitled()
            stem = re.sub(r"[^\w \-.]", "", stem).strip() or "Manuscript"
            base = "%s %s" % (stem, time.strftime("%Y-%m-%d %H%M"))
            path = os.path.join(DOCS_DIR, base + ".json")
            n = 2
            while os.path.exists(path):
                path = os.path.join(DOCS_DIR, "%s (%d).json" % (base, n))
                n += 1
            nbapp.atomic_write_json(path, self._serialize())
            return os.path.basename(path)
        except Exception:                                         # noqa: BLE001
            # A floor that cannot be laid must not stop the action the person
            # asked for; undo still holds the book for this session.
            return None

    def _file_behind(self):
        """True when the bound file does not already hold what is on screen.

        Compared on the CONTENT the serializer writes, with the two view-state
        keys dropped: `active` is which chapter is selected and `doc_path` is
        where the file lives, and neither is anything a person would mourn.
        Any problem reading the file counts as behind — a floor errs toward
        keeping a copy, never toward assuming the disk is fine."""
        try:
            with open(self.doc_path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
        except Exception:                                         # noqa: BLE001
            return True
        if not isinstance(on_disk, dict):
            return True
        drop = ("active", "doc_path")
        live = {k: v for k, v in self._serialize().items() if k not in drop}
        kept = {k: v for k, v in on_disk.items() if k not in drop}
        return live != kept

    def _say_kept(self, kept):
        """Name the file the outgoing manuscript went into — where it will
        still be read later.

        NOT the save chip. Two reasons, both measured: the chip is rewritten
        by the very next autosave ("Kept as …" became "Saved 14:25" within a
        second), and — the stronger one — the chip describes THE MANUSCRIPT ON
        SCREEN. Putting the outgoing book's fate there recreates the exact
        confusion novel_realuse_selftest pins ("a new manuscript carries its
        own save state, not the last one's"), which is a fix worth keeping.
        The notification centre is the OS's channel for something that
        happened while attention was elsewhere, and it is the one that is
        still there when the writer looks up."""
        if not kept:
            return
        text = _t("Kept as %s in Documents") % kept
        try:
            import nbnotify                                       # noqa: PLC0415
            nbnotify.post(_t("Manuscript kept"), text,
                          app="novel", app_name=_t("Novel"))
        except Exception:                                         # noqa: BLE001
            pass

    def _do_file_new(self):
        """Blank the model and persist it, retaining the prior book in undo."""
        kept = self._keep_outgoing()
        self.undo.checkpoint("New Manuscript")
        self.doc_path = None
        self.parts = [{"name": ""}]
        self.chapters = []
        self._total_words = 0
        self.active = 0
        self._set_title(_untitled())
        self._new_chapter(select=True)
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        # Say what happened to THIS manuscript: the chip used to keep whatever
        # the last book left behind, so a brand-new blank one opened under an
        # "Exported 13:51" from the book that had just been replaced.
        self._show_save_state(self._save_state())
        self._say_kept(kept)
        self.undo.commit()

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
        for ch in self.chapters:
            title = ch.get("title", "").strip()
            if title and title != "Chapter " + str(ch["num"]):
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
        """Load a manuscript JSON file as one full, reversible undo step."""
        self._do_open_path(path)

    def _do_open_path(self, path, confirmed=False):
        """Load a manuscript JSON file from disk and make it the active
        document (bound for subsequent File ▸ Save).

        Every app writes JSON into the same $NB_HOME/Documents folder, so the
        chosen file is validated as a manuscript BEFORE anything is mutated:
        opening another app's file must not replace the model, adopt the path,
        or trigger a recovery write. On any mismatch we flash and change
        nothing."""
        try:
            data = read_manuscript_json(path)
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
        # recovery snapshot, so the book that was on screen must be
        # recoverable — on screen through undo, and after a close through the
        # file _keep_outgoing lays down.
        kept = self._keep_outgoing()
        self.undo.checkpoint("Open Manuscript")
        self._restore(state)
        self._init_counts()
        self._refresh_chapter_list()
        self._recount()
        self._show_save_state(self._save_state())
        self._say_kept(kept)
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
            # WHICH FILE THE MANUSCRIPT IS BOUND TO RIDES IN THE SNAPSHOT undo
            # restores (it has to — the recovery store has to remember the
            # binding across a restart), so re-binding without a checkpoint
            # folded it into whichever typing step happened to be open. One
            # Ctrl+Z, labelled "Undo Typing", then bound the book back to the
            # file it had been saved as BEFORE while leaving every visible word
            # alone, and the next Ctrl+S wrote over that older file — the one
            # the writer had just named stayed as it was. Its own step names
            # itself in the Edit menu, so the binding only ever moves when the
            # menu says it did.
            self.undo.checkpoint("Save As…")
            self.doc_path = path
            self._save_state()
            self.undo.commit()

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
        """The chapter's body as a list of (style, markup) paragraphs — EVERY
        line of the buffer, tagged 'quote'/'subhead'/'body'.

        Line 0 is prose like any other. It was skipped here, which was right in
        format 1, where the chapter heading WAS the buffer's first line. In
        format 2 the heading is a field of its own (self.chapter_title) and the
        buffer holds body text alone, so the skip silently dropped the opening
        paragraph of every chapter out of both publish routes — a chapter with
        one paragraph exported as a heading above an empty page. A legacy file
        has its mirrored first line lifted into the title field as it loads
        (_migrate_legacy_body), so nothing reaches here with a heading still in
        its body."""
        buf = ch["buffer"]
        tbl = buf.get_tag_table()
        thead, tquote = tbl.lookup("heading"), tbl.lookup("quote")
        paras = []
        n = buf.get_line_count()
        for ln in range(n):
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
        """A throwaway cairo context for measuring text during pagination.

        It MUST be a PDF surface, because that is what the pages are finally
        drawn on. Pagination measures each paragraph's line positions here and
        stores a clip window per fragment; _draw_page then re-lays the same
        paragraph out on the real PDF surface and clips to that window. If the
        two surfaces disagree about line height by even a fraction, the window
        lands on the wrong lines — text is sliced through the middle, some lines
        never appear, and the rest looks like it has overflowed the page.

        They DID disagree. This used to measure on a RecordingSurface, which
        takes cairo's default font options with metric hinting ON and so snaps
        each line to a whole pixel; a PDF surface turns metric hinting off and
        keeps fractions. Measured on the shipped book font, one line was 17.00pt
        while measuring and 15.50pt while drawing — over a 14-line paragraph the
        window was out by 21pt, most of a line. It went unnoticed because a
        one-line paragraph is 17 vs 15.5 and still looks fine.

        Measuring on the same KIND of surface makes the two identical by
        construction, rather than by keeping two sets of font options in step.
        The surface writes nowhere (PDFSurface accepts no filename) and the
        page size only has to match the real one so nothing wraps differently.
        """
        import cairo
        return cairo.Context(cairo.PDFSurface(None, PAGE_W, PAGE_H))

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
            ("rule", (PAGE_W - 60) / 2.0, ty + th + 20, 60, 2, C_RULE))
        # The author, under the rule. Nothing is drawn when no name is set —
        # an empty line or a stand-in name on a cover is worse than a cover
        # that simply carries the title.
        author = (self._author or "").strip()
        if author:
            tp["items"].append(
                ("text", self._esc(author), F_AUTHOR, 40, ty + th + 44,
                 PAGE_W - 80, "c", C_SEC))

        # --- 1b. the back of the cover, deliberately blank ------------------
        # Folded, page 1 is the front cover and page 2 is whatever is printed on
        # its reverse. Left to the flow, that reverse was the first page of the
        # book — so opening the cover landed straight in the text, and on a
        # short manuscript the imposition put the closing page there instead.
        # Every printed book leaves the cover's verso empty; so does this. It is
        # a real page in the model (not a special case at imposition time), so
        # Export to PDF and Zine Print stay identical, the count stays a
        # multiple the fold can use, and every page number below counts it.
        #
        # folio=False: a blank leaf carries no page number.
        new_page(folio=False)

        # --- 2. Table of Contents (reserve the pages; fill after body) -----
        show_parts = self._parts_visible()
        toc_rows = []                     # (kind, text, chapter_index_or_part)
        last_part = None
        for ci, ch in enumerate(self.chapters):
            pi = ch.get("part", 0)
            if show_parts and pi != last_part:
                toc_rows.append(("part", self._part_label(pi), pi))
                last_part = pi
            title_txt = self._display_chapter_title(ch)
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
            # Just the numeral. It used to read "CHAPTER 4" directly above a
            # heading that already says "Chapter 4", so the page said the same
            # thing twice in two type sizes.
            pg["items"].append(
                ("text", roman(ch["num"]), F_CHNUM, MARGIN_X,
                 top, COL_W, "l", C_SEC))
            top += 18
            ctitle = self._display_chapter_title(ch)
            clay = self._mk_layout(mcr, self._esc(ctitle), F_CHTITLE, COL_W)
            _cw, chh = clay.get_pixel_size()
            pg["items"].append(
                ("frag", self._esc(ctitle), F_CHTITLE, MARGIN_X, top, COL_W,
                 0, chh))
            top += chh + 12
            pg["items"].append(("rule", MARGIN_X, top, 40, 2, C_RULE))
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
                    ("rule", MARGIN_X, BODY_TOP + 34, 40, 2, C_RULE))
            for row, y in rowpage:
                kind, text, ref = row
                if kind == "part":
                    pg["items"].append(
                        ("text", self._esc(text), F_TOCPART, MARGIN_X, y + 6,
                         COL_W, "l", C_MUT))
                else:
                    # The chapter's numeral leads the row, so Contents and the
                    # chapter opener name the chapter the same way. Set in the
                    # quieter secondary ink and given a fixed column so the
                    # titles line up under each other whatever the numeral.
                    ci_ = ref
                    numeral = roman(self.chapters[ci_]["num"]) \
                        if 0 <= ci_ < len(self.chapters) else ""
                    pg["items"].append(
                        ("text", numeral, F_TOCROW, MARGIN_X, y,
                         TOC_NUMERAL_W, "l", C_SEC))
                    pg["items"].append(
                        ("text", self._esc(text), F_TOCROW,
                         MARGIN_X + TOC_NUMERAL_W, y,
                         COL_W - 34 - TOC_NUMERAL_W, "l", C_INK))
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
    def _on_set_author(self):
        """File ▸ Author… — the name printed under the title on the cover."""
        self._close_style()
        self._close_prompt()
        self._prompt_text("Author", self._author, "Name", "Set",
                          self._commit_author)

    def _commit_author(self, name):
        # Undo-able and persisted, exactly like naming a part.
        self.undo.checkpoint("Set the author")
        self._author = (name or "").strip()
        self._save_state()
        self.undo.commit()
        self._render_pages = None       # the cover changed; re-impose on demand

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
        # Exporting twice under one name is an ordinary thing to do — the name
        # defaults to the book's title, so the second export offers the same one
        # back. It used to destroy the first PDF without a word. Ask, using the same three strings as
        # Novel's Save As -- one wording for "you are about to overwrite",
        # already carried by all seventeen catalogs.
        if os.path.exists(path):
            self._confirm(
                _t("Replace file?"),
                _t("“%s” already exists in Documents. Replace it?")
                % name,
                _t("Replace"), lambda: self._write_export_pdf(path))
            return
        self._write_export_pdf(path)

    def _write_export_pdf(self, path):
        """Render the book to `path`. Split from _commit_export_pdf so the
        replace-an-existing-file question can be answered before anything is
        written."""
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            count = self._prepare_render()
            # Rendered beside the destination and moved into place only when
            # complete. Writing onto `path` meant a layout or cairo failure
            # part-way through had already truncated whatever was there — and
            # the reason to export a novel twice is that the novel changed, so
            # the casualty was yesterday's good PDF, seconds after the user
            # answered "Replace". Seven other apps have been fixed for this;
            # the shared primitive is the one they all use now.
            nbapp.atomic_write_via(
                path,
                lambda draft: nbprint.simple_pdf(
                    draft, count, self._draw_page, PAGE_W, PAGE_H))
        except Exception:
            self._set_save_error("Export failed")
            return
        # "Exported" replaces the save state in the one place that states it, so
        # it may only be said when the manuscript itself IS safe. A book whose
        # recovery store could not be written — a quarantined store, a full disk
        # — used to have its red "Not saved" replaced by a green "Exported" the
        # moment a PDF was written, which is the one moment the writer most
        # needs to be told the book is not saved.
        if (self._save_error is not None
                or getattr(self, "_store_read_only", False)):
            self._show_save_state(False)
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
            return nbprint.booklet_pdf(path, count, self._draw_page,
                                        fold_line=True)
        try:
            nbprint.print_booklet(self, make_pdf, "Novel")
        except Exception:
            self._set_save_error("Print failed")

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

    def _on_cursor_moved(self, buf, _pspec):
        self._update_style_pill()
        # Only the chapter on screen: a restore rebuilds every buffer, and a
        # caret settling in one the writer cannot see must not move the page.
        if buf is self.view.get_buffer():
            self._keep_caret_in_view()

    def _place_cursor_body(self, buf):
        """Drop the caret at the start of the body when a chapter is
        activated, so the pill reads 'Body' and the writer can start typing.

        Deferred via _place_caret_deferred — see its docstring for the
        freeze this dodges."""
        self._place_caret_deferred(buf, 0)

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
        items = []
        active_item = None
        for style in ("Body", "Heading", "Quote"):
            item = Gtk.Button()
            item.set_relief(Gtk.ReliefStyle.NONE)
            item.get_style_context().add_class("nvstyleitem")
            if style == current:
                item.get_style_context().add_class("active")
                active_item = item
            lab = Gtk.Label(label=style, xalign=0)
            lab.get_style_context().add_class("nvstyleitemlab")
            item.add(lab)
            item.connect("clicked", self._on_style_pick, style)
            item.connect("key-press-event", self._on_style_item_key)
            menu.pack_start(item, False, False, 0)
            items.append(item)

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
        self._style_items = items
        (active_item or items[0]).grab_focus()

    def _on_style_item_key(self, item, ev):
        items = list(getattr(self, "_style_items", []))
        if not items or item not in items:
            return False
        if ev.keyval == Gdk.KEY_ISO_Left_Tab:
            step = -1
        elif ev.keyval in (Gdk.KEY_Down, Gdk.KEY_Right, Gdk.KEY_Tab):
            step = -1 if (ev.keyval == Gdk.KEY_Tab and
                          ev.state & Gdk.ModifierType.SHIFT_MASK) else 1
        elif ev.keyval in (Gdk.KEY_Up, Gdk.KEY_Left):
            step = -1
        else:
            return False
        items[(items.index(item) + step) % len(items)].grab_focus()
        return True

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
            self._style_items = []
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
        """Delete the active chapter, reversibly. Edit ▸ Undo brings it and its
        words back; nothing is asked first (the OS-wide decision that retired
        this confirmation), which is why the menu item carries no ellipsis. The
        last remaining chapter is guarded (the menu item is already disabled at
        one chapter) so the manuscript always holds at least one."""
        if len(self.chapters) <= 1:
            return
        idx = self.active
        self._delete_chapter(idx, self.chapters[idx])

    def _delete_chapter(self, i, expected_chapter=None):
        """Remove chapter `i`, drop its words from the running total, keep the
        active selection valid, and re-sequence the remaining chapter numbers.
        One chapter is always kept so the editor never holds an empty model."""
        if (len(self.chapters) <= 1 or not (0 <= i < len(self.chapters))
                or (expected_chapter is not None
                    and self.chapters[i] is not expected_chapter)):
            return
        before = self._undo_snapshot()
        self.undo.checkpoint("Delete Chapter")
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
        if not self._save_state():
            # Renumbering may have changed surviving headings and their text
            # buffers, so restoring only the removed list item is insufficient.
            # Rebuild from the exact serialized manuscript held before delete.
            self._restore(before)
            self._init_counts()
            self._refresh_chapter_list()
            self._recount()
            self.undo.commit()  # clear the pending label; state is unchanged
            return
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
        """Rename an untouched default title without modifying body prose."""
        ch["title"] = text
        if ch is self.chapters[self.active]:
            self._show_chapter_title(text)

    def _on_delete_part(self):
        """Delete the part that contains the active chapter, reversibly (Edit ▸
        Undo restores it), which is why the menu item carries no ellipsis. Its
        chapters are reassigned to a neighbouring part (as Cookbook moves a
        removed category's recipes) — no chapters are deleted — and the sole
        part is guarded so the model always keeps one."""
        if len(self.parts) <= 1:
            return
        pi = self.chapters[self.active].get("part", 0)
        if not (0 <= pi < len(self.parts)):
            return
        self._remove_part(pi)

    def _remove_part(self, pi):
        """Delete part index `pi`, reassigning its chapters to the neighbouring
        part so nothing is lost, then compact the remaining part indices."""
        if not (0 <= pi < len(self.parts)) or len(self.parts) <= 1:
            return
        before = self._undo_snapshot()
        self.undo.checkpoint("Delete Part")
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
        if not self._save_state():
            self._restore(before)
            self._init_counts()
            self._refresh_chapter_list()
            self._recount()
            # The restored state equals the checkpoint, so this clears the
            # pending label without creating a phantom undo step.
            self.undo.commit()
            return
        self.undo.commit()

    def _prompt_text(self, title, initial, placeholder, ok_label, on_ok):
        """A small in-window text-entry card (naming a part, a Save As filename,
        …). `on_ok` is called with the trimmed entry text; cancel/scrim/Esc
        dismiss it."""
        return_focus = self.get_focus()
        self._close_style()
        self._close_prompt()
        self._prompt_return_focus = return_focus
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
            return_focus = getattr(self, "_prompt_return_focus", None)
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._prompt_layer = None
            self._prompt_return_focus = None
            # The prompt's Entry/Cancel button has just been removed. Without
            # restoring the widget that invoked it, GTK leaves no focus owner:
            # typing and shortcuts appear dead until the writer clicks back in
            # the manuscript. Restore after removal, and tolerate an invoker
            # that was itself replaced while the card was open.
            if return_focus is not None:
                try:
                    return_focus.grab_focus()
                except Exception:
                    pass
            return True
        return False

    def _confirm(self, title, message, ok_label, on_ok, cancel=True,
                 danger=True):
        """A small in-window confirmation card for a destructive action.
        `on_ok` runs only when the user accepts; cancel / scrim / Esc dismiss
        it and change nothing. Shares _prompt_text's overlay idiom and the
        _prompt_layer / _close_prompt lifecycle (no popup window).

        `cancel=False, danger=False` makes the same card an acknowledgement:
        one plain button that only dismisses it. A card that merely TELLS the
        writer something must not offer a choice between two buttons that do
        the same nothing, still less paint one of them the red of a
        destructive action."""
        return_focus = self.get_focus()
        self._close_style()
        self._close_prompt()
        self._prompt_return_focus = return_focus
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
        cancel_btn = None
        if cancel:
            cancel_btn = Gtk.Button(label=_t("Cancel"))
            cancel_btn.set_relief(Gtk.ReliefStyle.NONE)
            cancel_btn.get_style_context().add_class("nvpromptcancel")
            cancel_btn.connect("clicked", lambda *_: self._close_prompt())
            btnrow.pack_start(cancel_btn, False, False, 0)
        ok = Gtk.Button(label=ok_label)
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("nvpromptok")
        if danger:
            ok.get_style_context().add_class("danger")
        ok.connect("clicked", _accept)
        btnrow.pack_start(ok, False, False, 0)
        card.pack_start(btnrow, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._center_card(layer, card_win, W, H)
        # Rest focus on Cancel: a destructive card must never let a stray
        # Space/Enter fire the destructive button by default. An
        # acknowledgement has only the one button, which is safe to rest on.
        (cancel_btn or ok).grab_focus()
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
        .nveyebrow { font-size: 11px; letter-spacing: 2px; color: #6E695E;
                     font-weight: 700; margin-bottom: 10px; }
        .nvtitle { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 24px; color: #1A1916; margin-bottom: 14px; }
        .nvflatbtn { padding: 0; border: none; background: transparent;
                     background-image: none; box-shadow: none; }
        .nvtitlebtn { border-radius: 8px; }
        .nvtitlebtn:hover { background: #EAE3D2; }
        .nvstatrow { margin-bottom: 2px; }
        .nvtotal { font-size: 13px; color: #6E695E; }

        .nvchaplist { padding: 18px 14px; }
        .nvpart { font-size: 11px; letter-spacing: 1.7px; color: #6E695E;
                  font-weight: 700; padding: 0 10px; margin: 6px 0 10px; }
        .nvparthdr { margin: 4px 0 2px; }
        .nvparthdr:hover { background: #F0EADC; border-radius: 6px; }
        .nvrow { padding: 11px 10px; border-radius: 6px; margin-bottom: 2px;
                 border-left: 3px solid transparent; }
        .nvrowhit:hover { background: #F0EADC; border-radius: 6px; }
        .nvrow.active { background: #EAE3D2; border-left: 3px solid #C8341E; }
        /* muted, not muted-2. The row is a BUTTON and its fill deepens to
           @select on hover / when active, so muted-2 measured 2.61:1 on the
           sidebar ground and 2.36:1 under the pointer -- the number went
           faintest exactly when you reached for it. muted holds 4.71:1 and
           4.27:1, and is already what .nvtotal uses. The active disc below
           still inverts to ink. */
        .nvnum { font-size: 13px; color: #6E695E; border: 1px solid #D7D2C5;
                 border-radius: 50%; }
        /* Active chapter marker: a soft warm disc (the OS's selected-control
           tone), not a black slab. The row already signals "active" with its
           red edge + beige fill, so the number just firms up in ink on a gently
           deeper disc rather than inverting to black-on-white. */
        .nvnum.active { background: #DED4C2; color: #1A1916; font-weight: 600;
                        border: 1px solid #B3AD9E; }
        .nvrowtitle { font-size: 15px; color: #1A1916; font-weight: 500; }
        .nvrowwords { font-size: 12px; color: #6E695E; margin-top: 2px; }
        /* The ACTIVE row keeps the @select fill, where @muted is 4.27:1.
           The word count steps to @ink-3 on that one row -- still a clear
           step below the title's @ink, and readable on the chapter you are
           actually in. */
        .nvrow.active .nvrowwords { color: #3A362E; }

        .nvfoot { border-top: 1px solid #D7D2C5; padding: 14px 18px; }
        .nvnewbtn { min-height: 40px; border: 1px solid #C9C4B6;
                    border-radius: 8px; background: #FCFBF8; color: #2A2620;
                    box-shadow: none; }
        .nvnewbtn:hover { background: #F1EEE6; }
        .nvnewbtn label { font-size: 15px; font-weight: 500; color: #2A2620; }

        .nvformatbar { min-height: 54px; padding: 0 36px; background: #FCFBF8;
                       border-bottom: 1px solid #D7D2C5; }
        .nvstylebtn { min-height: 34px; padding: 0 13px;
                      border: 1px solid #C9C4B6; border-radius: 8px;
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
                         border: 1px solid #C9C4B6; border-radius: 8px;
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
                      border-radius: 8px; background: #1A1916;
                      box-shadow: none; }
        .nvpromptok:hover { background: #2A2620; }
        /* Destructive variant. Ink is the primary action everywhere in this app
           (Save, Export, Rename), so deleting a chapter or a part must not look
           identical to saving one: the design system reserves signage red for
           exactly this, and Journal's Delete Entry already uses it. Only
           _confirm() adds .danger; _prompt_text stays ink. */
        .nvpromptok.danger { background: #C8341E; border-color: #C8341E; }
        .nvpromptok.danger:hover { background: #B12D19; border-color: #B12D19; }
        .nvpromptcancel { min-height: 34px; padding: 0 16px; color: #2A2620;
                          border: 1px solid #C9C4B6; border-radius: 8px;
                          background: #FCFBF8; box-shadow: none; font-size: 14px; }
        .nvpromptcancel:hover { background: #F1EEE6; }
        .nvpromptempty { font-size: 13px; color: #6E695E; }
        .nvfileitem { padding: 8px 12px; background: transparent; border: none;
                      box-shadow: none; border-radius: 6px; }
        .nvfileitem:hover { background: #EAE3D2; }
        .nvfileitemlab { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 15px; color: #1A1916; }
        .nvfileitem:hover .nvfileitemlab { color: #1A1916; }
        .nvfmtbtn { min-width: 34px; min-height: 34px; padding: 0; border: none;
                    border-radius: 8px; background: transparent;
                    box-shadow: none; color: #2A2620; font-size: 17px; }
        .nvfmtbtn:hover { background: #EFEBE0; }
        .nvfmtbtn.bold { font-weight: 700; }
        .nvfmtbtn.ital { font-style: italic;
                         font-family: "Newsreader","Liberation Serif",serif; }
        .nvfmtbtn.under { text-decoration-line: underline; font-size: 16px; }
        .nvsep { color: #D7D2C5; min-width: 1px; }
        .nvfindbar { background: #F4F2EC; border-bottom: 1px solid #D7D2C5;
                     padding: 8px 36px; }
        .nvfindentry { font-size: 13px; color: #1A1916; background: #FCFBF8;
                       border: 1px solid #C9C4B6; border-radius: 8px;
                       box-shadow: none; min-height: 30px; }
        .nvfindentry:focus { border: 1px solid #8A857A; }
        .nvfindbtn { min-height: 30px; padding: 0 12px; font-size: 13px;
                     color: #2A2620; background: #FCFBF8;
                     border: 1px solid #D7D2C5; border-radius: 8px;
                     box-shadow: none; }
        .nvfindbtn:hover { background: #F1EEE6; }
        .nvfindcount { font-size: 13px; color: #6E695E; }
        .nvcount { font-size: 13px; color: #6E695E; }
        .nvsave { font-size: 13px; color: #6E695E; }

        .nvcanvas { background: #FCFBF8; }
        .nvpage { padding: 80px 24px 160px; }
        .nvcaneyebrow { font-size: 12px; letter-spacing: 2px; color: #6E695E;
                        font-weight: 700; margin-bottom: 24px; }
        .nvchaptertitle { font-family: "Newsreader","Liberation Serif",serif;
                          font-size: 32pt; font-weight: 500; color: #2A2620;
                          background: transparent; border: none;
                          border-radius: 0;
                          box-shadow: none; padding: 0 0 12px; margin: 0 0 22px; }
        .nvbody { font-family: "Newsreader","Liberation Serif",serif;
                  font-size: 20px; color: #2A2620; background: #FCFBF8;
                  caret-color: #C8341E; }
        .nvbody text { background: #FCFBF8; }
        .nvbody text selection { background-color: #EAE3D2; color: #1A1916; }
        .nvplaceholder { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 20px; font-style: italic; color: #8A857A; }
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
