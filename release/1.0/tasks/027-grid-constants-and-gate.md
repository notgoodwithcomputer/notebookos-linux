# 027 — The grid becomes law, and the law joins the run

**Lane:** C (harness + shared layer) · **Streams:** S2 evidence / S3 grid
**Status:** CLOSED 2026-08-07 — landed by the campaign session directly

## What landed

1. **§E3 constants are code** (PAPER-PHYSICS migration row 3b).
   `tools/design_tokens.py` gained the grid section — GRID_UNIT=4, the two
   ladders, LINE=20, MARGIN=24, RAIL=240, GUTTER=24, HAIRLINE=1,
   MEASURE_READ=640, MEASURE_FORM=1040, PANEL_H=46, THIRD_PANE_MIN_W=1366,
   RAIL_EXCEPTIONS (illustrator dock 252, CJK history), `canvas_h()`.
   `de/nbapp.py` re-exports the runtime subset (tools/ does not ship).

2. **`tools/grid_check.py`** — §E4 v1: lockstep (design_tokens == nbapp ==
   shell/widgets strut == minsize_sweep budget), rails (every sidebar constant
   == RAIL, or excepted, or ratcheted debt), ladder (named steps in the
   control band [22,40]; interior-on-grid mod-4 rule above it). 229 checks
   green. §E4 checks 3–5 (hairlines, rest edges, travel vectors) land with
   the Article G motion inventory; sub-22px values are out of v1 scope
   (spacers/tracks — needs runtime node-type pairing to judge).

3. **`run_all_gates.py` now runs the check gates.** Discovery was
   `*_selftest.py` only — css_parse_check, jargon_sweep, minsize_sweep and
   the rest ran OUTSIDE the one command the release gate requires. 18 named
   CHECK_GATES join the run; a named gate whose file vanishes crashes the run
   rather than shrinking it. TIMEOUTS: minsize_sweep 900, data_stress 600.

## What the first sweep found

- **min-height:32px ships five times and is legal** (8u) — the spec's open
  ladder had omitted it; §E3.2 amended, enumeration corrected rather than the
  tree. The convergence method from the token pass, working as designed.
- The 65 raw findings triaged to ZERO height debt: everything ≥42 conforms to
  the general interior rule (sequencer 76 = 19u, music 84 = 21u, calculator
  66 = interior 16u), everything <22 is a field element.
- Rail debt carried (ratcheted, exact-match both directions): workout 210,
  packages 212, bills 252 → burn-down is migration row 3d, app lanes.

## Red-proof (M1) — release/1.0/redproofs/grid_check-2026-08-07.txt

All four spec'd mutations produced distinct failure text and exit 1, and the
reverted tree runs 229-green: sidebar→238 (fired BOTH ratchet directions),
min-height→31, budget→740 ("minsize_sweep default budget ('740','740') !=
canvas_h(768) = 722"), debt entry deleted while source deviates.

## Verification

`python3 tools/run_all_gates.py --only grid_check` → `[1/1] grid_check PASS`,
runner exit 0. Full extended run lands with tonight's integration pass.
