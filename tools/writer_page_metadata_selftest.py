#!/usr/bin/env python3
"""Regression: Writer retains ruler tabs and page extension metadata."""
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import writer  # noqa: E402


def main():
    page = {
        "size": "Letter", "orientation": "portrait",
        "margins": [1, 1, 1, 1], "tabs": [2.5, 1.25, 2.5],
        "writing_direction": "vertical",
    }
    reopened = writer._sane_doc({"body": "", "runs": [], "page": page})
    assert reopened["page"]["tabs"] == [1.25, 2.5]
    assert reopened["page"]["writing_direction"] == "vertical"
    changed = writer._page_with_setup(
        reopened["page"], "A4", "landscape", [0.5, 0.5, 0.5, 0.5])
    assert changed["tabs"] == [1.25, 2.5]
    assert changed["writing_direction"] == "vertical"
    damaged = writer._sane_page(dict(page, tabs=[math.inf, math.nan, "bad", 3]))
    assert damaged["tabs"] == [3.0]
    print("PASS ruler tabs and page metadata survive reopen and Page Setup")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
