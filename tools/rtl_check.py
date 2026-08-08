#!/usr/bin/env python3
"""rtl_check — a sign or unit on the WRONG SIDE of a number in RTL (yi).

app-improve found this 2026-08-09 and it is the campaign's TRUE bar in a place
nobody looked: no app in the OS had ever been run right-to-left. GTK mirrors
the CONTAINERS correctly and for free, so the screenshot looks right and you
move on — but a leading "+"/"−"/"$"/"%"/"(" is a bidi-WEAK character, and
followed by European numerals the Unicode bidi algorithm resolves it to the
paragraph direction and lays it on the FAR side. A label holding "+$1,105.00"
has Pango draw "$1,105.00+"; a debit "−$950.00" draws "$950.00−". In a ledger
the sign is the only thing on the row that says which way the money went. The
UNSIGNED figures are unaffected, which is exactly why it hid.

The fix is nbi18n.ltr(): U+2066 LEFT-TO-RIGHT ISOLATE .. U+2069 keeps the weak
char attached, gated on the direction ACTUALLY IN FORCE so the other sixteen
languages are byte-for-byte unchanged.

THIS GATE is static and precise: it finds every user-visible string literal
whose shape is AT RISK — a bidi-weak sign/unit/bracket adjacent to a digit or
a numeric placeholder — and, walking the AST, passes it ONLY if that literal
is reached from inside an `ltr(...)` call (directly, or through a `_t(...) %
args` chain that ltr wraps). Anything at-risk and unwrapped is a violation.

  LIMITATION it is honest about: it cannot see a figure COMPOSED at runtime
  from pieces that are individually innocent ("%s%s" % (sign, amount)). Those
  want the per-app dynamic proof (accounting_rtl_selftest lays the finished
  label out with Pango and reads the resolved visual order — the authoritative
  check; this is the OS-wide source guard that keeps new ones from landing).

Both-direction ratchet debt (grid_check's pattern) for the apps not yet
migrated. Red-proof: a new at-risk unwrapped label fails; wrapping it in ltr()
clears it; a stale debt entry (fixed at source) fails.

  python3 tools/rtl_check.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")

# A bidi-weak sign / currency / bracket sitting immediately against a number,
# OR a number immediately before a DISPLAYED percent or a close-bracket. `−` is
# U+2212 (the real minus the money code uses), not the ASCII hyphen. A NUMBER
# is a digit or a numeric format placeholder (%d/%i/%f, or {} / {:..}); %s is
# usually text and is NOT a number here (the runtime-composed case the
# docstring cedes to the dynamic proof). The displayed percent is the LITERAL
# `%%` in a format string (renders as "%"), never the `%` of a format
# specifier — including that `%` was the first draft's bug, matching every
# %d/%02d/%s in the OS.
_NUM = r"(?:\d|%[-+ #0]*\d*\.?\d*[difDIF]|\{\d*:?[^{}]*\})"
# HIGH-CONFIDENCE only: a sign/currency that STARTS a numeric run (at string
# start or after whitespace), or a number directly before a literal percent.
# Deliberately NOT flagged, because the static view cannot tell a truly-broken
# one from a correct one and the dynamic Pango proof must: a "+" after a Latin
# word ("Ctrl+1" — the strong-LTR prefix anchors the run) and bracket groups
# ("(0)" — brackets MIRROR correctly under RTL, that is the right behaviour).
# Those belong to the per-app dynamic check, not this source guard.
AT_RISK = re.compile(
    r"(?:^|\s)[-+−$£€]" + _NUM                # leading sign/currency at a boundary
    + r"|" + _NUM + r"%%")                    # number directly before "%"

# Debt: the initial OS-wide sweep, 2026-08-09 — at-risk labels not yet routed
# through ltr(). module -> count, exact-match both directions (fix one and drop
# the count, or a new one landed). Burn-down is per-lane: wrap each label in
# nbi18n.ltr() at the point the finished string reaches its widget, then lower
# the count. Dominant class is "%d%%" (a percent that lands on the wrong side
# of its number in yi); "+%d more"/"+%d XP" are the leading-sign class; the
# currency message is accounting's.
DEBT = {
    "accounting.py": 1,    # "at least $0.01" (app-improve's — validation msg)
    "calendar.py": 1,      # "+%d more"       (bug-fix's claim)
    "illustrator.py": 5,   # zoom "%d%%" / "%.1f%%"
    "installer.py": 1,     # progress "%d%%"  (campaign)
    "language.py": 1,      # "+%d XP"          (campaign)
    "media.py": 1,         # "%d%%"           (bug-fix's claim)
    "sequencer.py": 2,     # "%d%%" x2
    "settings.py": 2,      # "%d%%" x2         (campaign)
    "sysmon.py": 1,        # "%d%% in use"     (campaign)
    "usbwriter.py": 1,     # write "%d%%"      (campaign)
    "widgets.py": 2,       # "+%d more" x2     (campaign)
}

_FAILS = []
_CHECKS = [0]


def _check(ok, msg):
    _CHECKS[0] += 1
    if not ok:
        _FAILS.append(msg)
        print("FAIL  %s" % msg)


def _ltr_wrapped_literals(tree):
    """Every string literal reachable from inside an ltr(...) call — those are
    handled. Recurse the call's args so `ltr(_t("+%d") % n)` counts the inner
    literal."""
    handled = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name == "ltr":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) \
                            and isinstance(sub.value, str):
                        handled.add(id(sub))
    return handled


# Calls whose string argument is shown to a person (mirrors voice_check).
_TEXT = {"_t", "set_text", "set_label", "set_markup", "set_title",
         "set_tooltip_text", "flash", "_flash", "_flash_status"}


def _visible_at_risk(tree):
    """(lineno, text, node_id) for user-visible literals whose shape is
    at-risk. Only literals that reach a text call or a _t() — a docstring or a
    dict key that happens to look numeric is not a label."""
    out, seen = [], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in _TEXT:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                    and id(sub) not in seen and AT_RISK.search(sub.value):
                seen.add(id(sub))       # one literal, one finding, even if it
                out.append((getattr(sub, "lineno", 0), sub.value, id(sub)))
    return out


def main():
    seen_debt = {}
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py"):
            continue
        try:
            tree = ast.parse(open(os.path.join(DE, fn), encoding="utf-8").read())
        except SyntaxError:
            continue
        handled = _ltr_wrapped_literals(tree)
        risky = [(ln, t) for (ln, t, nid) in _visible_at_risk(tree)
                 if nid not in handled]
        n = len(risky)
        debt = DEBT.get(fn)
        if debt is not None:
            seen_debt[fn] = n
            if n == debt:
                _CHECKS[0] += 1
                continue
        if n and debt is None:
            for ln, t in risky:
                _check(False, "%s:%d at-risk label not wrapped in ltr(): %r"
                       % (fn, ln, t[:48]))
        elif debt is not None and n != debt:
            _check(False, "%s: %d at-risk unwrapped labels != debt %d "
                   "(fix + drop the debt, or a new one landed)" % (fn, n, debt))
        elif n == 0:
            _CHECKS[0] += 1
    for fn in sorted(set(DEBT) - set(seen_debt)):
        _check(False, "STALE DEBT: %s not scanned — remove its entry" % fn)

    n = _CHECKS[0]
    if _FAILS:
        print("RESULT: FAILED — %d of %d checks (RTL sign placement)"
              % (len(_FAILS), n))
        return 1
    print("PASS  rtl: %d checks (no unratcheted at-risk labels)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
