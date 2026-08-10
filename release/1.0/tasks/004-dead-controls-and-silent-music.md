# 004 — Controls that persist nothing, and a player that failed in silence

**Lane:** A (apps) + C (harness) · **Streams:** S1 truth defects, S4 ALIVE, S8
**Status:** CLOSED 2026-08-06

## A. The sweep the campaign asked for  (S8: "controls that persist a value
## nothing reads — the class `gbaemu`'s `scale` belongs to")

`tools/dead_setting_check.py` reports two shapes:

* **ROUND TRIP** — every read of the key feeds the very widget whose own
  handler writes it. The value's only consumer is the control that produces it.
* **WRITE ONLY** — the key is stored and never read outside the loader.

This is the quietest way for a control to lie. It is not broken, it is
*consistent*: you set it, you come back, and your choice is still there. The
absence of any effect is the only evidence.

A read that feeds a DIFFERENT widget is not a finding — `self._sidebar
.set_visible(cfg["show_sidebar"])` is what applying a setting looks like. The
distinction is between a value that travels somewhere and a value that goes in
a circle.

**Two rounds of false positives, both fixed before trusting the output.** The
first run reported eight; five were the tool's fault:

* `nbprint` `media` and `sides` — a LOCAL dict of CUPS options handed to
  `submit_pdf`, whose keys are written and never read back by design. `opts` is
  no longer treated as a settings container.
* `settings` `blank_timeout`, `kbd_delay`, `kbd_rate` — read through the typed
  accessors `_cfg_int` / `_cfg_float`, which the checker could not see. It now
  resolves accessor helpers: a method that reads the container using one of its
  own parameters as the key is an accessor, and calls to it with a constant
  first argument count as reads. Three false findings out of eight is more than
  enough to make a gate stop being believed.

### The three real ones, all fixed
| Where | What |
|---|---|
| `gbaemu` `fullscreen` | ROUND TRIP. A game ALWAYS runs fullscreen — nbgame must reparent vbam into a fullscreen app window or the single-app WM unmaps it. The toggle could never do anything, so it is **removed**, not explained. (ROADMAP #19) |
| `gbaemu` `scale` | WRITE ONLY. Loaded, range-checked to 1..6, read by nothing, and no control ever set it. Removed. |
| `installer` `password2` | WRITE ONLY. The confirm field is compared against the password in `_validate`, straight off the two entries. The copy in `cfg` was read by nothing — it only kept a **second plaintext copy of the password** alive in a dict for the length of the install. Removed. |

Both gbaemu keys were the entire contents of its config, so `gbaemu.json`, its
loader, its saver and the toggle are all gone.

**Red-proof (2026-08-06)** — two dead settings replanted on a scratch copy AND
a live one as a negative control, because the whole value of this gate is
telling them apart:

    gbaemu   ROUND TRIP  fullscreen   every read feeds _fs_btn, the control that writes it
    shell    WRITE ONLY  grid_snap    written in _on_grid_toggled; never read
    35 settings key(s) across 5 module(s), 2 dead        EXIT=1

`maps.show_labels` — written by one toggle, read into a *different* widget —
was correctly **not** reported. Clean tree: `32 keys across 4 modules, 0 dead`.

## B. Music failed in total silence  (ROADMAP #18)

`_on_error` called `_stop_playback()` and returned. The play glyph flipped back
to Play and nothing anywhere said why. From the listener's side that is
indistinguishable from a broken button, and the natural response is to press it
again. Music was one of the twelve apps the campaign lists as having **no status
channel at all**.

**Fix.** The now-playing line becomes the channel — Finder's `_flash_status`
pattern on the one label a listener is already reading. Three previously silent
paths now speak, and a track that really starts cancels any message still up so
a stale failure cannot caption the wrong song.

The cause is taken from the GStreamer error DOMAIN, never its text:

    gst-resource-error-quark  ->  "“%s” is no longer where the library found it"
    gst-stream-error-quark    ->  "“%s” can’t be played — the file may be damaged"

The GError's own message is developer English, never translated, and says things
like *"Your GStreamer installation is missing a plug-in."* or *"This appears to
be a text file."* — the machinery talking about itself. The domain separates the
only two causes a listener can act on differently, which is all that needs to
reach them.

**Gate: `tools/music_failure_selftest.py`** — the REAL pipeline against REAL bad
input, not `_on_error` called with a fabricated message. A file of plain bytes
named `.mp3` really does produce `gst-stream-error-quark`; a real silent WAV is
synthesised so the success case is genuinely a success. It asserts the two
messages **differ**, because one "something went wrong" for both would satisfy
every other check while telling the listener nothing actionable — and that a
track which plays shows **no** message, since a status channel that fires on
success is noise, and noise is how a status channel gets ignored.

It waits on a real `GLib.MainLoop` rather than pumping pending events: the error
arrives as a bus message, and an earlier draft that pumped synchronously raced
the pipeline and passed for the wrong reason.

**Red-proof (2026-08-06)** — both messages deleted on a scratch copy:

    FAIL an undecodable track reports something ('Broken Take')
    FAIL the message names the track  [not reached: nothing was reported]
    FAIL the message is not the GStreamer error text  [not reached: ...]
    FAIL a missing file reports something ('Vanished Take')
    FAIL the missing-file message names the track  [not reached: ...]
    FAIL the two failures do not say the same thing  [not reached: ...]
    10 checks, 4 passed, 6 FAILED

The first version of this red-proof scored only **2** failures. "The message
names the track" passed while the feature was removed, because with no message
the label falls back to the bare track title — so the title is trivially "in"
it. **Second occurrence of the vacuous pass in two sessions** (task 003 had the
same shape in Put Back). An assertion that is true whether or not the code under
test ran is worse than no assertion, because it reads as coverage.

Three new strings, in all seventeen catalogs (3095 → 3098).

## C. Recorded, not guessed at — ROADMAP #41
Found while reading nbgame for #19: **the emulator picture is a fixed 4×**.
vbam runs `-f 17` (kStretch4x) so the game is always 960×640, and `_embed`
centres it at that natural size in a fullscreen stage. On the 1366×768 laptops
this OS targets that is ~70% of the width; on 1920×1080 it is half; on the HiDPI
panels now supported it is a postage stamp in a field of black. vbam's largest
enlarging filters are xbrz5x/6x, so 6× (1440×960) is the ceiling without making
the embed scale.

**Deliberately not fixed.** No ROM has ever been executed by this project's
harnesses, so a change to the video path cannot be run here — and an
unverifiable change to the one subsystem with a standing unexplained bug is a
guess wearing a fix's clothes. Filed with the measurements so whoever has
hardware can settle it in minutes.
