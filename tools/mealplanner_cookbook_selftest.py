#!/usr/bin/env python3
"""Cross-app recipe-store compatibility between Cookbook and Meal Planner."""

import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import mealplanner  # noqa: E402


def titles(recipes):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "cookbook.json"
        path.write_text(json.dumps({"recipes": recipes}), encoding="utf-8")
        return mealplanner.read_recipe_titles(str(path))


def main():
    records = [
        {"title": "Soup", "steps": "Simmer"},
        {"title": " Bread ", "steps": "Bake"},
        {"title": ""},
        "damaged-record",
    ]
    expected = ["Soup", "Bread"]
    assert titles(records) == expected
    print("PASS list-shaped Cookbook store")
    keyed = {"Soup": records[0], "Bread": records[1],
             "empty": records[2], "damaged": records[3]}
    assert titles(keyed) == expected
    print("PASS title-keyed Cookbook store")
    assert titles(None) == []
    print("PASS wrong-shaped Cookbook store")
    # Terminal verdict for the release runner (run_all_gates SUCCESSWORD): a
    # stream of PASS lines with a zero exit is not a report it will trust —
    # a suite that dies half way prints those too.
    print("meal planner / cookbook contract: PASS")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
