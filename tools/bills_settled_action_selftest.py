#!/usr/bin/env python3
"""A settled one-off bill must not offer a meaningless payment action."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import bills  # noqa: E402


def main() -> None:
    bill = {"id": "one", "due": "2026-08-15", "every": 0,
            "lead": 0, "paid": [{"for": "2026-08-15", "on": "2026-08-15"}]}
    assert bills.due_info(bill, "2026-08-15")["due"] is None
    app = bills.Bills.__new__(bills.Bills)
    app.sel = "one"
    app._bill = lambda *_args: bill
    items = app.menu_items("Bill")
    assert items[0][0] == "Record Payment…" and items[0][1] is None
    bill["paid"] = []
    assert app.menu_items("Bill")[0][1] is not None
    print("PASS settled one-off bills cannot record occurrence-less payments")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
