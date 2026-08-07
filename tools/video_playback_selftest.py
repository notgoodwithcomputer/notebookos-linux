#!/usr/bin/env python3
"""
Pressing Play streams the clip; it does not flick through stills.

ROADMAP #29. `_playback_step` used to spawn one ffmpeg process per second to
fetch a single frame — about 1fps, silent — for every clip on the storyboard.
It now hands a real `video` clip to `nbvideo.Playback` (playbin into gtksink),
and keeps the frame-by-frame path only as the fallback for a machine whose
GStreamer cannot open the file.

This drives the REAL editor: a real project with a real clip on it, the real
`_on_play`, and the real pipeline. What is asserted is that the player was given
the clip AT ITS TRIM-IN and is actually running — because "playback did not
error" is exactly what the slideshow also did.

The fallback is asserted too, by making the player unavailable and checking the
old frame path still runs. A migration that only works when the new engine is
present would strand every machine the engine is missing on.

Run:
    tools/guestrun.sh python3 tools/video_playback_selftest.py
    tools/guestrun.sh python3 tools/video_playback_selftest.py --de DIR
"""
import os
import sys
import time
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-vplay-")
os.environ["NB_HOME"] = _HOME
for _d in ("Videos", "Documents"):
    os.makedirs(os.path.join(_HOME, _d), exist_ok=True)

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

import video  # noqa: E402

FAILED, N = [], [0]
CLIP_SECS = 6


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
    Gst.init(None)
    pipe = Gst.parse_launch(
        'videotestsrc num-buffers=%d pattern=ball ! '
        'video/x-raw,width=320,height=240,framerate=25/1 ! videoconvert ! '
        'avenc_mpeg4 ! qtmux ! filesink location="%s"' % (CLIP_SECS * 25, path))
    pipe.set_state(Gst.State.PLAYING)
    pipe.get_bus().timed_pop_filtered(
        30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipe.set_state(Gst.State.NULL)
    return os.path.exists(path) and os.path.getsize(path) > 2048


def main():
    clip_file = os.path.join(_HOME, "Videos", "shot.mp4")
    if not check("a decodable clip can be built", build_clip(clip_file)):
        not_reached("no clip", "the editor takes the clip",
                    "Play streams it", "the trim-in is honoured")
        return 1

    app = video.VideoEditor()
    pump(0.4)

    # A real project: one trimmed clip on the storyboard.
    app.clips = [{"kind": "video", "path": clip_file, "name": "shot",
                  "start": 2.0, "duration": 4, "speed": 1.0,
                  "transition": None}]
    app._render_timeline()
    pump(0.3)
    check("the editor takes the clip", len(app.clips) == 1)

    have_player = app._player is not None and app._player.available
    check("the editor built a player", have_player,
          "available=%s failed=%s" % (getattr(app._player, "available", None),
                                      getattr(app._player, "failed", None)))

    # ---- Play must STREAM -------------------------------------------
    app._on_play()
    pump(1.2)
    if not have_player:
        not_reached("no GStreamer player on this host",
                    "Play streams the clip", "the trim-in is honoured",
                    "the picture advances under the transport")
    else:
        streaming = check("Play streams the clip",
                          app._live_clip == 0, "_live_clip=%r" % app._live_clip)
        if streaming:
            # start=2.0 means the player must be inside the source at ~2s+,
            # not at zero. Export honours the trim; a preview that ignored it
            # would show a different film from the one that gets rendered.
            pos = app._player.position()
            check("the trim-in is honoured", pos >= 1.8,
                  "source position %.2fs (trim-in was 2.0s)" % pos)
            a = app._player.position()
            pump(1.0)
            b = app._player.position()
            check("the picture advances under the transport", b > a + 0.3,
                  "%.2fs -> %.2fs" % (a, b))
        else:
            not_reached("the clip never went to the player",
                        "the trim-in is honoured",
                        "the picture advances under the transport")

    # ---- Stop releases it -------------------------------------------
    app._stop_playback(reset=True)
    pump(0.3)
    check("Stop drops the stream", app._live_clip is None)
    check("Stop rewinds the clock", app._play_pos == 0.0)

    # ---- the fallback still works when there is no player ------------
    # A machine whose GStreamer cannot open the file must still get the old
    # frame-by-frame picture rather than a blank screen.
    real = app._player
    app._player = None
    app._live_clip = None
    stepped = {"n": 0}
    real_pv = app._pv_start
    app._pv_start = lambda *a, **k: stepped.__setitem__("n", stepped["n"] + 1)
    try:
        app._play_pos = 0.5
        app._playback_step(0, 0.0)
        pump(0.2)
        check("without a player it falls back to the frame path",
              stepped["n"] >= 1 or not video.PIXBUF_OK,
              "frame requests=%d" % stepped["n"])
    finally:
        app._pv_start = real_pv
        app._player = real

    # ---- ROADMAP #30: split must give the halves DIFFERENT pictures ----
    # Both halves come from one file, so a frame cache keyed on the path alone
    # handed them the same decoded frame: the stage and both storyboard cards
    # showed an identical picture, which makes a split look like it did nothing.
    app.clips = [{"kind": "video", "path": clip_file, "name": "shot",
                  "start": 0.0, "duration": 8, "speed": 1.0,
                  "transition": None}]
    app._sel_cell = 0
    app._menu_split()
    pump(0.2)
    split = check("splitting yields two clips", len(app.clips) == 2)
    if split:
        a, b = app.clips[0], app.clips[1]
        check("the second half picks up where the first left off",
              abs(float(b["start"]) - 4.0) < 0.01 and float(a["start"]) == 0.0,
              "starts %.2f / %.2f" % (float(a["start"]), float(b["start"])))
        ka, kb = app._frame_key(a), app._frame_key(b)
        check("the two halves have different frame keys, so different pictures",
              ka != kb, "%r vs %r" % (ka, kb))
    else:
        not_reached("the split did not happen",
                    "the second half picks up where the first left off",
                    "the two halves have different frame keys, so different pictures")

    try:
        app.destroy()
    except Exception:
        pass
    pump(0.2)

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
