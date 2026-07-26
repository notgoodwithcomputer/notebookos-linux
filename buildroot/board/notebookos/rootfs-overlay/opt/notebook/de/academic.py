#!/usr/bin/env python3
"""
Academic Notes — Notebook OS lecture-note editor (native GTK).

Two panes: a sidebar listing classes and their numbered lectures, and a note
canvas with a format bar (Style, B / I / highlight, bullet / number lists) plus
a live word count and save state. The model is a flat list of classes, each
holding numbered lectures — there is no semester layer.

Ships empty: no classes, no lectures. File ▸ New Lecture creates the first
class and lecture. The app auto-persists the whole notebook to a JSON file on
every edit; the File menu exports the active lecture to a PDF under
$NB_HOME/Documents (it does no file-based document management).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import time
import cairo

import nbapp
import nbicons
import nbprint
from nbi18n import _t  # noqa: E402

CLASS_COLORS = ["#9A7B4F", "#4A5E73", "#6E7B57", "#8A6D5B", "#566E86"]

# Persistence: the classes and every lecture (titles + notes + formatting) live
# in one JSON file under the shared Notebook config dir so the session survives
# app close / reboot. Missing/invalid file -> open empty, exactly as a fresh
# install does. This JSON is the sole source of truth, rewritten on every edit;
# there is no file-based document management.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
ACADEMIC_FILE = os.path.join(CFG_DIR, "academic.json")
# File ▸ Export to PDF writes rendered lectures here.
DOCS_DIR = os.path.join(HOME, "Documents")


class AcademicNotes(nbapp.AppWindow):
    app_name = "Academic Notes"
    menus = ("File", "Edit", "Format", "Insert", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        self.classes = []      # {label, color}
        self.lectures = []     # {cls, num, title, date, meta, notes, ranges}
        self.active = -1
        # Live handle to the active lecture row's title Gtk.Label, captured on
        # every sidebar rebuild so per-keystroke title edits update it in place
        # instead of triggering a full sidebar rebuild.
        self._active_title_label = None
        self._save_timer = None
        # Debounce for the per-keystroke note sync + live word count, so typing
        # a long note doesn't re-serialize the whole buffer twice per keypress.
        self._notes_timer = None

        # Load any saved notebook BEFORE building the UI from the model, so the
        # sidebar / canvas render the restored classes and lectures. On a fresh
        # install (no file) this leaves the empty default untouched.
        self._load_from_disk()

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._build_sidebar(), False, False, 0)
        body.pack_start(self._build_editor(), True, True, 0)

        self._refresh_sidebar()
        self._refresh_canvas()
        # Undo/redo over the whole notebook, not the open note: deleting a
        # lecture takes its class with it when it was the last one, and that is
        # the operation that actually loses a term of notes. Built here so its
        # baseline is the notebook as restored. See nbapp.UndoHistory.
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()

        # Flush the final edit when the window closes so nothing is lost.
        self.connect("destroy", self._on_destroy)

    # ---------------- sidebar ----------------
    def _build_sidebar(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_size_request(340, -1)
        col.get_style_context().add_class("sidebar")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("side-head")
        eyebrow = Gtk.Label(label=_t("CLASSES"), xalign=0)
        eyebrow.get_style_context().add_class("side-eyebrow")
        head.pack_start(eyebrow, False, False, 0)
        # Model summary (class/lecture counts), updated on every sidebar refresh
        # in place of the removed semester label.
        self.side_summary = Gtk.Label(label="", xalign=0)
        self.side_summary.get_style_context().add_class("side-term")
        head.pack_start(self.side_summary, False, False, 0)

        # Search. Notes taken across a whole term run to dozens of lectures in
        # several classes; the only way back to the one that covered eigenvalues
        # was to open them one at a time. This filters the list by class name,
        # lecture title AND note text. Hidden until there is something to search.
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(_t("Search notes"))
        self.search.get_style_context().add_class("acsearch")
        self.search.set_no_show_all(True)     # driven by hand (_refresh_sidebar)
        self.search.connect("search-changed", self._on_search)
        self.search.connect("activate", lambda *_: self._first_match())
        self._query = ""
        self._filter_timer = None
        head.pack_start(self.search, False, False, 0)
        col.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.side_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.side_list.get_style_context().add_class("side-list")
        scroll.add(self.side_list)
        col.pack_start(scroll, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        foot.get_style_context().add_class("side-foot")
        newbtn = Gtk.Button()
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("newlecture")
        newbtn.set_tooltip_text(_t("New Lecture"))
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        inner.set_halign(Gtk.Align.CENTER)
        inner.pack_start(
            Gtk.Image.new_from_pixbuf(nbicons.pixbuf("plus", 16, "#1A1916")),
            False, False, 0)
        inner.pack_start(Gtk.Label(label=_t("New Lecture")), False, False, 0)
        newbtn.add(inner)
        newbtn.connect("clicked", lambda *_: self._new_lecture())
        foot.pack_start(newbtn, False, False, 0)
        col.pack_start(foot, False, False, 0)
        return col

    def _refresh_sidebar(self):
        # Drop any stale label handle; it's re-captured below for the row that
        # matches self.active, keeping the in-place title update in sync with
        # the model across new/delete/select/rename/reorder.
        self._active_title_label = None
        for c in self.side_list.get_children():
            self.side_list.remove(c)

        nc, nl = len(self.classes), len(self.lectures)
        self.side_summary.set_text(
            _t("No classes yet") if not nl else
            "%d class%s · %d lecture%s"
            % (nc, "" if nc == 1 else "es", nl, "" if nl == 1 else "s"))
        # Only worth showing once there is a notebook to search.
        self.search.set_visible(bool(self.lectures))

        if not self.classes:
            # The header above already says there are no classes, so this line
            # explains what the pane is for rather than repeating it.
            empty = Gtk.Label(
                label=_t("Your classes and their lectures appear here."))
            empty.set_line_wrap(True)
            empty.get_style_context().add_class("side-empty")
            self.side_list.pack_start(empty, False, False, 0)
            self.side_list.show_all()
            return

        keep = self._match_lectures()
        if keep is not None:
            if not keep:
                empty = Gtk.Label(
                    label=_t("No note matches “%s”") % self._query)
                empty.set_line_wrap(True)
                empty.set_max_width_chars(26)
                empty.get_style_context().add_class("side-empty")
                self.side_list.pack_start(empty, False, False, 0)
                self.side_list.show_all()
                return
            n = len(keep)
            cnt = Gtk.Label(label=(_t("1 lecture found") if n == 1
                                   else _t("%d lectures found") % n), xalign=0)
            cnt.get_style_context().add_class("side-count")
            self.side_list.pack_start(cnt, False, False, 0)

        for ci, cl in enumerate(self.classes):
            if keep is not None and not any(
                    l["cls"] == ci and i in keep
                    for i, l in enumerate(self.lectures)):
                continue          # this class has no matching lecture
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            hdr.get_style_context().add_class("cls-head")
            # Per-class colour swatch (the darker class palette, never the
            # signage red), matching the mockup's class header marker.
            sw = Gtk.DrawingArea()
            sw.set_size_request(11, 11)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("draw", self._swatch_draw, cl["color"])
            hdr.pack_start(sw, False, False, 0)
            lbl = Gtk.Label(label=cl["label"].upper(), xalign=0)
            lbl.get_style_context().add_class("cls-label")
            # A long class name is trimmed rather than allowed to stretch the
            # sidebar (which would push the whole window past the screen edge).
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(28)
            hdr.pack_start(lbl, True, True, 0)
            self.side_list.pack_start(hdr, False, False, 0)

            for li, lec in enumerate(self.lectures):
                if lec["cls"] != ci or (keep is not None and li not in keep):
                    continue
                self.side_list.pack_start(self._lecture_row(li, lec), False,
                                          False, 0)
        self.side_list.show_all()

    # ---------------- search ----------------
    def _match_lectures(self):
        """The set of lecture indices the search text matches, or None when no
        search is active (meaning: show everything)."""
        if not self._query:
            return None
        q = self._query.lower()
        keep = set()
        for i, lec in enumerate(self.lectures):
            cls = lec.get("cls", 0)
            label = (self.classes[cls].get("label", "")
                     if 0 <= cls < len(self.classes) else "")
            hay = "%s %s %s" % (label, lec.get("title", ""), lec.get("notes", ""))
            if q in hay.lower():
                keep.add(i)
        return keep

    def _on_search(self, _entry):
        """Filter the lecture list as the search text is typed, debounced so a
        big notebook is not rebuilt on every keystroke."""
        if self._filter_timer:
            GLib.source_remove(self._filter_timer)
        self._filter_timer = GLib.timeout_add(120, self._filter_tick)

    def _filter_tick(self):
        self._filter_timer = None
        # Read the field HERE rather than in the signal handler, so the filter
        # is whatever is actually in the box at the moment it is applied.
        self._query = self.search.get_text().strip()
        # Pull the live buffer into the model first, so words typed a moment ago
        # are searchable.
        self._capture_active()
        self._refresh_sidebar()
        return False

    def _first_match(self):
        """Enter in the search field opens the first lecture that matched."""
        if self._filter_timer:
            GLib.source_remove(self._filter_timer)
        self._filter_tick()
        keep = self._match_lectures()
        if keep:
            for i in self._display_order():
                if i in keep:
                    self._select(i)
                    break
        self._focus_note()

    def _focus_search(self):
        """Ctrl+F / View ▸ Search Notes — put the caret in the search field."""
        if self.lectures:
            self.search.grab_focus()

    def _clear_search(self):
        """Drop the filter and show the whole notebook again."""
        if not self._query and not self.search.get_text():
            return False
        self.search.set_text("")
        self._query = ""
        self._refresh_sidebar()
        return True

    def _lecture_row(self, index, lec):
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("lec-row")
        if index == self.active:
            row.get_style_context().add_class("active")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)

        num = Gtk.Label(label=lec["num"])
        num.get_style_context().add_class("lec-num")
        if index == self.active:
            num.get_style_context().add_class("active")
        num.set_valign(Gtk.Align.CENTER)
        box.pack_start(num, False, False, 0)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=lec["title"] or "Untitled Lecture", xalign=0)
        title.get_style_context().add_class("lec-title")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        if index == self.active:
            # Remember this label so _on_title_changed can update it in place.
            self._active_title_label = title
        txt.pack_start(title, False, False, 0)
        date = Gtk.Label(label=lec["date"], xalign=0)
        date.get_style_context().add_class("lec-date")
        txt.pack_start(date, False, False, 0)
        box.pack_start(txt, True, True, 0)

        row.add(box)
        row.connect("clicked", lambda *_a, i=index: self._select(i))
        return row

    def _swatch_draw(self, area, cr, color):
        # Never let a malformed colour (e.g. a hand-edited JSON) raise inside a
        # draw callback; fall back to the first class colour instead.
        try:
            r, g, b = nbicons._hex(color)
        except Exception:
            r, g, b = nbicons._hex(CLASS_COLORS[0])
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, area.get_allocated_width(),
                     area.get_allocated_height())
        cr.fill()
        return False

    # ---------------- editor ----------------
    def _build_editor(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("editor")

        fbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        fbar.get_style_context().add_class("formatbar")

        # Refs to every format control so the empty state (no lecture open) can
        # grey them out — there is nothing to format on a blank canvas.
        self._fmt_btns = []
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        stylebtn = Gtk.Button()
        stylebtn.set_relief(Gtk.ReliefStyle.NONE)
        stylebtn.get_style_context().add_class("stylebtn")
        stylebtn.set_tooltip_text(
            "Paragraph style — click to cycle Body, Heading, Subheading")
        sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        self.stylelbl = Gtk.Label(label=_t("Body"))
        sb.pack_start(self.stylelbl, False, False, 0)
        car = Gtk.Label(label="▾")
        car.get_style_context().add_class("caret")
        sb.pack_start(car, False, False, 0)
        stylebtn.add(sb)
        stylebtn.connect("clicked", lambda *_: self._cycle_style())
        left.pack_start(stylebtn, False, False, 0)
        self._fmt_btns.append(stylebtn)
        left.pack_start(self._sep(), False, False, 10)

        b = self._txtbtn("B", "bold")
        b.set_tooltip_text(_t("Bold (Ctrl+B)"))
        b.connect("clicked", lambda *_: self._toggle_tag("bold"))
        left.pack_start(b, False, False, 0)
        self._fmt_btns.append(b)
        i = self._txtbtn("I", "ital")
        i.set_tooltip_text(_t("Italic (Ctrl+I)"))
        i.connect("clicked", lambda *_: self._toggle_tag("italic"))
        left.pack_start(i, False, False, 0)
        self._fmt_btns.append(i)
        hi = self._iconbtn("highlight")
        hi.set_tooltip_text(_t("Highlight"))
        hi.connect("clicked", lambda *_: self._toggle_tag("highlight"))
        left.pack_start(hi, False, False, 0)
        self._fmt_btns.append(hi)
        left.pack_start(self._sep(), False, False, 10)
        blt = self._iconbtn("bullet")
        blt.set_tooltip_text(_t("Bullet list"))
        blt.connect("clicked", lambda *_: self._insert_list("• "))
        left.pack_start(blt, False, False, 0)
        self._fmt_btns.append(blt)
        num = self._iconbtn("number")
        num.set_tooltip_text(_t("Numbered list"))
        num.connect("clicked", lambda *_: self._insert_list("1. "))
        left.pack_start(num, False, False, 0)
        self._fmt_btns.append(num)
        fbar.pack_start(left, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.wordlbl = Gtk.Label(label=_t("0 words"))
        self.wordlbl.set_tooltip_text(_t("Words in this lecture"))
        self.wordlbl.get_style_context().add_class("wordcount")
        right.pack_end(self._make_savebox(), False, False, 0)
        right.pack_end(self._sep(), False, False, 0)
        right.pack_end(self.wordlbl, False, False, 0)
        fbar.pack_end(right, False, False, 0)
        col.pack_start(fbar, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("canvaswrap")
        # The paper surface must SPAN the scroller, not just sit under the note
        # column: a centred canvas leaves the viewport's own bin-window exposed
        # either side, and that window is native — with a TextView inside it, it
        # is never repainted and comes up solid BLACK on this no-compositor
        # stack (the Writer bug). So the canvas fills the width and paints the
        # paper, and the note itself lives in a centred column inside it.
        self.canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas.set_halign(Gtk.Align.FILL)
        self.canvas.get_style_context().add_class("canvas")
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.column.set_halign(Gtk.Align.CENTER)
        # Start at the NARROW measure and widen to COLUMN_W once we know how
        # much room the editor really has (below). Requesting the full 720 up
        # front would make it the window's minimum width, which is what pushed
        # the app past a 1024-wide panel; a request can only ever be grown into
        # space that exists.
        self.column.set_size_request(self.COLUMN_MIN_W, -1)
        self.canvas.pack_start(self.column, True, True, 0)
        self._column_w = self.COLUMN_MIN_W
        scroll.connect("size-allocate", self._on_canvas_alloc)
        scroll.add(self.canvas)
        col.pack_start(scroll, True, True, 0)
        return col

    # Ideal note measure, and the narrowest it may be squeezed to on a small
    # panel before the window would otherwise overflow the screen.
    COLUMN_W = 720
    COLUMN_MIN_W = 460

    def _on_canvas_alloc(self, scroll, alloc):
        """Re-fit the note column to the editor's width (window resize)."""
        # Everything around the column inside the scroller — the canvas padding,
        # the scrollbar, any frame — measured rather than guessed, so the column
        # lands exactly inside the space that exists instead of creeping a few
        # pixels wider on every allocation.
        chrome = max(0, scroll.get_preferred_width()[0] - self._column_w)
        want = max(self.COLUMN_MIN_W,
                   min(self.COLUMN_W, alloc.width - chrome))
        if want != self._column_w:
            self._column_w = want
            self.column.set_size_request(want, -1)

    def _make_savebox(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_tooltip_text(_t("Your notes are saved automatically"))
        self.savedot = Gtk.DrawingArea()
        self.savedot.set_size_request(8, 8)
        self.savedot.set_valign(Gtk.Align.CENTER)
        self._saved = True
        self.savedot.connect("draw", self._draw_savedot)
        box.pack_start(self.savedot, False, False, 0)
        self.savelbl = Gtk.Label(label=_t("Saved %s") % time.strftime("%H:%M"))
        self.savelbl.get_style_context().add_class("savestate")
        box.pack_start(self.savelbl, False, False, 0)
        return box

    def _draw_savedot(self, area, cr):
        # No compositor: paint the whole widget with the opaque formatbar
        # surface first, or the disc's corners render solid black on real HW.
        r, g, b = nbicons._hex("#FCFBF8")
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, area.get_allocated_width(),
                     area.get_allocated_height())
        cr.fill()
        color = "#7FA98C" if self._saved else "#C8341E"
        r, g, b = nbicons._hex(color)
        cr.set_source_rgb(r, g, b)
        cr.arc(4, 4, 4, 0, 2 * 3.14159265)
        cr.fill()
        return False

    def _refresh_canvas(self):
        for c in self.column.get_children():
            self.column.remove(c)

        if self.active < 0 or not self.lectures:
            # Nothing open: grey the format bar and reset the live indicators so
            # no stale word count / style label lingers from a deleted lecture.
            self._set_fmt_sensitive(False)
            self.stylelbl.set_text("Body")
            self.wordlbl.set_text("0 words")
            wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            wrap.get_style_context().add_class("empty-wrap")
            t = Gtk.Label(label=_t("No lectures yet"))
            t.get_style_context().add_class("empty-title")
            wrap.pack_start(t, False, False, 0)
            s = Gtk.Label(
                label=_t("Start one and type as the lecture goes — your notes "
                         "are saved as you write."))
            s.set_line_wrap(True)
            s.set_max_width_chars(46)
            s.get_style_context().add_class("empty-sub")
            wrap.pack_start(s, False, False, 0)
            # The action itself, on the pane the reader is looking at: the only
            # way in used to be a button in the far corner of the other pane.
            b = Gtk.Button(label=_t("New Lecture"))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_halign(Gtk.Align.CENTER)
            b.get_style_context().add_class("emptybtn")
            b.connect("clicked", lambda *_: self._new_lecture())
            wrap.pack_start(b, False, False, 0)
            self.column.pack_start(wrap, False, False, 0)
            self.column.show_all()
            return
        self._set_fmt_sensitive(True)

        lec = self.lectures[self.active]
        cl = self.classes[lec["cls"]]

        eb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        eb.get_style_context().add_class("canvas-eyebrow-row")
        sw = Gtk.DrawingArea()
        sw.set_size_request(11, 11)
        sw.set_valign(Gtk.Align.CENTER)
        # The eyebrow marker is the class colour (as in the mockup), NOT the
        # signage red — red is reserved for the active/alert states only.
        sw.connect("draw", self._swatch_draw, cl["color"])
        eb.pack_start(sw, False, False, 0)
        eyebrow = Gtk.Label(
            label=("%s · Lecture %s" % (cl["label"], lec["num"])).upper(),
            xalign=0)
        eyebrow.get_style_context().add_class("canvas-eyebrow")
        # A long class name must not be able to widen the note column (and with
        # it the whole window) — let the eyebrow take the row's width and trim
        # to whatever actually fits.
        eyebrow.set_ellipsize(Pango.EllipsizeMode.END)
        eyebrow.set_max_width_chars(48)
        eb.pack_start(eyebrow, True, True, 0)
        self.column.pack_start(eb, False, False, 0)

        # Title: a read/edit pair, not a bare Gtk.Entry. An Entry cannot wrap, so
        # a real lecture title ("Thermodynamics II — the Clausius inequality")
        # ran off the end of the 40px serif field and could never be read back
        # in full; the writer had to arrow through their own heading. The label
        # wraps and shows all of it; clicking swaps in the entry to edit.
        self.title_lbl = Gtk.Label(xalign=0)
        self.title_lbl.set_line_wrap(True)
        self.title_lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title_lbl.get_style_context().add_class("doctitle")
        self.title_ev = Gtk.EventBox()
        self.title_ev.get_style_context().add_class("doctitlebtn")
        self.title_ev.set_tooltip_text(_t("Click to rename this lecture"))
        self.title_ev.add(self.title_lbl)
        self.title_ev.connect("button-press-event",
                              lambda *_a: (self._focus_title(), True)[1])

        self.title = Gtk.Entry()
        self.title.set_has_frame(False)
        self.title.set_placeholder_text(_t("Lecture title"))
        self.title.get_style_context().add_class("doctitle")
        self.title.set_text(lec["title"])
        self.title.connect("changed", self._on_title_changed)
        # Enter in the title jumps to the note body, so naming a lecture flows
        # straight into typing it up.
        self.title.connect("activate", lambda *_: self._focus_note())
        # Leaving the field goes back to the wrapped, fully readable heading.
        self.title.connect("focus-out-event",
                           lambda *_a: (self._show_title_label(), False)[1])
        self.title.set_no_show_all(True)
        self.column.pack_start(self.title_ev, False, False, 0)
        self.column.pack_start(self.title, False, False, 0)
        self._show_title_label()

        meta = Gtk.Label(label=lec["meta"], xalign=0)
        meta.get_style_context().add_class("canvas-meta")
        self.column.pack_start(meta, False, False, 0)

        self.body = Gtk.TextView()
        self.body.set_wrap_mode(Gtk.WrapMode.WORD)
        self.body.get_style_context().add_class("docbody")
        self.body.set_pixels_below_lines(9)
        self.body.set_pixels_inside_wrap(8)
        # Height only: the note takes its width from the (adaptive) column, and
        # a hard 720 here would pin the window's minimum width above 1024.
        self.body.set_size_request(-1, 460)
        buf = self.body.get_buffer()
        buf.set_text(lec["notes"])
        buf.create_tag("bold", weight=Pango.Weight.BOLD)
        buf.create_tag("italic", style=Pango.Style.ITALIC)
        buf.create_tag("highlight", background="#F0E2C0")
        buf.create_tag("heading", weight=Pango.Weight.BOLD, scale=1.6)
        buf.create_tag("subheading", weight=Pango.Weight.BOLD, scale=1.22)
        # Re-apply the lecture's saved formatting spans so bold/italic/highlight/
        # heading survive switching lectures (before, only plain text restored).
        self._apply_ranges(buf, lec.get("ranges"))
        buf.connect("changed", self._on_notes_changed)
        # Track the caret so the Style indicator always names the paragraph style
        # at the cursor (matching the Writer / Novel toolbars).
        buf.connect("mark-set", self._on_mark_set)
        self._sync_style_label()
        self.column.pack_start(self.body, True, True, 0)

        self.column.show_all()
        self._recount()

    # ---------------- rich-text ranges ----------------
    # Formatting is stored per lecture as {tag_name: [[start_off, end_off], ...]}
    # so bold / italic / highlight / heading / subheading survive lecture
    # switches and disk saves — previously only plain "notes" was synced, so
    # every tag was dropped the moment you left a lecture and came back.
    _RANGE_TAGS = ("bold", "italic", "highlight", "heading", "subheading")

    def _capture_ranges(self):
        """Snapshot the live note buffer's tag spans into the active lecture."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        tbl = buf.get_tag_table()
        end_off = buf.get_char_count()
        ranges = {}
        for name in self._RANGE_TAGS:
            tag = tbl.lookup(name)
            if tag is None:
                continue
            spans = []
            it = buf.get_start_iter()
            # A span open at offset 0 has no preceding toggle, so seed from has_tag.
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
        self.lectures[self.active]["ranges"] = ranges

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

    # ---------------- actions ----------------
    def _append_class(self):
        """Append a new untitled class and return its index."""
        color = CLASS_COLORS[len(self.classes) % len(CLASS_COLORS)]
        self.classes.append(
            {"label": "Untitled Class %d" % (len(self.classes) + 1),
             "color": color})
        return len(self.classes) - 1

    def _next_num(self, cls):
        """Next zero-padded lecture number for class index `cls`.

        Parse each lecture's `num` independently: a single non-numeric value
        (a hand-edited / foreign academic.json can persist any string into
        'num') must not discard the rest, or the next lecture gets numbered
        '01' again and silently duplicates an existing number.
        """
        nums = []
        for l in self.lectures:
            if l["cls"] != cls:
                continue
            try:
                nums.append(int(l["num"]))
            except (TypeError, ValueError):
                continue
        return "%02d" % (max(nums) + 1 if nums else 1)

    def _blank_lecture(self, cls=0, num="01"):
        """A fresh lecture dict with no notes and no formatting."""
        return {
            "cls": cls, "num": num, "title": "Lecture " + num,
            "date": self._short_date(),
            "meta": self._long_date() + " · added " + time.strftime("%H:%M"),
            "notes": "", "ranges": {}}

    def _new_lecture(self):
        if not self.classes:
            self._new_class()
            return
        self.undo.checkpoint("New Lecture")
        cls = self.lectures[self.active]["cls"] if self.active >= 0 else 0
        # Flush the outgoing lecture's note text + formatting before we switch.
        self._capture_active()
        # A blank lecture matches no search, so drop the filter first or the row
        # about to be created would not be in the list at all.
        self._clear_search()
        self.lectures.append(self._blank_lecture(cls, self._next_num(cls)))
        self.active = len(self.lectures) - 1
        self._refresh_sidebar()
        self._refresh_canvas()
        self.undo.commit()
        # Land the cursor in the title so she can name the new lecture at once.
        self._focus_title()

    def _new_class(self):
        # Flush the outgoing lecture's note text + formatting before we switch.
        self._capture_active()
        self.undo.checkpoint("New Class")
        self._clear_search()
        ci = self._append_class()
        self.lectures.append(self._blank_lecture(ci, "01"))
        self.active = len(self.lectures) - 1
        self._refresh_sidebar()
        self._refresh_canvas()
        self.undo.commit()
        self._focus_title()

    def _rename_class(self):
        # Rename the active class — it was frozen at "Untitled Class N" with no
        # way to change it (only lecture titles were editable).
        if self.active < 0 or not self.lectures:
            return
        cl = self.classes[self.lectures[self.active]["cls"]]
        dlg = self._dialog_shell("Rename Class")
        entry = Gtk.Entry()
        entry.set_text(cl.get("label", ""))
        entry.set_activates_default(True)
        entry.set_size_request(280, -1)
        entry.get_style_context().add_class("acdlgentry")
        dlg._box.pack_start(entry, False, False, 0)
        self._dialog_buttons(dlg, "Rename", destructive=False)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        entry.grab_focus()
        if dlg.run() == Gtk.ResponseType.OK:
            name = entry.get_text().strip()
            if name:
                cl["label"] = name
                self._refresh_sidebar()
                # Flush the live buffer (note text + tag ranges) before the
                # canvas rebuilds; otherwise just-applied edits are dropped.
                self._capture_active()
                self._refresh_canvas()
                try:
                    self._save_to_disk()
                except Exception:
                    pass
        dlg.destroy()

    def _dialog_shell(self, title):
        """An undecorated papertone dialog card with a heading — the pattern the
        rest of the OS uses (journal, cookbook), in place of a stock GTK dialog
        wearing a window-manager title bar. Content goes in dlg._box."""
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("acdlg")
        area = dlg.get_content_area()
        area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.get_style_context().add_class("acdlgbox")
        hd = Gtk.Label(label=_t(title), xalign=0)
        hd.get_style_context().add_class("acdlgtitle")
        box.pack_start(hd, False, False, 0)
        area.add(box)
        dlg._box = box
        return dlg

    def _dialog_buttons(self, dlg, ok_label, destructive=True):
        """Cancel + <ok_label> row for a _dialog_shell card. A destructive
        action takes the signage red; an ordinary primary (Rename) takes dark
        ink — red is reserved for alerts. Cancel always keeps the focus so a
        stray Return is safe."""
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("acdlgcancel")
        cancel.connect("clicked",
                       lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        ok = Gtk.Button(label=_t(ok_label))
        ok.get_style_context().add_class(
            "acdlgok" if destructive else "acdlgprimary")
        ok.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        dlg._box.pack_start(btns, False, False, 0)
        dlg._cancel = cancel
        return ok

    def _confirm(self, heading, detail):
        """Modal warning confirm; returns True only if the user chose Delete.
        The default response is Cancel so an accidental Enter never deletes."""
        dlg = self._dialog_shell(heading)
        msg = Gtk.Label(label=detail, xalign=0)
        msg.set_line_wrap(True)
        # width-chars sets the card's measure (max-width-chars alone only caps
        # it, leaving GTK free to size the dialog to a cramped ~25 characters).
        msg.set_width_chars(38)
        msg.set_max_width_chars(40)
        msg.get_style_context().add_class("acdlgmsg")
        dlg._box.pack_start(msg, False, False, 0)
        self._dialog_buttons(dlg, "Delete")
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        dlg.show_all()
        dlg._cancel.grab_focus()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _delete_lecture(self):
        """Remove the active lecture after a confirm, then re-point the
        selection at the nearest remaining lecture (or the empty state)."""
        if not (0 <= self.active < len(self.lectures)):
            return
        lec = self.lectures[self.active]
        cl = self.classes[lec["cls"]]
        # If this is the class's only lecture, deleting it empties the class —
        # and an empty class can't hold or gain lectures, so it's removed too.
        # Say so up front rather than let a ghost class header appear.
        last_in_class = sum(1 for l in self.lectures
                            if l["cls"] == lec["cls"]) == 1
        # Undoable now, and the confirm says so: a warning that a delete is
        # permanent sends a student looking for a backup that does not exist.
        # Wrapped in _t() as well — the catalogs have carried these sentences
        # all along, and nothing ever applied them.
        if last_in_class:
            detail = (_t("“%s” (Lecture %s) is the only lecture in %s, so the "
                         "class is removed too. You can undo this with Ctrl+Z.")
                      % (lec.get("title") or "Untitled Lecture",
                         lec.get("num", ""), cl.get("label", "")))
        else:
            detail = (_t("“%s” (Lecture %s · %s) will be removed. You can undo "
                         "this with Ctrl+Z.")
                      % (lec.get("title") or "Untitled Lecture",
                         lec.get("num", ""), cl.get("label", "")))
        if not self._confirm("Delete this lecture?", detail):
            return
        self.undo.checkpoint("Delete Lecture…")
        # The outgoing lecture is being discarded, so just drop any pending
        # debounce rather than flushing it back into a row we're deleting.
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
            self._notes_timer = None
        del self.lectures[self.active]
        # Drop any class this left with no lectures (reindexes cls fields) so no
        # stranded, un-addable class header lingers in the sidebar.
        self._prune_empty_classes()
        # Deleting shifts every later lecture down one, so the old index now
        # points at the following lecture; clamp into range (-1 when empty).
        self.active = min(self.active, len(self.lectures) - 1)
        self._refresh_sidebar()
        self._refresh_canvas()
        try:
            self._save_to_disk()
        except Exception:
            pass
        self.undo.commit()

    def _prune_empty_classes(self):
        """Remove any class that no longer owns a lecture and remap the
        remaining lectures' `cls` indices, keeping the invariant that every
        class holds at least one lecture (so the sidebar shows no dead headers)."""
        used = {l["cls"] for l in self.lectures}
        if len(used) == len(self.classes):
            return
        keep = [ci for ci in range(len(self.classes)) if ci in used]
        remap = {old: new for new, old in enumerate(keep)}
        self.classes = [self.classes[ci] for ci in keep]
        for l in self.lectures:
            l["cls"] = remap[l["cls"]]

    def _delete_class(self):
        """Remove the active lecture's class and ALL of its lectures after a
        confirm, then shift the remaining classes' indices down to match."""
        if not (0 <= self.active < len(self.lectures)):
            return
        ci = self.lectures[self.active]["cls"]
        cl = self.classes[ci]
        n = sum(1 for l in self.lectures if l["cls"] == ci)
        if not self._confirm(
                "Delete this class?",
                _t("“%s” and its %d lecture%s will be removed. You can undo "
                   "this with Ctrl+Z.")
                % (cl.get("label", ""), n, "" if n == 1 else "s")):
            return
        self.undo.checkpoint("Delete Class…")
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
            self._notes_timer = None
        del self.classes[ci]
        self.lectures = [l for l in self.lectures if l["cls"] != ci]
        # Every class index above the removed one shifts down by one.
        for l in self.lectures:
            if l["cls"] > ci:
                l["cls"] -= 1
        self.active = 0 if self.lectures else -1
        self._refresh_sidebar()
        self._refresh_canvas()
        try:
            self._save_to_disk()
        except Exception:
            pass
        self.undo.commit()

    def _select(self, i):
        if i == self.active:
            return
        # Flush the outgoing lecture (note text + tag ranges) before the canvas
        # rebuilds; otherwise set_text on the new lecture drops the old edits.
        self._capture_active()
        self.active = i
        self._refresh_sidebar()
        self._refresh_canvas()

    def _on_title_changed(self, entry):
        if self.active >= 0:
            new = entry.get_text()
            # Only act on a real change: the canvas-rebuild set_text fires
            # "changed" with new == stored, so skipping keeps this cheap and
            # avoids a redundant sidebar rebuild during canvas construction.
            if new != self.lectures[self.active]["title"]:
                self.lectures[self.active]["title"] = new
                # Update the active row's title label in place instead of
                # rebuilding the whole sidebar on every keystroke — the full
                # O(classes×lectures) rebuild + show_all() caused visible typing
                # lag on the software-rendered VM. Structural changes (new/
                # delete/select/rename) still go through _refresh_sidebar, which
                # re-captures this handle.
                if self._active_title_label is not None:
                    # Mirror the sidebar's empty-title fallback so clearing the
                    # title shows "Untitled Lecture" there, not a blank row.
                    self._active_title_label.set_text(new or "Untitled Lecture")
            self._mark_editing()

    def _on_notes_changed(self, buf):
        # Keep the per-keystroke path cheap: schedule ONE debounced buffer read
        # (note-text sync + live word count) instead of scanning the whole
        # buffer twice on every keypress (once to store notes, once to recount).
        # The save-state indicator still flips immediately so typing stays live.
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
        self._notes_timer = GLib.timeout_add(150, self._flush_notes)
        self._mark_editing()

    def _flush_notes(self):
        self._notes_timer = None
        self._sync_notes()
        return False

    def _sync_notes(self):
        """One buffer read: store the note text into the active lecture and
        refresh the live word count. Shared by the debounce and every flush
        point (lecture switch / save / export) so a note is never left stale."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.lectures[self.active]["notes"] = txt
        self.wordlbl.set_text(self._wordcount_text(txt))

    def _capture_active(self):
        """Flush the live buffer (note text + tag ranges) into the active
        lecture and cancel any pending notes debounce, so switching lectures or
        saving never loses keystrokes typed inside the debounce window."""
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
            self._notes_timer = None
        self._sync_notes()
        self._capture_ranges()

    def _toggle_tag(self, name):
        """Toggle a character tag (bold / italic / highlight) over the current
        selection: remove it if the whole run already carries it, otherwise
        apply it — so B / I un-format as well as format, like every editor.
        With no selection there is nothing to format, so just return focus to
        the note (never silently do the wrong thing)."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        bounds = buf.get_selection_bounds()
        if not bounds:
            self.body.grab_focus()
            return
        start, end = bounds
        tag = buf.get_tag_table().lookup(name)
        if tag is None:
            return
        it = start.copy()
        fully = True
        while it.compare(end) < 0:
            if not it.has_tag(tag):
                fully = False
                break
            it.forward_char()
        self.undo.checkpoint("Formatting")
        if fully:
            buf.remove_tag(tag, start, end)
        else:
            buf.apply_tag(tag, start, end)
        # A tag change fires no "changed" signal, so flip the save state (and
        # schedule the disk write) here or the formatting looks unsaved / is
        # only persisted on the next text edit.
        self._mark_editing()
        self.undo.commit()

    _STYLE_ORDER = ("Body", "Heading", "Subheading")

    def _line_style(self, it):
        tbl = self.body.get_buffer().get_tag_table()
        for name, label in (("heading", "Heading"), ("subheading", "Subheading")):
            tag = tbl.lookup(name)
            if tag is not None and it.has_tag(tag):
                return label
        return "Body"

    def _sync_style_label(self):
        """Set the Style indicator to the paragraph style at the caret, so the
        toolbar always names what the writer is standing in."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        start = buf.get_iter_at_line(
            buf.get_iter_at_mark(buf.get_insert()).get_line())
        label = self._line_style(start)
        if self.stylelbl.get_text() != label:
            self.stylelbl.set_text(label)

    def _on_mark_set(self, buf, _it, mark):
        # Only the insertion caret drives the Style indicator.
        if mark is buf.get_insert():
            self._sync_style_label()

    def _cycle_style(self):
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        ins = buf.get_iter_at_mark(buf.get_insert())
        start = buf.get_iter_at_line(ins.get_line())
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        cur = self._line_style(start)
        nxt = self._STYLE_ORDER[
            (self._STYLE_ORDER.index(cur) + 1) % len(self._STYLE_ORDER)]
        buf.remove_tag_by_name("heading", start, end)
        buf.remove_tag_by_name("subheading", start, end)
        if nxt == "Heading":
            buf.apply_tag_by_name("heading", start, end)
        elif nxt == "Subheading":
            buf.apply_tag_by_name("subheading", start, end)
        self.stylelbl.set_text(nxt)
        self.body.grab_focus()
        self._mark_editing()

    def _insert_list(self, prefix):
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        it = buf.get_iter_at_mark(buf.get_insert())
        it.set_line_offset(0)
        # Numbered list: continue the sequence from the previous line instead
        # of always inserting "1. " (which produced "1. 1. 1.").
        if prefix.strip().rstrip(".").isdigit():
            n, line = 1, it.get_line()
            if line > 0:
                prev = buf.get_iter_at_line(line - 1)
                pend = prev.copy(); pend.forward_to_line_end()
                head = buf.get_text(prev, pend, False).lstrip().split(".", 1)[0]
                if head.strip().isdigit():
                    n = int(head.strip()) + 1
            prefix = "%d. " % n
        buf.insert(it, prefix)
        self.body.grab_focus()

    @staticmethod
    def _wordcount_text(txt):
        stripped = txt.strip()
        n = len(stripped.split()) if stripped else 0
        return "%d word%s" % (n, "" if n == 1 else "s")

    def _recount(self):
        if not hasattr(self, "body"):
            self.wordlbl.set_text("0 words")
            return
        buf = self.body.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.wordlbl.set_text(self._wordcount_text(txt))

    def _mark_editing(self):
        # One undo step per burst of typing. _mark_editing is reached by every
        # content change — the note, the title, a tag toggle, a style change —
        # and only re-arms a timer, so it costs nothing per keystroke.
        self.undo.touch()
        self._saved = False
        self.savelbl.set_text("Saving…")
        self.savedot.queue_draw()
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(900, self._mark_saved)

    def _mark_saved(self):
        # The debounce has settled: this is where the real disk write happens,
        # so the "Saved HH:MM" indicator only lights green after a genuine save.
        if self._save_to_disk():
            self._saved = True
            self.savelbl.set_text("Saved %s" % time.strftime("%H:%M"))
        else:
            # I/O failed — don't claim "Saved"; leave the dot red so the state
            # is honest, but never crash the app over a disk error.
            self._saved = False
            self.savelbl.set_text("Saving…")
        self.savedot.queue_draw()
        self._save_timer = None
        return False

    # ---------------- persistence ----------------
    @staticmethod
    def _valid_hex(c):
        """True only for a '#RRGGBB' string nbicons._hex can parse, so a
        foreign / hand-edited colour never reaches a swatch draw."""
        if not isinstance(c, str) or len(c) != 7 or c[0] != "#":
            return False
        try:
            int(c[1:], 16)
            return True
        except ValueError:
            return False

    def _load_from_disk(self):
        """Restore classes + lectures from ACADEMIC_FILE.

        Validates the shape defensively: any missing/malformed/foreign data
        leaves the empty default in place so the app still opens exactly as a
        fresh install (no classes) does.
        """
        try:
            with open(ACADEMIC_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        raw_classes = data.get("classes")
        raw_lectures = data.get("lectures")
        if not isinstance(raw_classes, list) or not isinstance(raw_lectures, list):
            return

        classes = []
        for c in raw_classes:
            if not isinstance(c, dict):
                return
            color = c.get("color")
            classes.append({
                "label": str(c.get("label", "Untitled Class")),
                "color": color if self._valid_hex(color) else CLASS_COLORS[0],
            })

        lectures = []
        for lec in raw_lectures:
            if not isinstance(lec, dict):
                return
            try:
                cls = int(lec.get("cls", 0))
            except (TypeError, ValueError):
                return
            if cls < 0 or cls >= len(classes):
                return
            # Tolerate old files that saved plain text with no formatting ranges.
            raw_ranges = lec.get("ranges")
            lectures.append({
                "cls": cls,
                "num": str(lec.get("num", "01")),
                "title": str(lec.get("title", "")),
                "date": str(lec.get("date", self._short_date())),
                "meta": str(lec.get("meta", "")),
                "notes": str(lec.get("notes", "")),
                "ranges": raw_ranges if isinstance(raw_ranges, dict) else {},
            })

        # Everything validated — adopt the restored model wholesale.
        self.classes = classes
        self.lectures = lectures
        try:
            active = int(data.get("active", -1))
        except (TypeError, ValueError):
            active = -1
        if 0 <= active < len(lectures):
            self.active = active
        elif lectures:
            self.active = 0
        else:
            self.active = -1
        # A hand-edited / foreign file can carry a class with no lectures, which
        # would render as an un-addable ghost header; drop those on load so the
        # restored notebook obeys the same one-lecture-minimum invariant we keep.
        self._prune_empty_classes()

    def _save_to_disk(self):
        """Persist the full editable model. Returns True on success.

        Wrapped so a disk error can never crash the app; the caller decides
        whether to show the "Saved" state from the return value.
        """
        # Pull the live buffer's note text + formatting into the model so both
        # persist even if the notes debounce hasn't fired yet.
        self._capture_active()
        data = {
            "classes": [dict(c) for c in self.classes],
            "lectures": [dict(lec) for lec in self.lectures],
            "active": self.active,
        }
        try:
            nbapp.atomic_write_json(ACADEMIC_FILE, data)
            return True
        except Exception:
            return False

    # ---------------- undo / redo ----------------
    # The snapshot is the same model the autosave writes: every class, every
    # lecture's title / notes / formatting, and the selection. One mechanism
    # therefore reverses typing, a deleted lecture and a deleted class alike.
    def _undo_snapshot(self):
        self._capture_active()          # fold the live buffer into the model
        return {"classes": [dict(c) for c in self.classes],
                "lectures": self._copy_lectures(self.lectures),
                "active": self.active,
                "_caret": self._caret_offset()}

    @staticmethod
    def _copy_lectures(lectures):
        """Fresh dicts (and fresh range lists) per lecture, so a snapshot can
        never be edited from under itself by the next _capture_active. The
        note text inside is an immutable string and is shared, which is what
        keeps a full notebook's history small."""
        out = []
        for lec in lectures:
            copy = dict(lec)
            copy["ranges"] = {k: [list(sp) for sp in v]
                              for k, v in (lec.get("ranges") or {}).items()}
            out.append(copy)
        return out

    def _caret_offset(self):
        try:
            buf = self.body.get_buffer()
            return buf.get_iter_at_mark(buf.get_insert()).get_offset()
        except Exception:
            return 0

    def _undo_restore(self, state):
        self.classes = [dict(c) for c in state["classes"]]
        self.lectures = self._copy_lectures(state["lectures"])
        self.active = state["active"]
        self._clear_search()       # a filter can hide the row we just restored
        self._refresh_sidebar()
        self._refresh_canvas()     # rebuilds the title field and the note view
        try:
            buf = self.body.get_buffer()
            caret = min(max(0, state.get("_caret", 0)), buf.get_char_count())
            buf.place_cursor(buf.get_iter_at_offset(caret))
            self.body.grab_focus()
        except Exception:
            pass
        self._save_to_disk()

    def _on_destroy(self, *_a):
        """Flush a final save on window close so the last edit isn't lost."""
        self.undo.cancel()
        for attr in ("_save_timer", "_notes_timer", "_filter_timer"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        # _save_to_disk -> _capture_active still pulls the live buffer in, so a
        # keystroke typed inside the debounce window is persisted on close.
        self._save_to_disk()
        return False

    # ---------------- File menu: export active lecture to PDF -----------------
    # academic.json stays the sole source of truth (autosaved on every edit).
    # The File menu offers a one-way render of the ACTIVE lecture — its
    # class/lecture eyebrow, title, meta and note body (heading spans honoured)
    # — to a paginated PDF under $NB_HOME/Documents. No file open/save.
    def _pdf_name(self, lec):
        """A neutral PDF filename derived from the class + lecture title."""
        cl = self.classes[lec["cls"]]
        raw = "%s %s" % (cl.get("label", ""), lec.get("title", ""))
        words = "".join(c if c.isalnum() else " " for c in raw).split()
        base = "-".join(words).lower()[:70] if words else "lecture"
        return base + ".pdf"

    def _make_active_pdf(self, path):
        """Write the active lecture to a PDF at `path` — the single renderer
        shared by File ▸ Export to PDF and File ▸ Print. Flushes the live buffer
        first so the output reflects the on-screen note, not the last debounced
        snapshot. Raises if there is no active lecture (callers guard for this)."""
        self._capture_active()
        lec = self.lectures[self.active]
        cl = self.classes[lec["cls"]]
        self._render_pdf(path, lec, cl)

    def _print_doc(self, *_a):
        """Print the active lecture via the shared themed Print dialog, reusing
        the exact same PDF the Export action writes. The no-printer case is
        handled inside nbprint."""
        if not (0 <= self.active < len(self.lectures)):
            self._flash("No lecture to print")
            return
        nbprint.print_document(self, self._make_active_pdf, job_name="Paper")

    def _export_pdf(self, *_a):
        """Render the active lecture to a PDF under Documents. Reports a neutral
        status line in the save indicator; never crashes on a bad path/write."""
        if not (0 <= self.active < len(self.lectures)):
            self._flash("No lecture to export")
            return
        lec = self.lectures[self.active]
        name = self._pdf_name(lec)
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            # Same renderer the Print dialog uses, so the exported and printed
            # PDFs are identical byte-for-byte in layout.
            self._make_active_pdf(os.path.join(DOCS_DIR, name))
        except Exception:
            self._flash("Export failed")
            return
        # Success. Settle the autosave first (cancel the pending timer and write
        # now) so it can't overwrite this confirmation a moment later, then say
        # where the PDF landed — a novice needs to know it's under Documents.
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = None
        self._saved = self._save_to_disk()
        try:
            self.savedot.queue_draw()
            self.savelbl.set_text("Exported to Documents")
        except Exception:
            pass

    @staticmethod
    def _line_style_at(a, b, ranges):
        """Block style ('heading'/'subheading'/'body') for the char span [a,b),
        from the lecture's stored tag ranges; tolerant of malformed data."""
        if isinstance(ranges, dict):
            for name in ("heading", "subheading"):
                spans = ranges.get(name)
                if not isinstance(spans, list):
                    continue
                for span in spans:
                    try:
                        s, e = int(span[0]), int(span[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if s < b and e > a:
                        return name
        return "body"

    def _render_pdf(self, path, lec, cl):
        """Draw `lec` onto a cairo PDF at `path`, paginating when the cursor
        overflows the page. Serif body + ink palette to match the canvas."""
        PW, PH = 612.0, 792.0            # US Letter, points
        ML, MR, MT, MB = 64.0, 64.0, 72.0, 64.0
        text_w = PW - ML - MR
        surf = cairo.PDFSurface(path, PW, PH)
        cr = cairo.Context(surf)
        y = [MT]                         # cursor top, boxed so helpers can bump it

        def ink(hexc):
            r, g, b = nbicons._hex(hexc)
            cr.set_source_rgb(r, g, b)

        def face(bold):
            cr.select_font_face(
                "Serif", cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)

        def wrap(text, size, bold):
            face(bold)
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

        def emit(text, size, bold, color, gap_before=0.0, gap_after=0.0):
            lh = size * 1.4
            y[0] += gap_before
            for ln in (wrap(text, size, bold) if text else [""]):
                if y[0] + lh > PH - MB:
                    surf.show_page()
                    y[0] = MT
                face(bold)
                cr.set_font_size(size)
                ink(color)
                cr.move_to(ML, y[0] + size)
                cr.show_text(ln)
                y[0] += lh
            y[0] += gap_after

        # Header: class/lecture eyebrow, title, meta, then a hairline rule.
        emit(("%s · Lecture %s" % (cl.get("label", ""), lec.get("num", "")))
             .upper(), 9.5, False, "#6E695E", gap_after=6)
        emit(lec.get("title", "") or "Untitled Lecture", 26, True,
             "#1A1916", gap_after=3)
        meta = lec.get("meta", "")
        if meta:
            emit(meta, 10, False, "#9A9484", gap_after=6)
        if y[0] + 1 <= PH - MB:
            ink("#D7D2C5")
            cr.set_line_width(1.0)
            cr.move_to(ML, y[0])
            cr.line_to(PW - MR, y[0])
            cr.stroke()
        y[0] += 18

        # Body: one buffer line at a time so heading/subheading spans size their
        # whole line. Char offsets are reconstructed exactly as the buffer counts
        # them (each newline is one char), matching _capture_ranges.
        ranges = lec.get("ranges", {})
        off = 0
        for raw in lec.get("notes", "").split("\n"):
            style = self._line_style_at(off, off + len(raw), ranges)
            if style == "heading":
                emit(raw, 17, True, "#1A1916", gap_before=10, gap_after=2)
            elif style == "subheading":
                emit(raw, 13.5, True, "#1A1916", gap_before=7, gap_after=2)
            else:
                emit(raw, 11, False, "#1A1916")
            off += len(raw) + 1

        surf.finish()

    def _flash(self, text):
        """Surface a transient status/error line in the save indicator
        (crash-safe; the next edit or successful save resets it)."""
        try:
            self._saved = False
            self.savelbl.set_text(text)
            self.savedot.queue_draw()
        except Exception:
            pass

    # ---------------- menu bar ----------------
    def menu_items(self, name):
        if name == "File":
            # academic.json is the sole source of truth (autosaved on every
            # edit). File offers only the in-memory new/delete-item actions plus
            # a one-way render of the active lecture to a PDF under
            # $NB_HOME/Documents — no file open / save / save-as. The delete
            # actions need an open lecture, so they disable in the empty state.
            have = 0 <= self.active < len(self.lectures)
            return [("New Lecture", self._new_lecture),
                    ("New Class", self._new_class),
                    nbapp.SEP,
                    ("Delete Lecture…", self._delete_lecture if have else None),
                    ("Delete Class…", self._delete_class if have else None),
                    nbapp.SEP,
                    ("Export to PDF", self._export_pdf if have else None),
                    ("Print…", self._print_doc if have else None),
                    nbapp.SEP,
                    ("Close    Esc", self.close)]
        if name == "Edit":
            # Base Cut/Copy/Paste/Select All, plus the class-rename action —
            # an in-memory model edit (not a file operation), needing an open
            # lecture so it disables in the empty state.
            have = 0 <= self.active < len(self.lectures)
            # Undo/redo lead the menu, as they do in every editor — and they
            # have to be VISIBLE, not just bound to a key nobody can discover.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit") + [
                nbapp.SEP,
                ("Rename Class…", self._rename_class if have else None)]
        if name == "Format":
            # Formatting acts on the open note, so every item disables in the
            # empty state rather than looking live but doing nothing.
            have = 0 <= self.active < len(self.lectures)
            return [("Bold    Ctrl+B",
                     (lambda: self._toggle_tag("bold")) if have else None),
                    ("Italic    Ctrl+I",
                     (lambda: self._toggle_tag("italic")) if have else None),
                    ("Highlight",
                     (lambda: self._toggle_tag("highlight")) if have else None),
                    nbapp.SEP,
                    ("Body Text",
                     (lambda: self._set_style("Body")) if have else None),
                    ("Heading",
                     (lambda: self._set_style("Heading")) if have else None),
                    ("Subheading",
                     (lambda: self._set_style("Subheading")) if have else None),
                    nbapp.SEP,
                    ("Cycle Style", self._cycle_style if have else None)]
        if name == "Insert":
            have = 0 <= self.active < len(self.lectures)
            return [("Bullet List",
                     (lambda: self._insert_list("• ")) if have else None),
                    ("Numbered List",
                     (lambda: self._insert_list("1. ")) if have else None),
                    nbapp.SEP,
                    ("Date",
                     (lambda: self._insert_at_cursor(self._long_date()))
                     if have else None),
                    ("Time",
                     (lambda: self._insert_at_cursor(time.strftime("%H:%M")))
                     if have else None)]
        if name == "View":
            have = 0 <= self.active < len(self.lectures)
            return [("Search Notes    Ctrl+F",
                     self._focus_search if self.lectures else None),
                    ("Show All Lectures",
                     (lambda: self._clear_search()) if self._query else None),
                    nbapp.SEP,
                    ("Previous Lecture",
                     (lambda: self._nav(-1)) if have else None),
                    ("Next Lecture",
                     (lambda: self._nav(1)) if have else None),
                    nbapp.SEP,
                    ("Focus Note", self._focus_note if have else None),
                    ("Refresh Word Count", self._recount if have else None)]
        return super().menu_items(name)

    def _on_key(self, w, ev):
        # Esc drops an active search before it reaches the base handler (which
        # would close the whole app) — the escape a filtered list needs.
        if ev.keyval == Gdk.KEY_Escape and self._clear_search():
            self._focus_note()
            return True
        # Ctrl+F puts the caret in the search field wherever focus happens to be.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F)):
            self._focus_search()
            return True
        # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y, handled at the window level so they
        # work from the sidebar and the title field too, not only the note.
        if nbapp.undo_keys(self.undo, ev):
            return True
        # Ctrl+B / Ctrl+I toggle the selection's formatting — the shortcuts the
        # toolbar tooltips promise. Only when a lecture is open; Esc / menu keys
        # stay with the base handler. Modal dialogs run their own loops, so their
        # keys are unaffected.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and 0 <= self.active < len(self.lectures)):
            if ev.keyval in (Gdk.KEY_b, Gdk.KEY_B):
                self._toggle_tag("bold")
                return True
            if ev.keyval in (Gdk.KEY_i, Gdk.KEY_I):
                self._toggle_tag("italic")
                return True
        return super()._on_key(w, ev)

    def _set_style(self, target):
        """Set the current line to a specific style (Body/Heading/Subheading)."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        ins = buf.get_iter_at_mark(buf.get_insert())
        start = buf.get_iter_at_line(ins.get_line())
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        self.undo.checkpoint("Style")
        buf.remove_tag_by_name("heading", start, end)
        buf.remove_tag_by_name("subheading", start, end)
        if target == "Heading":
            buf.apply_tag_by_name("heading", start, end)
        elif target == "Subheading":
            buf.apply_tag_by_name("subheading", start, end)
        self.stylelbl.set_text(target)
        self.body.grab_focus()
        self._mark_editing()
        self.undo.commit()

    def _insert_at_cursor(self, text):
        """Insert plain text at the note's cursor (no-op if no note open)."""
        if not hasattr(self, "body") or self.active < 0:
            return
        self.body.get_buffer().insert_at_cursor(text)
        self.body.grab_focus()

    def _display_order(self):
        """Lecture indices in the order the sidebar shows them (grouped by
        class), which can differ from raw self.lectures creation order."""
        order = []
        for ci in range(len(self.classes)):
            for li, lec in enumerate(self.lectures):
                if lec["cls"] == ci:
                    order.append(li)
        return order

    def _nav(self, delta):
        """Move selection to the previous/next lecture, clamped in range.

        Navigate in the sidebar's grouped display order — stepping by raw
        creation order made Next jump to a lecture in a different class.
        """
        order = self._display_order()
        if not order:
            return
        cur = self.active if self.active in order else order[0]
        pos = order.index(cur)
        self._select(order[max(0, min(len(order) - 1, pos + delta))])

    def _focus_note(self):
        """Put keyboard focus in the note body (no-op if no note open)."""
        if hasattr(self, "body") and self.active >= 0:
            self.body.grab_focus()

    def _focus_title(self):
        """Swap the heading into edit mode and select it, so a just-created
        lecture can be named immediately and a click lands in the field."""
        if getattr(self, "title", None) is None:
            return
        self.title_ev.hide()
        self.title.show()
        self.title.grab_focus()
        self.title.select_region(0, -1)

    def _show_title_label(self):
        """Back to the read view: the wrapped heading (or a ghost prompt when
        the lecture has no title yet)."""
        if getattr(self, "title", None) is None:
            return
        text = self.title.get_text().strip()
        self.title_lbl.set_text(text or _t("Lecture title"))
        ctx = self.title_lbl.get_style_context()
        (ctx.add_class if not text else ctx.remove_class)("ghost")
        self.title.hide()
        self.title_ev.show()

    def _set_fmt_sensitive(self, have):
        """Enable the format-bar controls only when a lecture is open, so a
        blank canvas never shows a live-looking but inert toolbar."""
        for b in getattr(self, "_fmt_btns", []):
            b.set_sensitive(have)

    # ---------------- helpers ----------------
    def _sep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("fsep")
        return s

    def _txtbtn(self, label, cls):
        b = Gtk.Button(label=label)
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("fmtbtn")
        b.get_style_context().add_class(cls)
        return b

    def _iconbtn(self, name):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("fmtbtn")
        b.add(Gtk.Image.new_from_pixbuf(nbicons.pixbuf(name, 19, "#1A1916")))
        return b

    def _short_date(self):
        # %-d (no-pad) is a glibc extension; fall back to %d on libcs that
        # reject it (musl/uClibc raise ValueError) so date stamps never crash.
        try:
            return time.strftime("%a %-d %b")
        except ValueError:
            return time.strftime("%a %d %b")

    def _long_date(self):
        try:
            return time.strftime("%A %-d %B %Y")
        except ValueError:
            return time.strftime("%A %d %B %Y")

    # ---------------- style ----------------
    def _install_css(self):
        css = b"""
        .sidebar { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        /* No compositor: every scroller/viewport must own an opaque surface or
           it renders solid black on real hardware. */
        .sidebar scrolledwindow, .sidebar viewport,
        .side-list { background: #F1EEE6; }
        .canvaswrap viewport, .canvas { background: #FCFBF8; }
        .sidebar *, .editor * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .side-head { padding: 24px 26px 20px; border-bottom: 1px solid #D7D2C5; }
        .side-eyebrow { font-size: 11px; letter-spacing: 0.16em; color: #9A9484;
                        font-weight: 600; margin-bottom: 8px; }
        .side-term { font-size: 21px; font-weight: 700; color: #1A1916; }
        .acsearch { margin-top: 16px; font-size: 13px; color: #1A1916;
                    background: #FCFBF8; border: 1px solid #C4BFB1;
                    border-radius: 2px; box-shadow: none; min-height: 30px; }
        .acsearch:focus { border: 1px solid #8A857A; }
        .side-count { font-size: 11px; letter-spacing: 0.1em; color: #9A9484;
                      font-weight: 700; padding: 0 10px; margin: 2px 0 10px; }
        .side-list { padding: 16px 14px; }
        .side-empty { padding: 30px 12px; font-size: 13px; color: #9A9484; }
        .cls-head { padding: 0 10px; margin: 10px 0 9px; }
        .cls-label { font-size: 11px; letter-spacing: 0.1em; color: #6E695E;
                     font-weight: 700; }
        .lec-row { padding: 10px 10px; margin-bottom: 2px; border-radius: 2px;
                   background: transparent; border: none; box-shadow: none; }
        .lec-row:hover { background: #E6DFCE; }
        .lec-row.active { background: #EAE3D2; box-shadow: inset 3px 0 0 #C8341E; }
        .lec-num { min-width: 30px; min-height: 24px; padding: 0 6px;
                   font-size: 12px; border-radius: 2px; color: #9A9484;
                   border: 1px solid #D7D2C5; }
        .lec-num.active { background: #1A1916; color: #FCFBF8; font-weight: 600;
                          border: 1px solid #1A1916; }
        .lec-title { font-size: 14px; color: #1A1916; font-weight: 500; }
        .lec-date { font-size: 12px; color: #9A9484; margin-top: 2px; }
        .side-foot { border-top: 1px solid #D7D2C5; padding: 14px 18px; }
        .newlecture { min-height: 40px; border: 1px solid #C9C4B6;
                      border-radius: 2px; background: #FCFBF8; color: #1A1916;
                      font-size: 14px; font-weight: 500; box-shadow: none; }
        .newlecture:hover { background: #ECE8DD; }

        .editor { background: #FCFBF8; }
        .formatbar { background: #FCFBF8; border-bottom: 1px solid #D7D2C5;
                     padding: 10px 36px; min-height: 34px; }
        .stylebtn { min-height: 34px; padding: 0 13px; border: 1px solid #D7D2C5;
                    border-radius: 2px; background: #FCFBF8; color: #1A1916;
                    font-size: 14px; font-weight: 500; box-shadow: none; }
        .stylebtn:hover { background: #F1EDE2; }
        .stylebtn .caret { font-size: 11px; color: #9A9484; }
        .fmtbtn { min-width: 34px; min-height: 34px; padding: 0;
                  background: transparent; border: none; box-shadow: none;
                  border-radius: 2px; color: #1A1916; font-size: 17px; }
        .fmtbtn:hover { background: #EFEBE0; }
        /* With no lecture open the format bar is insensitive, but Body / B / I
           still looked live: the rules above set their colour outright, and a
           declaration that lands on the button's own LABEL node beats any
           colour inherited from the button, so GTK's insensitive dimming never
           showed. The icon buttons greyed (GTK dims the image itself), which
           left half a greyed toolbar. Name the labels explicitly, as journal
           does for the same bar. */
        .fmtbtn:disabled, .fmtbtn:disabled label { color: #B9B4A8; }
        .stylebtn:disabled, .stylebtn:disabled label,
        .stylebtn:disabled .caret { color: #B9B4A8; }
        .fmtbtn.bold { font-weight: 700; }
        .fmtbtn.ital { font-style: italic; }
        .fsep { color: #D7D2C5; min-width: 1px; }
        .wordcount, .savestate { font-size: 13px; color: #9A9484; }
        .canvaswrap { background: #FCFBF8; }
        .canvas { padding: 56px 24px 160px; }
        .canvas-eyebrow-row { margin-bottom: 18px; }
        .canvas-eyebrow { font-size: 12px; letter-spacing: 0.1em; color: #6E695E;
                          font-weight: 700; }
        .doctitle { font-family: "Newsreader","Liberation Serif",serif;
                    font-weight: 700; font-size: 40px; color: #1A1916;
                    background: transparent; border: none; padding: 0;
                    margin-bottom: 8px; }
        .doctitle.ghost { color: #B9B4A8; }
        .doctitlebtn { border-radius: 2px; }
        .doctitlebtn:hover { background: #F1EDE2; }
        .canvas-meta { font-size: 13px; color: #9A9484; margin-bottom: 34px;
                       padding-bottom: 24px; border-bottom: 1px solid #D7D2C5; }
        .docbody { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 18px; color: #1A1916; background: #FCFBF8;
                   margin-top: 14px; caret-color: #C8341E; }
        .docbody text { background: #FCFBF8; }
        .docbody text selection { background-color: #F1D9D2; color: #1A1916; }
        .empty-wrap { padding: 60px 0 0; }
        .empty-title { font-family: "Newsreader","Liberation Serif",serif;
                       font-size: 21px; color: #1A1916; margin-bottom: 6px; }
        .empty-sub { font-size: 13px; color: #9A9484; margin-bottom: 16px; }
        .emptybtn { min-height: 36px; padding: 0 18px; font-size: 14px;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 2px; box-shadow: none; color: #1A1916; }
        .emptybtn:hover { background: #ECE8DD; }

        /* Rename / delete cards: papertone, undecorated, matching the rest of
           the OS rather than a stock GTK dialog in a window-manager frame.
           Each inverted button colours its LABEL node as well as itself: the
           theme's `* { color: ink }` matches the label directly and would
           otherwise beat the colour inherited from the button. */
        .acdlg { background: #FCFBF8; border: 1px solid #C4BFB1; }
        .acdlgbox { padding: 24px 28px 20px; }
        .acdlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .acdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
                      font-size: 19px; font-weight: 600; color: #1A1916; }
        .acdlgmsg { font-size: 13px; color: #57534B; }
        .acdlgentry { min-height: 38px; padding: 0 10px; background: #FCFBF8;
                      border: 1px solid #C4BFB1; border-radius: 2px;
                      font-size: 14px; color: #1A1916; }
        .acdlgcancel { font-size: 13px; color: #2A2620; padding: 6px 16px;
                       background: #FCFBF8; border: 1px solid #C9C4B6;
                       border-radius: 2px; box-shadow: none; }
        .acdlgcancel:hover { background: #ECE8DD; }
        .acdlgok { font-size: 13px; padding: 6px 16px; background: #C8341E;
                   border: 1px solid #C8341E; border-radius: 2px;
                   box-shadow: none; font-weight: 600; }
        .acdlgok label { color: #FCFBF8; }
        .acdlgok:hover { background: #A82A18; border-color: #A82A18; }
        .acdlgprimary { font-size: 13px; padding: 6px 16px; background: #1A1916;
                        border: 1px solid #1A1916; border-radius: 2px;
                        box-shadow: none; font-weight: 600; }
        .acdlgprimary label { color: #FCFBF8; }
        .acdlgprimary:hover { background: #33302A; border-color: #33302A; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(AcademicNotes)
