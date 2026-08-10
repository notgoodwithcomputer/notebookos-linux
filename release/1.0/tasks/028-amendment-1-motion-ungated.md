# 028 — Amendment 1 lands: the render path is not a motion input

**Lane:** C (shared layer + harness) · **Streams:** S3b · **Status:** CLOSED 2026-08-07

The NB_ACCEL animation gate is reversed per PAPER-PHYSICS §0.5 Amendment 1, in
all three places it lived: `nbmotion.policy()` (no accelerated() branch; the
Reduced-Motion fade survives on any path), `nbmotion._apply_gtk_animations()`
and `nbapp._apply_motion_policy()` (gtk-enable-animations = not reduced — the
docstring now argues the amendment where it used to argue the gate).
`accelerated()` remains for genuine render-path decisions (compositor gating,
frame budgets, pacing-harness labeling) and its docstring says so.

Suites updated to the amended contract, not deleted: the motion policy matrix
is accel-invariant row by row; a NEW assertion "NB_ACCEL is not a motion
input" compares policy under both values; nbapp conformance now walks the AST
for a "NB_ACCEL" constant inside _apply_motion_policy (the docstring may talk
about it, the code may not read it); transitions' software column must plan
exactly the accelerated column; the synchronous-replace contract moved to its
one honest remaining route (no frame clock) and kept every assertion.

Green: motion (960+ checks), transitions (102), accessibility_ux,
video_transition, session_prefs. Red-proof recorded at
redproofs/amendment1-motion-2026-08-07.txt: reintroducing the gate fails 3+6
checks including "NB_ACCEL is not a motion input: accel (200, 200) != soft
(0, 0)"; revert restores green.

What this makes true on real hardware: every AMD/NVIDIA laptop (the majority)
gets the motion language today, at real risk of uneven frames until Article F
damage-limiting lands and the GPU restore (task 023) moves them off swrast —
that ordering is the amendment's explicit bet, and the frame-pacing gate will
measure it instead of us guessing.
