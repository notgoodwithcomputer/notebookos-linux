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
gi.require_version("Atk", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo, Atk  # noqa: E402
try:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf                        # noqa: E402
    PIXBUF_OK = True
except Exception:                                              # noqa: BLE001
    GdkPixbuf = None
    PIXBUF_OK = False

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
import re
import sys
import json
import codecs
import math
import copy
import posixpath
import time
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
import nbstate
import nbpicker
import nbicons
import nbtransitions
import nbi18n
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
DOCUMENTS_DIR = os.path.join(HOME, "Documents")
CONFIG_DIR = os.path.join(HOME, ".config", "notebook")
CONFIG_PATH = os.path.join(CONFIG_DIR, "ebook.json")

# EPUB members are external input. A tiny archive can advertise a chapter or
# image that expands to gigabytes; ZipFile.read would allocate it all before
# the parser/decoder had a chance to reject it.
EPUB_XML_MAX = 2 * 1024 * 1024
EPUB_CHAPTER_MAX = 8 * 1024 * 1024
EPUB_TEXT_TOTAL_MAX = 64 * 1024 * 1024
EPUB_IMAGE_MAX = 32 * 1024 * 1024
EPUB_MEMBER_MAX = 10000


def _epub_read_limited(zf, name, limit):
    """Read one archive member only when its expanded size is bounded."""
    info = zf.getinfo(name)
    if info.file_size < 0 or info.file_size > limit:
        raise ValueError("EPUB member is too large")
    data = zf.read(info)
    if len(data) > limit:  # defensive against inconsistent archive metadata
        raise ValueError("EPUB member expanded past its limit")
    return data


def _epub_names_bounded(zf, limit=EPUB_MEMBER_MAX):
    """Return archive names only when the central directory is reasonable."""
    infos = zf.infolist()
    if len(infos) > limit:
        raise ValueError("EPUB contains too many files")
    return {info.filename for info in infos}


def _holds_books(data):
    """True when a parsed store plainly contains book-shaped records, whether or
    not this app's loader managed to read them.

    An empty shelf is a perfectly legitimate state (a new reader, or one who
    removed their last volume), so the test is the SHAPE of what is in the file,
    never emptiness."""
    pools = list(data.values()) if isinstance(data, dict) else [data]
    for pool in pools:
        if isinstance(pool, dict):
            pool = list(pool.values())
        if not isinstance(pool, list):
            continue
        for rec in pool:
            if isinstance(rec, dict) and any(
                    isinstance(rec.get(k), str) and rec.get(k)
                    for k in ("path", "title")):
                return True
    return False


# ------------------------------------------------------------- EPUB parsing
def _epub_localname(tag):
    """Namespace-stripped, lower-cased XML tag name (EPUB opf/container use
    namespaced elements; matching on the local name keeps parsing simple)."""
    if isinstance(tag, str) and "}" in tag:
        tag = tag.rsplit("}", 1)[-1]
    return (tag or "").lower()


_MARKUP_TEXT_RE = re.compile(r"</?[ib]>")

# The face tables are measured AND drawn in. One constant because they used to
# be two different strings — the width pass asked for a bare "Newsreader" and
# the layout pass for "Newsreader,Liberation Serif,Georgia,serif" — so column
# widths were measured in whatever Pango fell back to while the text that
# filled them was drawn in something else. Neither Newsreader nor Georgia is on
# this image, so both landed on Liberation Serif by luck rather than by saying
# so; name what actually ships and the two passes cannot drift.
TABLE_FAMILY = "Liberation Serif,DejaVu Serif,serif"


def escape_markup(s):
    """The five characters Pango markup reserves. GLib.markup_escape_text does
    this too, but only for str — book text arrives from a decode that can yield
    surrogates, and a raised exception there would lose the paragraph."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def set_reading_text(label, markup):
    """Put a reading block on a label as markup, falling back to plain text.

    A book is a file from outside the system and its markup is assembled from
    whatever tag soup the publisher shipped. If a paragraph will not parse, the
    reader must still SEE the paragraph — losing the italics is a blemish,
    losing the sentence is a bug — so the tags are stripped and the text set
    directly rather than allowed to raise."""
    # The words belong to the BOOK, never to the interface. nbi18n looks up
    # every label it is shown, and a book is full of short blocks that collide:
    # a chapter called "Contents", a table cell reading "Name" or "Date", a
    # one-word paragraph. Translating those edits the contents of a file the
    # system does not own. set_verbatim stamps the widget so both the setter
    # and the show_all walk leave it alone; the markup is applied after the
    # stamp so the tags still render.
    try:
        Pango.parse_markup(markup, -1, "\0")
        nbi18n.set_verbatim(label, markup)
        label.set_markup(markup)
    except Exception:
        nbi18n.set_verbatim(label, _MARKUP_TEXT_RE.sub("", markup)
                            .replace("&amp;", "&").replace("&lt;", "<")
                            .replace("&gt;", ">").replace("&quot;", '"')
                            .replace("&apos;", "'"))


class _EpubBlocks(HTMLParser):
    """Strip an XHTML document to a list of (kind, markup) reading blocks: 'h'
    for a heading (h1–h6), 'p' otherwise. Block-level tags separate paragraphs
    and inline runs of whitespace are collapsed; script/style/head are dropped.

    The text is PANGO MARKUP, not plain text, so italic and bold survive the
    trip to the page. They used to be discarded with every other tag, which in
    a novel is not a detail: emphasis, book titles, foreign words and a
    character's inner voice are all italics, and a reader was given none of
    them (ROADMAP #34). Only <em>/<i> and <strong>/<b> are carried — the two
    that change what a sentence MEANS. Everything else an EPUB can carry is
    still dropped.

    Data is escaped as it arrives, because the emitted string now has to parse
    as markup: `convert_charrefs` has already turned `&amp;` back into `&`, and
    an unescaped one would make the whole paragraph unrenderable."""

    SKIP = {"script", "style", "head", "title"}
    HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK = {"p", "div", "br", "li", "blockquote", "section", "article",
             "header", "footer", "figcaption", "pre", "tr", "td", "th",
             "h1", "h2", "h3", "h4", "h5", "h6"}
    # html inline tag -> pango tag
    INLINE = {"em": "i", "i": "i", "cite": "i", "strong": "b", "b": "b"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._buf = []
        self._skip = 0
        self._kind = "p"
        self._open = []          # pango tags currently open, outermost first
        self._table = None

    @staticmethod
    def _attr_int(attrs, name):
        for key, value in attrs or ():
            if (key or "").lower() == name:
                try:
                    return max(1, int(value))
                except (TypeError, ValueError):
                    return 1
        return 1

    def _table_start(self):
        self._flush()
        if self._table is None:
            self._table = {"rows": [], "row": [], "cell": None,
                           "nested": 0}
        else:
            # A browser can recursively lay out a table in a cell.  This small
            # reader deliberately cannot: keep its text in the containing cell
            # as stacked runs, in source order, rather than dropping it.
            self._table["nested"] += 1

    def _table_cell_start(self, attrs):
        if self._table["nested"]:
            if self._table["cell"] is not None and self._table["cell"]["buf"]:
                self._table["cell"]["buf"].append(" ")
            return
        self._table_cell_end()
        self._table["cell"] = {
            "buf": [], "colspan": self._attr_int(attrs, "colspan"),
            "rowspan": self._attr_int(attrs, "rowspan")}

    def _table_cell_end(self):
        if not self._table or self._table["nested"] or self._table["cell"] is None:
            return
        cell = self._table["cell"]
        text = " ".join("".join(cell["buf"]).split())
        cell["text"] = text
        del cell["buf"]
        self._table["row"].append(cell)
        self._table["cell"] = None

    def _table_row_end(self):
        if not self._table or self._table["nested"]:
            return
        self._table_cell_end()
        if self._table["row"]:
            self._table["rows"].append(self._table["row"])
        self._table["row"] = []

    def _table_end(self):
        if self._table["nested"]:
            self._table["nested"] -= 1
            if self._table["cell"] is not None:
                self._table["cell"]["buf"].append(" ")
            return
        self._table_row_end()
        rows = self._table["rows"]
        self._table = None
        if rows:
            # Spans degrade predictably: the spanning cell becomes a separate
            # full-measure row.  Other cells from its source row retain their
            # order in a following ordinary row.  No cell content disappears.
            out = []
            for row in rows:
                ordinary = []
                for cell in row:
                    if cell["colspan"] > 1 or cell["rowspan"] > 1:
                        if ordinary:
                            out.append(ordinary); ordinary = []
                        out.append([{"text": cell["text"], "full": True}])
                    else:
                        ordinary.append({"text": cell["text"], "full": False})
                if ordinary:
                    out.append(ordinary)
            self.blocks.append(("table", out))

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP:
            self._skip += 1
            return
        if t == "table":
            self._table_start(); return
        if self._table is not None:
            if t == "tr" and not self._table["nested"]:
                self._table_row_end()
            elif t in ("td", "th"):
                self._table_cell_start(attrs)
            elif t == "br" and self._table["cell"] is not None:
                self._table["cell"]["buf"].append(" ")
            return
        if t == "br":
            self._flush()
            return
        if t in self.BLOCK:
            self._flush()
            self._kind = "h" if t in self.HEAD else "p"
            return
        if t in ("img", "image"):
            # <img src=...> without the closing slash never reaches
            # handle_startendtag, and most EPUBs in the wild are written that
            # way even though the spec calls for XHTML.
            self._image(attrs)
            return
        if not self._skip and t in self.INLINE:
            pt = self.INLINE[t]
            self._open.append(pt)
            self._buf.append("<%s>" % pt)

    def handle_startendtag(self, tag, attrs):
        t = tag.lower()
        if t == "br":
            self._flush()
        elif t in ("img", "image"):
            self._image(attrs)

    def _image(self, attrs):
        """Record a picture as its own block, between paragraphs.

        Only the href is kept. Reading the bytes here would mean holding every
        plate of a picture book in memory from the moment it is opened; the
        renderer pulls each one out of the zip when its page is built."""
        if self._skip:
            return
        src = ""
        for k, v in attrs or ():
            if k and k.lower() in ("src", "href", "xlink:href") and v:
                src = v
                break
        if src:
            self._flush()
            self.blocks.append(("img", src))

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP:
            if self._skip > 0:
                self._skip -= 1
            return
        if self._table is not None:
            if t == "table":
                self._table_end()
            elif t in ("td", "th"):
                self._table_cell_end()
            elif t == "tr":
                self._table_row_end()
            return
        if t in self.BLOCK:
            self._flush()
            return
        if not self._skip and t in self.INLINE:
            pt = self.INLINE[t]
            # Close only if it is actually open. A stray </em> is common in
            # hand-made EPUBs and must not emit an unbalanced tag, which would
            # make the paragraph fail to parse and lose the text entirely.
            if pt in self._open:
                while self._open:
                    top = self._open.pop()
                    self._buf.append("</%s>" % top)
                    if top == pt:
                        break

    def handle_data(self, data):
        if self._skip:
            return
        if self._table is not None:
            if self._table["cell"] is not None:
                self._table["cell"]["buf"].append(escape_markup(data))
            return
        self._buf.append(escape_markup(data))

    def _flush(self):
        # Close what is open so this block stands alone as valid markup, then
        # reopen it in the next: emphasis that runs across a paragraph break is
        # legal HTML and should keep going rather than end at the boundary.
        carry = list(self._open)
        closing = "".join("</%s>" % t for t in reversed(self._open))
        text = " ".join("".join(self._buf + [closing]).split())
        self._buf = ["<%s>" % t for t in carry]
        kind = self._kind
        self._kind = "p"
        # A block of tags and nothing else is not a block.
        if text and _MARKUP_TEXT_RE.sub("", text).strip():
            self.blocks.append((kind, text))

    def close(self):
        while self._table is not None:
            self._table_end()
        super().close()
        self._flush()
        return self.blocks


def _epub_extract(data):
    """Decode one XHTML document (bytes) and return its (kind, text) blocks."""
    text = None
    declared = None
    try:
        head = data[:256].decode("ascii", "ignore")
        match = re.search(r"encoding\s*=\s*['\"]([A-Za-z0-9._-]+)", head,
                          re.IGNORECASE)
        if match:
            candidate = match.group(1).lower()
            if candidate in {"utf-8", "utf-16", "utf-16le", "utf-16be",
                             "iso-8859-1", "windows-1252"}:
                declared = candidate
    except Exception:
        pass
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ["utf-16"]
    elif data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings = ["utf-32"]
    else:
        encodings = [declared] if declared else []
        encodings += ["utf-8", "latin-1"]
    for enc in encodings:
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
        names = _epub_names_bounded(zf)

        # META-INF/container.xml points at the .opf package document.
        opf_path = None
        try:
            root = ET.fromstring(_epub_read_limited(
                zf, "META-INF/container.xml", EPUB_XML_MAX))
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
            opf = ET.fromstring(_epub_read_limited(
                zf, opf_path, EPUB_XML_MAX))
        except Exception:
            return None, "This EPUB's package document could not be parsed."

        # manifest id -> href, then the spine's reading order of those ids.
        manifest = {}
        nav_ids = set()
        for el in opf.iter():
            if _epub_localname(el.tag) == "item":
                iid, href = el.get("id"), el.get("href")
                if iid and href:
                    manifest[iid] = href
                    if "nav" in (el.get("properties") or "").split():
                        nav_ids.add(iid)
        # A spine itemref marked linear="no" is auxiliary content — a table of
        # contents, a notes page — that EPUB puts OUTSIDE the default reading
        # order, and so is the manifest's properties="nav" document. Counting
        # either as a chapter opened the book on a dead list of links titled
        # "CHAPTER 1" and numbered every real chapter one too high, so the
        # eyebrow over "Chapter One" read "CHAPTER 2".
        every = [el.get("idref") for el in opf.iter()
                 if _epub_localname(el.tag) == "itemref" and el.get("idref")]
        spine = [el.get("idref") for el in opf.iter()
                 if _epub_localname(el.tag) == "itemref" and el.get("idref")
                 and (el.get("linear") or "").strip().lower() != "no"
                 and el.get("idref") not in nav_ids]
        if not spine:
            # A book that marks its WHOLE spine auxiliary still has to be
            # readable: better an off-by-one eyebrow than an empty reader.
            spine = every
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

        text_left = EPUB_TEXT_TOTAL_MAX
        for full in order:
            data = None
            for cand in (full, unquote(full)):
                if cand in names:
                    try:
                        limit = min(EPUB_CHAPTER_MAX, text_left)
                        data = _epub_read_limited(zf, cand, limit)
                        text_left -= len(data)
                    except Exception:
                        data = None
                    break
            if data is None:
                continue
            blocks = _epub_extract(data)
            # An <img src> is relative to the DOCUMENT it appears in, not to the
            # .opf, so it is resolved against this chapter's own directory and
            # then checked against the archive. An href that resolves to nothing
            # (a broken book, or a picture stored outside the zip) is dropped
            # here rather than becoming a blank gap on the page.
            here = posixpath.dirname(full)
            fixed = []
            for kind, payload in blocks:
                if kind != "img":
                    fixed.append((kind, payload))
                    continue
                ref = posixpath.normpath(
                    posixpath.join(here, payload.split("#", 1)[0]))
                for cand in (ref, unquote(ref)):
                    if cand in names:
                        fixed.append(("img", cand))
                        break
            blocks = fixed
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
        names = _epub_names_bounded(zf)
        opf_path = None
        try:
            root = ET.fromstring(_epub_read_limited(
                zf, "META-INF/container.xml", EPUB_XML_MAX))
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
            opf = ET.fromstring(_epub_read_limited(
                zf, opf_path, EPUB_XML_MAX))
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
        self._closed = False
        self._page = 0
        self._page_total = 0
        # Bumped every time the reading surface is swapped (_set_reader_widget)
        # AND on every page turn (_show_page). The deferred scroll restores
        # carry the value they were queued under, so a restore for the volume —
        # or the page — just left cannot land on the next one, and closing the
        # window invalidates whatever is still queued (see _on_destroy).
        self._nav = nbstate.Generation("reader")
        self._epub_chapters = []
        # (path, chapters) handed on by _open_book, which parses an EPUB before
        # it will shelve it (see there); consumed once by _open_epub so opening
        # a book never parses the same archive twice.
        self._epub_parsed = None
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
        self._store_extra = {}
        # Set by _load_state when the store on disk exists but could not be
        # read. The session then persists NOTHING: the reader's records stay
        # exactly where they are, at the path they would look for.
        self._store_damaged = False
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
        self.connect("destroy", self._on_destroy)

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
    @staticmethod
    def _as_books(v):
        """Whatever the shelf slot holds, as a list of records. A shelf stored as
        an OBJECT (keyed by path or title) still holds the volumes in its values;
        a scalar there holds none."""
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return list(v.values())
        return []

    def _load_state(self):
        """Restore the shelf and open book from ebook.json (best effort).

        A file that parses but is not shaped like this app's store is NOT
        written off. Every page turn, every Add and every Remove rewrites this
        whole file, so a shelf this loader shrugs off is destroyed by the next
        thing the reader does — and the file is the only record of where they
        had got to in twenty books. A bare list is read as the shelf it plainly
        is, a shelf under a renamed or extra wrapper key is found, and one
        stored as an object is read out of its values. Cookbook, Journal,
        Contacts and Meal Planner have read their stores this way for several
        rounds; the reader was the last store where a renamed wrapper cost the
        lot. Never raises: a surprise in one record costs itself."""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            # First run. Nothing to read and nothing at risk.
            return
        except Exception:
            # A file that EXISTS and will not parse is a different thing
            # entirely, and this used to be the same branch as first-run: the
            # reader opened on an empty shelf and the next page turn wrote a
            # new store over the old one. The bytes survived under a
            # .damaged- name nobody would find, so someone could lose their
            # place in twenty books and be shown a blank library with no
            # explanation. Novel, Music, Workout and Animation all take the
            # other path — read-only, and say so — and the reader was simply
            # the one that did not.
            self._store_damaged = True
            return
        if isinstance(data, list):
            data = {"books": data}
        elif not isinstance(data, dict):
            # Valid JSON, but a scalar — some other file, or a repair gone
            # wrong. Nothing to read, and nothing may overwrite it either.
            self._store_damaged = True
            return
        self._store_extra = {k: copy.deepcopy(v) for k, v in data.items()
                             if k not in ("books", "open", "read_pt")}
        # The reading size is a preference the reader set on purpose with
        # A-/A+, so it comes back with the shelf (Interaction Constitution
        # section 3, Restoration). Junk or an out-of-range value falls back to
        # the default rather than opening a book at 3pt.
        try:
            self._read_pt = max(self.READ_PT_MIN,
                                min(self.READ_PT_MAX, int(data["read_pt"])))
        except (KeyError, TypeError, ValueError):
            pass
        books = self._as_books(data.get("books"))
        if not books:
            # The wrapper key is gone or was written under another name. The
            # volumes are still in the file; take the first list of records that
            # looks like a shelf rather than opening on "No books" and writing
            # that empty shelf straight over them.
            for v in data.values():
                cand = self._as_books(v)
                if cand and any(isinstance(x, dict) and x.get("path")
                                for x in cand):
                    books = cand
                    break
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
                # Python's JSON decoder accepts NaN/Infinity even though they
                # are not valid JSON numbers.  A NaN survives the ordinary
                # min/max clamp below, then the resume path's reversed clamp
                # turns it into 1.0 and drops the reader at the bottom of the
                # page.  Treat every non-finite position as no saved scroll.
                if not math.isfinite(frac):
                    frac = 0.0
                known = {"path", "title", "fmt", "pos", "frac", "total",
                         "author"}
                rec = {k: copy.deepcopy(v) for k, v in b.items()
                       if k not in known}
                rec.update({
                    "path": str(b["path"]),
                    "title": str(b["title"]),
                    "fmt": str(b["fmt"]),
                    "pos": max(pos, 0),
                    "frac": min(max(frac, 0.0), 1.0),
                    "total": max(0, int(b.get("total") or 0))
                    if str(b.get("total") or 0).lstrip("-").isdigit() else 0,
                    "author": str(b.get("author") or ""),
                })
                self._books.append(rec)
        op = data.get("open")
        if isinstance(op, str) and any(b["path"] == op for b in self._books):
            self._open_path = op
        # LAST RESORT. If the file plainly holds books and we adopted none of
        # them, the next save writes an empty shelf over it. Valid JSON of the
        # wrong shape parses perfectly, so nbapp's generic quarantine cannot see
        # it — only this app knows the shape is not a shelf. Move it aside on
        # the way past instead, the way cookbook.py and language.py do.
        if not self._books and _holds_books(data):
            # The file plainly holds books and none were adopted. Refusing to
            # write is better than moving it aside and starting fresh: the
            # records stay exactly where the reader left them, at the path
            # they would look for.
            self._store_damaged = True

    # The reading generation is `self._nav`; `_doc_gen` stays as its documented
    # name (Article III §3) so a reader of the constitution — and the
    # lifecycle fixture — still find the counter where they expect it.
    @property
    def _doc_gen(self):
        return self._nav.token()

    @_doc_gen.setter
    def _doc_gen(self, value):
        # Keep the documented/test-stub compatibility field usable on an
        # object created with __new__ (before __init__ has installed _nav).
        if not hasattr(self, "_nav"):
            self._nav = nbstate.Generation("reader")
        self._nav.reset(value)

    def _save_state(self):
        """Persist the shelf and open book under $NB_HOME (best effort)."""
        protecting = False
        try:
            # Generic atomic writing preserves syntax-damaged JSON, but cannot
            # recognize valid JSON in a foreign shelf shape. Preserve either
            # kind explicitly before replacing it; a failed protective move
            # keeps the reader in retryable read-only mode.
            if getattr(self, "_store_damaged", False):
                protecting = True
                if not self._quarantine_store():
                    raise OSError("could not preserve unrecognized reader shelf")
                self._store_damaged = False
                protecting = False
            payload = copy.deepcopy(getattr(self, "_store_extra", {}))
            payload.update({"books": self._books, "open": self._open_path,
                            "read_pt": getattr(self, "_read_pt",
                                               self.READ_PT_DEFAULT)})
            nbapp.atomic_write_json(CONFIG_PATH, payload,
                                    ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            # A failed shelf write otherwise looks exactly like success until
            # the next launch restores the old library and reading position.
            # Use the shared notification path: this reader has no persistent
            # status strip of its own where the warning could remain visible.
            if protecting:
                # Stay read-only and retain the retry flag. Do not enqueue a
                # notification here: its own store write would violate this
                # branch's promise that no replacement write is attempted.
                self._save_error = nbapp.save_failure_reason(exc, CONFIG_PATH)
            else:
                nbapp.note_save_failure(self, exc, CONFIG_PATH)
            return False

    @staticmethod
    def _quarantine_store():
        if not os.path.exists(CONFIG_PATH):
            return True
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = "%s.damaged-%s" % (CONFIG_PATH, stamp)
            n = 2
            while os.path.exists(dest):
                dest = "%s.damaged-%s-%d" % (CONFIG_PATH, stamp, n)
                n += 1
            os.replace(CONFIG_PATH, dest)
            return True
        except OSError:
            return False

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
        self._library_trigger = lib_btn
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
            img = nbicons.image(name, 19, color)
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
        # This is that button. The notices (unsupported format, a file that has
        # gone, an archive that will not parse) carry it too: each of them ends
        # by telling the reader to choose a file, and an instruction with no
        # control under it is the empty state's own bug again.
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
        """Show `widget` as the sole child of the reader slot.

        This is the one point every document swap goes through (both readers and
        the message card), so it is where the document generation moves on and
        anything still queued against the old surface goes stale."""
        self._nav.bump()
        for child in self._reader_slot.get_children():
            self._reader_slot.remove(child)
        self._reader_slot.pack_start(widget, True, True, 0)
        widget.show_all()

    # --------------------------------------------------- reader dispatch
    def _show_current(self):
        """Repaint the reading surface for the open book, or the empty-state."""
        book = self._book_by_path(self._open_path) if self._open_path else None
        if book is None:
            # An unreadable store and an empty shelf look the same on screen
            # and are not the same thing.
            if getattr(self, "_store_damaged", False):
                self._show_damaged_store()
            else:
                self._show_empty()
            return
        # A book's title is the filename the reader chose, or the EPUB's own
        # metadata — "Notes.pdf" became "Notas" on a Spanish install while
        # ebook.json went on naming Notes.pdf.
        nbi18n.set_verbatim(self._title_lbl, book["title"])
        # Show the author when we have it (EPUB metadata), else the format.
        if book.get("author"):
            nbi18n.set_verbatim(self._subtitle_lbl, book["author"])
        else:
            self._subtitle_lbl.set_text(book["fmt"])
        # The file may have gone (a USB stick removed since it was added) — say
        # so plainly rather than letting the engine report a bogus parse error.
        if not os.path.isfile(book["path"]):
            self._unreadable_message(
                book["path"], book["title"], book["fmt"],
                _t("This file is no longer at that location."))
            return
        if book["fmt"] == "PDF":
            self._open_pdf(book)
        elif book["fmt"] == "EPUB":
            self._open_epub(book)
        else:
            self._unreadable_message(
                book["path"], book["title"], book["fmt"],
                _t("This document format is not supported."))

    def _show_damaged_store(self):
        """The shelf could not be read, so this session will not write.

        Shown INSTEAD of the empty state, because they look identical and mean
        opposite things: one is a reader with no books yet, the other is a
        reader whose twenty books are still on disk and temporarily out of
        reach. Saying nothing would leave the second one looking like the
        first, and then quietly refusing to remember anything."""
        self._title_lbl.set_text(_t("No document"))
        self._subtitle_lbl.set_text("")
        self._show_message(
            _t("READER"), _t("Your library could not be read"), "",
            _t("The books were kept, and nothing changed here will be "
               "saved over them."),
            action=True)

    def _show_empty(self):
        # _t throughout — but NOT for the reason this comment used to give.
        # It said _show_message "hands its arguments straight to set_text()",
        # so an unwrapped literal stayed English. MEASURED, and it is false:
        # nbi18n patches Gtk.Label.set_text itself (nbi18n.py:883-897), so a
        # bare catalog key handed to a setter at runtime is looked up on the
        # way through. Checked under NB_LANG=fr against the code as it was
        # before these _t() calls existed: the card already read "Ce fichier ne
        # se trouve plus a cet endroit."
        #
        # The _t() calls are kept because they are explicit and cost nothing,
        # and because the string a translator must find should be visible at
        # the call site. What is NOT true is that omitting one leaves English
        # on screen. The shape that genuinely defeats nbi18n is a string
        # SUBSTITUTED before it reaches the setter: the arriving text is no
        # longer a catalog key, and it is recoverable only through the format
        # table, which needs three-plus characters of literal anchor.
        # tools/runtime_translation_check.py is the gate for that.
        self._title_lbl.set_text(_t("No document"))
        self._subtitle_lbl.set_text("")
        self._show_message(
            _t("READER"), _t("No document open"),
            _t("Open an EPUB or PDF file."),
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
        # The heading is the book's title on every unreadable-file path.
        nbi18n.set_verbatim(self._card_heading, heading)
        self._card_detail.set_text(detail)
        self._card_note.set_text(note)
        self._card_action.set_visible(action)
        self._set_reader_widget(self._message_view)
        self._update_nav()

    def _show_page(self):
        """Render the current page/chapter for the active document mode.

        A page turn is a new reading state, so the generation moves on here as
        well as on a document swap: a resume-scroll queued for page 6 must not
        drop someone three quarters of the way down page 7 because they turned
        the page while the restore was still waiting on an idle tick."""
        self._nav.bump()
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

    def _on_destroy(self, *_):
        """Bank the place in the book, then retire the reading generation.

        Closing it invalidates every scroll restore still sitting on the main
        loop: those sources are dispatched even after the window is gone, and
        each of them would otherwise reach into a torn-down reading view."""
        # GTK teardown can be requested through nested close paths. Capture
        # exactly once: a second pass sees an already-destroyed scroller,
        # computes fraction 0, and would overwrite the real place saved here.
        if self._closed:
            return False
        self._closed = True
        self._remember_pos(force=True)
        self._nav.close()
        return False

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

    def _scroll_to_fraction(self, frac, token=None):
        """Put the reader back where they stopped reading on this page. Runs on
        an idle tick because the labels have not been laid out (and the
        adjustment still reads zero) until after the first allocation.

        `token` is the document generation this restore was queued for. Because
        it runs two idle ticks later, the reader may already have opened another
        volume (or removed/closed this one) in between, and _current_scroll()
        would then hand back the NEW document's scroller: the restore would drop
        someone three quarters of the way into a book they just opened at page
        one. A stale token means the restore has missed its document, so it is
        dropped rather than applied to whatever is showing now."""
        if not self._nav.valid(token):
            return False
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
        # At either end of the size range the stepper stays live and does
        # nothing, which is the one thing a control must never do: gate it on
        # the clamp too.
        pt = getattr(self, "_read_pt", self.READ_PT_DEFAULT)
        self._smaller_btn.set_sensitive(reading and pt > self.READ_PT_MIN)
        self._larger_btn.set_sensitive(reading and pt < self.READ_PT_MAX)
        # Both modes now turn pages: an EPUB chapter is cut into reading pages
        # (see EPUB_PAGE_BLOCKS), so "chapter" would no longer be true.
        prev_message = (
            _t("Previous page") if has_pages and n > 0 else
            (_t("This is the first page.") if has_pages else
             _t("No document is open.")))
        next_message = (
            _t("Next page") if has_pages and n < total - 1 else
            (_t("This is the last page.") if has_pages else
             _t("No document is open.")))
        for btn, message in ((self._prev_btn, prev_message),
                             (self._next_btn, next_message)):
            btn.set_tooltip_text(message)
            acc = btn.get_accessible()
            if acc is not None:
                acc.set_name(message)
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
        # Stamp the restore with the document showing NOW; _scroll_to_fraction
        # drops it if the surface has been swapped by the time it runs.
        token = self._nav.token()
        post = self._nav.guard(
            lambda: GLib.idle_add(self._scroll_to_fraction, frac, token) and False,
            token)
        GLib.idle_add(post)

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
        cached = getattr(self, "_epub_parsed", None)
        self._epub_parsed = None
        if cached is not None and cached[0] == book["path"]:
            chapters, err = cached[1], None
        else:
            chapters, err = _epub_load(book["path"])
        if chapters is None:
            self._unreadable_message(book["path"], book["title"], "EPUB", err)
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
        for ci, source in enumerate(chapters):
            # Rows are pagination atoms.  A long table is divided into table
            # fragments here, never in the renderer, so a row's wrapped text
            # can never be split between reading pages.
            blocks = []
            for kind, payload in source:
                if kind == "table":
                    for row in payload:
                        blocks.append(("tablerow", {"cells": row,
                                                    "table": payload}))
                else:
                    blocks.append((kind, payload))
            chapters[ci] = blocks
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

    @staticmethod
    def _epub_table_widths(rows, measure=560, pad=16, font_pt=17):
        """Two-pass column sizing in pixels: natural text maxima, then clamp
        the total to the reading measure while preserving a usable minimum."""
        cols = max((len(r) for r in rows if not (len(r) == 1 and
                   r[0].get("full"))), default=1)
        natural = [40] * cols
        cr = None
        if _CAIRO_OK:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
        for row in rows:
            if len(row) == 1 and row[0].get("full"):
                continue
            for ci, cell in enumerate(row[:cols]):
                markup = cell.get("text", "")
                if cr is not None:
                    layout = PangoCairo.create_layout(cr)
                    fd = Pango.FontDescription()
                    fd.set_family(TABLE_FAMILY)
                    fd.set_size(int(font_pt * Pango.SCALE))
                    layout.set_font_description(fd)
                    layout.set_markup(markup, -1)
                    text_w = layout.get_pixel_size()[0]
                else:
                    text_w = 9 * len(_MARKUP_TEXT_RE.sub("", markup))
                natural[ci] = max(natural[ci], min(360, text_w + pad))
        usable = max(cols * 40, measure)
        total = sum(natural)
        if total <= usable:
            spare = usable - total
            return [w + spare / cols for w in natural]
        floor = 40.0
        extra = max(0.0, usable - floor * cols)
        weights = [max(1.0, w - floor) for w in natural]
        weight_total = sum(weights)
        return [floor + extra * w / weight_total for w in weights]

    @classmethod
    def _epub_table_geometry(cls, rows, measure=560, font_pt=17,
                             sizing_rows=None):
        """Return the actual Pango-wrapped cell rectangles used by the table.

        Kept independent of a GDK display so formatting gates can inspect real
        text geometry on build hosts as well as in the guest.
        """
        if not _CAIRO_OK:
            return []
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        widths = cls._epub_table_widths(sizing_rows or rows, measure,
                                        font_pt=font_pt)
        geometry, y = [], 0
        for ri, row in enumerate(rows):
            full = len(row) == 1 and row[0].get("full")
            heights, x = [], 0
            for ci, cell in enumerate(row):
                width = sum(widths) if full else widths[min(ci, len(widths)-1)]
                layout = PangoCairo.create_layout(cr)
                fd = Pango.FontDescription()
                fd.set_family(TABLE_FAMILY)
                fd.set_size(int(font_pt * Pango.SCALE))
                layout.set_font_description(fd)
                layout.set_width(max(1, int((width - 16) * Pango.SCALE)))
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
                layout.set_markup(cell.get("text", ""), -1)
                heights.append(layout.get_pixel_size()[1] + 12)
                geometry.append({"row": ri, "col": ci, "x": x, "y": y,
                                 "width": width, "height": 0,
                                 "text": cell.get("text", "")})
                x += width
            row_h = max(heights or [12])
            for rect in geometry:
                if rect["row"] == ri:
                    rect["height"] = row_h
            y += row_h
        return geometry

    def _epub_table(self, rows):
        table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        table.get_style_context().add_class("readtable")
        # The table is as wide as its columns and no wider. Left alone it was
        # allocated the whole reading column, so every row's bottom rule ran on
        # past the last cell as an empty phantom column.
        table.set_halign(Gtk.Align.START)
        sizing_rows = rows[0]["table"] if rows else []
        # The measure is the reading COLUMN's width (620px less its rules),
        # exactly as it is for the paragraphs, and it does not change with the
        # type size — the column does not either. It used to be derived from
        # the paragraph character cap (min(560, cap * 9)), which is a cap on a
        # LABEL's natural width and shrinks as the type grows: at A+ maximum
        # that squeezed the columns to 342px inside a 620px page while the
        # prose beside them still filled it.
        widths = self._epub_table_widths(sizing_rows, 560,
                                         font_pt=self._read_pt)
        for descriptor in rows:
            row_data = descriptor["cells"]
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.get_style_context().add_class("readtablerow")
            full = len(row_data) == 1 and row_data[0].get("full")
            for ci, cell in enumerate(row_data):
                lbl = Gtk.Label(xalign=0)
                set_reading_text(lbl, cell.get("text", ""))
                lbl.set_line_wrap(True)
                lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_yalign(0)
                lbl.get_style_context().add_class("readbody")
                lbl.get_style_context().add_class("readtablecell")
                width = sum(widths) if full else widths[min(ci, len(widths)-1)]
                # A size request only raises a widget's MINIMUM width, and a
                # wrapping label's NATURAL width is its text unwrapped — so the
                # row box handed a long cell the whole sentence on one line,
                # which pushed the columns out of line with every other row and
                # dragged the table (and the reading column with it) past the
                # measure to the screen edge. Capping max-width-chars collapses
                # the natural width onto the minimum, so the cell asks for
                # exactly the column width _epub_table_widths computed and the
                # text wraps inside it.
                lbl.set_max_width_chars(1)
                lbl.set_size_request(max(1, int(width)), -1)
                row.pack_start(lbl, full, full, 0)
            table.pack_start(row, False, False, 0)
        return table

    def _build_epub_view(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("readscroll")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)

        col = self._new_epub_column()

        outer.pack_start(col, False, False, 0)
        scroll.add(outer)
        self._epub_col = col
        self._epub_scroll = scroll
        return scroll

    @staticmethod
    def _new_epub_column():
        """A document child for the stable EPUB page holder."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("readpage")
        col.set_halign(Gtk.Align.CENTER)
        col.set_size_request(620, -1)
        col.set_margin_top(72)
        col.set_margin_bottom(80)
        return col

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
            ttl = Gtk.Label()
            set_reading_text(ttl, title)
            ttl.get_style_context().add_class("readh1")
            ttl.set_halign(Gtk.Align.CENTER)
            ttl.set_justify(Gtk.Justification.CENTER)
            ttl.set_line_wrap(True)
            ttl.set_max_width_chars(30)
            ttl.set_margin_top(12)
            box.pack_start(ttl, False, False, 0)
        return box, body

    def _epub_image(self, entry, cap):
        """One plate from the open EPUB, scaled to the reading measure.

        Read on demand, from the zip, when the page is built — a picture book
        held open in memory would cost far more than the text it illustrates.
        Returns None for anything that will not decode, which is the honest
        outcome: a book may carry an SVG or a format GdkPixbuf was not built
        with, and a missing picture is better than a broken one or a crash.
        """
        if not PIXBUF_OK:
            return None
        path = self._open_path
        if not path or not os.path.isfile(path):
            return None
        try:
            with zipfile.ZipFile(path) as zf:
                data = _epub_read_limited(zf, entry, EPUB_IMAGE_MAX)
        except Exception:                                      # noqa: BLE001
            return None
        try:
            ldr = GdkPixbuf.PixbufLoader()
            # BOUNDED WHILE DECODING, not after. The scale below is a DISPLAY
            # cap — it runs once the whole image is already in memory, so a
            # cover plate stored at print resolution allocated its full size
            # first and was only then shrunk to fit the text column. size-
            # prepared arrives with the real dimensions and before any pixels
            # exist, which is the only point where a ceiling costs nothing.
            def _bound(loader, width, height):
                want = nbapp.decode_budget(width, height)
                if want != (width, height):
                    loader.set_size(want[0], want[1])
            ldr.connect("size-prepared", _bound)
            ldr.write(data)
            ldr.close()
            pb = ldr.get_pixbuf()
        except Exception:                                      # noqa: BLE001
            return None
        if pb is None:
            return None
        # The measure is the column's, in characters; a plate wider than the
        # text block would push the page sideways and force a horizontal
        # scrollbar over the prose. Never enlarged — a 200px thumbnail blown up
        # to the column width looks worse than a 200px thumbnail.
        want = max(160, min(560, cap * 9))
        if pb.get_width() > want:
            h = max(1, int(pb.get_height() * (want / float(pb.get_width()))))
            try:
                pb = pb.scale_simple(want, h, GdkPixbuf.InterpType.BILINEAR)
            except Exception:                                  # noqa: BLE001
                pass
        img = Gtk.Image.new_from_pixbuf(pb)
        img.set_halign(Gtk.Align.CENTER)
        img.set_margin_top(10)
        img.set_margin_bottom(14)
        return img

    def _epub_show_chapter(self, to_top=True):
        old_col = self._epub_col
        if old_col is None:
            return
        # A page turn replaces one document surface with another. Build the new
        # page offscreen and crossfade the stable holder once, rather than
        # revealing dozens of paragraphs independently. A type-size reflow is
        # the same page and stays instant.
        col = self._new_epub_column() if to_top else old_col
        if not to_top:
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
        table_rows = []
        def flush_table():
            if table_rows:
                col.pack_start(self._epub_table(list(table_rows)),
                               False, False, 0)
                del table_rows[:]
        for kind, text in blocks:
            if kind == "tablerow":
                table_rows.append(text)
                continue
            flush_table()
            if kind == "img":
                img = self._epub_image(text, cap)
                if img is not None:
                    col.pack_start(img, False, False, 0)
                continue
            # `text` is Pango markup now (italic/bold survive the
            # EPUB), so it must not go through label=, which would
            # show the tags.
            lbl = Gtk.Label(xalign=0)
            set_reading_text(lbl, text)
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
        flush_table()
        col.show_all()
        if to_top:
            # nbmotion-inventory: content.ebook
            # The page/chapter turn: ONE container replacement of the document
            # column, on the PAGE token. A chapter genuinely IS a different
            # document, so this is a replacement (Article C §C2) and not a
            # transform — and emphatically not per-paragraph reveals, which
            # would shimmer a page of text into view. Gated on `to_top`, so a
            # reading-SIZE change re-flows in place and does not throw the
            # reader back to the top of the chapter.
            holder = old_col.get_parent()
            if holder is not None:
                try:
                    nbtransitions.replace(
                        holder, col, duration=nbtransitions.PAGE,
                        pack=lambda box, child: box.pack_start(
                            child, False, False, 0))
                except Exception:                                 # noqa: BLE001
                    # Motion must never gate the document. Install the completed
                    # page plainly if the transition primitive cannot run.
                    for child in holder.get_children():
                        holder.remove(child)
                    holder.pack_start(col, False, False, 0)
                    col.show_all()
                self._epub_col = col
        # A page/chapter turn starts at the top; a re-flow after a reading-size
        # change must NOT throw the reader back to the top of the chapter.
        if to_top:
            GLib.idle_add(self._nav.guard(self._scroll_top), self._epub_scroll)

    # --------------------------------------------------- PDF rendering
    def _open_pdf(self, book):
        """Open the PDF with Poppler and render its first page. When Poppler is
        unavailable (guarded import) or the file cannot be opened, fall back to a
        neutral notice — the app never crashes."""
        if not POPPLER_OK:
            self._unreadable_message(
                book["path"], book["title"], "PDF",
                _t("The PDF engine is unavailable in this build."))
            return
        try:
            uri = GLib.filename_to_uri(os.path.abspath(book["path"]), None)
        except Exception:
            uri = "file://" + os.path.abspath(book["path"])
        try:
            doc = Poppler.Document.new_from_file(uri, None)
            npages = doc.get_n_pages()
        except Exception:
            self._unreadable_message(
                book["path"], book["title"], "PDF",
                _t("This PDF could not be opened for rendering."))
            return
        if npages <= 0:
            self._unreadable_message(
                book["path"], book["title"], "PDF",
                _t("This PDF has no pages to render."))
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
        if self._pdf_page_obj is None:
            # A page that cannot be fetched used to become a BLANK WHITE
            # SURFACE: _pdf_relayout returns early on a missing page and
            # _pdf_draw then paints white, so pulling the USB stick a book
            # was opened from and pressing Right showed an empty page and no
            # explanation at all. The reader already knows how to say both of
            # these things — it says the first one when a book is opened from
            # a device that has gone — so say them here too.
            book = (self._book_by_path(self._open_path)
                    if self._open_path else None)
            if book is not None:
                gone = not os.path.isfile(book["path"])
                self._unreadable_message(
                    book["path"], book["title"], book["fmt"],
                    _t("This file is no longer at that location.") if gone
                    else _t("This PDF could not be opened for rendering."))
                return
        self._pdf_relayout()
        # a fresh page returns the reader to the top of the viewport
        GLib.idle_add(self._nav.guard(self._scroll_top), self._pdf_scroll)

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
        # The device scale is part of the KEY as well as the size: a page
        # rasterized for a 1x screen must not be reused after the window moves
        # to a 2x one, or the reader would keep showing the soft copy.
        sf = max(1, int(self.get_scale_factor() or 1))
        key = (self._page, round(self._pdf_scale, 4), sf)
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
            # RASTERIZED AT DEVICE RESOLUTION. A PDF is vector, so on a HiDPI
            # panel the page can be rendered at the screen's real pixel density
            # instead of being rasterized at logical size and then stretched by
            # the compositor -- which is what a page of body text can least
            # afford, since every stem in every letter goes through the
            # interpolator. The surface carries the scale, so the ctx.scale()
            # and page.render() below still work in logical units and the blit
            # in _pdf_draw still places the page at its logical size; only the
            # grid underneath gets finer.
            # BOUNDED. Page size times zoom times device scale is three
            # multipliers and none of them was capped: a large page zoomed in
            # on a HiDPI panel asks for a surface of w*h*sf^2*4 bytes, and the
            # zoom is a button a person can keep pressing. On a machine with
            # no swap that is not a slow render, it is the end of the app.
            # When the ask is too big the DENSITY drops rather than the page —
            # the surface carries the scale, so the blit still places the page
            # at its logical size and the only loss is sharpness.
            want = nbapp.decode_budget(int(w * sf), int(h * sf))
            eff = sf * min(1.0, want[0] / float(max(1, int(w * sf))))
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                         max(1, int(w * eff)),
                                         max(1, int(h * eff)))
            surface.set_device_scale(eff, eff)
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

    def _set_read_size(self, points):
        """Set the reading size to `points` (clamped) and remember it.

        The size is a preference the reader set deliberately, so it is written
        with the shelf and restored on the next launch; it used to live only in
        this process, so every relaunch put the type back to 17pt and someone
        who needs large text had to set it again every single time."""
        points = max(self.READ_PT_MIN, min(self.READ_PT_MAX, int(points)))
        if points == self._read_pt:
            return
        self._read_pt = points
        self._apply_read_size()
        self._resize_reader()
        self._update_nav()
        self._save_state()

    def _on_text_larger(self, *_):
        """Step the reading size up one point, clamped at READ_PT_MAX."""
        self._set_read_size(self._read_pt + 1)

    def _on_text_smaller(self, *_):
        """Step the reading size down one point, clamped at READ_PT_MIN."""
        self._set_read_size(self._read_pt - 1)

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
        before_books = copy.deepcopy(self._books)
        before_open = self._open_path
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
            # Read the book BEFORE shelving it. A file that will not parse used
            # to be added to the library AND made the open book, so the notice
            # came back as the whole reader on every launch and the shelf
            # listed a volume that can never be opened, with nothing on its row
            # saying why. Nothing is shelved unless the reader can be given the
            # book. The parse is kept for _open_epub, a few lines below, so a
            # long book is still read exactly once.
            chapters, err = _epub_load(path)
            if chapters is None:
                self._title_lbl.set_text(_t("No document"))
                self._subtitle_lbl.set_text("")
                self._unreadable_message(path, title, fmt, err)
                return True
            self._epub_parsed = (path, chapters)
            mt, ma = _epub_meta(path)
            if mt:
                title = mt
            if ma:
                author = ma
        self._add_book(path, title, fmt, author)
        self._open_path = path
        if not self._save_state():
            # Do not present a library/open-book change that the next launch
            # will silently undo. The save helper has already posted the
            # actionable storage failure; restore the last durable shelf and
            # reading surface in place.
            self._books = before_books
            self._open_path = before_open
            self._show_current()
            self._populate_shelf()
            return True
        self._show_current()
        self._populate_shelf()
        return True

    def _unsupported_message(self, path):
        """Neutral notice when a chosen/opened file is not a readable format —
        never a silent no-op, which would just look broken to the reader."""
        self._title_lbl.set_text(_t("No document"))
        self._subtitle_lbl.set_text("")
        self._show_message(
            _t("READER"), _t("Unsupported format"),
            _t("%s can't be opened. Supported formats: EPUB, PDF.")
            % os.path.basename(path),
            _t("Choose an EPUB or PDF file."), action=True)

    def _unreadable_message(self, path, title, fmt, reason):
        """Neutral notice for a volume that cannot be put on the reading
        surface — the file has gone, the format is not one this build renders,
        the archive will not parse.

        WHAT WENT WRONG is the card's own line and the file sits under it in
        the caption. These notices used to be the other way round, so the
        largest text on the card was a file path the reader already knew and
        the reason was the smallest grey line beneath it."""
        self._show_message(fmt, title, reason, self._short_path(path),
                           action=True)

    def _add_book(self, path, title, fmt, author=""):
        """Insert a book at the front of the shelf, de-duplicating by path so
        re-opening a volume moves it to the top rather than listing it twice.
        A volume already on the shelf keeps everything it had saved.

        THE BUG THIS EXISTS FOR: this used to BUILD A FRESH record from
        path/title/fmt/pos/author, which silently dropped every other key the
        existing one held -- `frac`, how far down the page the reader had got,
        and `total`, the page count the shelf shows progress against. Opening a
        book from the Library is the commonest action in the app, and
        _open_book() calls _save_state() right after this, so the loss went
        straight to disk: the reader was dropped at the TOP of the right page
        instead of the three-quarter mark they stopped at, and the shelf row's
        "Page 6 / 9" vanished. _resume_scroll() then read this same stripped
        record, so the restore it exists to perform was a silent no-op.
        Carrying the record forward also means a key added here later cannot be
        quietly thrown away by this path."""
        existing = self._book_by_path(path)
        rec = dict(existing) if isinstance(existing, dict) else {}
        try:
            pos = max(int(rec.get("pos") or 0), 0)
        except (TypeError, ValueError):
            pos = 0
        rec.update({"path": path, "title": title, "fmt": fmt, "pos": pos,
                    "author": author or ""})
        self._books = [b for b in self._books if b["path"] != path]
        self._books.insert(0, rec)

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
        try:
            sheet.get_accessible().set_role(Atk.Role.DIALOG)
            sheet.get_accessible().set_name(_t("Library"))
        except Exception:
            pass
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
            label=_t("EPUB and PDF files. Add one with Open Book…, below."),
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
        self._library_add_btn = add_btn
        foot.pack_start(add_btn, False, False, 0)
        done = Gtk.Button(label=_t("Done"))
        done.set_relief(Gtk.ReliefStyle.NONE)
        done.get_style_context().add_class("sheetbtn")
        done.connect("clicked", lambda *_: self._close_library())
        self._library_done_btn = done
        foot.pack_start(done, False, False, 0)
        box.pack_start(foot, False, False, 0)

        scrim.add(sheet)
        scrim.set_no_show_all(True)
        try:
            revealer = Gtk.Revealer()
            revealer.set_reveal_child(False)
            revealer.add(scrim)
            revealer.set_no_show_all(True)
            self._library_sheet_revealer = True
            return revealer
        except Exception:                                       # noqa: BLE001
            # The sheet remains usable on a GTK build where Revealer cannot be
            # constructed; motion is never a prerequisite for Library access.
            self._library_sheet_revealer = False
            return scrim

    def _book_row(self, book):
        """One Library row: title over a 'format · author/path' line as a click
        target that opens the volume, with a trailing Remove control (its own
        button, so a remove click never also opens the book)."""
        entry = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        bt = Gtk.Label(xalign=0)
        nbi18n.set_verbatim(bt, book["title"])
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

        # A native button makes the primary row action keyboard reachable while
        # remaining a separate sibling of Remove.
        open_area = Gtk.Button()
        open_area.set_relief(Gtk.ReliefStyle.NONE)
        open_area.get_style_context().add_class("bookopen")
        open_area.connect("clicked", self._on_book_open, book["path"])
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
        previous = set(getattr(self, "_shelf_paths", ()))
        current = tuple(book["path"] for book in self._books)
        arriving = set(current) - previous
        for child in shelf.get_children():
            shelf.remove(child)
        opening = []
        if not self._books:
            # Already names the way in ("Use Open Book… below"); it just never
            # said it in the reader's language — the catalogs carry this string
            # in all 17 and the label was handed the raw English.
            empty = Gtk.Label(
                label=_t("No books.\n"
                         "Use Open Book… below to add an EPUB or PDF file."),
                xalign=0)
            empty.get_style_context().add_class("sheetempty")
            empty.set_line_wrap(True)
            shelf.pack_start(empty, False, False, 0)
        else:
            for book in self._books:
                row = self._book_row(book)
                if book["path"] in arriving:
                    try:
                        rev = Gtk.Revealer()
                        rev.set_reveal_child(False)
                        rev.add(row)
                        shelf.pack_start(rev, False, False, 0)
                        opening.append(rev)
                        continue
                    except Exception:                             # noqa: BLE001
                        pass
                shelf.pack_start(row, False, False, 0)
        self._shelf_paths = current
        shelf.show_all()
        # All new books settle together: even a bulk import is one short
        # response, never a hundreds-row staggered cascade.
        for rev in opening:
            try:
                nbtransitions.reveal(
                    rev, True, direction=nbtransitions.SLIDE_DOWN,
                    duration=nbtransitions.SURFACE_IN)
            except Exception:                                   # noqa: BLE001
                try:
                    rev.set_reveal_child(True)
                except Exception:                               # noqa: BLE001
                    pass

    def _on_library_open(self, *_):
        # Rebuild the shelf before showing so newly opened books and the current
        # open-book highlight are reflected each time the sheet is shown.
        self._populate_shelf()
        self._library_restore_focus = self.get_focus()
        self._library_sheet.show()
        # The scrim carries set_no_show_all(True) — it must never be revealed
        # by an ancestor show_all() — and gtk_widget_show_all() RETURNS
        # IMMEDIATELY on such a widget. So show_all() on the scrim showed
        # nothing at all: the Library icon and Library ▸ Open Library… reported
        # a revealed revealer over an unmapped 1x1 scrim, and the sheet never
        # appeared. show() ignores the flag: show the scrim itself, then
        # show_all the sheet inside it.
        scrim = self._library_sheet
        if getattr(self, "_library_sheet_revealer", False):
            scrim = self._library_sheet.get_child()
            if scrim is not None:
                scrim.show()
        sheet = scrim.get_child() if scrim is not None else None
        if sheet is not None:
            sheet.show_all()
        focusables = self._library_focusables()
        if focusables:
            focusables[0].grab_focus()
        if not getattr(self, "_library_sheet_revealer", False):
            return
        try:
            nbtransitions.reveal(
                self._library_sheet, True,
                direction=nbtransitions.SLIDE_UP,
                duration=nbtransitions.SURFACE_IN)
        except Exception:                                       # noqa: BLE001
            try:
                self._library_sheet.set_reveal_child(True)
            except Exception:                                   # noqa: BLE001
                pass

    def _on_library_add(self, *_):
        """Open Book… from inside the Library: dismiss the sheet, then run the
        same chooser as File ▸ Open so the new volume lands on the reader."""
        self._close_library()
        self._file_open()

    def _on_book_open(self, _row, path):
        """Open the tapped library volume, then dismiss the sheet."""
        if path:
            self._open_book(path)
        self._close_library()
        return True

    def _close_library(self):
        restore = getattr(self, "_library_restore_focus", None)
        self._library_restore_focus = None
        if restore is not None:
            try:
                restore.grab_focus()
            except Exception:
                pass
        def hidden(_completed=True):
            self._library_sheet.hide()
        if not getattr(self, "_library_sheet_revealer", False):
            hidden(False)
            return True
        try:
            nbtransitions.reveal(
                self._library_sheet, False,
                direction=nbtransitions.SLIDE_DOWN,
                duration=nbtransitions.SURFACE_OUT,
                on_done=hidden)
        except Exception:                                       # noqa: BLE001
            try:
                self._library_sheet.set_reveal_child(False)
            except Exception:                                   # noqa: BLE001
                pass
            hidden(False)
        return True

    def _library_focusables(self):
        """Visible controls belonging to the modal Library sheet, in order."""
        out = []

        def walk(widget):
            if isinstance(widget, Gtk.Button):
                try:
                    if widget.get_visible() and widget.get_sensitive():
                        out.append(widget)
                except Exception:
                    pass
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    walk(child)

        walk(self._library_sheet)
        return out

    # ---------------------------------------------- remove from library
    def _on_book_remove(self, _btn, book):
        self._open_confirm_remove(book)

    def _open_confirm_remove(self, book):
        """Confirm before dropping a volume from the shelf — a destructive
        action, so it is never one stray click. The file itself is left alone;
        only the library entry is removed."""
        self._close_confirm()
        self._confirm_restore_focus = self.get_focus()
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
        dialog_acc = holder.get_accessible()
        dialog_acc.set_role(Atk.Role.DIALOG)
        dialog_acc.set_name(_t("Remove from library"))
        dialog_acc.notify_state_change(Atk.StateType.MODAL, True)
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
        self._confirm_accessible = dialog_acc
        self._confirm_cancel = cancel
        self._confirm_remove = remove
        # Destructive confirmations always begin on the safe action. Deferring
        # until after show_all lets GTK map the button before it receives focus.
        GLib.idle_add(lambda: (cancel.grab_focus(), False)[1]
                      if self._confirm_layer is layer else False)

    def _close_confirm(self):
        layer = self._confirm_layer
        if layer is not None:
            try:
                self._confirm_accessible.notify_state_change(
                    Atk.StateType.MODAL, False)
            except Exception:
                pass
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._confirm_layer = None
            self._confirm_accessible = None
            self._confirm_cancel = None
            self._confirm_remove = None
            prior = getattr(self, "_confirm_restore_focus", None)
            self._confirm_restore_focus = None
            if prior is not None:
                try:
                    prior.grab_focus()
                except Exception:
                    pass
            return True
        return False

    def _remove_book(self, book):
        """Drop the volume from the shelf (its reading position with it). If it
        was the open book, return the reader to the empty-state. The file on
        disk is untouched."""
        self._close_confirm()
        path = book["path"]
        previous_books = self._books
        previous_open = self._open_path
        self._books = [b for b in self._books if b["path"] != path]
        if self._open_path == path:
            self._open_path = None
        if not self._save_state():
            # Do not show a shelf removal that the next launch will undo, and
            # do not blank a document whose durable library record still says
            # it is open.
            self._books = previous_books
            self._open_path = previous_open
            self._populate_shelf()
            return
        if previous_open == path:
            self._show_empty()
        self._populate_shelf()

    # --------------------------------------------------------- keyboard
    def _on_key(self, w, ev):
        """Reading-surface keys. Esc backs out of an open overlay (the remove
        confirmation, then the Library sheet) before the base handler can treat
        it as a quit — so pressing Esc to dismiss a sheet never closes the whole
        app. Left/Right turn the page (PDF) or chapter (EPUB) while reading, when
        nothing is layered over the document."""
        kv = ev.keyval
        if self._confirm_layer is not None and kv in (
                Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            cancel = getattr(self, "_confirm_cancel", None)
            remove = getattr(self, "_confirm_remove", None)
            if cancel is not None and remove is not None:
                backwards = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
                current = self.get_focus()
                target = (cancel if backwards and current is remove else
                          remove if not backwards and current is cancel else
                          remove if backwards else cancel)
                target.grab_focus()
                return True
        if (self._confirm_layer is None and self._library_sheet.get_visible()
                and kv in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab)):
            controls = self._library_focusables()
            if controls:
                current = self.get_focus()
                try:
                    i = controls.index(current)
                except ValueError:
                    i = 0
                step = -1 if ev.state & Gdk.ModifierType.SHIFT_MASK else 1
                controls[(i + step) % len(controls)].grab_focus()
                return True
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
            # Open Library… keeps its ellipsis: the shelf is a modal sheet you
            # pick a book from, not an inline panel. Close Library greys out
            # when the sheet is already down — it used to stay live and do
            # nothing, which is the one thing a menu item must never do.
            return [
                ("Open Library…", self._on_library_open),
                ("Close Library",
                 self._close_library if self._library_sheet.get_visible()
                 else None),
            ]
        return super().menu_items(name)

    # ------------------------------------------------------------------ css
    def _install_css(self):
        css = b"""
        .readbar { background: #F4F2EC; border-bottom: 1px solid #D7D2C5; }
        .readbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .toolbtn { min-width: 32px; min-height: 32px; padding: 0; margin: 0;
                   background: transparent; border: none; box-shadow: none;
                   border-radius: 8px; color: #1A1916; }
        .toolbtn:hover { background: #F1EEE6; }
        /* A control that cannot be used says so by its INK WEIGHT (Papertone's
           unavailable family): same shape, same place in the row, printed
           faintly. The nav glyphs are drawn pixbufs, so no CSS colour can
           reach them and only opacity dims the icon and its button together:
           without it the page arrows on the first/last page, and the size
           steppers with no document at all, were the same solid ink as a live
           control. */
        .toolbtn:disabled { background: transparent; opacity: 0.45; }
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
                       font-size: 11px; letter-spacing: 0.22em; color: #6E695E;
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
                      border: 1px solid #1A1916; border-radius: 8px;
                      box-shadow: none; font-size: 14px; font-weight: 600; }
        .readaction label { color: #FCFBF8; }
        .readaction:hover { background: #3A362E; border-color: #3A362E; }
        /* EPUB reading paragraphs + chapter headings (serif reading face). The
           font SIZE of .readpara comes from the live .readbody rule so the
           A-/A+ steppers scale the body text; headings keep a stable size. */
        .readpara { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    color: #1A1916; }
        .readchhead { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                      font-size: 24px; font-weight: 600; color: #1A1916; }
        .readtable { margin: 8px 0 18px 0; border-top: 1px solid #6E695E;
                     border-left: 1px solid #6E695E; }
        .readtablerow { border-bottom: 1px solid #6E695E; }
        .readtablecell { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                         color: #1A1916; padding: 6px 8px;
                         border-right: 1px solid #6E695E; }
        /* PDF page sheet: a white leaf on the papertone mat. */
        .pdfpaper { background: #FCFBF8; border: 1px solid #D7D2C5;
                    box-shadow: 2px 3px 0 rgba(26,25,22,0.10); }

        .scrim { background: rgba(26,25,22,0.18); }
        .sheet { background: #FCFBF8; border: 1px solid #C9C4B6;
                 box-shadow: 4px 4px 0 rgba(26,25,22,0.15); }
        .sheet * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .sheetttl { font-size: 17px; font-weight: 700; color: #1A1916; }
        .sheetsub { font-size: 13px; color: #6E695E; }
        .sheetscroll, .sheetscroll viewport { background: #FCFBF8; }
        .sheetshelf { border-top: 1px solid #D7D2C5; }
        .sheetempty { font-size: 13px; color: #6E695E; padding: 18px 2px; }
        .sheetbook { border-bottom: 1px solid #D7D2C5; padding: 14px 2px; }
        .sheetbookopen { border-left: 2px solid #C8341E; padding-left: 10px; }
        .sheetbookttl { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                        font-size: 17px; color: #1A1916; }
        .sheetbooksub { font-size: 12px; color: #6E695E; }
        .bookopen { padding: 0; border: none; background: transparent;
                    background-image: none; box-shadow: none; }
        /* The label node needs the colour too: the Papertone theme sets
           `* { color: ink }`, and a rule that matches a node directly always
           beats a colour inherited from its parent, so colouring only the
           button left "Done" as ink-on-ink: an unreadable black slab. */
        .sheetbtn { min-width: 96px; min-height: 38px; background: #1A1916;
                    color: #FCFBF8; border: none; border-radius: 8px;
                    box-shadow: none; font-size: 14px; font-weight: 600; }
        .sheetbtn label { color: #FCFBF8; }
        .sheetbtn:hover { background: #3A362E; }
        /* secondary sheet action (Open Book): a bordered paper button */
        .sheetbtn2 { min-width: 96px; min-height: 38px; background: #FCFBF8;
                     color: #1A1916; border: 1px solid #C9C4B6;
                     border-radius: 8px; box-shadow: none; font-size: 14px;
                     font-weight: 600; }
        .sheetbtn2:hover { background: #EFEBE0; }
        /* per-row Remove control: muted ink, warm alert tint on hover */
        .rmbook { min-width: 30px; min-height: 30px; padding: 0;
                  background: transparent; border: none; box-shadow: none;
                  border-radius: 8px; }
        .rmbook:hover { background: #EAE3D2; }
        /* remove-from-library confirmation card */
        .confirm { background: #FCFBF8; border: 1px solid #C9C4B6;
                   box-shadow: 4px 4px 0 rgba(26,25,22,0.15);
                   padding: 26px 30px; }
        .confirm * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .confirmttl { font-size: 17px; font-weight: 700; color: #1A1916; }
        .confirmbody { font-size: 13px; color: #6E695E; }
        .confirmbtn { min-width: 88px; min-height: 36px; background: #FCFBF8;
                      color: #1A1916; border: 1px solid #C9C4B6;
                      border-radius: 8px; box-shadow: none; font-size: 14px; }
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
