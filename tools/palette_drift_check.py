#!/usr/bin/env python3
"""
palette_drift_check — colours that are ALMOST a palette token.

Papertone defines the OS palette once (@paper @panel @ink @hair @accent @muted
@select @rail @hairlt). Every app then writes its CSS as literal hex, and over
time those literals drift: #EFEBE0 where @rail is #F1EEE6, #E6DFCE where
@select is #EAE3D2. At a channel distance of a dozen out of 765 nobody can see
the difference side by side -- which is the point. A colour nobody can
distinguish from the palette is not a design decision, it is a typo that got
copied, and it means a screen quietly sits a shade off the rest of the OS.

Exact palette values and colours that are clearly their OWN thing are fine.
Only the near-misses are reported, closest first, because those are the ones
that were meant to be a palette colour and missed.

  python3 tools/palette_drift_check.py [--max-distance N] [--fail]

Exit 0 always, unless --fail is passed (then non-zero when any drift is found).
Advisory by default: some near-misses are deliberate (a hover tint one shade
off its base), so this informs a human rather than gating a build.
"""
import argparse
import collections
import glob
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
THEME = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/usr/share/"
                           "themes/Papertone/gtk-3.0/gtk.css")


def rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def palette():
    """The theme's own @define-color table — read, never restated here, so the
    check cannot drift from the palette it is checking against."""
    out = {}
    try:
        src = io.open(THEME, encoding="utf-8").read()
    except OSError:
        return out
    for m in re.finditer(r'@define-color\s+([A-Za-z0-9_-]+)\s+(#[0-9A-Fa-f]{3,6})\s*;', src):
        out[m.group(2).upper()] = m.group(1)
    return out


def app_colours():
    """Every literal hex inside an app's b\"\"\"CSS\"\"\" blob, and who uses it."""
    found = collections.defaultdict(set)
    for f in sorted(glob.glob(os.path.join(DE, "*.py"))):
        try:
            src = io.open(f, encoding="utf-8").read()
        except OSError:
            continue
        for blob in re.finditer(r'b"""(.*?)"""', src, re.S):
            for c in re.finditer(r'#[0-9A-Fa-f]{6}\b', blob.group(1)):
                found[c.group(0).upper()].add(os.path.basename(f))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-distance", type=int, default=12)
    ap.add_argument("--fail", action="store_true")
    a = ap.parse_args()

    pal = palette()
    if not pal:
        print("could not read the palette from %s" % THEME)
        return 2
    found = app_colours()

    drift = []
    for hexv, files in found.items():
        if hexv in pal:
            continue
        r = rgb(hexv)
        best = None
        for p, name in pal.items():
            d = sum(abs(x - y) for x, y in zip(r, rgb(p)))
            if 0 < d <= a.max_distance and (best is None or d < best[2]):
                best = (p, name, d)
        if best:
            drift.append((best[2], hexv, best[0], best[1], sorted(files)))
    drift.sort()

    print("palette: %d tokens · app CSS: %d distinct colours" % (len(pal), len(found)))
    if not drift:
        print("no near-miss colours within %d/765" % a.max_distance)
        print("RESULT: ALL PASS")
        return 0
    print("\n%d colour(s) within %d/765 of a palette token — indistinguishable "
          "by eye, so almost certainly meant to BE that token:\n"
          % (len(drift), a.max_distance))
    for d, hexv, p, name, files in drift:
        print("  %s -> %s (@%-7s) distance %-3d %s"
              % (hexv, p, name, d, ", ".join(files)))
    print("\nRESULT: %d DRIFTED" % len(drift))
    return 1 if a.fail else 0


if __name__ == "__main__":
    sys.exit(main())
