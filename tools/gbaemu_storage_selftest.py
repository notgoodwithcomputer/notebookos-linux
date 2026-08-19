#!/usr/bin/env python3
"""Headless durable-storage checks for ROMs on read-only/removable media."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import gbaemu  # noqa: E402


with tempfile.TemporaryDirectory(prefix="gbaemu-storage-") as root:
    gbaemu.GAME_DATA_DIR = os.path.join(root, "user-data")
    romdir = os.path.join(root, "media"); os.makedirs(romdir)
    rom = os.path.join(romdir, "game.gba")
    open(rom, "wb").write(b"rom")
    legacy = rom + "1.sgm"; open(legacy, "wb").write(b"state")
    dest = gbaemu.prepare_game_storage(rom)
    state = gbaemu.state_path(rom, 1)
    checks = [
        (state.startswith(dest + os.sep), "state path lives in user storage"),
        (open(state, "rb").read() == b"state",
         "legacy state is copied into durable storage"),
    ]

    # Durable identity follows cartridge bytes across a Finder rename, and
    # prepare_game_storage also gives VBA-M the basename-derived filenames it
    # will request after that rename.
    old_dest = dest
    renamed = os.path.join(romdir, "game renamed.gba")
    os.rename(rom, renamed)
    gbaemu._identity_cache = None
    new_dest = gbaemu.prepare_game_storage(renamed)
    renamed_state = gbaemu.state_path(renamed, 1)
    checks.extend([
        (new_dest == old_dest, "renamed ROM keeps content-addressed storage"),
        (os.path.exists(renamed_state)
         and open(renamed_state, "rb").read() == b"state",
         "renamed ROM keeps VBA-M slot data under its new basename"),
    ])

source = open(os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                           "opt/notebook/de/gbaemu.py"), encoding="utf-8").read()
checks.append(('extra_args=["--save-dir", save_dir,' in source
               and '"--battery-dir", save_dir]' in source,
               "emulator launch routes state and battery writes to that storage"))
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
# Terminal verdict for the release runner (run_all_gates SUCCESSWORD): a stream
# of PASS lines with a zero exit is not a report it will trust — a suite that
# dies half way prints those too.
_ok = all(ok for ok, _name in checks)
print("RESULT: %s" % ("ALL PASS" if _ok else "FAILED"))
raise SystemExit(not _ok)
