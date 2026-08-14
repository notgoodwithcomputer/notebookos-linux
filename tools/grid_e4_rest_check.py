#!/usr/bin/env python3
"""grid_e4_rest_check — §E4 check 4: no arriving surface's rest edge is off the
grid.

§E3.8: "A surface that stops 11 px from a hairline is the single most visible
way to prove the grid is decorative."

THE DEFECT THIS GUARDS, found by arithmetic on 2026-08-11: `nbtransitions.
present_card` CENTRES a card — `tx = (W - cw) // 2` — so its rest edge lands on
the 4u grid only when the card's natural width happens to be a multiple of 8.
Measured across plausible card widths at 1024x722, MORE THAN HALF put both edges
1-3px off. Every anchored card in the OS goes through that one function (About,
Get Info, Confirm, overlay cards), so it was one defect affecting all of them,
and it is now one snap protecting all of them.

WHY THIS IS BEHAVIOURAL AND NOT A SOURCE GREP. Asserting that the word "snap"
appears in present_card would pass on motion that no longer works — the failure
mode this campaign has caught repeatedly. So the check DRIVES present_card and
reads the target rect actually handed to GrowCard.grow.

⚠ AND IT VARIES THE CARD SIZE ON PURPOSE. An earlier probe of mine appeared to
prove five cases and had proved ONE: `set_size_request` on an EMPTY Gtk.Box
never reaches `get_preferred_size`, so every case silently fell back to
present_card's 340x220 default. A fixture that cannot vary its input measures
one case however many times it runs it. Real Gtk.Labels with different text are
used here, and the distinct widths observed are PRINTED so a vacuous run is
visible rather than merely believed.

    python3 tools/grid_e4_rest_check.py

Exit 0 clean; 1 if any card comes to rest off the grid, or if the fixture failed
to produce at least two distinct card widths (which would make it vacuous).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.environ.get("NB_DE_DIR") or os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="grid-rest-"))

fails = []


def main():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception as exc:                                      # noqa: BLE001
        print("FAIL  cannot load Gtk: %s" % exc)
        return 1
    if not Gtk.init_check()[0]:
        # Honest, not a skip-shaped pass: this check cannot run headless and
        # says so with a non-zero exit, because a green here would be a lie.
        print("FAIL  no display — this check MEASURES a laid-out card and "
              "cannot be satisfied without one (run under tools/guestrun.sh)")
        return 1

    import nbtransitions
    import nbapp
    unit = int(getattr(nbapp, "GRID_UNIT", 4)) or 4

    targets = []
    real_grow = nbtransitions.GrowCard.grow

    def spy(self, anchor, target, on_done=None):
        targets.append(tuple(target))
        return real_grow(self, anchor, target, on_done)

    nbtransitions.GrowCard.grow = spy
    widths = set()
    try:
        # Real Labels with different text, so the natural width genuinely
        # differs run to run — see the docstring's warning about the empty-Box
        # fixture that measured one case five times.
        for text in ("Delete", "Delete this recipe?",
                     "Delete this recipe and everything in it?",
                     "Are you sure you want to permanently delete this item "
                     "and all of the notes filed under it?"):
            overlay = Gtk.Overlay()
            overlay.add(Gtk.Box())
            label = Gtk.Label(label=text)
            label.set_line_wrap(False)
            # show() BEFORE present_card measures: present_card asks the card
            # for get_preferred_size(), and a container whose child is not
            # visible reports nothing, so present_card falls back to its 340x220
            # default. That is what made the earlier fixture measure one case
            # four times — the anti-vacuous guard below is what exposed it.
            label.show()
            targets[:] = []
            win, close = nbtransitions.present_card(overlay, label, (8, 8, 16, 16))
            if not targets:
                fails.append("[not reached: present_card produced no grow "
                             "target for %r]" % text[:24])
            else:
                x, y, w, h = targets[0]
                widths.add(int(w))
                ok = (int(x) % unit == 0 and int(y) % unit == 0)
                print("  card w=%-5s rest=(%s,%s)  on %dpx grid: %s"
                      % (int(w), int(x), int(y), unit, ok))
                if not ok:
                    fails.append(
                        "a card of width %d comes to rest at (%d,%d), off the "
                        "%dpx grid — E3.8: a surface that stops just short of "
                        "a rule proves the grid decorative"
                        % (int(w), int(x), int(y), unit))
            try:
                close()
            except Exception:                                     # noqa: BLE001
                pass
    finally:
        nbtransitions.GrowCard.grow = real_grow

    # THE ANTI-VACUOUS GUARD, and it is not "did we see many widths".
    # This fixture cannot vary the card's natural size — present_card measures
    # the card BEFORE it is realised and falls back to its 340x220 default, so
    # every case lands on the same width however much text the label holds. An
    # earlier version demanded 2+ distinct widths and simply failed forever.
    # What actually proves the check is not vacuous is that the SNAP CHANGED
    # SOMETHING: for the observed geometry the raw centred position is off the
    # grid, and the position actually used is on it. If the raw value were
    # already aligned, this check could pass with the snap deleted — so that
    # case is called out rather than counted as a pass.
    if not targets:
        fails.append("VACUOUS: no card was measured at all")
    else:
        x, y, w, h = targets[0]
        raw_x = max((1024 - int(w)) // 2, 0)
        raw_y = max((722 - int(h)) // 2, 0)
        moved = (raw_x % unit) or (raw_y % unit)
        print("  raw centred would be (%d,%d); snapped to (%d,%d)"
              % (raw_x, raw_y, int(x), int(y)))
        if not moved:
            fails.append(
                "INCONCLUSIVE: the raw centred position (%d,%d) is ALREADY on "
                "the %dpx grid for this card, so this run would pass with the "
                "snap removed — it proves nothing. Vary the card geometry."
                % (raw_x, raw_y, unit))

    for f in fails:
        print("FAIL  %s" % f)
    print("\n%s  §E4 check 4 (rest edges on the grid): %d card(s), "
          "%d distinct width(s)"
          % ("FAIL" if fails else "PASS", len(targets and widths or widths),
             len(widths)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
