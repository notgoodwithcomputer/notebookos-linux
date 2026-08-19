#!/usr/bin/env python3
"""Headless regression for Journal's localized Insert Date action."""
import ast
import os
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/journal.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_insert_date")

translations = {
    "%s, %d %s %d": "%s %d %s %d",
    "Friday": "Vendredi",
    "August": "Août",
}
namespace = {
    "time": types.SimpleNamespace(localtime=lambda: types.SimpleNamespace(
        tm_wday=4, tm_mday=15, tm_mon=8, tm_year=2026)),
    "WD_LONG": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday"],
    "MONTHS": ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November",
               "December"],
    "_t": lambda text: translations.get(text, text),
}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeJournal:
    _insert_date = namespace["_insert_date"]

    def __init__(self):
        self.inserted = None

    def _insert_at_cursor(self, text, label):
        self.inserted = (text, label)


journal = FakeJournal()
journal._insert_date()
assert journal.inserted == ("Vendredi 15 Août 2026", "Insert Date")
print("PASS Journal inserts a translated and reorderable long date")
print("RESULT: PASS")
