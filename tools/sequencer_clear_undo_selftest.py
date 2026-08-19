#!/usr/bin/env python3
"""Emptying a track, or the tape, acts at once and says what went.

UNDO REPLACES CONFIRMATION is an OS-wide decision, and these two actions are
among the ones it was decided for (see the campaign's own list: "sequencer
clear-track/shorten/clear-all"). A card asking "Clear this track's takes?"
whose second sentence read "Undo (Ctrl+Z) puts them back" was answering its own
question — a second click on every deliberate use, to guard against an
accidental one Ctrl+Z already covers.

What the retirement must not become is a silent one, so this pins both halves.

  CU-1 bin-acts-at-once        the bin on a track head empties that track
                               there and then, with no card in the way
  CU-2 bin-says-what-went      ...and the sentence that used to be on the card
                               is on the status line, naming the count and the
                               track
  CU-3 bin-is-one-step-back    ...over a step the Edit menu names, which puts
                               the clips back
  CU-4 remove-every-clip-acts  the same for Track ▸ Remove Every Clip, which
                               says how many went across all eight lanes
  CU-5 remove-every-clip-asks-nothing
                               ...so its menu label carries no ellipsis:
                               MENU-CONVENTIONS §1 makes "…" a promise of a
                               dialog, and there is no longer one to open

Run under the guest theme:
  tools/guestrun.sh python3 tools/sequencer_clear_undo_selftest.py
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
                      tempfile.mkdtemp(prefix="sequencer-clear-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                   # noqa: E402

PROMISED = [
    "CU-1 bin-acts-at-once", "CU-2 bin-says-what-went",
    "CU-3 bin-is-one-step-back", "CU-4 remove-every-clip-acts",
    "CU-5 remove-every-clip-asks-nothing",
]
REPORTED = {}


def check(name, ok, detail=""):
    ok = bool(ok)
    REPORTED[name] = ok
    print(("PASS " if ok else "FAIL ") + name
          + (("  -- " + str(detail)) if (detail and not ok) else ""))


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
    app.tracks[track]["clips"].append(
        seq.clip_make(start, start + dur, take_wav(seq, name, dur), 0.0))
    app._save()
    app.refresh()


def clip_count(app):
    return sum(len(tk["clips"]) for tk in app.tracks)


def main():
    d = appdrive.Drive("sequencer", home=os.path.join(HOME_ROOT, "clear"))
    app, seq = d.app, d.mod
    try:
        app.track_widgets[2]["name"].set_text("Rhythm gtr")
        d.pump(0.1)
        app.set_focus(None)
        inject(app, seq, 2, 0.0, 4.0, "a.wav")
        inject(app, seq, 2, 8.0, 4.0, "b.wav")
        inject(app, seq, 5, 4.0, 4.0, "c.wav")
        before = clip_count(app)

        app.track_widgets[2]["clr"].clicked()
        d.pump(0.3)
        check("CU-1 bin-acts-at-once",
              before == 3 and app._prompt_layer is None
              and app.tracks[2]["clips"] == [] and len(app.tracks[5]["clips"]) == 1,
              "card=%r track3=%d track6=%d"
              % (app._prompt_layer, len(app.tracks[2]["clips"]),
                 len(app.tracks[5]["clips"])))
        said = app.proj_lbl.get_text()
        check("CU-2 bin-says-what-went",
              "2 clips" in said and "Rhythm gtr" in said,
              "status=%r" % said)
        named = app.history.undo_label()
        d.key("z", ctrl=True)
        d.pump(0.3)
        check("CU-3 bin-is-one-step-back",
              named == "Remove Clips" and len(app.tracks[2]["clips"]) == 2,
              "label=%r back=%d" % (named, len(app.tracks[2]["clips"])))

        labels = [it[0] for it in app.menu_items("Track")
                  if isinstance(it, (tuple, list))]
        every = [x for x in labels if x.startswith("Remove Every Clip")]
        d.menu_action("Track", "Remove Every Clip")
        d.pump(0.3)
        all_said = app.proj_lbl.get_text()
        check("CU-4 remove-every-clip-acts",
              app._prompt_layer is None and clip_count(app) == 0
              and app.history.undo_label() == "Remove Every Clip"
              and "3 clips" in all_said,
              "card=%r tape=%d label=%r status=%r"
              % (app._prompt_layer, clip_count(app),
                 app.history.undo_label(), all_said))
        check("CU-5 remove-every-clip-asks-nothing",
              every == ["Remove Every Clip"],
              "Track menu offers %r" % every)
    except Exception as exc:                                      # noqa: BLE001
        print("NOTE the drive raised %s: %s" % (type(exc).__name__, exc))
    finally:
        d.close()
    for name in PROMISED:
        if name not in REPORTED:
            check(name, False, "the drive never reached this check")
    failed = [n for n in PROMISED if not REPORTED.get(n)]
    print("\n%d checks, %d failed" % (len(PROMISED), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
