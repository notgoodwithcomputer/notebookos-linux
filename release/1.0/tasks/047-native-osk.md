# 047 - Native touchscreen keyboard

Completed the Notebook OS on-screen keyboard in `de/osk.py`, with its headless
proof suite in `tools/osk_selftest.py`.

## Design decisions

- The keyboard is a full-width bottom `DOCK`, sized to about 36 percent of the
  active monitor with a four-row 48 px logical-height floor scaled by
  `NB_SCALE`. It also requests keep-above as the fallback for a window manager
  that does not honor dock reservation. Matchbox display/Yoga verification is
  still required to confirm its actual work-area reservation.
- The window cannot accept focus or focus on map. Every key and accent target
  is also non-focusable, leaving typing in the already-focused application.
- Physical X keycodes define positions only. `Gdk.Keymap` supplies legends for
  the active XKB group and shift level; keymap/group changes rebuild the keys.
  The symbol page uses both levels of the physical number row where available,
  then direct keysyms for punctuation without a universal physical position.
- Shift is phone-like: one tap arms one-shot shift, the next character consumes
  it; tapping Shift a second time latches caps; tapping it once more clears it.
  `?123` changes to the symbol page and `ABC` returns.
- Injection uses a private X connection and `XTestFakeKeyEvent` by keycode.
  Shifted keys are wrapped by Shift press/release. If an accent has no keycode,
  an empty keycode is borrowed with `XChangeKeyboardMapping`, injected using
  the Unicode keysym (`0x01000000 | codepoint`), and its original mapping is
  restored unconditionally in `finally`, including after injection failure.
- Long press uses the imported `nbdiacritics.TABLE` and `HOLD_MS` (450 ms).
  Its large popover targets accept tap or slide selection; release without a
  target cancels without input.
- Chrome uses the existing paper, ink, hairline, warm-press, and muted tokens.
  There is no sound or animation; only the pressed letterpress state moves.
  The narrow Hide key uses a font-independent function mark.
- Launch shows immediately. SIGTERM hides before leaving the GTK loop. A stale-
  safe PID claim permits one keyboard process at a time.

## Headless proof

`tools/osk_selftest.py` proves:

- fake-keymap group and level resolution, including Cyrillic `q` positions;
- one-shot/latching shift and both page transitions;
- exact Shift/key press-release/flush ordering;
- exact remap/failing-injection/flush/restore ordering;
- the imported diacritic table, threshold, remap selection, and no-op cancel;
- AST presence of `set_accept_focus(False)` and the `DOCK` type hint.

GTK window construction was honestly skipped because this sandbox has no
display. Window placement, Matchbox work-area reservation, touch slide feel,
and the Yoga tablet-mode launch path await orchestrator verification with X and
hardware.

## Red-proofs

The self-test runs these deliberately broken models before the green suite:

```text
RED-PROOF 1 PASS: missing remap finally was caught
RED-PROOF 2 PASS: broken caps latch was caught
```

The first omits restoration after forced injection failure; its restore-order
assertion goes red. The second consumes a caps latch; its shifted-intent
assertion goes red. The real implementations are then exercised by the passing
tests.

## Final suite tails

```text
Ran 6 tests in 0.013s
OK (skipped=1)
osk.py                 1 css block(s)
clean
clean: no non-ASCII inside any bytes literal
131 classes checked (0 for calls only), 0 skipped, 0 finding(s)
CLEAN: no undefined self attributes, every class checked
0 flagged string(s) across 1 file(s)
RESULT: CLEAN
0 flagged strings
RESULT: CLEAN
```

`python3 -m py_compile` completed with no output. The skipped test is only the
live GTK construction; its static window-contract assertions passed first.

Localization fragments: none. All visible marks are keymap-derived legends or
conventional keyboard/function glyphs; the voice and jargon gates found no UI
copy.
