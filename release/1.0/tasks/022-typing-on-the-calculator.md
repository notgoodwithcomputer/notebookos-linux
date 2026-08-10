# 022 — Typing on the calculator

**Lane:** G (calculator) · **Streams:** S1 truth defects, S3 i18n
**Status:** CLOSED

Third module chosen by `tools/func_coverage.py`: 8 of calculator.py's 35
functions never entered. Two of them mattered.

**`_on_key_calc` — the entire physical-keyboard path.** `calculator_selftest`
drives `evaluate()` directly, so nothing had ever *typed* on the calculator.
On a laptop the keyboard is how a calculator is used.

**`_operand_start` — reached only by the relative percent rule.** That it was
never entered means `_PCT_REL` had never matched in any test, so the whole
reason `_expand_percent` exists — "200+10% is 220, not 200.1, because every
consumer calculator says 220" — was unverified. Its docstring makes three
specific numeric claims and nothing checked any of them.

## The defect

**`comma` and `KP_Separator` were not in the key table.** A comma is the
decimal point on most of the keyboard layouts this OS ships a group for —
French, German, Spanish, Italian, Russian, Polish, Portuguese, Turkish — and on
a laptop with no numeric keypad the main-row comma is the *only* decimal key
there is. Pressing it did nothing at all: `_on_key_calc` returned False and the
keystroke fell through, so typing "3,5" gave **35**.

Both names now map to `"."`. Not a localised separator — the display, the
keypad button and the parser are all ASCII `.` throughout, and changing that
would mean rewriting the formatter and the parser together. The point is that
the key does something and that what it does is visible: press comma, see a
decimal point.

## What I checked and did not change

The percent rules all hold, including the parenthesised cases `_operand_start`
exists for: `2*(3+10%)` is 6.6 (ten percent of the 3 inside the group, not of
anything outside it), `(200+10%)*2` is 440, `1000+(200+10%)` is 1220. All three
docstring claims are exact.

`200++10%` expands to `200++(200+)*(10/100)` and raises a SyntaxError, which
`evaluate` already reports as "not a calculation". Malformed input reaching a
clear refusal is the correct end of that path, so it is left alone.

## Gate

`tools/calculator_keyboard_selftest.py`, 26 checks. Real `Gdk` key events
through the real handler: an expression typed digit by digit, both decimal
keys, BackSpace, Delete, Up/Down history, Ctrl+C to the real clipboard.

It also asserts the keys the calculator must **not** claim: Esc has to reach
nbapp, or the calculator becomes the one app you cannot leave with it, and a
letter must fall through too.

**A trap avoided by one line.** The first version read the result by walking
every label in the window, which searches the menu bar, the clock and the mode
chip — "7" can be found in a timestamp. It reads `disp_lbl` and nothing else,
so the assertions are exact equality (`== "7"`, not `"7" in ...`). This is the
same shape as the bills suite reading the whole `Gtk.Overlay` when it wanted
one card.

**A false alarm, isolated rather than "fixed".** One Up recalled `8×8`, and the
check expected the calculation before it. One Up giving the last thing typed is
correct — that is what every command line does. The expectation was wrong, not
the code; the suite now asserts one Up reaches the newest, two Ups the one
before, and Down walks back.

**Red-proof, five mutations:**

| mutation | result |
|---|---|
| the comma keys back out of the table (the shipped code) | 3 fail |
| "%" only ever meaning /100 | 4 fail |
| Esc claimed, so the app cannot be left with it | 1 fail |
| Ctrl+C no longer copying | 1 fail |
| Up/Down falling through to wander the keypad focus | 4 fail |

## Finished the module rather than leaving three behind

The first pass took calculator from 27 of 35 functions entered to 32. The three
left were each a few lines to cover, so they are covered: the on-screen keypad
button (`_on_press` is a one-line wrapper, exactly the sort of thing assumed to
work and then quietly unwired), Enter on the history box recalling — and Tab
there still falling through so focus can move — and Clear from the menu.

**35 of 35.** 30 checks.
