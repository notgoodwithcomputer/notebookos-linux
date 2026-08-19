#!/usr/bin/env python3
"""Display-free interval regressions for Duplicate Clip placement."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/sequencer.py"


def load_helpers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {"clip_parts", "first_free_clip_span"}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns["first_free_clip_span"]


def clip(s, e):
    return {"s": s, "e": e}


def main():
    first = load_helpers()
    cases = [
        ([clip(0, 4), clip(4, 8)], 4, 4, 12, 4, 8,
         "an adjacent blocker is skipped"),
        ([clip(0, 4), clip(5, 6)], 4, 4, 12, 4, 8,
         "a partial blocker is skipped"),
        ([clip(0, 4), clip(8, 10)], 4, 4, 12, 4, 4,
         "touching endpoints do not overlap"),
        ([clip(0, 4), clip(4, 8), clip(8, 12)], 4, 4, 12, 4, None,
         "a full remainder reports no room"),
    ]
    failed = 0
    for clips, start, span, limit, step, expected, label in cases:
        got = first(clips, start, span, limit, step)
        if got != expected:
            failed += 1
            print("FAIL: %s (%r != %r)" % (label, got, expected))
        else:
            print("PASS: " + label)
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
