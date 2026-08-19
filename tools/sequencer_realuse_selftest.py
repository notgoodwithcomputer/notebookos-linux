#!/usr/bin/env python3
"""Real-use regression drive for the Sequencer, on the real widget tree.

Each check below is something a person did with the app and got wrong, driven
the way she did it (the real deck buttons, the real lanes and ruler, the real
File menu, the real confirm cards) through tools/appdrive on an offscreen
holder at the 1024x740 panel. Every check is named, and a name that is never
reported is printed as a failure at the end, so a check fails by name and never
by crash.

  SQ-1 new-keeps       File > New / Ctrl+N and File > Open replace the whole
                       arrangement AND session recovery, so takes that exist
                       only in this window are written to Documents as a real
                       project first and the notification names the file (this
                       OS gives destruction undo, not a question, and undo
                       lives only as long as the window); a project already
                       bound to a file is not kept a second time
  SQ-2 length-says     wrapping LENGTH from 128 bars back round to 8 says how
                       many takes it trimmed or dropped instead of doing it in
                       silence, and the Edit menu names the step it banks
  SQ-3 undo-scope      Undo takes back the LAST edit only: a rename, a mute
                       and a fader made after a clip was moved survive it, and
                       the zoom does not jump
  SQ-4 timeline-focus  a press on a lane or the ruler takes the keyboard off
                       the track-name box, so S / Delete / Space split, remove
                       and play instead of typing into the name
  SQ-5 count-in        with the shipped defaults (metronome off, count-in on)
                       the pre-roll bar is clicked and the deck counts it
                       down, and the click goes off as the take starts
  SQ-6 flash-clears    a transient status message restores the project status
                       instead of sitting there contradicting the screen
  SQ-7 knob-clear      a mixer slider at its minimum does not land on the
                       caption beside it, and the master strip still fits 1024
  SQ-8 bar-singular    a one-bar clip's plate says "1 bar", not "1 bars"
  SQ-9 wording         the bin, the menu and the cards they open use one noun,
                       the EDIT tab says what EDIT is for, and Repeat names
                       itself in the Edit menu
  SQ-10 name-box       a name box left blank goes back to the track's own
                       name, and a long name is shown from its first letter
  SQ-12 strip-heights  a CJK track name does not push its mixer strip down

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \\
      tools/sequencer_realuse_selftest.py
"""
import os
import sys
import json
import math
import wave
import time
import struct
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="sequencer-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                   # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

PROMISED = [
    "SQ-1 new-keeps-the-takes", "SQ-1 kept-file-is-a-real-project",
    "SQ-1 saved-project-not-kept-again", "SQ-1 open-keeps-the-takes",
    "SQ-2 length-says-what-it-took", "SQ-2 length-named-step",
    "SQ-2 undo-puts-the-takes-back",
    "SQ-3 undo-keeps-later-edits", "SQ-3 undo-keeps-the-view",
    "SQ-3 undo-took-back-the-last-edit-only",
    "SQ-4 timeline-takes-focus", "SQ-4 shortcuts-reach-the-timeline",
    "SQ-5 count-in-clicks", "SQ-5 count-in-is-audible",
    "SQ-5 count-in-counts-down", "SQ-5 click-off-at-punch-in",
    "SQ-6 flash-clears",
    "SQ-7 knob-clears-caption", "SQ-7 master-strip-fits",
    "SQ-8 bar-singular",
    "SQ-9 edit-tab-tooltip", "SQ-9 repeat-names-itself",
    "SQ-9 empty-edit-prompt",
    "SQ-10 blank-name-falls-back", "SQ-10 long-name-shows-its-head",
    "SQ-12 strip-heights-match",
]
REPORTED = {}


def check(name, ok, detail=""):
    ok = bool(ok)
    REPORTED[name] = ok
    print(("PASS " if ok else "FAIL ") + name
          + (("  -- " + str(detail)) if (detail and not ok) else ""))


# ------------------------------------------------------------------ fixtures
def drive(tag):
    return appdrive.Drive("sequencer", home=os.path.join(HOME_ROOT, tag))


def take_wav(seq, name, dur):
    os.makedirs(seq.TAKES_DIR, exist_ok=True)
    p = os.path.join(seq.TAKES_DIR, name)
    w = wave.open(p, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes(b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / 48000)))
        for i in range(int(48000 * dur))))
    w.close()
    return p


def inject(app, seq, track, start, dur, name):
    """A recorded take on a lane, banked the way a real one is."""
    app._remember("Record")
    c = seq.clip_make(start, start + dur, take_wav(seq, name, dur), 0.0)
    app.tracks[track]["clips"].append(c)
    app._save()
    app.refresh()
    return c


def clip_count(app):
    return sum(len(tk["clips"]) for tk in app.tracks)


def store_clips(seq):
    with open(seq.CFG_FILE) as fh:
        return sum(len(t.get("clips") or []) for t in json.load(fh)["tracks"])


def card(app):
    """Every label on the open confirm card, or None when none is open."""
    layer = app._prompt_layer
    if layer is None:
        return None
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            out.append(w.get_text())
        if isinstance(w, Gtk.Container):
            for ch in w.get_children():
                walk(ch)
    walk(layer)
    return out


def card_button(app, label):
    hits = []

    def walk(w):
        if isinstance(w, Gtk.Button) and w.get_label() == label:
            hits.append(w)
        if isinstance(w, Gtk.Container):
            for ch in w.get_children():
                walk(ch)
    if app._prompt_layer is not None:
        walk(app._prompt_layer)
    return hits[0] if hits else None


def menu_labels(app, menu):
    return [it[0] for it in app.menu_items(menu)
            if isinstance(it, (tuple, list))]


def docs_json(seq):
    """The project files in Documents — none at all is an empty list, not a
    stack trace, so a check that expects one fails by name."""
    try:
        return sorted(f for f in os.listdir(seq.PROJ_DIR)
                      if f.endswith(".json"))
    except OSError:
        return []


def lane_x(app, t):
    return app.px_of_time(t, app.lanes[0].get_allocated_width())


# ------------------------------------------------------- SQ-1 New replaces
def sq1():
    d = drive("new")
    app, seq = d.app, d.mod
    try:
        for i in range(4):
            inject(app, seq, i, i * 4.0, 4.0, "t%d.wav" % i)
        app.set_focus(None)
        d.key("n", ctrl=True)
        d.pump(0.3)
        kept = docs_json(seq)
        check("SQ-1 new-keeps-the-takes",
              len(kept) == 1 and clip_count(app) == 0
              and store_clips(seq) == 0,
              "Documents=%r tape=%d store=%d"
              % (kept, clip_count(app), store_clips(seq)))
        data = {}
        if kept:
            with open(os.path.join(seq.PROJ_DIR, kept[0])) as fh:
                data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
        check("SQ-1 kept-file-is-a-real-project",
              app._is_project(data)
              and sum(len(t.get("clips") or [])
                      for t in data.get("tracks", [])) == 4,
              "kept file holds %r" % (list(data),))

        # a project with a file of its own is already on disk
        import nbpicker
        saved = os.path.join(HOME_ROOT, "saved-project.json")
        nbpicker.save_file = lambda *a, **k: saved
        inject(app, seq, 0, 0.0, 4.0, "saved.wav")
        d.menu_action("File", "Save As")
        d.pump(0.2)
        d.menu_action("File", "New")
        d.pump(0.3)
        check("SQ-1 saved-project-not-kept-again",
              docs_json(seq) == kept and clip_count(app) == 0,
              "Documents=%r tape=%d" % (docs_json(seq), clip_count(app)))

        # ...and Open replaces the arrangement for the same reasons New does
        inject(app, seq, 5, 2.0, 4.0, "before-open.wav")
        nbpicker.open_file = lambda *a, **k: saved
        d.menu_action("File", "Open")
        d.pump(0.3)
        now = docs_json(seq)
        check("SQ-1 open-keeps-the-takes",
              len(now) == len(kept) + 1 and clip_count(app) == 1,
              "Documents=%r tape=%d" % (now, clip_count(app)))
    finally:
        d.close()


# ------------------------------------------------- SQ-2 shortening speaks
def sq2():
    d = drive("length")
    app, seq = d.app, d.mod
    try:
        inject(app, seq, 0, 30.0, 4.0, "late.wav")
        inject(app, seq, 1, 14.0, 4.0, "straddle.wav")
        before = [(round(c["s"], 2), round(c["e"], 2))
                  for tk in app.tracks for c in tk["clips"]]
        chip = [w for w in d.find(Gtk.Button)
                if w.get_style_context().has_class("lenbtn")][0]
        for _ in range(5):                    # 32 -> 48 -> 64 -> 96 -> 128 -> 8
            chip.clicked()
            d.pump(0.1)
        flash = app.proj_lbl.get_text()
        after = [(round(c["s"], 2), round(c["e"], 2))
                 for tk in app.tracks for c in tk["clips"]]
        check("SQ-2 length-says-what-it-took",
              after != before and "2 recorded takes" in flash,
              "flash=%r clips=%r (were %r)" % (flash, after, before))
        check("SQ-2 length-named-step",
              app.history.undo_label() == "Set Length",
              "label=%r" % app.history.undo_label())
        d.key("z", ctrl=True)
        d.pump(0.2)
        back = [(round(c["s"], 2), round(c["e"], 2))
                for tk in app.tracks for c in tk["clips"]]
        check("SQ-2 undo-puts-the-takes-back", back == before,
              "clips=%r (were %r)" % (back, before))
    finally:
        d.close()


# --------------------------------------------- SQ-3 Undo is the LAST edit
def sq3():
    d = drive("undo")
    app, seq = d.app, d.mod
    try:
        inject(app, seq, 2, 4.0, 4.0, "m.wav")
        lane = app.lanes[2]
        d.drag(lane, [(lane_x(app, 6.0), 20), (lane_x(app, 8.0), 20),
                      (lane_x(app, 12.0), 20)])
        d.pump(0.1)
        moved = [(round(c["s"], 2), round(c["e"], 2))
                 for c in app.tracks[2]["clips"]]
        e = app.track_widgets[3]["name"]
        e.grab_focus()
        e.select_region(0, -1)
        d.type("Vocals")
        d.pump(0.3)
        app.set_focus(None)
        app.track_widgets[4]["mute"].clicked()
        d.pump(0.05)
        app.track_widgets[5]["gain"].set_value(60)
        d.pump(0.3)
        app._zoom_step(seq.ZOOM_STEP)
        app._zoom_step(seq.ZOOM_STEP)
        d.pump(0.2)
        zoom = app.zoom
        # one step back: the fader was the last edit, so the fader is what
        # comes back — the rename and the mute made before it stay made
        d.key("z", ctrl=True)
        d.pump(0.2)
        kept = (app.tracks[3]["name"] == "Vocals"
                and app.tracks[4]["muted"] is True
                and app.tracks[5]["gain"] == 100
                and app.track_widgets[3]["name"].get_text() == "Vocals")
        check("SQ-3 undo-keeps-later-edits", kept,
              "name=%r muted=%r gain=%r entry=%r"
              % (app.tracks[3]["name"], app.tracks[4]["muted"],
                 app.tracks[5]["gain"],
                 app.track_widgets[3]["name"].get_text()))
        check("SQ-3 undo-keeps-the-view", abs(app.zoom - zoom) < 1e-6,
              "zoom %r -> %r" % (zoom, app.zoom))
        # and the clip is still where the drag put it: the step taken back was
        # the fader, not the move four edits ago
        check("SQ-3 undo-took-back-the-last-edit-only",
              [(round(c["s"], 2), round(c["e"], 2))
               for c in app.tracks[2]["clips"]] == moved,
              "clip moved back to %r (was %r)"
              % ([(round(c["s"], 2), round(c["e"], 2))
                  for c in app.tracks[2]["clips"]], moved))
    finally:
        d.close()


# ------------------------------------------- SQ-4 / SQ-10 the name box
def sq4():
    d = drive("focus")
    app, seq = d.app, d.mod
    try:
        inject(app, seq, 2, 4.0, 4.0, "f.wav")
        e = app.track_widgets[2]["name"]
        e.grab_focus()
        e.select_region(0, -1)
        d.type("Chorus 2")
        d.pump(0.3)
        lane = app.lanes[2]
        d.press(lane, lane_x(app, 6.0), 20)
        d.pump(0.1)
        focused = app.get_focus()
        check("SQ-4 timeline-takes-focus",
              not isinstance(focused, Gtk.Editable) and app.sel is not None,
              "focus=%r sel=%r" % (type(focused).__name__, app.sel))
        app.pos = 6.0
        d.key("s")
        d.pump(0.1)
        split = len(app.tracks[2]["clips"])
        named = app.tracks[2]["name"]
        d.press(lane, lane_x(app, 5.0), 20)   # one of the two halves
        d.pump(0.1)
        d.key("Delete")
        d.pump(0.1)
        removed = len(app.tracks[2]["clips"])
        d.key("space")
        d.pump(0.1)
        playing = app.transport
        app._stop_transport()
        check("SQ-4 shortcuts-reach-the-timeline",
              split == 2 and named == "Chorus 2" and removed == 1
              and playing == "play",
              "split=%d name=%r after Delete=%d transport=%r"
              % (split, named, removed, playing))

        # SQ-10, the same box: emptied, and far too long for it
        e2 = app.track_widgets[1]["name"]
        e2.grab_focus()
        e2.select_region(0, -1)
        d.type("Café 日本 - vox!")
        d.pump(0.3)
        e3 = app.track_widgets[3]["name"]
        e3.grab_focus()
        e3.select_region(0, -1)
        d.key("BackSpace")
        d.pump(0.3)
        d.press(app.ruler, 120, 10)          # done typing: back to the tape
        d.pump(0.2)
        check("SQ-10 blank-name-falls-back",
              e3.get_text() == app.tracks[3]["name"] == "Track 4",
              "entry=%r model=%r" % (e3.get_text(), app.tracks[3]["name"]))
        check("SQ-10 long-name-shows-its-head",
              e2.get_position() == 0 and e2.get_text() == "Café 日本 - vox!",
              "position=%d text=%r" % (e2.get_position(), e2.get_text()))
    finally:
        d.close()


# ------------------------------------------------------- SQ-5 the count-in
class FakeEngine:
    """The sound engine's answers, without a sound device: what the app ASKS
    for is the thing under test (which click, and when it is taken off)."""
    available = True
    failed = False
    bypassed = False
    underruns = 0

    def __init__(self):
        self.started = []
        self.updates = []
        self._pos = 0.0
        self._render = 0.0

    def start(self, song, at=0.0, metronome=False):
        self.started.append((at, bool(metronome)))
        self._pos = self._render = float(at)
        return True

    def update(self, song):
        self.updates.append(bool(song.get("metronome")))

    def stop(self):
        pass

    def shutdown(self):
        pass

    def position(self):
        return self._pos

    def render_position(self):
        return self._render

    def peaks(self):
        return (0.0, 0.0)

    def track_peaks(self):
        return []


def sq5():
    d = drive("countin")
    app = d.app
    try:
        eng = FakeEngine()
        app.engine = eng
        app._start_capture = lambda *a, **k: None
        defaults = (app.metronome is False and app.countin is True)
        app.track_widgets[0]["arm"].clicked()
        d.pump(0.1)
        app._begin_record()
        d.pump(0.05)
        app.refresh()
        check("SQ-5 count-in-clicks",
              defaults and eng.started
              and eng.started[-1] == (-app.sec_per_bar(), True),
              "defaults=%r start=%r" % (defaults, eng.started))
        # ...and what it asked for is a bar of actual sound: the renderer
        # only clicks when the song it is handed says to, which is the whole
        # reason a count-in with the metronome off used to be silent
        import array                                              # noqa: PLC0415
        import nbsynth                                            # noqa: PLC0415
        at, metro = eng.started[-1] if eng.started else (0.0, False)
        mix = nbsynth.Mixdown(app._song(), at, metronome=metro)
        peak = 0
        for _ in range(int(abs(at) * nbsynth.SR / nbsynth.BLOCK)):
            block = array.array("h")
            block.frombytes(bytes(mix.render(nbsynth.BLOCK)))
            peak = max(peak, max(abs(x) for x in block) if len(block) else 0)
        check("SQ-5 count-in-is-audible", peak > 0,
              "the pre-roll bar renders at peak %d" % peak)

        first = app.statuslbl.get_text()
        eng._pos, eng._render = -1.0, -0.6
        app._runner()
        d.pump(0.05)
        counting = app.statuslbl.get_text()
        check("SQ-5 count-in-counts-down",
              first.startswith("Counting in") and counting == "Counting in 2"
              and abs(app.pos - app.rec_start) < 1e-6,
              "first=%r then=%r pos=%r" % (first, counting, app.pos))
        clicked_into_take = list(eng.updates)
        eng._pos, eng._render = -0.05, 0.02
        app._runner()
        d.pump(0.05)
        off = list(eng.updates)
        eng._pos = eng._render = 0.4
        app._runner()
        d.pump(0.05)
        check("SQ-5 click-off-at-punch-in",
              clicked_into_take == [] and off == [False]
              and app.statuslbl.get_text() == "Recording",
              "before=%r at punch-in=%r status=%r"
              % (clicked_into_take, off, app.statuslbl.get_text()))
        app.transport = "stop"
    finally:
        d.close()


# --------------------------------------------------- SQ-6 / SQ-8 the words
def sq6():
    d = drive("flash")
    app, seq = d.app, d.mod
    try:
        app._flash("There is nothing in the arrangement to export")
        d.pump(0.2)
        up = app.proj_lbl.get_text()
        d.pump(getattr(seq, "FLASH_MS", 6000) / 1000.0 + 1.0)
        check("SQ-6 flash-clears",
              "nothing in the arrangement" in up
              and app.proj_lbl.get_text() == "Not saved to a file",
              "flashed=%r then=%r" % (up, app.proj_lbl.get_text()))

        c = inject(app, seq, 0, 0.0, 2.0, "onebar.wav")
        try:
            plate = app.lanes[0]._clip_label(c)
        except Exception as exc:                                  # noqa: BLE001
            plate = "raised %s: %s" % (type(exc).__name__, exc)
        check("SQ-8 bar-singular", plate == "1 bar · 2.00 s",
              "plate=%r (sec_per_bar=%r)" % (plate, app.sec_per_bar()))
    finally:
        d.close()


def sq9():
    d = drive("words")
    app, seq = d.app, d.mod
    try:
        c = inject(app, seq, 0, 0.0, 2.0, "onebar.wav")
        app.refresh()
        check("SQ-9 edit-tab-tooltip",
              "take" in (app.view_btns["edit"].get_tooltip_text() or ""),
              "tip=%r" % app.view_btns["edit"].get_tooltip_text())

        app.select_clip(0, c)
        app.pos = 1.0
        app.refresh()
        d.menu_action("Track", "Repeat Clip to")
        d.pump(0.1)
        check("SQ-9 repeat-names-itself",
              app.history.undo_label() == "Repeat Clip to the End",
              "label=%r" % app.history.undo_label())

        for tk in app.tracks:
            tk["clips"] = []
        app.sel = None
        app.refresh()
        app.view_btns["edit"].clicked()
        d.pump(0.1)
        prompt = app.proj_lbl.get_text()
        check("SQ-9 empty-edit-prompt",
              "Draw" not in prompt and "Record" in prompt,
              "prompt=%r" % prompt)
    finally:
        d.close()


# -------------------------------------------------- SQ-7 / SQ-12 the mixer
def sq7():
    d = drive("mixer")
    app = d.app
    try:
        app.track_widgets[1]["name"].set_text("Café 日本 - vox!")
        d.pump(0.2)
        app.view_btns["mix"].clicked()
        d.pump(0.3)
        cap = [w for w in d.walk()
               if isinstance(w, Gtk.Label) and w.get_text() == "LO CUT"][0]
        scale = cap.get_parent().get_children()[1]
        knob = scale.style_get_property("slider-width") or 14
        ca, sa = cap.get_allocation(), scale.get_allocation()
        knob_left = sa.x - knob / 2.0
        check("SQ-7 knob-clears-caption",
              knob_left >= ca.x + ca.width,
              "caption ends at %d, knob at the minimum starts at %.1f"
              % (ca.x + ca.width, knob_left))
        strip = [w for w in d.walk()
                 if w.get_style_context().has_class("masterstrip")][0]
        xy = strip.translate_coordinates(d.child, 0, 0)
        right = (xy[0] if xy else 0) + strip.get_allocation().width
        check("SQ-7 master-strip-fits", right <= d.w,
              "master strip ends at %d of %d" % (right, d.w))
        heights = [s["name"].get_allocation().height for s in app.strips]
        check("SQ-12 strip-heights-match",
              len(set(heights)) == 1,
              "stripname heights %r for %r"
              % (heights, [s["name"].get_text() for s in app.strips]))
    finally:
        d.close()


def main():
    for stage in (sq1, sq2, sq3, sq4, sq5, sq6, sq9, sq7):
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
