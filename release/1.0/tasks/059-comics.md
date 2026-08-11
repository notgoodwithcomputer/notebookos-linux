# 059 — Comics: the comic zine studio (NEW APP)

User directive 2026-08-10, one-shot: "a workflow for producing comix zines
… a merger of Novel and Illustrator … the same Zine Print as Novel, with
the non-antialiased pixel drawing interface from Illustrator. (Think
Homestuck.) Each page 5.5x8.5 … manage panels and word bubbles … draw
cover pages … option for color covers and B/W inside for budget
production. Komika font in /assets for lettering. Use Codex to code it."

**The design is `docs/COMICS-SPEC.md` — normative, every decision made.**

Deliverables
- `de/comics.py` (new app), `tools/comics_selftest.py` (new suite)
- Registration: finder APP_MODULES/APP_KIND/FILE_APPS/FILE_OPENERS,
  `root/Applications/Comics.app` (755), nbicons `comics` glyph
- Fonts: `usr/share/fonts/notebookos/KomikaHand-*.ttf` + LICENSE.Komika
  (DONE — shipped with the claim)
- i18n fragment `release/1.0/i18n-fragments/059-comics/` ×17 (sr = Gaj's
  Latin), new-strings-only; catalogs untouched (campaign merges)

Closure
- Full gate battery green (construct, minsize@722, data_safety,
  self_attr, voice, jargon, menu_conformance, ascii_css/css_parse,
  button_contrast, icon_uniqueness) with the documented exception:
  i18n_check red ONLY on 059's unmerged fragment keys, listed in HANDOFF.
- comics_selftest green WITH display + guest fonts; red-proof recorded.
- Visual pass of the real window (guest theme + fonts) reviewed.

Lane: `comics` (CLAIMS.md 2026-08-10 13:40). Implementation via Codex per
the user's standing instruction; verification and fragments here.
