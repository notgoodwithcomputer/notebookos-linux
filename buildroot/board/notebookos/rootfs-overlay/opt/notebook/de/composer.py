#!/usr/bin/env python3
"""Composer -- a compact staff-notation MIDI editor for Notebook OS.

The song/model, MIDI codec and edit operations deliberately do not depend on a
realised GTK window.  Besides making them straightforward to test, this keeps
the model ready for a later notation or tracker view without teaching those
views about notation pixels.
"""
import array
import copy
import json
import math
import os
import random
import struct
import subprocess
import tempfile
import time
import wave

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402
import cairo

import nbapp
import nbpicker
from nbi18n import _t

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    GST_OK = True
except (ImportError, ValueError):
    Gst, GST_OK = None, False

PPQ = 480
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STATE_FILE = os.path.join(CFG_DIR, "composer.json")
DOC_DIR = os.path.join(HOME, "Documents")

# name, General MIDI program, preview family.  Names are catalog keys.
INSTRUMENTS = (
    ("Piano", 0, "sine"), ("Electric Piano", 4, "sine"),
    ("Music Box", 10, "triangle"), ("Organ", 16, "square"),
    ("Guitar", 24, "triangle"), ("Bass", 32, "square"),
    ("Strings", 48, "triangle"), ("Choir", 52, "sine"),
    ("Brass", 61, "saw"), ("Saxophone", 65, "saw"),
    ("Flute", 73, "sine"), ("Synth Lead", 80, "square"),
    ("Saw Wave", 81, "saw"), ("Synth Pad", 88, "triangle"),
    ("FX", 98, "sine"), ("Noise / Drums", 0, "noise"),
)


def _cap_combo_cells(combo, chars):
    """Ellipsize a combo's BUTTON cell so the longest translated entry cannot
    set the window's minimum width; the popup still shows every name whole."""
    for cell in combo.get_cells():
        cell.props.ellipsize = Pango.EllipsizeMode.END
        cell.props.max_width_chars = chars


def new_track(name="Track 1", instrument="Piano", program=0,
              percussion=False):
    return {"name": str(name), "instrument": str(instrument),
            "program": max(0, min(127, int(program))),
            "percussion": bool(percussion), "mute": False, "notes": []}


def new_song():
    return {"version": 1, "ppq": PPQ, "tempo": 120,
            "time_signature": [4, 4], "tracks": [new_track(_t("Track 1"))]}


def normalize_song(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("unrecognized Composer song")
    tempo = value.get("tempo")
    sig = value.get("time_signature")
    tracks = value.get("tracks")
    if (isinstance(tempo, bool) or not isinstance(tempo, (int, float)) or
            not 20 <= tempo <= 400 or not isinstance(sig, list) or
            len(sig) != 2 or sig[0] not in range(1, 33) or
            sig[1] not in (1, 2, 4, 8, 16, 32) or
            not isinstance(tracks, list) or not tracks):
        raise ValueError("invalid Composer song")
    out = {"version": 1, "ppq": PPQ, "tempo": int(tempo),
           "time_signature": [int(sig[0]), int(sig[1])], "tracks": []}
    for ti, tr in enumerate(tracks):
        if not isinstance(tr, dict) or not isinstance(tr.get("notes"), list):
            raise ValueError("invalid track")
        nt = new_track(tr.get("name", "Track %d" % (ti + 1)),
                       tr.get("instrument", "Piano"), tr.get("program", 0),
                       tr.get("percussion", False))
        nt["mute"] = bool(tr.get("mute", False))
        for note in tr["notes"]:
            if not isinstance(note, dict):
                raise ValueError("invalid note")
            vals = [note.get(k) for k in ("start", "duration", "pitch", "velocity")]
            if any(isinstance(v, bool) or not isinstance(v, int) for v in vals):
                raise ValueError("invalid note")
            start, duration, pitch, velocity = vals
            if start < 0 or duration < 1 or not 0 <= pitch <= 127 or not 1 <= velocity <= 127:
                raise ValueError("invalid note")
            nt["notes"].append({"start": start, "duration": duration,
                                "pitch": pitch, "velocity": velocity})
        nt["notes"].sort(key=lambda n: (n["start"], n["pitch"], n["duration"], n["velocity"]))
        out["tracks"].append(nt)
    return out


def vlq_encode(value):
    """Encode a MIDI variable-length integer (0..0x0fffffff)."""
    value = int(value)
    if not 0 <= value <= 0x0FFFFFFF:
        raise ValueError("VLQ out of range")
    buf = [value & 0x7f]
    value >>= 7
    while value:
        buf.append(0x80 | (value & 0x7f))
        value >>= 7
    return bytes(reversed(buf))


def _vlq_read(data, pos):
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise ValueError("truncated VLQ")
        b = data[pos]; pos += 1
        value = (value << 7) | (b & 0x7f)
        if not b & 0x80:
            return value, pos
    raise ValueError("invalid VLQ")


def _chunk(kind, payload):
    return kind + struct.pack(">I", len(payload)) + payload


def midi_export(song):
    """Return a format-1 SMF. A private sequencer meta preserves exact model
    fields; ordinary players simply skip it and play the standard events."""
    song = normalize_song(song)
    num, den = song["time_signature"]
    mpqn = int(round(60000000.0 / song["tempo"]))
    meta = bytearray()
    meta += b"\x00\xff\x51\x03" + mpqn.to_bytes(3, "big")
    meta += b"\x00\xff\x58\x04" + bytes((num, int(math.log(den, 2)), 24, 8))
    meta += b"\x00\xff\x2f\x00"
    chunks = [_chunk(b"MTrk", bytes(meta))]
    melodic_channel = 0
    for track in song["tracks"]:
        if track["percussion"]:
            channel = 9
        else:
            while melodic_channel == 9:
                melodic_channel += 1
            channel = melodic_channel % 16
            melodic_channel += 1
            if channel == 9:
                channel = 10
        events = []
        name = track["name"].encode("utf-8")
        events.append((0, 0, b"\xff\x03" + vlq_encode(len(name)) + name))
        model = json.dumps({"instrument": track["instrument"],
                            "percussion": track["percussion"],
                            "mute": track["mute"]}, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
        tag = b"NotebookOS-Composer\x00" + model
        events.append((0, 1, b"\xff\x7f" + vlq_encode(len(tag)) + tag))
        events.append((0, 2, bytes((0xc0 | channel, track["program"]))))
        for n in track["notes"]:
            events.append((n["start"], 4, bytes((0x90 | channel, n["pitch"], n["velocity"]))))
            events.append((n["start"] + n["duration"], 3,
                           bytes((0x80 | channel, n["pitch"], 0))))
        events.sort(key=lambda e: (e[0], e[1]))  # note-off before on at a boundary
        body, last = bytearray(), 0
        for tick, _order, raw in events:
            body += vlq_encode(tick - last) + raw
            last = tick
        body += b"\x00\xff\x2f\x00"
        chunks.append(_chunk(b"MTrk", bytes(body)))
    return _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks), PPQ)) + b"".join(chunks)


def _parse_track(data):
    pos = tick = 0
    running = None
    out = {"name": None, "programs": {}, "notes": [], "tempo": None,
           "signature": None, "model": None, "channels": set()}
    active = {}
    while pos < len(data):
        delta, pos = _vlq_read(data, pos); tick += delta
        if pos >= len(data):
            raise ValueError("truncated event")
        status = data[pos]
        if status < 0x80:
            if running is None:
                raise ValueError("running status without status")
            status = running
        else:
            pos += 1
            if status < 0xf0:
                running = status
            else:
                running = None
        if status == 0xff:
            if pos >= len(data): raise ValueError("truncated meta")
            kind = data[pos]; pos += 1
            size, pos = _vlq_read(data, pos)
            payload = data[pos:pos + size]; pos += size
            if len(payload) != size: raise ValueError("truncated meta payload")
            if kind == 0x03:
                out["name"] = payload.decode("utf-8", "replace")
            elif kind == 0x51 and size == 3:
                out["tempo"] = int(round(60000000 / int.from_bytes(payload, "big")))
            elif kind == 0x58 and size >= 2:
                out["signature"] = [payload[0], 1 << payload[1]]
            elif kind == 0x7f and payload.startswith(b"NotebookOS-Composer\x00"):
                try: out["model"] = json.loads(payload.split(b"\x00", 1)[1].decode("utf-8"))
                except Exception: pass
            elif kind == 0x2f:
                break
            continue
        if status in (0xf0, 0xf7):
            size, pos = _vlq_read(data, pos); pos += size; continue
        hi, ch = status & 0xf0, status & 0x0f
        need = 1 if hi in (0xc0, 0xd0) else 2
        vals = data[pos:pos + need]; pos += need
        if len(vals) != need: raise ValueError("truncated MIDI event")
        out["channels"].add(ch)
        if hi == 0xc0:
            out["programs"][ch] = vals[0]
        elif hi == 0x90 and vals[1] > 0:
            active.setdefault((ch, vals[0]), []).append((tick, vals[1]))
        elif hi in (0x80, 0x90):
            stack = active.get((ch, vals[0]), [])
            if stack:
                start, vel = stack.pop(0)
                out["notes"].append({"start": start, "duration": max(1, tick - start),
                                     "pitch": vals[0], "velocity": vel, "channel": ch})
    return out


def midi_import(raw):
    """Read SMF format 0/1, including channel running status."""
    if raw[:4] != b"MThd" or len(raw) < 14:
        raise ValueError("not a MIDI file")
    hlen = struct.unpack(">I", raw[4:8])[0]
    if hlen < 6 or len(raw) < 8 + hlen: raise ValueError("bad MIDI header")
    fmt, count, division = struct.unpack(">HHH", raw[8:14])
    if fmt not in (0, 1) or division & 0x8000 or division == 0:
        raise ValueError("unsupported MIDI format")
    pos, parsed = 8 + hlen, []
    for _ in range(count):
        if raw[pos:pos + 4] != b"MTrk" or pos + 8 > len(raw):
            raise ValueError("missing MIDI track")
        size = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
        payload = raw[pos + 8:pos + 8 + size]
        if len(payload) != size: raise ValueError("truncated MIDI track")
        parsed.append(_parse_track(payload)); pos += 8 + size
    scale = PPQ / float(division)
    tempo, sig = 120, [4, 4]
    for p in parsed:
        tempo = p["tempo"] or tempo; sig = p["signature"] or sig
    tracks = []
    sources = parsed[1:] if fmt == 1 and len(parsed) > 1 else parsed
    for index, p in enumerate(sources):
        # Format 0 can contain several channels: split them into usable tracks.
        channels = sorted({n["channel"] for n in p["notes"]}) or sorted(p["channels"]) or [0]
        for ch in channels:
            notes = [n for n in p["notes"] if n["channel"] == ch]
            model = p["model"] if isinstance(p["model"], dict) else {}
            program = p["programs"].get(ch, 0)
            instrument = model.get("instrument")
            if not isinstance(instrument, str):
                instrument = min(INSTRUMENTS, key=lambda x: abs(x[1] - program))[0]
            tr = new_track(p["name"] or _t("Track %d") % (len(tracks) + 1),
                           instrument, program,
                           model.get("percussion", ch == 9))
            tr["mute"] = bool(model.get("mute", False))
            tr["notes"] = [{"start": int(round(n["start"] * scale)),
                            "duration": max(1, int(round(n["duration"] * scale))),
                            "pitch": n["pitch"], "velocity": n["velocity"]}
                           for n in notes]
            tr["notes"].sort(key=lambda n: (n["start"], n["pitch"], n["duration"], n["velocity"]))
            tracks.append(tr)
    if not tracks: tracks = [new_track()]
    return normalize_song({"version": 1, "ppq": PPQ, "tempo": tempo,
                           "time_signature": sig, "tracks": tracks})


class SongEditor:
    """Pixel-free editing operations shared by the staff and tests."""
    def __init__(self, song=None, history=None):
        self.song = normalize_song(song or new_song())
        self.track = 0
        self.selection = set()  # note indices in active track
        self.history = history

    def snapshot(self): return copy.deepcopy(self.song)
    def restore(self, state):
        self.song = normalize_song(copy.deepcopy(state)); self.selection.clear()

    def _change(self, label, fn):
        if self.history: self.history.checkpoint(label)
        result = fn()
        if self.history: self.history.commit()
        return result

    def add_note(self, start, duration, pitch, velocity=96):
        note = {"start": max(0, int(start)), "duration": max(1, int(duration)),
                "pitch": max(0, min(127, int(pitch))),
                "velocity": max(1, min(127, int(velocity)))}
        def op():
            notes = self.song["tracks"][self.track]["notes"]; notes.append(note)
            notes.sort(key=lambda n: (n["start"], n["pitch"])); self.selection = {notes.index(note)}
        self._change("Add Note", op); return note

    def delete_selected(self):
        if not self.selection: return False
        def op():
            notes = self.song["tracks"][self.track]["notes"]
            notes[:] = [n for i, n in enumerate(notes) if i not in self.selection]
            self.selection.clear()
        self._change("Delete Notes", op); return True

    def move_selected(self, dt, dp):
        if not self.selection: return False
        def op():
            notes = self.song["tracks"][self.track]["notes"]
            actual_t = max(-min(notes[i]["start"] for i in self.selection), int(dt))
            actual_p = max(-min(notes[i]["pitch"] for i in self.selection),
                           min(127 - max(notes[i]["pitch"] for i in self.selection), int(dp)))
            chosen = [notes[i] for i in sorted(self.selection)]
            for n in chosen: n["start"] += actual_t; n["pitch"] += actual_p
            notes.sort(key=lambda n: (n["start"], n["pitch"])); self.selection = {notes.index(n) for n in chosen}
        self._change("Move Notes", op); return True

    def move_selected_diatonic(self, dt, steps):
        """Staff move: horizontal ticks plus vertical scale steps, preserving ♯."""
        if not self.selection: return False
        def op():
            notes = self.song["tracks"][self.track]["notes"]
            actual_t = max(-min(notes[i]["start"] for i in self.selection), int(dt))
            chosen = [notes[i] for i in sorted(self.selection)]
            for n in chosen:
                n["start"] += actual_t
                n["pitch"] = pitch_for_step(diatonic_number(n["pitch"]) + int(steps),
                                              is_sharp(n["pitch"]))
            notes.sort(key=lambda n: (n["start"], n["pitch"]))
            self.selection = {notes.index(n) for n in chosen}
        self._change("Move Notes", op); return True

    def resize_selected(self, delta):
        if not self.selection: return False
        return self._change("Resize Notes", lambda: [self.song["tracks"][self.track]["notes"][i].update(duration=max(1, self.song["tracks"][self.track]["notes"][i]["duration"] + int(delta))) for i in self.selection]) is not None

    def set_velocity(self, velocity):
        if not self.selection: return False
        v = max(1, min(127, int(velocity)))
        return self._change("Change Velocity", lambda: [self.song["tracks"][self.track]["notes"][i].update(velocity=v) for i in self.selection]) is not None

    def add_track(self, name="New Track"):
        self._change("Add Track", lambda: self.song["tracks"].append(new_track(name)))
        self.track = len(self.song["tracks"]) - 1; self.selection.clear()

    def remove_track(self, index=None):
        index = self.track if index is None else int(index)
        if len(self.song["tracks"]) <= 1: return False
        self._change("Delete Track", lambda: self.song["tracks"].pop(index))
        self.track = min(self.track, len(self.song["tracks"]) - 1); self.selection.clear(); return True


def render_preview(song, path, rate=24000):
    """Render the complete audible song to a mono 16-bit WAV."""
    song = normalize_song(song); sec_tick = 60.0 / song["tempo"] / PPQ
    end = max([n["start"] + n["duration"] for t in song["tracks"] for n in t["notes"]] or [PPQ])
    frames = min(int((end * sec_tick + .08) * rate), rate * 180)
    mix = [0.0] * frames
    families = {name: fam for name, _p, fam in INSTRUMENTS}
    for tr in song["tracks"]:
        if tr["mute"]: continue
        family = "noise" if tr["percussion"] else families.get(tr["instrument"], "sine")
        for n in tr["notes"]:
            a = int(n["start"] * sec_tick * rate); b = min(frames, int((n["start"] + n["duration"]) * sec_tick * rate))
            freq = 440.0 * 2 ** ((n["pitch"] - 69) / 12.0); amp = .16 * n["velocity"] / 127
            phase = 0.0; step = freq / rate
            rng = random.Random(n["pitch"] * 1000003 + n["start"])
            for i in range(a, b):
                x = phase % 1.0
                if family == "square": value = 1.0 if x < .5 else -1.0
                elif family == "triangle": value = 1.0 - 4.0 * abs(x - .5)
                elif family == "saw": value = 2.0 * x - 1.0
                elif family == "noise": value = rng.uniform(-1, 1)
                else: value = math.sin(2 * math.pi * x)
                fade = min(1.0, (i - a) / max(1, rate * .008), (b - i) / max(1, rate * .025))
                mix[i] += value * amp * fade; phase += step
    pcm = array.array("h", (max(-32767, min(32767, int(x * 32767))) for x in mix))
    with wave.open(path, "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(pcm.tobytes())
    return frames / float(rate)


_WHITE_PCS = (0, 2, 4, 5, 7, 9, 11)
_DURATIONS = (
    ("whole", PPQ * 4), ("half", PPQ * 2), ("quarter", PPQ),
    ("eighth", PPQ // 2), ("sixteenth", PPQ // 4),
)


def is_sharp(pitch):
    return int(pitch) % 12 not in _WHITE_PCS


def diatonic_number(pitch):
    """Diatonic step number; accidentals occupy their natural note's step."""
    pitch = max(0, min(127, int(pitch)))
    octave, pc = divmod(pitch, 12)
    natural = max(i for i, value in enumerate(_WHITE_PCS) if value <= pc)
    return octave * 7 + natural


def pitch_for_step(step, sharp=False):
    octave, degree = divmod(int(step), 7)
    pitch = octave * 12 + _WHITE_PCS[degree] + (1 if sharp else 0)
    return max(0, min(127, pitch))


def clef_for_track(track):
    if track.get("percussion"): return "percussion"
    pitches = sorted(n["pitch"] for n in track["notes"])
    if not pitches: median = 60
    elif len(pitches) % 2: median = pitches[len(pitches) // 2]
    else: median = (pitches[len(pitches)//2 - 1] + pitches[len(pitches)//2]) / 2.0
    return "treble" if median >= 60 else "bass"


def staff_step(pitch, clef):
    # bottom line: E4 in treble, G2 in bass
    return diatonic_number(pitch) - diatonic_number(64 if clef == "treble" else 43)


def duration_glyph(ticks):
    """Nearest display glyph only; callers never write it back to the model."""
    choices = []
    for name, base in _DURATIONS:
        choices.extend(((abs(ticks - base), name, False, base),
                        (abs(ticks - base * 3 // 2), name, True, base * 3 // 2)))
    _distance, name, dotted, shown = min(choices, key=lambda x: (x[0], -x[3]))
    return name, dotted, shown


def measure_ticks(song):
    num, den = song["time_signature"]
    return PPQ * 4 * num // den


def measure_rests(notes, start, end):
    """Return uncovered (start, duration, glyph, dotted) spans in a measure."""
    covered = sorted((max(start, n["start"]), min(end, n["start"] + n["duration"]))
                     for n in notes if n["start"] < end and n["start"] + n["duration"] > start)
    merged = []
    for a, b in covered:
        if merged and a <= merged[-1][1]: merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else: merged.append((a, b))
    gaps, cursor = [], start
    for a, b in merged + [(end, end)]:
        if a > cursor:
            left = a - cursor
            while left:
                name, dotted, shown = duration_glyph(left)
                shown = min(shown, left)
                if shown <= 0: break
                gaps.append((cursor, shown, name, dotted)); cursor += shown; left -= shown
        cursor = max(cursor, b)
    return gaps


class NoteGlyphButton(Gtk.DrawingArea):
    def __init__(self, name, callback):
        super().__init__(); self.name = name; self.active = False
        self.set_size_request(34, 30); self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self._draw); self.connect("button-press-event", lambda *_: callback(name))
        self.set_tooltip_text(_t(name.capitalize()))
    def _draw(self, _w, cr):
        cr.set_source_rgb(.82, .28, .18) if self.active else cr.set_source_rgb(.18, .22, .24)
        hollow = self.name in ("whole", "half")
        _draw_note(cr, 14, 19, self.name, hollow, False, False, 1)
        return False


class StaffNotation(Gtk.DrawingArea):
    SPACE, MEASURE_W, LEFT, TOP, TRACK_H = 12, 360, 88, 42, 150
    def __init__(self, app):
        super().__init__(); self.app = app; self.set_can_focus(True)
        self.playhead_tick = None
        self._resize()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("draw", self._draw); self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._release); self.connect("motion-notify-event", self._motion)
        self.drag = None

    def _resize(self):
        song = self.app.editor.song; end = max([n["start"] + n["duration"] for t in song["tracks"] for n in t["notes"]] or [measure_ticks(song) * 4])
        measures = max(4, int(math.ceil(end / measure_ticks(song))))
        self.set_size_request(self.LEFT + measures * self.MEASURE_W + 60,
                              self.TOP + len(song["tracks"]) * self.TRACK_H + 30)

    def _staff_y(self, track): return self.TOP + track * self.TRACK_H + 48
    def _tick_x(self, tick): return self.LEFT + tick / measure_ticks(self.app.editor.song) * self.MEASURE_W
    def _xy(self, event):
        track = max(0, min(len(self.app.editor.song["tracks"]) - 1,
                           int((event.y - self.TOP) // self.TRACK_H)))
        raw = max(0, (event.x - self.LEFT) / self.MEASURE_W * measure_ticks(self.app.editor.song))
        tick = int(round(raw / self.app.snap)) * self.app.snap
        tr = self.app.editor.song["tracks"][track]; clef = clef_for_track(tr)
        if clef == "percussion": pitch = 36
        else:
            step = int(round((self._staff_y(track) + 4 * self.SPACE - event.y) / (self.SPACE / 2)))
            pitch = pitch_for_step(diatonic_number(64 if clef == "treble" else 43) + step,
                                   self.app.sharp)
        return track, tick, pitch

    def _hit(self, x, y):
        track, tick, pitch = self._xy(type("Point", (), {"x": x, "y": y})())
        notes = self.app.editor.song["tracks"][track]["notes"]
        for i, n in reversed(list(enumerate(notes))):
            if abs(self._tick_x(n["start"]) - x) <= 12 and abs(self._note_y(track, n["pitch"]) - y) <= 9:
                return track, i
        return track, None

    def _press(self, _w, event):
        self.grab_focus(); track, idx = self._hit(event.x, event.y); _track, tick, pitch = self._xy(event)
        if track != self.app.editor.track: self.app.editor.track = track; self.app.editor.selection.clear(); self.app._refresh_tracks()
        if idx is None:
            if not event.state & Gdk.ModifierType.SHIFT_MASK: self.app.editor.selection.clear()
            self.drag = ("rubber", event.x, event.y, event.x, event.y); self.queue_draw(); return True
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            if idx in self.app.editor.selection: self.app.editor.selection.remove(idx)
            else: self.app.editor.selection.add(idx)
        elif idx not in self.app.editor.selection: self.app.editor.selection = {idx}
        self.drag = ("move", tick, diatonic_number(pitch), tick, diatonic_number(pitch)); self.queue_draw(); return True

    def _motion(self, _w, event):
        if self.drag:
            kind, x, y, _a, _b = self.drag
            if kind != "rubber":
                _track, a, pitch = self._xy(event); b = diatonic_number(pitch)
            else: a, b = event.x, event.y
            self.drag = (kind, x, y, a, b); self.queue_draw()
        return True

    def _release(self, _w, event):
        if not self.drag: return True
        kind, x, y, a, b = self.drag; self.drag = None
        if kind == "move":
            self.app.editor.move_selected_diatonic(a - x, b - y)
        else:
            x0, x1 = sorted((x, a)); y0, y1 = sorted((y, b)); selected = set()
            notes = self.app.editor.song["tracks"][self.app.editor.track]["notes"]
            for i, n in enumerate(notes):
                nx = self._tick_x(n["start"]); ny = self._note_y(self.app.editor.track, n["pitch"])
                if x0 == x1 and y0 == y1: continue
                if nx + 12 >= x0 and nx - 12 <= x1 and ny + 9 >= y0 and ny - 9 <= y1: selected.add(i)
            if selected: self.app.editor.selection |= selected
            elif abs(a - x) < 4 and abs(b - y) < 4:
                _track, tick, pitch = self._xy(event); self.app.editor.add_note(tick, self.app.note_duration, pitch)
        self.app.changed(); return True

    def _note_y(self, track, pitch):
        tr = self.app.editor.song["tracks"][track]; clef = clef_for_track(tr)
        return self._staff_y(track) + (2 * self.SPACE if clef == "percussion" else (4 - staff_step(pitch, clef) / 2) * self.SPACE)

    def _draw(self, _w, cr):
        self._resize(); w = self.get_allocated_width(); h = self.get_allocated_height()
        cr.set_source_rgb(.988, .984, .973); cr.paint()
        mt = measure_ticks(self.app.editor.song)
        for ti, tr in enumerate(self.app.editor.song["tracks"]):
            sy = self._staff_y(ti); perc = tr["percussion"]
            cr.set_source_rgb(.14, .14, .13); cr.set_line_width(1)
            for line in ((2,) if perc else range(5)):
                y = sy + line * self.SPACE; cr.move_to(self.LEFT, y); cr.line_to(w - 30, y); cr.stroke()
            _draw_clef(cr, 34, sy + 2 * self.SPACE, clef_for_track(tr))
            measures = int(math.ceil((w - self.LEFT) / self.MEASURE_W))
            for m in range(measures + 1):
                x = self.LEFT + m * self.MEASURE_W; cr.move_to(x, sy); cr.line_to(x, sy + 4 * self.SPACE); cr.stroke()
                cr.set_font_size(10); cr.move_to(x + 5, sy - 10); cr.show_text(str(m + 1))
            for start in range(0, measures * mt, mt):
                for rs, _rd, name, dotted in measure_rests(tr["notes"], start, start + mt):
                    _draw_rest(cr, self._tick_x(rs) + 5, sy + 2 * self.SPACE, name, dotted)
            for i, n in enumerate(tr["notes"]):
                x, y = self._tick_x(n["start"]), self._note_y(ti, n["pitch"])
                name, dotted, _shown = duration_glyph(n["duration"])
                cr.set_source_rgb(.78, .20, .12) if ti == self.app.editor.track and i in self.app.editor.selection else cr.set_source_rgb(.12, .16, .17)
                _draw_ledger(cr, x, y, sy, self.SPACE)
                _draw_note(cr, x, y, name, name in ("whole", "half"), dotted,
                           is_sharp(n["pitch"]), staff_step(n["pitch"], clef_for_track(tr)), perc)
        if self.playhead_tick is not None:
            x = self._tick_x(self.playhead_tick)
            cr.set_source_rgb(.78, .20, .12); cr.set_line_width(2)
            cr.move_to(x, 0); cr.line_to(x, h); cr.stroke()
        if self.drag and self.drag[0] == "rubber":
            _, x0, y0, x1, y1 = self.drag; cr.set_source_rgba(.78, .20, .12, .2); cr.rectangle(x0, y0, x1-x0, y1-y0); cr.fill()
        return False


def _draw_note(cr, x, y, name, hollow, dotted, sharp, step, percussion=False):
    if sharp:
        cr.set_line_width(1.4)
        for dx in (-10, -6): cr.move_to(x + dx, y - 7); cr.line_to(x + dx - 1, y + 7); cr.stroke()
        for dy in (-3, 3): cr.move_to(x - 13, y + dy + 1); cr.line_to(x - 3, y + dy - 1); cr.stroke()
    if percussion:
        cr.set_line_width(2); cr.move_to(x-5,y-4); cr.line_to(x+5,y+4); cr.move_to(x-5,y+4); cr.line_to(x+5,y-4); cr.stroke()
    else:
        cr.save(); cr.translate(x, y); cr.rotate(-.25); cr.scale(1.35, .9); cr.arc(0, 0, 5, 0, 2*math.pi); cr.restore()
        if hollow: cr.set_line_width(1.8); cr.stroke()
        else: cr.fill()
    if name != "whole":
        up = step < 4; sx = x + (5 if up else -5); end = y - 30 if up else y + 30
        cr.set_line_width(1.5); cr.move_to(sx, y); cr.line_to(sx, end); cr.stroke()
        flags = 2 if name == "sixteenth" else 1 if name == "eighth" else 0
        for f in range(flags):
            fy = end + (f * 7 if up else -f * 7); cr.move_to(sx, fy); cr.curve_to(sx + (10 if up else -10), fy + (6 if up else -6), sx + (8 if up else -8), fy + (14 if up else -14), sx + (3 if up else -3), fy + (18 if up else -18)); cr.stroke()
    if dotted: cr.arc(x + 11, y, 1.7, 0, 2*math.pi); cr.fill()


def _draw_ledger(cr, x, y, sy, space):
    top, bottom = sy, sy + 4*space
    yy = bottom + space
    while yy <= y + 2: cr.move_to(x-9, yy); cr.line_to(x+9, yy); cr.stroke(); yy += space
    yy = top - space
    while yy >= y - 2: cr.move_to(x-9, yy); cr.line_to(x+9, yy); cr.stroke(); yy -= space


def _draw_rest(cr, x, y, name, dotted):
    cr.set_source_rgb(.28, .28, .26)
    if name in ("whole", "half"):
        cr.rectangle(x-5, y + (0 if name == "whole" else -4), 10, 4); cr.fill()
    elif name == "quarter":
        cr.set_line_width(2.5); cr.move_to(x+3,y-12); cr.line_to(x-3,y-3); cr.line_to(x+3,y+3); cr.line_to(x-3,y+13); cr.stroke()
    else:
        cr.arc(x, y-6, 3, 0, 2*math.pi); cr.fill(); cr.set_line_width(1.8); cr.move_to(x+2,y-5); cr.curve_to(x+8,y, x-2,y+8, x-2,y+14); cr.stroke()
    if dotted: cr.arc(x+10,y,1.5,0,2*math.pi); cr.fill()


def _draw_clef(cr, x, y, clef):
    cr.save(); cr.translate(x, y); cr.set_source_rgb(.12,.12,.11); cr.set_line_width(3)
    if clef == "treble":
        cr.move_to(10,-31); cr.curve_to(-3,-22,2,3,11,19); cr.curve_to(18,33,-5,39,-11,22); cr.curve_to(-17,5,5,-9,16,2); cr.curve_to(26,15,8,25,-4,17); cr.stroke(); cr.arc(8,28,4,0,2*math.pi); cr.fill()
    elif clef == "bass":
        cr.move_to(-8,-4); cr.curve_to(4,-18,22,-8,14,9); cr.curve_to(10,18,1,23,-8,26); cr.stroke()
        for yy in (-5,7): cr.arc(22,yy,2.3,0,2*math.pi); cr.fill()
    else:
        cr.move_to(-8,-8); cr.line_to(8,8); cr.move_to(-8,8); cr.line_to(8,-8); cr.stroke(); cr.move_to(0,-18); cr.line_to(0,18); cr.stroke()
    cr.restore()


class _SurfaceStaff:
    """Small non-GTK layout adapter used for headless pixel proofs."""
    SPACE, MEASURE_W, LEFT, TOP, TRACK_H = (StaffNotation.SPACE, StaffNotation.MEASURE_W,
                                            StaffNotation.LEFT, StaffNotation.TOP, StaffNotation.TRACK_H)
    _staff_y = StaffNotation._staff_y
    _tick_x = StaffNotation._tick_x
    _note_y = StaffNotation._note_y
    _draw = StaffNotation._draw
    def __init__(self, song, width, height, track=0, selection=None, playhead=None):
        class Editor: pass
        class App: pass
        self.app = App(); self.app.editor = Editor(); self.app.editor.song = normalize_song(song)
        self.app.editor.track = track; self.app.editor.selection = set(selection or ())
        self.width, self.height = width, height; self.playhead_tick = playhead; self.drag = None
    def _resize(self): pass
    def get_allocated_width(self): return self.width
    def get_allocated_height(self): return self.height


def render_staff_surface(song, width=900, height=None, track=0, selection=None, playhead=None):
    """Render notation to a Cairo ImageSurface without a display server."""
    height = height or (StaffNotation.TOP + len(song["tracks"]) * StaffNotation.TRACK_H + 30)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    layout = _SurfaceStaff(song, width, height, track, selection, playhead)
    layout._draw(layout, cairo.Context(surface))
    surface.flush()
    return surface, layout


class Composer(nbapp.AppWindow):
    app_name = "Composer"
    menus = ("File", "Edit", "View", "Track", "Transport")

    def __init__(self):
        super().__init__(); self.set_default_size(980, 680)
        self._path = None; self._read_only = False; self._player = None; self._play_started = 0
        self.song = self._load_session(); self.editor = SongEditor(self.song)
        self.undo = nbapp.UndoHistory(self.editor.snapshot, self._restore)
        self.editor.history = self.undo; self.undo.reset(); self.snap = PPQ // 4
        self.note_duration = PPQ; self.sharp = False; self.dotted = False
        self.connect("destroy", self._destroy); self.connect("key-press-event", self._key)
        self._build(); self._refresh_tracks()

    def _load_session(self):
        if not os.path.exists(STATE_FILE): return new_song()
        try:
            with open(STATE_FILE, encoding="utf-8") as fh: return normalize_song(json.load(fh))
        except Exception:
            nbapp.quarantine_unrecognized(STATE_FILE); self._read_only = True; return new_song()

    def _save_session(self):
        if self._read_only: return False
        try: nbapp.atomic_write_json(STATE_FILE, self.editor.song, indent=2); return True
        except Exception: return False

    def _restore(self, state): self.editor.restore(state); self.changed()

    def _build(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); root.set_border_width(10); self.content.pack_start(root, True, True, 0)
        bar = Gtk.Box(spacing=8)
        # Track add/remove/rename live in the Track MENU only (the OS menu
        # contract) — as toolbar buttons their translations set the window's
        # minimum width: with the ru catalog merged the bar measured 1194px
        # against the 1024 budget. The combo cells and the status label are
        # capped/ellipsized for the same reason; the full text stays available
        # in the combo's popup and the menu.
        self.track_combo = Gtk.ComboBoxText(); self.track_combo.connect("changed", self._track_changed); bar.pack_start(self.track_combo, False, False, 0)
        _cap_combo_cells(self.track_combo, 12)
        self.mute = Gtk.CheckButton(label=_t("Mute track")); self.mute.connect("toggled", self._mute); bar.pack_start(self.mute, False, False, 0)
        bar.pack_start(Gtk.Label(label=_t("Tempo")), False, False, 0)
        self.tempo = Gtk.SpinButton.new_with_range(20, 400, 1); self.tempo.set_value(self.editor.song["tempo"]); self.tempo.connect("value-changed", self._tempo); bar.pack_start(self.tempo, False, False, 0)
        bar.pack_start(Gtk.Label(label=_t("Snap")), False, False, 0)
        self.snap_combo = Gtk.ComboBoxText()
        for label in (_t("Beat"), _t("1/2 beat"), _t("1/4 beat"), _t("1/8 beat")): self.snap_combo.append_text(label)
        self.snap_combo.set_active(2); self.snap_combo.connect("changed", self._snap); bar.pack_start(self.snap_combo, False, False, 0)
        self.duration_box = Gtk.Box(spacing=2); self.duration_buttons = []
        for name, _ticks in _DURATIONS:
            button = NoteGlyphButton(name, self._duration); self.duration_buttons.append(button); self.duration_box.pack_start(button, False, False, 0)
        self.dot = Gtk.ToggleButton(label="·"); self.dot.set_tooltip_text(_t("Dotted")); self.dot.connect("toggled", self._dot); self.duration_box.pack_start(self.dot, False, False, 0)
        self.accidental = Gtk.ToggleButton(label="♯"); self.accidental.set_tooltip_text(_t("Sharp")); self.accidental.connect("toggled", self._sharp); self.duration_box.pack_start(self.accidental, False, False, 0)
        self._duration("quarter")
        self.play = Gtk.Button(label=_t("Play")); self.play.connect("clicked", self._play); bar.pack_end(self.play, False, False, 0)
        root.pack_start(bar, False, False, 0)
        sub = Gtk.Box(spacing=8); sub.pack_start(Gtk.Label(label=_t("Instrument")), False, False, 0)
        self.instrument = Gtk.ComboBoxText()
        for name, _program, _family in INSTRUMENTS: self.instrument.append_text(_t(name))
        self.instrument.connect("changed", self._instrument); sub.pack_start(self.instrument, False, False, 0)
        _cap_combo_cells(self.instrument, 14)
        sub.pack_start(Gtk.Label(label=_t("Velocity")), False, False, 0)
        self.velocity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 127, 1); self.velocity.set_value(96); self.velocity.set_size_request(180, -1); self.velocity.connect("value-changed", self._velocity); sub.pack_start(self.velocity, False, False, 0)
        sub.pack_start(self.duration_box, False, False, 0)
        self.status = Gtk.Label(label=_t("Click a staff line or space to add a note."), xalign=1); self.status.set_ellipsize(Pango.EllipsizeMode.END); sub.pack_end(self.status, True, True, 0); root.pack_start(sub, False, False, 0)
        self.staff = StaffNotation(self); scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC); scroll.add(self.staff); root.pack_start(scroll, True, True, 0)

    def menu_items(self, name):
        if name == "File": return [(_t("New    Ctrl+N"), self._new), (_t("Open…    Ctrl+O"), self._open), ("-", None), (_t("Save    Ctrl+S"), self._save), (_t("Save As…    Ctrl+Shift+S"), self._save_as), ("-", None), (_t("Export MIDI…"), self._export), ("-", None), (_t("Close    Esc"), self.close)]
        if name == "Edit": return [(_t("Undo    Ctrl+Z"), self._undo), (_t("Redo    Ctrl+Shift+Z"), self._redo), ("-", None), (_t("Select All    Ctrl+A"), self._select_all), (_t("Delete Notes    Delete"), self._delete)]
        if name == "View": return [(_t("Return to beginning"), self._center)]
        if name == "Track": return [(_t("Add Track"), self._add_track), (_t("Remove Track"), self._remove_track), (_t("Rename Track…"), self._rename_track), (_t("Mute Track"), self._toggle_mute)]
        return [(_t("Play    Space"), self._play), (_t("Stop"), self._stop)]

    def changed(self): self.staff.queue_draw(); self._save_session()
    def _refresh_tracks(self):
        self.track_combo.remove_all()
        for tr in self.editor.song["tracks"]: self.track_combo.append_text(tr["name"])
        self.track_combo.set_active(self.editor.track); tr = self.editor.song["tracks"][self.editor.track]; self.mute.set_active(tr["mute"])
        names = [x[0] for x in INSTRUMENTS]; self.instrument.set_active(names.index(tr["instrument"]) if tr["instrument"] in names else 0)
    def _track_changed(self, w):
        # _refresh_tracks re-selects the active row, which re-emits "changed":
        # acting only on a REAL change breaks the feedback loop (construct-time
        # RecursionError, caught on the dispatcher's display re-verify).
        if w.get_active() >= 0 and w.get_active() != self.editor.track:
            self.editor.track = w.get_active(); self.editor.selection.clear(); self._refresh_tracks(); self.staff.queue_draw()
    def _add_track(self, *_): self.editor.add_track(_t("New Track")); self._refresh_tracks(); self.changed()
    def _remove_track(self, *_): self.editor.remove_track(); self._refresh_tracks(); self.changed()
    def _rename_track(self, *_):
        d = Gtk.Dialog(title=_t("Rename Track"), transient_for=self, modal=True); d.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); d.add_button(_t("Rename"), Gtk.ResponseType.OK)
        entry = Gtk.Entry(); entry.set_text(self.editor.song["tracks"][self.editor.track]["name"]); entry.set_activates_default(True); d.get_content_area().pack_start(entry, True, True, 12); d.set_default_response(Gtk.ResponseType.OK); d.show_all()
        if d.run() == Gtk.ResponseType.OK and entry.get_text().strip():
            self.undo.checkpoint("Rename Track"); self.editor.song["tracks"][self.editor.track]["name"] = entry.get_text().strip(); self.undo.commit(); self._refresh_tracks(); self.changed()
        d.destroy()
    def _mute(self, w):
        tr = self.editor.song["tracks"][self.editor.track]
        if tr["mute"] != w.get_active(): self.undo.checkpoint("Mute Track"); tr["mute"] = w.get_active(); self.undo.commit(); self.changed()
    def _toggle_mute(self, *_): self.mute.set_active(not self.mute.get_active())
    def _tempo(self, w):
        value = w.get_value_as_int()
        if self.editor.song["tempo"] != value: self.undo.checkpoint("Change Tempo"); self.editor.song["tempo"] = value; self.undo.commit(); self.changed()
    def _snap(self, w): self.snap = (PPQ, PPQ//2, PPQ//4, PPQ//8)[max(0, w.get_active())]
    def _duration(self, name):
        base = dict(_DURATIONS)[name]; self.note_duration = base * (3 if self.dotted else 2) // 2
        for button in self.duration_buttons: button.active = button.name == name; button.queue_draw()
        self.duration_name = name
    def _dot(self, w): self.dotted = w.get_active(); self._duration(self.duration_name)
    def _sharp(self, w): self.sharp = w.get_active()
    def _instrument(self, w):
        if w.get_active() < 0: return
        name, program, _fam = INSTRUMENTS[w.get_active()]; tr = self.editor.song["tracks"][self.editor.track]
        if (tr["instrument"], tr["program"], tr["percussion"]) != (name, program, name == "Noise / Drums"):
            self.undo.checkpoint("Change Instrument"); tr.update(instrument=name, program=program, percussion=name == "Noise / Drums"); self.undo.commit(); self.changed()
    def _velocity(self, w): self.editor.set_velocity(w.get_value_as_int()) and self.changed()
    def _delete(self, *_): self.editor.delete_selected() and self.changed()
    def _select_all(self, *_): self.editor.selection = set(range(len(self.editor.song["tracks"][self.editor.track]["notes"]))); self.staff.queue_draw()
    def _undo(self, *_): self.undo.undo(); self._refresh_tracks()
    def _redo(self, *_): self.undo.redo(); self._refresh_tracks()
    def _new(self, *_): self.undo.checkpoint("New Song"); self.editor.song = new_song(); self.editor.track = 0; self.editor.selection.clear(); self.undo.commit(); self._path = None; self._refresh_tracks(); self.changed()
    def _choose(self, save=False):
        os.makedirs(DOC_DIR, exist_ok=True)
        if save: return nbpicker.save_file(self, title=_t("Save MIDI As"), start_dir=DOC_DIR, suggested_name="song.mid", patterns=("*.mid", "*.midi"), default_ext=".mid")
        return nbpicker.open_file(self, title=_t("Open MIDI"), start_dir=DOC_DIR, patterns=("*.mid", "*.midi"))
    def _open(self, *_):
        path = self._choose(False)
        if not path: return
        try:
            with open(path, "rb") as fh: song = midi_import(fh.read())
            self.undo.checkpoint("Open MIDI"); self.editor.song = song; self.editor.track = 0; self.editor.selection.clear(); self.undo.commit(); self._path = path; self._refresh_tracks(); self.changed(); self.status.set_text(_t("MIDI file opened."))
        except Exception: self.status.set_text(_t("The MIDI file could not be opened."))
    def _write(self, path):
        try:
            if not os.path.splitext(path)[1]: path += ".mid"
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True); fd, tmp = tempfile.mkstemp(prefix=".composer-", dir=os.path.dirname(path) or ".")
            try:
                with os.fdopen(fd, "wb") as out: out.write(midi_export(self.editor.song)); out.flush(); os.fsync(out.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
            self._path = path; self.status.set_text(_t("MIDI file saved.")); return True
        except Exception: self.status.set_text(_t("The MIDI file could not be saved.")); return False
    def _save(self, *_): return self._write(self._path) if self._path else self._save_as()
    def _save_as(self, *_):
        path = self._choose(True); return self._write(path) if path else False
    def _export(self, *_): return self._save_as()
    def _play(self, *_):
        if self._player: return self._stop()
        try:
            fd, path = tempfile.mkstemp(prefix="composer-preview-", suffix=".wav"); os.close(fd); duration = render_preview(self.editor.song, path)
            if GST_OK:
                Gst.init(None); self._player = Gst.ElementFactory.make("playbin", None); self._player.set_property("uri", "file://" + path); self._player.set_state(Gst.State.PLAYING)
            else:
                self._player = subprocess.Popen(["aplay", "-q", path])
            self._preview_path = path; self._play_started = time.monotonic(); self._play_duration = duration; self.play.set_label(_t("Stop")); GLib.timeout_add(40, self._play_tick)
        except Exception: self._player = None; self.status.set_text(_t("Audio preview is not available."))
    def _play_tick(self):
        if not self._player: return False
        elapsed = time.monotonic() - self._play_started
        self.staff.playhead_tick = int(elapsed * self.editor.song["tempo"] / 60 * PPQ)
        self.staff.queue_draw()
        self.status.set_text(_t("Playing at beat %d") % int(elapsed * self.editor.song["tempo"] / 60 + 1))
        if elapsed >= self._play_duration: self._stop(); return False
        return True
    def _stop(self, *_):
        if GST_OK and self._player: self._player.set_state(Gst.State.NULL)
        elif self._player:
            try: self._player.terminate()
            except Exception: pass
        self._player = None; self.staff.playhead_tick = None; self.staff.queue_draw()
        self.play.set_label(_t("Play")); self.status.set_text(_t("Playback stopped."))
        try: os.unlink(self._preview_path)
        except Exception: pass
    def _center(self, *_):
        p = self.staff.get_parent()
        while p and not isinstance(p, Gtk.ScrolledWindow): p = p.get_parent()
        if p: p.get_hadjustment().set_value(0)
    def _key(self, _w, e):
        ctrl = bool(e.state & Gdk.ModifierType.CONTROL_MASK); shift = bool(e.state & Gdk.ModifierType.SHIFT_MASK)
        if e.keyval == Gdk.KEY_Escape: self.editor.selection.clear(); self.staff.queue_draw(); return True
        if e.keyval == Gdk.KEY_Delete: self._delete(); return True
        if e.keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            dt = -self.snap if e.keyval == Gdk.KEY_Left else self.snap if e.keyval == Gdk.KEY_Right else 0; dp = 1 if e.keyval == Gdk.KEY_Up else -1 if e.keyval == Gdk.KEY_Down else 0; self.editor.move_selected(dt, dp) and self.changed(); return True
        if e.keyval == Gdk.KEY_space: self._play(); return True
        if ctrl and e.keyval in (Gdk.KEY_z, Gdk.KEY_Z): (self._redo if shift else self._undo)(); return True
        if ctrl and e.keyval in (Gdk.KEY_a, Gdk.KEY_A): self._select_all(); return True
        if ctrl and e.keyval in (Gdk.KEY_n, Gdk.KEY_N): self._new(); return True
        if ctrl and e.keyval in (Gdk.KEY_o, Gdk.KEY_O): self._open(); return True
        if ctrl and e.keyval in (Gdk.KEY_s, Gdk.KEY_S): (self._save_as if shift else self._save)(); return True
        return False
    def _destroy(self, *_): self._stop(); self._save_session()


def main():
    win = Composer(); win.show_all(); Gtk.main()


if __name__ == "__main__": main()
