#!/usr/bin/env python3
"""Every legal byte-string prefix order is scanned."""

from pathlib import Path
import tempfile
import ascii_css_check as gate


def main():
    failed = 0
    with tempfile.TemporaryDirectory(prefix="nb-ascii-css-") as td:
        path = Path(td) / "sample.py"
        for prefix in ("b", "B", "rb", "rB", "br", "BR"):
            path.write_text('x = %s"""button { color: #fff; } —"""\n' % prefix,
                            encoding="utf-8")
            found = gate.check(path)
            ok = bool(found and found[0][1] == "—")
            print(("PASS: " if ok else "FAIL: ") + prefix + " prefix")
            failed += not ok
        path.write_text('x = r"""ordinary Unicode — text"""\n', encoding="utf-8")
        ok = gate.check(path) == []
        print(("PASS: " if ok else "FAIL: ") + "ordinary raw strings stay allowed")
        failed += not ok
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
