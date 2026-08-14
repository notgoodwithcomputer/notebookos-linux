# Fonts

What the image carries, what Writer offers, and the rule for adding more.

Proved by `tools/font_selftest.py` — 77 checks, resolving every offered family
through fontconfig **by file**, because a name match is exactly what a silent
substitution also produces.

## What Writer offers

Twenty-three families, grouped by what the face is for. A `GtkComboBoxText` has
no headings, so the order *is* the grouping.

| group | families |
|-------|----------|
| Serif | Liberation Serif · DejaVu Serif · PT Serif · Crimson Text · Spectral · Cardo · IBM Plex Serif · Arvo |
| Sans | Liberation Sans · DejaVu Sans · Nimbus Sans · Lato · Fira Sans |
| Monospaced | Liberation Mono · DejaVu Sans Mono · IBM Plex Mono · Fira Mono · Space Mono |
| Display | Bebas Neue · Abril Fatface |
| Handwriting | Patrick Hand · Indie Flower · Komika Hand |

The list draws **each family's name in its own face**. With two dozen entries
the names alone stop being an answer — "Arvo" and "Spectral" tell a person
nothing — and the face is the thing being chosen. The preview size is pinned to
the toolbar's own 13 **pixels**: written as `"Family 13"` Pango reads points,
which at 96 dpi is ~17 px and grew the toolbar from 28 px to 42 px.

## What was added, and from where

Fifteen families, 45 static faces, 10.7 MB. All SIL Open Font License 1.1,
taken verbatim from `github.com/google/fonts` at the commit recorded in
`assets/fonts/PROVENANCE.txt`, vendored under `assets/fonts/<family>/` and
installed to `usr/share/fonts/notebookos/<family>/` with each family's own
`OFL.txt` beside it.

- **Serif** — PT Serif, Crimson Text, Spectral, Cardo, IBM Plex Serif, Arvo (slab)
- **Sans** — Lato, Fira Sans
- **Monospaced** — IBM Plex Mono, Fira Mono, Space Mono
- **Display** — Bebas Neue, Abril Fatface
- **Handwriting** — Patrick Hand, Indie Flower

Already present and unchanged: Nimbus Sans (the interface face — see
`nimbus-sans` notes; do not disturb it), Liberation, DejaVu, Komika Hand, Noto
Sans CJK SC, Noto Sans Devanagari, and the Ghostscript URW Type 1 set.

### Static faces, not variable

Upstream now ships most families as **variable** fonts only, and the stack here
would cope (fontconfig 2.14.2, Pango 1.50.14, FreeType 2.13.2 all handle named
instances). Static TTFs were chosen anyway: every font already in this image is
a static, the failure modes are known, and a rendering difference that only
appears on the guest is expensive to find. The cost is size, and 10.7 MB is
affordable.

That choice is what set the selection. Families that are variable-only upstream
— Inter, Open Sans, Work Sans, Source Sans/Serif, JetBrains Mono, EB Garamond,
Lora, Libre Baskerville, Merriweather, Playfair Display — are **not** here. Any
of them can be added later either as a variable font or by instantiating
statics with `fonttools varLib.instancer` (not installed on the build host).

## Adding a family

1. Vendor it under `assets/fonts/<slug>/` with its licence. The licence is not
   optional: it is the condition on which the font may be redistributed.
2. Copy the faces **and the licence** to
   `rootfs-overlay/usr/share/fonts/notebookos/<slug>/`.
3. Add the family name to `writer.FONT_FAMILIES`, in its group.
4. Run `tools/font_selftest.py`.

Step 4 is the one that matters. Nothing errors when a picker lists a font the
image lacks — fontconfig substitutes the nearest thing it has, the text does
not visibly change, and the document is saved carrying a family name that will
render as something else on the next machine. The gate resolves each name to a
**file** and checks that file's own family name matches.

Its red-proof is worth reading: adding "Helvetica Neue LT" to the picker made
three separate checks fail at once — it resolved to `FiraSans-Regular.ttf`,
reported no bold or italic styles, and rendered pixel-identical to Fira Sans.
That is what the silent failure looks like from the inside.

## Two defects this found

- **The e-book reader measured tables in one face and drew them in another.**
  The width pass asked Pango for a bare `"Newsreader"` and the layout pass for
  `"Newsreader,Liberation Serif,Georgia,serif"`. Neither Newsreader nor Georgia
  is on this image, so both landed on Liberation Serif by luck rather than by
  saying so — and had the bare request fallen to a sans, column widths would
  have been measured in a different face than the text filling them. Both now
  share `ebook.TABLE_FAMILY`, which names fonts that ship. Rendering is
  unchanged; the coincidence is now a guarantee.
- **The Disc Burner drew its DVD menu with cairo's toy font API.** The toy API
  (`select_font_face` + `show_text`) takes one family and does no fallback and
  no shaping, so a disc named in Japanese, Hindi, Russian, Yiddish or Chinese
  came out as empty boxes — and the disc name is a field a person types on an
  OS that speaks seventeen languages. Now drawn through PangoCairo, with the
  baseline recovered from `layout.get_baseline()` so the subpicture buttons
  still line up with the text. `tools/burner_selftest.py` checks five scripts
  put real ink on the menu.

`tools/toyfont_check.py` is what caught the second one, and it had caught the
identical bug in `widgetsettings.py` before. Run it.

## Where fonts are NOT chosen

Only Writer has a font picker. Illustrator has **no text tool at all**, so it
has no Fonts tab to fill; Novel, Screenplay, Journal, Academics and the E-book
Reader each use a fixed face by design. Adding a picker to any of them is a
separate piece of work — the fonts are now on the disc for it.
