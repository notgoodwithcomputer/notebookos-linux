#!/usr/bin/env python3
"""login_kbd_apply_selftest — the sign-in screen may only claim a keymap it
actually loaded.

    python3 tools/login_kbd_apply_selftest.py        (no display needed)

WHAT THIS PROTECTS
de/login.py's keyboard row exists for one reason: on a Russian, Greek, Hindi or
Yiddish machine the live half of a dual layout is the non-Latin one, the
password field is masked, and somebody typing a Latin password sees nothing but
"that password did not work" forever. The row answers the question the masked
field cannot — which alphabet are these keys in — and offers the other one.

That answer is only worth anything if it is TRUE. `nbkeyboard.apply()` is a
subprocess and returns whether setxkbmap said it worked; it is False on a
machine with no setxkbmap (nbkeyboard's own docstring contemplates exactly
that), on a keymap xkbcomp will not compile, and on a timeout.

_setup_keyboard called it for its side effect and adopted the requested code
either way. Measured, on the machine case 2 of its docstring was written for
(saved "ru,us", a previous sign-in remembered on "us"):

    setxkbmap FAILED -> screen says live: English (US)   warning: ''

The keys were still typing Russian — session.sh's layout, untouched — while the
one indicator on the screen said English, and _kbd_warning() went silent
because it only speaks when the live group cannot type ASCII. Nothing corrects
it later either: _track_group maps the X group through the same wrong table, so
every keystroke re-confirms the lie.

login_keyboard_selftest.py cannot see this: its stand-in for the X server
answers True to every apply, which is the only case where believing it is
right. _set_kbd_group — the same decision, made from a button press instead of
at construction — already refuses to believe a failed apply; this holds
_setup_keyboard to that.

Driven on a real login.Login through __new__, the way login_lifecycle_selftest
does: _setup_keyboard, _track_group and _kbd_warning touch no Gtk widget, so
the invariant is checkable with no display and no keymap is ever loaded.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

os.environ.setdefault("NB_HOME", "/tmp/nb-loginkbd-apply")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import login                                                    # noqa: E402
import nbkeyboard                                               # noqa: E402

FAILURES = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    print("%s  %s" % ("ok  " if cond else "FAIL", what))
    if not cond:
        FAILURES.append(what)


class _Machine:
    """nbi18n's answers, and an X server that can refuse.

    `applied` records what setxkbmap was ASKED for, so a refusal is told apart
    from never having tried."""

    def __init__(self, saved, remembered="", loads=True, live=""):
        self.saved = saved
        self.remembered = remembered
        self.loads = loads
        self.applied = []
        # what the X server says is loaded ("" = it cannot say). The screen
        # asks this when the saved layout is showing, because session.sh may
        # have fallen back to US; here it must never be the developer's host.
        self.live = live

    def keyboard(self):
        return self.saved

    def login_keyboard(self):
        return self.remembered

    def set_login_keyboard(self, code):
        self.remembered = code
        return True

    def __enter__(self):
        self._i18n, self._apply = login.nbi18n, nbkeyboard.apply
        self._live = nbkeyboard.live_code
        login.nbi18n = self
        nbkeyboard.apply = self._do_apply
        nbkeyboard.live_code = lambda *a, **k: self.live
        return self

    def __exit__(self, *_e):
        login.nbi18n, nbkeyboard.apply = self._i18n, self._apply
        nbkeyboard.live_code = self._live
        return False

    def _do_apply(self, code, timeout=10):
        self.applied.append(code)
        return self.loads


def screen(win):
    """The group name the screen is showing as live, or "" when it draws no
    row at all."""
    if not win._kb_groups:
        return ""
    return nbkeyboard.group_name(*win._kb_groups[win._kb_active])


def build(machine):
    """The real _setup_keyboard, with no window and no display."""
    win = login.Login.__new__(login.Login)
    win._setup_keyboard()
    return win


# -- 1. the machine the whole row exists for, with a working setxkbmap --------
# Nothing here may change: the fix is about a REFUSAL, and a screen that stopped
# honouring the remembered half would be the original defect back again.
print("\n-- Cyrillic machine, a Latin half remembered, setxkbmap works")
with _Machine("ru,us", remembered="us", loads=True) as m:
    win = build(m)
    check(m.applied == ["us,ru"],
          "the remembered half is asked for: %r" % (m.applied,))
    check(screen(win) == "English (US)",
          "...and shown as live, because it IS live: %r" % screen(win))
    check(win._kbd_warning() == "",
          "nothing to warn about when the keys really are Latin")
    check(win._kb_code == "us,ru" and win._kb_loaded == "us,ru",
          "the screen records the code it loaded (%r/%r)"
          % (win._kb_code, win._kb_loaded))


# -- 2. THE REGRESSION: the same machine, where setxkbmap cannot load it ------
print("\n-- ...and where setxkbmap refuses (no setxkbmap, or xkbcomp fails)")
with _Machine("ru,us", remembered="us", loads=False) as m:
    win = build(m)
    check(m.applied == ["us,ru"],
          "it still TRIES to load the remembered half: %r" % (m.applied,))
    check(screen(win) == "Русский",
          "the chip names the alphabet the keys are ACTUALLY in — session.sh's "
          "layout is what is still loaded (got %r)" % screen(win))
    check(win._kb_loaded == "ru,us",
          "the screen does not record a keymap it failed to load (%r)"
          % win._kb_loaded)
    warn = win._kbd_warning()
    check("Русский" in warn and "English (US)" in warn,
          "a wrong password says which alphabet the keys are in and offers "
          "the other one — the sentence a masked field cannot show any other "
          "way: %r" % warn)

    # X reports the live group on every key event, and it is group 0: the
    # untouched "ru,us". Read through a table built from a keymap that never
    # loaded, that reading confirmed the wrong half instead of correcting it.
    class _Ev:
        group = 0

    win._kb_btns = []
    win._track_group(_Ev())
    check(screen(win) == "Русский",
          "typing does not talk the screen back into the wrong half: %r"
          % screen(win))


# -- 3. no Latin half at all (kana), where the added one cannot be loaded -----
# It must not promise an alphabet that is not there. This machine is genuinely
# stuck until setxkbmap works, and saying so is the only honest answer.
print("\n-- kana machine whose added Latin half cannot be loaded")
with _Machine("jp(kana)", loads=False) as m:
    win = build(m)
    check(m.applied == ["jp(kana),us"],
          "the Latin half is still attempted: %r" % (m.applied,))
    check(win._kb_groups == [("jp", "kana")],
          "...and not offered as though it were there: %r" % (win._kb_groups,))
    check(win._kbd_warning() == "",
          "no sentence pointing at a button that would do nothing: %r"
          % win._kbd_warning())
with _Machine("jp(kana)", loads=True) as m:
    win = build(m)
    check(win._kb_groups == [("jp", "kana"), ("us", "")],
          "...while a load that WORKS is offered exactly as before: %r"
          % (win._kb_groups,))


# -- 4. the single-layout machine loads nothing and so can believe nothing ----
print("\n-- single-layout machine")
for loads in (True, False):
    with _Machine("us", loads=loads) as m:
        win = build(m)
        check(m.applied == [] and win._kb_groups == [("us", "")],
              "setxkbmap=%s: nothing is loaded and nothing changes: %r / %r"
              % (loads, m.applied, win._kb_groups))


print()
if FAILURES:
    print("LOGIN KEYBOARD APPLY SELFTEST: %d checks, %d FAILED"
          % (CHECKS[0], len(FAILURES)))
    print("RESULT: FAIL")
    sys.exit(1)
print("LOGIN KEYBOARD APPLY SELFTEST: %d checks, all pass" % CHECKS[0])
print("RESULT: PASS")
