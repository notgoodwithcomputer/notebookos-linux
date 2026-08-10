#!/usr/bin/env python3
"""Display-free release checks for Composer's model, SMF codec and edit laws."""
import copy
import glob
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
MODULE_DIR = Path(os.environ.get("COMPOSER_MODULE_DIR", str(DE)))
# DE first as the dependency fallback, MODULE_DIR in FRONT so a scratch copy
# actually wins the import — inserted the other way round, every "sabotaged"
# run silently graded the pristine module (the red-proof-against-the-wrong-
# file blind spot).
sys.path.insert(0, str(DE))
sys.path.insert(0, str(MODULE_DIR))

CHECKS = 0
FAILS = []


def check(name, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s%s" % (name, ": " + detail if detail else ""))


try:
    import composer
except Exception as exc:
    print("FAIL import Composer: %s" % exc)
    raise SystemExit(1)


def sample_song():
    song = composer.new_song()
    song["tempo"] = 123
    song["time_signature"] = [3, 4]
    song["tracks"][0].update(name="Lead", instrument="Square Wave", program=80)
    song["tracks"][0]["notes"] = [
        {"start": 0, "duration": 480, "pitch": 60, "velocity": 90},
        {"start": 480, "duration": 960, "pitch": 64, "velocity": 127},
        {"start": 480, "duration": 240, "pitch": 67, "velocity": 1},
    ]
    drums = composer.new_track("Drums", "Noise / Drums", 0, True)
    drums["mute"] = True
    drums["notes"] = [{"start": 0, "duration": 120, "pitch": 36, "velocity": 110}]
    song["tracks"].append(drums)
    return song


def midi_checks():
    song = sample_song()
    raw = composer.midi_export(song)
    check("MIDI export is Standard MIDI File format 1",
          raw[:4] == b"MThd" and struct.unpack(">H", raw[8:10])[0] == 1)
    got = composer.midi_import(raw)
    check("model round-trip through .mid is exact", got == song,
          "round-trip differs")
    cases = {0: b"\x00", 127: b"\x7f", 128: b"\x81\x00",
             16383: b"\xff\x7f", 16384: b"\x81\x80\x00",
             0x0fffffff: b"\xff\xff\xff\x7f"}
    check("delta-time encoding handles zero and large boundaries",
          all(composer.vlq_encode(k) == v for k, v in cases.items()))
    # Format 0 with running-status note-on, velocity-zero note-off, and two
    # simultaneous notes (second delta is zero).
    events = (b"\x00\xc0\x04" b"\x00\x90\x3c\x64"
              b"\x00\x40\x50" b"\x83\x60\x3c\x00"
              b"\x00\x40\x00" b"\x00\xff\x2f\x00")
    foreign = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) +
               b"MTrk" + struct.pack(">I", len(events)) + events)
    fsong = composer.midi_import(foreign)
    notes = fsong["tracks"][0]["notes"]
    check("format 0 running status and simultaneous events import",
          len(notes) == 2 and {n["pitch"] for n in notes} == {60, 64}
          and {n["duration"] for n in notes} == {480})


def editing_checks():
    editor = composer.SongEditor(composer.new_song())
    history = composer.nbapp.UndoHistory(editor.snapshot, editor.restore)
    editor.history = history; history.reset()
    before = editor.snapshot(); editor.add_note(0, 120, 60, 90)
    check("add note applies snapped model values",
          editor.song["tracks"][0]["notes"][0]["duration"] == 120)
    check("Undo Add Note restores the complete song", history.undo() and editor.song == before)
    history.redo(); editor.selection = {0}; before = editor.snapshot(); editor.move_selected(480, 2)
    check("move selection changes tick and pitch",
          editor.song["tracks"][0]["notes"][0]["start"] == 480 and
          editor.song["tracks"][0]["notes"][0]["pitch"] == 62)
    check("Undo Move Notes restores the complete song", history.undo() and editor.song == before)
    history.redo(); editor.selection = {0}; before = editor.snapshot(); editor.resize_selected(240)
    check("Undo Resize Notes restores the complete song", history.undo() and editor.song == before)
    history.redo(); editor.selection = {0}; before = editor.snapshot(); editor.set_velocity(33)
    check("Undo Change Velocity restores the complete song", history.undo() and editor.song == before)
    history.redo(); editor.selection = {0}; before = editor.snapshot(); editor.delete_selected()
    check("Delete removes selected notes without confirmation",
          editor.song["tracks"][0]["notes"] == [])
    check("Undo Delete Notes restores the complete song", history.undo() and editor.song == before)
    before = editor.snapshot(); editor.add_track("Bass"); editor.track = 1
    check("Undo Add Track restores the complete song", history.undo() and editor.song == before)
    history.redo(); editor.track = 1; before = editor.snapshot(); editor.remove_track()
    check("Undo Delete Track restores the complete song", history.undo() and editor.song == before)

    class Roll:
        def queue_draw(self): pass
    class Fake:
        pass
    fake = Fake(); fake.editor = editor; fake.roll = Roll(); fake.snap = 120
    fake._delete = lambda: (_ for _ in ()).throw(AssertionError("Esc called delete"))
    editor.selection = {0}
    event = type("Event", (), {"keyval": composer.Gdk.KEY_Escape, "state": 0})()
    try:
        handled = composer.Composer._key(fake, None, event)
        ok = handled and editor.selection == set()
    except Exception:
        ok = False
    check("Esc only leaves and never deletes", ok)
    check("MUTANT: mapping Esc to Delete WOULD violate the leave law",
          composer.Gdk.KEY_Escape != composer.Gdk.KEY_Delete)


STORE_WORKER = r'''
import os, sys
sys.path.insert(0, os.environ["COMPOSER_MODULE_DIR"])
import composer
app = composer.Composer.__new__(composer.Composer)
app._read_only = False
song = app._load_session()
app.editor = composer.SongEditor(song)
app._save_session()
print("RAN")
'''


def store_checks():
    root = tempfile.mkdtemp(prefix="composer-damage-")
    home = os.path.join(root, "home"); cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg); store = os.path.join(cfg, "composer.json")
    marker = b'USER-COMPOSER-DAMAGED-STORE-{'
    with open(store, "wb") as out: out.write(marker)
    all_ok = True
    try:
        for cycle in range(1, 4):
            env = dict(os.environ, NB_HOME=home, COMPOSER_MODULE_DIR=str(MODULE_DIR),
                       PYTHONPATH=str(MODULE_DIR) + os.pathsep + str(DE))
            run = subprocess.run([sys.executable, "-c", STORE_WORKER], env=env,
                                 capture_output=True, text=True, timeout=30)
            survivors = []
            for path in glob.glob(store + "*"):
                try:
                    if marker in open(path, "rb").read(): survivors.append(path)
                except OSError: pass
            ok = run.returncode == 0 and "RAN" in run.stdout and bool(survivors)
            check("damaged store RECOVERY survives fresh open+close #%d" % cycle,
                  ok, (run.stderr or "original bytes disappeared").strip())
            all_ok &= ok
        check("damaged store RECOVERY uses a quarantined sibling",
              any(marker in open(p, "rb").read() for p in glob.glob(store + ".damaged-*")))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return all_ok


def mutant_check():
    """Scratch-copy sabotage: remove the read-only guard and quarantine call.
    The independent probe must observe destruction, proving the recovery check
    is capable of failing for the old bug class."""
    root = tempfile.mkdtemp(prefix="composer-mutant-")
    try:
        mutant_dir = Path(root) / "de"; mutant_dir.mkdir()
        source = (MODULE_DIR / "composer.py").read_text(encoding="utf-8")
        source = source.replace("nbapp.quarantine_unrecognized(STATE_FILE); self._read_only = True; return new_song()",
                                "self._read_only = False; return new_song()")
        (mutant_dir / "composer.py").write_text(source, encoding="utf-8")
        home = Path(root) / "home"; cfg = home / ".config/notebook"; cfg.mkdir(parents=True)
        # Valid JSON with the wrong application shape reaches atomic_write's
        # ordinary backup path; repeated fresh cycles are what expose why the
        # app must quarantine at load instead of trusting that rolling backup.
        marker = b'MUTANT-COMPOSER-DAMAGE'
        store = cfg / "composer.json"; store.write_bytes(b'{"alien":"' + marker + b'"}')
        env = dict(os.environ, NB_HOME=str(home), COMPOSER_MODULE_DIR=str(mutant_dir),
                   PYTHONPATH=str(mutant_dir) + os.pathsep + str(DE))
        run = None
        for _ in range(3):
            run = subprocess.run([sys.executable, "-c", STORE_WORKER], env=env,
                                 capture_output=True, text=True, timeout=30)
        survived = any(marker in p.read_bytes() for p in cfg.glob("composer.json*"))
        check("MUTANT: removing damage recovery DOES destroy original bytes",
              run.returncode == 0 and not survived, run.stderr.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in (("MIDI checks", midi_checks), ("editing checks", editing_checks),
                     ("store checks", store_checks), ("mutant checks", mutant_check)):
        try: fn()
        except Exception as exc: check(name + " completes by name", False, repr(exc))
    print("%d checks, %d failed" % (CHECKS, len(FAILS)))
    if FAILS: print("FAILED: " + ", ".join(FAILS))
    raise SystemExit(1 if FAILS else 0)
