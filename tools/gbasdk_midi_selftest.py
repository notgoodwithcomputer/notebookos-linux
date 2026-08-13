#!/usr/bin/env python3
"""Headless MIDI -> existing GBA SDK tracker resource checks.

GBASDK_MODULE_DIR points mutation runs at a scratch copy of de/.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
MODULE_DIR = Path(os.environ.get("GBASDK_MODULE_DIR", DEFAULT_DE))
sys.path.insert(0, str(MODULE_DIR))

import composer
import gbasdk

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name +
          ("" if ok else "   <- " + str(detail)))


def note(start, duration, pitch, velocity=100):
    return {"start": start, "duration": duration,
            "pitch": pitch, "velocity": velocity}


def exported(tracks, tempo=120):
    song = composer.new_song()
    song["tempo"] = tempo
    song["tracks"] = tracks
    return composer.midi_export(song)


melody = composer.new_track("Melody")
melody["notes"] = [note(0, 120, 60), note(480, 120, 64)]
bass = composer.new_track("Bass")
bass["notes"] = [note(240, 120, 36), note(720, 120, 43)]
drums = composer.new_track("Drums", percussion=True)
drums["notes"] = [note(0, 120, 36), note(480, 120, 38)]
sound, report = gbasdk._midi_to_sound(exported([melody, bass, drums]), "Band")
check("Composer format-1 populates lead, bass and noise voices",
      report["voices"] == 3 and all(any(sound[k]) for k in
                                    ("lead", "bass", "drum")), report)
check("sixteenth rows keep specific Composer notes",
      sound["lead"][0] == 60 and sound["lead"][4] == 64 and
      sound["bass"][2] == 48 and sound["bass"][6] == 55 and
      sound["drum"][0] == 1 and sound["drum"][4] == 2, sound)
check("the imported object is the normal tracker resource shape",
      set(("tempo", "steps", "lead", "bass", "drum", "kind", "duty",
           "vol", "decay", "prio")).issubset(sound) and "tracks" not in sound)


class Undo:
    def __init__(self): self.events = []
    def checkpoint(self, label): self.events.append(("checkpoint", label))
    def commit(self): self.events.append(("commit", None))


fd, midi_path = tempfile.mkstemp(suffix=".mid")
try:
    with os.fdopen(fd, "wb") as target:
        target.write(exported([melody, bass, drums]))
    app = gbasdk.GbaSdk.__new__(gbasdk.GbaSdk)
    app.proj = {"sounds": []}
    app.undo = Undo()
    app._save_autosave = lambda: None
    app._render_tree = lambda: None
    selected = []
    flashed = []
    app._select_resource = lambda kind, index: selected.append((kind, index))
    app._flash = flashed.append
    real_picker = gbasdk.nbpicker.open_file
    gbasdk.nbpicker.open_file = lambda *_args, **_kwargs: midi_path
    try:
        app._import_midi()
    finally:
        gbasdk.nbpicker.open_file = real_picker
    check("Import MIDI creates one undoable normal project resource",
          len(app.proj["sounds"]) == 1 and
          app.undo.events[0][0] == "checkpoint" and
          app.undo.events[-1][0] == "commit" and selected == [("sound", 0)],
          (app.undo.events, selected))
    check("Import MIDI shows the conversion summary in app status",
          len(flashed) == 1 and "voices used" in flashed[0], flashed)
finally:
    os.unlink(midi_path)

chord = composer.new_track("Chord")
chord["notes"] = [note(0, 120, 60), note(0, 120, 64), note(0, 120, 67)]
third = composer.new_track("Overflow")
third["notes"] = [note(240, 120, 72)]
_chord_sound, chord_report = gbasdk._midi_to_sound(
    exported([chord, bass, third]), "Reduction")
check("chords and overflow drop in stable allocation order",
      [(r["kept"], r["dropped"]) for r in chord_report["tracks"]] ==
      [(1, 2), (2, 0), (0, 1)], chord_report["tracks"])
check("the honest summary carries per-track kept and dropped counts",
      "Chord: 1 kept/2 dropped" in chord_report["summary"] and
      "Overflow: 0 kept/1 dropped" in chord_report["summary"],
      chord_report["summary"])

# A deliberately plain third-party-style format-0 file: no Composer private
# meta, one track, running status for the second note-on.
body = (b"\x00\xff\x51\x03\x07\xa1\x20" +
        b"\x00\x90\x3c\x64" + composer.vlq_encode(120) + b"\x3e\x64" +
        composer.vlq_encode(120) + b"\x80\x3c\x00" +
        b"\x00\x3e\x00\x00\xff\x2f\x00")
foreign = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) +
           b"MTrk" + struct.pack(">I", len(body)) + body)
foreign_sound, foreign_report = gbasdk._midi_to_sound(foreign, "Foreign")
check("foreign format-0 running status imports", foreign_report["kept"] == 2 and
      foreign_sound["lead"][:2] == [60, 62], foreign_sound["lead"])

try:
    gbasdk._midi_to_sound(b"MThd\x00", "Broken")
except Exception as exc:
    check("garbage hits _MidiUnsupported by name",
          type(exc).__name__ == "_MidiUnsupported" and
          "readable MIDI" in str(exc), repr(exc))
else:
    check("garbage hits _MidiUnsupported by name", False, "no refusal")

extremes = composer.new_track("Extremes")
extremes["notes"] = [note(0, 120, 24), note(240, 120, 108)]
extreme_sound, extreme_report = gbasdk._midi_to_sound(
    exported([extremes]), "Octaves")
check("out-of-range pitches transpose by octaves into the playable roll",
      extreme_sound["lead"][0] == 48 and extreme_sound["lead"][2] == 72 and
      extreme_report["transposed"] == 2, extreme_sound["lead"])

tempo_sound, _tempo_report = gbasdk._midi_to_sound(
    exported([melody], tempo=150), "Tempo")
check("MIDI tempo maps to tracker frames per sixteenth row",
      tempo_sound["tempo"] == 6, tempo_sound["tempo"])


def mutant_check():
    if os.environ.get("GBASDK_MIDI_MUTANT"):
        return
    scratch_root = ROOT / ".codex-scratch"
    scratch_root.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="gbasdk-midi-mutant-", dir=scratch_root))
    try:
        for source in MODULE_DIR.glob("*.py"):
            shutil.copy2(source, tmp / source.name)
        target = tmp / "gbasdk.py"
        source = target.read_text()
        needle = 'melodic = iter(("lead", "bass"))'
        if needle not in source:
            check("PASS-MUTANT can locate voice allocation order", False, needle)
            return
        target.write_text(source.replace(
            needle, 'melodic = iter(("bass", "lead"))', 1))
        env = dict(os.environ, GBASDK_MODULE_DIR=str(tmp),
                   GBASDK_MIDI_MUTANT="1")
        run = subprocess.run([sys.executable, __file__], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check("PASS-MUTANT scrambled allocation makes a named check red",
              run.returncode != 0 and
              "FAIL sixteenth rows keep specific Composer notes" in run.stdout,
              run.stdout[-1200:])
    finally:
        shutil.rmtree(tmp)


mutant_check()
passed = sum(results)
print("\n%d/%d checks passed" % (passed, len(results)))
raise SystemExit(0 if passed == len(results) else 1)
