#!/usr/bin/env python3
"""Recovered app-store shapes remain truthful on their desktop tiles."""

import json
import math
from pathlib import Path
import sys
import tempfile
import time
import types
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import language  # noqa: E402
import widgets  # noqa: E402


def store(payload, callback, attr):
    with tempfile.TemporaryDirectory(prefix="desktop-recovery-") as td:
        path = Path(td) / "store.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(widgets, attr, str(path)):
            return callback()


def board():
    return types.SimpleNamespace(
        _read_store=widgets.Widgets._read_store,
        _read_records=widgets.Widgets._read_records,
        _as_list=widgets.Widgets._as_list,
        _when=widgets.Widgets._when,
        _journal_ordinal=widgets.Widgets._journal_ordinal,
    )


def main():
    now = time.localtime()
    birthday = "%d %s" % (now.tm_mday, time.strftime("%B", now))
    contact = {"name": "Ada", "bday": birthday}
    for label, payload in (
            ("keyed contacts", {"people": {"ada": contact}}),
            ("bare contacts", [contact]),
            ("renamed contacts wrapper", {"cards": [contact]})):
        tile = store(payload,
                     lambda: widgets.Widgets._read_birthdays(board()),
                     "CONTACTS_FILE")
        assert tile is not None and tile[1][0][1] == "Ada", (label, tile)
        print("PASS " + label)

    book = {"title": "Middlemarch", "path": "/tmp/m.epub",
            "fmt": "epub", "frac": 0.5}
    for label, payload in (
            ("keyed shelf", {"books": {"m": book}}),
            ("bare shelf", [book]),
            ("renamed shelf wrapper", {"library": [book]})):
        tile = store(payload, lambda: widgets.Widgets._read_reading(board()),
                     "EBOOK_FILE")
        assert tile is not None and tile[1][0][1:3] == ("Middlemarch", "50%"), \
            (label, tile)
        print("PASS " + label)

    for bad in (float("nan"), float("inf"), float("-inf")):
        damaged_book = dict(book, frac=bad)
        tile = store({"books": [damaged_book]},
                     lambda: widgets.Widgets._read_reading(board()),
                     "EBOOK_FILE")
        assert tile is not None and tile[1][0][2] == "0%", (bad, tile)
    print("PASS non-finite Ebook progress")

    entry = {"day": str(now.tm_mday),
             "month_label": time.strftime("%B %Y", now),
             "title": "Today", "body": "Words"}
    for label, payload in (
            ("keyed journal", {"entries": {"today": entry}}),
            ("bare journal", [entry]),
            ("renamed journal wrapper", {"pages": [entry]})):
        tile = store(payload, lambda: widgets.Widgets._read_journal(board()),
                     "JOURNAL_FILE")
        assert tile is not None and tile[4]["done"] is True, (label, tile)
        print("PASS " + label)

    progress = {"streak": "3", "day_xp": "20",
                "day": time.strftime("%Y-%m-%d"), "goal": 20,
                "crowns": {"en:0:0": 1}}
    tile = store(progress, lambda: widgets.Widgets._read_language(board()),
                 "LANGUAGE_FILE")
    normalized = language.Language.norm_progress(progress)
    assert normalized["streak"] == 3 and normalized["day_xp"] == 20
    assert tile[0] == "3 day streak" and tile[4]["done"] is True, tile
    print("PASS Language numeric-string progress")
    for key in ("xp", "streak", "day_xp", "hearts", "heart_time"):
        for bad in (float("nan"), float("inf"), float("-inf")):
            normalized = language.Language.norm_progress({key: bad})
            assert math.isfinite(normalized[key]), (key, bad, normalized)
    print("PASS non-finite Language progress")
    print("RESULT: PASS (desktop recovery shapes)")


if __name__ == "__main__":
    main()
