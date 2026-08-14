#!/usr/bin/env python3
"""screenplay_zine_selftest — does the printed script paginate like a script?

    python3 tools/screenplay_zine_selftest.py

Screenplay could already impose a saddle-stitch zine; what it could not do was
BREAK PAGES the way a script breaks them. It cut the laid-out lines every N and
called the result a page, which strands a slugline at the foot of a sheet, tears
a character's name off the speech under it, and splits a speech with nothing to
say it continues. Those are the rules that make a printed page read as a
screenplay rather than as monospace prose, so they are what this checks.

`paginate_script()` is a module-level pure function over row tuples — no GTK, no
cairo, no page — so the rules can be checked exactly rather than inferred from a
rendered image. The element geometry is checked against the standard 60-column
script measure, and the page COUNTER is checked against the same paginator the
PDF uses, because those two computing pages separately is a defect this app has
already had once.

Every family ends with a MUTANT that must go RED.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="spzine-"))

import screenplay as S                                        # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def mutant(name, ok_when_broken):
    CHECKS[0] += 1
    caught = not ok_when_broken
    print("%-4s MUTANT %s%s" % ("ok" if caught else "FAIL", name,
                                "" if caught else
                                "   -> sabotage went UNDETECTED"))
    if not caught:
        FAILS.append("MUTANT " + name)


def cue_col():
    return S.PDF_ELEMENT[S.EL_CUE][0]


def dlg_col():
    return S.PDF_ELEMENT[S.EL_DIALOGUE][0]


def speech(name, n, start=1):
    return [S._row(dlg_col(), "line %d" % i, False, S.EL_DIALOGUE, name)
            for i in range(start, start + n)]


def texts(page):
    return [r[1] for r in page if r is not None]


# ===================================== 1. the element grid is a script's grid
print("--- 1. standard script geometry ----------------------------------")

# The whole point of "script formatting" is that the indents are FIXED in a
# 60-column measure. Scaled onto another page size they must keep their order
# and their proportions, or the page stops being recognisable as a script.
scene = S.PDF_ELEMENT[S.EL_SCENE]
action = S.PDF_ELEMENT[S.EL_ACTION]
cue = S.PDF_ELEMENT[S.EL_CUE]
dlg = S.PDF_ELEMENT[S.EL_DIALOGUE]
paren = S.PDF_ELEMENT[S.EL_PAREN]
trans = S.PDF_ELEMENT[S.EL_TRANSITION]

check("a scene heading and its action share one measure",
      scene[0] == action[0] == 0 and scene[1] == action[1],
      (scene, action))
check("the dialogue block is set in from the left margin",
      dlg[0] > 0, dlg)
check("the character cue sits further in than its dialogue",
      cue[0] > dlg[0], (cue[0], dlg[0]))
check("the parenthetical sits between dialogue and cue",
      dlg[0] < paren[0] < cue[0], (dlg[0], paren[0], cue[0]))
check("dialogue is narrower than the full measure",
      dlg[1] < scene[1], (dlg[1], scene[1]))
check("a transition is flush right", trans[3] is True)
check("only the shouted elements are upper-cased",
      (scene[2], cue[2], trans[2]) == (True, True, True)
      and (action[2], dlg[2], paren[2]) == (False, False, False))
# Proportions against the real thing: cue 3.7in, paren 3.1in, dialogue 2.5in
# from the paper edge, against a 1.5in left margin, at ten characters an inch.
for label, got, want in (("cue", cue[0], S._std(22)),
                         ("parenthetical", paren[0], S._std(16)),
                         ("dialogue", dlg[0], S._std(10))):
    check("the %s indent is the standard one, scaled" % label, got == want,
          (got, want))
check("no element overflows the page measure",
      all(v[0] + v[1] <= S.PDF_COLS for v in S.PDF_ELEMENT.values()),
      {k: v for k, v in S.PDF_ELEMENT.items() if v[0] + v[1] > S.PDF_COLS})

def geometry_is_a_script(table):
    """The ordering that makes a page read as a script, as a predicate over a
    table — so a BROKEN table can be fed to the same rule the real one passes.
    Written first as `cue[0] > dlg[0]` evaluated on the live table, which is a
    tautology dressed as a mutant: it re-checked the real values and could never
    go red."""
    return (table[S.EL_DIALOGUE][0] < table[S.EL_PAREN][0]
            < table[S.EL_CUE][0]
            and table[S.EL_SCENE][1] == table[S.EL_ACTION][1]
            and all(v[0] + v[1] <= S.PDF_COLS for v in table.values()))


check("the live element table is a script's geometry",
      geometry_is_a_script(S.PDF_ELEMENT))
_flat = dict(S.PDF_ELEMENT)
_flat[S.EL_CUE] = (0, S.PDF_COLS, True, False)      # cue back at the margin
mutant("a cue indented less than its own dialogue",
       geometry_is_a_script(_flat))
_wide = dict(S.PDF_ELEMENT)
_wide[S.EL_ACTION] = (0, S.PDF_COLS - 2, False, False)
mutant("a scene heading and its action on different measures",
       geometry_is_a_script(_wide))


# ============================================ 2. a speech broken by a page
print("\n--- 2. a speech broken by a page says so --------------------------")

rows = ([S._row(cue_col(), "MARA", False, S.EL_CUE)] + speech("MARA", 10))
pages = S.paginate_script(rows, 6)
check("the speech spills onto a second page", len(pages) >= 2, len(pages))
check("the first page closes with (MORE)",
      texts(pages[0])[-1] == S.MORE_MARK, texts(pages[0])[-1:])
check("the continuation opens with the speaker and (CONT'D)",
      texts(pages[1])[0] == "MARA (CONT'D)", texts(pages[1])[:1])
check("(MORE) is set at the cue indent, not the dialogue indent",
      pages[0][-1][0] == cue_col(), (pages[0][-1][0], cue_col()))
check("no page runs over its line budget",
      all(len(p) <= 6 for p in pages), [len(p) for p in pages])
check("not one line of the speech is lost to the break",
      sum(1 for p in pages for t in texts(p) if t.startswith("line ")) == 10,
      sum(1 for p in pages for t in texts(p) if t.startswith("line ")))

# A break BETWEEN two speakers is a paragraph break, not a split speech —
# marking it (MORE) would say Mara is still talking when Dev has started.
two = (speech("MARA", 5) + [None]
       + [S._row(cue_col(), "DEV", False, S.EL_CUE)] + speech("DEV", 5))
pages2 = S.paginate_script(two, 6)
check("a break between two speakers is NOT marked (MORE)",
      not any(S.MORE_MARK in t for p in pages2 for t in texts(p)),
      [t for p in pages2 for t in texts(p) if S.MORE_MARK in t])


def marks_a_split(fn):
    pg = fn(([S._row(cue_col(), "MARA", False, S.EL_CUE)]
             + speech("MARA", 10)), 6)
    flat = [t for p in pg for t in texts(p)]
    return S.MORE_MARK in flat and any("CONT'D" in t for t in flat)


check("the paginator marks a split speech", marks_a_split(S.paginate_script))
mutant("a paginator that just slices every N lines",
       marks_a_split(lambda r, n: [r[i:i + n] for i in range(0, len(r), n)]))


# ================================================ 3. widows and orphans
print("\n--- 3. nothing is stranded at the foot of a page ------------------")

# A slugline with its scene overleaf tells the reader nothing.
r = ([S._row(0, "action %d" % i, False, S.EL_ACTION) for i in range(5)]
     + [S._row(0, "INT. ROOM - DAY", False, S.EL_SCENE)]
     + [S._row(0, "she waits.", False, S.EL_ACTION)] * 4)
pg = S.paginate_script(r, 6)
check("a scene heading never ends a page",
      all(p[-1][3] != S.EL_SCENE for p in pg if p and p[-1]),
      [texts(p)[-1] for p in pg])

# A cue with its speech on the next sheet is worse.
r = ([S._row(0, "action %d" % i, False, S.EL_ACTION) for i in range(5)]
     + [S._row(cue_col(), "MARA", False, S.EL_CUE)] + speech("MARA", 4))
pg = S.paginate_script(r, 6)
check("a character cue never ends a page",
      all(p[-1][3] != S.EL_CUE or p[-1][1] == S.MORE_MARK
          for p in pg if p and p[-1]),
      [texts(p)[-1] for p in pg])
check("a cue keeps at least one line of its speech",
      all(not (len(p) >= 2 and p[-2] is not None and p[-2][3] == S.EL_CUE
               and p[-1] is not None and p[-1][3] == S.EL_DIALOGUE
               and len(p) == 6)
          for p in pg))

# A parenthetical is part of the speech beneath it.
r = ([S._row(0, "action %d" % i, False, S.EL_ACTION) for i in range(4)]
     + [S._row(cue_col(), "MARA", False, S.EL_CUE),
        S._row(S.PDF_ELEMENT[S.EL_PAREN][0], "(quietly)", False, S.EL_PAREN,
               "MARA")] + speech("MARA", 4))
pg = S.paginate_script(r, 6)
check("a parenthetical never ends a page",
      all(p[-1][3] != S.EL_PAREN for p in pg if p and p[-1]),
      [texts(p)[-1] for p in pg])

check("a page never opens on blank air",
      all(p[0] is not None for p in S.paginate_script(
          [None, None] + speech("MARA", 12) + [None, None], 5) if p))


def no_stranded_scene(fn):
    r2 = ([S._row(0, "a%d" % i, False, S.EL_ACTION) for i in range(5)]
          + [S._row(0, "INT. ROOM", False, S.EL_SCENE)]
          + [S._row(0, "b", False, S.EL_ACTION)] * 4)
    return all(p[-1][3] != S.EL_SCENE for p in fn(r2, 6) if p and p[-1])


mutant("a paginator that strands a slugline at the page foot",
       no_stranded_scene(lambda r, n: [r[i:i + n]
                                       for i in range(0, len(r), n)]))


# ================================ 4. the counter and the PDF agree, always
print("\n--- 4. the page counter is the printed page count ------------------")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")

LONG = " ".join("Sentence %d of a speech that will not stop." % i
                for i in range(1, 34))
CASES = {
    "empty": [],
    "one scene": [(0, "INT. ROOM - DAY"), (1, ""), (1, "She waits.")],
    "a speech over a page": [(0, "INT. ROOM - DAY"), (1, ""),
                             (2, "MARA"), (3, LONG), (1, ""),
                             (2, "DEV"), (3, "...oh.")],
}
app = S.Screenplay()
buf = app.body.get_buffer()
table = buf.get_tag_table()

for label, script in CASES.items():
    buf.set_text("")
    for idx, text in script:
        end = buf.get_end_iter()
        off = end.get_offset()
        buf.insert(end, text + "\n")
        start = buf.get_iter_at_offset(off)
        stop = buf.get_end_iter()
        stop.backward_char()
        tag = table.lookup(app.EL_TAGS[idx])
        if tag is not None:
            buf.apply_tag(tag, start, stop)
    pages, _lpp = app._page_rows()
    shown = app._page_total()
    printed, _draw = app._build_pages()
    # The bar counts body pages; the PDF adds the title page in front.
    check("%s: counter == paginator == PDF" % label,
          shown == len(pages) == printed - 1,
          (shown, len(pages), printed))

# The bug this guards is the two routes paginating SEPARATELY. Comparing their
# page counts on a fixture proves nothing on its own — measured, naive slicing
# and script pagination return the same count for every script tried, because
# the added (MORE)/(CONT'D) lines rarely cross a page boundary. So sabotage the
# SHARED paginator instead and require both routes to move together: a route
# that computed pages its own way would sit still while the other moved.
baseline = app._page_total()
_real_pag = S.paginate_script
S.paginate_script = lambda r, n: _real_pag(r, max(1, n // 3))
try:
    moved = app._page_total()
    coupled = (moved > baseline and moved == app._build_pages()[0] - 1)
    # ...and now break the coupling on purpose: one route frozen, one live.
    _real_total = S.Screenplay._page_total
    S.Screenplay._page_total = lambda self: baseline
    try:
        drifted = (app._page_total() != app._build_pages()[0] - 1)
    finally:
        S.Screenplay._page_total = _real_total
finally:
    S.paginate_script = _real_pag
check("both routes read the one shared paginator",
      coupled, (baseline, moved))
mutant("a counter that paginates on its own", not drifted)

app.destroy()

print("\n%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
if FAILS:
    print("RESULT: FAILED")
    for f in FAILS:
        print("   - %s" % f)
else:
    print("RESULT: ALL PASS")
sys.exit(len(FAILS))
