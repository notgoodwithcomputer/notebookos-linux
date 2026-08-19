#!/usr/bin/env python3
"""Regression: editing Contacts does not strip phone/email metadata."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import contacts  # noqa: E402


def main():
    original = [{"label": "mobile", "value": "555-0100",
                 "preferred": True, "sync_id": "phone-7"}]
    same = contacts.parse_labeled_text(
        contacts.labeled_text(original), "mobile")
    saved = contacts.merge_labeled_values(original, same)
    assert saved[0]["preferred"] is True
    assert saved[0]["sync_id"] == "phone-7"
    changed = contacts.merge_labeled_values(
        original, [{"label": "work", "value": "555-0199"}])
    assert changed[0] == {"label": "work", "value": "555-0199",
                          "preferred": True, "sync_id": "phone-7"}
    added = contacts.merge_labeled_values(
        original, same + [{"label": "home", "value": "555-0111"}])
    assert added[1] == {"label": "home", "value": "555-0111"}
    print("PASS phone/email metadata survives no-op and changed edits")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
