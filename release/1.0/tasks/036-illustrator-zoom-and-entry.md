# Task 036 — Illustrator Zoom and Off-Canvas Entry

Completed 2026-08-07.

## User reports

1. Off-canvas stroke entry
   - Mechanism: pointer handlers existed only on the document DrawingArea, so a
     button press in the surrounding field never armed a gesture.
   - Fix: the full field is now an EventBox with the same press, motion, and
     release handlers. Events are translated into canvas coordinates and field
     presses are clamped to the nearest document edge. Freehand tools begin at
     that edge pixel; line, rectangle, and ellipse tools retain it as their
     anchor. Ordinary canvas motion remains unclamped so brushes can leave and
     re-enter naturally.

2. Zoom out
   - Mechanism: `ZOOM_MIN` was 1, `_set_zoom` converted every value to `int`,
     and fit used integer floor division. A document larger than the viewport
     could therefore never fit.
   - Fix: the ladder now includes 1/8, 1/6, 1/4, 1/3, and 1/2. Fit selects the
     largest ladder step that fits. Enlargements retain integer-exact nearest
     filtering; reductions use bilinear filtering for legibility. The status
     readout reports fractional percentages without rounding them to 100%.

3. Black field after zoom
   - Mechanism: the area behind the centred document depended only on themed
     viewport painting, leaving newly exposed native-window pixels vulnerable
     to the display server's black clear during resize.
   - Fix: the full field allocation is explicitly painted papertone with
     `OPERATOR_SOURCE` on every draw, in addition to the CSS background.

## Zoom audit findings

- Canvas requests now ceil fractional scaled dimensions instead of passing
  floats or truncating the last row and column.
- Pointer-to-pixel mapping is shared, float-safe, floor-based, and exact at all
  four document corners for every reduction step.
- Damage rectangles floor their origin and ceil their far edge, preventing
  fractional-scale cursor or preview remnants.
- Pixel grid remains disabled below its existing 8x threshold.
- Brush footprint and shape preview use float scale coordinates and remain
  aligned with document pixels.
- Rapid zoom continues to coalesce the recentre idle; viewport-centre anchoring
  uses the old and new floating scales without integer loss.
- Canvas resize and document reset now request ceiled scaled dimensions; resize
  then re-runs fit, including sub-1x choices.
- Cursor readouts continue to report image pixels from the shared mapping.

## Verification

- `python3 -m py_compile .../illustrator.py tools/illustrator_view_selftest.py`:
  pass.
- `tools/illustrator_view_selftest.py`: 27 checks, 0 failures.
- `tools/illustrator_lifecycle_selftest.py`: 14 checks, 0 failed.
- `tools/illustrator_save_as_selftest.py`: 11 checks, all pass.
- `tools/illustrator_selftest.py`: display sections could not run because GTK
  could not initialize in the displayless sandbox; the failure occurred before
  its first application check.
- `tools/menu_conformance_check.py`: 810 checks, PASS.
- `tools/ascii_css_check.py`: clean.
- `tools/css_parse_check.py`: clean.
- `tools/self_attr_audit.py`: 120 classes checked, 0 findings.
- `tools/voice_check.py`: repository allowlisted findings only, RESULT CLEAN.
- `tools/jargon_sweep.py`: repository allowlisted findings only, RESULT CLEAN.

## Red proofs

- Mutation `ZOOM_MIN = 1`: `FAIL zoom minimum is the first fractional ladder
  step`, 24 checks, 1 failure. Restored.
- Mutation disabling entry clamping: `FAIL margin press stores a clamped edge
  anchor (-16, 2)`, 24 checks, 1 failure. Restored.

## Strings

No user-visible strings added. Translation fragment count: 0; no
`release/1.0/i18n-fragments/036-illustrator/` files required.

## Follow-up

The three display self-checks now follow the user-requested zoom-out contract:
the reduction ladder is exactly 1/8, 1/6, 1/4, 1/3, and 1/2; 1x is the
boundary rather than the floor; and zooms at or above 1x remain integer-exact
with nearest-neighbour blitting. Reductions use the implementation's
non-nearest bilinear filter for legibility.

- `zoom never goes below 1x` -> an oversized document driven through
  `_zoom_fit` selects a level below 1x and fits wholly within the viewport.
- `every zoom level is an integer` -> the sub-1x levels equal the reciprocal
  ladder and are monotonic through the 1x boundary, while every level at or
  above 1x remains an integer.
- `zoom blit is nearest-neighbour, never smoothing` -> the 1x boundary selects
  nearest-neighbour at and above 1x and a non-nearest smoothing filter below.

`de/illustrator.py` here is the implementation at
`buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/illustrator.py`.
Its SHA-256 before sabotage was
`c0916da519c5a1310416921af2b314eca0f5e5514f2efe300f925861e7c52355`;
after restoration it was
`c0916da519c5a1310416921af2b314eca0f5e5514f2efe300f925861e7c52355`.
They match byte-for-byte.

Headlessly, `py_compile` passed. The full suite reached its first section but
GTK could not initialize before the first application check, both normally and
with the targeted `ZOOM_MIN = 1` sabotage, so none of the three display checks
ran. A headless probe of the reachable ladder, filter branch, fit helper, and
zoom-min clamp failed with the sabotage (`clamped_fit: 1`) and passed after
restoration (`clamped_fit: 0.5`). Execution of the actual GTK `_zoom_fit` check
and rendered filter assertions therefore depends on the orchestrator's next
real-display run; no 198-check pass is claimed here.
