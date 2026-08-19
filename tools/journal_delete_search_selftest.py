#!/usr/bin/env python3
"""Deletion must reanchor only to an entry visible through the search."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import journal  # noqa: E402


def main():
    app = journal.Journal.__new__(journal.Journal)
    app._query = "alpha"
    app.entries = [{"date": "", "month_label": "", "title": "beta",
                    "text": "", "tags": []}]
    none = app._matching_index_near(0) is None
    app.entries.append({"date": "", "month_label": "", "title": "alpha two",
                        "text": "", "tags": []})
    visible = app._matching_index_near(0) == 1
    ok = none and visible
    print(("PASS" if ok else "FAIL") + ": delete reanchor follows visible matches")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
