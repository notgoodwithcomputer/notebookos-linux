#!/usr/bin/env python3
"""Display-free reset semantics for the long-lived session timezone."""
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbprefs  # noqa: E402


original = os.environ.get("TZ")
try:
    assert nbprefs.apply_timezone({"tz_posix": "EST5"}) == "EST5"
    assert os.environ.get("TZ") == "EST5"
    assert nbprefs.apply_timezone({}) == ""
    assert "TZ" not in os.environ
    print("PASS clearing the preference clears the live session TZ")

    os.environ["TZ"] = "CST6"
    assert nbprefs.apply_timezone({"tz_posix": ""}) == ""
    assert "TZ" not in os.environ
    print("PASS an explicitly empty preference restores appliance time")
finally:
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    try:
        import time
        time.tzset()
    except AttributeError:
        pass

print("RESULT: ALL PASS")
