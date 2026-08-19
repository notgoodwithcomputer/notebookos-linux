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

    # A real project: one trimmed clip on the storyboard, built the way the
    # EDITOR builds one. This fixture used to hand the clip its own "path"
    # key, which no clip this app makes has ever carried — a clip carries an
    # index into the media bin and the path lives on the bin entry. Both
    # playback entry points read clip["path"], so they agreed with the fixture
    # and with nothing else: every check below passed while, in the app, Play
    # never opened the stream at all. The fixture is the app's own shape now,
    # so these checks answer for the app.
    app._bin = [{"path": clip_file, "name": "shot", "kind": "video", "dur": 4}]
    app.clips = [dict(video._new_clip(0, "video", 4), start=2.0)]
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

    # ---- A mid-play edit reaches the stream --------------------------
    # _playback_step early-returns while _live_clip is pinned, so a speed
    # or trim edit on the STREAMING clip used to change the model, the
    # timeline and the export while the stage kept playing the old film —
    # the picture contradicting the edit the panel just confirmed. The
    # editor must drop and reopen the stream under the new mapping within
    # a transport tick.
    SPEED_NAME = "a mid-play speed change reopens the stream at the new rate"
    TRIM_NAME = "a mid-play trim change reopens inside the new cut"
    if not have_player:
        not_reached("no GStreamer player on this host", SPEED_NAME, TRIM_NAME)
    else:
        opens = []
        # The editor streams through open_async (a preroll that must not
        # block the GTK thread — a decoder may take seconds); the spy sits
        # on that call. Spying open() here would see nothing and report the
        # reopen missing when it is only elsewhere.
        real_open = app._player.open_async

        def spy_open(path, at=0.0, rate=1.0, **kw):
            opens.append((round(float(at), 2), float(rate)))
            return real_open(path, at=at, rate=rate, **kw)

        app._player.open_async = spy_open
        try:
            app._on_play()
            pump(1.0)
            if app._live_clip != 0:
                not_reached("the clip never went to the player",
                            SPEED_NAME, TRIM_NAME)
            else:
                base = len(opens)
                app._on_speed(2.0)      # the real handler the button calls
                pump(0.5)
                check(SPEED_NAME,
                      len(opens) > base and opens[-1][1] == 2.0,
                      "opens=%r" % (opens,))
                app._stop_playback(reset=True)
                pump(0.2)

                # reset the arrangement, then cut deep into the source while
                # it streams: the reopen must land INSIDE the new cut (the
                # old mapping cannot reach 4.0s this early in the clock).
                app.clips[0]["speed"] = 1.0
                app.clips[0]["start"] = 2.0
                app.clips[0]["duration"] = 4
                app._render_timeline()
                pump(0.2)
                app._on_play()
                pump(0.6)
                if app._live_clip != 0:
                    not_reached("the clip did not stream again", TRIM_NAME)
                else:
                    base = len(opens)
                    app._prop_trim.set_value(4.0)   # fires _on_trim_changed
                    pump(0.5)
                    reopened = len(opens) > base
                    check(TRIM_NAME,
                          reopened and opens[-1][0] >= 4.0,
                          "opens=%r" % (opens,))
                app._stop_playback(reset=True)
                pump(0.2)
        finally:
            app._player.open_async = real_open
            app.clips[0]["speed"] = 1.0
            app.clips[0]["start"] = 2.0
            app.clips[0]["duration"] = 4
            app._render_timeline()
            pump(0.2)

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
    app._bin = [{"path": clip_file, "name": "shot", "kind": "video", "dur": 8}]
    app.clips = [video._new_clip(0, "video", 8)]
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
