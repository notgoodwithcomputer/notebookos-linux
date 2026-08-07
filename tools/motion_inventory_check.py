#!/usr/bin/env python3
"""Gate PAPER-PHYSICS Article G's motion inventory.

Pacing is deliberately staged: null results warn today.  Once the campaign sets
top-level ``pacing_required`` true, every null result is a failure.  This gate
does not import or depend on the separately developed frame-pacing checker.
"""
import argparse
import ast
import io
import json
from pathlib import Path
import sys
import tokenize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "tools/motion_inventory.json"
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"


def comments(path):
    try:
        source = path.read_text(encoding="utf-8")
        return [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
                if t.type == tokenize.COMMENT]
    except (OSError, UnicodeError, tokenize.TokenError):
        return []


def markers(path):
    prefix = "# nbmotion-inventory:"
    return [(c[len(prefix):].strip(), path) for c in comments(path)
            if c.startswith(prefix)]


def symbols(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
    return found


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
    counts = {s: sum(e["status"] == s for e in entries)
              for s in ("implemented", "partial", "unimplemented")}
    scan = list(DE.glob("*.py")) + a.extra_file
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
        if e.get("pacing") is None and status in ("implemented", "partial"):
            msg = f"null pacing result: {e['id']}"
            (failures if data.get("pacing_required") else warnings).append(msg)

    for mid, path in seen:
        checks += 1
        if mid not in ids:
            failures.append(f"implementation marker with no inventory entry: {mid} ({path})")

    print("STATUS: implemented={implemented} partial={partial} unimplemented={unimplemented} total={total}".format(total=len(entries), **counts))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
