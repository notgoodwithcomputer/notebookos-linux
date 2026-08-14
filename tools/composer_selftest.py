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
    before = editor.snapshot(); editor.add_note(0, 480, 60, 90)
    check("staff palette add applies its exact duration",
          editor.song["tracks"][0]["notes"][0]["duration"] == 480)
    check("Undo Add Note restores the complete song", history.undo() and editor.song == before)
    history.redo(); editor.selection = {0}; before = editor.snapshot(); editor.move_selected(480, 2)
    check("move selection changes tick and pitch",
          editor.song["tracks"][0]["notes"][0]["start"] == 480 and
          editor.song["tracks"][0]["notes"][0]["pitch"] == 62)
    check("Undo Move Notes restores the complete song", history.undo() and editor.song == before)
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

    class Staff:
        def queue_draw(self): pass
    class Fake:
        pass
    fake = Fake(); fake.editor = editor; fake.staff = Staff(); fake.snap = 120
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

    # Typing owns the keys. The Tempo box is a Gtk.SpinButton — an Editable
    # in the same toplevel — and the window handler used to fire first:
    # Delete deleted the SELECTED NOTES under a tempo edit, arrows moved
    # the selection instead of the caret, Space played the piece instead
    # of typing. With an editable focused, each must fall through.
    entry = composer.Gtk.Entry()
    fake2 = Fake(); fake2.editor = editor; fake2.staff = Staff(); fake2.snap = 120
    fake2.get_focus = lambda: entry
    deleted, played = [], []
    fake2._delete = lambda: deleted.append(1)
    fake2._play = lambda: played.append(1)
    fake2.changed = lambda: None    # the unguarded arrow branch calls it
    editor.selection = {0}
    ev = lambda kv: type("Event", (), {"keyval": kv, "state": 0})()
    before = editor.snapshot()
    r1 = composer.Composer._key(fake2, None, ev(composer.Gdk.KEY_Delete))
    r2 = composer.Composer._key(fake2, None, ev(composer.Gdk.KEY_Left))
    r3 = composer.Composer._key(fake2, None, ev(composer.Gdk.KEY_space))
    check("Delete while editing a number stays in the field (notes survive)",
          r1 is False and not deleted and editor.song == before)
    check("arrows while editing move the caret, not the selection",
          r2 is False and editor.song == before)
    check("Space while editing types a space, not playback",
          r3 is False and not played)
    fake2.get_focus = lambda: None
    r4 = composer.Composer._key(fake2, None, ev(composer.Gdk.KEY_Delete))
    check("Delete with no field focused still deletes the selection",
          r4 is True and bool(deleted))


def notation_checks():
    check("treble pitch maps C4 below and E4 onto bottom line",
          composer.staff_step(60, "treble") == -2 and composer.staff_step(64, "treble") == 0)
    check("bass pitch maps C2 ledger and G2 onto bottom line",
          composer.staff_step(36, "bass") == -4 and composer.staff_step(43, "bass") == 0)
    check("sharp pitch keeps the natural staff position",
          composer.staff_step(61, "treble") == composer.staff_step(60, "treble") and composer.is_sharp(61))
    high = composer.staff_step(84, "treble"); low = composer.staff_step(24, "bass")
    check("ledger cases extend above treble and below bass", high > 8 and low < 0)
    song = composer.new_song(); tr = song["tracks"][0]
    tr["notes"] = [{"start": 0, "duration": 480, "pitch": 71, "velocity": 90},
                   {"start": 480, "duration": 480, "pitch": 47, "velocity": 90}]
    check("median pitch chooses treble at 60 and bass below",
          composer.clef_for_track(tr) == "bass")
    tr["notes"].append({"start": 960, "duration": 480, "pitch": 72, "velocity": 90})
    check("median pitch threshold chooses treble", composer.clef_for_track(tr) == "treble")
    drums = composer.new_track("Drums", percussion=True)
    check("percussion track chooses percussion staff", composer.clef_for_track(drums) == "percussion")

    expected = {1920: ("whole", False), 2880: ("whole", True),
                960: ("half", False), 1440: ("half", True),
                480: ("quarter", False), 720: ("quarter", True),
                240: ("eighth", False), 360: ("eighth", True),
                120: ("sixteenth", False), 180: ("sixteenth", True)}
    check("duration glyph map covers whole through sixteenth and dots",
          all(composer.duration_glyph(t)[:2] == glyph for t, glyph in expected.items()))
    foreign = {"start": 37, "duration": 317, "pitch": 61, "velocity": 73}
    model = copy.deepcopy(foreign); glyph = composer.duration_glyph(foreign["duration"])
    check("nearest display quantization never mutates model",
          glyph == ("eighth", True, 360) and foreign == model)
    rests = composer.measure_rests([{"start": 480, "duration": 480}], 0, 1920)
    check("rests derive in both gaps of a partly occupied measure",
          rests[0][0:2] == (0, 480) and sum(r[1] for r in rests) == 1440)
    check("empty measure derives a whole rest",
          composer.measure_rests([], 0, 1920) == [(0, 1920, "whole", False)])

    pixel_song = composer.new_song(); pixel_song["tracks"][0]["notes"] = [
        {"start": 480, "duration": 480, "pitch": 64, "velocity": 90}]
    surface, staff = composer.render_staff_surface(pixel_song, 900, 230)
    data = bytes(surface.get_data()); stride = surface.get_stride()
    def dark(x, y):
        b, g, r, _a = data[y*stride+x*4:y*stride+x*4+4]
        return r < 220 and g < 220 and b < 220
    sy = staff._staff_y(0); expected_y = int(staff._note_y(0, 64)); expected_x = int(staff._tick_x(480))
    check("rendered staff pixel probe finds all five staff lines",
          all(any(dark(x, sy + line*staff.SPACE) for x in range(120, 180)) for line in range(5)))
    check("rendered quarter head lands on expected line-space y",
          any(dark(x, y) for x in range(expected_x-5, expected_x+6) for y in range(expected_y-4, expected_y+5)))
    check("rendered bar lines land at measure boundaries",
          dark(staff.LEFT, sy) and dark(staff.LEFT + staff.MEASURE_W, sy))

    app = composer.SongEditor(composer.new_song())
    # Staff coordinate conversion produces C4; insertion applies palette and snap.
    sy = composer.StaffNotation.TOP + 48
    y = sy + (4 - composer.staff_step(60, "treble")/2) * composer.StaffNotation.SPACE
    raw_tick = 130; snapped = int(round(raw_tick / 120)) * 120
    step = int(round((sy + 4*composer.StaffNotation.SPACE - y) / (composer.StaffNotation.SPACE/2)))
    pitch = composer.pitch_for_step(composer.diatonic_number(64) + step, False)
    app.add_note(snapped, 480, pitch)
    check("staff click inserts snapped pitch-time and palette duration",
          app.song["tracks"][0]["notes"] == [{"start": 120, "duration": 480, "pitch": 60, "velocity": 96}])
    app.history = composer.nbapp.UndoHistory(app.snapshot, app.restore); app.history.reset()
    before = app.snapshot(); app.selection = {0}; app.move_selected(120, 0)
    check("staff horizontal edit participates in complete undo",
          app.history.undo() and app.song == before)
    app.history.redo(); app.song["tracks"][0]["notes"][0]["pitch"] = 61; app.selection = {0}; app.history.reset()
    before = app.snapshot(); app.move_selected_diatonic(0, 1)
    check("staff vertical drag moves diatonically and preserves sharp",
          app.song["tracks"][0]["notes"][0]["pitch"] == 63)
    check("staff diatonic drag has a complete undo checkpoint",
          app.history.undo() and app.song == before)


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


def notation_mutant_check():
    """A scratch module with collapsed staff geometry must be rejected."""
    root = tempfile.mkdtemp(prefix="composer-notation-mutant-")
    try:
        mutant_dir = Path(root) / "de"; mutant_dir.mkdir()
        source = (MODULE_DIR / "composer.py").read_text(encoding="utf-8")
        source = source.replace("return diatonic_number(pitch) - diatonic_number(64 if clef == \"treble\" else 43)",
                                "return 0  # MUTANT: collapse every pitch onto one line")
        (mutant_dir / "composer.py").write_text(source, encoding="utf-8")
        probe = "import composer,sys; sys.exit(0 if composer.staff_step(60,'treble') == -2 and composer.staff_step(64,'treble') == 0 else 9)"
        env = dict(os.environ, COMPOSER_MODULE_DIR=str(mutant_dir),
                   PYTHONPATH=str(mutant_dir) + os.pathsep + str(DE))
        run = subprocess.run([sys.executable, "-c",
                              "import sys,os; d=os.environ['COMPOSER_MODULE_DIR']; de=os.environ['PYTHONPATH'].split(os.pathsep)[1]; sys.path.insert(0,de); sys.path.insert(0,d); " + probe],
                             env=env, capture_output=True, text=True, timeout=30)
        check("MUTANT: collapsed staff pitch geometry is rejected by name", run.returncode == 9,
              run.stderr.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in (("MIDI checks", midi_checks), ("editing checks", editing_checks),
                     ("notation checks", notation_checks), ("store checks", store_checks),
                     ("mutant checks", mutant_check), ("notation mutant checks", notation_mutant_check)):
        try: fn()
        except Exception as exc: check(name + " completes by name", False, repr(exc))
    print("%d checks, %d failed" % (CHECKS, len(FAILS)))
    if FAILS: print("FAILED: " + ", ".join(FAILS))
    raise SystemExit(1 if FAILS else 0)
