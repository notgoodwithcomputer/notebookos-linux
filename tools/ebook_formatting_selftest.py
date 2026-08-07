#!/usr/bin/env python3
"""
Italics survive the EPUB.

`_EpubBlocks` reduced every chapter to plain `(kind, text)`, so `<em>` and
`<strong>` were discarded along with every other tag (ROADMAP #34). In a novel
that is not a detail: emphasis, book titles, foreign words, ships' names and a
character's inner voice are all italics, and the reader was given none of them —
"he said he was *fine*" and "he said he was fine" are different sentences.

Italic and bold travel as Pango markup. Tables are asserted from the same Pango
geometry the reader lays out: common row bands, stable column positions, and
page boundaries between complete rows.

A real EPUB is built here — a zip with a mimetype, a container, an OPF and two
XHTML documents — and opened through the app's own loader, so the assertions run
against whatever the shipped parser actually produces.

Run:
    tools/guestrun.sh python3 tools/ebook_formatting_selftest.py
    tools/guestrun.sh python3 tools/ebook_formatting_selftest.py --de DIR
"""
import os
import sys
import shutil
import zipfile
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-ebookfmt-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

import ebook  # noqa: E402

FAILED, N = [], [0]


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


LONG_ROWS = "".join(
    "<tr><td>R%02d-A</td><td>R%02d-B wrapped words</td><td>R%02d-C</td></tr>"
    % (n, n, n) for n in range(72))

CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title>
<style>p{color:red}</style></head><body>
<h1>The <em>Hesperus</em> Sails</h1>
<p>He said he was <em>fine</em>, and she did <strong>not</strong> believe him.</p>
<p>Tom &amp; Jerry cost &lt; 5 &gt; nothing.</p>
<p><i>Continued emphasis</i> and <b>bold</b> and <cite>a cited title</cite>.</p>
<p>A stray close tag </em> must not lose this sentence.</p>
<table><tr><th>Port</th><th>Depth</th><th>State</th></tr>%s</table>
<table><tr><td colspan="3">SPAN-WIDE</td><td>SPAN-AFTER</td></tr></table>
<table><tr><td>NEST-OUTER<table><tr><td>NEST-INNER-A</td><td>NEST-INNER-B</td></tr></table>NEST-END</td><td>NEST-PEER</td></tr></table>
<p><img src="images/plate.png" alt="a plate">After the plate.</p>
<script>var x = 1 &lt; 2;</script>
</body></html>""" % LONG_ROWS

CH2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>y</title></head><body>
<h1>Landfall</h1><p>Plain second chapter.</p></body></html>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>The Hesperus</dc:title><dc:creator>A Sailor</dc:creator>
<dc:identifier id="i">urn:uuid:1</dc:identifier></metadata>
<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>"""

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/book.opf"
 media-type="application/oebps-package+xml"/></rootfiles></container>"""


def make_png():
    """A 2x2 PNG, written byte by byte so the fixture needs no encoder."""
    import struct, zlib
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def build_epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("OEBPS/images/plate.png", make_png())
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/book.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", CH1)
        z.writestr("OEBPS/ch2.xhtml", CH2)


def plain(markup):
    return ebook._MARKUP_TEXT_RE.sub("", markup)


def block_text(block):
    kind, payload = block
    if kind == "table":
        return " ".join(plain(c.get("text", ""))
                        for row in payload for c in row)
    return payload if kind != "img" else ""


def main():
    path = os.path.join(_HOME, "hesperus.epub")
    build_epub(path)

    blocks = []
    for doc in (CH1, CH2):
        blocks.append(ebook._epub_extract(doc.encode("utf-8")))
    ch1 = blocks[0]
    flat = " ".join(block_text(b) for b in ch1)
    print("   chapter 1 parsed into %d blocks" % len(ch1))

    # ---- every block must be valid Pango markup ----------------------
    bad = []
    for kind, text in ch1 + blocks[1]:
        if kind == "table":
            texts = [c.get("text", "") for r in text for c in r]
        elif kind == "img":
            texts = []
        else:
            texts = [text]
        for cell_text in texts:
            try:
                Pango.parse_markup(cell_text, -1, "\0")
            except Exception as exc:
                bad.append((cell_text[:50], str(exc)[:40]))
    check("every block parses as Pango markup (%s)" % (bad[:1] or "ok"), not bad)

    # ---- emphasis survives -------------------------------------------
    check("<em> becomes italic", "<i>fine</i>" in flat)
    check("<strong> becomes bold", "<b>not</b>" in flat)
    check("<i> and <b> pass through", "<i>Continued emphasis</i>" in flat
          and "<b>bold</b>" in flat)
    check("<cite> is italic too", "<i>a cited title</i>" in flat)
    check("a heading keeps its emphasis",
          any(k == "h" and "<i>Hesperus</i>" in t for k, t in ch1))

    # ---- and the text itself is not damaged --------------------------
    check("an ampersand is escaped, not dropped", "Tom &amp; Jerry" in flat)
    check("literal angle brackets are escaped",
          "&lt; 5 &gt;" in flat)
    check("a stray close tag does not lose the sentence",
          "must not lose this sentence" in flat)
    check("script contents are still dropped", "var x" not in flat)

    # ---- table model and deliberate degradations ---------------------
    tables = [payload for kind, payload in ch1 if kind == "table"]
    check("table parser preserves every cell in reading order",
          all(flat.index("R%02d-A" % n) < flat.index("R%02d-B" % n)
              < flat.index("R%02d-C" % n) for n in range(72))
          and all(flat.index("R%02d-C" % n) < flat.index("R%02d-A" % (n + 1))
                  for n in range(71)))
    check("spanning cell degrades to its own full-width row",
          any(len(r) == 1 and r[0].get("full")
              and "SPAN-WIDE" in r[0]["text"] for t in tables for r in t)
          and flat.index("SPAN-WIDE") < flat.index("SPAN-AFTER"))
    check("nested table degrades to stacked text inside its cell",
          "NEST-OUTER NEST-INNER-A NEST-INNER-B NEST-END" in plain(flat)
          and flat.index("NEST-END") < flat.index("NEST-PEER"))
    check("an image contributes no markup but loses no prose",
          "After the plate." in flat and "<img" not in flat)
    imgs = [t for k, t in ch1 if k == "img"]
    check("the picture becomes its own block (%r)" % (imgs,), len(imgs) == 1)

    # ---- through the LOADER, where hrefs become zip entries ----------
    # The checks above run _epub_extract directly, so they see the raw href.
    # What decides whether a picture appears is the resolution step in
    # _epub_load: an <img src> is relative to the DOCUMENT, not the .opf, so
    # "images/plate.png" in OEBPS/ch1.xhtml must become "OEBPS/images/plate.png"
    # and must exist in the archive. Getting that wrong loses the picture
    # silently, which is exactly what this is here to prevent.
    chapters, err = ebook._epub_load(path)
    loaded = check("the loader parses the real EPUB (%s)" % (err or "ok"),
                   chapters is not None)
    if loaded:
        entries = [t for ch in chapters for k, t in ch if k == "img"]
        check("the image href resolves to a real archive entry (%r)" % entries,
              entries == ["OEBPS/images/plate.png"])
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
        check("and that entry really is in the zip",
              all(e in names for e in entries))
    else:
        not_reached("the EPUB did not load",
                    "the image href resolves to a real archive entry",
                    "and that entry really is in the zip")

    # ---- the app opens the real file ---------------------------------
    check("the real .epub on disk is well-formed",
          zipfile.is_zipfile(path))
    # Exercise the shipped pagination and its Pango renderer without requiring
    # a display server. This is rendered geometry, not a source-code proxy.
    if not hasattr(ebook.EbookReader, "_epub_table_geometry"):
        # Against a tree without table rendering the geometry helper does
        # not exist. That state must read as the DEFECT, not as a crashed
        # suite: letting the AttributeError fly here launders "every cell
        # stacks" into a traceback — the exact blind-spot class M1 names.
        # Three named FAILs keep the check count identical red and green.
        check("rendered cells in one row share a baseline row band "
              "(table rendering absent: no _epub_table_geometry)", False)
        check("rendered columns align across rows "
              "(table rendering absent)", False)
        check("EPUB table page break falls between complete rows "
              "(table rendering absent)", False)
    else:
        render_chapters = [[(k, v) for k, v in ch] for ch in chapters]
        pages = ebook.EbookReader._paginate(render_chapters)
        page_rows = []
        page_columns = []
        row_bands_ok = True
        for ci, start, end in pages:
            descriptors = [payload
                           for kind, payload in render_chapters[ci][start:end]
                           if kind == "tablerow"]
            rows = [d["cells"] for d in descriptors]
            tokens = []
            columns = []
            geometry = ebook.EbookReader._epub_table_geometry(
                rows, measure=560,
                sizing_rows=descriptors[0]["table"] if descriptors else None)
            for ri in sorted({r["row"] for r in geometry}):
                rects = [r for r in geometry if r["row"] == ri]
                texts = [plain(r["text"]) for r in rects]
                tokens.extend(t for t in texts if t.startswith("R"))
                if len(rects) == 3:
                    row_bands_ok &= len({(r["y"], r["height"])
                                         for r in rects}) == 1
                    columns.append(tuple(round(r["x"], 3) for r in rects))
            page_rows.append(tokens)
            page_columns.extend(columns)
        check("rendered cells in one row share a baseline row band",
              row_bands_ok and bool(page_columns))
        check("rendered columns align across rows",
              bool(page_columns) and len(set(page_columns)) == 1
              and len(set(page_columns[0])) == 3)
        row_pages = {}
        for pi, tokens in enumerate(page_rows):
            for token in tokens:
                rn = int(token[1:3])
                row_pages.setdefault(rn, set()).add(pi)
        check("EPUB table page break falls between complete rows",
              len(row_pages) == 72
              and all(len(pages) == 1 for pages in row_pages.values())
              and len({next(iter(p)) for p in row_pages.values()}) >= 2
              and all(max(row_pages[n]) <= min(row_pages[n + 1])
                      for n in range(71)))
    gtk_ok = ebook.Gtk.init_check()[0]
    if gtk_ok:
        app = ebook.EbookReader()
        check("the reader constructs against a real EPUB", app is not None)
        try:
            app.destroy()
        except Exception:
            pass
    else:
        print("SKIP reader construction (GTK display unavailable)")

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
