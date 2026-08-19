#!/usr/bin/env python3
"""Locally appended menu rows stay visible to conformance checks."""

import ast
import menu_conformance_check as gate


def main():
    fn = ast.parse("""
def menu_items(self, name):
    rows = []
    rows.append(('save as', self._save_as))
    rows.extend([('Open', self._open), nbcommands.item('file.save_as')])
    return rows
""").body[0]
    rows = gate.labels_in_menu_method(fn)
    labels = [row[1] for row in rows]
    if "save as" not in labels or "Open" not in labels:
        print("FAIL: appended literal menu rows were not resolved")
        return 1
    save_rows = [row for row in rows if row[2] == "file.save_as"]
    if not save_rows:
        print("FAIL: appended registry menu row was not resolved")
        return 1
    scratch = ast.parse("""
def menu_items(self, name):
    rows = []
    preview = []
    preview.append(('save as', self._save_as))
    return rows
""").body[0]
    if gate.labels_in_menu_method(scratch):
        print("FAIL: an unreturned scratch list became visible menu content")
        return 1
    print("PASS: append/extend literal and registry rows are inventoried")
    print("PASS: unreturned scratch-list rows are ignored")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
