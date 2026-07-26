#!/usr/bin/env python3
"""
E-book Reader — the Notebook OS reading surface (native GTK).

A paper reading surface with a Library sheet listing the books on the device.
Books are loaded from a USB stick or added with File ▸ Open (EPUB or PDF); the
shelf, the open book, and each book's reading position persist under
$NB_HOME/.config/notebook/ebook.json, so re-opening a volume returns to where
you left off. Ships EMPTY per the no-seed rule — no book is loaded until one is
opened, and the library starts with no volumes.

Document contents are rendered on the device:
  · EPUB — parsed in pure Python (zipfile → META-INF/container.xml → the .opf
    spine order → each XHTML doc's readable text) and laid out as reading
    paragraphs; the A−/A+ controls scale the reading type.
  · PDF  — rendered page-by-page with Poppler into a cairo DrawingArea; A−/A+
    zoom the render and the ‹ / › controls page through the document.

Poppler is only guaranteed on the built guest, so its import is GUARDED: when it
is absent, PDFs show a neutral 'PDF engine unavailable' state while EPUB (pure
Python) always works — the app always constructs and opens its empty state.

An optional book path may be passed as sys.argv[1] (the Finder opens EPUB/PDF
this way).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

# Poppler (PDF) is only guaranteed on the built guest, not on the host running
# construct_all.py / the selftests. Guard the require_version + import so the
# module always imports and the window always constructs; POPPLER_OK gates every
# use of the PDF engine below, and PDFs fall back to a neutral notice when it is
# unavailable. EPUB rendering is pure Python and needs none of this.
POPPLER_OK = False
try:
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler  # noqa: E402
    POPPLER_OK = True
except (ImportError, ValueError):
    Poppler = None

# pycairo backs the PDF page-raster cache: _pdf_relayout renders each page once
# into an off-screen ImageSurface (keyed on page + zoom) and _pdf_draw just
# blits it. It ships with the guest's pygobject (the gi cairo bridge), but guard
# the import so the module still loads where it is absent — _pdf_draw then falls
# back to rendering each page directly.
try:
    import cairo
    _CAIRO_OK = True
except Exception:
    cairo = None
    _CAIRO_OK = False

import os
import sys
import json
import math
import posixpath
import zipfile
try:
    import xml.etree.ElementTree as ET
    _XML_OK = True
except Exception:                       # a trimmed python without the xml pkg
    ET = None
    _XML_OK = False
from html.parser import HTMLParser
from urllib.parse import unquote

import nbapp
import nbpicker
import nbicons
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
DOCUMENTS_DIR = os.path.join(HOME, "Documents")
CONFIG_DIR = os.path.join(HOME, ".config", "notebook")
CONFIG_PATH = os.path.join(CONFIG_DIR, "ebook.json")


# ------------------------------------------------------------- EPUB parsing
def _epub_localname(tag):
    """Namespace-stripped, lower-cased XML tag name (EPUB opf/container use
    namespaced elements; matching on the local name keeps parsing simple)."""
    if isinstance(tag, str) and "}" in tag:
        tag = tag.rsplit("}", 1)[-1]
    return (tag or "").lower()


class _EpubBlocks(HTMLParser):
    """Strip an XHTML document to a list of (kind, text) reading blocks: 'h' for
    a heading (h1–h6), 'p' otherwise. Block-level tags separate paragraphs and
    inline runs of whitespace are collapsed; script/style/head are dropped."""

    SKIP = {"script", "style", "head", "title"}
    HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK = {"p", "div", "br", "li", "blockquote", "section", "article",
             "header", "footer", "figcaption", "pre", "tr", "td", "th",
             "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._buf = []
        self._skip = 0
        self._kind = "p"

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP:
            self._skip += 1
            return
        if t == "br":
            self._flush()
            return
        if t in self.BLOCK:
            self._flush()
            self._kind = "h" if t in self.HEAD else "p"

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br":
            self._flush()

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP:
            if self._skip > 0:
                self._skip -= 1
            return
        if t in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        self._buf.append(data)

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        self._buf = []
        kind = self._kind
        self._kind = "p"
        if text:
            self.blocks.append((kind, text))

    def close(self):
        super().close()
        self._flush()
        return self.blocks


def _epub_extract(data):
    """Decode one XHTML document (bytes) and return its (kind, text) blocks."""
    text = None
    for enc in ("utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            text = None
    if text is None:
        return []
    parser = _EpubBlocks()
    try:
        parser.feed(text)
        return parser.close()
    except Exception:
        return list(parser.blocks)


def _epub_load(path):
    """Parse an EPUB into an ordered list of chapters, each a list of
    (kind, text) blocks. Returns (chapters, None) on success or (None, message)
    on failure. Uses the stdlib xml parser (present when python was built with
    pyexpat); degrades gracefully if that is absent.
    """
    if not _XML_OK:
        return None, "The EPUB reader is unavailable on this build."
    try:
        zf = zipfile.ZipFile(path)
    except Exception:
        return None, "This file could not be read as an EPUB archive."
    chapters = []
    try:
        names = set(zf.namelist())

        # META-INF/container.xml points at the .opf package document.
        opf_path = None
        try:
            root = ET.fromstring(zf.read("META-INF/container.xml"))
            for el in root.iter():
                if _epub_localname(el.tag) == "rootfile" and el.get("full-path"):
                    opf_path = el.get("full-path")
                    break
        except Exception:
            opf_path = None
        if opf_path not in names:
            opf_path = next(
                (n for n in names if n.lower().endswith(".opf")), None)
        if opf_path is None or opf_path not in names:
            return None, "This EPUB is missing its package document."

        try:
            opf = ET.fromstring(zf.read(opf_path))
        except Exception:
            return None, "This EPUB's package document could not be parsed."

        # manifest id -> href, then the spine's reading order of those ids.
        manifest = {}
        for el in opf.iter():
            if _epub_localname(el.tag) == "item":
                iid, href = el.get("id"), el.get("href")
                if iid and href:
                    manifest[iid] = href
        spine = [el.get("idref") for el in opf.iter()
                 if _epub_localname(el.tag) == "itemref" and el.get("idref")]
        opf_dir = posixpath.dirname(opf_path)

        def _resolve(href):
            # hrefs are relative to the .opf; drop any #fragment.
            href = href.split("#", 1)[0]
            return posixpath.normpath(posixpath.join(opf_dir, href))

        order = []
        for sid in spine:
            href = manifest.get(sid)
            if href:
                order.append(_resolve(href))
        if not order:  # no usable spine — fall back to manifest HTML order
            for href in manifest.values():
                if href.lower().split("#", 1)[0].endswith(
                        (".xhtml", ".html", ".htm")):
                    order.append(_resolve(href))

        for full in order:
            data = None
            for cand in (full, unquote(full)):
                if cand in names:
                    try:
                        data = zf.read(cand)
                    except Exception:
                        data = None
                    break
            if data is None:
                continue
            blocks = _epub_extract(data)
            if blocks:  # skip empty front-matter / navigation documents
                chapters.append(blocks)
    finally:
        try:
            zf.close()
        except Exception:
            pass
    if not chapters:
        return None, "No readable text was found in this EPUB."
    return chapters, None


def _epub_meta(path):
    """Best-effort (title, author) from an EPUB's OPF Dublin Core metadata, so
    the shelf and the reading bar show the book's real title and author rather
    than its filename. Returns (None, None) when unavailable — pure stdlib,
    fully guarded, never raises — so callers fall back to the filename."""
    def _clean(s):
        if not s:
            return None
        s = " ".join(s.split())
        return s[:120] if s else None

    if not _XML_OK:
        return None, None
    try:
        zf = zipfile.ZipFile(path)
    except Exception:
        return None, None
    title = author = None
    try:
        names = set(zf.namelist())
        opf_path = None
        try:
            root = ET.fromstring(zf.read("META-INF/container.xml"))
            for el in root.iter():
                if _epub_localname(el.tag) == "rootfile" and el.get("full-path"):
                    opf_path = el.get("full-path")
                    break
        except Exception:
            opf_path = None
        if opf_path not in names:
            opf_path = next(
                (n for n in names if n.lower().endswith(".opf")), None)
        if opf_path and opf_path in names:
            opf = ET.fromstring(zf.read(opf_path))
            for el in opf.iter():
                ln = _epub_localname(el.tag)
                txt = (el.text or "").strip()
                if not txt:
                    continue
                if ln == "title" and title is None:
                    title = txt
                elif ln == "creator" and author is None:
                    author = txt
    except Exception:
        pass
    finally:
        try:
            zf.close()
        except Exception:
            pass
    return _clean(title), _clean(author)


class EbookReader(nbapp.AppWindow):
    app_name = "E-book Reader"
    menus = ("File", "Library")

    # Supported document formats: extension -> label. EPUB is rendered in pure
    # Python; PDF is rendered with Poppler when the engine is available.
    FORMATS = {".epub": "EPUB", ".pdf": "PDF"}

    # Reading font size in points. The reading toolbar's A−/A+ buttons step
    # self._read_pt one point at a time within [READ_PT_MIN, READ_PT_MAX];
    # READ_PT_DEFAULT matches the reading surface's default body type. For EPUB
    # the size scales the reading paragraphs; for PDF it zooms the page render
    # (READ_PT_DEFAULT == fit-to-width, larger magnifies, smaller reduces).
    READ_PT_MIN = 12
    READ_PT_MAX = 28
    READ_PT_DEFAULT = 17

    # margin (px) kept clear either side of a rendered PDF page in the viewport
    PDF_PAD = 40

    # An EPUB spine document is not a page: plenty of books (anything from
    # Project Gutenberg, most of all) put the WHOLE text in one or two of them.
    # Building a Gtk.Label per paragraph costs about 2 ms, so a 3000-paragraph
    # document froze the reader for 25 seconds on open and again on every
    # return to it -- and left the reader with "Chapter 1 of 1" for a whole
    # novel, no way to move through it but scrolling, and nothing to remember.
    # Chapters are therefore cut into reading pages of at most this many
    # blocks; a page never spans two chapters.
    EPUB_PAGE_BLOCKS = 60
    # A trailing run shorter than this joins the page before it, so an ordinary
    # 65-paragraph chapter stays one page instead of becoming one page and a
    # five-line stub.
    EPUB_ORPHAN = 20

    def __init__(self):
        super().__init__()
        self._install_css()

        # Reading font-size state. A dedicated CSS provider (registered above the
        # base sheet) drives the size of the reading body labels; _apply_read_size
        # loads the current size into it.
        self._read_pt = self.READ_PT_DEFAULT
        self._read_labels = []
        self._read_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._read_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)

        # Reader mode + pagination state. _mode is "message" (empty-state /
        # notices), "epub", or "pdf"; _page is the 0-based page/chapter index
        # into a document of _page_total pages. All initialised before any UI is
        # built so the nav/size handlers are always safe to call.
        self._mode = "message"
        self._page = 0
        self._page_total = 0
        self._epub_chapters = []
        # (chapter index, first block, last block + 1) for each reading page
        self._epub_pages = []
        self._epub_col = None
        self._epub_scroll = None
        self._pdf_doc = None
        self._pdf_page_obj = None
        self._pdf_scale = 1.0
        self._pdf_last_w = 0
        self._pdf_area = None
        self._pdf_scroll = None
        # Cached rasterization of the current PDF page: a cairo ImageSurface
        # keyed on (page index, scale). Built once in _pdf_relayout when the
        # page or zoom changes and cleared when a document is opened; _pdf_draw
        # only blits it, so scrolling / uncover / resize never re-render the
        # page (a full Poppler render is tens-to-hundreds of ms per page).
        self._pdf_cache_surface = None
        self._pdf_cache_key = None

        # Overlay layer for the remove-from-library confirmation card, if open.
        self._confirm_layer = None

        # Library shelf: list of {"path", "title", "fmt"} for books added to the
        # device; self._open_path is the path of the book on the reading surface
        # (or None). Both are restored from disk so the shelf is never fabricated.
        self._books = []
        self._open_path = None
        self._load_state()

        self.content.pack_start(self._reading_bar(), False, False, 0)

        # The reading body fills the content area. The modal Library sheet and
        # the remove-confirmation card layer over the WHOLE window via the base
        # AppWindow overlay (self._overlay) so their scrim covers the menu bar
        # and reading bar too — and, critically, so the base menu dropdowns and
        # the About card (which also use self._overlay) stay correctly
        # positioned. This app must NOT shadow self._overlay with its own.
        self.content.pack_start(self._body(), True, True, 0)
        self._apply_read_size()
        self._library_sheet = self._library_modal()
        self._overlay.add_overlay(self._library_sheet)

        # The Finder launches the reader with a book path as argv[1] (.epub/.pdf).
        # Falls back to the restored open book (or the empty-state) otherwise.
        # An existing but unsupported file gets a neutral notice, never silence.
        # Closing the reader must keep the place in the book, not just the
        # page: everything else here writes on a page turn, and the last thing
        # a reader does is stop mid-page and leave.
        self.connect("destroy", lambda *_: self._remember_pos(force=True))

        opened = False
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            if self._open_book(sys.argv[1]):
                opened = True
            else:
                self._unsupported_message(sys.argv[1])
                opened = True
        if not opened:
            self._show_current()

    # ---------------------------------------------------------- persistence
    def _load_state(self):
        """Restore the shelf and open book from ebook.json (best effort)."""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        books = data.get("books") if isinstance(data, dict) else None
        if isinstance(books, list):
            for b in books:
                if (isinstance(b, dict) and b.get("path")
                        and b.get("title") and b.get("fmt")):
                    try:
                        pos = int(b.get("pos") or 0)
                    except (TypeError, ValueError):
                        pos = 0
                    try:
                        frac = float(b.get("frac") or 0.0)
                    except (TypeError, ValueError):
                        frac = 0.0
                    self._books.append({
                        "path": str(b["path"]),
                        "title": str(b["title"]),
                        "fmt": str(b["fmt"]),
                        "pos": max(pos, 0),
                        "frac": min(max(frac, 0.0), 1.0),
                        "total": max(0, int(b.get("total") or 0))
                        if str(b.get("total") or 0).lstrip("-").isdigit() else 0,
                        "author": str(b.get("author") or ""),
                    })
        op = data.get("open") if isinstance(data, dict) else None
        if isinstance(op, str) and any(b["path"] == op for b in self._books):
            self._open_path = op

    def _save_state(self):
        """Persist the shelf and open book under $NB_HOME (best effort)."""
        try:
            nbapp.atomic_write_json(
                CONFIG_PATH, {"books": self._books, "open": self._open_path},
                ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _short_path(self, path):
        """Render a path under $NB_HOME as ~/… so rows stay compact."""
        if path == HOME or path.startswith(HOME + os.sep):
            return os.path.join("~", os.path.relpath(path, HOME))
        return path

    def _book_by_path(self, path):
        for b in self._books:
            if b["path"] == path:
                return b
        return None

    # ---------------------------------------------------------------- bars
    def _reading_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("readbar")
        bar.set_size_request(-1, 52)

        left = Gtk.Box(spacing=10)
        left.set_margin_start(24)
        left.set_valign(Gtk.Align.CENTER)
        lib_btn = self._tool_icon("library", "Library")
        lib_btn.connect("clicked", self._on_library_open)
        left.pack_start(lib_btn, False, False, 0)
        bar.pack_start(left, False, False, 0)

        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        centre.set_valign(Gtk.Align.CENTER)
        centre.set_hexpand(True)
        centre.set_halign(Gtk.Align.CENTER)
        self._title_lbl = Gtk.Label(label=_t("No document"))
        self._title_lbl.get_style_context().add_class("bookttl")
        self._title_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self._title_lbl.set_max_width_chars(40)
        centre.pack_start(self._title_lbl, False, False, 0)
        self._subtitle_lbl = Gtk.Label(label="")
        self._subtitle_lbl.get_style_context().add_class("bookauth")
        centre.pack_start(self._subtitle_lbl, False, False, 0)
        bar.pack_start(centre, True, True, 0)

        # right: page nav (‹ page ›) then the reading font-size controls
        # (A− smaller / A+ larger). The nav pages through PDF pages / EPUB
        # chapters; the size controls scale the reading type or zoom the render.
        right = Gtk.Box(spacing=4)
        right.set_valign(Gtk.Align.CENTER)
        right.set_margin_end(24)
        self._prev_btn = self._tool_icon("prev", "Previous page")
        self._prev_btn.connect("clicked", self._on_prev)
        self._prev_btn.set_sensitive(False)
        self._page_lbl = Gtk.Label(label="")
        self._page_lbl.get_style_context().add_class("pagelbl")
        self._page_lbl.set_size_request(96, -1)
        self._next_btn = self._tool_icon("next", "Next page")
        self._next_btn.connect("clicked", self._on_next)
        self._next_btn.set_sensitive(False)
        right.pack_start(self._prev_btn, False, False, 0)
        right.pack_start(self._page_lbl, False, False, 0)
        right.pack_start(self._next_btn, False, False, 0)
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("readvsep")
        right.pack_start(sep, False, False, 6)
        self._smaller_btn = self._text_btn(
            "A-", "Decrease reading text size", self._on_text_smaller)
        self._larger_btn = self._text_btn(
            "A+", "Increase reading text size", self._on_text_larger)
        right.pack_start(self._smaller_btn, False, False, 0)
        right.pack_start(self._larger_btn, False, False, 0)
        bar.pack_end(right, False, False, 0)
        return bar

    def _text_btn(self, label, tip, cb):
        """A flat text toolbar button (used for the A−/A+ size steppers)."""
        b = Gtk.Button(label=label)
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("toolbtn")
        b.get_style_context().add_class("sizebtn")
        b.set_tooltip_text(tip)
        b.connect("clicked", cb)
        return b

    def _tool_icon(self, name, tip, color="#1A1916"):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("toolbtn")
        b.set_tooltip_text(tip)
        try:
            img = Gtk.Image.new_from_pixbuf(nbicons.pixbuf(name, 19, color))
        except Exception:
            # icon renderer unavailable (e.g. no PNG pixbuf loader on a
            # stripped build) — keep the button, just without a glyph.
            img = Gtk.Image()
        b.add(img)
        b._img = img
        return b

    # ---------------------------------------------------------------- body
    def _body(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("readcol")

        # The reader slot holds exactly one view at a time: the message card
        # (empty-state / notices), the EPUB reading column, or the PDF page.
        # _set_reader_widget swaps its child as the open document changes.
        self._reader_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._reader_slot.set_hexpand(True)
        self._reader_slot.set_vexpand(True)
        col.pack_start(self._reader_slot, True, True, 0)

        self._message_view = self._build_message_view()
        return col

    def _build_message_view(self):
        """The centred reading card, used for the empty-state and for neutral
        notices (unsupported / unreadable document, PDF engine unavailable)."""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("readscroll")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("readpage")
        card.set_halign(Gtk.Align.CENTER)
        card.set_size_request(560, -1)
        card.set_margin_top(90)
        card.set_margin_bottom(64)

        self._card_eyebrow = Gtk.Label(label=_t("READER"))
        self._card_eyebrow.get_style_context().add_class("readeyebrow")
        self._card_eyebrow.set_halign(Gtk.Align.CENTER)
        self._card_eyebrow.set_margin_bottom(12)
        card.pack_start(self._card_eyebrow, False, False, 0)

        self._card_heading = Gtk.Label(label="")
        self._card_heading.get_style_context().add_class("readh1")
        self._card_heading.set_halign(Gtk.Align.CENTER)
        self._card_heading.set_line_wrap(True)
        self._card_heading.set_justify(Gtk.Justification.CENTER)
        self._card_heading.set_max_width_chars(30)
        self._card_heading.set_margin_bottom(18)
        card.pack_start(self._card_heading, False, False, 0)

        self._card_detail = Gtk.Label(label="")
        self._card_detail.get_style_context().add_class("readdetail")
        self._card_detail.get_style_context().add_class("readbody")
        self._read_labels.append(self._card_detail)
        self._card_detail.set_halign(Gtk.Align.CENTER)
        self._card_detail.set_line_wrap(True)
        self._card_detail.set_justify(Gtk.Justification.CENTER)
        self._card_detail.set_max_width_chars(52)
        self._card_detail.set_margin_bottom(14)
        card.pack_start(self._card_detail, False, False, 0)

        self._card_note = Gtk.Label(label="")
        self._card_note.get_style_context().add_class("readnote")
        self._card_note.get_style_context().add_class("readcap")
        self._read_labels.append(self._card_note)
        self._card_note.set_halign(Gtk.Align.CENTER)
        self._card_note.set_line_wrap(True)
        self._card_note.set_justify(Gtk.Justification.CENTER)
        self._card_note.set_max_width_chars(52)
        card.pack_start(self._card_note, False, False, 0)

        # The empty state told the reader to open a book but gave her nothing to
        # press for it — the only way in was an unlabelled icon in the corner.
        # This is that button; notices (unreadable file, unsupported format)
        # hide it, so the card never offers an action that doesn't apply.
        self._card_action = Gtk.Button(label=_t("Open Book…"))
        self._card_action.set_relief(Gtk.ReliefStyle.NONE)
        self._card_action.get_style_context().add_class("readaction")
        self._card_action.set_halign(Gtk.Align.CENTER)
        self._card_action.set_margin_top(26)
        self._card_action.set_no_show_all(True)
        self._card_action.set_tooltip_text(
            _t("Choose an EPUB or PDF file to read"))
        self._card_action.connect("clicked", lambda *_: self._file_open())
        card.pack_start(self._card_action, False, False, 0)

        outer.pack_start(card, False, False, 0)
        scroll.add(outer)
        return scroll

    def _set_reader_widget(self, widget):
        """Show `widget` as the sole child of the reader slot."""
        for child in self._reader_slot.get_children():
            self._reader_slot.remove(child)
        self._reader_slot.pack_start(widget, True, True, 0)
        widget.show_all()

    # --------------------------------------------------- reader dispatch
    def _show_current(self):
        """Repaint the reading surface for the open book, or the empty-state."""
        book = self._book_by_path(self._open_path) if self._open_path else None
        if book is None:
            self._show_empty()
            return
        self._title_lbl.set_text(book["title"])
        # Show the author when we have it (EPUB metadata), else the format.
        self._subtitle_lbl.set_text(book.get("author") or book["fmt"])
        # The file may have gone (a USB stick removed since it was added) — say
        # so plainly rather than letting the engine report a bogus parse error.
        if not os.path.isfile(book["path"]):
            self._show_message(
                book["fmt"], book["title"], self._short_path(book["path"]),
                "This file is unavailable. It may be on a USB stick that "
                "has been removed.")
            return
        if book["fmt"] == "PDF":
            self._open_pdf(book)
        elif book["fmt"] == "EPUB":
            self._open_epub(book)
        else:
            self._show_message(
                book["fmt"], book["title"], self._short_path(book["path"]),
                "This document format is not supported.")

    def _show_empty(self):
        self._title_lbl.set_text("No document")
        self._subtitle_lbl.set_text("")
        self._show_message(
            "READER", "No document open",
            "Books on a USB stick appear in the Library. You can also open an "
            "EPUB or PDF file from this device.",
            # no note line here: the heading already says nothing is open, and
            # the button below says what to do about it.
            "", action=True)

    def _show_message(self, eyebrow, heading, detail, note, action=False):
        """Switch the reader to the centred message card with the given text.
        `action` shows the card's Open Book button (the empty state)."""
        self._mode = "message"
        self._page = 0
        self._page_total = 0
        self._card_eyebrow.set_text(eyebrow)
        self._card_heading.set_text(heading)
        self._card_detail.set_text(detail)
        self._card_note.set_text(note)
        self._card_action.set_visible(action)
        self._set_reader_widget(self._message_view)
        self._update_nav()

    def _show_page(self):
        """Render the current page/chapter for the active document mode."""
        if self._mode == "epub":
            self._epub_show_chapter()
        elif self._mode == "pdf":
            self._pdf_show_page()
        self._update_nav()
        self._remember_pos()

    def _restore_pos(self, book):
        """The saved page/chapter index for `book`, clamped to the document's
        current length. New or out-of-range positions fall back to the start."""
        try:
            pos = int(book.get("pos", 0))
        except (TypeError, ValueError):
            pos = 0
        if self._page_total <= 0:
            return 0
        return max(0, min(pos, self._page_total - 1))

    def _remember_pos(self, force=False):
        """Persist the reading position of the open book when it changes, so the
        volume re-opens where it was left. Writes only on a real change (never
        per frame), so paging costs one small JSON write per page turn.

        `force` also captures how far down the page the reader has scrolled,
        and is used when the book is closed or swapped: paging alone put you
        back at the top of a page you were three quarters through."""
        if self._mode not in ("epub", "pdf") or not self._open_path:
            return
        book = self._book_by_path(self._open_path)
        if book is None:
            return
        frac = self._scroll_fraction() if force else 0.0
        if not force and book.get("pos") == self._page:
            return
        book["pos"] = self._page
        book["frac"] = frac
        book["total"] = self._page_total
        self._save_state()

    def _current_scroll(self):
        """The scroller of whichever reading view is showing, or None."""
        return self._epub_scroll if self._mode == "epub" else self._pdf_scroll

    def _scroll_fraction(self):
        """How far down the current page the reader is, as 0.0-1.0."""
        try:
            adj = self._current_scroll().get_vadjustment()
            span = adj.get_upper() - adj.get_page_size() - adj.get_lower()
            if span <= 0:
                return 0.0
            return max(0.0, min(1.0, (adj.get_value() - adj.get_lower()) / span))
        except Exception:
            return 0.0

    def _scroll_to_fraction(self, frac):
        """Put the reader back where they stopped reading on this page. Runs on
        an idle tick because the labels have not been laid out (and the
        adjustment still reads zero) until after the first allocation."""
        try:
            adj = self._current_scroll().get_vadjustment()
            span = adj.get_upper() - adj.get_page_size() - adj.get_lower()
            if span > 0:
                adj.set_value(adj.get_lower() + span * max(0.0, min(1.0, frac)))
        except Exception:
            pass
        return False

    # ---------------------------------------------------- page navigation
    def _update_nav(self):
        """Refresh the ‹ / › sensitivity and the page indicator label, and gate
        the A−/A+ size controls so they are only live while a document is open
        (there is nothing to resize on the empty-state / notice card)."""
        total, n = self._page_total, self._page
        has_pages = total > 0
        reading = self._mode in ("epub", "pdf")
        self._prev_btn.set_sensitive(has_pages and n > 0)
        self._next_btn.set_sensitive(has_pages and n < total - 1)
        self._smaller_btn.set_sensitive(reading)
        self._larger_btn.set_sensitive(reading)
        # Both modes now turn pages: an EPUB chapter is cut into reading pages
        # (see EPUB_PAGE_BLOCKS), so "chapter" would no longer be true.
        self._prev_btn.set_tooltip_text(_t("Previous page"))
        self._next_btn.set_tooltip_text(_t("Next page"))
        if has_pages:
            self._page_lbl.set_text(_t("Page %d / %d") % (n + 1, total))
        else:
            self._page_lbl.set_text("")

    def _on_prev(self, *_):
        if self._page > 0:
            self._page -= 1
            self._show_page()

    def _on_next(self, *_):
        if self._page < self._page_total - 1:
            self._page += 1
            self._show_page()

    def _resume_scroll(self, book):
        """Scroll to where the book was closed. Two idle passes: the first lets
        the reading column be allocated, the second runs once the adjustment
        reflects that height."""
        try:
            frac = float(book.get("frac") or 0.0)
        except (TypeError, ValueError):
            return
        if frac <= 0.0:
            return
        GLib.idle_add(lambda: GLib.idle_add(self._scroll_to_fraction, frac)
                      and False)

    def _scroll_top(self, scroll):
        """Return a paged view to the top after a page/chapter change."""
        try:
            adj = scroll.get_vadjustment()
            if adj is not None:
                adj.set_value(adj.get_lower())
        except Exception:
            pass
        return False

    # -------------------------------------------------- EPUB rendering
    def _open_epub(self, book):
        """Parse the EPUB and show the page it was left on (pure Python, always
        available). Parse failures fall back to a neutral notice."""
        chapters, err = _epub_load(book["path"])
        if chapters is None:
            self._show_message("EPUB", book["title"],
                               self._short_path(book["path"]), err)
            return
        self._epub_chapters = chapters
        self._epub_pages = self._paginate(chapters)
        self._mode = "epub"
        self._page_total = len(self._epub_pages)
        self._page = self._restore_pos(book)
        self._set_reader_widget(self._build_epub_view())
        self._show_page()
        self._resume_scroll(book)

    @classmethod
    def _paginate(cls, chapters):
        """Cut the chapters into bounded reading pages — see EPUB_PAGE_BLOCKS.
        Returns a list of (chapter index, first block, last block + 1)."""
        pages = []
        for ci, blocks in enumerate(chapters):
            n = len(blocks)
            if n == 0:
                pages.append((ci, 0, 0))
                continue
            start = 0
            while start < n:
                end = min(n, start + cls.EPUB_PAGE_BLOCKS)
                if 0 < n - end < cls.EPUB_ORPHAN:   # absorb a short tail
                    end = n
                pages.append((ci, start, end))
                start = end
        return pages or [(0, 0, 0)]

    def _build_epub_view(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("readscroll")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("readpage")
        col.set_halign(Gtk.Align.CENTER)
        col.set_size_request(620, -1)
        col.set_margin_top(72)
        col.set_margin_bottom(80)

        outer.pack_start(col, False, False, 0)
        scroll.add(outer)
        self._epub_col = col
        self._epub_scroll = scroll
        return scroll

    def _epub_chapter_header(self, index, start=0, end=None):
        """The centred reading-area header for one page: a neutral 'CHAPTER n'
        index over the chapter's own opening heading (real document text), used
        as the title when the chapter has one. Returns (header_widget, body) so
        a heading promoted to the title is not repeated in the reading body.

        A page that continues a chapter keeps the CHAPTER line — so the reader
        always knows where they are — but not the big title, which belongs at
        the chapter's opening the way it does in a printed book."""
        try:
            blocks = self._epub_chapters[index]
        except (IndexError, TypeError):
            blocks = []
        if end is None:
            end = len(blocks)
        title = ""
        if start == 0 and blocks and blocks[0][0] == "h":
            title = blocks[0][1]
            start = 1
        body = blocks[start:end]

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_bottom(38)
        eye = Gtk.Label(label=_t("CHAPTER %d") % (index + 1))
        eye.get_style_context().add_class("readeyebrow")
        eye.set_halign(Gtk.Align.CENTER)
        box.pack_start(eye, False, False, 0)
        if title:
            ttl = Gtk.Label(label=title)
            ttl.get_style_context().add_class("readh1")
            ttl.set_halign(Gtk.Align.CENTER)
            ttl.set_justify(Gtk.Justification.CENTER)
            ttl.set_line_wrap(True)
            ttl.set_max_width_chars(30)
            ttl.set_margin_top(12)
            box.pack_start(ttl, False, False, 0)
        return box, body

    def _epub_show_chapter(self, to_top=True):
        col = self._epub_col
        if col is None:
            return
        for child in col.get_children():
            col.remove(child)
        try:
            ci, start, end = self._epub_pages[self._page]
        except (IndexError, TypeError):
            ci, start, end = 0, 0, None
        header, blocks = self._epub_chapter_header(ci, start, end)
        col.pack_start(header, False, False, 0)
        # The reading measure is the COLUMN's width, not a character count, so
        # the paragraphs fill it (aligning with the centred chapter heading) and
        # the character cap tracks the type size. A fixed 64-character cap is a
        # cap on the label's NATURAL width, so at the largest reading size it
        # stretched the column itself from 620px to ~900px — A+ was widening the
        # page instead of reflowing the text into it.
        cap = max(24, int(64 * self.READ_PT_DEFAULT / float(self._read_pt)))
        for kind, text in blocks:
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.set_line_wrap(True)
            lbl.set_justify(Gtk.Justification.FILL)
            lbl.set_max_width_chars(cap)
            if kind == "h":
                lbl.get_style_context().add_class("readchhead")
                lbl.set_margin_top(10)
                lbl.set_margin_bottom(12)
            else:
                # class "readbody" carries the live font-size; "readpara" gives
                # the serif reading face and colour.
                lbl.get_style_context().add_class("readbody")
                lbl.get_style_context().add_class("readpara")
                lbl.set_margin_bottom(15)
            col.pack_start(lbl, False, False, 0)
        col.show_all()
        # A page/chapter turn starts at the top; a re-flow after a reading-size
        # change must NOT throw the reader back to the top of the chapter.
        if to_top:
            GLib.idle_add(self._scroll_top, self._epub_scroll)

    # --------------------------------------------------- PDF rendering
    def _open_pdf(self, book):
        """Open the PDF with Poppler and render its first page. When Poppler is
        unavailable (guarded import) or the file cannot be opened, fall back to a
        neutral notice — the app never crashes."""
        if not POPPLER_OK:
            self._show_message(
                "PDF", book["title"], self._short_path(book["path"]),
                "The PDF engine is unavailable in this build.")
            return
        try:
            uri = GLib.filename_to_uri(os.path.abspath(book["path"]), None)
        except Exception:
            uri = "file://" + os.path.abspath(book["path"])
        try:
            doc = Poppler.Document.new_from_file(uri, None)
            npages = doc.get_n_pages()
        except Exception:
            self._show_message(
                "PDF", book["title"], self._short_path(book["path"]),
                "This PDF could not be opened for rendering.")
            return
        if npages <= 0:
            self._show_message(
                "PDF", book["title"], self._short_path(book["path"]),
                "This PDF has no pages to render.")
            return
        self._pdf_doc = doc
        self._mode = "pdf"
        self._page_total = npages
        self._page = self._restore_pos(book)
        self._pdf_last_w = 0
        # Drop any prior document's cached raster — a new doc may land on the
        # same page index and zoom, which would otherwise reuse a stale surface.
        self._pdf_cache_surface = None
        self._pdf_cache_key = None
        self._set_reader_widget(self._build_pdf_view())
        self._show_page()
        self._resume_scroll(book)

    def _build_pdf_view(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("readscroll")
        scroll.connect("size-allocate", self._on_pdf_alloc)

        area = Gtk.DrawingArea()
        area.connect("draw", self._pdf_draw)
        self._pdf_area = area

        paper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        paper.get_style_context().add_class("pdfpaper")
        paper.pack_start(area, True, True, 0)

        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.set_halign(Gtk.Align.CENTER)
        holder.set_valign(Gtk.Align.START)
        holder.set_margin_top(self.PDF_PAD)
        holder.set_margin_bottom(self.PDF_PAD)
        holder.pack_start(paper, False, False, 0)

        self._pdf_scroll = scroll
        scroll.add(holder)
        return scroll

    def _pdf_show_page(self):
        try:
            self._pdf_page_obj = self._pdf_doc.get_page(self._page)
        except Exception:
            self._pdf_page_obj = None
        self._pdf_relayout()
        # a fresh page returns the reader to the top of the viewport
        GLib.idle_add(self._scroll_top, self._pdf_scroll)

    def _pdf_relayout(self):
        """Size the drawing area for the current page at the current zoom. The
        page fits the viewport width at READ_PT_DEFAULT; A−/A+ scale around
        that. Never upscales a small page by more than 3×."""
        page = self._pdf_page_obj
        area = self._pdf_area
        if page is None or area is None:
            return
        try:
            pw, ph = page.get_size()
        except Exception:
            return
        if pw <= 0 or ph <= 0:
            return
        avail = 0
        if self._pdf_scroll is not None:
            avail = self._pdf_scroll.get_allocated_width() - 2 * self.PDF_PAD
        if avail < 200:
            avail = 760  # not yet allocated — a sensible first-paint width
        base = min(avail / pw, 3.0)
        if base <= 0:
            base = 1.0
        self._pdf_scale = base * (self._read_pt / float(self.READ_PT_DEFAULT))
        area.set_size_request(int(math.ceil(pw * self._pdf_scale)),
                              int(math.ceil(ph * self._pdf_scale)))
        self._pdf_render_cache()
        area.queue_draw()

    def _pdf_render_cache(self):
        """Rasterize the current page at the current zoom into a cached cairo
        ImageSurface, keyed on (page index, scale). Called from _pdf_relayout,
        which fires only when the page or zoom changes — never per expose — so
        _pdf_draw just blits this surface and scrolling / uncover / resize don't
        re-render the page. No-op (and _pdf_draw falls back to a direct render)
        when pycairo is unavailable."""
        page = self._pdf_page_obj
        if not _CAIRO_OK or page is None:
            self._pdf_cache_surface = None
            self._pdf_cache_key = None
            return
        key = (self._page, round(self._pdf_scale, 4))
        if key == self._pdf_cache_key and self._pdf_cache_surface is not None:
            return  # same page at the same zoom — reuse the existing raster
        try:
            pw, ph = page.get_size()
        except Exception:
            self._pdf_cache_surface = None
            self._pdf_cache_key = None
            return
        w = max(1, int(math.ceil(pw * self._pdf_scale)))
        h = max(1, int(math.ceil(ph * self._pdf_scale)))
        try:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            ctx = cairo.Context(surface)
            # White ground: Poppler leaves un-inked regions transparent, so the
            # white sheet is what makes the page read as paper.
            ctx.set_source_rgb(1.0, 1.0, 1.0)
            ctx.paint()
            ctx.scale(self._pdf_scale, self._pdf_scale)
            page.render(ctx)
        except Exception:
            self._pdf_cache_surface = None
            self._pdf_cache_key = None
            return
        self._pdf_cache_surface = surface
        self._pdf_cache_key = key

    def _pdf_draw(self, area, cr):
        # This handler fires on every expose (scroll / uncover / resize), so it
        # must not re-render the page — it blits the cached raster built once in
        # _pdf_relayout.
        surface = self._pdf_cache_surface
        if surface is not None and self._pdf_page_obj is not None:
            cr.set_source_surface(surface, 0, 0)
            cr.paint()
            return False
        # No cache (pycairo unavailable, or no page yet): paint the white sheet
        # and, when there is a page, render it directly this once.
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.paint()
        page = self._pdf_page_obj
        if page is None:
            return False
        try:
            cr.scale(self._pdf_scale, self._pdf_scale)
            page.render(cr)
        except Exception:
            pass
        return False

    def _on_pdf_alloc(self, _w, alloc):
        # Re-fit the page when the viewport width changes (window resize). The
        # width guard keeps the set_size_request → re-allocate cycle from looping.
        if self._mode != "pdf":
            return
        if abs(alloc.width - self._pdf_last_w) > 4:
            self._pdf_last_w = alloc.width
            self._pdf_relayout()

    # ------------------------------------------------- reading font size
    def _apply_read_size(self):
        """Push the current reading point size into the live CSS provider so the
        reading body re-renders. The supporting caption tracks 5pt below the
        body (floored at 11pt) to preserve the reading surface's type scale;
        at READ_PT_DEFAULT this reproduces the base sizes exactly."""
        cap = max(self._read_pt - 5, 11)
        css = (".readbody { font-size: %dpx; } "
               ".readcap { font-size: %dpx; }" % (self._read_pt, cap))
        self._read_css.load_from_data(css.encode("utf-8"))

    def _on_text_larger(self, *_):
        """Step the reading size up one point, clamped at READ_PT_MAX."""
        self._read_pt = min(self.READ_PT_MAX, self._read_pt + 1)
        self._apply_read_size()
        self._resize_reader()

    def _on_text_smaller(self, *_):
        """Step the reading size down one point, clamped at READ_PT_MIN."""
        self._read_pt = max(self.READ_PT_MIN, self._read_pt - 1)
        self._apply_read_size()
        self._resize_reader()

    def _resize_reader(self):
        """Re-fit the open document to the new reading size: zoom the PDF
        render, or re-flow the EPUB chapter at the same measure (its character
        cap tracks the type size), keeping the reader where it was reading."""
        if self._mode == "pdf":
            self._pdf_relayout()
        elif self._mode == "epub":
            self._epub_show_chapter(to_top=False)

    # ---------------------------------------------------- open / add books
    def _open_book(self, path):
        """Open `path` on the reading surface and add it to the shelf. Returns
        True when the format is supported, False otherwise."""
        ext = os.path.splitext(path)[1].lower()
        fmt = self.FORMATS.get(ext)
        if fmt is None:
            return False
        # Bank the place in the book being left before the new one replaces it.
        self._remember_pos(force=True)
        existing = self._book_by_path(path)
        # Prefer a title/author already derived for this volume; only recompute
        # from the file (EPUB metadata) when the file is actually present, so a
        # removed USB volume keeps the good name it was added with.
        title = os.path.splitext(os.path.basename(path))[0] or path
        author = ""
        if existing:
            if existing.get("title"):
                title = existing["title"]
            author = existing.get("author", "")
        if fmt == "EPUB" and os.path.isfile(path):
            mt, ma = _epub_meta(path)
            if mt:
                title = mt
            if ma:
                author = ma
        self._add_book(path, title, fmt, author)
        self._open_path = path
        self._save_state()
        self._show_current()
        self._populate_shelf()
        return True

    def _unsupported_message(self, path):
        """Neutral notice when a chosen/opened file is not a readable format —
        never a silent no-op, which would just look broken to the reader."""
        self._title_lbl.set_text("No document")
        self._subtitle_lbl.set_text("")
        self._show_message(
            "READER", "Unsupported format",
            "%s can't be opened. Supported formats: EPUB, PDF."
            % os.path.basename(path),
            "Choose an EPUB or PDF file.")

    def _add_book(self, path, title, fmt, author=""):
        """Insert a book at the front of the shelf, de-duplicating by path so
        re-opening a volume moves it to the top rather than listing it twice.
        A volume already on the shelf keeps its saved reading position."""
        existing = self._book_by_path(path)
        pos = existing.get("pos", 0) if existing else 0
        self._books = [b for b in self._books if b["path"] != path]
        self._books.insert(0, {"path": path, "title": title, "fmt": fmt,
                               "pos": max(int(pos or 0), 0),
                               "author": author or ""})

    # ------------------------------------------------------------- library
    def _library_modal(self):
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.set_halign(Gtk.Align.FILL)
        scrim.set_valign(Gtk.Align.FILL)
        scrim.connect("button-press-event", lambda *_: self._close_library())

        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sheet.get_style_context().add_class("sheet")
        sheet.set_halign(Gtk.Align.CENTER)
        sheet.set_valign(Gtk.Align.CENTER)
        sheet.set_size_request(520, -1)
        inner = Gtk.EventBox()  # swallow clicks so the sheet doesn't close
        # Input-only: no GdkWindow of its own to paint, so the sheet's opaque
        # paper shows through it (a visible-window EventBox with no background
        # blits solid black on the no-compositor stack) while it still catches
        # the press that would otherwise reach the scrim and dismiss the sheet.
        inner.set_visible_window(False)
        inner.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        inner.connect("button-press-event", lambda *_: True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(28); box.set_margin_bottom(28)
        box.set_margin_start(30); box.set_margin_end(30)
        inner.add(box)
        sheet.pack_start(inner, False, False, 0)

        t = Gtk.Label(label=_t("Library"), xalign=0)
        t.get_style_context().add_class("sheetttl")
        box.pack_start(t, False, False, 0)
        s = Gtk.Label(
            label="Books are loaded from a USB stick, or tap Open Book… "
                  "below. EPUB and PDF are read on the device.",
            xalign=0)
        s.get_style_context().add_class("sheetsub")
        s.set_line_wrap(True)
        s.set_max_width_chars(46)
        s.set_margin_bottom(18)
        box.pack_start(s, False, False, 0)

        # The device's shelf. Each row opens that volume (dismissing the sheet
        # back onto the reading surface). Filled by _populate_shelf, which shows
        # a technical empty-state when no books have been added yet.
        shelf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shelf.get_style_context().add_class("sheetshelf")
        shelf.set_margin_top(6); shelf.set_margin_bottom(20)
        self._shelf = shelf
        self._populate_shelf()
        # A large library must scroll INSIDE the sheet rather than push the sheet
        # off a short real panel. Cap the shelf to a fraction of the REAL screen
        # height (never a hardcoded 1080) and let it scroll past that; a small
        # shelf keeps its natural height, so the sheet is unchanged when empty.
        shelf_scroll = Gtk.ScrolledWindow()
        shelf_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        shelf_scroll.get_style_context().add_class("sheetscroll")
        try:
            shelf_scroll.set_propagate_natural_height(True)
            shelf_scroll.set_max_content_height(
                max(220, nbapp.screen_size()[1] - 360))
        except Exception:
            pass
        shelf_scroll.add(shelf)
        box.pack_start(shelf_scroll, False, False, 0)

        # Footer: add a book right here (so the Library is never a dead end when
        # empty) alongside Done. Open Book… runs the same chooser as File ▸ Open.
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        foot.set_halign(Gtk.Align.CENTER)
        foot.set_margin_top(10)
        add_btn = Gtk.Button(label=_t("Open Book…"))
        add_btn.set_relief(Gtk.ReliefStyle.NONE)
        add_btn.get_style_context().add_class("sheetbtn2")
        add_btn.set_tooltip_text(_t("Add an EPUB or PDF file to the library"))
        add_btn.connect("clicked", lambda *_: self._on_library_add())
        foot.pack_start(add_btn, False, False, 0)
        done = Gtk.Button(label=_t("Done"))
        done.set_relief(Gtk.ReliefStyle.NONE)
        done.get_style_context().add_class("sheetbtn")
        done.connect("clicked", lambda *_: self._close_library())
        foot.pack_start(done, False, False, 0)
        box.pack_start(foot, False, False, 0)

        scrim.add(sheet)
        scrim.set_no_show_all(True)
        return scrim

    def _book_row(self, book):
        """One Library row: title over a 'format · author/path' line as a click
        target that opens the volume, with a trailing Remove control (its own
        button, so a remove click never also opens the book)."""
        entry = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        bt = Gtk.Label(label=book["title"], xalign=0)
        bt.get_style_context().add_class("sheetbookttl")
        bt.set_ellipsize(3)  # PANGO_ELLIPSIZE_END — a long title never overflows
        bt.set_max_width_chars(40)
        entry.pack_start(bt, False, False, 0)
        author = book.get("author")
        detail = "%s  ·  %s" % (book["fmt"], author) if author else \
                 "%s  ·  %s" % (book["fmt"], self._short_path(book["path"]))
        # Say how far into each volume the reader got. The shelf was a flat list
        # in which the book you are half-way through looked exactly like the one
        # you have never opened.
        try:
            total = int(book.get("total") or 0)
            pos = int(book.get("pos") or 0)
        except (TypeError, ValueError):
            total = pos = 0
        if total > 1 and pos > 0:
            detail += "  ·  " + _t("Page %d / %d") % (min(pos + 1, total), total)
        sub = Gtk.Label(label=detail, xalign=0)
        sub.get_style_context().add_class("sheetbooksub")
        sub.set_ellipsize(3)
        sub.set_max_width_chars(46)
        entry.pack_start(sub, False, False, 0)

        # An input-only wrapper: it catches the open click but lets the row's
        # background paint through and sits beside (not over) the Remove button.
        open_area = Gtk.EventBox()
        open_area.set_visible_window(False)
        open_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        open_area.connect("button-press-event", self._on_book_open, book["path"])
        open_area.set_tooltip_text(_t("Open %s") % book["title"])
        open_area.add(entry)

        rm = self._tool_icon("trash", "Remove from library")
        rm.get_style_context().add_class("rmbook")
        rm.set_valign(Gtk.Align.CENTER)
        rm.connect("clicked", self._on_book_remove, book)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("sheetbook")
        if book["path"] == self._open_path:
            row.get_style_context().add_class("sheetbookopen")
        row.pack_start(open_area, True, True, 0)
        row.pack_end(rm, False, False, 0)
        return row

    def _populate_shelf(self):
        """(Re)fill the Library shelf from self._books, or show the technical
        empty-state when the device has no books."""
        shelf = getattr(self, "_shelf", None)
        if shelf is None:
            return
        for child in shelf.get_children():
            shelf.remove(child)
        if not self._books:
            empty = Gtk.Label(
                label="No books on the device.\n"
                      "Use Open Book… below to add an EPUB or PDF file.",
                xalign=0)
            empty.get_style_context().add_class("sheetempty")
            empty.set_line_wrap(True)
            shelf.pack_start(empty, False, False, 0)
        else:
            for book in self._books:
                shelf.pack_start(self._book_row(book), False, False, 0)
        shelf.show_all()

    def _on_library_open(self, *_):
        # Rebuild the shelf before showing so newly opened books and the current
        # open-book highlight are reflected each time the sheet is shown.
        self._populate_shelf()
        self._library_sheet.show()
        self._library_sheet.get_child().show_all()

    def _on_library_add(self, *_):
        """Open Book… from inside the Library: dismiss the sheet, then run the
        same chooser as File ▸ Open so the new volume lands on the reader."""
        self._close_library()
        self._file_open()

    def _on_book_open(self, _row=None, _ev=None, path=None):
        """Open the tapped library volume, then dismiss the sheet."""
        if path:
            self._open_book(path)
        self._close_library()
        return True

    def _close_library(self):
        self._library_sheet.hide()
        return True

    # ---------------------------------------------- remove from library
    def _on_book_remove(self, _btn, book):
        self._open_confirm_remove(book)

    def _open_confirm_remove(self, book):
        """Confirm before dropping a volume from the shelf — a destructive
        action, so it is never one stray click. The file itself is left alone;
        only the library entry is removed."""
        self._close_confirm()
        # Size + centre against the LIVE window allocation, never a hardcoded
        # 1920x1080: on a smaller real panel a 1920-wide scrim overflows the
        # surface and the card lands off-centre/off-screen. nbapp.screen_size()
        # gives the real primary-monitor pixels as the pre-realize fallback
        # (mirrors the installer's confirm + nbapp's About overlay).
        alloc = self.get_allocation()
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh

        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *_: (self._close_confirm(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("confirm")
        ttl = Gtk.Label(label=_t("Remove from library"), xalign=0)
        ttl.get_style_context().add_class("confirmttl")
        card.pack_start(ttl, False, False, 0)
        msg = Gtk.Label(
            label="Remove “%s” from the library? The file on the "
                  "device is not deleted." % book["title"], xalign=0)
        msg.get_style_context().add_class("confirmbody")
        msg.set_line_wrap(True)
        msg.set_max_width_chars(34)
        card.pack_start(msg, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        btns.set_margin_top(6)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("confirmbtn")
        cancel.connect("clicked", lambda *_: self._close_confirm())
        btns.pack_start(cancel, False, False, 0)
        remove = Gtk.Button(label=_t("Remove"))
        remove.set_relief(Gtk.ReliefStyle.NONE)
        remove.get_style_context().add_class("confirmbtn")
        remove.get_style_context().add_class("confirmremove")
        remove.connect("clicked", lambda *_, b=book: self._remove_book(b))
        btns.pack_start(remove, False, False, 0)
        card.pack_start(btns, False, False, 0)

        holder = Gtk.EventBox()   # own GdkWindow so the card blits over the sheet
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        _min, nat = holder.get_preferred_size()
        cw = nat.width if nat.width > 1 else 380
        ch = nat.height if nat.height > 1 else 170
        layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        # Raise the overlay + card windows so they blit above the reading
        # surface on the no-compositor stack (mirrors the installer's confirm).
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            hw = holder.get_window()
            if hw is not None:
                hw.raise_()
        except Exception:
            pass
        self._confirm_layer = layer

    def _close_confirm(self):
        layer = self._confirm_layer
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._confirm_layer = None
            return True
        return False

    def _remove_book(self, book):
        """Drop the volume from the shelf (its reading position with it). If it
        was the open book, return the reader to the empty-state. The file on
        disk is untouched."""
        self._close_confirm()
        path = book["path"]
        self._books = [b for b in self._books if b["path"] != path]
        if self._open_path == path:
            self._open_path = None
            self._show_empty()
        self._save_state()
        self._populate_shelf()

    # --------------------------------------------------------- keyboard
    def _on_key(self, w, ev):
        """Reading-surface keys. Esc backs out of an open overlay (the remove
        confirmation, then the Library sheet) before the base handler can treat
        it as a quit — so pressing Esc to dismiss a sheet never closes the whole
        app. Left/Right turn the page (PDF) or chapter (EPUB) while reading, when
        nothing is layered over the document."""
        kv = ev.keyval
        if kv == Gdk.KEY_Escape:
            if self._close_confirm():
                return True
            if self._library_sheet.get_visible():
                self._close_library()
                return True
            return super()._on_key(w, ev)
        overlay_up = (self._library_sheet.get_visible()
                      or self._menu_open is not None
                      or self._confirm_layer is not None
                      or getattr(self, "_about_layer", None) is not None)
        if (self._mode in ("epub", "pdf") and not overlay_up
                and kv in (Gdk.KEY_Left, Gdk.KEY_Right)):
            if kv == Gdk.KEY_Left:
                self._on_prev()
            else:
                self._on_next()
            return True
        return super()._on_key(w, ev)

    # ------------------------------------------------------ File ▸ Open
    def _file_open(self):
        path = self._choose_file()
        if path and os.path.isfile(path):
            if not self._open_book(path):
                self._unsupported_message(path)

    def _choose_file(self):
        """Finder-style in-app picker for EPUB/PDF; a path or None."""
        start = DOCUMENTS_DIR if os.path.isdir(DOCUMENTS_DIR) else HOME
        return nbpicker.open_file(self, title="Open Book",
                                  start_dir=start,
                                  patterns=("*.epub", "*.pdf"))

    # ------------------------------------------------------------- menus
    def menu_items(self, name):
        """Real dropdown actions for the reader's own menus, each wired to an
        existing handler. Unhandled names fall back to the base (File/Edit/
        app-name), keeping Close/About working."""
        if name == "File":
            return [
                ("Open…", self._file_open),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Library":
            return [
                ("Open Library…", self._on_library_open),
                ("Close Library", self._close_library),
            ]
        return super().menu_items(name)

    # ------------------------------------------------------------------ css
    def _install_css(self):
        css = b"""
        .readbar { background: #F4F2EC; border-bottom: 1px solid #D7D2C5; }
        .readbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .toolbtn { min-width: 32px; min-height: 32px; padding: 0; margin: 0;
                   background: transparent; border: none; box-shadow: none;
                   border-radius: 2px; color: #1A1916; }
        .toolbtn:hover { background: #E6DFCE; }
        .toolbtn:disabled { background: transparent; }
        .sizebtn { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                   font-weight: 600; color: #1A1916; padding: 0 8px;
                   min-width: 26px; min-height: 32px; }
        .pagelbl { font-family: "Nimbus Sans","Helvetica",sans-serif;
                   font-size: 11px; letter-spacing: 0.08em; color: #9A9484; }
        .readvsep { color: #D7D2C5; min-width: 1px; margin: 0 6px; }
        .bookttl { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                   font-size: 17px; color: #1A1916; }
        .bookauth { font-size: 10px; letter-spacing: 0.20em; color: #9A9484; }

        .readcol { background: #F1EEE6; }
        .readscroll, .readscroll viewport { background: #F1EEE6; }
        .readpage { background: #F1EEE6; }
        .readeyebrow { font-family: "Nimbus Sans","Helvetica",sans-serif;
                       font-size: 11px; letter-spacing: 0.22em; color: #9A9484;
                       font-weight: 600; }
        .readh1 { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                  font-size: 30px; color: #1A1916; }
        .readdetail { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                      font-size: 17px; color: #6E695E; }
        .readnote { font-family: "Nimbus Sans","Helvetica",sans-serif;
                    font-size: 12px; letter-spacing: 0.02em; color: #9A9484; }
        /* the empty state's one action (dark ink: signage red is for alerts) */
        .readaction { font-family: "Nimbus Sans","Helvetica",sans-serif;
                      min-width: 150px; min-height: 40px; background: #1A1916;
                      border: 1px solid #1A1916; border-radius: 2px;
                      box-shadow: none; font-size: 14px; font-weight: 600; }
        .readaction label { color: #FCFBF8; }
        .readaction:hover { background: #33302A; border-color: #33302A; }
        /* EPUB reading paragraphs + chapter headings (serif reading face). The
           font SIZE of .readpara comes from the live .readbody rule so the
           A-/A+ steppers scale the body text; headings keep a stable size. */
        .readpara { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    color: #1A1916; }
        .readchhead { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                      font-size: 23px; font-weight: 600; color: #1A1916; }
        /* PDF page sheet: a white leaf on the papertone mat. */
        .pdfpaper { background: #FFFFFF; border: 1px solid #D7D2C5;
                    box-shadow: 2px 3px 0 rgba(26,25,22,0.10); }

        .scrim { background: rgba(26,25,22,0.18); }
        .sheet { background: #FCFBF8; border: 1px solid #C9C4B6;
                 box-shadow: 4px 4px 0 rgba(26,25,22,0.15); }
        .sheet * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .sheetttl { font-size: 18px; font-weight: 700; color: #1A1916; }
        .sheetsub { font-size: 13px; color: #9A9484; }
        .sheetscroll, .sheetscroll viewport { background: #FCFBF8; }
        .sheetshelf { border-top: 1px solid #D7D2C5; }
        .sheetempty { font-size: 13px; color: #9A9484; padding: 18px 2px; }
        .sheetbook { border-bottom: 1px solid #D7D2C5; padding: 14px 2px; }
        .sheetbookopen { border-left: 2px solid #C8341E; padding-left: 10px; }
        .sheetbookttl { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                        font-size: 17px; color: #1A1916; }
        .sheetbooksub { font-size: 12px; color: #9A9484; }
        /* The label node needs the colour too: the Papertone theme sets
           `* { color: ink }`, and a rule that matches a node directly always
           beats a colour inherited from its parent, so colouring only the
           button left "Done" as ink-on-ink: an unreadable black slab. */
        .sheetbtn { min-width: 96px; min-height: 38px; background: #1A1916;
                    color: #FCFBF8; border: none; border-radius: 2px;
                    box-shadow: none; font-size: 14px; font-weight: 600; }
        .sheetbtn label { color: #FCFBF8; }
        .sheetbtn:hover { background: #33302A; }
        /* secondary sheet action (Open Book): a bordered paper button */
        .sheetbtn2 { min-width: 96px; min-height: 38px; background: #FCFBF8;
                     color: #1A1916; border: 1px solid #C9C4B6;
                     border-radius: 2px; box-shadow: none; font-size: 14px;
                     font-weight: 600; }
        .sheetbtn2:hover { background: #EFEBE0; }
        /* per-row Remove control: muted ink, warm alert tint on hover */
        .rmbook { min-width: 30px; min-height: 30px; padding: 0;
                  background: transparent; border: none; box-shadow: none;
                  border-radius: 2px; }
        .rmbook:hover { background: #F1DAD4; }
        /* remove-from-library confirmation card */
        .confirm { background: #FCFBF8; border: 1px solid #C9C4B6;
                   box-shadow: 4px 4px 0 rgba(26,25,22,0.15);
                   padding: 26px 30px; }
        .confirm * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .confirmttl { font-size: 17px; font-weight: 700; color: #1A1916; }
        .confirmbody { font-size: 13px; color: #6E695E; }
        .confirmbtn { min-width: 88px; min-height: 36px; background: #FCFBF8;
                      color: #1A1916; border: 1px solid #C9C4B6;
                      border-radius: 2px; box-shadow: none; font-size: 14px; }
        .confirmbtn:hover { background: #EFEBE0; }
        .confirmremove { background: #C8341E; color: #FCFBF8;
                         border: 1px solid #C8341E; font-weight: 600; }
        .confirmremove label { color: #FCFBF8; }
        .confirmremove:hover { background: #B12D19; border-color: #B12D19; }
        """
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(EbookReader)
