#!/usr/bin/env python3
"""Calendar event time damage cannot take down the desktop board."""
import math
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="widget-event-"))
import widgets  # noqa: E402


parse = widgets.Widgets._start_minutes
for bad in (float("nan"), float("inf"), float("-inf"), True, False,
            -1, 24, 1e300, "not a time"):
    assert parse(bad) is None, bad
assert parse(9.0) == 540
assert parse(18.5) == 1110
assert parse(23.999999) is None
print("PASS nonfinite and out-of-day event times never raise")
print("RESULT: PASS")
