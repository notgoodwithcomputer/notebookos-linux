# 062 — Animation: the 2D animation studio (NEW APP)

User directive 2026-08-11: "Let's design a 2D animation app for Notebook
OS. It should be capable of producing a complete work on the same level
as Dr. Katz: Professional Therapist (very pixely, but broadcastable) as
well as the Jack Stauber/PilotRedSun/cboyardee genre of pixel animation."
Then: "Let's call it Animation and go for it."

**The design is `docs/ANIMATION-SPEC.md` — normative, every decision
made.** Exposure-sheet timeline (no tweening, law 2), boil takes,
loudness lip-sync, sample-exact 48 kHz audio (SPF table), ffmpeg export
through video.py's encoder probe.

Deliverables
- `de/animation.py` (new app), `tools/animation_selftest.py` (new suite,
  nine families + named PASS-MUTANTs, MODULE_DIR-override order proven)
- Registration: finder APP_MODULES / APP_KIND("Cartooning") /
  FILE_APPS[".anim"] / FILE_OPENERS + HIDDEN_APPS entry (ships hidden),
  `root/Applications/Animation.app` (755), nbicons "animation" → Lucide
  `film` via tools/gen_nbicons.py regeneration (coordinate glyphs banned)
- i18n fragment `release/1.0/i18n-fragments/062-animation/` ×17 (sr =
  Gaj's Latin), new-strings-only; catalogs untouched (campaign merges)

Closure
- Full gate battery green (construct, minsize@722 **with fragments
  injected** — the batch-0810 vacuous-measurement trap, data_safety,
  self_attr, voice, jargon, menu_conformance, ascii_css/css_parse,
  button_contrast, icon drift + uniqueness) with the documented
  exception: i18n_check red ONLY on 062's unmerged fragment keys.
- animation_selftest green WITH display + guest fonts; red-proof
  recorded per family.
- Spec §14's two benchmarks produced in the real app (the lane's
  finishing bar; may land over rounds).
- Visual pass of the real window (guest theme + fonts) reviewed.

Lane: `animation` (CLAIMS.md 2026-08-11 00:28). Implementation via Codex
per the user's standing instruction; verification and fragments here.
