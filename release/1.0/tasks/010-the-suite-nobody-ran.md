# 010 — ID3 tags verified, and the suite nobody was running

**Lane:** A (music) + C (harness) · **Streams:** S1, S2 evidence
**Status:** CLOSED 2026-08-06

## A. ROADMAP #28 is not a defect

*"ID3 tags are never read although the tag reader is already running. The whole
Albums/Artists sidebar is filename guesses."* `_info_tags` reads title, artist
and album off the `DiscovererInfo` that already runs over every file for its
duration. Driven end to end against a file named `01 track.mp3` whose tags say
something else entirely:

    after the scan        '01 track' / 'Unknown Artist' / 'Unknown Album' / ''
    after the Discoverer  'Blue Monday' / 'New Order' / 'Power Corruption…' / '0:02'

The fixture is named to make those two answers DIFFER. A suite whose test file
was called `New Order - Blue Monday.mp3` would pass whether or not a tag was
ever read.

**Gate: `tools/music_tags_selftest.py`.** The audio is encoded by GStreamer,
which the app requires anyway, and the ID3v2.3 tag is written **by hand** —
`taginject` with both `id3v2mux` and `id3mux` produced files whose tags read
back empty, and `ffmpeg` is not in the image, so depending on it would make the
gate skippable on the guest. 25 lines of struct packing, no dependencies.

**Red-proof:** `_info_tags` made to return empty → the library stays on
`01 track / Unknown Artist`. The duration check is asserted **outside** that
dependency, because it rides the same Discoverer pass through a different
accessor and survives the tags being discarded — gating it produced a
`[not reached]` naming the wrong cause.

Also fixed: `_track_from_path`'s docstring still claimed *"no tag reader is
available"*. One arrived and the sentence did not keep up, which sends the next
reader off to build something that already exists.

## B. Three red suites, and why nobody knew

Running the neighbouring music suites — which I should have done in task 004 —
turned up three failures.

**1. Mine.** Task 004 added `_track_label` and `_flash` to `_on_error`.
`music_lifecycle_selftest` drives that method against a hand-written `FakeMusic`
stub ("only the attributes the lifecycle methods touch"), which now touched two
more. **I broke it two sessions ago and did not notice, because I ran the suite
I had just written and not the ones beside it.** The stub gained both methods,
recording rather than no-op, which bought two real assertions: a late error
after destroy must say nothing, and a live error must still report itself.
18/18.

**2. A permanently-red false positive.** `music_transport_accessibility_selftest`
asserts `"Gtk.EventBox" not in factory`. The transport WAS rewritten from an
EventBox to a real `Gtk.Button` — and the comment recording why still names the
old approach, so the sentence explaining the fix tripped the check for the bug.
Comments are stripped before matching now. Red-proofed by putting real
`Gtk.EventBox` code back: still caught.

**3. A test poisoned by its own history.** `music_playlist_selftest` pins a
throwaway NB_HOME with `os.environ.setdefault` — but `guestrun.sh`, the
documented way to run it, **exports NB_HOME itself**, so the setdefault never
fired. Every run shared one home, and the playlists the suite creates had
accumulated: **sixteen** of them, so "playlist list starts empty" failed on
state its own earlier runs left behind. Now assigned unconditionally. Isolation
a caller can accidentally switch off is not isolation.

**36 of 179 selftests use that same `setdefault`.**

## C. The aggregate runner (S2)

The campaign asks for *"one command, one report, non-zero on any failure"* and
there wasn't one. That absence is why all three of the above could persist: a
suite can be red for months, and a regression can survive sessions, when nothing
runs them together.

**`tools/run_all_gates.py`.** Every suite gets its own NB_HOME, assigned over
whatever the environment holds, so a `setdefault` suite still lands somewhere
private — a guard, not a licence, and the 36 still want fixing.

Two things it does that a plain runner would not:

* **It does not trust a suite's exit code alone.** A suite that exits 0 while
  printing FAIL is itself broken; that disagreement is reported as `LIES`
  rather than counted as a pass.
* **The baseline file is labelled a ledger of debt, not a permission slip.**
  Every line in it is a gate that is protecting nothing. `--update-baseline`
  also lists entries that now pass so they get pruned rather than accumulating —
  the failure mode `i18n_coverage_baseline` showed in task 003, where updating
  a baseline silently absorbed 23 real problems.

A sample of twelve already turned up two more reds beyond the music three
(`accent_selftest`, `boot_surface_selftest`). The full run is in progress; its
result is the first honest count of how much of this project's evidence system
is actually green.
