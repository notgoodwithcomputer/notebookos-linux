#!/usr/bin/env python3
"""The shipped-binary census resolves common subprocess argv construction."""

from pathlib import Path
import tempfile

import shipped_binaries_check as gate


SOURCE = '''
import shutil, subprocess
DIRECT = "direct-tool"
def literal():
    subprocess.run([DIRECT, "--x"])
def variable():
    cmd = ["variable-tool", "--x"]
    subprocess.run(cmd)
def concatenated(args):
    subprocess.run(["concat-tool"] + args)
def guarded():
    if shutil.which("optional-tool"):
        subprocess.run(["optional-tool"])
def unrelated_guard():
    shutil.which("unguarded-tool")
def actually_unguarded():
    subprocess.run(["unguarded-tool"])
'''


def main():
    with tempfile.TemporaryDirectory(prefix="binary-gate-") as td:
        path = Path(td) / "sample.py"
        path.write_text(SOURCE, encoding="utf-8")
        found = gate.commands_in(path)
    expected = {
        "direct-tool": False, "variable-tool": False, "concat-tool": False,
        "optional-tool": True, "unguarded-tool": False,
    }
    assert found == expected, found
    print("shipped binary command resolution: PASS")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
