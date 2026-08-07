# 06. Writing

Four applications produce written documents. They differ in what they are for,
not in how much they can do: Writer handles general documents, Novel handles
book-length work in chapters, Screenplay produces scripts in standard format,
and Journal keeps dated entries.

---

## Writer

Writer is the word processor. The document is shown as a page on a desk: a
Letter or A4 sheet with real margins, a ruler above it, and page-break guides
where each page ends.

### Formatting

| Category | Controls |
|---|---|
| Text | Font family, font size, bold, italic, underline, strikethrough |
| Colour | Text colour, highlight colour |
| Paragraph | Left, centre, right and justified alignment; line spacing; indent and outdent |
| Lists | Bulleted and numbered |
| Styles | Title, Heading 1, Heading 2, Heading 3, Body, Quote |

The style gallery applies a complete set of formatting to a paragraph.
Individual formatting applied afterwards takes precedence over the style.

### Tables

Tables are inserted inline. Rows and columns can be added or removed after
insertion.

### Page setup

Page size, orientation and margins are set for the document. A header and
footer can be enabled, with optional automatic page numbers.

### Find and replace

Find and Replace searches the document and highlights each match as it is
found.

### Undo

Writer keeps an undo history for the editing session. Undo and Redo are at the
top of the Edit menu.

### Files

| Format | Extension | Notes |
|---|---|---|
| Writer document | `.writer` | Keeps all formatting |
| Plain text | `.txt` | Text only; formatting is discarded |
| Markdown | `.md` | Text only; formatting is discarded |

**Export to PDF** writes a PDF into `Documents` with the document's formatting
and page layout intact. **Print…** sends the same rendering to a printer.

---

## Novel

Novel is for manuscripts long enough to be divided into chapters.

The window is in two parts. The sidebar holds the manuscript title, the list of
chapters, and a **New Chapter** control. The main area is the editing canvas,
set in a serif face, with a format bar above it and a word count and save
indicator below.

### Formatting

Paragraph style, bold, italic, underline, block quote, and lists. The
formatting available is deliberately narrower than Writer's: a manuscript is
submitted as text, and the formatting that matters is structural.

### Chapters and parts

Each chapter is a separate section of the manuscript with its own title, and is
selected from the sidebar. Chapters can be grouped into parts.

| Command | Effect |
|---|---|
| New Chapter | Adds a chapter |
| New Part… | Adds a part |
| Delete Chapter… | Removes the selected chapter, after confirmation |
| Delete Part… | Removes the selected part, after confirmation |
| Author… | Sets the author name used on the title page |

Delete Chapter… and Delete Part… are greyed out when only one chapter or one
part remains, so a manuscript always has at least one of each.

The word count shown is for the whole manuscript.

### Files

Manuscripts are saved as `.json` files in `Documents` through New, Open, Save
and Save As. The working state is also written continuously to a session
recovery file, so an interrupted session is restored on the next launch.

A new manuscript opens with a single empty chapter titled "Chapter 1".

### Zine Print

**Zine Print…** prints a manuscript as a folded booklet. Pages are laid out at
half-letter size, two to a sheet, in saddle-stitch folding order. Printing
double-sided, folding the stack down the middle and stapling it produces a
booklet with the pages in the correct order.

**Export to PDF…** and **Zine Print…** use the same page renderer, so the
exported file matches what is printed.

---

## Screenplay

Screenplay produces scripts in standard screenplay format. The page is set in
Courier, at the margins the format requires.

### Elements

The format bar sets the element type of the current line. Each element has its
own indentation and capitalisation.

| Element | Use |
|---|---|
| Scene | Scene heading |
| Action | Description of what happens |
| Character | The name above a line of dialogue |
| Dialogue | Spoken lines |
| Paren. | A parenthetical direction within dialogue |
| Transition | A transition between scenes |

A live page count and word count are shown as the script is written.

### Files

Screenplay reads and writes `.fountain`, `.txt`, and its own `.json` format in
`Documents`. A script can also be opened by double-clicking it in the Finder.

The element formatting of each line is preserved in the `.json` format and in
the session recovery snapshot. The `.fountain` and `.txt` formats carry the
formatting through standard screenplay conventions.

Screenplay also offers the booklet printing described under Novel.

---

## Journal

Journal keeps dated entries. It is a single-store application: there are no
files to open or save, and every change is written as it is made.

The window is in two parts. The sidebar lists entries grouped by month. The
main area shows the selected entry: its date in full, a line stating when it was
written, and the entry body.

- **New Entry** creates an entry dated today and places it at the top of the
  list.
- The word count and save state are shown beneath the entry.
- Undo and Redo are available from the Edit menu.
- **Export to PDF** renders the entries into a PDF in `Documents`.

The desktop board's Journal card reports whether today's entry has been
written.
