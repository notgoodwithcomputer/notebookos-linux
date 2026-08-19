#!/usr/bin/env python3
"""A one-rule GTK stylesheet is real gate coverage, not 'none'."""

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/css_parse_check.py"


def run(path):
    return subprocess.run([sys.executable, str(GATE), str(path)], cwd=ROOT,
                          text=True, capture_output=True)


def main():
    with tempfile.TemporaryDirectory(prefix="nb-css-one-") as td:
        path = Path(td) / "sample.py"
        path.write_text("CSS = b'''button { color: #fff; }'''\n", encoding="utf-8")
        good = run(path)
        ok1 = good.returncode == 0 and "1 css block(s)" in good.stdout
        path.write_text(
            "CSS = b'''button { background: definitely-not-a-color; }'''\n",
            encoding="utf-8")
        bad = run(path)
        ok2 = bad.returncode != 0 and "PARSE-ERROR" in bad.stdout
        path.write_text("NOTE = '''prose with {braces}, not CSS'''\n", encoding="utf-8")
        prose = run(path)
        ok3 = prose.returncode == 0 and "0 css block(s)" in prose.stdout
    print(("PASS" if ok1 else "FAIL") + ": valid single rule is parsed")
    print(("PASS" if ok2 else "FAIL") + ": invalid single rule fails")
    print(("PASS" if ok3 else "FAIL") + ": prose remains excluded")
    print("RESULT: %s" % ("ALL PASS" if ok1 and ok2 and ok3 else "FAILED"))
    return not (ok1 and ok2 and ok3)


if __name__ == "__main__":
    raise SystemExit(main())
