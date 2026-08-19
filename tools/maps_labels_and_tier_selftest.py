#!/usr/bin/env python3
"""Maps: the zoom-tier parser skip and the street-label pass.

Two pieces of this app carry real risk and this gate exists to hold them:

  THE TIER SKIP is a speed optimisation that WORKS BY NOT DECODING THINGS. It
  steps over a hidden feature's geometry by counting varint terminator bytes,
  so an off-by-one in that scan does not fail loudly — it desynchronises the
  reader and every later feature in the cell comes out as garbage geometry, or
  silently vanishes. So the test is equivalence, not smoke: a tier decode must
  equal the full decode filtered to the same categories, feature for feature and
  point for point.

  THE STREET LABELS depend on joining the ways that share a name. Checking that
  "some text was drawn" would pass on the broken version this replaced, which
  labelled alleys and left every arterial bare, so the test asserts on the
  ARTERIALS BY NAME and on how many distinct streets get named.

Runs against the bundled Monaco pack, so it needs nothing that is not in the
tree. Both parts are red-provable: see PROVE-RED at the bottom.
"""

import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
PACK = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/maps/monaco.nbm2"

# Pango/PangoCairo must be the real thing (labels are what is under test); the
# widget toolkit around them is not needed to render to an ImageSurface.
import gi                                                    # noqa: E402
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo                  # noqa: E402,F401


class _Stub:
    def __getattr__(self, k):
        return _Stub()

    def __call__(self, *a, **k):
        return _Stub()


_repo = sys.modules["gi.repository"]
for _n in ("Gtk", "Gdk", "GLib"):
    if not hasattr(_repo, _n):
        setattr(_repo, _n, _Stub())
_nbapp = types.ModuleType("nbapp")
_nbapp.AppWindow = type("AppWindow", (), {"__init__": lambda self, *a, **k: None})
_nbapp.run = lambda *a, **k: None
sys.modules["nbapp"] = _nbapp
sys.modules["nbicons"] = types.ModuleType("nbicons")
_i18n = types.ModuleType("nbi18n")
_i18n._t = lambda s, *a: s
sys.modules["nbi18n"] = _i18n
_mot = types.ModuleType("nbmotion")
_mot.animate = lambda *a, **k: None
sys.modules["nbmotion"] = _mot
sys.path.insert(0, str(DE))
import maps                                                  # noqa: E402

checks = []


def check(ok, name):
    checks.append((bool(ok), name))


# ---------------------------------------------------------------- tier skip
pack = maps.NBM2(str(PACK))
key = next(iter(pack.dir))
full = pack.cell(key[0], key[1], None)
check(len(full) > 1000, "the fixture cell is big enough to be worth testing")

for tier, hidden in enumerate(maps._TIER_HIDDEN):
    got = pack.cell(key[0], key[1], tier)
    want = [f for f in full if f[0] not in hidden]
    same = len(got) == len(want) and all(
        a[0] == b[0] and a[1] == b[1] and a[2] == b[2]
        and a[3] == b[3] and a[4] == b[4] for a, b in zip(got, want))
    check(same, "tier %d decode equals the full decode minus %s (%d feats)"
          % (tier, sorted(hidden) or "nothing", len(want)))

# The skip must actually skip something, or the equivalence above is vacuous.
check(len(pack.cell(key[0], key[1], 0)) < len(full) // 2,
      "the lowest tier really does drop most of the cell")
# Tiers must not share a cache entry.
check(pack.cell(key[0], key[1], 0) is not pack.cell(key[0], key[1], 4),
      "the cell cache is keyed by tier, not just by cell")

# Zooming back OUT must not re-read a cell already in hand. Proved by taking
# the file handle away: if the reader reaches for the pack, cell() swallows the
# error and returns nothing, so a wrong answer here cannot look right.
probe = maps.NBM2(str(PACK))
deep = probe.cell(key[0], key[1], len(maps._TIER_HIDDEN) - 1)
probe.f = None
back_out = probe.cell(key[0], key[1], 1)
want = [f for f in deep if f[0] not in maps._TIER_HIDDEN[1]]
check(len(back_out) == len(want) and len(want) > 0
      and all(a[3] == b[3] for a, b in zip(back_out, want)),
      "zooming out is served by filtering the cell already decoded, not re-read")

# A pack the size of the one that ships must OPEN. This constant was set below
# the bundled continent, so Maps refused North America outright.
check(maps.NBM2.MAX_DIRECTORY_CELLS >= 300000,
      "the directory limit clears the 272,226-cell continent pack")

# ------------------------------------------------------------ joining ways
# One street cut into three ways, handed over out of order and with the middle
# one reversed -- which is exactly how OSM stores a street.
a = (1, 0, "Rue Test", [0, 0, 0, 100], (0, 0, 0, 100))
b = (1, 0, "Rue Test", [0, 300, 0, 100], (0, 100, 0, 300))     # reversed
c = (1, 0, "Rue Test", [0, 300, 0, 600], (0, 300, 0, 600))
joined = maps._join_ways([c, a, b])
check(len(joined) == 1, "three ways sharing endpoints join into one line")
check(joined and len(joined[0]) == 8,
      "the joined line keeps every point once (no duplicated junctions)")
check(joined and {joined[0][1], joined[0][-1]} == {0, 600},
      "the joined line runs end to end")
apart = maps._join_ways([a, (1, 0, "Rue Test", [0, 9000, 0, 9500],
                             (0, 9000, 0, 9500))])
check(len(apart) == 2, "ways that do not touch are NOT joined")
check(maps._join_ways([(1, 0, "x", [], (0, 0, 0, 0))]) == [],
      "a feature with no points is dropped, not indexed into")

# ------------------------------------------------------------ straight runs
line = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (150.0, 0.0)]
run = maps._straight_run(line)
check(run and abs(run[2] - 150.0) < 0.01, "a straight line yields its whole length")
ell = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
run = maps._straight_run(ell)
check(run and abs(run[2] - 100.0) < 0.01,
      "an L-shape yields one arm, not the diagonal across the corner")
check(maps._straight_run([(0.0, 0.0)]) is None, "a single point has no run")

# ------------------------------------- a street too short to name, in pieces
# THE DEFECT THIS GATE EXISTS FOR. A downtown street arrives as one way per
# block -- in Chicago the median named way is 69 px long while its own name
# sets at ~120 px -- so labelling per feature names nothing but alleys. Monaco,
# where the ways are long, CANNOT SEE THAT BUG: this fixture is a street cut
# into eight 40 px pieces, which no piece can carry and the whole can.
import cairo                                                 # noqa: E402

BLOCKS = 8
STEP = 250              # quantised units; 0.0025 deg = 40 px at scale 16000
LONG_NAME = "Very Long Boulevard Name"
pieces = [(1, 0, LONG_NAME, [0, i * STEP, 0, (i + 1) * STEP],
           (0, i * STEP, 0, (i + 1) * STEP)) for i in range(BLOCKS)]


def label_run(feats):
    got = []
    real_layout = maps._layout

    def spy(cr, text, size, bold=False):
        got.append(text)
        return real_layout(cr, text, size, bold)

    maps._layout = spy
    fake = maps.Maps.__new__(maps.Maps)
    fake.pack = types.SimpleNamespace(quant=100000)
    fake.scale = 16000.0
    fake.cx = BLOCKS * STEP / 2 / 100000.0
    fake.cy = 0.0
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 1024, 700)
    try:
        maps.Maps._draw_named_labels(fake, cairo.Context(surf), list(feats),
                                     1024, 700, [])
    finally:
        maps._layout = real_layout
    return got


check(LONG_NAME in label_run(pieces),
      "a street cut into blocks too short to carry its name is still named")
one_piece = [(1, 0, "Solo Street", [0, 0, 0, STEP], (0, 0, 0, STEP))]
check("Solo Street" not in label_run(one_piece),
      "a genuinely short stub is NOT named (the join is not a licence to guess)")

# ------------------------------------------------------------- real labels
drawn = []
_real_layout = maps._layout


def spy_layout(cr, text, size, bold=False):
    drawn.append(text)
    return _real_layout(cr, text, size, bold)


maps._layout = spy_layout
mp = maps.Maps.__new__(maps.Maps)
mp.pack = pack
mp.scale = 60000.0
mp.cx, mp.cy = maps._merc(43.7384, 7.4246)
mp._surface = None
mp._surf_size = mp._surf_scale = None
mp._surf_cx = mp._surf_cy = 0.0
maps.Maps._render_surface(mp, 1024, 700)
maps._layout = _real_layout
names = [t for t in drawn if t]
check(len(set(names)) >= 12,
      "a Monaco street view names at least a dozen distinct streets (got %d)"
      % len(set(names)))
# The named things must be STREETS, not just the place labels that already
# worked -- the defect this fixes drew place names and nothing else.
streets = [n for n in set(names)
           if n.split(" ")[0] in ("Avenue", "Boulevard", "Rue", "Quai",
                                  "Allée", "Promenade", "Route", "Chemin",
                                  "Place", "Voie", "Descente", "Montée")]
check(len(streets) >= 8,
      "at least eight of them are streets by name (got %d: %s)"
      % (len(streets), sorted(streets)[:5]))
check(len(names) <= maps.MAX_ROAD_LABELS + 40,
      "the label pass stays inside its bound (%d layouts)" % len(names))

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
passed = sum(ok for ok, _ in checks)
print("RESULT: %d checks, %s (%d/%d)"
      % (len(checks), "ALL PASS" if passed == len(checks) else "FAILED",
         passed, len(checks)))

# PROVE-RED (run by hand after touching either mechanism), both confirmed when
# this gate was written:
#   1. in maps.py _parse, change the skip loop's `if raw[i] < 0x80` to
#      `<= 0x80`. Tiers 0-3 go red; the label checks stay green.
#   2. in _draw_named_labels, replace `_join_ways(fs)` with `[f[3] for f in fs]`.
#      The blocks-too-short check goes red.
# Sabotage 2 was ALSO run against the Monaco assertions alone, and they stayed
# GREEN -- Monaco's ways are long enough to carry their own names, so a real
# pack is not by itself proof of anything here. That is why the synthetic
# eight-block street exists.
raise SystemExit(passed != len(checks))
