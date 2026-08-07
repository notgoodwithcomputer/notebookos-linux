#!/usr/bin/env python3
"""
Playback that actually plays: real frames, real sound, a real seek.

The Video Editor's Play button spawned one ffmpeg process per second to fetch a
single frame — roughly 1fps and silent (ROADMAP #29). `nbvideo.Playback` is the
replacement: playbin into gtksink, which hands back a GtkWidget the preview
stage can pack.

This drives a REAL file through the REAL pipeline. The fixture is encoded here
with `avenc_mpeg4` into `qtmux`, deliberately: libav and the mp4 muxer are both
in the shipped image (`libgstlibav.so`, `libgstisomp4.so`), whereas x264enc is
not — a fixture the guest could not decode would be testing the developer's
machine.

What matters most is the third check. A pipeline can reach PLAYING and still
show nothing, so the position is sampled twice and must have MOVED. "It did not
error" is not evidence that a picture is running.

Run:
    tools/guestrun.sh python3 tools/nbvideo_selftest.py
    tools/guestrun.sh python3 tools/nbvideo_selftest.py --de DIR
"""
import os
import sys
import time
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-vid-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Gst, GLib  # noqa: E402

import nbvideo  # noqa: E402

FAILED, N = [], [0]
CLIP_SECS = 4


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump(secs=0.0):
    end = time.time() + secs
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        GLib.main_context_default().iteration(False)
        if time.time() >= end:
            return
        time.sleep(0.02)


def build_clip(path):
    """A four-second 320x240 clip with a tone on it, in formats the IMAGE can
    decode."""
    Gst.init(None)
    desc = (
        'videotestsrc num-buffers=%d pattern=ball ! '
        'video/x-raw,width=320,height=240,framerate=25/1 ! videoconvert ! '
        'avenc_mpeg4 ! mux. '
        'audiotestsrc num-buffers=%d ! audioconvert ! avenc_aac ! mux. '
        'qtmux name=mux ! filesink location="%s"'
        % (CLIP_SECS * 25, CLIP_SECS * 43, path))
    try:
        pipe = Gst.parse_launch(desc)
    except Exception:
        # No AAC encoder: picture only is enough for every check but one.
        desc = ('videotestsrc num-buffers=%d pattern=ball ! '
                'video/x-raw,width=320,height=240,framerate=25/1 ! '
                'videoconvert ! avenc_mpeg4 ! qtmux ! filesink location="%s"'
                % (CLIP_SECS * 25, path))
        pipe = Gst.parse_launch(desc)
    pipe.set_state(Gst.State.PLAYING)
    pipe.get_bus().timed_pop_filtered(
        30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipe.set_state(Gst.State.NULL)
    return os.path.exists(path) and os.path.getsize(path) > 2048


def main():
    clip = os.path.join(_HOME, "clip.mp4")
    made = check("a decodable test clip can be built", build_clip(clip),
                 "%d bytes" % (os.path.getsize(clip)
                               if os.path.exists(clip) else 0))
    if not made:
        not_reached("no clip to play", "the player reports itself available",
                    "it opens the clip", "the picture actually advances")
        return 1

    seen = {"eos": 0, "err": []}
    p = nbvideo.Playback(on_eos=lambda: seen.__setitem__("eos", seen["eos"] + 1),
                         on_error=lambda d: seen["err"].append(d))

    ok = check("the player reports itself available", p.available)
    check("it hands back a real GTK widget to pack",
          p.widget is not None and isinstance(p.widget, Gtk.Widget))
    if not ok:
        not_reached("no pipeline", "it opens the clip",
                    "the picture actually advances")
        return 1

    # ---- open and inspect -------------------------------------------
    opened = check("it opens the clip", p.open(clip))
    if not opened:
        not_reached("the clip did not open", "the picture actually advances")
        return 1
    check("it knows the clip carries a picture", p.has_video())
    dur = p.duration()
    check("it reports a sane duration (%.2fs)" % dur,
          abs(dur - CLIP_SECS) < 1.5)

    # ---- THE check: the picture moves --------------------------------
    p.play()
    pump(0.6)
    a = p.position()
    pump(1.0)
    b = p.position()
    moved = check("the picture actually advances", b > a + 0.3,
                  "%.2fs -> %.2fs" % (a, b))
    check("no error came off the bus", not seen["err"], str(seen["err"]))

    # ---- pause holds, seek lands -------------------------------------
    if moved:
        p.pause()
        pump(0.3)
        c = p.position()
        pump(0.6)
        check("pause stops the clock", abs(p.position() - c) < 0.2,
              "%.2fs -> %.2fs" % (c, p.position()))
        p.seek(2.5)
        pump(0.4)
        s = p.position()
        check("a seek lands where it was asked", abs(s - 2.5) < 0.7,
              "asked 2.50s, got %.2fs" % s)
    else:
        not_reached("the picture never moved", "pause stops the clock",
                    "a seek lands where it was asked")

    # ---- end of clip tells the caller --------------------------------
    p.seek(max(0.0, dur - 0.4))
    p.play()
    end = time.time() + 8
    while time.time() < end and not seen["eos"]:
        pump(0.1)
    check("running out fires on_eos so the caller can step on",
          seen["eos"] >= 1, "eos=%d" % seen["eos"])

    # ---- teardown is clean and idempotent ----------------------------
    p.teardown()
    check("teardown drops the pipeline", not p.available)
    p.stop()
    p.teardown()
    check("stop and teardown are safe to call again", True)

    # ---- and a missing file is refused, not fatal --------------------
    q = nbvideo.Playback()
    check("a file that is not there is refused, not raised",
          q.open("/nonexistent/nope.mp4") is False)
    q.teardown()

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
