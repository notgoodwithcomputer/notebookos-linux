#!/usr/bin/env python3
"""
An unplayable track must say so.

Music had no status channel of any kind. A file that would not decode reached
`_on_error`, which called `_stop_playback()` and returned — the play glyph
flipped back to Play and nothing, anywhere on screen, said why. From the
listener's side that is indistinguishable from a broken button, and the natural
response is to press it again. (ROADMAP #18.)

This drives the REAL GStreamer pipeline against REAL bad input rather than
calling `_on_error` with a fabricated message. Two failures are provoked the way
they actually happen:

  a file of plain bytes named .mp3  -> gst-stream-error-quark   (undecodable)
  a library row whose file is gone  -> caught before the pipeline

and one that must NOT produce a message: a track that plays. A status channel
that fires on success is noise, and noise is how a status channel gets ignored.

The distinction between the two messages is the point of `_play_failure`, so
the suite asserts they DIFFER — a single "something went wrong" for both would
pass a weaker test while telling the listener nothing they can act on.

Run:
    tools/guestrun.sh python3 tools/music_failure_selftest.py
"""
import os
import sys
import struct
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-musicfail-")
os.environ["NB_HOME"] = _HOME
MUSIC = os.path.join(_HOME, "Music")
os.makedirs(MUSIC, exist_ok=True)

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GLib, Gst  # noqa: E402

import music  # noqa: E402

FAILED = []
N = [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(*names):
    """Record dependent assertions as failed when their precondition did not
    hold, instead of letting them pass for the wrong reason.

    Without this, "the message names the track" passed while the feature was
    removed: with no message the label falls back to the bare track title, so
    the title is trivially "in" it. An assertion that is true whether or not
    the code under test ran is worse than no assertion, because it reads as
    coverage."""
    for n in names:
        check(n + "  [not reached: nothing was reported]", False)


def pump_until(pred, ms=6000):
    """Run the real main loop until `pred()` or the deadline. The error arrives
    as a bus message on the main loop, so nothing can be asserted synchronously
    after set_state — an earlier draft that just pumped pending events raced the
    pipeline and passed for the wrong reason."""
    done = {"v": False}

    def tick():
        if pred():
            done["v"] = True
            loop.quit()
            return False
        return True

    loop = GLib.MainLoop()
    GLib.timeout_add(50, tick)
    GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
    if pred():
        return True
    loop.run()
    return done["v"] or pred()


def write_silent_wav(path, seconds=1):
    """A real, decodable file, so the success case is genuinely a success."""
    rate, n = 8000, 8000 * seconds
    data = b"\x00\x00" * n
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        fh.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
        fh.write(b"data" + struct.pack("<I", len(data)) + data)


def main():
    bad = os.path.join(MUSIC, "Broken Take.mp3")
    with open(bad, "wb") as fh:
        fh.write(b"this is not audio, it is a text file\n" * 200)
    good = os.path.join(MUSIC, "Real Take.wav")
    write_silent_wav(good)
    missing = os.path.join(MUSIC, "Vanished Take.wav")

    w = music.Music()
    for _ in range(200):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()

    if not w._engine_ok():
        check("GStreamer is available to test against", False)
        return 1
    check("GStreamer is available to test against", True)

    def now():
        return w._nowlbl.get_text() if w._nowlbl is not None else ""

    # ---- 1. undecodable file ------------------------------------------
    w._play_track({"title": "Broken Take", "artist": "", "album": "",
                   "time": "", "path": bad})
    got = pump_until(lambda: "Broken Take" in now() and "…" not in now()[:1]
                     and now() != "Broken Take", ms=8000)
    undecodable = now()
    spoke = check("an undecodable track reports something (%r)"
                  % undecodable[:60],
                  bool(undecodable) and undecodable != _idle(w))
    if spoke:
        check("the message names the track", "Broken Take" in undecodable)
        check("the message is not the GStreamer error text",
              "GStreamer" not in undecodable
              and "gst" not in undecodable.lower()
              and "plug-in" not in undecodable)
    else:
        not_reached("the message names the track",
                    "the message is not the GStreamer error text")
    check("playback really stopped", not w._playing)

    # ---- 2. a file the library lists but the disk no longer has -------
    w._play_track({"title": "Vanished Take", "artist": "", "album": "",
                   "time": "", "path": missing})
    for _ in range(200):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()
    gone = now()
    spoke2 = check("a missing file reports something (%r)" % gone[:60],
                   bool(gone) and gone != _idle(w))
    if spoke2 and spoke:
        check("the missing-file message names the track",
              "Vanished Take" in gone)
        # The whole point of _play_failure is that these two causes are told
        # apart. One "something went wrong" for both would satisfy every check
        # above while telling the listener nothing they can act on.
        check("the two failures do not say the same thing",
              gone != undecodable)
    else:
        not_reached("the missing-file message names the track",
                    "the two failures do not say the same thing")

    # ---- 3. a track that plays says nothing --------------------------
    w._play_track({"title": "Real Take", "artist": "", "album": "",
                   "time": "", "path": good})
    ok = pump_until(lambda: w._playing, ms=6000)
    check("a good track actually plays", ok and w._playing)
    playing_text = now()
    check("a track that plays shows no failure message (%r)"
          % playing_text[:60],
          "can’t be played" not in playing_text
          and "no longer where" not in playing_text)

    try:
        w._stop_playback()
        w.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


def _idle(w):
    """What the label says when nothing has happened — the string a silent
    failure would leave on screen, and therefore the thing every message must
    differ from."""
    return w._nowtext()


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
