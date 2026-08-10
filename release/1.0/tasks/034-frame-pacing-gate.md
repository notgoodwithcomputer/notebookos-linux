# 034 — Frame-pacing gate (PAPER-PHYSICS §F5)

**Lane:** C (harness) · **Streams:** S3b · **Status:** CLOSED 2026-08-08
(Dispatched to Codex; never delivered — built by the campaign session.)

`tools/frame_pacing_check.py`, two halves:

**Dynamic.** Drives a Scalar per non-instant token under NB_MOTION_TRACE and
reads nbmotion.trace_drain(): per run, frame count / longest inter-frame gap /
total duration vs DURATION_BANDS. The VACUOUS-PASS GUARD is the sharp part: a
run policy said should animate but that recorded ZERO frames FAILS — a
transition that never fired cannot be conformant (red-proof: driving one frame
only fails feedback/select/surface-in as "recorded ZERO frames"). Honest-mode:
headless the inter-frame GAPS are the test's stepping, not a compositor's, so
the longest-frame budget is ADVISORY and only total-in-band + non-vacuous are
enforced; the header says which mode ran. Phase-3 hardware calibrates
LONGEST_FRAME_ACCEL/SOFT.

**Static.** A bare `self.queue_draw()` inside an AppWindow subclass, in a
module that animates, is a whole-window software repaint (§F1). Class-aware:
`self` in a DrawingArea/Box subclass is a small widget invalidating itself
(correct F1 scope — g2048's board layer, widgets' _Check), so only AppWindow
classes are checked. One audited one-shot remains (finder.py:2191, a redraw
after folder navigation) — ratcheted debt, both directions: a NEW AppWindow
self.queue_draw() (count→2) fails, deleting the entry while the call stays
fails stale.

15+ checks green. Red-proofs: redproofs/frame-pacing-2026-08-08.txt. Enrolled
in CHECK_GATES. Runtime ~a few seconds. Next: wire per-transition pacing
results into motion_inventory.json and flip pacing_required in Phase 3 on
hardware.
