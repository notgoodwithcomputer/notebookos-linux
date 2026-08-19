#!/usr/bin/env python3
"""Regression: Calculator retains newer graph-window metadata safely."""
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import calculator  # noqa: E402


def main():
    state = calculator.sanitize_state({
        "window": {
            "xmin": math.nan, "xmax": 12, "ymin": -5, "ymax": 5,
            "xscl": 1, "yscl": 1,
            "polar_grid": {"angle": 15}, "axis_colour": "ochre",
        },
    })
    window = state["window"]
    assert math.isfinite(window["xmin"])
    assert window["polar_grid"] == {"angle": 15}
    assert window["axis_colour"] == "ochre"
    state2 = calculator.sanitize_state(state)
    assert state2["window"]["polar_grid"] == {"angle": 15}
    print("PASS graph-window metadata survives repeated safe normalization")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
