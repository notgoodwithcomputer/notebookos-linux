#!/usr/bin/env python3
"""
Italics survive the EPUB.

`_EpubBlocks` reduced every chapter to plain `(kind, text)`, so `<em>` and
`<strong>` were discarded along with every other tag (ROADMAP #34). In a novel
that is not a detail: emphasis, book titles, foreign words, ships' names and a
character's inner voice are all italics, and the reader was given none of them —
"he said he was *fine*" and "he said he was fine" are different sentences.

Italic and bold now travel as Pango markup. Images and real tables still do not,
and this suite pins that down as a KNOWN LIMIT rather than leaving it vague:
a table's cells must still arrive as readable text, in order, even though the
grid is gone.

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


CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title>
<style>p{color:red}</style></head><body>
<h1>The <em>Hesperus</em> Sails</h1>
<p>He said he was <em>fine</em>, and she did <strong>not</strong> believe him.</p>
<p>Tom &amp; Jerry cost &lt; 5 &gt; nothing.</p>
<p><i>Continued emphasis</i> and <b>bold</b> and <cite>a cited title</cite>.</p>
<p>A stray close tag </em> must not lose this sentence.</p>
<table><tr><td>Port</td><td>Depth</td></tr><tr><td>Hull</td><td>9m</td></tr></table>
<p><img src="images/plate.png" alt="a plate">After the plate.</p>
<script>var x = 1 &lt; 2;</script>
</body></html>"""

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


def main():
    path = os.path.join(_HOME, "hesperus.epub")
    build_epub(path)

    blocks = []
    for doc in (CH1, CH2):
        blocks.append(ebook._epub_extract(doc.encode("utf-8")))
    ch1 = blocks[0]
    flat = " ".join(t for _k, t in ch1)
    print("   chapter 1 parsed into %d blocks" % len(ch1))

    # ---- every block must be valid Pango markup ----------------------
    bad = []
    for kind, text in ch1 + blocks[1]:
        try:
            Pango.parse_markup(text, -1, "\0")
        except Exception as exc:
            bad.append((text[:50], str(exc)[:40]))
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

    # ---- known limits, pinned ----------------------------------------
    check("table cells still arrive as readable text, in order",
          all(w in flat for w in ("Port", "Depth", "Hull", "9m"))
          and flat.index("Port") < flat.index("Hull"))
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
    app = ebook.EbookReader()
    check("the reader constructs against a real EPUB", app is not None)
    check("the real .epub on disk is well-formed",
          zipfile.is_zipfile(path))
    try:
        app.destroy()
    except Exception:
        pass

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
