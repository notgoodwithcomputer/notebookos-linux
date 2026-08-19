#!/usr/bin/env python3
"""Decimal px spellings cannot disappear from the control-height ladder."""
import os
import contextlib
import io
import tempfile
from pathlib import Path

import grid_check as gate


def scan(value):
    with tempfile.TemporaryDirectory(prefix="nb-grid-ladder-") as td:
        root = Path(td)
        (root / "app.py").write_text(
            "CSS = b'.x { min-height: %spx; }'\n" % value,
            encoding="utf-8")
        theme = root / "gtk.css"
        theme.write_text("", encoding="utf-8")
        old_de, old_theme, old_debt = gate.DE, gate.THEME, gate.HEIGHT_DEBT
        gate.DE, gate.THEME, gate.HEIGHT_DEBT = td, str(theme), {}
        gate._FAILS[:] = []
        gate._CHECKS[0] = 0
        try:
            # Rejected values are the expected mutant outcome.  Capture the
            # underlying gate's diagnostic so this selftest does not print raw
            # FAIL lines and then contradict them with a successful verdict.
            with contextlib.redirect_stdout(io.StringIO()):
                gate.check_ladder()
            return list(gate._FAILS), gate._CHECKS[0]
        finally:
            gate.DE, gate.THEME, gate.HEIGHT_DEBT = old_de, old_theme, old_debt


def main():
    good24, n24 = scan("24.0")
    good26, n26 = scan("26.0")
    bad25, _ = scan("25.0")
    fractional, _ = scan("24.5")
    ok = not good24 and n24 == 1 and not good26 and n26 == 1
    ok = ok and bool(bad25) and bool(fractional)
    print(("PASS" if not good24 and not good26 else "FAIL")
          + ": integral decimal ladder values remain measurable")
    print(("PASS" if bad25 else "FAIL")
          + ": 25.0px is rejected like 25px")
    print(("PASS" if fractional else "FAIL")
          + ": fractional 24.5px cannot evade the ladder")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
