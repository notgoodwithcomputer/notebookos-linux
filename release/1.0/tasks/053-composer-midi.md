# 053 — Composer MIDI piano roll

**Lane:** batch-0810 · **Role:** standalone MuseScore-role MIDI editor for Notebook OS

## Built

Added `de/composer.py`, a GTK3 document app whose song model is independent of
the piano-roll view so notation and tracker views can be added later. The fixed
resolution is 480 PPQ. A song carries tempo, time signature and ordered tracks;
tracks carry name, instrument, GM program, percussion/channel-10 and mute;
notes carry start, duration, pitch and velocity.

The scrollable piano roll covers all 128 pitches and scrolls in both axes. It
supports click/drag note creation, selection, Shift multi-selection,
rubber-band selection, dragging, right-edge resize, keyboard nudging, velocity
changes, Delete, and Esc-to-leave/deselect. Esc never deletes. Track controls
add, immediately remove, rename, mute and choose an instrument. Tempo and snap
choices (beat through 1/8 beat) are live. All destructive and structural edits
checkpoint and commit through `nbapp.UndoHistory`; there are no destructive
confirmation dialogs. The window requests 980×680, within the 1000×700 target.

The curated instrument set is: Piano (GM 0), Electric Piano (4), Music Box
(10), Organ (16), Guitar (24), Bass (32), Strings (48), Choir (52), Brass
(61), Saxophone (65), Flute (73), Synth Lead (80), Saw Wave (81), Synth Pad
(88), FX (98), and Noise / Drums (percussion on MIDI channel 10). Instrument
labels and every other visible string pass through `_t()`.

## MIDI and persistence decisions

Export is self-written Standard MIDI File format 1: a conductor track contains
tempo and time-signature meta events, and each song track contains track name,
program change, note-on/off and end-of-track events with correct variable-length
delta times. A sequencer-specific meta event preserves the instrument label,
mute and percussion fields, which ordinary MIDI readers safely ignore; this
makes Composer export/import an exact model identity. Import also accepts
format 0, rescales foreign PPQ, splits channels into tracks, and implements
running status and velocity-zero note-offs. File Open, Save As and Export use
the shared `nbpicker` under Documents.

Session recovery uses the app-standard `composer` store at
`$NB_HOME/.config/notebook/composer.json` and `nbapp.atomic_write_json`. An
unreadable or unrecognized store is moved to a `.damaged-<stamp>` sibling at
load and that launch remains read-only, so opening and closing cannot replace
the user's bytes. The suite describes and verifies this as **RECOVERY of
damaged files** across three fresh-process open+close cycles.

## Preview audio

V1 renders the complete audible song first to a temporary 24 kHz, 16-bit WAV,
then plays it through the house GStreamer/automatic audio-output path (with an
`aplay` fallback when GStreamer is unavailable). Voices are sine, triangle,
square, saw or deterministic noise according to the selected instrument
family. Muted tracks do not render. Stop tears down playback and removes the
temporary file; a red piano-roll playhead and beat status follow elapsed
playback.

This render-then-play choice has honest start latency: playback begins only
after the whole preview has rendered. Short songs generally start quickly, but
long or dense songs can take noticeably longer; previews are capped at three
minutes to bound time and memory.

## Verification

- `python3 -m py_compile .../de/composer.py tools/composer_selftest.py` — PASS.
- `PYTHONPATH=.../de python3 tools/composer_selftest.py` — PASS, 21 checks:
  exact model→MIDI→model identity, format-1 structure, VLQ zero/large edges,
  simultaneous events, foreign format-0/running-status import, edit operations,
  complete undo for each destructive operation, Esc-never-deletes, three-cycle
  damaged-store recovery, and named scratch-copy PASS-MUTANT checks.
- `python3 tools/menu_conformance_check.py` — PASS, 851 checks.
- `python3 tools/i18n_merge.py release/1.0/i18n-fragments/053-composer` — PASS
  dry run: all 17 languages, 46 new keys each. Every fragment also passed
  `python3 -m json.tool`; Serbian is Gaj's Latin script.
- `python3 tools/css_parse_check.py` — PASS (`clean`; Composer adds no CSS).
- GTK construct/render and visual 1024×722 checks — **SKIPPED (display-blocked)**.
  This sandbox intentionally has no X server; no display probe was attempted.
  The dispatcher must rerun these checks with its real display.

## Dispatcher registrations

Add these exact one-line entries during integration (this builder did not edit
either registry):

```python
"Composer": "composer",
```

to `finder.py`'s `APP_MODULES`, and this suggested piano-roll glyph to
`nbicons.py`'s `ICONS`:

```python
"composer": [("M", 4, 7), ("L", 20, 7), ("M", 4, 12), ("L", 20, 12), ("M", 4, 17), ("L", 20, 17), ("R", 7, 5, 6, 4), ("R", 12, 10, 7, 4), ("R", 5, 15, 5, 4)],
```

## Dispatcher verification + integration (batch-0810, 2026-08-10)
Three defects found on the display rerun that the sandbox could not see, all
fixed in place:
1. construct-time RecursionError — _track_changed ↔ _refresh_tracks feedback
   loop (set_active re-emits "changed"); fixed with an idempotence guard.
2. ru width 1194px vs the 1024 budget — measured with the 053 ru fragment
   INJECTED into a scratch catalog (the unmerged-fragment fallback makes plain
   NB_LANG runs vacuous). Track add/remove/rename buttons were duplicate
   chrome (the Track menu is the contract) — removed; combo button cells
   ellipsized (12/14 chars); status ellipsized. ru now 694px.
3. suite path-order bug — COMPOSER_MODULE_DIR was inserted BEHIND the real
   de/, so scratch-copy sabotages graded the pristine module; order fixed and
   re-proven (VLQ off-by-one sabotage now fails by name; clean run 21/21).
Integration: finder APP_MODULES + nbicons glyph registered (ride the
integration sweep, uncommitted); "Composer" app-name key added to all 17
catalogs directly per the amended additive-key rule (campaign may revise term
choices); store_damage COVERAGE row recorded (ratchet ALL PASS with
PYTHONPATH=de). Guest-theme render verified at 1024×722. VERIFIED per M2.
