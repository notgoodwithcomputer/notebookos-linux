#!/usr/bin/env python3
"""Rail widths are evaluated as AST expressions, never numeric prefixes."""

from pathlib import Path
import contextlib
import io
import tempfile
import grid_check as gate


def scan(source):
    with tempfile.TemporaryDirectory(prefix="nb-grid-rail-") as td:
        Path(td, "app.py").write_text(source, encoding="utf-8")
        gate._FAILS[:] = []
        gate._CHECKS[0] = 0
        debt, gate.RAIL_DEBT = gate.RAIL_DEBT, {}
        # The scanner prints its own "FAIL  ..." lines, and several of these
        # probes exist to MAKE it fail. Swallow that output: a suite that
        # exits 0 while printing FAIL reads as a liar to run_all_gates.
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                gate.check_rails(td)
        finally:
            gate.RAIL_DEBT = debt
        return list(gate._FAILS), gate._CHECKS[0]


def main():
    good, checks = scan("SIDEBAR_W = %d\n" % gate.dt.RAIL)
    expr, _ = scan("SIDEBAR_W = %d + 1\n" % gate.dt.RAIL)
    conditional, _ = scan("SIDEBAR_W = 240 if compact else 320\n")
    alias, _ = scan("WIDTH = 240\nSIDEBAR_W = WIDTH\n")
    local, local_checks = scan(
        "def compute_layout_hint():\n"
        "    SIDEBAR_W = 17\n"
        "    return SIDEBAR_W * 2\n")
    ok = (not good and checks == 1 and bool(expr) and bool(conditional)
          and bool(alias) and not local and local_checks == 0)
    print(("PASS" if not good and checks == 1 else "FAIL")
          + ": literal canonical rail remains green")
    print(("PASS" if expr else "FAIL")
          + ": arithmetic expression is evaluated, not prefix-matched")
    print(("PASS" if conditional and alias else "FAIL")
          + ": unresolved widths fail instead of disappearing")
    print(("PASS" if not local and local_checks == 0 else "FAIL")
          + ": unrelated function-local names are not UI rail constants")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
