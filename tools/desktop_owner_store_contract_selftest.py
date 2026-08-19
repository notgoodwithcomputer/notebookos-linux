#!/usr/bin/env python3
"""Desktop tiles honor repaired/legacy store shapes their owning apps read."""

import json
from pathlib import Path
import sys
import tempfile
import time
import types
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import academics  # noqa: E402
import accounting  # noqa: E402
import widgets  # noqa: E402


def accounting_read(payload):
    with tempfile.TemporaryDirectory(prefix="accounting-widget-") as td:
        path = Path(td) / "accounting.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        owner = types.SimpleNamespace()
        owner._num = accounting.Accounting._num
        owner._parse_tx = types.MethodType(accounting.Accounting._parse_tx, owner)
        board = types.SimpleNamespace(_as_records=widgets.Widgets._as_records)
        with mock.patch.object(accounting, "TX_FILE", str(path)), \
                mock.patch.object(widgets, "ACCOUNTING_FILE", str(path)):
            state = accounting.Accounting._load_state(owner)
            tile = widgets.Widgets._read_accounting(board)
    return state, tile


def academics_board(path):
    board = types.SimpleNamespace()
    board._academics_store = lambda: str(path)
    board._read_store = widgets.Widgets._read_store
    board._as_records = widgets.Widgets._as_records
    board._when = widgets.Widgets._when
    board.events = []
    board._calendar_colors = lambda: {}
    return board


def main():
    tx = {"date": "15 Aug", "desc": "Pay", "amt": 123.45}
    for label, payload in (
            ("wrapped ledger", {"tx": [tx], "opening": 10}),
            ("bare-list ledger", [tx]),
            ("id-keyed ledger", {"tx": {"line-1": tx}})):
        state, tile = accounting_read(payload)
        assert len(state["tx"]) == 1, (label, state)
        assert tile is not None and tile[0] in ("$123.45", "$133.45"), \
            (label, tile)
        print("PASS " + label)

    today = time.localtime()
    due = "%04d-%02d-%02d" % (today.tm_year, today.tm_mon, today.tm_mday)
    cls = {"name": "Math", "meets": [{
        "day": today.tm_wday, "start": "09:00", "end": "10:00"}]}
    homework = {"title": "Problem set", "due": due, "done": False}
    with tempfile.TemporaryDirectory(prefix="academics-widget-") as td:
        path = Path(td) / "academics.json"
        path.write_text(json.dumps({
            "classes": {"math": cls},
            "homework": {"hw-1": homework},
        }), encoding="utf-8")
        board = academics_board(path)
        assert len(academics._records({"math": cls})) == 1
        schedule = widgets.Widgets._read_schedule_model(board, today)
        homework_tile = widgets.Widgets._read_homework(board)
    assert schedule is not None and schedule[0][0]["name"] == "Math", schedule
    assert homework_tile is not None and homework_tile[1][0][1] == "Problem set", \
        homework_tile
    print("PASS id-keyed Academics classes and homework")
    print("desktop / owner store contracts: PASS")
    print("RESULT: 4 checks, ALL PASS")


if __name__ == "__main__":
    main()
