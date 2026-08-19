#!/usr/bin/env python3
"""
Screenplay — the Notebook OS script editor (native GTK).

A Courier screenplay page on paper, with an Element format bar
(Scene / Action / Character / Dialogue / Paren. / Transition) and a live
page-count / word-count / autosave indicator. The File menu provides
New / Open / Save / Save As over user files under $NB_HOME/Documents
(.fountain, .txt, or Screenplay .json). Opens on a blank UNTITLED page — no seed
content. The session-recovery snapshot (body text + per-line element formatting +
current file path) persists to screenplay.json and is restored on launch. An
optional script path may be passed as sys.argv[1] (the Finder opens scripts this
way).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo  # noqa: E402

import time
import os
import sys
import json
import copy

import nbapp
import nbicons
import nbpicker
import nbprint
from nbi18n import _t  # noqa: E402

ELEMENTS = ("Scene", "Action", "Character", "Dialogue", "Paren.", "Transition")

# Pressing Enter at the end of an element advances to the element that
# conventionally follows it (Final Draft / Fountain auto-advance): after a Scene
# Heading you write Action; a Character cue is followed by Dialogue; a
# Parenthetical returns to Dialogue; a Transition begins the next Scene.
#   Scene(0) Action(1) Character(2) Dialogue(3) Paren.(4) Transition(5)
FLOW = {0: 1, 1: 1, 2: 3, 3: 1, 4: 3, 5: 0}

# ---- Zine / PDF page layout (half-letter 5.5x8.5" page, monospace script) ----
# Standard screenplay is fixed-pitch Courier with fixed element indents; on the
# half-letter zine page we keep the proportions and scale the type down to fit.
PDF_FS = 9.0            # monospace body size (pt)
PDF_LEAD = 12.0         # line advance (pt)
PDF_ML = 48.0           # left text margin (pt)
PDF_MR = 30.0           # right text margin (pt)
PDF_MT = 54.0           # top text margin — first baseline (pt)
PDF_MB = 48.0           # bottom text margin (pt)
# A standard script is a SIXTY-COLUMN measure: 6.0 inches of text at ten
# characters to the inch, with every element's indent fixed in that grid. This
# half-letter page holds 63 columns at PDF_FS, so the standard indents are
# scaled into it rather than guessed — the proportions a reader recognises as a
# screenplay are what survive the change of paper size, which is the whole point
# of "script formatting" as opposed to "monospace text".
#
# The two full-width elements used to be 54 and 56 columns wide. Nothing wanted
# that; a scene heading and the action under it share one measure.
PDF_COLS = 63           # columns the half-letter measure holds at PDF_FS
_STD_MEASURE = 60.0     # the standard 6.0in text block, in columns


def _std(units):
    """A standard-script column, scaled into this page's measure."""
    return int(round(units * PDF_COLS / _STD_MEASURE))


# Per element: (indent in monospace columns, wrap width in columns, UPPER-case,
# right-align). The standard positions, in inches from the paper's left edge
# with its 1.5in left margin: cue 3.7, parenthetical 3.1, dialogue 2.5.
PDF_ELEMENT = {
    0: (0, PDF_COLS, True, False),                      # Scene Heading
    1: (0, PDF_COLS, False, False),                     # Action
    2: (_std(22), PDF_COLS - _std(22), True, False),    # Character cue
    3: (_std(10), _std(35), False, False),              # Dialogue
    4: (_std(16), _std(20), False, False),              # Parenthetical
    5: (0, PDF_COLS, True, True),                       # Transition (flush right)
}

# Element indices, named where the pagination rules read them.
EL_SCENE, EL_ACTION, EL_CUE, EL_DIALOGUE, EL_PAREN, EL_TRANSITION = range(6)
MORE_MARK = "(MORE)"
CONTD_MARK = "(CONT'D)"

# -- persistence: the script survives close/reboot under $NB_HOME/.config/notebook,
# matching writer.py's word-processor pattern (plain body + formatting spans) --
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
DOC_FILE = os.path.join(CFG_DIR, "screenplay.json")
MAX_SCRIPT_BYTES = 64 * 1024 * 1024


def _read_script_bytes(path, limit=MAX_SCRIPT_BYTES):
    """Read a selected script without unbounded UI-thread allocation."""
    with open(path, "rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("script is too large")
    return raw


def _read_plain_text(path):
    """Return recovered text and whether UTF-8 decoding was lossy."""
    raw = _read_script_bytes(path)
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), True
# User files (File ▸ Open/Save) live under Documents; the session-recovery
# snapshot stays in CFG_DIR/screenplay.json and is independent of the user file.
DOCS_DIR = os.path.join(HOME, "Documents")

# First-run default: a blank UNTITLED page (no-seed rule). The title page shows
# this until a user file is opened or saved.
DEFAULT_TITLE = "UNTITLED SCREENPLAY"


# The exported script goes through Pango, never cairo's toy text API. The toy
# API binds ONE face and does no per-character fallback, and the face
# "monospace" resolves to carries no CJK, no Devanagari and no Hebrew — so a
# screenplay written in any of those exported a correct title page followed by
# BLANK sheets, because .notdef in that face draws nothing at all rather than a
# box. Pango keeps the monospace face for the Latin the format is built around
# and falls back per glyph for the rest.
_PDF_FAMILY = "Liberation Mono, DejaVu Sans Mono, monospace"


# ---- script pagination ------------------------------------------------------
# A row is (column, text, right_align, element, cue) or None for a blank
# spacer. `cue` is the speaking character on dialogue rows and None elsewhere;
# it exists only so a speech broken by a page can name itself again on the next.
def _row(col, text, right, element, cue=None):
    return (col, text, right, element, cue)


def _splits_speech(rows, at):
    """The character whose dialogue a break at `at` would cut in half, or None.

    Both sides have to be the SAME speaker's dialogue: a break between one
    character's last line and the next character's cue is a paragraph break, not
    a split speech, and marking it (MORE) would be a lie about who is talking.
    """
    if at <= 0 or at >= len(rows):
        return None
    before, after = rows[at - 1], rows[at]
    if before is None or after is None:
        return None
    if (before[3] in (EL_DIALOGUE, EL_PAREN)
            and after[3] in (EL_DIALOGUE, EL_PAREN)
            and before[4] and before[4] == after[4]):
        return before[4]
    return None


def _pull_back(rows, start, end):
    """Move a page break back off a line that must not end a page.

    A scene heading stranded at the foot of a page tells the reader nothing —
    its scene is overleaf. A character cue with its speech on the next sheet is
    worse. Both get pushed forward whole. The walk is bounded: a page has to
    hold SOMETHING, so a pathological run gives up and breaks where it was.
    """
    limit = end
    for _ in range(8):
        if end - start <= 1:
            return limit
        last = rows[end - 1]
        if last is None:                       # trailing air, free to drop
            end -= 1
            continue
        kind = last[3]
        if kind in (EL_SCENE, EL_CUE, EL_PAREN):
            end -= 1
            continue
        # one lonely line of dialogue under its cue: take the cue with it
        if (kind == EL_DIALOGUE and end - 2 >= start
                and rows[end - 2] is not None and rows[end - 2][3] == EL_CUE):
            end -= 2
            continue
        break
    return end if end > start else limit


def paginate_script(rows, lines_per_page):
    """Split laid-out rows into pages the way a script paginates.

    Cutting every N lines is what a text file does. A script has rules, and they
    are most of what makes a printed page read as a screenplay:

      * a speech broken by a page gets **(MORE)** beneath it, and the speaker's
        name again with **(CONT'D)** at the top of the continuation;
      * a **scene heading never ends a page**;
      * a **character cue never ends a page**, and never loses its first line of
        dialogue across the break;
      * a page never **opens on blank air**.

    Returns a list of pages, each a list of rows. Shared by the page counter and
    the PDF on purpose — they used to compute page counts separately and
    disagreed, and page count is the number a screenwriter works in.
    """
    lpp = max(1, int(lines_per_page))
    rows = list(rows)
    pages, carried = [], []
    i, n = 0, len(rows)
    while i < n:
        while i < n and rows[i] is None:       # never open a page on air
            i += 1
        if i >= n:
            break
        room = max(1, lpp - len(carried))
        end = min(i + room, n)
        if end < n:
            end = _pull_back(rows, i, end)
        speaker = _splits_speech(rows, end)
        if speaker is not None and end - i >= 2:
            end -= 1                           # give (MORE) its own line
            speaker = _splits_speech(rows, end)
        page = carried + [r for r in rows[i:end]]
        carried = []
        while page and page[-1] is None:       # trailing air prints as nothing
            page.pop()
        if speaker is not None:
            cue_col = PDF_ELEMENT[EL_CUE][0]
            page.append(_row(cue_col, MORE_MARK, False, EL_CUE, speaker))
            carried = [_row(cue_col, "%s %s" % (speaker, CONTD_MARK), False,
                            EL_CUE, speaker)]
        pages.append(page)
        i = end
    if carried:
        pages.append(carried)
    return pages or [[]]


def _pdf_layout(cr, text, size):
    lay = PangoCairo.create_layout(cr)
    # a PDF user unit IS a point: at Pango's default 96dpi every line would come
    # out a third larger than the half-letter grid above was measured for
    PangoCairo.context_set_resolution(lay.get_context(), 72.0)
    fd = Pango.FontDescription()
    fd.set_family(_PDF_FAMILY)
    fd.set_size(int(size * Pango.SCALE))
    lay.set_font_description(fd)
    lay.set_text(text, -1)
    return lay


def _pdf_w(cr, text, size):
    """The drawn width of `text`, in points."""
    return _pdf_layout(cr, text, size).get_pixel_size()[0]


def _pdf_show(cr, x, y, text, size):
    """Draw `text` with its BASELINE at y — the anchor cr.show_text used, so the
    page grid keeps the geometry it was measured for."""
    lay = _pdf_layout(cr, text, size)
    cr.move_to(x, y - lay.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, lay)


class Screenplay(nbapp.AppWindow):
    app_name = "Screenplay"
    menus = ("File", "Edit", "Format", "Insert", "View")

    def __init__(self):
        super().__init__()
        self._install_css()
        self._elbtns = []
        self._active = 0
        self._save_timer = None
        self._count_timer = None          # debounced word / page totals
        self._notice_timer = None         # puts the save chip back after a notice
        self._caret_idle = None           # coalesced "bring the caret on screen"
        self._caret_goal = "caret"
        self._caret_armed = None          # a caret scroll the desk was too short for
        self._focus_seen = None           # the widget on the paper that has focus
        self._closed = False
        self._find_hits = []              # (start_off, end_off) of find hits
        self._find_i = -1
        # True when there are edits not yet written to the bound user file (the
        # session-recovery autosave is separate). Drives the discard confirm on
        # New / Open; cleared on load and on a real File ▸ Save.
        self._file_dirty = False

        # --- persistence: load BEFORE the body is seeded so the saved script
        # (or the blank page on first run) fills the canvas, not a mock. The
        # _loading guard keeps seeding from tripping the autosave path. ---
        self._loading = True
        doc = self._load_doc()
        # the user file this script maps to (None = unsaved / no file). The File
        # menu operates on this; session recovery restores it across boots.
        self._path = doc.get("path")

        # --- element / format bar ---
        fbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        fbar.get_style_context().add_class("formatbar")

        elabel = Gtk.Label(label=_t("ELEMENT"))
        elabel.get_style_context().add_class("elementlabel")
        elabel.set_margin_end(10)
        fbar.pack_start(elabel, False, False, 0)

        for i, name in enumerate(ELEMENTS):
            b = Gtk.ToggleButton(label=name)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("elbtn")
            b.set_tooltip_text("%s element  (Ctrl+%d)" % (name, i + 1))
            if i == 0:
                b.get_style_context().add_class("active")
                b.set_active(True)
            b.connect("clicked", self._on_element, i)
            self._elbtns.append(b)
            fbar.pack_start(b, False, False, 4)

        # right: page count | word count | save state
        self.saved = Gtk.Label()
        self.saved.get_style_context().add_class("savestate")
        self.saved.set_tooltip_text(
            _t("Save state. File ▸ Save writes the script to a file."))
        fbar.pack_end(self.saved, False, False, 4)
        fbar.pack_end(self._sep(), False, False, 14)
        self.words = Gtk.Label(label=_t("0 words"))
        self.words.get_style_context().add_class("meta")
        self.words.set_tooltip_text(_t("Words in this script"))
        fbar.pack_end(self.words, False, False, 0)
        fbar.pack_end(self._sep(), False, False, 14)
        self.pages = Gtk.Label(label=_t("1 page"))
        self.pages.get_style_context().add_class("meta")
        self.pages.set_tooltip_text(_t("Pages in the printed script"))
        fbar.pack_end(self.pages, False, False, 0)
        # Resting state is an honest "No changes" — the chip only claims a
        # timestamped "Saved HH:MM" after a real write has actually happened.
        self.saved.set_markup(
            '<span foreground="#7FA98C">● </span>No changes')

        self.content.pack_start(fbar, False, False, 0)
        self.content.pack_start(self._build_findbar(), False, False, 0)

        # --- canvas (scrolling paper desk) ---
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("desk")
        # The desk is the only thing in this app that scrolls; every "bring
        # that on screen" goes through its adjustment (see _scroll_to_caret).
        self._scroll = scroll

        centering = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        centering.set_halign(Gtk.Align.CENTER)
        centering.set_margin_top(48)
        centering.set_margin_bottom(120)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_size_request(800, -1)

        # page sheet
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.get_style_context().add_class("page")
        page.set_size_request(800, 1040)

        overlay = Gtk.Overlay()
        overlay.add(page)
        pageno = Gtk.Label(label="1.")
        pageno.get_style_context().add_class("pageno")
        pageno.set_halign(Gtk.Align.END)
        pageno.set_valign(Gtk.Align.START)
        pageno.set_margin_top(34)
        pageno.set_margin_end(60)
        overlay.add_overlay(pageno)
        overlay.set_overlay_pass_through(pageno, True)

        # title block — the title reflects the current file (default UNTITLED),
        # and the line beneath it is the file-status line: the open file's path,
        # or the empty-state prompt when no user file is open. No marketing text.
        titlebox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titlebox.set_halign(Gtk.Align.CENTER)
        titlebox.set_margin_top(74)
        titlebox.set_margin_bottom(56)
        # Title and subtitle are EDITABLE (they were fixed labels derived from
        # the filename, so a script could never be titled from inside the app).
        # Frameless, centred entries keep the title-page look while taking the
        # caret; both are part of the document and persist with it.
        # A Gtk.Entry's natural width comes from width-chars, NEVER from the text
        # in it, and the untitled page's own title is 19 characters of 18px bold
        # Courier. Left at the default the entry was ~170px wide and the title
        # page opened reading "UNTITLED SCREENP" with the L of PLAY sliced down
        # the middle — the first thing anyone saw of this app. 34 characters is
        # wider than any of the three lines in the block and still far inside the
        # page sheet, so the paper keeps its width and its centring (see the
        # status label's ellipsize note below).
        _TITLE_CHARS = 34
        self.scripttitle = Gtk.Entry()
        self.scripttitle.set_width_chars(_TITLE_CHARS)
        self.scripttitle.set_max_width_chars(_TITLE_CHARS)
        self.scripttitle.set_has_frame(False)
        self.scripttitle.set_alignment(0.5)
        self.scripttitle.set_placeholder_text(DEFAULT_TITLE)
        self.scripttitle.set_text(doc.get("title") or DEFAULT_TITLE)
        self.scripttitle.get_style_context().add_class("scripttitle")
        self.scripttitle.connect("changed", self._on_titlebar_change)
        self.scriptsubtitle = Gtk.Entry()
        # Same measure as the title, or "written by Alexander Hamilton" clips.
        self.scriptsubtitle.set_width_chars(_TITLE_CHARS)
        self.scriptsubtitle.set_max_width_chars(_TITLE_CHARS)
        self.scriptsubtitle.set_has_frame(False)
        self.scriptsubtitle.set_alignment(0.5)
        self.scriptsubtitle.set_placeholder_text(_t("written by"))
        self.scriptsubtitle.set_text(doc.get("subtitle") or "")
        self.scriptsubtitle.get_style_context().add_class("scriptsubtitle")
        self.scriptsubtitle.set_margin_top(10)
        self.scriptsubtitle.connect("changed", self._on_titlebar_change)
        self.status = Gtk.Label(label="")
        self.status.get_style_context().add_class("scriptsub")
        self.status.set_margin_top(14)
        # The file path is arbitrarily long (a script on a USB stick, a deep
        # folder). Left un-trimmed it sets the page sheet's width, which STRETCHES
        # the paper to the window edges, kills its centring and leaves the script
        # hard against the left of the screen — the "text veers left" report.
        # Trim in the middle so the file name (the useful end) always shows.
        self.status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status.set_max_width_chars(46)
        titlebox.pack_start(self.scripttitle, False, False, 0)
        titlebox.pack_start(self.scriptsubtitle, False, False, 0)
        titlebox.pack_start(self.status, False, False, 0)
        page.pack_start(titlebox, False, False, 0)
        self._update_status()

        # editable body
        self.body = Gtk.TextView()
        # WORD_CHAR, not WORD: the page is a fixed measure, and a word longer
        # than the measure (a URL, a long compound) has to break inside itself
        # or it runs off the paper with no way to scroll to it. The exported
        # page breaks over-long words the same way (see _wrap_text), so the
        # screen keeps agreeing with the print.
        self.body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.body.get_style_context().add_class("scriptbody")
        self.body.set_pixels_below_lines(8)
        self.body.set_pixels_inside_wrap(8)
        self.body.set_left_margin(92)
        self.body.set_right_margin(92)
        self.body.set_size_request(800, 560)
        buf = self.body.get_buffer()
        self._setup_elements(buf)
        buf.connect("changed", self._on_change)
        # keep the element bar reflecting whatever line the caret sits in
        buf.connect("mark-set", self._on_mark_set)
        if doc.get("body"):
            buf.set_text(doc["body"])   # seed from disk (fires guarded _on_change)
            # restore the saved per-line element formatting on top of the text
            self._apply_body_tags(buf, doc.get("body_tags"))
        page.pack_start(self.body, True, True, 0)

        col.pack_start(overlay, False, False, 0)
        centering.pack_start(col, False, False, 0)
        scroll.add(centering)           # GTK wraps this in a Viewport
        # The paper sheet, its title block and the page number sit AROUND the
        # body, so the TextView is not the ScrolledWindow's scrollable child:
        # GTK allocates it its whole content height, its own adjustment has
        # nothing left to move (upper == page size), and body.scroll_to_mark
        # is therefore inert. _scroll_to_caret drives the desk instead.
        #
        # That viewport also carries a FOCUS adjustment, which scrolls the page
        # to whatever child takes focus — and for a TextView taller than the
        # canvas the clamp lands on the TextView's TOP, so clicking back into
        # the script after editing the title threw a scrolled script back to
        # its first line. Focus never moves the page here. (The binding refuses
        # None, so point the clamp at an adjustment nothing scrolls by — the
        # same fix journal.py carries.)
        vp = scroll.get_child()
        if vp is not None:
            vp.set_focus_vadjustment(Gtk.Adjustment())
            vp.set_focus_hadjustment(Gtk.Adjustment())
        # GTK measures a page of text AFTER the edit that made it, so a scroll
        # to the caret can run while the desk still holds the old script's
        # height and stop at the old bottom (an undo that puts a long script
        # back landed a page short). Finish the job when the measurement lands.
        scroll.get_vadjustment().connect("changed", self._on_desk_measured)
        # The title and byline sit above the script and can be reached from the
        # keyboard, so scroll to THEM when they take focus — they are small, so
        # clamping the page to their allocation is right for them.
        for widget in (self.scripttitle, self.scriptsubtitle, self.body):
            widget.connect("focus-in-event", self._on_desk_focus)
        self.content.pack_start(scroll, True, True, 0)

        # seeding complete — real edits now autosave, and a final flush on
        # close keeps the last edit from being lost.
        self._loading = False
        # Undo/redo over the whole script: the body text, its element
        # formatting and the title page, so File ▸ New and File ▸ Open — which
        # replace all three AND overwrite the recovery snapshot that is an
        # unsaved script's only copy — are reversible too. See nbapp.UndoHistory.
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()
        self._recovery_dirty = False
        self._save_error = None
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)

        # Finder launches Screenplay with a script path as argv[1]
        # (.fountain/.txt/.json). Load it last so it overrides the restored
        # session-recovery snapshot.
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self._open_file(sys.argv[1])

        # Land the caret in the script so she can start typing on open without
        # having to click into the page first, and point the element bar at
        # whatever line the caret lands on so it is honest from the first frame.
        try:
            self._sync_element_bar(self.body.get_buffer())
            self.body.grab_focus()
        except Exception:
            pass

    # -- find --------------------------------------------------------------
    # A feature script is a hundred pages in ONE scrolling buffer with no
    # navigation of any kind: finding the scene where a character first appears,
    # or every "INT. KITCHEN", meant scrolling and reading. Every other writing
    # app here can search its own text; this one could not.
    def _build_findbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("findbar")
        self.find_entry = Gtk.SearchEntry()
        nbicons.style_search_entry(self.find_entry)
        self.find_entry.set_placeholder_text(_t("Find in script"))
        self.find_entry.set_width_chars(24)
        self.find_entry.get_style_context().add_class("findinput")
        self.find_entry.connect("search-changed", lambda *_: self._do_find())
        self.find_entry.connect("activate", lambda *_: self._find_step(1))
        bar.pack_start(self.find_entry, False, False, 0)
        prev = Gtk.Button(label="‹")
        prev.get_style_context().add_class("findbtn")
        prev.set_tooltip_text(_t("Previous match"))
        prev.connect("clicked", lambda *_: self._find_step(-1))
        bar.pack_start(prev, False, False, 0)
        nxt = Gtk.Button(label="›")
        nxt.get_style_context().add_class("findbtn")
        nxt.set_tooltip_text(_t("Next match"))
        nxt.connect("clicked", lambda *_: self._find_step(1))
        bar.pack_start(nxt, False, False, 0)
        self.find_count = Gtk.Label(label="", xalign=0)
        self.find_count.get_style_context().add_class("findcount")
        bar.pack_start(self.find_count, False, False, 6)
        done = Gtk.Button(label=_t("Done"))
        done.get_style_context().add_class("findbtn")
        done.connect("clicked", lambda *_: self._toggle_find(False))
        bar.pack_end(done, False, False, 0)
        # Show the controls ONCE, then take the bar out of show_all()'s reach:
        # gtk_widget_show_all() returns immediately on a no-show-all widget, so
        # children shown after the flag is set would never appear (the bug that
        # once opened Writer's find bar as an empty strip).
        bar.show_all()
        bar.set_no_show_all(True)
        bar.hide()
        self._findbar = bar
        return bar

    def _toggle_find(self, show=None):
        vis = self._findbar.get_visible()
        show = (not vis) if show is None else show
        self._findbar.set_visible(show)
        if show:
            self.find_entry.grab_focus()
            self._do_find()
        else:
            self._find_hits = []
            self.find_count.set_text("")
            self.body.grab_focus()

    def _do_find(self):
        needle = self.find_entry.get_text().strip().lower()
        self._find_hits = []
        self._find_i = -1
        if not needle:
            self.find_count.set_text("")
            return
        buf = self.body.get_buffer()
        hay = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).lower()
        i = hay.find(needle)
        while i != -1:
            self._find_hits.append((i, i + len(needle)))
            i = hay.find(needle, i + len(needle))
        n = len(self._find_hits)
        if not n:
            self.find_count.set_text(_t("No matches"))
            return
        self.find_count.set_text(_t("1 match") if n == 1
                                 else _t("%d matches") % n)
        self._find_step(1)

    def _find_step(self, direction):
        """Select the next/previous match and scroll the page to it."""
        if not self._find_hits:
            self._do_find()
            if not self._find_hits:
                return
        self._find_i = (self._find_i + direction) % len(self._find_hits)
        s_off, e_off = self._find_hits[self._find_i]
        buf = self.body.get_buffer()
        n = buf.get_char_count()
        s = buf.get_iter_at_offset(max(0, min(s_off, n)))
        e = buf.get_iter_at_offset(max(0, min(e_off, n)))
        buf.select_range(s, e)
        # The match is now the selection and its start is the caret, so putting
        # the caret on screen puts the match on screen. It is the DESK that
        # scrolls, never the TextView (see _scroll_to_caret) — a match below
        # the fold used to be selected and counted while the page stayed on the
        # title page.
        self._queue_caret_scroll("find")
        self.find_count.set_text(
            _t("%d of %d") % (self._find_i + 1, len(self._find_hits)))

    # -- helpers --
    def _sep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("msep")
        return s

    # each screenplay element is a paragraph style: indentation + alignment,
    # the way Final Draft / Fountain lay a script out on the page.
    EL_TAGS = ("el_scene", "el_action", "el_character",
               "el_dialogue", "el_paren", "el_transition")

    def _setup_elements(self, buf):
        tt = buf.get_tag_table()
        # A GTK TextTag's left/right-margin REPLACES the TextView's own margin for
        # tagged text (it is absolute from the text-content edge, NOT added on top
        # of the view margin). These indents were written as if additive over the
        # body's BASE page margin, so Scene/Action (indent 0) landed FLUSH on the
        # page's left edge and the whole script "veered left". Bake the base margin
        # into every element so each keeps its intended relative indent but starts
        # from the proper page margin. BASE must match the body's set_left/right_
        # margin (see _build). (Same GTK gotcha as the Writer word processor.)
        BASE = 92
        specs = [
            ("el_scene", {"weight": Pango.Weight.BOLD, "left-margin": BASE}),
            ("el_action", {"left-margin": BASE}),
            ("el_character", {"left-margin": BASE + 216}),
            ("el_dialogue", {"left-margin": BASE + 104, "right-margin": BASE + 128}),
            ("el_paren", {"left-margin": BASE + 160, "right-margin": BASE + 160,
                          "style": Pango.Style.ITALIC}),
            ("el_transition", {"justification": Gtk.Justification.RIGHT}),
        ]
        for name, props in specs:
            t = Gtk.TextTag(name=name)
            for k, v in props.items():
                t.set_property(k, v)
            tt.add(t)

    def _element_bounds(self, buf):
        """Whole-line bounds covering the selection (every line it touches), or
        just the caret's line when nothing is selected — so applying an element
        to a multi-line selection lays it on all of them, not one line."""
        sel = buf.get_selection_bounds()
        if not sel:
            it = buf.get_iter_at_mark(buf.get_insert())
            ls = it.copy(); ls.set_line_offset(0)
            le = it.copy()
            if not le.ends_line():
                le.forward_to_line_end()
            return ls, le
        ls = sel[0].copy(); ls.set_line_offset(0)
        le = sel[1].copy()
        if not le.ends_line():
            le.forward_to_line_end()
        return ls, le

    def _apply_element(self, idx):
        buf = self.body.get_buffer()
        ls, le = self._element_bounds(buf)
        for name in self.EL_TAGS:
            buf.remove_tag_by_name(name, ls, le)
        buf.apply_tag_by_name(self.EL_TAGS[idx], ls, le)

    def _set_active_button(self, idx):
        """Move the active-element highlight to button `idx` (no text change)."""
        if not (0 <= idx < len(self._elbtns)):
            return
        # The row is toggle buttons so the chosen element is readable to
        # assistive technology, and set_active on a toggle emits "clicked" —
        # restating the row from inside _on_element re-entered _on_element for
        # every button until the stack blew (a Codex accessibility pass turned
        # the plain buttons into toggles and kept the plain-button setter).
        # choose_segment lights the row with every handler blocked.
        nbapp.choose_segment(enumerate(self._elbtns), idx, "active")
        self._active = idx

    def _on_element(self, btn, idx):
        self.undo.checkpoint("Element")
        self._set_active_button(idx)
        # apply the element's layout to the caret line (or the whole selection),
        # even when the same element is re-clicked. Element formatting is
        # tag-only and does NOT fire the buffer's 'changed' signal, so _touch()
        # explicitly keeps it counted as an edit and autosaved.
        self._apply_element(idx)
        self._touch()
        self.undo.commit()
        self.body.grab_focus()

    def _cycle_element(self, delta):
        """Tab / Shift+Tab step the caret line through the element types in
        order, applying the layout immediately — the keyboard-only way to pick an
        element while writing, so a novice never has to reach for the mouse."""
        if not self._elbtns:
            return
        idx = (self._active + delta) % len(self._elbtns)
        self._on_element(self._elbtns[idx], idx)

    def _newline(self, soft):
        """Enter: break the line and auto-advance to the element that follows the
        current one (Scene→Action, Character→Dialogue, …). Shift+Enter keeps the
        same element for a soft line break within a block. Grouped as one undo
        step; the new line is scrolled into view."""
        try:
            buf = self.body.get_buffer()
            buf.begin_user_action()
            if buf.get_has_selection():
                buf.delete_selection(True, True)
            buf.insert_at_cursor("\n")
            buf.end_user_action()
            if not soft:
                self._set_active_button(FLOW.get(self._active, 1))
            self._queue_caret_scroll()
        except Exception:
            pass
        return True

    # -- keeping the caret on screen ---------------------------------------
    # The body is not the scrollable child of anything (see _build), so the
    # TextView's own scroll_to_mark has no adjustment left to move. Everything
    # that moves the caret — typing, Enter, the arrow keys, Find, Go to End —
    # ends up here, and here scrolls the DESK.
    #
    # How much of the page to keep around the caret, per kind of move: a
    # keystroke only has to stay clear of the window edge, while a jump to a
    # match lands somewhere the writer has not been reading and needs the lines
    # around it to make sense of.
    CARET_MARGIN = {"caret": 40, "find": 150}

    def _queue_caret_scroll(self, goal="caret"):
        """Bring the caret into view once GTK has laid the new text out.

        Deferred to an idle, which runs after the resize that the new text
        causes: measuring the caret before that reads the OLD layout."""
        self._caret_goal = goal
        if self._closed or self._caret_idle is not None:
            return
        self._caret_idle = GLib.idle_add(self._caret_tick)

    def _caret_tick(self):
        self._caret_idle = None
        goal, self._caret_goal = self._caret_goal, "caret"
        if self._closed:
            return False
        if goal == "top":
            self._caret_armed = None
            try:
                self._scroll.get_vadjustment().set_value(0)
            except Exception:
                pass
        else:
            self._scroll_to_caret(goal)
        return False

    def _desk_y(self, widget, y, height):
        """(top, height) of a widget-relative y in the desk's scroll
        coordinates — what the vertical adjustment measures — or None."""
        vp = self._scroll.get_child()
        if vp is None:
            return None
        pos = widget.translate_coordinates(vp, 0, y)
        if pos is None:
            return None
        # translate_coordinates answers in the viewport's VISIBLE coordinates,
        # which the current scroll offset has already moved; adding the offset
        # back gives the position in the scrolled content.
        return (pos[1] + self._scroll.get_vadjustment().get_value(), height)

    def _scroll_to_caret(self, goal="caret"):
        """Scroll the desk the least it takes to show the caret. Crash-safe."""
        margin = self.CARET_MARGIN.get(goal, 40)
        try:
            buf = self.body.get_buffer()
            it = buf.get_iter_at_mark(buf.get_insert())
            rect = self.body.get_iter_location(it)
            _wx, wy = self.body.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x, rect.y)
            spot = self._desk_y(self.body, wy, rect.height)
            if spot is None:
                return
            y, h = spot
            adj = self._scroll.get_vadjustment()
            adj.clamp_page(max(0, y - margin), y + h + margin)
            # Did the caret actually land on the page? It cannot when the desk
            # has not been measured for this text yet, so remember the job and
            # let _on_desk_measured finish it.
            top = adj.get_value()
            landed = top <= y and y + h <= top + adj.get_page_size()
            self._caret_armed = None if landed else goal
        except Exception:
            pass

    def _on_desk_measured(self, _adj):
        """The desk's content height changed (GTK laid the script out). If a
        caret scroll stopped short against the old height, finish it now."""
        if self._caret_armed and not self._closed:
            self._queue_caret_scroll(self._caret_armed)

    def _on_desk_focus(self, widget, _ev=None):
        """Focus arrived somewhere on the paper. The desk does not scroll to a
        focused child by itself any more (see _build) — that is what keeps a
        click into the script from throwing a scrolled page back to line 1 —
        but the title and the byline are small and can be reached from the
        keyboard while they are off the top, so bring THOSE into view.

        Only on a real move: coming back to the window from another app hands
        the same widget focus again, and that must not move the page.
        Crash-safe."""
        moved, self._focus_seen = self._focus_seen is not widget, widget
        if widget is self.body or not moved:
            return False
        try:
            spot = self._desk_y(widget, 0, widget.get_allocation().height)
            if spot is not None:
                y, h = spot
                self._scroll.get_vadjustment().clamp_page(
                    max(0, y - 24), y + h + 24)
        except Exception:
            pass
        return False

    def _on_mark_set(self, buf, _it, mark):
        """Caret moved / selection changed → point the element bar at the caret
        line's element so the bar honestly reflects 'what element you're in'."""
        if self._loading:
            return
        try:
            if mark is buf.get_insert() or mark is buf.get_selection_bound():
                self._sync_element_bar(buf)
            if mark is buf.get_insert() and self.body.has_focus():
                # Arrow keys, Page Down, a selection dragged past the bottom
                # edge: the caret moves with no edit, and the desk is what has
                # to follow it (see _scroll_to_caret).
                self._queue_caret_scroll()
        except Exception:
            pass

    def _sync_element_bar(self, buf):
        """Highlight the element the caret's line is tagged with. A line with no
        element tag (plain text, laid out identically to Action) leaves the
        current highlight unchanged, so it never flickers off mid-script."""
        it = buf.get_iter_at_mark(buf.get_insert())
        ls = it.copy(); ls.set_line_offset(0)
        table = buf.get_tag_table()
        for i, name in enumerate(self.EL_TAGS):
            tag = table.lookup(name)
            if tag is not None and ls.has_tag(tag):
                self._set_active_button(i)
                return

    def _on_titlebar_change(self, _entry):
        """Title / subtitle edited. These are Gtk.Entries, so they must NOT be
        wired to _on_change (which is a TextBuffer handler and would call
        buf.get_text(start, end, False) on an Entry). Word and page counts come
        from the body only, so this just marks the document dirty for autosave."""
        if self._loading:
            return
        self._touch()

    def _on_change(self, buf):
        if self._loading:
            self._refresh_counts()
            return   # seeding from disk: update the counts, don't autosave
        # Counting words means copying the whole script out of the buffer, and
        # the page total lays every line out; neither belongs on the keystroke
        # path of a 120-page script. Coalesce them, as the other writing apps do.
        if self._count_timer is None:
            self._count_timer = GLib.timeout_add(200, self._count_tick)
        # Keep the caret line tagged with its element as the writer types. GTK
        # does NOT extend a tag onto text appended past its end, nor onto a fresh
        # line after Enter, and a tag can't be applied to an empty line at all —
        # so without this, setting an element then typing would silently lose the
        # formatting. Re-tagging just the one caret line each keystroke is cheap.
        self._retag_current_line()
        self._touch()
        # Typing at the bottom of the window used to run off it: the caret
        # went on down the page while the desk stayed where it was.
        self._queue_caret_scroll()

    def _count_tick(self):
        self._count_timer = None
        if self._closed:
            return False
        self._refresh_counts()
        return False

    def _refresh_counts(self):
        """Word total and page total for the format bar."""
        buf = self.body.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        n = len(txt.split())
        self.words.set_text("%d word%s" % (n, "" if n == 1 else "s"))
        try:
            p = self._page_total()
        except Exception:
            p = max(1, -(-n // 190))          # last resort, never crash a count
        self.pages.set_text("%d page%s" % (p, "" if p == 1 else "s"))

    def _page_total(self):
        """The script's REAL page count: the same laid-out lines the PDF and the
        printer use, divided by the lines a page holds.

        It used to be words / 190, which is not how a script paginates — the
        format is fixed-pitch with fixed indents, so page count follows LINES,
        and a dialogue-heavy scene has far fewer words per page than an action
        one. The bar disagreed with the script that came out of the printer, and
        page count is the one number a screenwriter actually works in.

        It reads the SAME paginator the PDF does, so the (MORE)/(CONT'D) lines
        and the pages a widowed slugline pushes forward are counted here too."""
        pages, _lpp = self._page_rows()
        return max(1, len(pages))

    def _retag_current_line(self):
        """Apply the active element's tag across the whole caret line so typed
        and appended text stays formatted. No-op on an empty line (nothing to
        tag — it re-tags itself the moment a character is typed)."""
        try:
            buf = self.body.get_buffer()
            it = buf.get_iter_at_mark(buf.get_insert())
            ls = it.copy(); ls.set_line_offset(0)
            le = it.copy()
            if not le.ends_line():
                le.forward_to_line_end()
            if le.get_offset() <= ls.get_offset():
                return
            for name in self.EL_TAGS:
                buf.remove_tag_by_name(name, ls, le)
            if 0 <= self._active < len(self.EL_TAGS):
                buf.apply_tag_by_name(self.EL_TAGS[self._active], ls, le)
        except Exception:
            pass

    def _touch(self):
        """Record an edit and arm the debounced session-recovery autosave. The
        single entry point for every content change — including a tag-only
        element change, which does NOT emit the buffer's 'changed' signal and so
        would otherwise never mark the script dirty, flip the chip, or autosave.
        Marks the script dirty relative to its user file (drives the New / Open
        discard confirm) and flashes the 'Saving…' chip. Crash-safe."""
        if self._loading:
            return
        self._prepare_recovery_write()
        self._file_dirty = True
        # From here until a write returns, the script exists only in this
        # window. See _on_delete.
        self._recovery_dirty = True
        # One undo step per burst of typing; _touch is the single entry point
        # for every content change, element retag included. Only re-arms a
        # timer, so it adds nothing measurable per keystroke.
        self.undo.touch()
        try:
            self.saved.set_markup(
                '<span foreground="#C8341E">● </span>Saving…')
        except Exception:
            pass
        if self._save_timer:
            try:
                GLib.source_remove(self._save_timer)
            except Exception:
                pass
        self._save_timer = GLib.timeout_add(900, self._save_now)

    def _save_now(self):
        """Debounce fired: persist to disk, and only then flip the chip to a
        REAL 'Saved HH:MM' — the indicator now reflects an actual write."""
        self._save_timer = None
        if self._closed:
            return False
        if self._save_doc():
            self._set_saved()
        else:
            # The chip used to stay on "Saving…" forever here. Not a false
            # claim, but not a signal either — and this app had nothing else
            # anywhere that mentioned a failed write.
            try:
                self.saved.set_markup(
                    '<span foreground="#C8341E">● </span>' + _t("Not saved"))
            except Exception:
                pass
        return False

    def _set_saved(self):
        self.saved.set_markup(
            '<span foreground="#7FA98C">● </span>Saved %s'
            % time.strftime("%H:%M"))
        return False

    # ---- persistence (matches writer.py: plain body + formatting spans) ----
    def _load_doc(self):
        """Load the session-recovery snapshot (title + body text + element spans
        + current file path), or fall back to the blank page. Malformed/foreign
        data is ignored (→ blank) and never crashes the app."""
        self._recovery_store_writable = not os.path.exists(DOC_FILE)
        # Reset before every load so reusing an instance cannot carry extension
        # metadata from an earlier recovery document into a different one.
        self._replace_recovery_extra()
        try:
            with open(DOC_FILE) as fh:
                data = json.load(fh)
            # A store that parses but is not this app's shape is moved aside
            # BEFORE the first autosave can replace it. _collect_doc always
            # emits a string "body" and a list "body_tags" (a blank script emits
            # "" and []), so this cannot misfire on our own file — it is the
            # same test File > Open already applies to a chosen file. nbapp's one
            # .bak does not cover this: a blank script still carries the default
            # title, which outweighs a wrong-shape store holding the user's
            # scenes, so the second open overwrites the last copy. See
            # nbapp.quarantine_unrecognized.
            if not (isinstance(data, dict)
                    and isinstance(data.get("body"), str)
                    and isinstance(data.get("body_tags"), list)):
                return {"body": "", "body_tags": [],
                        "title": DEFAULT_TITLE, "subtitle": "", "path": None}
            if isinstance(data, dict):
                self._recovery_store_writable = True
                known = {"title", "subtitle", "body", "body_tags", "path"}
                self._replace_recovery_extra({
                    key: copy.deepcopy(value) for key, value in data.items()
                    if key not in known
                })
                doc = {"body": str(data.get("body", ""))}
                tags = data.get("body_tags")
                doc["body_tags"] = tags if isinstance(tags, list) else []
                t = data.get("title")
                doc["title"] = str(t) if isinstance(t, str) and t else DEFAULT_TITLE
                sub = data.get("subtitle")
                doc["subtitle"] = str(sub) if isinstance(sub, str) else ""
                # the user file this snapshot maps to (may be absent/older)
                p = data.get("path")
                doc["path"] = p if isinstance(p, str) and p else None
                return doc
        except Exception:
            pass
        return {"body": "", "body_tags": [], "title": DEFAULT_TITLE,
                "subtitle": "", "path": None}

    def _prepare_recovery_write(self):
        """Allow recovery writes after a real edit replaces unreadable bytes."""
        if getattr(self, "_recovery_store_writable", True):
            return True
        moved = nbapp.quarantine_unrecognized(DOC_FILE)
        # A failed rename must never turn the guard off: the file may be the
        # user's only surviving copy in a schema this version cannot read.
        self._recovery_store_writable = bool(moved) or not os.path.exists(DOC_FILE)
        return self._recovery_store_writable

    def _collect_doc(self):
        """Snapshot the script (title + body + per-line element formatting + the
        current file path) from the widgets, ready to serialise."""
        buf = self.body.get_buffer()
        # Valid recovery files may contain fields owned by a newer Notebook OS
        # version. Preserve those across autosave/open-close, then let current
        # widget state authoritatively replace the fields this version owns.
        doc = copy.deepcopy(getattr(self, "_recovery_extra", {}))
        doc.update({
            "title": self.scripttitle.get_text(),
            "subtitle": self.scriptsubtitle.get_text(),
            "body": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False),
            # keep the screenplay layout alive across close/reopen: plain text
            # alone would drop every Scene/Character/Dialogue element span
            "body_tags": self._serialize_body_tags(buf),
            # remember which user file this maps to so Save targets it after a boot
            "path": self._path,
        })
        return doc

    def _replace_recovery_extra(self, extra=None):
        """Replace metadata belonging to the current recovery document."""
        self._recovery_extra = copy.deepcopy(extra) if isinstance(extra, dict) else {}

    def _serialize_body_tags(self, buf):
        """Capture the body buffer's element formatting as a list of
        {tag,start,end} char spans (offsets into the plain text) so the
        screenplay layout round-trips through screenplay.json. Only the app's
        own element tags (from _setup_elements) are recorded."""
        spans = []
        table = buf.get_tag_table()
        for name in self.EL_TAGS:
            tag = table.lookup(name)
            if tag is None:
                continue
            it = buf.get_start_iter()
            # a toggle located exactly at the start iter is not reported by
            # forward_to_tag_toggle, so seed the open offset here
            open_off = 0 if it.has_tag(tag) else None
            while it.forward_to_tag_toggle(tag):
                off = it.get_offset()
                if it.has_tag(tag):
                    open_off = off
                elif open_off is not None:
                    spans.append({"tag": name, "start": open_off, "end": off})
                    open_off = None
            if open_off is not None:
                spans.append({"tag": name, "start": open_off,
                              "end": buf.get_end_iter().get_offset()})
        return spans

    def _apply_body_tags(self, buf, spans):
        """Re-apply serialized {tag,start,end} element spans onto the body.
        Defensive about missing/older data (spans may be None) and clamps
        offsets to the text so a stale span can never raise."""
        if not isinstance(spans, list):
            return
        table = buf.get_tag_table()
        n = buf.get_char_count()
        for sp in spans:
            try:
                name = sp.get("tag")
                if name not in self.EL_TAGS or table.lookup(name) is None:
                    continue
                s = max(0, min(int(sp.get("start")), n))
                e = max(0, min(int(sp.get("end")), n))
                if e > s:
                    buf.apply_tag_by_name(name, buf.get_iter_at_offset(s),
                                          buf.get_iter_at_offset(e))
            except Exception:
                continue

    # ---- undo / redo ----
    # The snapshot IS the recovery document: one _collect_doc covers the body,
    # every element span, the title page and the bound file path, so typing, an
    # element change and File ▸ Open are all reversible by the same mechanism.
    def _undo_snapshot(self):
        doc = self._collect_doc()
        buf = self.body.get_buffer()
        doc["_caret"] = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        # Leading underscore: volatile, so it rides along and is restored, but
        # two states differing only in it are not separate undo steps.
        doc["_file_dirty"] = self._file_dirty
        return doc

    def _undo_restore(self, doc):
        known = {"title", "subtitle", "body", "body_tags", "path"}
        extras = {key: value for key, value in doc.items()
                  if key not in known and not key.startswith("_")}
        self._set_document(doc.get("title", ""), doc.get("body", ""),
                           doc.get("body_tags"), doc.get("path"),
                           doc.get("subtitle", ""), extras)
        # _set_document declares the script clean (it is used by New / Open);
        # an undo puts back a state that may well differ from the file on disk.
        self._file_dirty = doc.get("_file_dirty", False)
        buf = self.body.get_buffer()
        caret = min(max(0, doc.get("_caret", 0)), buf.get_char_count())
        buf.place_cursor(buf.get_iter_at_offset(caret))
        self._sync_element_bar(buf)
        self.body.grab_focus()
        # Undo put a caret back somewhere the reader may not be looking.
        self._queue_caret_scroll()

    def _save_doc(self):
        """Write the script to disk. Returns True on success (crash-safe).

        The failure is RECORDED as well as returned. Until the writer picks a
        file this store is the script — there is no other copy — so a write
        that did not land has to survive the moment it happened and reach the
        close guard, which is the last point anyone can be told.""" 
        if not getattr(self, "_recovery_store_writable", True):
            # Not an I/O failure: the bytes on disk were not ours to replace.
            # Deliberately leaves _save_error alone, so the close guard can
            # tell the two apart.
            return False
        try:
            nbapp.atomic_write_json(DOC_FILE, self._collect_doc())
        except Exception as exc:                                  # noqa: BLE001
            self._save_error = exc
            self._recovery_dirty = True
            return False
        self._save_error = None
        self._recovery_dirty = False
        return True

    def _on_delete(self, *_a):
        """Veto a close that would lose the script.

        Until the writer chooses a file, the recovery store IS the document.
        The case that matters is a full or read-only disk: every autosave since
        the last good write has been refused, and only this window still holds
        the afternoon's work. Novel has carried this guard for the same reason;
        this app had the same exposure and no guard at all.

        True keeps the window (and the work) on screen; False lets it close."""
        if not getattr(self, "_recovery_dirty", False):
            return False              # already durable: close, no questions
        if getattr(self, "_path", None) and not getattr(self, "_file_dirty", True):
            # The authored script itself is current. A failed auxiliary
            # recovery/session write may lose caret placement on restart, but
            # it cannot honestly be described as losing the writing or require
            # a destructive-close confirmation.
            return False
        if self._save_doc():          # one retry, silent when it works
            return False
        if getattr(self, "_save_error", None) is None:
            # The store is held read-only because its bytes were not ours to
            # replace. That is not a disk-full error, but the edited script is
            # still only in memory: closing silently here loses it. Use the
            # generic already-translated explanation rather than falsely
            # advising the writer to make room.
            return not self._confirm(
                _t("Not saved"), _t("This could not be saved."),
                _t("Close Without Saving"))
        return not self._confirm(
            _t("Not saved"),
            _t(nbapp.save_failure_reason(self._save_error, DOC_FILE))
            + " " + _t("Closing now loses the writing since the last save. "
                       "Make room and close again to try once more."),
            _t("Close Without Saving"))

    def _on_destroy(self, *_):
        """Flush a final synchronous save on close so the last edit survives."""
        if self._closed:
            return False
        self._closed = True
        self.undo.cancel()
        for attr in ("_save_timer", "_count_timer", "_notice_timer",
                     "_caret_idle"):
            tid = getattr(self, attr, None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._save_doc()
        return False

    # ---- File menu: user files under $NB_HOME/Documents ----
    def _fmt_of(self, path):
        """Screenplay .json (structured, keeps element formatting) vs plain
        screenplay text (.fountain/.txt)."""
        return "json" if os.path.splitext(path)[1].lower() == ".json" else "text"

    def _title_from_path(self, path):
        """Derive a title-page title from a filename: the stem, separators
        collapsed to spaces and upper-cased (screenplay titles are set caps)."""
        stem = os.path.splitext(os.path.basename(path))[0]
        disp = " ".join(stem.replace("_", " ").replace("-", " ").split()).upper()
        return disp or DEFAULT_TITLE

    def _update_status(self):
        """Refresh the file-status line under the title: the open file's path,
        or the empty-state prompt when no user file is open."""
        try:
            if self._path:
                p = self._path
                if p == HOME or p.startswith(HOME + os.sep):
                    p = os.path.join("~", os.path.relpath(p, HOME))
                self.status.set_text(p)
            else:
                # Plain sentence, not a menu path fragment: a first-time writer
                # needs to know the script is safe but has no file of its own.
                self.status.set_text(_t("Not saved to a file"))
        except Exception:
            pass

    def _set_document(self, title, body, tags, path, subtitle="",
                      recovery_extra=None):
        """Replace the whole script (used by New and Open). Seeds under the
        _loading guard so it doesn't trip the autosave path, then marks a clean
        state and refreshes the status line and counts."""
        # New/Open start a different document and must not inherit opaque
        # metadata from the recovery document they replace. Undo explicitly
        # passes the prior document's extras so restoring A still restores all
        # of A, while opening B cannot acquire A's future-version state.
        self._replace_recovery_extra(recovery_extra)
        self._loading = True
        try:
            self.scripttitle.set_text(title or DEFAULT_TITLE)
            self.scriptsubtitle.set_text(subtitle or "")
            buf = self.body.get_buffer()
            buf.remove_all_tags(buf.get_start_iter(), buf.get_end_iter())
            buf.set_text(body)              # fires _on_change (loading → count only)
            self._apply_body_tags(buf, tags)
            self._path = path
            # reset the element bar to Scene, matching the fresh document
            if self._active != 0 and self._elbtns:
                self._elbtns[self._active].get_style_context().remove_class("active")
                self._elbtns[0].get_style_context().add_class("active")
                self._active = 0
        finally:
            self._loading = False
        self._file_dirty = False            # fresh script matches its file / blank
        self._update_status()
        self._prepare_recovery_write()
        if self._save_doc():                # snapshot recovery (incl. new path)
            self._set_saved()
            return True
        # New/Open may still remain safely alive in this window (the close guard
        # sees _recovery_dirty), but a failed write must never be painted as the
        # same green Saved timestamp as a durable recovery snapshot.
        try:
            self.saved.set_markup(
                '<span foreground="#C8341E">● </span>' + _t("Not saved"))
        except Exception:
            pass
        return False

    def _keep_outgoing(self):
        """Put an unsaved, unbound script somewhere it survives, and say so.

        Novel's floor, in the app with the same exposure. New and Open replace
        the script AND screenplay.json (its only copy when no file has been
        chosen), and the campaign retired the "discard?" question in favour of
        undo — which lives only as long as the window. Press New by mistake,
        close, reopen, and the pages are gone. So: write the outgoing script
        into Documents as a real screenplay under its own title before
        replacing it, and post a notification naming the file. Undo still puts
        it back on screen; the file is what makes closing survivable.

        A SCRIPT WITH A FILE IS NOT AUTOMATICALLY SAFE. "It is already on
        disk" holds only until the writer types one more line after Save:
        New then replaced the script AND screenplay.json, and the pages
        written since the last write existed nowhere. `_file_dirty` is
        maintained by `_touch` (the single entry point for every content
        change, tag-only retags included) and cleared only by a real write,
        so it is the honest answer to "does the file already hold this?".

        Returns the basename kept, or None when there was nothing to keep (an
        empty script holds nothing; a bound one already in sync with its file
        is already on disk)."""
        if self._is_empty():
            return None
        if self._path and not getattr(self, "_file_dirty", True):
            return None
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            stem = (self.scripttitle.get_text() or "").strip() or DEFAULT_TITLE
            stem = "".join(c for c in stem if c.isalnum() or c in " -_.").strip()
            base = "%s %s" % (stem or "Screenplay",
                              time.strftime("%Y-%m-%d %H%M"))
            path = os.path.join(DOCS_DIR, base + ".json")
            n = 2
            while os.path.exists(path):
                path = os.path.join(DOCS_DIR, "%s (%d).json" % (base, n))
                n += 1
            if not self._write_file(path):
                return None
            return os.path.basename(path)
        except Exception:                                         # noqa: BLE001
            # A floor that cannot be laid must not stop the action asked for.
            return None

    def _say_kept(self, kept):
        """Name the file the outgoing script went into.

        The notification centre, not the status line: the status line
        describes the script ON SCREEN, and the next save rewrites it anyway.
        (Novel learned both halves the hard way — see its _say_kept.)"""
        if not kept:
            return
        try:
            import nbnotify                                       # noqa: PLC0415
            nbnotify.post(_t("Script kept"),
                          _t("Kept as %s in Documents") % kept,
                          app="screenplay", app_name=_t("Screenplay"))
        except Exception:                                         # noqa: BLE001
            pass

    def _file_new(self):
        """Blank UNTITLED page (no file). The old file is left on disk untouched.

        New replaces the on-screen script AND overwrites screenplay.json (session
        recovery), so anything not written to a user file is lost. Confirm first
        when that would discard real work — an unsaved no-file script (recovery
        is its only copy), or a file-bound script with edits since its last Save
        — and if the user cancels, change nothing (no blank, no overwrite)."""
        # Undoable, and NOT confirmed: the campaign retired the "discard
        # unsaved changes?" card (8ddfd945 -- destruction gets undo, never a
        # confirmation), and Edit > Undo brings the whole script back.
        # Blanking overwrites the recovery snapshot as well, so the checkpoint
        # is what makes that reversible.
        kept = self._keep_outgoing()
        self.undo.checkpoint("New Script")
        self._set_document(DEFAULT_TITLE, "", [], None)
        self._say_kept(kept)
        self.undo.commit()
        self.body.grab_focus()
        # A different script is on the desk now, so show it from the top —
        # its title page — however far down the last one the reader was.
        self._queue_caret_scroll("top")

    def _dirty_to_lose(self):
        """True when replacing the script would discard work: an unsaved no-file
        script (session recovery is its only copy, and it is about to be
        overwritten), or a file-bound script with edits since its last Save."""
        if self._path is None:
            return not self._is_empty()
        # _file_dirty only ever says "something happened since the last save".
        # Undo the something and the script matches its file again, so ask the
        # history as well: every content change goes through _touch, which
        # calls undo.touch(), and the history errs towards dirty, so this can
        # only drop the confirm when the page really is what the file holds.
        return self._file_dirty and self.undo.is_dirty()

    def _confirm_replace(self, title):
        """Ask before replacing the script if that would lose unsaved work.
        Returns True when it is safe to proceed (nothing to lose, or confirmed)."""
        if not self._dirty_to_lose():
            return True
        return self._confirm(
            title,
            _t("The current script has unsaved changes. Discard them?"),
            _t("Discard"))

    def _is_empty(self):
        """True when the script has no body text and no custom title (still the
        default), i.e. there is nothing to lose by blanking it."""
        title = self.scripttitle.get_text().strip()
        if title and title != DEFAULT_TITLE:
            return False
        buf = self.body.get_buffer()
        body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return not body.strip()

    def _confirm(self, title, body, ok_label):
        """A small modal Cancel / <ok_label> confirmation for a destructive
        action. Returns True on the positive response. Defaults to Cancel so a
        stray Return never discards work (crash-safe).

        Undecorated papertone card with its own heading and buttons — the
        pattern the rest of the OS uses (journal, cookbook) — rather than a
        stock GTK dialog with a window-manager title bar."""
        try:
            dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
            dlg.set_decorated(False)
            dlg.get_style_context().add_class("spdlg")
            area = dlg.get_content_area()
            area.set_spacing(0)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.get_style_context().add_class("spdlgbox")
            hd = Gtk.Label(label=title, xalign=0)
            hd.get_style_context().add_class("spdlgtitle")
            msg = Gtk.Label(label=body, xalign=0)
            msg.set_line_wrap(True)
            # width-chars sets the card's measure; max-width-chars alone only
            # caps it and leaves GTK free to size the dialog uncomfortably narrow.
            msg.set_width_chars(38)
            msg.set_max_width_chars(40)
            msg.get_style_context().add_class("spdlgmsg")
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            btns.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("spdlgcancel")
            cancel.connect("clicked",
                           lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
            ok = Gtk.Button(label=ok_label)
            ok.get_style_context().add_class("spdlgok")
            ok.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
            btns.pack_start(cancel, False, False, 0)
            btns.pack_start(ok, False, False, 0)
            box.pack_start(hd, False, False, 0)
            box.pack_start(msg, False, False, 0)
            box.pack_start(btns, False, False, 0)
            area.add(box)
            dlg.show_all()
            # Focus the safe choice so Space/Return keeps the script.
            cancel.grab_focus()
            try:
                resp = dlg.run()
            finally:
                dlg.destroy()
            return resp == Gtk.ResponseType.OK
        except Exception:
            # If the dialog can't be shown, fail safe: do NOT discard.
            return False

    def _open_file(self, path):
        """Load a user file into the script. Returns True on success."""
        recovery_extra = None
        plain_decode_failed = False
        try:
            if self._fmt_of(path) == "json":
                data = json.loads(_read_script_bytes(path).decode("utf-8-sig"))
                # Every app saves into the shared Documents folder, so validate
                # this is a Screenplay document BEFORE touching any state: a
                # foreign JSON (a ledger, a calendar, contacts, …) that lacks a
                # string 'body' and a 'body_tags' element list is rejected here.
                # Without this, Open would replace the model with an empty
                # script, overwrite screenplay.json (session recovery) with it,
                # and a later Save would clobber the foreign file — a silent
                # data loss. On mismatch, flash and change nothing (no model /
                # path / autosave mutation).
                if (not isinstance(data, dict)
                        or not isinstance(data.get("body"), str)
                        or not isinstance(data.get("body_tags"), list)):
                    self._flash("Unrecognized file")
                    return False
                body = str(data.get("body", ""))
                tags = data.get("body_tags")
                tags = tags if isinstance(tags, list) else []
                title = str(data.get("title", "")) or self._title_from_path(path)
                sub = data.get("subtitle")
                subtitle = str(sub) if isinstance(sub, str) else ""
                known = {"title", "subtitle", "body", "body_tags", "path"}
                recovery_extra = {
                    key: copy.deepcopy(value) for key, value in data.items()
                    if key not in known}
            else:
                body, plain_decode_failed = _read_plain_text(path)
                # A plain script may open with Fountain's title page — this app
                # writes one there — so the title and byline come off it, and
                # the file name is the script's identity only when it does not.
                title, subtitle, body = self._split_title_page(body)
                if not title:
                    title = self._title_from_path(path)
                # A .fountain/.txt script carries its layout in convention, not
                # markup. Recover the elements from those conventions, or every
                # line would land on the Action margin and a real script would
                # open as a flat, unindented memo.
                tags = self._elements_from_text(body)
        except Exception:
            self._flash("Open failed")
            return False
        # Same reason as New: opening replaces the script AND its recovery
        # snapshot, so the script that was on screen must be recoverable --
        # through Undo, not a confirmation card (see _file_new).
        kept = self._keep_outgoing()
        self.undo.checkpoint("Open Script")
        # Lossy replacement characters may be useful for recovery, but binding
        # them to the original would let Ctrl+S destroy its undecodable bytes.
        # Treat the recovered script as a new unsaved document instead.
        bound_path = None if plain_decode_failed else path
        self._set_document(title, body, tags, bound_path, subtitle, recovery_extra)
        self._say_kept(kept)
        self.undo.commit()
        self._queue_caret_scroll("top")     # the script opens at its title page
        # What is on screen is exactly what is in the file just opened, so this
        # is the point undo/redo can return to without anything being at risk.
        if plain_decode_failed:
            self._file_dirty = True
            self._update_status()
            self._flash(_t("Some text could not be decoded. Use Save As to "
                           "preserve the original file."))
        else:
            self.undo.mark_saved()
        return True

    # Screenplay text conventions used to recognise elements in a plain script.
    SCENE_PREFIXES = ("INT.", "EXT.", "INT ", "EXT ", "EST.", "I/E.", "INT/EXT")

    def _elements_from_text(self, body):
        """Element spans for a plain screenplay (.fountain/.txt), read from the
        page conventions Fountain itself uses: a line opening INT./EXT. is a
        scene heading, a line in brackets is a parenthetical, an all-caps line
        with text under it is a character cue and what follows it is dialogue,
        and a caps line ending 'TO:' is a transition. Deliberately conservative
        — anything unrecognised is left as Action, which is exactly how an
        untagged line already renders."""
        spans = []
        lines = body.split("\n")
        off = 0
        prev = None
        for i, raw in enumerate(lines):
            t = raw.strip()
            if not t:
                prev = None
                off += len(raw) + 1
                continue
            caps = (t == t.upper() and any(c.isalpha() for c in t))
            if t.startswith(".") and not t.startswith(".."):
                idx = 0                     # forced scene heading
            elif t.upper().startswith(self.SCENE_PREFIXES):
                idx = 0
            elif t.startswith("(") and t.endswith(")"):
                idx = 4
            elif caps and (t.endswith("TO:") or t.startswith("FADE OUT")
                           or t.startswith("FADE TO")):
                # right-aligned transitions only: "FADE IN:" opens a script on
                # the left, which is why Insert treats it as Action too
                idx = 5
            elif caps and len(t) <= 40 and (
                    i + 1 < len(lines) and lines[i + 1].strip()):
                idx = 2                     # cue: caps line with dialogue under it
            elif prev in (2, 3, 4):
                idx = 3                     # dialogue runs to the next blank line
            else:
                idx = 1
            spans.append({"tag": self.EL_TAGS[idx], "start": off,
                          "end": off + len(raw)})
            prev = idx
            off += len(raw) + 1
        return spans

    # Fountain's title page: a block of "Key: value" lines at the very top of
    # the file, closed by a blank line. Only these keys make one — a script
    # that opens "FADE IN:" must not lose its first line to a key-shaped guess.
    TITLE_KEYS = ("title", "credit", "author", "authors", "source", "date",
                  "draft date", "contact", "copyright", "notes", "revision")

    def _title_page_text(self):
        """This script's Fountain title-page block, or "" when the title page
        is still empty. The byline is the credit line of a title page, which is
        what Fountain's Credit key holds."""
        title = self.scripttitle.get_text().strip()
        byline = self.scriptsubtitle.get_text().strip()
        lines = []
        if title and title != DEFAULT_TITLE:
            lines.append("Title: " + title)
        if byline:
            lines.append("Credit: " + byline)
        return "\n".join(lines) + "\n\n" if lines else ""

    def _split_title_page(self, text):
        """Split a plain script into (title, byline, body).

        A leading block of Fountain title-page keys closed by a blank line is
        the title page; anything else is script from its first character. The
        block has to be keys ALL THROUGH or it is not a title page at all, so
        an ordinary script can never lose its opening lines to this."""
        lines = text.split("\n")
        end = 0
        while end < len(lines) and lines[end].strip():
            end += 1
        if end == 0 or end >= len(lines):
            return "", "", text     # no leading block, or nothing closing it
        keys, last = {}, None
        for raw in lines[:end]:
            if last is not None and raw[:1].isspace():
                keys[last] = (keys[last] + " " + raw.strip()).strip()
                continue
            head, sep, value = raw.partition(":")
            key = head.strip().lower()
            if not sep or key not in self.TITLE_KEYS:
                return "", "", text                  # script, not a title page
            keys[key] = value.strip()
            last = key
        # A title page prints its credit line and its author as one line; this
        # app has one byline entry, so join them the way the page reads.
        byline = " ".join(part for part in (
            keys.get("credit", ""),
            keys.get("author", "") or keys.get("authors", "")) if part)
        return keys.get("title", ""), byline.strip(), "\n".join(lines[end + 1:])

    def _write_file(self, path):
        """Serialise the script to `path`. .json keeps the title page and the
        element spans; .fountain/.txt keep the title page in Fountain's own
        title-page keys and the body text, and leave the element spans to be
        read back from the page conventions. Returns True on success."""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            buf = self.body.get_buffer()
            body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            if self._fmt_of(path) == "json":
                data = {
                    "title": self.scripttitle.get_text(),
                    "subtitle": self.scriptsubtitle.get_text(),
                    "body": body,
                    "body_tags": self._serialize_body_tags(buf),
                }
                nbapp.atomic_write_json(path, data, ensure_ascii=False, indent=2)
            else:
                # atomic_write_text, never open(path, "w"): a plain open
                # TRUNCATES the destination before the first new byte arrives,
                # so a .fountain save that could not finish (a full disk, a USB
                # stick pulled mid-write) destroyed the previous draft and left
                # a stump in its place. Temp + fsync + rename leaves either the
                # old script or the new one, never a ruined one.
                # Plain text used to be the body ALONE: the chip said
                # Saved, and the script came back from that file untitled and
                # with no byline. Fountain has title-page keys; use them (see
                # _title_page_text / _split_title_page).
                nbapp.atomic_write_text(path, self._title_page_text() + body)
            return True
        except Exception as exc:
            self._last_file_save_error = exc
            return False

    def _file_save(self):
        """Write to the current file; prompt via Save As if there is none."""
        if not self._path:
            return self._file_save_as()
        if self._write_file(self._path):
            self._file_dirty = False        # in sync with the file on disk now
            self.undo.mark_saved()          # undoing back to here is not "dirty"
            self._save_doc()
            self._update_status()
            self._set_saved()
        else:
            reason = nbapp.save_failure_reason(
                getattr(self, "_last_file_save_error", None), self._path)
            if getattr(getattr(self, "_last_file_save_error", None), "errno", None) == 28:
                reason += " " + _t("Free up space and try again.")
            self._flash(reason)

    def _file_save_as(self):
        """Pick a path and write the script there, then adopt it. A bare name
        defaults to Screenplay .json because it preserves the title and element
        formatting; plain-text (.fountain/.txt) export still works if explicitly
        typed (it keeps the title page but not the element spans, so it
        must not be default). An UNTITLED script takes its title from the chosen
        file name; a title the writer typed is kept."""
        path = self._choose_file(save=True)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"             # default extension preserves formatting
        # Adopt the new file ONLY once its bytes have landed. Taking the path
        # and the title first meant a Save As that could not finish (a full
        # disk, a read-only stick, a folder removed under it) still renamed the
        # script on screen and re-pointed it at a file that does not exist,
        # while the file it was actually bound to was quietly abandoned: the
        # next Ctrl+S then went to the wrong place and "Save failed" was the
        # only sign. The old path and title are put back on failure, so the
        # previous file, the document's identity and its saved point all
        # survive a refused write untouched.
        prev_path = self._path
        prev_title = self.scripttitle.get_text()
        prev_dirty = self._file_dirty
        # The title page belongs to the writer; Save As names a FILE. This used
        # to overwrite the title with the file's stem, upper-cased with its
        # punctuation collapsed to spaces — so saving "Don't Look Up!" under
        # the app's OWN suggested name retitled the script "DON T LOOK UP", on
        # the page and in the file. Deriving the title from the name is right
        # only while the page is still untitled (it dates from the title being
        # a fixed label; see the title block in _build).
        typed = self.scripttitle.get_text().strip()
        self._set_identity(path, self.scripttitle.get_text()
                           if typed and typed != DEFAULT_TITLE
                           else self._title_from_path(path))
        if not self._write_file(path):
            self._set_identity(prev_path, prev_title)
            self._file_dirty = prev_dirty
            self._update_status()
            reason = nbapp.save_failure_reason(
                getattr(self, "_last_file_save_error", None), path)
            if getattr(getattr(self, "_last_file_save_error", None), "errno", None) == 28:
                reason += " " + _t("Free up space and try again.")
            self._flash(reason)
            return
        self._file_dirty = False        # in sync with the file on disk now
        self.undo.mark_saved()
        self._save_doc()
        self._update_status()
        self._set_saved()

    def _set_identity(self, path, title):
        """Point the script at `path` and show `title`, without the title change
        counting as an edit — this is bookkeeping, not something the user
        typed."""
        self._path = path
        was, self._loading = self._loading, True
        try:
            self.scripttitle.set_text(title or "")
        finally:
            self._loading = was

    def _file_open(self):
        path = self._choose_file(save=False)
        if not (path and os.path.isfile(path)):
            return
        # Open replaces the script and overwrites session recovery — guard the
        # same unsaved-work case New does before touching any state.
        self._open_file(path)

    def _choose_file(self, save):
        """Finder-style in-app picker under Documents; return a path or None."""
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
        except Exception:
            pass
        base = os.path.dirname(self._path) if self._path else DOCS_DIR
        start = base if os.path.isdir(base) else DOCS_DIR
        pats = ("*.fountain", "*.txt", "*.json")
        if save:
            suggested = (os.path.basename(self._path) if self._path
                         else self._default_name())
            return nbpicker.save_file(self, title="Save Script As",
                                      start_dir=start, suggested_name=suggested,
                                      patterns=pats, default_ext=".json")
        return nbpicker.open_file(self, title="Open Script",
                                  start_dir=start, patterns=pats)

    def _default_name(self):
        """A neutral filename derived from the title, else 'screenplay.json'.
        Defaults to the lossless .json extension so the prefilled Save-As name
        keeps the title and element formatting unless the user types
        .fountain/.txt."""
        words = "".join(c if c.isalnum() else " "
                        for c in self.scripttitle.get_text()).split()
        return ("-".join(words).lower() or "screenplay") + ".json"

    def _flash(self, text):
        """Surface a transient file-op error in the save chip (crash-safe)."""
        try:
            self.saved.set_markup(
                '<span foreground="#C8341E">● </span>%s' % text)
        except Exception:
            pass

    NOTICE_MARKUP = '<span foreground="#7FA98C">● </span>%s'

    def _notice(self, text):
        """Report a FINISHED action in the save chip, then put the save state
        back a few seconds later.

        Red is this app's failure colour (see _flash), so "Exported PDF" went
        up in the same red as "Not saved" — and sat on top of the save state
        until the next edit, so the writer could no longer see whether the
        script itself was saved. Crash-safe."""
        # Compare against what the widget ENDED UP holding, not against the
        # English constant: set_markup is one of the setters nbi18n rewrites,
        # so on every non-English install the chip held the translated notice
        # while _notice_done tested it against `NOTICE_MARKUP % text` in
        # English. That test never matched, the restore never ran, and the
        # green "Exported PDF" sat on top of the save state for good — exactly
        # the stuck chip this method exists to prevent.
        try:
            previous = self.saved.get_label()
            self.saved.set_markup(self.NOTICE_MARKUP % text)
            self._notice_shown = self.saved.get_label()
        except Exception:
            return
        if self._notice_timer:
            try:
                GLib.source_remove(self._notice_timer)
            except Exception:
                pass
        self._notice_timer = GLib.timeout_add(
            3000, self._notice_done, previous, text)

    def _notice_done(self, previous, text):
        """Restore the save state the notice covered — unless an edit or a save
        has claimed the chip since, in which case that is the truer message."""
        self._notice_timer = None
        if self._closed:
            return False
        try:
            # Anything that has claimed the chip since (an edit, a save) left
            # different markup on it, and that is the truer message.
            if self.saved.get_label() == getattr(self, "_notice_shown", None):
                self.saved.set_markup(previous)
        except Exception:
            pass
        return False

    # ---- PDF / printing (monospace screenplay pages) ----
    def _script_elements(self):
        """The script as a list of (element_index, line_text), one per body line.
        An untagged line reads as Action, the screenplay default."""
        out = []
        buf = self.body.get_buffer()
        table = buf.get_tag_table()
        tags = [table.lookup(n) for n in self.EL_TAGS]
        for ln in range(buf.get_line_count()):
            s = buf.get_iter_at_line(ln)
            e = s.copy()
            if not e.ends_line():
                e.forward_to_line_end()
            text = buf.get_text(s, e, False)
            idx = 1
            for i, tag in enumerate(tags):
                if tag is not None and s.has_tag(tag):
                    idx = i
                    break
            out.append((idx, text))
        return out

    @staticmethod
    def _wrap_text(text, cols):
        """Word-wrap `text` to at most `cols` monospace columns, breaking any
        single over-long word. Always returns at least one line."""
        cols = max(1, int(cols))
        words = text.split()
        if not words:
            return [""]
        lines = []
        cur = ""
        for w in words:
            while len(w) > cols:
                if cur:
                    lines.append(cur); cur = ""
                lines.append(w[:cols]); w = w[cols:]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= cols:
                cur += " " + w
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    def _pdf_lines(self):
        """Flatten the script into physical page lines: (column, text, right)
        tuples, with None for a blank spacer. Scene/Character/Transition set in
        caps; a blank line is inserted before the elements that want the air."""
        rows = []
        prev_blank = True
        speaker = None
        for idx, text in self._script_elements():
            col, width, upper, right = PDF_ELEMENT.get(idx, PDF_ELEMENT[1])
            t = text.strip()
            if not t:
                if not prev_blank:
                    rows.append(None); prev_blank = True
                continue
            if upper and idx in (EL_SCENE, EL_CUE, EL_TRANSITION) \
                    and not prev_blank:
                rows.append(None)          # air before scene / cue / transition
            disp = t.upper() if upper else t
            # Who is speaking, carried onto the dialogue so a speech split by a
            # page can put the name back at the top of the continuation. A cue
            # already carrying (CONT'D) keeps its plain name, or a speech
            # crossing two page breaks would grow a second one.
            if idx == EL_CUE:
                speaker = disp.split("(")[0].strip() or disp
            elif idx not in (EL_DIALOGUE, EL_PAREN):
                speaker = None
            for wl in self._wrap_text(disp, width):
                rows.append(_row(col, wl, right, idx,
                                 speaker if idx in (EL_DIALOGUE, EL_PAREN)
                                 else None))
            prev_blank = False
        return rows

    def _page_rows(self):
        """(pages, lines_per_page) — the one pagination both routes read."""
        usable = nbprint.HALF_H_PT - PDF_MT - PDF_MB
        lpp = max(1, int(usable // PDF_LEAD))
        return paginate_script(self._pdf_lines(), lpp), lpp

    def _build_pages(self):
        """Return (page_count, draw_page) for nbprint. Page 1 is the title page;
        the script flows from page 2. draw_page(cr, page_no, w, h) fills an opaque
        page and lays the monospace script out at half-letter scale."""
        title = (self.scripttitle.get_text() or DEFAULT_TITLE).upper()
        # The byline. It is captured, persisted and restored by undo, and until
        # now it was the one thing on the title page that never reached the
        # page: a writer typed "written by Alexander Hamilton", saw it on
        # screen, and printed a script with no name on it. NOT uppercased — the
        # title is shouted by convention, a byline is not.
        byline = self.scriptsubtitle.get_text().strip()
        pages, _lpp = self._page_rows()
        body_pages = max(1, len(pages))
        page_count = 1 + body_pages

        def draw_page(cr, page_no, w, h):
            # opaque paper — a PDF page must never be left transparent
            cr.set_source_rgb(0.988, 0.984, 0.972)   # #FCFBF8
            cr.rectangle(0, 0, w, h); cr.fill()
            cr.set_source_rgb(0.102, 0.098, 0.086)    # #1A1916 ink
            if page_no <= 1:
                title_cw = _pdf_w(cr, "M", 13.0) or 7.8
                title_cols = max(1, int((w - PDF_ML - PDF_MR) // title_cw))
                title_lines = self._wrap_text(title, title_cols)
                first_y = h * 0.44 - (len(title_lines) - 1) * PDF_LEAD / 2
                for i, line in enumerate(title_lines):
                    _pdf_show(cr, (w - _pdf_w(cr, line, 13.0)) / 2.0,
                              first_y + i * PDF_LEAD, line, 13.0)
                if byline:
                    # 11pt to the title's 13 keeps the printed hierarchy the
                    # same as the on-screen one (15px to 17px). Full ink, not
                    # the muted grey the entry shows: a title page is printed,
                    # and a grey byline reads as a photocopy artefact.
                    _pdf_show(cr, (w - _pdf_w(cr, byline, 11.0)) / 2.0,
                              first_y + (len(title_lines) + 2) * PDF_LEAD,
                              byline, 11.0)
                return
            cw = _pdf_w(cr, "M", PDF_FS) or (PDF_FS * 0.6)
            # body page number, top-right (first body page reads as "1.")
            pn = "%d." % (page_no - 1)
            _pdf_show(cr, w - PDF_MR - _pdf_w(cr, pn, PDF_FS),
                      PDF_MT - PDF_LEAD - 6, pn, PDF_FS)
            y = PDF_MT
            page_rows = pages[page_no - 2] if page_no - 2 < len(pages) else []
            for row in page_rows:
                if row is not None:
                    col, text, right = row[0], row[1], row[2]
                    if right:
                        x = w - PDF_MR - _pdf_w(cr, text, PDF_FS)
                    else:
                        x = PDF_ML + col * cw
                    _pdf_show(cr, x, y, text, PDF_FS)
                y += PDF_LEAD

        return page_count, draw_page

    def _print(self):
        """Print the script one logical page per sheet via the shared dialog."""
        try:
            page_count, draw = self._build_pages()
        except Exception:
            self._flash("Print failed")
            return
        try:
            nbprint.print_document(
                self, lambda p: nbprint.simple_pdf(p, page_count, draw),
                job_name="Screenplay")
        except Exception:
            self._flash("Print failed")

    def _zine_print(self):
        """Impose the script as a 5.5x8.5 saddle-stitch booklet and print it."""
        try:
            page_count, draw = self._build_pages()
        except Exception:
            self._flash("Print failed")
            return
        try:
            nbprint.print_booklet(
                self, lambda p: nbprint.booklet_pdf(p, page_count, draw),
                job_name="Screenplay")
        except Exception:
            self._flash("Print failed")

    def _export_pdf(self):
        """Write the script to a PDF the user picks (default under Documents)."""
        path = self._choose_pdf_path()
        if not path:
            return
        try:
            page_count, draw = self._build_pages()
            nbprint.simple_pdf(path, page_count, draw)
            self._notice("Exported PDF")
        except Exception:
            self._flash("Export failed")

    def _choose_pdf_path(self):
        """Finder-style Save picker for a .pdf under Documents; path or None."""
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
        except Exception:
            pass
        base = os.path.splitext(self._default_name())[0]
        path = nbpicker.save_file(self, title="Export to PDF",
                                  start_dir=DOCS_DIR,
                                  suggested_name=base + ".pdf",
                                  patterns=("*.pdf",), default_ext=".pdf")
        if path and not os.path.splitext(path)[1]:
            path += ".pdf"
        return path

    # -- menu-driven actions --
    def _insert_snippet(self, text, element_idx=None):
        """Insert `text` on its own line at the cursor, optionally tagging that
        line with a screenplay element. Crash-safe."""
        try:
            buf = self.body.get_buffer()
            it = buf.get_iter_at_mark(buf.get_insert())
            self.undo.checkpoint("Insert")
            buf.insert(it, ("" if it.starts_line() else "\n") + text)
            if element_idx is not None and 0 <= element_idx < len(self._elbtns):
                self._on_element(self._elbtns[element_idx], element_idx)
            self.undo.commit()
            self.body.grab_focus()
        except Exception:
            pass

    def _goto(self, where):
        """Move the cursor to the start/end of the script and scroll to it.

        Through the desk's adjustment, not the TextView's (see
        _scroll_to_caret): Go to End used to move the caret to the last line
        and leave the window on the title page. Go to Start goes all the way
        to the top so the title page is what the start of the script shows,
        rather than the minimum move that puts the first line on screen."""
        try:
            buf = self.body.get_buffer()
            it = buf.get_start_iter() if where == "start" else buf.get_end_iter()
            buf.place_cursor(it)
            self.body.grab_focus()
            self._queue_caret_scroll("top" if where == "start" else "caret")
        except Exception:
            pass

    def menu_items(self, name):
        if name == "Edit":
            # Undo/redo lead the menu, as they do in every editor — and they
            # have to be VISIBLE, not just bound to a key nobody can discover.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit")
        if name == "File":
            return [
                ("New    Ctrl+N", self._file_new),
                ("Open…    Ctrl+O", self._file_open),
                nbapp.SEP,
                ("Save    Ctrl+S", self._file_save),
                ("Save As…    Ctrl+Shift+S", self._file_save_as),
                nbapp.SEP,
                ("Print…", self._print),
                ("Export to PDF…", self._export_pdf),
                ("Zine Print…", self._zine_print),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Format":
            # each screenplay element applies its layout to the caret line (or
            # the whole selection); Ctrl+1…6 pick them from the keyboard
            return [("%s    Ctrl+%d" % (el, i + 1),
                     (lambda i=i: self._on_element(self._elbtns[i], i)))
                    for i, el in enumerate(ELEMENTS)]
        if name == "Insert":
            return [
                ("Scene Heading, INT.",
                 lambda: self._insert_snippet("INT. ", 0)),
                ("Scene Heading, EXT.",
                 lambda: self._insert_snippet("EXT. ", 0)),
                nbapp.SEP,
                ("Fade In", lambda: self._insert_snippet("FADE IN:", 1)),
                ("Cut To", lambda: self._insert_snippet("CUT TO:", 5)),
                ("Fade Out", lambda: self._insert_snippet("FADE OUT.", 5)),
                nbapp.SEP,
                ("The End", lambda: self._insert_snippet("THE END", 5)),
            ]
        if name == "View":
            # There is no word-wrap toggle. A screenplay is a fixed sixty-column
            # measure — the page on screen, the PDF and the printer all wrap at
            # it — and switching the body to WrapMode.NONE simply grew the paper
            # to the longest line in the script: the sheet filled the window
            # edge to edge, the desk disappeared, the ends of long lines and the
            # right-aligned transitions fell off the right of the window with no
            # horizontal scrollbar to reach them, and turning wrap back on left
            # the page at its blown width (a TextView's requested width follows
            # its last layout). Long words break inside themselves instead, on
            # screen and in the export alike (see the body's WORD_CHAR wrap).
            return [
                ("Find in Script    Ctrl+F", lambda: self._toggle_find(True)),
                nbapp.SEP,
                ("Go to Start", lambda: self._goto("start")),
                ("Go to End", lambda: self._goto("end")),
            ]
        return super().menu_items(name)

    def _on_key(self, w, ev):
        """The shortcuts a screenwriter reaches for: Ctrl+S save, Ctrl+Shift+S
        Save As, Ctrl+O open, Ctrl+N new, and — while the body is focused —
        Ctrl+1…6 to set the current line's element. Anything else falls through
        to the base (Esc / menu handling, and the TextView's own editing keys)."""
        try:
            # Esc closes the find bar before the base handler closes the app.
            if ev.keyval == Gdk.KEY_Escape and self._findbar.get_visible():
                self._toggle_find(False)
                return True
            # Tab / Enter screenwriting flow — only while editing the body, so
            # neither key is stolen from menu/focus navigation elsewhere.
            if (self.get_focus() is self.body
                    and not (ev.state & Gdk.ModifierType.CONTROL_MASK)):
                kv = ev.keyval
                if kv == Gdk.KEY_Tab:
                    self._cycle_element(1)
                    return True
                if kv == Gdk.KEY_ISO_Left_Tab:   # Shift+Tab
                    self._cycle_element(-1)
                    return True
                if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                    return self._newline(
                        bool(ev.state & Gdk.ModifierType.SHIFT_MASK))
            if ev.state & Gdk.ModifierType.CONTROL_MASK:
                shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
                kv = ev.keyval
                if kv in (Gdk.KEY_s, Gdk.KEY_S):
                    self._file_save_as() if shift else self._file_save()
                    return True
                if kv in (Gdk.KEY_o, Gdk.KEY_O) and not shift:
                    self._file_open()
                    return True
                if kv in (Gdk.KEY_n, Gdk.KEY_N) and not shift:
                    self._file_new()
                    return True
                if kv in (Gdk.KEY_f, Gdk.KEY_F) and not shift:
                    self._toggle_find(True)
                    return True
                # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y
                if nbapp.undo_keys(self.undo, ev):
                    return True
                # element quick-keys, only while editing the body so Ctrl+2 never
                # fires from elsewhere in the chrome
                if self.get_focus() is self.body and not shift:
                    keys = (Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3,
                            Gdk.KEY_4, Gdk.KEY_5, Gdk.KEY_6)
                    kpkeys = (Gdk.KEY_KP_1, Gdk.KEY_KP_2, Gdk.KEY_KP_3,
                              Gdk.KEY_KP_4, Gdk.KEY_KP_5, Gdk.KEY_KP_6)
                    for i in range(len(self._elbtns)):
                        if kv == keys[i] or kv == kpkeys[i]:
                            self._on_element(self._elbtns[i], i)
                            return True
        except Exception:
            pass
        return super()._on_key(w, ev)

    def _install_css(self):
        # Signage-red (#C8341E) appears ONLY as the active-element accent, the
        # editing caret and error chips (alerts) — never decorative. The
        # selected element chip uses a darker-beige chrome, not black. Chrome is
        # Nimbus Sans; the script body itself is fixed-pitch Courier, as
        # screenplay pages require. Papertone surfaces throughout.
        css = b"""
        .formatbar { background: #F4F2EC; border-bottom: 1px solid #D7D2C5;
                     padding: 0 36px; min-height: 54px; }
        .formatbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .elementlabel { font-size: 11px; letter-spacing: 0.12em; color: #6E695E;
                        font-weight: 700; }
        .elbtn { min-height: 30px; padding: 0 13px; font-size: 13px;
                 font-weight: 500; color: #1A1916; background: #FCFBF8;
                 border: 1px solid #D7D2C5; border-radius: 8px;
                 box-shadow: none; }
        .elbtn:hover { background: #F1EEE6; }
        .elbtn.active { color: #1A1916; background: #EAE3D2;
                        border: 1px solid #C9C4B6; font-weight: 700;
                        box-shadow: inset 0 -3px 0 #C8341E; }
        /* The button's own label node needs the accent too: the theme's
           `* { color: ink }` matches it directly and so beats the colour it
           would otherwise inherit from the button, which silently dropped the
           red from the selected element chip. */
        .elbtn.active label { color: #1A1916; }
        .elbtn.active:hover { background: #EAE3D2; }
        .meta, .savestate { font-size: 13px; color: #6E695E; }
        .msep { color: #D7D2C5; min-width: 1px; margin: 15px 0; }
        .findbar { background: #EFEBE0; border-bottom: 1px solid #D7D2C5;
                   padding: 8px 36px; }
        .findbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .findinput { background: #FCFBF8; border: 1px solid #C9C4B6;
                     border-radius: 8px; box-shadow: none; color: #1A1916;
                     font-size: 13px; min-height: 30px; }
        .findinput:focus { border: 1px solid #8A857A; }
        .findbtn { min-height: 30px; padding: 0 12px; font-size: 13px;
                   color: #2A2620; background: #FCFBF8;
                   border: 1px solid #D7D2C5; border-radius: 8px;
                   box-shadow: none; }
        .findbtn:hover { background: #EFEBE0; }
        .findcount { font-size: 13px; color: #6E695E; }
        .desk { background: #DED4C2; }
        .desk viewport { background: #DED4C2; }
        .page { background: #FCFBF8; border: 1px solid #D7D2C5;
                box-shadow: 2px 3px 0 rgba(26,25,22,0.10); }
        .pageno { font-family: "Courier New","Liberation Mono",monospace; font-size: 15px;
                  color: #3A362E; }
        .scripttitle { font-family: "Courier New","Liberation Mono",monospace; font-size: 17px;
                       font-weight: 700; letter-spacing: 0.04em; color: #1A1916; }
        .scripttitle { background: transparent; border: none; box-shadow: none;
                       padding: 0; caret-color: #C8341E; }
        .scriptsubtitle { font-family: "Courier New","Liberation Mono",monospace;
                       font-size: 15px; color: #6E695E; background: transparent;
                       border: none; box-shadow: none; padding: 0;
                       caret-color: #C8341E; }
        .scriptsub { font-family: "Courier New","Liberation Mono",monospace; font-size: 15px;
                     color: #6E695E; }
        .scriptbody { font-family: "Courier New","Liberation Mono",monospace; font-size: 16px;
                      color: #1A1916; background: #FCFBF8; caret-color: #C8341E; }
        .scriptbody text { background: #FCFBF8; }
        .scriptbody text selection { background-color: #EAE3D2; color: #1A1916; }
        /* discard confirmation: a papertone card, matching the OS pattern */
        .spdlg { background: #FCFBF8; border: 1px solid #C9C4B6; }
        .spdlgbox { padding: 24px 28px 20px; }
        .spdlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .spdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
                      font-size: 20px; font-weight: 600; color: #1A1916; }
        .spdlgmsg { font-size: 13px; color: #6E695E; }
        .spdlgcancel { font-size: 13px; color: #2A2620; padding: 6px 16px;
                       background: #FCFBF8; border: 1px solid #C9C4B6;
                       border-radius: 8px; box-shadow: none; }
        .spdlgcancel:hover { background: #EFEBE0; }
        .spdlgok { font-size: 13px; padding: 6px 16px; background: #C8341E;
                   color: #FCFBF8; border: 1px solid #C8341E;
                   border-radius: 8px; box-shadow: none; font-weight: 600; }
        .spdlgok label { color: #FCFBF8; }
        .spdlgok:hover { background: #B12D19; border-color: #B12D19; }
        """
        # A CSS parse failure must never abort window construction — degrade to
        # the app's default (nbapp) styling instead of crashing on launch.
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass


if __name__ == "__main__":
    nbapp.run(Screenplay)
