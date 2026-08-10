# 007 — A screenplay with no name on it, and a novel with no italics

**Lane:** A (screenplay, ebook) · **Streams:** S1 truth defects
**Status:** CLOSED 2026-08-06 (#37 fixed, #34 partial and pinned)

Both items were confirmed genuinely open by task 006's audit, and both are the
same shape: something the app captures, shows on screen, and then does not put
on the page.

## A. The byline never printed  (ROADMAP #37)

Screenplay's title page has a second field under the title, placeholder
*"written by"*. It is captured, persisted to the .json, and restored by undo —
and `_build_pages` drew only the title. A writer typed "written by Alexander
Hamilton", saw it on screen, and printed an anonymous screenplay.

Now drawn under the title at 11pt to the title's 13, keeping the printed
hierarchy the same as the on-screen 15px-to-17px. Not uppercased: the title is
shouted by convention, a byline is not. In full ink rather than the muted grey
the entry shows — a title page is printed, and a grey byline reads as a
photocopy artefact.

**Gate: `tools/screenplay_titlepage_selftest.py`** — renders a real PDF through
the app's own `_build_pages` and reads the text back with `pdftotext`. Asserting
on the drawing calls would prove only that a function ran; the failure mode is
"it is not on the page", so the page is what gets read.

Guarded against the trap that caught Novel's zine print — a clip-based renderer
produces a PDF whose text cannot be extracted at all, and `pdftotext` then
returns nothing whether the page is right or wrong. So the suite first asserts
the TITLE, which was always drawn, comes back. If that fails, extraction is
broken rather than the byline.

**Red-proof:** the byline draw removed → **3 of 8 fail**, with the two dependent
assertions reporting `[not reached: no byline was drawn]`, and the extraction
control still passing so the cause is unambiguous.

### The second half of #37 is not a defect
The entry also says *"Save As overwrites the title"*. It does — and
`_file_save_as` documents it as deliberate: *"the title page takes the chosen
filename so it always reflects the open file"*, with the old path and title
restored on a failed write. Reversing a documented decision on my own judgement
is not a bug fix. It is worth a product decision, because it loses an authored
title as a side effect of naming a file, and it is recorded in the ROADMAP as
that rather than silently changed.

## B. EPUBs rendered without emphasis  (ROADMAP #34 — PARTIAL)

`_EpubBlocks` reduced every chapter to plain `(kind, text)`, discarding `<em>`
and `<strong>` with every other tag. In a novel that is not a detail: emphasis,
book titles, foreign words, ships' names and a character's inner voice are all
italics. *"He said he was fine"* and *"He said he was* fine*"* are different
sentences, and the reader was given the first one.

`<em>/<i>/<cite>` and `<strong>/<b>` now travel as Pango markup. Cheap, because
the reading column already builds a `Gtk.Label` per block — the change is
`set_markup` instead of `label=`.

Three things the change forced, none of them optional:
* **Escaping.** The emitted string now has to parse as markup, and
  `convert_charrefs` has already turned `&amp;` back into `&`. Without escaping,
  one ampersand makes a whole paragraph unrenderable — the red-proof caught
  exactly this.
* **Unbalanced tags.** A stray `</em>` is common in hand-made EPUBs. Closing
  only what is actually open keeps the paragraph parseable; otherwise the text
  is lost, which is far worse than losing the italics.
* **A fallback.** `set_reading_text` validates with `Pango.parse_markup` and
  falls back to plain text. A book is a file from outside the system: losing the
  italics is a blemish, losing the sentence is a bug.

Emphasis spanning a paragraph break is closed at the boundary and reopened in
the next block, which is legal HTML and should keep going.

**Still not done, and pinned rather than left vague:** images and real tables.
The suite asserts a table's cells arrive as readable text **in order** even
though the grid is gone, so the known limit is a tested property rather than an
absence. Those need a layout pass, not a parser change.

**Gate: `tools/ebook_formatting_selftest.py`** — builds a real EPUB (mimetype,
container, OPF, two XHTML documents) and runs the shipped parser over it. 14
checks, including that every block parses as Pango markup.

**Red-proof:** inline tags and escaping removed → **8 of 14 fail**, the first
being `Tom & Jerry cost < 5 > nothing.` failing to parse.

## Note on nbi18n
Markup on a reading label is safe: `nbi18n`'s tree walk sees `use_markup` and
routes through `_t_markup`, which splits on tags and only replaces text runs
with a catalog hit. Book prose never hits, so it is left alone. Worth checking
before the change rather than after — the walk rewrites every Label in the tree.
