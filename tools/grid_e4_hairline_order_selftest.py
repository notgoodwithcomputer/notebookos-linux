#!/usr/bin/env python3
"""CSS shorthand order must not hide a governed one-pixel hairline."""

import grid_e4_hairline_check as gate


def main():
    good = [
        "border-top: 1px solid #000;",
        "border-top: solid 1px #000;",
        "border-bottom: #000 solid 1px;",
    ]
    bad = ["border-top: 2px solid #000;", "border-bottom: 1px dashed #000;"]
    ok = all(gate.has_side_hairline(x) for x in good)
    ok = ok and not any(gate.has_side_hairline(x) for x in bad)
    print(("PASS" if ok else "FAIL") + ": hairline shorthand order")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
