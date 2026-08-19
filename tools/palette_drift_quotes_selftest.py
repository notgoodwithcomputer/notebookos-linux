#!/usr/bin/env python3
"""Both Python triple-quote spellings must enter palette analysis."""

import palette_drift_check as gate


def main():
    src = "CSS = b'''button { background: #FCFBF7; }'''\n"
    blobs = list(gate.css_byte_blobs(src))
    ok = len(blobs) == 1 and "#FCFBF7" in blobs[0]
    print(("PASS" if ok else "FAIL") + ": single-quoted byte CSS discovered")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
