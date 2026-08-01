#!/usr/bin/env python3
"""toyfont_check — no user-facing text may be drawn with cairo's TOY font API.

    DISPLAY=:0 python3 tools/toyfont_check.py

THE BUG THIS EXISTS FOR. cairo's toy text API — cr.select_font_face() +
cr.show_text() / cr.text_path() / cr.text_extents() — binds ONE FreeType face
and does NO per-character fallback. Nimbus Sans (and Liberation, and "serif",
and "sans-serif") carry no CJK, no Devanagari and no Hebrew, so every string
drawn this way came out as .notdef for five of the seventeen shipped languages.
Measured on the guest font tree, for the seven weekday abbreviations the
Academics timetable paints across the top of the Schedule:

    ja 7/7 glyphs missing   zh 14/14   ko 7/7   hi 25/25   yi 25/25

and .notdef in these faces is INVISIBLE, not a box — so a Japanese student
opened their timetable to a blank strip where the days should be, with nothing
on screen to suggest anything had gone wrong. The same API drew their class
names, their recipes, their address book and their ledger exports.

WHY tofu_sweep.py CANNOT CATCH THIS, and why this file has to exist alongside
it: tofu_sweep asks "does SOME shipped face contain this character", which is
Pango's question. The answer was yes the whole time — NotoSansCJK and friends
ship and cover all of it — and it has nothing to do with which single face
show_text happened to bind. A green tofu_sweep is not evidence here.

THE FIX is PangoCairo, which picks a face per glyph: nbprint.PdfText for
paginated documents, PangoCairo.create_layout/show_layout for widget drawing.
Both resolve the same strings at 0 unknown glyphs.

PENDING lists the files a parallel sweep still owns. It is an ALLOWANCE, not a
target: shrinking it needs no change here, and a file that has been migrated can
never quietly go back.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# The toy API, as it is actually spelled at a call site.
TOY = re.compile(r"\b(?:cr|ctx|c)\.(?:show_text|text_path|text_extents"
                 r"|select_font_face|set_font_size)\s*\(")

# Files whose migration belongs to the parallel guest-divergence sweep. Listed
# by name so this check is useful today rather than only once everything lands.
PENDING = {
    # maps.py and screenplay.py were MIGRATED on 2026-07-30 -- removed from the
    # allowance so a regression reports BROKEN instead of quietly "pending".
    # Their toy calls had left the Maps empty state invisible and dropped every
    # line of an exported screenplay in ja/zh/ko/hi/yi.
    "settings.py", "video.py", "writer.py",
    "gbasdk.py", "nbmediakeys.py", "sequencer.py",
    # nbprint.py is the PangoCairo report engine itself; its remaining toy calls
    # are in the non-text ruling/label paths its own callers do not use for
    # user prose.
    "nbprint.py",
}


def offenders(path):
    """Line numbers of toy-API calls, ignoring comments and docstring prose."""
    out = []
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
        code = line.split("#", 1)[0]
        if TOY.search(code):
            out.append(i)
    return out


def main():
    if not os.path.isdir(DE):
        print("no DE tree at %s" % DE)
        return 1
    clean, pending, broken = [], [], []
    for name in sorted(os.listdir(DE)):
        if not name.endswith(".py"):
            continue
        hits = offenders(os.path.join(DE, name))
        if not hits:
            clean.append(name)
        elif name in PENDING:
            pending.append((name, hits))
        else:
            broken.append((name, hits))
    for name, hits in broken:
        print("TOY FONT API  %-16s lines %s" % (
            name, ", ".join(str(h) for h in hits[:12])))
    for name, hits in pending:
        print("pending (parallel sweep)  %-16s %d call%s"
              % (name, len(hits), "" if len(hits) == 1 else "s"))
    print("\n%s: %d files draw text only through Pango, %d pending, %d BROKEN"
          % ("clean" if not broken else "FAILED",
             len(clean), len(pending), len(broken)))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
