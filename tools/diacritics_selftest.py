#!/usr/bin/env python3
"""
Headless selftest for the press-and-hold accent picker (de/nbdiacritics.py).

The picker sits between every keystroke and every text field in the OS, so it
has to be exactly right in two dimensions:

  * the TABLE — does it actually carry the letters the shipped Language
    courses need (Spanish, French, Esperanto, Serbo-Croatian), and is every row
    well-formed (single characters, at most the nine the 1-9 keys can reach)?
  * the state machine — does a hold open the palette, a tap NOT open it, an
    X autorepeat pair read as "still held", a commit REPLACE the letter that
    was already typed, and does it stay out of the way of password fields,
    spin buttons, read-only views and the Pinyin IME?

Real Gtk widgets are used (a real Entry, a real TextView, real focus, real
GLib timers) with synthetic key events fed straight to the handlers, so the
logic under test is the shipped logic. Nothing is ever mapped on screen, so
this is safe to run against a live desktop.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  python3 diacritics_selftest.py
"""
import os
import sys
import tempfile

os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbdia-home-"))

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import nbdiacritics  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %-58s %s%s" % (name, "PASS" if cond else "FAIL",
                            "" if cond or not detail else "  <- " + detail))


class Ev(object):
    """Duck-typed Gdk key event: the handlers read keyval, string and state."""

    def __init__(self, keyval, string="", state=0):
        self.keyval = keyval
        self.string = string
        self.state = Gdk.ModifierType(state)


def pump(ms=0):
    """Run the real main loop for `ms` milliseconds (0 = just drain)."""
    if ms:
        done = []
        GLib.timeout_add(ms, lambda: (done.append(1), False)[1])
        while not done:
            Gtk.main_iteration_do(False)
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def make_host(widget):
    """A window shaped like nbapp's: a Gtk.Overlay the picker can mount into."""
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_default_size(900, 600)
    overlay = Gtk.Overlay()
    box = Gtk.Box()
    box.pack_start(widget, True, True, 0)
    overlay.add(box)
    win.add(overlay)
    win._overlay = overlay
    win.realize()               # realized, never shown: nothing appears on screen
    win.set_focus(widget)
    return win


def entry_host(text="", visible=True, editable=True, spin=False):
    w = Gtk.SpinButton() if spin else Gtk.Entry()
    if not spin:
        w.set_visibility(visible)
        w.set_editable(editable)
        w.set_text(text)
        w.set_position(len(text))
    return w, make_host(w)


# ---------------------------------------------------------------- table ----
def test_table():
    print("\nTABLE — shape and coverage")
    tbl = nbdiacritics.TABLE
    bad = [(k, v) for k, v in tbl.items()
           if not isinstance(v, tuple) or not 1 <= len(v) <= nbdiacritics.MAX_ITEMS
           or any(len(c) != 1 for c in v)]
    check("every row is 1-9 single characters", not bad, str(bad[:3]))
    dupes = [k for k, v in tbl.items() if len(set(v)) != len(v)]
    check("no row repeats a variant", not dupes, str(dupes[:5]))
    check("uppercase rows derived (A, E, C, S)",
          all(k in tbl for k in "AECS"))
    check("uppercase rows are uppercase",
          all(c.isupper() or not c.isalpha()
              for k in "AECSNOU" for c in tbl.get(k, ())))
    check("multi-char uppercase dropped (no SS from ss)",
          all(len(c) == 1 for c in tbl.get("S", ())))
    # the courses this OS actually ships
    need = {
        "Spanish":        "áéíóúñ¿¡ü",
        "French":         "àâçèéêëîïôùûüÿœ",
        "Esperanto":      "ĉĝĥĵŝŭ",
        "Serbo-Croatian": "čćžšđ",
    }
    have = set()
    for v in tbl.values():
        have.update(v)
    for lang, chars in need.items():
        missing = [c for c in chars if c not in have]
        check("%s letters all reachable" % lang, not missing,
              "missing " + "".join(missing))
    check("hold ? gives the inverted question mark", tbl.get("?") == ("¿",))
    check("hold ! gives the inverted exclamation", tbl.get("!") == ("¡",))
    check("no accents on a bare consonant like q/v", "q" not in tbl and "v" not in tbl)


# ------------------------------------------------------------ behaviour ----
def test_hold_opens():
    print("\nHOLD — opening and dismissing the palette")
    nbdiacritics.HOLD_MS = 40
    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)

    swallowed = p._on_press(win, Ev(Gdk.KEY_e, "e"))
    check("first press is NOT swallowed (letter types normally)", swallowed is False)
    check("palette not open immediately", p._open is False)
    e.set_text("e"); e.set_position(1)          # what GTK would have typed
    pump(90)
    check("palette opens after the hold elapses", p._open is True)
    check("palette holds e's variants", p._items[:3] == ("é", "è", "ê"),
          str(p._items[:3]))

    got = p._on_press(win, Ev(Gdk.KEY_3, "3"))
    check("digit key is swallowed by the palette", got is True)
    check("picking 3 replaces the typed letter with ê", e.get_text() == "ê",
          repr(e.get_text()))
    check("palette closed after a pick", p._open is False)
    check("caret left after the inserted character", e.get_position() == 1)
    win.destroy()


def test_escape_and_arrows():
    print("\nHOLD — Esc, arrows, Return, click")
    nbdiacritics.HOLD_MS = 40
    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); e.set_text("e"); e.set_position(1)
    pump(90)
    got = p._on_press(win, Ev(Gdk.KEY_Escape))
    check("Esc is swallowed by the open palette", got is True)
    check("Esc leaves the plain letter alone", e.get_text() == "e")
    check("Esc closes the palette", p._open is False)

    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    p._on_press(win, Ev(Gdk.KEY_Right))
    p._on_press(win, Ev(Gdk.KEY_Right))
    check("arrows move the selection", p._sel == 2, str(p._sel))
    p._on_press(win, Ev(Gdk.KEY_Left))
    check("left arrow moves back", p._sel == 1)
    p._on_press(win, Ev(Gdk.KEY_Return))
    check("Return commits the highlighted variant", e.get_text() == "è",
          repr(e.get_text()))

    e.set_text("e"); e.set_position(1)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    sel = p._sel
    p._on_press(win, Ev(Gdk.KEY_Left))
    check("selection wraps backwards from the first tile",
          p._sel == len(p._items) - 1, str((sel, p._sel)))
    p._buttons[0].emit("clicked")
    check("clicking a tile commits it", e.get_text() == "é", repr(e.get_text()))

    e.set_text("e"); e.set_position(1)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    got = p._on_press(win, Ev(Gdk.KEY_BackSpace))
    check("a non-printable key is NOT swallowed", got is False)
    check("...and it dismisses the palette", p._open is False)
    win.destroy()


def test_autorepeat():
    print("\nHOLD — X autorepeat handling (no XKB detectable repeat)")
    nbdiacritics.HOLD_MS = 400          # deliberately longer than the test waits
    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); e.set_text("e"); e.set_position(1)
    # X delivers repeats as release+press pairs; the press arrives immediately.
    p._on_release(win, Ev(Gdk.KEY_e, "e"))
    got = p._on_press(win, Ev(Gdk.KEY_e, "e"))
    check("autorepeat press is swallowed (no eeeee)", got is True)
    check("autorepeat opens the palette early", p._open is True)
    check("only ONE letter was typed", e.get_text() == "e", repr(e.get_text()))
    p._close()

    # A genuine tap: press, release, and nothing after it.
    nbdiacritics.HOLD_MS = 40
    p._on_press(win, Ev(Gdk.KEY_a, "a"))
    p._on_release(win, Ev(Gdk.KEY_a, "a"))
    pump(90)
    check("a tap (press+release) never opens the palette", p._open is False)
    check("a tap stops being tracked", p._held is None)

    # Release then re-press INSIDE the grace window = autorepeat, still held.
    p._on_press(win, Ev(Gdk.KEY_a, "a"))
    p._on_release(win, Ev(Gdk.KEY_a, "a"))
    p._on_press(win, Ev(Gdk.KEY_a, "a"))
    pump(90)
    check("release+press inside the grace window stays held", p._open is True)
    p._close()

    # Typing a different key abandons the hold and starts tracking the new one.
    p._on_press(win, Ev(Gdk.KEY_a, "a"))
    p._on_press(win, Ev(Gdk.KEY_o, "o"))
    check("a different key abandons the previous hold",
          p._held is not None and p._held[1] == "o", str(p._held))
    # ...and a key with no accents leaves nothing being tracked at all.
    p._on_press(win, Ev(Gdk.KEY_b, "b"))
    check("a key with no accents clears the tracked hold", p._held is None)
    pump(90)
    check("no palette after typing through a hold", p._open is False)
    win.destroy()


def test_palette_printable_replay():
    print("\nHOLD — printable replay while the palette is open")
    nbdiacritics.HOLD_MS = 40

    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); e.set_text("e"); e.set_position(1)
    pump(90)
    if not p._open:
        check("palette replay into Entry is consumed and inserted once", False,
              "[not reached: palette did not open]")
    else:
        got = p._on_press(win, Ev(Gdk.KEY_a, "a"))
        check("palette replay into Entry is consumed and inserted once",
              got is True and e.get_text() == "ea",
              "return=%r text=%r" % (got, e.get_text()))
    win.destroy()

    tv = Gtk.TextView()
    win = make_host(tv)
    buf = tv.get_buffer()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); buf.insert_at_cursor("e")
    pump(90)
    if not p._open:
        check("palette replay into TextView is consumed and inserted once", False,
              "[not reached: palette did not open]")
    else:
        got = p._on_press(win, Ev(Gdk.KEY_o, "o"))
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        check("palette replay into TextView is consumed and inserted once",
              got is True and txt == "eo",
              "return=%r text=%r" % (got, txt))
    win.destroy()

    e, win = entry_host("e")
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    if not p._open:
        check("Ctrl chord closes palette without being consumed", False,
              "[not reached: palette did not open]")
    else:
        got = p._on_press(win, Ev(Gdk.KEY_a, "a",
                                  Gdk.ModifierType.CONTROL_MASK))
        check("Ctrl chord closes palette without being consumed",
              got is False and p._open is False,
              "return=%r open=%r" % (got, p._open))
    win.destroy()

    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); e.set_text("e"); e.set_position(1)
    pump(90)
    if not p._open:
        check("replayed key starts a fresh hold without double typing", False,
              "[not reached: first palette did not open]")
    else:
        got = p._on_press(win, Ev(Gdk.KEY_a, "a"))
        replayed = e.get_text()
        repeated = p._on_press(win, Ev(Gdk.KEY_a, "a"))
        check("replayed key starts a fresh hold without double typing",
              got is True and repeated is True and p._open
              and p._base == "a" and e.get_text() == "ea",
              "replay=%r repeat=%r open=%r base=%r before=%r after=%r" %
              (got, repeated, p._open, p._base, replayed, e.get_text()))
    win.destroy()

    e, win = entry_host("e")
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    if not p._open:
        check("missing replay target is not consumed", False,
              "[not reached: palette did not open]")
    else:
        p._target = None
        got = p._on_press(win, Ev(Gdk.KEY_a, "a"))
        check("missing replay target is not consumed",
              got is False and p._open is False and e.get_text() == "e",
              "return=%r open=%r text=%r" % (got, p._open, e.get_text()))
    win.destroy()


def test_eligibility():
    print("\nELIGIBILITY — where the palette must stay out of the way")
    nbdiacritics.HOLD_MS = 40

    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e", Gdk.ModifierType.CONTROL_MASK))
    pump(90)
    check("Ctrl+e is an accelerator, not a hold", p._open is False)
    p._on_press(win, Ev(Gdk.KEY_e, "e", Gdk.ModifierType.MOD1_MASK))
    pump(90)
    check("Alt+e is an accelerator, not a hold", p._open is False)
    p._on_press(win, Ev(Gdk.KEY_q, "q")); pump(90)
    check("a letter with no accents never opens a palette", p._open is False)
    win.destroy()

    e, win = entry_host(visible=False)
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    check("password field is skipped (never echo the character)", p._open is False)
    win.destroy()

    e, win = entry_host(editable=False)
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    check("read-only entry is skipped", p._open is False)
    win.destroy()

    e, win = entry_host(spin=True)
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_1, "1")); pump(90)
    check("spin button is skipped (a number field wants no fractions)",
          p._open is False)
    win.destroy()

    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)

    class FakeIME(object):
        active = True
        buffer = "ni"
    win._pinyin = FakeIME()
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    check("Pinyin composition keeps the keystroke", p._open is False)
    win._pinyin = None
    win.destroy()

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.realize()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    check("no focused text widget = nothing happens", p._open is False)
    win.destroy()


def test_textview_and_guard():
    print("\nCOMMIT — TextView, replacement guard, uppercase, punctuation")
    nbdiacritics.HOLD_MS = 40
    tv = Gtk.TextView()
    win = make_host(tv)
    buf = tv.get_buffer()
    buf.set_text("caf")
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e"))
    buf.insert_at_cursor("e")
    pump(90)
    check("palette opens in a TextView", p._open is True)
    p._on_press(win, Ev(Gdk.KEY_1, "1"))
    txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    check("TextView commit replaces the letter", txt == "café", repr(txt))

    # The guard: if the character before the caret is NOT the one we typed
    # (the app moved the caret / rewrote the text), insert without deleting.
    buf.set_text("xy")
    p._on_press(win, Ev(Gdk.KEY_e, "e"))
    pump(90)
    p._on_press(win, Ev(Gdk.KEY_1, "1"))
    txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    check("never eats text it did not type", txt == "xyé", repr(txt))
    win.destroy()

    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_E, "E", Gdk.ModifierType.SHIFT_MASK))
    e.set_text("E"); e.set_position(1)
    pump(90)
    check("Shift+hold opens the uppercase row", p._open and p._items[0] == "É",
          str(p._items[:3]))
    p._on_press(win, Ev(Gdk.KEY_1, "1"))
    check("uppercase commit", e.get_text() == "É", repr(e.get_text()))

    e.set_text("Como"); e.set_position(4)
    p._on_press(win, Ev(Gdk.KEY_question, "?", Gdk.ModifierType.SHIFT_MASK))
    e.set_text("Como?"); e.set_position(5)
    pump(90)
    check("punctuation holds work too", p._open is True)
    p._on_press(win, Ev(Gdk.KEY_1, "1"))
    check("hold ? -> ¿", e.get_text() == "Como¿", repr(e.get_text()))
    win.destroy()


def test_teardown_and_wiring():
    print("\nLIFECYCLE — teardown and the nbapp wiring")
    nbdiacritics.HOLD_MS = 40
    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e"))
    win.destroy()                 # destroyed with a hold timer still armed
    pump(120)
    check("destroying mid-hold cancels the timers", p._hold_src == 0 and p._dead)

    e, win = entry_host()
    p = nbdiacritics.DiacriticsPicker(win)
    p._on_press(win, Ev(Gdk.KEY_e, "e")); pump(90)
    check("palette mounted into the overlay layer", p._layer is not None)
    p._close()
    check("closing removes the layer", p._layer is None)
    win.destroy()

    try:
        import nbapp
        w = nbapp.AppWindow()     # constructed, never shown
        ok = getattr(w, "_diacritics", None) is not None
        check("nbapp.AppWindow installs the picker for every app", ok)
        pyi = getattr(w, "_pinyin", None)
        check("the Pinyin IME is still installed alongside it", pyi is not None)
        w.destroy()
        pump()
    except Exception as exc:
        check("nbapp.AppWindow installs the picker for every app", False, repr(exc))


def main():
    print("=" * 74)
    print("nbdiacritics selftest — press-and-hold accent picker")
    print("=" * 74)
    test_table()
    test_hold_opens()
    test_escape_and_arrows()
    test_autorepeat()
    test_palette_printable_replay()
    test_eligibility()
    test_textview_and_guard()
    test_teardown_and_wiring()
    print("\n" + "=" * 74)
    print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("   FAILED: " + f)
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
