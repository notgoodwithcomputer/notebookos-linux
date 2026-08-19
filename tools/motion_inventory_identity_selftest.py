#!/usr/bin/env python3
"""A motion contract must have exactly one inventory identity."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/motion_inventory_check.py"
INVENTORY = ROOT / "tools/motion_inventory.json"


def main():
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    duplicate = dict(data["entries"][0])
    duplicate["status"] = "exempt"
    duplicate["binding"] = None
    duplicate["note"] = "contradictory duplicate mutation"
    data["entries"].append(duplicate)
    with tempfile.TemporaryDirectory(prefix="nb-motion-id-") as td:
        path = Path(td) / "inventory.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(GATE), "--inventory", str(path)],
            text=True, capture_output=True, cwd=ROOT)
    if proc.returncode == 0 or "duplicate inventory id" not in proc.stdout:
        print("FAIL: contradictory duplicate motion ID passed")
        return 1
    print("PASS: duplicate motion IDs fail by identity")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
