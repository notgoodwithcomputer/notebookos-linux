#!/usr/bin/env python3
"""What the Sequencer says about the project is true of the project.

Three places where the window described a state it was no longer in.

  ST-1 undo-keeps-the-file      A banked step carries the file the window was
                                attached to, so undoing an Open puts the
                                previous project back. A step banked BEFORE a
                                Save As carried no file at all, so one Ctrl+Z
                                past the save detached the project from the
                                file that had just been written: "Not saved to
                                a file" beside a file that plainly existed, and
                                the next Ctrl+S asking for a name again.
  ST-1 undo-of-new-keeps-its-own-file
                                ...and the rebase is scoped: a step from
                                before a New still points at the file that New
                                left behind, not at the one saved since.
  ST-2 message-goes-with-its-step
                                "Trims or removes N recorded takes past the
                                new end. Undo (Ctrl+Z) puts them back." is only
                                true while that IS the step Undo would take.
                                One more click on LENGTH left it on screen over
                                a tape that had just lost nothing.
  ST-4 a-full-disk-is-not-a-bad-microphone
                                The pump can hit a full disk while Stop is
                                still draining the input's pipe. Asked only
                                BEFORE the drain, the app fell through to
                                "Nothing was recorded — try another input" and
                                sent someone off to test a microphone that was
                                working perfectly.
  ST-5 a-step-back-takes-something-back
                                The loop belongs to the project and banks a
                                step like every other project control — but a
                                drag across empty tape that snapped to the two
                                bars already looped banked one anyway, so
                                Ctrl+Z visibly did nothing and the edit the
                                user wanted back moved a press further away.
  ST-3 caption-keeps-its-words  The EDIT bar's caption is the only line saying
                                which take is open, on which track, at which
                                bar and how long. Ellipsizing labels ask for
                                nothing, so on a narrow panel it collapsed to a
                                bare "…" instead of letting the row scroll.

Run under the guest theme:
  tools/guestrun.sh python3 tools/sequencer_status_truth_selftest.py
"""
import os
import sys
import math
import wave
import struct
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="sequencer-truth-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                   # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

PROMISED = [
    "ST-1 undo-keeps-the-file",
    "ST-1 undo-of-new-keeps-its-own-file",
    "ST-2 message-goes-with-its-step",
    "ST-3 caption-keeps-its-words",
    "ST-4 a-full-disk-is-not-a-bad-microphone",
    "ST-5 a-step-back-takes-something-back",
]
REPORTED = {}
# Twenty characters of the caption face. Measured, not guessed: the fixed
# label is allocated ~150px at this panel width and the unfixed one ~10 (the
# width of an ellipsis alone), so anything between the two separates them.
CAPTION_MIN_PX = 80


def check(name, ok, detail=""):
    ok = bool(ok)
    REPORTED[name] = ok
    print(("PASS " if ok else "FAIL ") + name
          + (("  -- " + str(detail)) if (detail and not ok) else ""))


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
    app._remember("Record")
    c = seq.clip_make(start, start + dur, take_wav(seq, name, dur), 0.0)
    app.tracks[track]["clips"].append(c)
    app._save()
    app.refresh()
    return c


# ------------------------------------------------ ST-1 saving gives a home
def st1():
    d = drive("saveid")
    app, seq = d.app, d.mod
    try:
        import nbpicker
        os.makedirs(seq.PROJ_DIR, exist_ok=True)
        first = os.path.join(seq.PROJ_DIR, "Kitchen demo.json")
        inject(app, seq, 0, 0.0, 4.0, "one.wav")
        e = app.track_widgets[0]["name"]
        e.grab_focus()
        e.select_region(0, -1)
        d.type("Rhythm gtr")
        d.pump(0.3)
        app.set_focus(None)
        nbpicker.save_file = lambda *a, **k: first
        d.menu_action("File", "Save As")
        d.pump(0.3)
        saved_path, saved_lbl = app._path, app.proj_lbl.get_text()
        d.key("z", ctrl=True)          # takes back the rename, not the save
        d.pump(0.3)
        check("ST-1 undo-keeps-the-file",
              saved_path == first and app._path == first
              and "Not saved" not in app.proj_lbl.get_text()
              and app.tracks[0]["name"] != "Rhythm gtr",
              "saved as %r (%r); after undo path=%r status=%r name=%r"
              % (saved_path, saved_lbl, app._path,
                 app.proj_lbl.get_text(), app.tracks[0]["name"]))

        # a step from before a New keeps pointing at what New left behind
        d.key("z", ctrl=True, shift=True)             # redo the rename
        d.pump(0.2)
        d.menu_action("File", "New")
        d.pump(0.4)
        second = os.path.join(seq.PROJ_DIR, "Second song.json")
        nbpicker.save_file = lambda *a, **k: second
        inject(app, seq, 3, 0.0, 2.0, "two.wav")
        d.menu_action("File", "Save As")
        d.pump(0.3)
        for _ in range(4):                            # back past the New
            d.key("z", ctrl=True)
            d.pump(0.15)
            if app._path == first:
                break
        check("ST-1 undo-of-new-keeps-its-own-file", app._path == first,
              "walked back to path=%r (wanted %r)" % (app._path, first))
    finally:
        d.close()


# ------------------------------------------- ST-2 a message about a step
def st2():
    d = drive("length")
    app, seq = d.app, d.mod
    try:
        inject(app, seq, 0, 50.0, 4.0, "late.wav")
        chip = [w for w in d.find(Gtk.Button)
                if w.get_style_context().has_class("lenbtn")][0]
        for _ in range(5):            # 32 -> 48 -> 64 -> 96 -> 128 -> 8 bars
            chip.clicked()
            d.pump(0.1)
        trimmed = app.proj_lbl.get_text()
        lost_here = sum(len(tk["clips"]) for tk in app.tracks)
        chip.clicked()                # 8 -> 16 bars: nothing left to lose
        d.pump(0.2)
        after = app.proj_lbl.get_text()
        check("ST-2 message-goes-with-its-step",
              "recorded take" in trimmed and lost_here == 0
              and "recorded take" not in after,
              "trimming said %r; the next click left %r" % (trimmed, after))
    finally:
        d.close()


# ------------------------------------------ ST-3 the caption keeps its words
def st3():
    d = drive("caption")
    app, seq = d.app, d.mod
    try:
        c = inject(app, seq, 0, 0.0, 4.0, "cap.wav")
        app.select_clip(0, c)
        app.view_btns["edit"].clicked()
        d.pump(0.3)
        d.resize(800, 600)
        d.pump(0.3)
        wide = app.clip_lbl.get_allocated_width()
        check("ST-3 caption-keeps-its-words",
              wide >= CAPTION_MIN_PX and app.clip_lbl.get_text().strip(),
              "caption %r allocated %dpx of a needed %d at 800x600"
              % (app.clip_lbl.get_text(), wide, CAPTION_MIN_PX))
    finally:
        d.close()


class LateFullDisk:
    """A recorder whose WAV write fails on the last chunk: the failure is only
    knowable once stop() has drained what the input had already queued."""

    monitor_failed = False
    monitoring = False

    def __init__(self):
        self.proc = None
        self._failed = False

    def start(self, device, path, monitor=False):
        self.proc, self._failed = object(), False
        return True, ""

    def start_monitor(self):
        return True, ""

    def stop_monitor(self):
        pass

    def level(self):
        return 0.0

    def stop(self):
        self.proc = None
        self._failed = True      # found while draining, not before
        return None              # a partial WAV is not a take

    def failed_early(self):
        return self._failed

    def write_failed(self):
        return self._failed


# ------------------------------------------- ST-4 blame the right thing
def st4():
    d = drive("fulldisk")
    app = d.app
    try:
        app.recorder = LateFullDisk()
        app.countin = False
        app.track_widgets[0]["arm"].clicked()
        d.pump(0.1)
        app._on_rec()
        d.pump(1.2)
        app._on_rec()
        d.pump(0.3)
        said = app.proj_lbl.get_text()
        check("ST-4 a-full-disk-is-not-a-bad-microphone",
              "no room left" in said and "another input" not in said,
              "status=%r" % said)
    finally:
        d.close()


# ------------------------------------- ST-5 an edit that is not a change
def st5():
    d = drive("loop")
    app, seq = d.app, d.mod
    try:
        inject(app, seq, 0, 0.0, 4.0, "loop.wav")
        app._set_loop(0.0, 8.0)
        d.pump(0.1)
        depth = len(app._undo_stack)
        app._set_loop(0.0, 8.0)          # the same two bars, dragged again
        d.pump(0.1)
        again = len(app._undo_stack)
        app._set_loop(8.0, 16.0)         # ...and a real change still banks
        d.pump(0.1)
        moved = len(app._undo_stack)
        check("ST-5 a-step-back-takes-something-back",
              again == depth and moved == depth + 1
              and app.history.undo_label() == "Loop",
              "history %d -> %d (same bars) -> %d (new bars), label=%r"
              % (depth, again, moved, app.history.undo_label()))
    finally:
        d.close()


def main():
    for stage in (st1, st2, st3, st4, st5):
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
