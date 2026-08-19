#!/usr/bin/env python3
"""Gate PAPER-PHYSICS Article G's motion inventory.

Pacing is deliberately staged: null results warn today.  Once the campaign sets
top-level ``pacing_required`` true, every null result is a failure.  This gate
does not import or depend on the separately developed frame-pacing checker.
"""
import argparse
import ast
from collections import Counter
import io
import json
from pathlib import Path
import sys
import tokenize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "tools/motion_inventory.json"
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
THEME = ROOT / ("buildroot/board/notebookos/rootfs-overlay/usr/share/themes/"
                "Papertone/gtk-3.0/gtk.css")


def comments(path):
    try:
        source = path.read_text(encoding="utf-8")
        return [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
                if t.type == tokenize.COMMENT]
    except (OSError, UnicodeError, tokenize.TokenError):
        return []


def markers(path):
    prefix = "# nbmotion-inventory:"
    if path.suffix == ".css":
        return _css_markers(path)     # some transitions are realised in the theme
    return [(c[len(prefix):].strip(), path) for c in comments(path)
            if c.startswith(prefix)]


def _css_markers(path):
    """CSS block-comment markers: /* nbmotion-inventory: <id> ... */. The theme
    realises the state-feedback transitions (app.toolbar-state), so a marker in
    gtk.css is as valid as one in a .py file. The id is the first token after
    the prefix (ids have no spaces)."""
    import re
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for block in re.findall(r"/\*(.*?)\*/", src, re.S):
        i = block.find("nbmotion-inventory:")
        if i != -1:
            rest = block[i + len("nbmotion-inventory:"):].strip()
            if rest:
                out.append((rest.split()[0], path))
    return out


def symbols(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
    return found


# Statuses that are a real answer even though no frame trace exists. Both come
# from tools/transition_pacing_probe.py: `configured-verified` read the duration
# GTK itself was configured with (a Stack/Revealer transition, or a CSS one) and
# checked it against the band — a genuine check, deliberately NOT called
# `measured` so it can never be mistaken for one; `continuous-untraced` is a
# value that follows a real quantity (the sequencer playhead follows the audio)
# and so creates no discrete run to time. Anything else is an absence.
# `exempt` is a DECISION, not a gap: a transition the design owner has ruled must
# NOT animate (the menu bar is deliberately static; the desktop board appears
# without staging). Counting those as `unimplemented` overstates the backlog and
# invites a future pass to "finish" them — which would undo an owner's call. An
# exempt row must SAY WHY, so nobody can quietly retire work by relabelling it.
STATUSES = ("implemented", "partial", "unimplemented", "exempt")

PACING_ANSWERED = {"configured-verified", "continuous-untraced"}


def pacing_problems(entry):
    """Why `entry`'s pacing record is not a passing measurement, if it is not.

    A RECORD is not the same as a PASS. The staged plan is that setting
    `pacing_required` turns null results into failures — but once every entry
    carries some record, a null-only test passes trivially, and an entry saying
    "nobody has driven this yet" would count exactly like one that was measured
    and conformed. That is a gate that cannot fail. So when the flag is on, the
    record has to SAY something: measured and passing, or one of the named
    non-traceable answers above, each of which somebody had to decide."""
    p = entry.get("pacing")
    if p is None:
        return ["null pacing result: %s" % entry["id"]]
    if not isinstance(p, dict):
        return ["pacing result is not a record: %s" % entry["id"]]
    status = p.get("status")
    if status == "measured":
        if p.get("verdict") != "pass":
            return ["measured pacing outside its band: %s (%s)"
                    % (entry["id"], p.get("reason"))]
        return []
    if status in PACING_ANSWERED:
        # A recorded answer still has to be a PASSING one. `configured-verified`
        # carries a verdict of its own — the configured duration is checked
        # against the token's band — and reading only the status swallowed it,
        # so an inventory recording a 20ms `SURFACE_IN` reveal was "answered"
        # and this gate stayed green.
        if p.get("verdict", "pass") != "pass":
            return ["recorded pacing outside its band: %s (status %r: %s)"
                    % (entry["id"], status, p.get("reason"))]
        return []
    return ["pacing not measured: %s (status %r: %s)"
            % (entry["id"], status, p.get("reason"))]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    p.add_argument("--extra-file", type=Path, action="append", default=[],
                   help="also scan a Python file for markers (red-proof support)")
    a = p.parse_args()
    data = json.loads(a.inventory.read_text(encoding="utf-8"))
    entries = data["entries"]
    ids = {e["id"] for e in entries}
    failures, warnings = [], []
    checks = 0
    id_counts = Counter(e.get("id") for e in entries)
    duplicate_ids = sorted(str(mid) for mid, count in id_counts.items()
                           if count != 1)
    if duplicate_ids:
        failures.append("duplicate inventory id: " + ", ".join(duplicate_ids))
    counts = {s: sum(e["status"] == s for e in entries)
              for s in STATUSES}
    unknown = [e["id"] for e in entries if e["status"] not in STATUSES]
    if unknown:
        # A status nobody counts is a row that silently leaves the ledger.
        failures.append("unknown status: " + ", ".join(unknown))
    scan = list(DE.glob("*.py")) + [THEME] + a.extra_file
    seen = []
    for path in scan:
        seen.extend(markers(path))
    marker_ids = {mid for mid, _ in seen}

    for e in entries:
        checks += 1
        status, binding = e["status"], e.get("binding")
        if status in ("implemented", "partial") and not binding:
            failures.append(f"entry missing implementation binding: {e['id']}")
        if status == "unimplemented" and binding:
            failures.append(f"unimplemented entry has binding (status lie): {e['id']}")
        if status == "exempt":
            checks += 1
            if not (e.get("note") or "").strip():
                failures.append("exempt entry gives no reason: %s" % e["id"])
            if e["id"] in marker_ids:
                failures.append(
                    "exempt entry has implementation marker: %s" % e["id"])
        if status == "unimplemented" and e["id"] in marker_ids:
            failures.append(f"unimplemented entry has implementation marker (status lie): {e['id']}")
        if binding:
            kind = binding["binding_kind"]
            path = ROOT / binding["module"]
            checks += 1
            if kind == "comment-marker":
                if e["id"] not in {mid for mid, mp in seen if mp.resolve() == path.resolve()}:
                    failures.append(f"entry missing implementation marker: {e['id']} ({path})")
            elif kind == "module-behavior":
                try:
                    if binding["symbol_or_marker"] not in symbols(path):
                        failures.append(f"entry binding symbol absent: {e['id']} ({binding['symbol_or_marker']})")
                except (OSError, SyntaxError) as ex:
                    failures.append(f"entry binding unreadable: {e['id']} ({ex})")
            elif kind == "css-section":
                try:
                    if binding["symbol_or_marker"] not in path.read_text(encoding="utf-8"):
                        failures.append(f"entry CSS section absent: {e['id']} ({binding['symbol_or_marker']})")
                except OSError as ex:
                    failures.append(f"entry binding unreadable: {e['id']} ({ex})")
            else:
                failures.append(f"unknown binding kind: {e['id']} ({kind})")
        checks += 1
        if status in ("implemented", "partial"):
            problems = pacing_problems(e)
            (failures if data.get("pacing_required") else warnings).extend(problems)

    for mid, path in seen:
        checks += 1
        if mid not in ids:
            failures.append(f"implementation marker with no inventory entry: {mid} ({path})")

    # Every status printed, so the parts visibly sum to the total. A line that
    # does not add up is read as a miscount or as rows quietly going missing.
    print("STATUS: " + " ".join("%s=%d" % (s, counts[s]) for s in STATUSES)
          + " total=%d" % len(entries))
    print("Entries missing an implementation binding:")
    missing = [e["id"] for e in entries if e["status"] in ("implemented", "partial") and not e.get("binding")]
    print("  " + (", ".join(missing) if missing else "none"))
    print("Implementation markers with no matching entry:")
    unknown = [mid for mid, _ in seen if mid not in ids]
    print("  " + (", ".join(unknown) if unknown else "none"))
    for w in warnings:
        print("WARN:", w)
    for f in failures:
        print("FAIL:", f)
    if failures:
        print(f"RESULT: FAILED — {len(failures)} failures; {checks} checks")
        return 1
    print(f"PASS  motion inventory conformance: {checks} checks")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
