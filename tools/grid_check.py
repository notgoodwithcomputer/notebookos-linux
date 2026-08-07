#!/usr/bin/env python3
"""grid_check — the §E4 gate of docs/PAPER-PHYSICS.md (v1).

Static checks available today:
  A. LOCKSTEP  tools/design_tokens.py (canonical) == de/nbapp.py runtime copy
               == shell.py / widgets.py strut == minsize_sweep's budget.
  B. RAILS     every sidebar-width constant in de/ equals RAIL, or is a listed
               exception (design_tokens.RAIL_EXCEPTIONS), or is grandfathered
               debt below.
  C. LADDER    every `min-height: Npx` in the Papertone theme and app CSS sits
               on the bordered ladder (interior+2), the open ladder, or is
               grandfathered debt below.

Checks 3–5 of §E4 (hairline positions, rest edges on rules, no diagonal travel
vectors) require the Article G motion inventory and land with it.

THE DEBT LEDGERS ARE A RATCHET, in both directions. An entry must match the
source exactly: fix the source without deleting the entry and the entry goes
STALE (fail); delete the entry without fixing the source and the deviation is
a REGRESSION (fail). So the gate is green today without lying about the state
of the tree — the lie would be a debt entry nobody can see. Burn-down is
migration row 3d in PAPER-PHYSICS §9.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
THEME = os.path.join(ROOT, "buildroot", "board", "notebookos",
                     "rootfs-overlay", "usr", "share", "themes", "Papertone",
                     "gtk-3.0", "gtk.css")

sys.path.insert(0, HERE)
import design_tokens as dt  # noqa: E402

# ---- Debt: sidebars not yet converged on RAIL (PAPER-PHYSICS §9 row 3d).
# module -> (constant name, current width). Exact match required.
RAIL_DEBT = {
    "workout": ("SIDEBAR_W", 210),
    "packages": ("SIDEBAR_W", 212),
    "bills": ("SIDEBAR_W", 252),
}

# ---- Debt: min-height values off both ladders, grandfathered at their
# current per-file count. (file basename, value) -> count.
HEIGHT_DEBT = {
}

_FAILS = []
_CHECKS = [0]


def _check(ok, msg):
    _CHECKS[0] += 1
    if not ok:
        _FAILS.append(msg)
        print("FAIL  %s" % msg)


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def check_lockstep():
    nbapp = _src(os.path.join(DE, "nbapp.py"))
    for name in ("GRID_UNIT", "MARGIN", "RAIL", "GUTTER", "HAIRLINE",
                 "MEASURE_READ", "MEASURE_FORM", "PANEL_H",
                 "THIRD_PANE_MIN_W"):
        want = getattr(dt, name)
        m = re.search(r"^%s = (\d+)" % name, nbapp, re.M)
        _check(m is not None and int(m.group(1)) == want,
               "nbapp.py %s: runtime copy %s != design_tokens %d"
               % (name, m.group(1) if m else "MISSING", want))
    _check("screen_h - PANEL_H" in nbapp,
           "nbapp.canvas_h no longer derives from PANEL_H")
    for mod in ("shell.py", "widgets.py"):
        m = re.search(r"^PANEL_H = (\d+)", _src(os.path.join(DE, mod)), re.M)
        _check(m is not None and int(m.group(1)) == dt.PANEL_H,
               "%s PANEL_H %s != design_tokens %d"
               % (mod, m.group(1) if m else "MISSING", dt.PANEL_H))
    # minsize_sweep must measure against the REAL budget (§E4 check 6 — this
    # is the 18px phantom that let Video ship 3px too tall).
    sweep = _src(os.path.join(HERE, "minsize_sweep.py"))
    m = re.search(r"\[\(1024, (\d+)\), \(1366, (\d+)\)\]", sweep)
    budget = dt.canvas_h(768)
    _check(m is not None and int(m.group(1)) == budget
           and int(m.group(2)) == budget,
           "minsize_sweep default budget %s != canvas_h(768) = %d"
           % (m.groups() if m else "MISSING", budget))


def check_rails():
    pat = re.compile(r"^\s*(SIDEBAR_W|PANEL_W|DOCK_W|RAIL_W)\s*=\s*(\d+)",
                     re.M)
    seen_debt = set()
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py"):
            continue
        mod = fn[:-3]
        for cname, val in pat.findall(_src(os.path.join(DE, fn))):
            val = int(val)
            if val == dt.RAIL:
                _CHECKS[0] += 1
                continue
            if dt.RAIL_EXCEPTIONS.get(mod) == val:
                _CHECKS[0] += 1
                continue
            debt = RAIL_DEBT.get(mod)
            if debt and debt == (cname, val):
                seen_debt.add(mod)
                _CHECKS[0] += 1
                continue
            _check(False, "%s.%s = %d is off RAIL=%d and not excepted or "
                   "in debt" % (mod, cname, val, dt.RAIL))
    for mod in sorted(set(RAIL_DEBT) - seen_debt):
        _check(False, "STALE DEBT: RAIL_DEBT[%r] no longer matches the "
               "source — it was fixed; delete the entry" % mod)


def check_ladder():
    # The ladder governs CONTROL boxes. Values below the control band (< 22)
    # are spacers, progress tracks, drag handles and shrink-enablers — a
    # static scan cannot tell those from a control, so v1 leaves them alone
    # (runtime node-type pairing is the Article G-era upgrade). Inside the
    # band [22, 40] the named steps apply; above it the general §E3.2 rule:
    # interior on the 4u grid, so rendered ≡ 0 (open) or ≡ 2 (bordered).
    named = set(dt.LADDER_RENDERED) | set(dt.LADDER_OPEN)
    pat = re.compile(r"min-height:\s*(\d+)px")
    found = {}
    files = [THEME] + [os.path.join(DE, f) for f in sorted(os.listdir(DE))
                       if f.endswith(".py")]
    for path in files:
        base = os.path.basename(path)
        for v in pat.findall(_src(path)):
            v = int(v)
            if v < 22 or (v <= 40 and v in named) or \
                    (v > 40 and v % 4 in (0, 2)):
                _CHECKS[0] += 1
                continue
            found[(base, v)] = found.get((base, v), 0) + 1
    for key in sorted(set(found) | set(HEIGHT_DEBT)):
        have, debt = found.get(key), HEIGHT_DEBT.get(key)
        if have == debt:
            _CHECKS[0] += 1
            continue
        if debt is None:
            _check(False, "%s min-height:%dpx x%d off both ladders %s/%s "
                   "and not in debt" % (key[0], key[1], have,
                                        dt.LADDER_RENDERED, dt.LADDER_OPEN))
        elif have is None:
            _check(False, "STALE DEBT: HEIGHT_DEBT[%r] fixed in source — "
                   "delete the entry" % (key,))
        else:
            _check(False, "%s min-height:%dpx count %d != debt %d — ledger "
                   "must track the source exactly" % (key[0], key[1], have,
                                                      debt))


def main():
    check_lockstep()
    check_rails()
    check_ladder()
    n = _CHECKS[0]
    if _FAILS:
        print("RESULT: FAILED — %d of %d checks (grid: PAPER-PHYSICS §E3/§E4)"
              % (len(_FAILS), n))
        return 1
    print("PASS  grid conformance: %d checks (lockstep, rails, ladder)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
