#!/usr/bin/env python3
"""Headless byte-fidelity contract for Screenplay plain-text imports."""
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
import screenplay  # noqa: E402

checks = []
with tempfile.TemporaryDirectory(prefix="screenplay-encoding-") as root:
    bad = os.path.join(root, "draft.fountain")
    original = b"INT. CAFE - DAY\nPrice \x96 ten\n"
    with open(bad, "wb") as fh: fh.write(original)
    text, lossy = screenplay._read_plain_text(bad)
    checks.append((lossy and "\ufffd" in text
                   and open(bad, "rb").read() == original,
                   "invalid UTF-8 is detected without changing source bytes"))
    good = os.path.join(root, "good.fountain")
    with open(good, "wb") as fh: fh.write("café\n".encode())
    text, lossy = screenplay._read_plain_text(good)
    checks.append((not lossy and text == "café\n",
                   "valid UTF-8 remains exact"))

source = open(os.path.join(DE, "screenplay.py"), encoding="utf-8").read()
ast.parse(source)
method = source[source.index("    def _open_file("):
                source.index("    # Screenplay text conventions")]
checks.append(("bound_path = None if plain_decode_failed else path" in method
               and "if plain_decode_failed:" in method
               and "self._file_dirty = True" in method,
               "lossy recovery is Save-As-only rather than source-bound"))

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
passed = sum(bool(ok) for ok, _name in checks)
print("RESULT: 3 checks, ALL PASS (3/3)" if passed == len(checks) else
      "RESULT: FAILED (%d/%d checks passed)" % (passed, len(checks)))
raise SystemExit(passed != len(checks))
