# 011 — What the aggregate runner found the first time it ran

**Lane:** A + C · **Streams:** S2 evidence
**Status:** CLOSED — 9 of 10 fixed, 1 baselined with its reason

The first full run of `tools/run_all_gates.py`: **179 suites, 10 failing.**
Nothing had run them together before, which is exactly why ten could
accumulate.

## Two were mine, both the same mistake

**`gbaemu_selftest` and `gbaemu_lifecycle_selftest`** broke in task 004, when I
removed gbaemu's dead settings file. I ran `construct_one gbaemu` and the new
`dead_setting_check`, and not the app's own suites sitting next to them. That is
the **second time in three sessions** I have shipped a regression this way — the
first was `music_lifecycle_selftest` in task 010.

Both are now rewritten to assert the *new* contract rather than deleted: gbaemu
keeps no settings file, offers no toggle to write one, and a file left behind by
an older build is ignored and **not rewritten on the way out**. The lifecycle
suite keeps its `_save_settings` stub so a regression that reintroduces a save
on the teardown path shows up as an unexpected event.

## One real defect: the first-run form did not fit in CJK

`password_lockout_selftest` measures the first-run form in all 18 languages
against the 1024x740 budget. **ja, zh and ko each came out at 758px — 18 over.**
Latin sits at 707, so CJK line boxes carry 51px of extra height through six
stacked fields. This is the one screen a machine cannot be used without.

Fixed by trimming the form's outer vertical margin 24 → 12. That is the least
invasive 24px available: it touches no type, no field spacing and no rhythm
between rows. ja now measures **734**, with room to spare rather than exactly on
the line where one longer translation would put it back over. 50/50.

## One that read as data loss and was not

`reopen_shapes_selftest` reported **"23 survived, 5 LOST"**, all in the language
app including the undamaged `control` case. Data loss is the worst class in this
project, so it looked like the headline.

It was a stale method name: the suite calls `_bump_streak_xp`, renamed to
`_award_xp`, so every language case died before doing anything. **The harness
counted a case that never ran as LOST** — and that wording is what made me read
it as data loss. Both fixed; a case that cannot run now reports FAILED. 28/28.

## Three were comments tripping checks for the bugs they document

The same shape, three times in one session:

* `music_transport_accessibility_selftest` — `"Gtk.EventBox" not in factory`,
  failing on the comment saying a real `Gtk.Button` is used *instead of* one.
* `mealplanner_accessibility_selftest` — identical, on the same word.
* **`gbaemu_selftest` — which I wrote an hour after fixing the first one**, and
  made the same mistake in: `"Fullscreen" not in source`, tripped by my own
  comment recording the toggle's removal.

All three strip comments before matching now, each red-proofed by putting real
code back. A static guard that greps a whole file reports the documentation, not
the code — and the failure is self-perpetuating, because the better the comment
explaining a fix, the more certainly it fails the check for the bug.

## One was a false positive in the runner itself

`jobs_selftest` was flagged `LIES` — exits 0 while printing a traceback. The
traceback is **deliberate**: the suite raises inside a callback to prove the
exception does not escape, and GLib prints it as designed. My `FAILWORD` pattern
matched `Traceback` and `ERROR`, which in a codebase whose style is negative
testing is far too crude. Narrowed to a suite's own failure vocabulary; a suite
that genuinely crashes exits non-zero and is caught anyway.

Worth noting where that came from: I spent the session fixing over-sensitive
static guards and then shipped one.

## Also fixed
`accent_selftest` — the Track menu item was renamed `Clear All Takes…` →
`Remove Every Clip…` (function first, per the UI text mandate) and both the menu
check and the catalog-coverage list still named the retired label. The old key
is still in all 17 catalogs as dead weight, so checking it proved nothing about
what ships. 29/29.

## The last three

**`av_apps_selftest` — stale against a rewritten sequencer.** Two dead
references, not one. `ToneEngine` became `AudioOut`, which streams nbsynth's
render to the sink instead of synthesising a tone; that port was mechanical.
`test_master_fader` was not: it drove `_fire_beat` against a spy engine and
`sequencer.Player` against a faked `aplay` subprocess, and **both are gone** —
the sequencer renders in process now and auditions a note by handing a mono
buffer to `Audition`.

Rewritten rather than patched. The master-fader property it was named for is
already measured in `sequencer_mix_selftest` on the RMS of a rendered WAV,
which is better evidence than a spy ever gave, so the function now checks what
nothing else covers: `Audition`'s own arithmetic — equal-power centre split,
gain scaling, hard-left isolation, an empty buffer queuing nothing, and the
voice list staying bounded so a held key cannot grow it for ever. 60/60.

**`language_course_selftest` — slow, not hung.** It exceeded 300s and printed
nothing, which reads exactly like a hang. It is neither: it generates and
shape-checks **8800 exercises across five courses** before answering a single
question, then plays every skill of every course through the real widgets. That
is the only way to catch a lesson that cannot be finished — a multiple choice
whose correct option is missing, a word bank short a token — and it is worth the
minutes. The runner gained per-suite timeout overrides: **a timeout reported as
a failure is a lie about the code**, so a slow suite gets the time it needs or
it does not belong in the run.

**`boot_surface_selftest` — a gate for a feature that was never built.** It
calls `Backdrop.watch_settings()`, a live backdrop colour following
settings.json. That method has never existed, at HEAD or anywhere; only the
unused `desktopbg.set_color()` half of it was ever written. It is a drafted
piece of the Appearance system (campaign S6, the most cuttable stream), and
building it is a feature, not a fix.

Baselined with that reasoning recorded in `tools/gates_baseline.txt`, because
**a gate nobody has seen PASS protects as little as one nobody has seen fail**.
The entry says plainly: build the feature or delete the suite, but do not leave
it sitting red.
