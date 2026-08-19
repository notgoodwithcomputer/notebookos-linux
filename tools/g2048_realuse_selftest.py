#!/usr/bin/env python3
"""g2048_realuse_selftest — drive 2048 the way a person does and check the
three things a person actually notices about its chrome.

    tools/guestrun.sh python3 tools/g2048_realuse_selftest.py

Every check hosts the REAL app in tools/appdrive (its own handlers, its own
menu callbacks, its own store) and pushes REAL key events through the window's
key ladder, because all three defects these checks pin lived in the ladder and
in the menu labels, not in the game arithmetic the older suites cover:

  about-card-blocks-moves       the About card is up, so arrow keys must not
                                slide tiles and spawn new ones behind it;
  reset-best-label-promise      MENU-CONVENTIONS rule 1 — an item ending in
                                "…" asks first, an item without one does not;
  win-banner-esc-dismisses      Esc leaves the innermost thing: the "2048
                                reached" banner first, the app only after.

Checks report by NAME and never by traceback: an exception inside one is
caught and printed as that check's FAIL, so a broken check can never be read
as a green suite. Exit status is the number of failures.
"""
import os
import sys
import copy
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import appdrive                                               # noqa: E402
from gi.repository import Gtk                                 # noqa: E402

FAILS = []
HOMES = tempfile.mkdtemp(prefix="g2048-realuse-")


def check(name, fn):
    """Run one check; a raised exception is that check's FAIL, not a crash."""
    try:
        problem = fn()
    except Exception as exc:                                  # noqa: BLE001
        problem = "raised %s: %s" % (type(exc).__name__, exc)
    if problem:
        FAILS.append(name)
        print("FAIL %-28s %s" % (name, problem))
    else:
        print("PASS %-28s" % name)


def drive(sub):
    return appdrive.Drive("g2048", home=os.path.join(HOMES, sub))


def about_open(d):
    """Is the About card up? Read the handle nbapp itself sets and clears
    (`_about_close`), not a name the app under test happens to define — a
    check that asks the app for its own opinion of "About is open" cannot
    catch a guard that reads the wrong attribute."""
    return getattr(d.app, "_about_close", None) is not None


def _asks(d):
    """A count of the surfaces an 'ask' could arrive on: separate toplevel
    windows, plus layers added to the app's own Gtk.Overlay (the house form
    for a confirm on a compositor-less desktop)."""
    tops = len([w for w in Gtk.Window.list_toplevels() if w.get_visible()])
    try:
        layers = len(d.app._overlay.get_children())
    except Exception:                                         # noqa: BLE001
        layers = 0
    return tops + layers


# -- about-card-blocks-moves ------------------------------------------------
def about_card_blocks_moves():
    """With About showing, an arrow key must change nothing; once About is
    dismissed the same key must move again (the guard may not be a lock)."""
    d = drive("about")
    try:
        d.app.board = [[2, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
        d.app.score = 0
        d.app.status = "play"
        d.app._refresh()
        d.menu_action("2048", "About")
        d.pump(0.3)
        if not about_open(d):
            return "About did not open, so the check proves nothing"
        before = copy.deepcopy(d.app.board)
        d.key("Right")
        d.pump(0.3)
        if d.app.board != before:
            return ("Right moved tiles behind the open About card: %r -> %r"
                    % (before, d.app.board))
        if d.app.score != 0:
            return "score changed behind the open About card: %r" % d.app.score
        d.key("Escape")
        d.pump(0.3)
        if about_open(d):
            return "Esc did not dismiss the About card"
        if d.app._closed:
            return "Esc on the About card closed the app instead of the card"
        before = copy.deepcopy(d.app.board)
        d.key("Right")
        d.pump(0.3)
        if d.app.board == before:
            return "the board stayed frozen after About was dismissed"
        return ""
    finally:
        d.close()


# -- reset-best-label-promise ----------------------------------------------
def reset_best_label_promise():
    """MENU-CONVENTIONS rule 1, checked as an invariant rather than a pinned
    string: fire Reset Best Score and compare what the label PROMISED with
    what the app DID. An ellipsis must be paid for with an ask; no ellipsis
    must be paid for with the action happening immediately."""
    d = drive("reset")
    try:
        d.app.board = [[2, 4, 8, 16], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
        d.app.score = 332
        d.app.best = 332
        d.app.status = "play"
        d.app._refresh()
        item = None
        for it in d.menu("Game"):
            if isinstance(it, tuple) and it[0].startswith("Reset Best Score"):
                item = it
                break
        if item is None:
            return "no Reset Best Score item in the Game menu"
        label, cb = item[0], item[1]
        if cb is None:
            return "Reset Best Score was greyed with a best score of 332"
        promises_ask = "…" in label or "..." in label
        before = _asks(d)
        cb()
        d.pump(0.4)
        asked = _asks(d) > before
        acted = d.app.best == 0
        if promises_ask and not asked:
            return ("label %r promises an ask, but firing it opened nothing "
                    "(best went %s -> %s straight away)"
                    % (label, 332, d.app.best))
        if asked and not promises_ask:
            return "label %r opened an ask but carries no ellipsis" % label
        if not promises_ask and not acted:
            return ("label %r promises the action happens immediately, but "
                    "best is still %r" % (label, d.app.best))
        return ""
    finally:
        d.close()


# -- win-banner-esc-dismisses ----------------------------------------------
def win_banner_esc_dismisses():
    """Esc leaves the innermost thing. On the "2048 reached" banner that is
    the banner (the same outcome as its own Continue button); a second Esc
    leaves the app. The no-moves banner offers nothing to continue to, so Esc
    there must still leave the app."""
    d = drive("win")
    try:
        d.app.board = [[1024, 1024, 0, 0], [0, 0, 0, 0],
                       [4, 0, 0, 0], [0, 0, 0, 0]]
        d.app.score = 0
        d.app.status = "play"
        d.app._won_shown = False
        d.app._refresh()
        d.key("Left")
        d.pump(0.3)
        if d.app.status != "win" or not d.app.ov_box.get_visible():
            return "the win banner did not come up, so the check proves nothing"
        board = copy.deepcopy(d.app.board)
        d.key("Escape")
        d.pump(0.3)
        if d.app._closed:
            return "Esc quit the app instead of dismissing the 2048 banner"
        if d.app.status != "play" or d.app.ov_box.get_visible():
            return ("Esc left the banner up: status %r, banner visible %r"
                    % (d.app.status, d.app.ov_box.get_visible()))
        if d.app.board != board:
            return "Esc changed the board: %r -> %r" % (board, d.app.board)
        d.key("Escape")
        d.pump(0.3)
        if not d.app._closed:
            return "a second Esc, with nothing open, did not leave the app"
    finally:
        d.close()
    # the no-moves banner keeps the base behaviour
    d = drive("lose")
    try:
        d.app.board = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
        d.app.score = 100
        d.app.status = "lose"
        d.app._refresh()
        d.key("Escape")
        d.pump(0.3)
        if not d.app._closed:
            return "Esc on the no-moves banner no longer leaves the app"
        return ""
    finally:
        d.close()


def main():
    check("about-card-blocks-moves", about_card_blocks_moves)
    check("reset-best-label-promise", reset_best_label_promise)
    check("win-banner-esc-dismisses", win_banner_esc_dismisses)
    print("RESULT: %s" % ("ALL PASS" if not FAILS
                          else "%d FAILED: %s" % (len(FAILS), ", ".join(FAILS))))
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
