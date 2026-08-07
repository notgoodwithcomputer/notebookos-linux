#!/usr/bin/env python3
"""
Typing Chinese.

`de/nbpinyin.py` is the ONLY way to enter Chinese on this machine — no ibus, no
fcitx — and nbapp installs it into every window, so it is on the keystroke path
of all thirty apps. It had **no gate of any kind**: the i18n suites check
catalogs, which is what the interface says to you, not what you can say back.

Everything here goes through real `Gdk.Event`s emitted on a real window with a
real focused widget, so the wiring is under test too, not just the class. A
handler that fails to claim a keystroke shows up as raw pinyin sitting in the
entry — which is precisely the visible failure.

Run:
    tools/guestrun.sh python3 tools/pinyin_ime_selftest.py
    tools/guestrun.sh python3 tools/pinyin_ime_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-py-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

import nbpinyin  # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(200):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


class Field(object):
    """A window with one focused text widget, driven by real key events."""

    def __init__(self, textview=False):
        self.win = Gtk.Window()
        self.tgt = Gtk.TextView() if textview else Gtk.Entry()
        self.win.add(self.tgt)
        self.win.show_all()
        self.tgt.grab_focus()
        pump()
        self.ime = nbpinyin.PinyinIME(self.win)

    def key(self, keyval, string="", ctrl=False):
        ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        ev.keyval = keyval
        ev.state = (Gdk.ModifierType.CONTROL_MASK if ctrl
                    else Gdk.ModifierType(0))
        ev.string = string
        ev.window = self.win.get_window()
        self.win.emit("key-press-event", ev)
        pump()

    def type(self, text):
        for c in text:
            self.key(Gdk.unicode_to_keyval(ord(c)), c)

    def toggle(self):
        self.key(Gdk.KEY_space, " ", ctrl=True)

    def text(self):
        if isinstance(self.tgt, Gtk.TextView):
            b = self.tgt.get_buffer()
            return b.get_text(b.get_start_iter(), b.get_end_iter(), False)
        return self.tgt.get_text()

    def popup_text(self):
        lbl = self.ime._pop_label
        return lbl.get_text() if lbl is not None else ""

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def main():
    # The dictionary is the feature. A missing one turns every check below
    # into a test of the empty case, which would pass quietly.
    nbpinyin.PinyinIME._load()
    d = nbpinyin.PinyinIME._dict
    loaded = check("the pinyin dictionary loads", bool(d), "%d keys" % len(d or {}))
    if not loaded:
        print("\nRESULT: FAILED\n  no dictionary — nothing below is meaningful")
        return 1
    check("...and knows 你好", d.get("nihao") == ["你好"], repr(d.get("nihao")))

    f = Field()

    # ---- off by default: an app must type ASCII until asked otherwise ----
    f.type("ni")
    check("with the IME off, letters go straight through", f.text() == "ni",
          repr(f.text()))
    f.tgt.set_text("")

    # ---- composing: the raw pinyin must NOT reach the field --------------
    f.toggle()
    f.type("nihao")
    held = check("while composing, the entry stays empty", f.text() == "",
                 repr(f.text()))
    check("...and the buffer holds the pinyin", f.ime.buffer == "nihao",
          repr(f.ime.buffer))
    shown = f.popup_text()
    check("...and the popup offers 你好", "你好" in shown, repr(shown))

    # ---- Space commits the first candidate -------------------------------
    f.key(Gdk.KEY_space, " ")
    check("Space commits the candidate", f.text() == "你好", repr(f.text()))
    check("...and the composition is cleared", f.ime.buffer == "",
          repr(f.ime.buffer))
    f.tgt.set_text("")

    # ---- a digit picks that numbered candidate ---------------------------
    f.type("ni")
    third = f.ime.cands[2] if len(f.ime.cands) > 2 else None
    if third:
        f.key(Gdk.KEY_3, "3")
        check("the digit 3 commits the THIRD candidate, not the first",
              f.text() == third, "%r wanted %r" % (f.text(), third))
    else:
        not_reached("fewer than three candidates",
                    "the digit 3 commits the THIRD candidate, not the first")
    f.tgt.set_text("")

    # ---- paging ----------------------------------------------------------
    f.type("shi")
    cands = list(f.ime.cands)
    if len(cands) > nbpinyin.PAGE:
        f.key(Gdk.KEY_equal, "=")
        check("'=' turns to page 2", f.ime.page == 1, "page=%d" % f.ime.page)
        check("...and the popup says which page (%s)"
              % f.popup_text()[-12:].strip(), "2/" in f.popup_text())
        f.key(Gdk.KEY_1, "1")
        want = cands[nbpinyin.PAGE]
        check("digit 1 on page 2 commits the TENTH candidate",
              f.text() == want, "%r wanted %r" % (f.text(), want))
    else:
        not_reached("'shi' has one page only", "'=' turns to page 2",
                    "...and the popup says which page",
                    "digit 1 on page 2 commits the TENTH candidate")
    f.tgt.set_text("")
    f.ime._reset()

    # ---- Backspace edits the pinyin, it does not delete the field --------
    f.tgt.set_text("keep")
    f.tgt.set_position(-1)
    f.type("nihao")
    f.key(Gdk.KEY_BackSpace)
    check("Backspace shortens the pinyin", f.ime.buffer == "niha",
          repr(f.ime.buffer))
    check("...and leaves the text already in the field alone",
          f.text() == "keep", repr(f.text()))
    for _ in range(4):
        f.key(Gdk.KEY_BackSpace)
    check("backspacing past the start ends the composition",
          f.ime.buffer == "" and f.text() == "keep",
          "%r / %r" % (f.ime.buffer, f.text()))
    f.tgt.set_text("")

    # ---- Esc abandons, and abandons ONLY the composition -----------------
    f.tgt.set_text("keep")
    f.type("ni")
    f.key(Gdk.KEY_Escape)
    check("Esc abandons the composition without typing anything",
          f.text() == "keep" and f.ime.buffer == "",
          "%r / %r" % (f.text(), f.ime.buffer))
    f.tgt.set_text("")

    # ---- punctuation commits the candidate AND is itself typed -----------
    # "nihao," should read 你好, — losing the comma would be a silent drop.
    f.type("nihao")
    f.key(Gdk.KEY_comma, ",")
    check("punctuation commits the candidate and still types itself",
          f.text() == "你好,", repr(f.text()))
    f.tgt.set_text("")

    # ---- a letter pinyin is not written in ends the composition ----------
    # `str.isalpha()` is true of every Unicode letter, so é, ü, Cyrillic д and
    # 好 itself used to be appended to the pinyin: the buffer then matched
    # nothing, the popup emptied, and the keystroke was SWALLOWED, so on any
    # layout that can produce an accented letter the composition died with no
    # visible cause. These are handled the way punctuation already is.
    for ch, what in (("é", "an accented letter"), ("д", "another script"),
                     ("好", "a hanzi")):
        f.tgt.set_text("")
        f.ime._reset()
        f.type("nihao")
        f.key(Gdk.unicode_to_keyval(ord(ch)), ch)
        check("%s (%s) commits the candidate and types itself" % (what, ch),
              f.text() == "你好" + ch, repr(f.text()))
        check("...and does not sit in the pinyin buffer", ch not in f.ime.buffer,
              repr(f.ime.buffer))

    # It must not START one either — that swallowed the very first keystroke.
    f.tgt.set_text("")
    f.ime._reset()
    f.type("é")
    check("an accented letter does not start a composition",
          f.text() == "é" and f.ime.buffer == "",
          "%r / %r" % (f.text(), f.ime.buffer))
    f.tgt.set_text("")

    # ---- toggling off returns the keyboard --------------------------------
    f.toggle()
    f.type("ni")
    check("toggling off gives the plain letters back", f.text() == "ni",
          repr(f.text()))
    f.tgt.set_text("")

    # ---- committing over a selection replaces it --------------------------
    f.tgt.set_text("abc")
    f.tgt.select_region(0, 3)
    f.toggle()
    f.type("nihao")
    f.key(Gdk.KEY_space, " ")
    check("committing over a selection replaces it", f.text() == "你好",
          repr(f.text()))
    f.destroy()

    # ---- and all of it in a TextView, the other target type ---------------
    # This is where it was broken, and the breakage was invisible to an Entry:
    # Gtk.TextView.get_window(win_type) shadows the zero-argument
    # Gtk.Widget.get_window, so _place raised TypeError on every keystroke.
    # PyGObject swallows an exception out of a signal handler and treats it as
    # unhandled, so GTK passed the raw pinyin through — "nihao你好". Eleven
    # apps have a text area; that is every long-form writing surface in the OS.
    tv = Field(textview=True)
    tv.toggle()
    tv.type("nihao")
    check("in a TextView, the raw pinyin stays out of the document",
          tv.text() == "", repr(tv.text()))
    # The popup is now hidden rather than mispositioned when placement fails,
    # so its visibility is what catches a broken _place even though a
    # placement failure can no longer corrupt the text.
    check("...and the candidate popup is actually placed and shown",
          tv.ime.popup is not None and tv.ime.popup.get_visible())
    tv.key(Gdk.KEY_space, " ")
    check("...and the candidate commits", tv.text() == "你好", repr(tv.text()))
    tv.destroy()

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for x in FAILED:
            print("  " + x)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
