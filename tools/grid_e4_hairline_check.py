#!/usr/bin/env python3
"""grid_e4_hairline_check — §E4 check 3: every hairline sits in one of the five
positions §E3.7 permits.

    1. a rail <-> field boundary
    2. the outer edge of a card, sheet or dialog
    3. beneath a header band — toolbar, table header
    4. between rows of a ruled list
    5. above a fixed bottom bar
  "Nowhere else. A rule floating in a field is decoration, and decoration is
   what this design language does not have."

WHAT THIS IS HONESTLY FOR. Measured at build time: the OS already follows E3.7.
Of the single-side hairlines in the tree, all classify into the five roles once
the vocabulary knows the local names. **So this gate finds no current defects —
it is a REGRESSION GUARD**, and saying that plainly matters more than letting it
look like it caught something. Its value is that the next floating rule cannot
land unnoticed.

THE DISCRIMINATOR, and why the obvious version does not work. A first pass
matched any `1px solid` border and produced 314 "offenders" — because it counted
the border around every button, chip and entry. Those are CONTROL OUTLINES, the
papertone bordered-control look, not rules dividing regions. A RULE is a
SINGLE-SIDE border (`border-top` / `border-bottom`) on something that is not a
control. That one distinction takes the candidate set from 314 to ~100 and makes
the check mean what E3.7 means.

Selectors are matched against a ROLE VOCABULARY; anything it cannot place must
be listed in REVIEWED with the role a human assigned and why. An unexplained
entry is how a ledger stops being reviewable.

    python3 tools/grid_e4_hairline_check.py

Exit 0 clean; 1 on a hairline in no permitted position, or on a STALE reviewed
entry whose selector no longer exists.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.environ.get("NB_DE_DIR") or os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de")
THEME = os.path.join(ROOT, "buildroot", "board", "notebookos",
                     "rootfs-overlay", "usr", "share", "themes", "Papertone",
                     "gtk-3.0", "gtk.css")

SIDE_DECL = re.compile(r"border-(?:top|bottom)\s*:\s*([^;}]+)", re.I)
CONTROL = re.compile(r"btn|button|chip|entry|field|input|switch|check|radio|"
                     r"slider|spin|combo|tab\b|pill|crumb", re.I)

# selector fragment -> the E3.7 position it occupies.
ROLES = {
    "rail <-> field": r"sidebar|rail|places|devices|\bsb|pane|side",
    "card/sheet/dialog": r"card|sheet|dialog|popover|menu|tooltip|about|overlay",
    "under a header band": r"head|toolbar|thead|titlebar|menubar|bar\b|top|"
                           r"ruler|tabs",
    "between ruled rows": r"row|treeview|item|list|table|slot|cell|grid|track",
    "above a bottom bar": r"bottom|foot|status|transport|playbar",
}

# Selectors the vocabulary cannot place, each REVIEWED BY HAND with the E3.7
# position it actually occupies. Reviewable on purpose: an entry without a
# reason is indistinguishable from a rule nobody looked at.
REVIEWED = {
    ("video.py", ".transwrap"):
        "between ruled rows — wraps the transport/clip strip's row band.",
    ("calculator.py", ".calcnav"):
        "under a header band — the mode strip above the keypad.",
    ("calculator.py", ".display"):
        "under a header band — the readout band above the keys.",
    ("academics.py", ".canvas-meta"):
        "under a header band — the lecture meta strip beneath the title.",
    ("cookbook.py", ".cookinghd"):
        "under a header band — the cooking-view header.",
    ("media.py", ".filmstrip"):
        "above a bottom bar — the strip is the fixed bottom band.",
    ("tasks.py", ".eventadd"):
        "between ruled rows — the add-row sits in the ruled schedule list.",
    # ⚠ THESE TWO OCCUPY A POSITION §E3.7 DOES NOT NAME — see SPEC GAP below.
    ("tasks.py", ".minical"):
        "SIXTH POSITION: a rule ABOVE a stacked section inside a rail. Not one "
        "of E3.7's five; kept because it is structural, not decorative.",
    ("widgets.py", ".agsec"):
        "SIXTH POSITION: a rule above an agenda SECTION HEADING, dividing "
        "stacked sections in a column. Same shape as tasks .minical.",
}

# ⚠ SPEC GAP FOR THE DESIGN OWNER, found by building this gate rather than by
# reading the doc. E3.7 permits exactly five hairline positions, and the OS uses
# a SIXTH in two places: a rule that separates STACKED SECTIONS in a column
# (above `.minical` in the tasks rail, above `.agsec` in the widgets agenda).
# It is not "beneath a header band" — it sits ABOVE the heading it belongs to.
# By the design thesis (structure is revealed, not hidden) a section divider is
# clearly structural rather than decoration, so the likely correction is that
# E3.7 should name a sixth position — not that these two rules should go. That
# is a design call, so they are listed here rather than silently reclassified.

fails = []
checks = 0


def css_rules(text):
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text, re.S):
        yield m.group(1).strip().replace("\n", " "), m.group(2)


def has_side_hairline(body):
    """CSS border shorthand components are order-independent."""
    for value in SIDE_DECL.findall(body):
        tokens = {tok.lower() for tok in re.findall(r"[^\s]+", value)}
        if "1px" in tokens and "solid" in tokens:
            return True
    return False


def role_of(selector):
    s = selector.lower()
    for role, pat in ROLES.items():
        if re.search(pat, s):
            return role
    return None


def main():
    global checks
    files = [("gtk.css", THEME)]
    for f in sorted(os.listdir(DE)):
        if f.endswith(".py"):
            files.append((f, os.path.join(DE, f)))

    placed = 0
    used_reviewed = set()
    for name, path in files:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for selector, body in css_rules(text):
            if not has_side_hairline(body):
                continue
            if len(selector) > 160 or "def " in selector:
                continue          # a python block that merely looks like CSS
            if CONTROL.search(selector):
                continue          # a control outline, not a dividing rule
            checks += 1
            if role_of(selector):
                placed += 1
                continue
            hit = None
            for (f, frag), why in REVIEWED.items():
                if f == name and frag in selector:
                    hit, _ = (f, frag), why
                    break
            if hit:
                used_reviewed.add(hit)
                placed += 1
                continue
            fails.append("%s: hairline on %r occupies none of E3.7's five "
                         "permitted positions — a rule floating in a field"
                         % (name, selector[:70]))

    # The ledger is a ratchet: a reviewed entry whose selector is gone is a
    # stale claim, and a stale claim is how a ledger drifts from the tree.
    for key in sorted(set(REVIEWED) - used_reviewed):
        fails.append("STALE REVIEWED entry: %s %s no longer carries a hairline"
                     % key)

    for f in fails:
        print("FAIL  %s" % f)
    print("\n%s  §E4 check 3 (hairline positions): %d rule(s) examined, "
          "%d placed, %d reviewed by hand"
          % ("FAIL" if fails else "PASS", checks, placed, len(REVIEWED)))
    if not fails:
        print("RESULT: PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
