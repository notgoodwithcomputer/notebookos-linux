#!/usr/bin/env python3
"""A take that exists only in this window survives the window.

A take costs real time at a microphone and cannot be typed again, so the
Sequencer lays a floor under every action that replaces the arrangement: the
outgoing project is written into Documents first (_keep_outgoing), and undo —
which lives only as long as the window — is not the only thing holding it.
This suite drives the three ways a take fell through that floor.

  TF-1 close-commits-the-take      Esc is this window's Close key. Closing
                                   while the tape was rolling stopped the
                                   recorder and threw away the WAV path it
                                   returns: the file sat in the takes folder
                                   with nothing pointing at it, and nothing in
                                   this app opens a loose WAV.
  TF-2 new-keeps-the-take-in-hand  File > New laid the floor BEFORE it stopped
                                   the transport, so the kept file was written
                                   without the take being performed into it.
  TF-3 unsaved-takes-are-kept      "A project already written to a file is
                                   safe on disk" is only true of the takes
                                   that were there at Save time. Nothing
                                   writes the named file behind File > Save,
                                   so takes recorded after it lived only in
                                   the window and in the recovery store New
                                   was about to overwrite.
  TF-4 unchanged-project-not-kept  ...and the floor must not lay a copy of a
                                   project the file beside it already holds,
                                   or Documents fills with duplicates.

Run under the guest theme:
  tools/guestrun.sh python3 tools/sequencer_take_floor_selftest.py
"""
import os
import sys
import json
import math
import wave
import struct
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="sequencer-floor-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                   # noqa: E402

PROMISED = [
    "TF-1 close-commits-the-take",
    "TF-2 new-keeps-the-take-in-hand",
    "TF-3 unsaved-takes-are-kept",
    "TF-4 unchanged-project-not-kept",
]
REPORTED = {}


def check(name, ok, detail=""):
    ok = bool(ok)
    REPORTED[name] = ok
    print(("PASS " if ok else "FAIL ") + name
          + (("  -- " + str(detail)) if (detail and not ok) else ""))


class StandInRecorder:
    """The microphone, without one. Writes a real WAV of the length the take
    actually ran for, so every check downstream (wav_peak, take_length) sees a
    genuine recording rather than a mock."""

    monitor_failed = False

    def __init__(self):
        self.proc = None
        self.path = None
        self.monitoring = False
        self._t0 = 0.0

    def start(self, device, path, monitor=False):
        import time
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path, self.proc, self._t0 = path, object(), time.monotonic()
        self.monitoring = bool(monitor)
        return True, ""

    def start_monitor(self):
        self.monitoring = True
        return True, ""

    def stop_monitor(self):
        self.monitoring = False

    def level(self):
        return 0.4 if self.proc is not None else 0.0

    def stop(self):
        import time
        if self.proc is None:
            return None
        self.proc = None
        path, self.path = self.path, None
        dur = max(1.0, time.monotonic() - self._t0)
        w = wave.open(path, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / 48000)))
            for i in range(int(48000 * dur))))
        w.close()
        return path

    def failed_early(self):
        return False

    def write_failed(self):
        return False


def drive(tag):
    d = appdrive.Drive("sequencer", home=os.path.join(HOME_ROOT, tag))
    d.app.recorder = StandInRecorder()
    d.app.countin = False          # the count-in is not what is under test
    return d


def roll(d, track, seconds):
    """Arm one lane and record into it for `seconds`, leaving it ROLLING."""
    app = d.app
    for i, tk in enumerate(app.tracks):
        if tk["armed"] != (i == track):
            app.track_widgets[i]["arm"].clicked()
    d.pump(0.1)
    app._on_rec()
    d.pump(seconds)


def take(d, track, seconds):
    roll(d, track, seconds)
    d.app._on_rec()
    d.pump(0.3)


def clip_count(app):
    return sum(len(tk["clips"]) for tk in app.tracks)


def store_clips(seq):
    try:
        with open(seq.CFG_FILE) as fh:
            data = json.load(fh)
        return sum(len(t.get("clips") or []) for t in data["tracks"])
    except Exception:                                             # noqa: BLE001
        return -1


def docs_json(seq):
    try:
        return sorted(f for f in os.listdir(seq.PROJ_DIR)
                      if f.endswith(".json"))
    except OSError:
        return []


def file_clips(seq, name):
    try:
        with open(os.path.join(seq.PROJ_DIR, name)) as fh:
            data = json.load(fh)
        return sum(len(t.get("clips") or []) for t in data["tracks"])
    except Exception:                                             # noqa: BLE001
        return -1


def wavs_on_lanes(app):
    """Every WAV a clip points at — a take that is 'kept' but unreferenced is
    not kept at all."""
    return {c.get("wav") for tk in app.tracks for c in tk["clips"]}


# ------------------------------------------------- TF-1 closing mid-take
def tf1():
    d = drive("close")
    app, seq = d.app, d.mod
    try:
        roll(d, 0, 3.0)
        rolling = app.transport == "rec"
        d.key("Escape")             # File ▸ Close's own key
        d.pump(0.5)
        on_lane = wavs_on_lanes(app)
        check("TF-1 close-commits-the-take",
              rolling and clip_count(app) == 1 and store_clips(seq) == 1
              and all(w and os.path.isfile(w) for w in on_lane),
              "rolling=%r tape=%d store=%d wavs=%r"
              % (rolling, clip_count(app), store_clips(seq), on_lane))
    finally:
        d.close()


# --------------------------------------------------- TF-2 New mid-take
def tf2():
    d = drive("newroll")
    app, seq = d.app, d.mod
    try:
        roll(d, 0, 3.0)
        app.set_focus(None)
        d.key("n", ctrl=True)
        d.pump(0.5)
        kept = docs_json(seq)
        check("TF-2 new-keeps-the-take-in-hand",
              len(kept) == 1 and file_clips(seq, kept[0]) == 1
              and clip_count(app) == 0,
              "Documents=%r kept-clips=%r tape=%d"
              % (kept, [file_clips(seq, k) for k in kept], clip_count(app)))
    finally:
        d.close()


# ------------------------------------ TF-3 / TF-4 saved is not the same as in
def tf34():
    d = drive("saved")
    app, seq = d.app, d.mod
    try:
        import nbpicker
        named = os.path.join(seq.PROJ_DIR, "Kitchen demo.json")
        os.makedirs(seq.PROJ_DIR, exist_ok=True)
        nbpicker.save_file = lambda *a, **k: named
        take(d, 0, 2.5)
        app.set_focus(None)
        d.menu_action("File", "Save As")
        d.pump(0.3)
        base = docs_json(seq)

        # nothing has happened since the save: the file holds this project
        d.menu_action("File", "New")
        d.pump(0.4)
        check("TF-4 unchanged-project-not-kept", docs_json(seq) == base,
              "Documents=%r (was %r)" % (docs_json(seq), base))

        # ...now open it again and record a take the file has never seen
        nbpicker.open_file = lambda *a, **k: named
        d.menu_action("File", "Open")
        d.pump(0.4)
        take(d, 1, 2.5)
        before = clip_count(app)
        d.menu_action("File", "New")
        d.pump(0.5)
        now = [f for f in docs_json(seq) if f not in base]
        check("TF-3 unsaved-takes-are-kept",
              before == 2 and len(now) == 1 and file_clips(seq, now[0]) == 2,
              "on the tape=%d new files=%r holding=%r"
              % (before, now, [file_clips(seq, f) for f in now]))
    finally:
        d.close()


def main():
    for stage in (tf1, tf2, tf34):
        try:
            stage()
        except Exception as exc:                                  # noqa: BLE001
            print("NOTE %s raised %s: %s" % (stage.__name__,
                                             type(exc).__name__, exc))
    for name in PROMISED:
        if name not in REPORTED:
            check(name, False, "the drive never reached this check")
    failed = [n for n in PROMISED if not REPORTED.get(n)]
    print("\n%d checks, %d failed" % (len(PROMISED), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
