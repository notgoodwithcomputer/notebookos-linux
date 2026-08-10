# 017 — Typing Chinese

**Lane:** C (i18n) · **Streams:** S1 truth defects
**Status:** CLOSED

Not a ROADMAP item. Found by asking a different question: **which modules has
no suite ever imported?** Eight, and one of them was `de/nbpinyin.py` — the only
way to enter Chinese on this machine (no ibus, no fcitx), installed by `nbapp`
into every window, on the keystroke path of all thirty apps.

The seventeen i18n gates check catalogs. A catalog is what the interface says to
you; none of them is about what you can say back.

## Two defects, one of them severe

**Chinese could not be typed into any document.** `Gtk.TextView.get_window(
win_type)` **shadows** the inherited zero-argument `Gtk.Widget.get_window`, so
`_place()` raised `TypeError` on every keystroke in a text area. PyGObject
swallows an exception out of a signal handler and treats the handler as
unhandled — so GTK passed the raw pinyin straight through to the widget, *after*
the IME had already decided to claim it. Typing 你好 produced **`nihao你好`**.

Eleven apps put a `Gtk.TextView` on screen: writer, novel, journal, screenplay,
cookbook, academics, contacts, bills, installer, gbasdk, gbaemu. That is every
long-form writing surface in the OS. A one-line `Gtk.Entry` worked perfectly,
which is exactly why it could sit there unnoticed.

**Any letter outside a–z killed the composition silently.** The test for "is
this part of a pinyin syllable" was `ch.isalpha()`, which is true of *every*
Unicode letter. é, ü, Cyrillic д and 好 itself were appended to the buffer; it
then matched nothing, the popup emptied, and the keystroke was swallowed — no
character typed, no candidate, no explanation. On any layout that can produce an
accented letter, the composition just died. Pinyin is written in ASCII, so those
characters are now handled the way punctuation already was: commit what is
there, and let the character through.

## One design decision worth keeping

`_show()` now wraps placement and display together, and a popup that cannot be
placed is **not shown**. Whether a keystroke is claimed must not depend on
whether a popup could be drawn — that coupling is the entire mechanism of the
first defect, and it converted a cosmetic failure into text corruption in the
user's document.

Honest note: with `_place` corrected, that guard is not currently load-bearing
— the `guardonly` mutation below passes 29/29. It is there so the next
positioning failure costs a missing popup rather than a ruined paragraph.

## Gate

`tools/pinyin_ime_selftest.py`, 29 checks. Every keystroke is a real
`Gdk.Event` emitted on a real window with a real focused widget, so the wiring
is under test and not only the class — a handler that fails to claim a key
shows up as raw pinyin sitting in the field, which is the visible failure.
Covers: off by default, the raw pinyin staying out of the field, Space, digit
selection, paging (digit 1 on page 2 must give the tenth candidate), Backspace
editing the composition rather than the document, Esc abandoning only the
composition, punctuation committing *and* typing itself, toggling off,
committing over a selection, and all of it again in a TextView.

It asserts the dictionary loaded first: a missing `pinyin.dict.xz` would turn
every check below into a test of the empty case and pass quietly.

**Red-proof, five mutations:**

| mutation | result |
|---|---|
| exactly what shipped (no fix, no guard) | 3 fail — the corruption and the popup |
| guard present, `get_window` still wrong | 1 fail — the popup check alone catches it |
| `get_window` fixed, guard removed | 0 fail — the guard is not load-bearing today |
| a letter no longer claims the keystroke | 11 fail |
| `isalpha()` restored | 7 fail |

The second row is the point of the popup-visibility check: once the guard stops
a placement failure from corrupting text, something else has to notice that
placement failed at all.

## Still uncovered

Seven modules no suite imports: `shell` (1337 lines, the panel),
`nbmediakeys` (470), and the five tiny X helpers (`xflush`, `xflushd`,
`xnudge`, `xrootbg`, `xshape`). `shell` is the next one worth a gate.
