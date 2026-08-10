# 012 — The gate that took half an hour to say nothing was wrong

**Lane:** C (harness) · **Streams:** S2 evidence
**Status:** CLOSED 2026-08-06

## It was never broken

`language_course_selftest` exceeded the runner's 300s timeout printing
**nothing at all**, which is indistinguishable from a hang. Run to completion it
takes **29m44s and passes everything**: 8800 exercises generated and
shape-checked across five courses, then 200 skills played through the real
widgets to a crown apiece — 3400 exercises answered, every course finishing on
`wrong == 0`.

That is a genuinely valuable suite. language.py does not ship lessons, it
GENERATES them from `course_<code>.json`, so a course file that looks fine can
still produce a question nobody can answer — a multiple choice whose correct
option is missing from the options, a word bank short a token of its own
answer. Nothing but sitting the lesson finds that.

**The runner gained per-suite timeout overrides.** A timeout reported as a
failure is a lie about the code: a slow suite gets the time it needs, or it does
not belong in the run at all.

## But 96% of that half hour was spent asleep

    real 29m44.202s
    user  1m10.840s

Seventy seconds of CPU. Everything else was waiting for real UI timers to
expire — chiefly the 750ms pause after a correct answer, multiplied by 3400
exercises.

Every lesson delay goes through one helper, `_lesson_later(ms, fn)`, so the
suite now wraps it to clamp `ms` to 1. **The duration is compressed and nothing
else**: it is still a real `GLib.timeout_add`, still registered in
`_lesson_sources`, still cancelled by the generation guard, so ordering,
cancellation and the "a timer must not fire across a lesson boundary" contract
are exercised exactly as they ship. What is no longer exercised is the
wall-clock length of the pause — which this suite never checked, and which
language's own lifecycle tests own.

The justification is not tidiness. **A thirty-minute gate is a gate people
skip**, and a skipped gate protects nothing — which is precisely how the ten
failures in task 011 accumulated in the first place.

## The result

    before   real 29m44.202s   user 1m10.840s
    after    real  2m15.667s   user 1m01.324s

**Thirteen times faster, and the output is identical line for line** — 200
skills, 3400 exercises, the same kind breakdown (bank=334, choose=1267,
intro=1200, listen=399, match=200), 200 crowns, 3612 XP, 1201 terms seen. That
identity is the evidence the compression changed nothing about what is tested;
CPU time barely moved, which is what says the missing 27 minutes were idle.

The runner's override for it went 2400 → **600**. A generous timeout does not
make a suite pass, it only delays the moment a genuine hang is noticed, so the
ceiling is kept close to the real cost.

## A defect this turned up in the runner itself

`--only language_course` reported *"1 baseline entry now PASS — prune them:
boot_surface_selftest"*. That suite had not run: `results.get(n, ("PASS",))`
defaulted a suite excluded by the filter to passing, so any filtered run advised
pruning every other baseline entry.

**A checker that cannot tell "did not fail" from "was never asked" is the same
fault this runner exists to catch elsewhere** — and it is the third time today:
the ROADMAP audit read a missing file as a missing feature, `reopen_shapes`
counted a case that never ran as data LOST, and now this. Fixed to consider only
suites that actually ran.

## Confirming run
The 179-suite re-run of task 011's fixes reached 151/179 with a single failure:
`boot_surface_selftest`, deliberately baselined as a gate for a feature that
was never built. Every repair held.
