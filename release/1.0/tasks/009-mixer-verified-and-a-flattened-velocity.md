# 009 — Two false alarms, and the real bug found underneath them

**Lane:** A (nbsynth) · **Streams:** S1 truth defects, S2 evidence
**Status:** CLOSED 2026-08-06

## The two entries were both wrong

**#21 — "Eight Pan sliders that move, relabel, and have zero audible effect
(the engine is mono)."** nbsynth is stereo (`CHANNELS = 2`) and `Mixdown`
implements an equal-power pan. Rendered and measured:

    hard left    L rms 3939   R rms    0
    centre       L rms 2886   R rms 2886      (0.733 of hard; equal-power = 0.707)
    hard right   L rms    0   R rms 3939

**#32 — "VU meters are `sin(tick)`."** `Mixdown.track_peak` is taken off the
rendered block, pre-fader.

Third and fourth stale entries in three sessions. So rather than correct them
one at a time, the verification became a standing gate.

## The gate: `tools/sequencer_mix_selftest.py`

Renders through the real `nbsynth.render_wav` and measures channel RMS in the
produced 16-bit WAV. No mocks: every assertion is about what came out of the
encoder. 17 checks over pan, the pan LAW, gain, master, mute, solo, and the
meters.

**Red-proofed with three separate mutations**, because one would only prove one
control is covered:

| mutation | result |
|---|---|
| pan law made linear instead of equal-power | **only** the equal-power check fails — hard left/right still pass, which is correct: a linear law also puts all energy on one side, it just dips in the middle |
| gains forced equal (a genuinely mono engine) | 4 failures |
| the mute gate neutralised | **only** the mute check fails |

## The real bug, found by a check that would not go red

The meter assertions were first written as *loud track vs muted track*. Planting
a genuine `sin(tick)` meter **passed** them — a muted track never reaches the
metering line at all (`_audible` skips it first), so its peak is zero whatever
the meter does. Fourth vacuous pass in five sessions, and the only reason it
surfaced is that the red-proof was run.

Rewritten to compare two AUDIBLE tracks at velocity 127 and 8 — and both
metered **0.8375**. That number is not arbitrary: `render_drum`'s velocity law
is `0.35 + 0.65·(vb/8)`, and 0.8375 is exactly its value for the **default
velocity of 100**. Velocity was being dropped.

**`normalize_song` was not idempotent.** It reads a stored note as
`[beat, row, length, velocity]` — velocity at index 3 — and emits
`(beat, row, velocity)`, three items with velocity at index 2. A second pass
therefore found nothing at index 3 and substituted 100:

    once : [(0.0, 0, 127), (1.0, 0, 8)]
    twice: [(0.0, 0, 100), (1.0, 0, 100)]

And there is **always** a second pass: `render_wav` normalises, then hands the
result to `Mixdown`, whose `__init__` normalises again.

So every drum accent — every loud kick, every ghost note — was flattened to one
velocity, in playback and in every exported file. The Sequencer's velocity
control wrote a value into the project that never reached the sound. Filed as
ROADMAP #42.

**Fix:** accept both shapes. The app only ever writes 4-element notes
(`_clip_json`), so a 3-element note can only be an already-normalised one and
reading index 2 as velocity is unambiguous. Verified idempotent to three passes.

**Red-proof:** the one-line read restored → 3 of 17 fail.

## A threshold that could never have passed
The first version asserted `loud > quiet * 3`. The velocity law spans
`0.35 → 1.0`, so the widest possible spread is **2.32×** and 3× was
unreachable — the check would have failed for ever against correct code. The
0.35 floor is deliberate (a ghost note is quiet, not absent), so the bound
belongs in the assertion, and the comment now records it.

## Standing suite
construct 38/38 · sequencer_mix 17/17 · sequencer 109/109 · audio_output 66/66 ·
drum accessibility, drum-label keyboard, smoothness, transition all pass ·
minsize ALL FIT.
