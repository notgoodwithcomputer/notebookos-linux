#!/usr/bin/env python3
"""Mandarin homophones must not share learned/strength state."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import language  # noqa: E402


def main() -> None:
    do = {"t": "zuò", "e": "to do", "note": "做", "phrase": False}
    ride = {"t": "zuò", "e": "to travel by", "note": "坐", "phrase": False}
    course = {"code": "zh", "units": [{"skills": [
        {"words": [do], "phrases": []},
        {"words": [ride], "phrases": []},
    ]}]}
    app = language.Language.__new__(language.Language)
    app.courses = [course]
    app.course = course
    app.progress = {"seen": [], "strength": {}}

    do_key = app._item_skey("zh", do)
    ride_key = app._item_skey("zh", ride)
    assert do_key != ride_key
    app.progress["seen"] = [do_key]
    seen = set(app.progress["seen"])
    assert app._item_progress_key("zh", do, seen) in seen
    assert app._item_progress_key("zh", ride, seen) not in seen

    legacy = app._skey("zh", "zuò")
    app.progress["seen"] = [legacy]
    seen = set(app.progress["seen"])
    assert app._item_progress_key("zh", do, seen) not in seen
    assert app._item_progress_key("zh", ride, seen) not in seen

    app.progress["strength"] = {do_key: {"s": 2, "t": 0}}
    app._bump_strength("zuò", True, ride_key)
    assert app.progress["strength"][do_key]["s"] == 2
    assert app.progress["strength"][ride_key]["s"] == 1

    print("PASS Mandarin homophones have independent seen and strength identity")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
