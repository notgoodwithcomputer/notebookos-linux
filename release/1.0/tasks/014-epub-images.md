# 014 — Pictures in EPUBs

**Lane:** A (ebook) · **Streams:** S1 truth defects
**Status:** CLOSED — images done, tables still pinned as a known limit

Task 007 gave EPUBs their italics back and left `<img>` and tables explicitly
unfinished, with the suite pinning what still did not work. This closes the
image half.

## Three decisions

**The href is resolved against the DOCUMENT, not the .opf.** This is the whole
trick and the classic way to get it wrong: the spine and manifest are relative
to the package document, but an `<img src>` is relative to the XHTML file it
sits in. `images/plate.png` inside `OEBPS/ch1.xhtml` is
`OEBPS/images/plate.png`. Resolve it against the .opf directory and every
picture in the book silently disappears — nothing errors, the prose is intact,
there is simply a hole where a plate should be. The red-proof below plants
exactly that mistake.

**Bytes are read at RENDER time, not at load.** Storing the decoded pictures
with the chapters would mean a picture book sat in memory in full from the
moment it was opened, costing far more than the text it illustrates. The block
carries the archive entry name; `_epub_image` opens the zip when the page is
built.

**A picture that will not decode yields None, not a placeholder.** A book may
carry an SVG, or a format this GdkPixbuf was not built with. A missing plate is
honest; a broken-image glyph or a traceback is not.

Also: `<img>` is caught in BOTH `handle_startendtag` and `handle_starttag`.
The XHTML spec wants `<img/>`, and most EPUBs in the wild write `<img>` — only
the second reaches `handle_starttag`, so handling one form would have worked on
the fixture and failed on real books.

Scaled to the reading measure and **never enlarged**: a 200px thumbnail blown
up to the column width looks worse than a 200px thumbnail, and a plate wider
than the text block would force a horizontal scrollbar across the prose.

## Gate

`tools/ebook_formatting_selftest.py`, now 18 checks. The fixture gained a real
2x2 PNG written byte by byte — CRC and all — so it needs no encoder, and the
`<img>` reference deliberately uses the non-self-closing form.

The important addition runs through `_epub_load` rather than `_epub_extract`:
the earlier checks see the raw href, but what decides whether a picture appears
is the resolution step. It asserts the entry resolves to `OEBPS/images/plate.png`
**and that the name really is in the zip**.

**Red-proof, two mutations:**

| mutation | result |
|---|---|
| images discarded (the shipped behaviour) | 2 fail |
| href resolved against the .opf instead of the document | 1 fail — resolves to nothing, exactly the silent-hole failure |

## Still open, still pinned
Tables. A `<tr>/<td>` stack still arrives as separate paragraphs, and the suite
continues to assert the cells are readable **and in order** so the limit stays a
tested property rather than an absence. Laying out a real grid is a layout
change, not a parser change.
