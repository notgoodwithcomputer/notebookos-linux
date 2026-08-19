#!/usr/bin/env python3
"""Headless forward-compatibility check for emulator UI metadata."""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import gbaemu  # noqa: E402

tmp = tempfile.mkdtemp(prefix="gbaemu-meta-")
gbaemu.CFG_DIR = tmp
gbaemu.CFG_PATH = os.path.join(tmp, "gbaemu.json")
future = {
    "version": 2,
    "cloud_profile": {"provider": "future", "sync": True},
    "games": {"abc": {"last_slot": 2, "future_note": "keep"}},
}
with open(gbaemu.CFG_PATH, "w", encoding="utf-8") as fh:
    json.dump(future, fh)

app = gbaemu.GbaEmu.__new__(gbaemu.GbaEmu)
app._state_meta = app._load_state_meta()
app._game_state("/missing/game.gba")["last_slot"] = 3
saved_ok = app._save_state_meta()
with open(gbaemu.CFG_PATH, encoding="utf-8") as fh:
    saved = json.load(fh)

ok = (saved_ok and saved.get("cloud_profile") == future["cloud_profile"]
      and saved["games"]["abc"].get("future_note") == "keep")
print(("PASS" if ok else "FAIL")
      + " unknown emulator metadata survives a normal save")

real_write = gbaemu.nbapp.atomic_write_json
real_note = gbaemu.nbapp.note_save_failure
notices = []
updates = []
gbaemu.nbapp.atomic_write_json = lambda *_a, **_k: (_ for _ in ()).throw(
    OSError("disk full"))
gbaemu.nbapp.note_save_failure = lambda *_a: notices.append(True)
app._update_slot_widgets = lambda path: updates.append(path)
before = app._game_state("/missing/game.gba")["last_slot"]
app._select_slot("/missing/game.gba", 2 if before != 2 else 1)
rolled_back = (app._game_state("/missing/game.gba")["last_slot"] == before
               and notices == [True] and updates == ["/missing/game.gba"])
print(("PASS" if rolled_back else "FAIL")
      + " failed slot persistence is visible and rolls selection back")
gbaemu.nbapp.atomic_write_json = real_write
gbaemu.nbapp.note_save_failure = real_note

# Real widgets: _update_slot_widgets now lights the slots through
# nbapp.choose_segment, which blocks the buttons' GObject signals — a fake
# object with a bare .active attribute has no signals to look up. The slots
# are a radio group in the app; build one here so the state check is honest.
path = "/missing/game.gba"
buttons = {}
_grp = None
for slot in gbaemu.STATE_SLOTS:
    rb = Gtk.RadioButton.new_from_widget(_grp)
    if _grp is None:
        _grp = rb
    buttons[slot] = rb
keys, last = Gtk.Label(), Gtk.Label()
app._slot_widgets = {path: {"buttons": buttons, "keys": keys, "last": last}}
app._update_slot_widgets = gbaemu.GbaEmu._update_slot_widgets.__get__(app)
app._save_state_meta = lambda: True
app._render_library = lambda: (_ for _ in ()).throw(
    AssertionError("slot selection rebuilt the library"))
app._select_slot(path, 2)
in_place = (buttons[2].get_active() is True and buttons[1].get_active() is False
            and "F2" in keys.get_text() and last.get_text())
print(("PASS" if in_place else "FAIL")
      + " slot selection updates controls without rebuilding or losing focus")

all_ok = ok and rolled_back and in_place
print("RESULT: %s" % ("PASS" if all_ok else "FAILED"))
raise SystemExit(not all_ok)
