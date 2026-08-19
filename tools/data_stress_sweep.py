#!/usr/bin/env python3
"""
data_stress_sweep — which STORED FIELD pushes an app past the panel?

    tools/guestrun.sh python3 tools/data_stress_sweep.py [app ...]

THE HOLE THIS FILLS. `minsize_sweep` and `ellipsis_sweep` both run against an
EMPTY NB_HOME and both say so in their own headers: neither can see an overflow
that only appears once there is something in the store. That is not a small
corner — it is where the two worst instances of this bug class actually came
from, journal's 3396px `.metaline` and academics' 3304px `.canvas-meta`, each a
stored field with no length clamp and no ellipsize, each invisible to a sweep of
a fresh profile.

WHY IT REPORTS A FIELD AND NOT JUST AN APP. Stretching every string at once
tells you an app broke; it does not tell you which of its twenty fields did it,
and the answer is the whole fix. So each string field is stretched ON ITS OWN,
with every other field left at its normal length, and the report names the path
(`tx.[].date`). One measurement per field costs a couple of seconds and is worth
far more than one measurement per app.

NO SCHEMA IS INVENTED HERE. The stores come from `data_safety_selftest`'s own
`RECORD_STORES` builders, which are already kept correct because that suite
depends on the app loading them. A second hand-written copy of nine schemas
would drift the week it was written, and a store the app quietly REJECTS
measures nothing while looking like a pass — the vacuous-pass shape.

WHAT COUNTS AS A FINDING. Exceeding the 1024x740 budget is a failure: GTK cannot
shrink a window below its minimum, so on a real panel the excess is simply
unreachable. Growing by more than GROW_PX without exceeding it is reported too,
because a field that moves the minimum at all is a field with no ceiling — the
next user's data is longer than this one's.

Found on the first run (2026-08-06), with every other gate green:

    accounting  tx.[].date  ->  1268x389

`date` is stored with `str(t.get("date", ""))` and no clamp, then rendered in a
plain `Gtk.Label` with no ellipsize, so a damaged, hand-edited or foreign
accounting.json puts the right quarter of the ledger off a 1024 panel. The
neighbouring `desc` is clamped; `date` was not, presumably because nobody
expects a date to be long — which is exactly the assumption a foreign store
breaks.

WHAT IT CANNOT SEE. Deleting academics' `room[:40]` clamp produced NO finding,
which looked like a hole and is not one — the first explanation written here
("it only measures the default view") was WRONG and is recorded because it is
the tempting one. A Gtk.Stack is hhomogeneous BY DEFAULT and reports the MAXIMUM
width across all of its pages regardless of which is visible: academics' three
pages want 788/271/564 and the stack answers 788 for every one of them. **Tab
content is already measured.** The room did not move the number because
`_rec_academics` gives its class `"meets": []`, so no meeting row exists to draw
one in, and the field is ellipsized where it is drawn.

The real limits are narrower: a field only reachable through a SELECTION or a
dialog (neither of which a freshly-constructed window has made), and anything
whose width is decided by a widget that is not a label. A clean run is evidence
about layout, not about validation — `date` here was unclamped all along and
would still be unclamped if the drawing had happened to ellipsize it.

Exit status is non-zero if any field overflows the budget.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, HERE)
sys.path.insert(0, DE)

W, H = 1024, 740
GROW_PX = 20
N = 3                       # enough rows to lay out; the LENGTH is the variable
# Long, but a sentence a person could actually type into a name or note field —
# not a fuzzing blob. A field that cannot hold this is a field with no ceiling.
LONG = "Kitchen renovation and general household repairs and quarterly review"
# Ten times longer, used only to settle a question the realistic string cannot:
# is this field CAPPED or merely wide? A clamped or ellipsizing field does not
# move at all no matter what you put in it, so if HUGE moves the minimum the
# field has no ceiling and the only thing standing between the user and an
# unreachable window is how much they happened to type. Reported separately
# from a plain overflow because the two need different fixes — a clamp on load
# versus an ellipsis on the widget.
HUGE = LONG * 10


def paths(obj, pre=()):
    """Every string-valued field path in a store, lists collapsed to `[]`."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += paths(v, pre + (k,))
    elif isinstance(obj, list):
        if obj:
            out += paths(obj[0], pre + ("[]",))
    elif isinstance(obj, str):
        out.append(pre)
    return out


def setpath(obj, path, val):
    """Copy of `obj` with `path` set to `val` in EVERY element of any list."""
    if not path:
        return val
    k = path[0]
    if k == "[]" and isinstance(obj, list):
        return [setpath(x, path[1:], val) for x in obj]
    if isinstance(obj, dict) and k in obj:
        out = dict(obj)
        out[k] = setpath(obj[k], path[1:], val)
        return out
    return obj


def write_store(home, cfgname, data):
    d = os.path.join(home, ".config", "notebook")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, cfgname), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def measure(app, home):
    """Minimum size with this store, via minsize_sweep's own --one child, so
    the measurement rules (height-for-width, mocked screen_size, one app per
    process) are shared rather than re-implemented and allowed to disagree."""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "minsize_sweep.py"),
             "--one", app, str(W), str(H)],
            capture_output=True, text=True, timeout=180,
            env=dict(os.environ, NB_HOME=home))
    except subprocess.TimeoutExpired:
        return None, None
    for ln in reversed((r.stdout or "").strip().splitlines()):
        if ln.startswith("{"):
            try:
                d = json.loads(ln)
            except ValueError:
                return None, None
            return d.get("w"), d.get("h")
    return None, None


def main():
    import data_safety_selftest as D
    want = sys.argv[1:]
    apps = [a for a in sorted(D.RECORD_STORES) if not want or a in want]
    over, grew, errors = [], [], []
    measured = 0
    for app in apps:
        cfgname, build = D.RECORD_STORES[app][0], D.RECORD_STORES[app][1]
        base = build(N)
        home = tempfile.mkdtemp(prefix="nbstress-%s-" % app)
        write_store(home, cfgname, base)
        bw, bh = measure(app, home)
        if bw is None:
            print("  %-11s ERROR measuring baseline" % app)
            errors.append((app, "baseline"))
            continue
        measured += 1
        print("  %-11s baseline %4dx%-4d" % (app, bw, bh))
        for p in paths(base):
            h2 = tempfile.mkdtemp(prefix="nbstress-f-")
            write_store(h2, cfgname, setpath(base, p, LONG))
            w2, hh2 = measure(app, h2)
            if w2 is None:
                errors.append((app, ".".join(p)))
                print("     ERROR     %-26s could not be measured" %
                      ".".join(p))
                continue
            label = ".".join(p)
            moved = (w2 > bw + GROW_PX or hh2 > bh + GROW_PX)
            if not (moved or w2 > W or hh2 > H):
                continue
            # It moved. Now find out whether it is capped at all.
            h3 = tempfile.mkdtemp(prefix="nbstress-h-")
            write_store(h3, cfgname, setpath(base, p, HUGE))
            w3, hh3 = measure(app, h3)
            if w3 is None:
                errors.append((app, label + " (bound check)"))
                print("     ERROR     %-26s bound could not be measured" % label)
                continue
            unbounded = w3 is not None and (w3 > w2 + GROW_PX or hh3 > hh2 + GROW_PX)
            if unbounded and (w3 > W or hh3 > H):
                over.append((app, label, w3, hh3))
                print("     UNBOUNDED %-26s -> %dx%d at %d chars (%dx%d at %d)"
                      % (label, w3, hh3, len(HUGE), w2, hh2, len(LONG)))
            elif w2 > W or hh2 > H:
                over.append((app, label, w2, hh2))
                print("     OVERFLOW  %-26s -> %dx%d" % (label, w2, hh2))
            else:
                grew.append((app, label, w2, hh2))
                print("     grows     %-26s -> %dx%d%s"
                      % (label, w2, hh2, "  (capped)" if not unbounded else ""))

    print("\n%d field(s) OVERFLOW %dx%d, %d more move the minimum"
          % (len(over), W, H, len(grew)))
    if errors:
        print("RESULT: INCONCLUSIVE — %d measurement error(s), %d/%d app baselines measured"
              % (len(errors), measured, len(apps)))
        return 2
    if not over:
        # "RESULT: <prose>" is not a verdict the release runner recognises
        # (run_all_gates POSITIVE_RESULT); lead with the word, keep the fact.
        print("RESULT: PASS — no stored field pushes an app off the panel")
        return 0
    print("RESULT: %d OVERFLOW" % len(over))
    return 1


if __name__ == "__main__":
    sys.exit(main())
