#!/usr/bin/env python3
"""present_card_selftest — the gate on nbtransitions.present_card.

present_card is the shared anchored-card PRESENTATION (PAPER-PHYSICS Article B),
extracted from the Finder so every app grows a confirm / About / info card from
the control that raised it, from one tested place. GrowCard is its paint;
transitions_selftest gates the four container primitives. This gate covers what
neither does — the presentation GrowCard alone is not:

  * Article B origin. GrowCard.grow raises without an anchor, so a card cannot
    forget where it came from; present_card allows ONE sanctioned exception —
    an explicit anchor=None centre-grows, for a surface with no on-screen origin
    (the Finder's grid view).
  * Instant EQUIVALENCE (constitution Rule 2 / Article F). Under a policy-still
    condition — here Reduced Motion, because policy() no longer consults NB_ACCEL
    (Amendment 1), so a bare software box with a frame clock ANIMATES — the card
    lands at its target and on_shown() has ALREADY run before present_card
    returns. The animated path's pacing is the frame-pacing harness's job.
  * Retract + remove. close() retracts and takes the overlay layer back out;
    on_close fires; a second close is idempotent.
  * The headless answer (no overlay): (None, close), on_shown/on_close still run.

Needs a display (it builds real Gtk widgets): run under DISPLAY=:0.

RED-PROOF (recorded 2026-08-08, textual mutate-run-revert): deleting the
`_call(on_shown)` line in present_card's reveal() turns two checks red —
  FAIL instant: on_shown fired before present_card returned
  FAIL Article B: anchor=None centre-grows (no raise, reveals)
and restoring the line returns 9/9. So the equivalence checks are not vacuous.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="presentcard-"))

import gi                                                          # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                      # noqa: E402
import nbtransitions as nbt                                        # noqa: E402
import nbmotion                                                    # noqa: E402

FAILURES = []
CHECKS = [0]


def check(label, ok, detail=""):
    CHECKS[0] += 1
    if not ok:
        FAILURES.append("%s%s" % (label, (": " + detail) if detail else ""))


def pump(n=60):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def _overlay():
    win = Gtk.Window()
    ov = Gtk.Overlay()
    base = Gtk.DrawingArea()
    base.set_size_request(400, 300)
    ov.add(base)
    win.add(ov)
    win.set_default_size(400, 300)
    win.show_all()
    pump()
    return win, ov


def _still(fn):
    """Run `fn` with motion forced still (Reduced Motion), restored after."""
    nbmotion.set_reduced_motion(True)
    try:
        fn()
    finally:
        nbmotion.set_reduced_motion(False)


def test_instant_equivalence():
    def body():
        win, ov = _overlay()
        shown = []
        card, _close = nbt.present_card(
            ov, Gtk.Label(label="x"), (10.0, 10.0, 20.0, 20.0),
            on_shown=lambda: shown.append(1), css_class="finderinfo")
        check("instant: on_shown fired before present_card returned",
              shown == [1])
        check("instant: card handle returned and parented",
              card is not None and card.get_parent() is not None)
        check("instant: card visible", bool(card and card.get_visible()))
        win.destroy()
        pump()
    _still(body)


def test_card_actually_shows():
    """The card must be ON SCREEN, centred on its own size, with its content.

    Three ways it silently was not (2026-08-16, found by the real-use drives
    reporting "About does nothing visible" in every app): the Fixed layer was
    never show()n (a visible child under a hidden parent), the card was
    positioned with a second gtk_fixed_put (a Gtk-CRITICAL no-op, so it
    stayed at 0,0), and it was measured hidden (GTK says 0x0 for a hidden
    widget) so the paper frame always grew to the 340x220 fallback around
    a much smaller card revealed top-left inside it."""
    def body():
        win, ov = _overlay()
        content = Gtk.Label(label="About me")          # deliberately unshown
        card, _close = nbt.present_card(
            ov, content, (10.0, 10.0, 20.0, 20.0), css_class="finderinfo")
        pump()
        layer = card.get_parent()
        check("shows: the layer itself is visible and mapped",
              layer is not None and layer.get_visible() and layer.get_mapped())
        check("shows: the content handed in is revealed, not just its holder",
              content.get_visible() and card.get_visible())
        x = layer.child_get_property(card, "x")
        y = layer.child_get_property(card, "y")
        check("shows: the card is placed at its centred target, not left at 0,0",
              x > 0 and y > 0, "x=%r y=%r" % (x, y))
        nat = card.get_preferred_size()[1]
        # centred on the REAL size: a small label card in a 400x300 host lands
        # well right of where a 340-wide fallback would (x = 30)
        check("shows: measured on the card's real size, not the 340x220 fallback",
              nat.width < 300 and x > 60,
              "nat=%dx%d x=%r" % (nat.width, nat.height, x))
        win.destroy()
        pump()
    _still(body)


def test_close_retract():
    def body():
        win, ov = _overlay()
        closed = []
        card, close = nbt.present_card(
            ov, Gtk.Label(label="x"), (10.0, 10.0, 20.0, 20.0),
            on_close=lambda: closed.append(1))
        layer = card.get_parent()
        check("close: layer parented before close",
              layer is not None and layer.get_parent() is not None)
        close()
        pump()
        check("close: on_close fired", closed == [1])
        check("close: layer removed from overlay", layer.get_parent() is None)
        close()
        pump()
        check("close: idempotent (no second on_close)", closed == [1])
        win.destroy()
        pump()
    _still(body)


def test_article_b():
    win, ov = _overlay()
    raised = False
    try:
        nbt.GrowCard(Gtk.DrawingArea()).grow(None, (0.0, 0.0, 10.0, 10.0))
    except ValueError:
        raised = True
    check("Article B: GrowCard.grow(None) raises (no surface from nowhere)",
          raised)

    def body():
        shown = []
        try:
            card, _close = nbt.present_card(
                ov, Gtk.Label(label="c"), None,
                on_shown=lambda: shown.append(1))
            ok = card is not None and shown == [1]
        except Exception:                                         # noqa: BLE001
            ok = False
        check("Article B: anchor=None centre-grows (no raise, reveals)", ok)
    _still(body)
    win.destroy()
    pump()


def test_headless():
    shown, closed = [], []
    card, close = nbt.present_card(
        None, None, (0.0, 0.0, 1.0, 1.0),
        on_shown=lambda: shown.append(1), on_close=lambda: closed.append(1))
    check("headless: no card built (overlay None)", card is None)
    check("headless: on_shown fired", shown == [1])
    close()
    check("headless: close fires on_close", closed == [1])


def main():
    test_instant_equivalence()
    test_card_actually_shows()
    test_close_retract()
    test_article_b()
    test_headless()
    n = CHECKS[0]
    if FAILURES:
        print("RESULT: FAILED — %d of %d checks" % (len(FAILURES), n))
        for f in FAILURES:
            print("  FAIL", f)
        return 1
    # "RESULT:" first, so the release runner has a verdict it recognises
    # (run_all_gates SUCCESSWORD); the descriptive line follows.
    print("RESULT: ALL PASS")
    print("PASS present_card: %d checks (Article B, instant-equivalence, "
          "retract, headless)" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
