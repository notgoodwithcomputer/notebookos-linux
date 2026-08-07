#!/usr/bin/env python3
"""
design_tokens — the ONE definition of Notebook OS's colours, type, radii and
spacing, plus a checker that finds every place an app has drifted off them.

WHY THIS EXISTS. Measured across the 58 modules in de/:

    255 distinct colours        (14 carry most of the OS; 108 of the rest are
                                 near-duplicates of those 14, and clusters like
                                 #3A362E/#3A362F or
                                 #B12D19/#B12C18/#A82A18/#A82B18 are the same
                                 colour typed slightly differently)
     34 distinct font sizes     (including 10.5, 11.5, 12.5, 13.5, 14.5 --
                                 half-pixel steps nobody chose as a system)
     11 distinct border radii   (with 220 uses of the old 2px square corner)

That last number is the important one. Every app's CSS loads at APPLICATION
priority, which BEATS the theme, so the OS-wide geometry the Papertone theme
sets is overridden in 220 places. The interface therefore has rounded controls
in the widgets an app did not style and square ones everywhere it did -- in the
same window. No single file looks wrong; the SYSTEM does.

WHAT THIS IS NOT. It is not a repaint. Every token below is drawn from what the
OS already uses most, so conforming to them changes almost nothing about the
design's intent and everything about its consistency. Colour tokens are exactly
the palette Papertone already declares; the type ramp keeps every integer size
that is already in heavy use and drops only the half-steps and one-offs.

    python3 tools/design_tokens.py                 # report drift, all files
    python3 tools/design_tokens.py finder.py ...   # report drift, some files
    python3 tools/design_tokens.py --strict        # exit 1 if anything drifted

The checker SUGGESTS a token for every off-scale value (nearest by colour
distance, or nearest step on a ramp) but deliberately does not rewrite anything:
which radius a given element wants is a judgement about what the element IS -- a
chip, a control, a card -- and that has to be read, not pattern-matched.
"""
import argparse
import collections
import glob
import os
import re
import sys

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")

# --------------------------------------------------------------------- colour
# The papertone palette, byte-identical to the @define-color names in
# usr/share/themes/Papertone/gtk-3.0/gtk.css. An app should be able to say
# "@ink" and mean it; until GTK lets app CSS see the theme's colour names in
# every context, these are the literals that are allowed to appear.
COLOURS = {
    "#FCFBF8": "paper      page / card surface",
    "#F8F7F2": "paper-2    a raised control on paper",
    "#F4F2EC": "panel      window furniture (menu bar)",
    "#F1EEE6": "rail-2     hover on furniture",
    "#EFEBE0": "rail       sidebars, side panels",
    "#EAE3D2": "select     the one selection tone",
    "#DED4C2": "field      the desktop backdrop / an empty track",
    "#D7D2C5": "hairlt     a seam INSIDE a panel",
    "#C9C4B6": "hair       a border BETWEEN surfaces",
    "#B3AD9E": "hair-firm  a border that must read as an edge",
    "#9A9484": "muted-2    placeholder text, disabled marks",
    "#8A857A": "muted-3    a quiet caption",
    "#6E695E": "muted      secondary text",
    "#3A362E": "ink-3      a heavy neutral (terminal chrome)",
    "#2A2620": "ink-2      body text in a document",
    "#1A1916": "ink        text, and the primary action's slab",
    "#C8341E": "accent     THIS DESTROYS SOMETHING, or: this is on",
    "#B12D19": "accent-dk  the accent's pressed / border state",
    # Added after the first conformance pass reported it from three files at
    # once (nbpicker, settings, installer): "the signage-red action, not
    # available yet". The palette had no desaturated accent, so a disabled
    # primary had nowhere on-token to go -- and it cannot fall back to a neutral,
    # because those rules also set `color: #FCFBF8` and a pale neutral would
    # render the label invisible. A gap found by three independent files is a
    # missing token, not drift.
    "#E0B8B0": "accent-dim the accent, disabled",
    # The palest accent wash: the hover state of a destructive control, and the
    # ground of a danger callout. Reported from two files independently.
    "#FBEFEC": "accent-wash a destructive control's hover ground",
    # THE ONE GREEN. Measured: 18 uses across NINE modules (academics, cookbook,
    # illustrator, journal, novel, screenplay, sequencer, terminal, writer) as
    # the "Saved" / "up to date" dot, paired with @accent for "not saved". Two
    # separate conformance agents flagged it from different files and both
    # correctly refused to change it: the palette is entirely warm neutrals plus
    # one red, so there is nothing on-palette that can say "this went well", and
    # conforming it in some files but not others would have fragmented the one
    # state signal the OS shares across nine apps. A colour that nine modules
    # agree on is a token that was never written down, not drift.
    "#7FA98C": "ok         saved / up to date",
    # ok-dk. The green family has TWO members for the same reason the red does
    # (accent / accent-dk): @ok is a pale ambient dot, and a pale green used as
    # a FILL -- Language's progress bars, its strength pips, its correct-answer
    # button -- is washed out to the point of not reading as a state at all.
    # Deep green fills, pale green dots, one hue.
    #
    # This is the honest form of "one green". Collapsing all three shades to a
    # single literal would have washed out six fills in Language AND rewritten
    # one of the six user-facing label colours in the Finder's Label menu, which
    # is an identity a person chooses, not a status. #2E7D4F (gbahelp, 1 use)
    # is the genuinely stray third shade and folds in here.
    "#4F7A3A": "ok-dk      a filled positive state",
    # The row-hover step. Sits between @rail (the sidebar ground) and @select,
    # because conforming hover onto the palette had collapsed hover and
    # selection into the same fill in six sidebars -- leaving selection carried
    # only by its accent bar. Light enough to read as "the pointer is here",
    # dark enough to be visible on rail.
    "#F0EADC": "hover      a row under the pointer",
    # The pale outline of an ENABLED destructive control -- distinct from
    # accent-dim, which means the action is unavailable. Reported independently
    # from accounting (#E7C7C1), gbaemu (#E4C7C0) and academics (#E2C3BB), all
    # within d<=8 of each other: the same three-files-agree signature that
    # produced accent-dim and ok.
    "#E4C7C0": "accent-edge an enabled destructive control's outline",
}
# Files whose colour LITERALS are machinery rather than styling, and where
# "conforming" them would break something real.
#
# nbapp.py is the whole list, and it earns it: every hex value in it is either
# prose in a comment about contrast ratios, or part of the HIGH-CONTRAST
# substitution tables (_HC_TEXT / _HC_LINE). Those tables' VALUES were chosen
# for measured WCAG ratios (7.05:1, 9.52:1, 3.01:1) and their KEYS deliberately
# enumerate the drift tones apps use, so that high-contrast mode can catch them.
# Snapping either to the palette would silently destroy the feature. Checked:
# 17 of 17 flagged colours in that file were false positives.
COLOUR_MACHINERY_FILES = {
    "nbapp.py": "high-contrast substitution tables; values are measured WCAG",
}

# Colours allowed to exist outside the palette because they carry MEANING the
# palette cannot: a drawing app needs a spectrum, a language course needs to be
# told apart from another at a glance, a chart needs series that differ. In
# these files a SATURATED value is left alone; a near-neutral that is six units
# from @hair is still drift and still reported.
SEMANTIC_FILES = {
    "illustrator.py": "the colour picker IS a spectrum",
    "writer.py": "document text + highlighter swatches, and a link blue",
    "language.py": "per-course identity badges",
    "gbasdk.py": "sprite / tile editor palettes",
    "nbicons.py": "icon call sites pass their own ink",
    "sequencer.py": "per-track meters and states",
    "accounting.py": "chart series",
    "sysmon.py": "load meters",
    "g2048.py": "tile values are a colour ramp",
}

# ----------------------------------------------------------------------- type
# Every integer size already in heavy use is KEPT (13 is the body size with 160
# uses, 14 has 126, 12 has 122, 11 has 92, 15 has 57). What goes is the
# half-pixel drift -- 10.5, 11.5, 12.5, 13.5, 14.5 account for 106 uses between
# them and no two of them mean anything different from their neighbours -- and
# the long tail of one-off display sizes.
#
# FINE AT THE BOTTOM, COARSE AT THE TOP, and that asymmetry is the whole idea.
# Between 10 and 17px a single pixel visibly changes how a label sits against
# the one beside it, and this interface does most of its work in that band --
# so every integer with real repeated use is kept (13 has 160 uses, 14 has 126,
# 12 has 122, 11 has 92, 15 has 57, 16 and 17 have 26 each, 10 has 29).
# Above 17 a pixel is invisible and the drift is obvious instead: 18/19/20/21/22
# were five heading sizes doing one job, and 23/25/26/27 four more. Those
# collapse to 20 and 24.
#
# An earlier version of this list excluded 16 and 10 while claiming to "keep
# every integer in heavy use" -- it did not, and the checker then reported 55
# perfectly ordinary labels as drift. A scale that argues with its own stated
# rule teaches people to ignore it.
TYPE_SCALE = [10, 11, 12, 13, 14, 15, 16, 17, 20, 24, 30]

# ...and ABOVE 30px the scale stops governing, on purpose.
#
# Those are hero sizes: the lock screen's clock (64), the 2048 title (60), a
# game-over overlay (44), an account balance (34). There is exactly ONE of them
# per screen, so no two are ever seen together and there is no system for them
# to be inconsistent WITH. "Conforming" a 64px clock to 52 would not remove
# drift, it would redesign the lock screen -- the thing this pass explicitly
# promises not to do. (34px is also used identically in three separate files,
# which makes it a token nobody wrote down rather than an accident.)
#
# The band that genuinely needed this was 18-27: eight distinct sizes doing the
# work of two, and unlike the hero sizes, a card heading and a section heading
# DO appear side by side.
TYPE_DISPLAY_MIN = 31

# --------------------------------------------------------------------- radius
#   0    square by intent (a table cell, a full-bleed band)
#   4    small marks that sit inside text: a chip, a check box, a badge
#   6    a compact ROW inside a container: a menu item, a list row. Its own
#        step because a row set at the control radius (8) nearly touches the
#        inner curve of the 12 container holding it, and set at 4 it reads
#        tighter than the marks beside it. The theme uses it for `menuitem`.
#   8    CONTROLS: buttons, entries, spin boxes, toggles, tabs
#  12    CONTAINERS: cards, dialogs, popovers, grouped lists
# 100    things that are conceptually round: pills, switches, slider knobs
RADIUS_SCALE = [0, 4, 6, 8, 12, 100]

# -------------------------------------------------------------------- spacing
# A 4px grid. Not enforced as hard as the others -- padding is where a design
# legitimately breathes differently per context -- but reported so the outliers
# (7px, 13px, 26px) can be seen.
SPACING_STEP = 4

# ----------------------------------------------------------------------- grid
# docs/PAPER-PHYSICS.md §E3 — the layout grid, normative. The unit is
# SPACING_STEP. Two things here are deliberately NOT multiples of it:
# HAIRLINE, because a rule is drawn ON a boundary rather than occupying the
# field, and the RENDERED heights of bordered controls, because the grid
# governs the INTERIOR box — a 1px border pair is drawn outside it, so a
# 28px interior renders 30. That is why the most-used heights in the OS are
# all ≡ 2 (mod 4): they were on-grid all along.
# de/nbapp.py re-exports the runtime subset (tools/ does not ship on the
# image); tools/grid_check.py holds the copies in lockstep.
GRID_UNIT = SPACING_STEP
LADDER_INTERIOR = [20, 24, 28, 32, 36]   # bordered controls: interior heights
LADDER_RENDERED = [22, 26, 30, 34, 38]   # = interior + the 1px border pair
LADDER_OPEN = [24, 28, 32, 36, 40]       # unbordered controls sit ON the grid
# Above the control band the general rule applies instead of named steps:
# interior on the 4u grid — so a compound row is ≡0 (open) or ≡2 (bordered)
# mod 4. A 76px sequencer lane (19u) and a 66px calculator key (interior 16u)
# both conform; a 45px anything does not.
LINE = 20              # body-text line at 13px; groups: 12 within, 24 between
MARGIN = 24            # window edge -> content
RAIL = 240             # THE sidebar width, OS-wide (was 210/212/240/252)
GUTTER = 24            # rail <-> field
HAIRLINE = 1           # drawn on the boundary it separates
MEASURE_READ = 640     # maximum prose measure
MEASURE_FORM = 1040    # maximum form measure (adopted from settings.MAX_W)
PANEL_H = 46           # the strut shell.py reserves; NOT 28, whatever old
                       # notes said — grid_check asserts every copy agrees
THIRD_PANE_MIN_W = 1366  # below this the middle pane collapses into the rail
# Deliberate rail exceptions — each must still fit a 1024 window minimum.
# module name -> width, with the reason on the same line.
RAIL_EXCEPTIONS = {
    "illustrator": 252,  # tool dock: set the window minimum once, clipped in
                         # CJK at 240-equivalent; widening history says keep 252
}


def canvas_h(screen_h):
    """The real vertical layout budget: what the strut leaves. 722 at 768."""
    return screen_h - PANEL_H


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _sat(h):
    r, g, b = _rgb(h)
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def nearest_colour(hexval):
    """(token, distance) for the palette colour closest to `hexval`."""
    target = _rgb(hexval)
    best, bestd = None, 1e9
    for tok in COLOURS:
        d = _dist(target, _rgb(tok))
        if d < bestd:
            best, bestd = tok, d
    return best, bestd


def nearest_step(value, scale):
    return min(scale, key=lambda s: abs(s - value))


def scan(path):
    """Return the drift found in one file."""
    src = open(path, encoding="utf-8", errors="replace").read()
    name = os.path.basename(path)
    semantic = name in SEMANTIC_FILES

    out = {"colour": [], "type": [], "radius": [], "spacing": [],
           "semantic": semantic,
           "machinery": name in COLOUR_MACHINERY_FILES}

    lines = src.split("\n")
    for m in re.finditer(r"#[0-9A-Fa-f]{6}\b", src):
        h = m.group(0).upper()
        if h in COLOURS:
            continue
        if out["machinery"]:
            continue
        line = src.count("\n", 0, m.start()) + 1
        # A colour NAMED IN PROSE is not drift. Comments in this codebase
        # routinely cite tones while explaining a decision ("#9A9484 is 2.92:1",
        # "the old toggle's make-it-#000000"), and flagging those trains the
        # reader to skim past the checker -- which is how a real drift value
        # gets waved through.
        if lines[line - 1].lstrip().startswith("#"):
            continue
        tok, d = nearest_colour(h)
        # A saturated colour in a file that legitimately needs colour is not
        # drift; a near-neutral that is 6 units from @hair always is.
        if semantic and _sat(h) > 0.35:
            continue
        out["colour"].append((line, h, tok, round(d)))

    for m in re.finditer(r"font-size: *([0-9.]+)px", src):
        v = float(m.group(1))
        if v in TYPE_SCALE:
            continue
        if v >= TYPE_DISPLAY_MIN:
            continue          # hero display type; see TYPE_DISPLAY_MIN
        line = src.count("\n", 0, m.start()) + 1
        out["type"].append((line, v, nearest_step(v, TYPE_SCALE)))

    # The WHOLE declaration, not just its first length. CSS allows up to four
    # corner values ("border-radius: 2px 0 0 2px" for a segmented control), and
    # a regex that stopped at the first number reported such a rule as clean
    # while three square corners survived in it. Found by an agent conforming
    # the Finder's view-switcher, which is exactly where that form is used.
    for m in re.finditer(r"border-radius: *([^;\n}]+)", src):
        decl = m.group(1)
        if "%" in decl:
            continue
        vals = [int(x) for x in re.findall(r"([0-9]+)px", decl)]
        bad = [v for v in vals if v not in RADIUS_SCALE]
        if not bad:
            continue
        v = bad[0]
        line = src.count("\n", 0, m.start()) + 1
        # NO nearest-step suggestion for radius, deliberately. "Nearest" is
        # actively misleading here: a 2px corner on a BUTTON is nearest to 0
        # and belongs at 8, and a 2px corner on a full-bleed band is nearest
        # to 0 and belongs there. The right value depends on what the element
        # IS, which has to be read from the selector, so the checker reports
        # the drift and states the scale rather than guessing.
        out["radius"].append((line, v, None))

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when anything is off-token")
    ap.add_argument("--summary", action="store_true",
                    help="counts per file only")
    args = ap.parse_args()

    paths = ([os.path.join(DE, f) for f in args.files] if args.files
             else sorted(glob.glob(os.path.join(DE, "*.py"))))

    totals = collections.Counter()
    per_file = []
    for p in paths:
        if not os.path.exists(p):
            print("missing: %s" % p)
            continue
        r = scan(p)
        n = len(r["colour"]) + len(r["type"]) + len(r["radius"])
        if not n:
            continue
        per_file.append((n, os.path.basename(p), r))
        totals["colour"] += len(r["colour"])
        totals["type"] += len(r["type"])
        totals["radius"] += len(r["radius"])

    per_file.sort(reverse=True)
    for n, name, r in per_file:
        tag = "  [semantic colours exempt]" if r["semantic"] else ""
        print("\n=== %s — %d off-token%s" % (name, n, tag))
        if args.summary:
            print("    colour %d   type %d   radius %d"
                  % (len(r["colour"]), len(r["type"]), len(r["radius"])))
            continue
        for line, h, tok, d in r["colour"][:60]:
            print("    L%-5d colour %s -> %s  (%s, d=%d)"
                  % (line, h, tok, COLOURS[tok].split()[0], d))
        for line, v, s in r["type"][:60]:
            print("    L%-5d font-size %spx -> %dpx" % (line, v, s))
        for line, v, _s in r["radius"][:60]:
            print("    L%-5d radius %dpx -> choose from %s by element type"
                  % (line, v, RADIUS_SCALE))

    print("\n%s" % ("-" * 58))
    print("TOTAL off-token:  colour %d   font-size %d   radius %d"
          % (totals["colour"], totals["type"], totals["radius"]))
    print("files with drift: %d" % len(per_file))
    if args.strict and sum(totals.values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
