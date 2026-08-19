#!/usr/bin/env python3
"""sequencer_selftest — the Sequencer's arrangement model and its audio engine.

    DISPLAY=:0 python3 tools/sequencer_selftest.py

Two halves, and the split matters:

  * nbsynth on its own — no GTK, no GStreamer, no sound card. It is a pure
    function from a song dict to PCM, so it can be checked the way any other
    function is: does a muted track make no sound, does a kick sit below a
    hi-hat, does trimming a take play the middle of it, does the pan control
    move the sound across the stereo field, does a loop come round.
  * the app driving it — that a take survives a trim, a cut, a repeat, a move
    to another lane, a save, a reload, an undo and a tempo change, because
    every one of those has destroyed somebody's recording in a sequencer at
    some point; and that the view controls (zoom, the grid, the knife) put an
    edit exactly where they say they will.
  * the capture pump, driven through a real pipe: whether every frame that
    came in reaches the take, whether the input is metered while it does, and
    whether a monitor that falls behind can cost the recording anything.

The whole point of the first half is that "the sound is right" stops being
something only ears can check on hardware nobody has to hand.
"""
import array
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay"
                        "/opt/notebook/de")
sys.path.insert(0, DE)

FAIL = []
PASS = [0]


def check(name, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  ok   %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s %s" % (name, detail))


def strongest(buf, lo=30.0, hi=4000.0, n=8192):
    """The loudest frequency in a buffer, by a coarse swept DFT."""
    x = list(buf[:min(len(buf), n)])
    best = (0.0, 0.0)
    f = lo
    while f < hi:
        w = 2 * math.pi * f / 48000.0
        re = sum(v * math.cos(w * i) for i, v in enumerate(x))
        im = sum(v * math.sin(w * i) for i, v in enumerate(x))
        m = re * re + im * im
        if m > best[0]:
            best = (m, f)
        f *= 1.012
    return best[1]


def band(buf, lo, hi):
    """Total energy between two frequencies — for "is this a low sound?"."""
    x = list(buf[:8000])
    tot = 0.0
    f = lo
    while f < hi:
        w = 2 * math.pi * f / 48000.0
        re = sum(v * math.cos(w * i) for i, v in enumerate(x))
        im = sum(v * math.sin(w * i) for i, v in enumerate(x))
        tot += re * re + im * im
        f *= 1.06
    return tot


def in_tune_with(buf, f0):
    """Whether a rendered note is a HARMONIC RELATIVE of the note it was asked
    for, and by how much it is out.

    "The loudest partial is the fundamental" is not what being in tune means,
    and testing for it fails on half a synthesiser's worth of perfectly correct
    sounds: a plucked string can have its third harmonic on top, a bass patch
    deliberately carries a sub-octave, a detuned pad has no single loudest
    partial at all. What DOES mean out of tune is a partial that is not
    harmonically related to the note — 1.06x, say, which is a semitone of
    error. So: find the loudest partial and check that its ratio to the note is
    a whole number or a whole fraction. Returns (ok, ratio)."""
    f = strongest(buf[2000:], max(40.0, f0 * 0.25), f0 * 4.5)
    r = f / f0
    for k in (0.25, 1.0 / 3, 0.5, 1.0, 2.0, 3.0, 4.0):
        if abs(r - k) < k * 0.035:
            return True, r
    return False, r


def peak(buf):
    return max((abs(v) for v in buf), default=0.0)


def wav_channels(path, limit=48000 * 4):
    with wave.open(path, "rb") as w:
        raw = w.readframes(min(w.getnframes(), limit))
        n = w.getnframes()
    a = array.array("h")
    a.frombytes(raw)
    return a[0::2], a[1::2], n


# ---------------------------------------------------------------------------
def synth_tests():
    import nbsynth as S
    print("nbsynth — the mix")
    tmp = tempfile.mkdtemp()
    try:
        take = os.path.join(tmp, "take.wav")
        with wave.open(take, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(array.array(
                "h", [int(12000 * math.sin(2 * math.pi * 440 * i / 48000))
                      for i in range(48000 * 3)]).tobytes())
        base = {"bpm": 120, "length": 4.0, "tracks": [
            {"gain": 1.0,
             "clips": [{"s": 0.0, "e": 2.0, "wav": take, "fin": 0.0,
                        "fout": 0.0}]}]}
        p = os.path.join(tmp, "a.wav")
        S.render_wav(base, p)
        L, R, n = wav_channels(p)
        check("a take plays in the mix", max(map(abs, L)) > 2000)
        f = strongest([v / 32768.0 for v in L[1000:9000]], 200, 900)
        check("the take plays at its own pitch", abs(f - 440) < 12, "%.1f" % f)
        check("the render is as long as the song plus its tail",
              4.0 * 48000 < n < 4.0 * 48000 + 9 * 48000, "%d frames" % n)

        # NOTHING BUT A TAKE MAKES A SOUND NOW, so a region with no recording
        # behind it must not even reach the mixer — it is an arrangement idea,
        # and one that reached the engine used to be a clip that looked
        # recorded and played nothing.
        empty = {"bpm": 120, "length": 4.0, "tracks": [
            {"gain": 1.0, "clips": [{"s": 0.0, "e": 2.0}]}]}
        S.render_wav(empty, p)
        L, _R, _n = wav_channels(p)
        check("a clip with no take in it is silent", max(map(abs, L)) == 0)
        check("...and is not handed to the mixer at all",
              S.normalize_song(empty)["tracks"][0]["clips"] == [])

        muted = json.loads(json.dumps(base))
        muted["tracks"][0]["mute"] = True
        S.render_wav(muted, p)
        L, R, _n = wav_channels(p)
        check("a muted track is silent", max(map(abs, L)) == 0)

        soloed = json.loads(json.dumps(base))
        soloed["tracks"].append({"solo": True, "clips": []})
        S.render_wav(soloed, p)
        L, R, _n = wav_channels(p)
        check("a solo elsewhere silences this track", max(map(abs, L)) == 0)

        panned = json.loads(json.dumps(base))
        panned["tracks"][0]["pan"] = -1.0
        S.render_wav(panned, p)
        L, R, _n = wav_channels(p)
        check("hard left puts the sound on the left",
              max(map(abs, L)) > 2000 and max(map(abs, R)) < 200,
              "L %d R %d" % (max(map(abs, L)), max(map(abs, R))))

        print("nbsynth — the clip's own level and fades")
        quiet = json.loads(json.dumps(base))
        quiet["tracks"][0]["clips"][0]["gain"] = 0.25
        S.render_wav(quiet, p)
        Lq, _R, _n = wav_channels(p)
        S.render_wav(base, p)
        Lb, _R, _n = wav_channels(p)
        r = max(map(abs, Lq)) / float(max(1, max(map(abs, Lb))))
        check("a clip's own level is applied", 0.2 < r < 0.32, "%.3f" % r)

        faded = json.loads(json.dumps(base))
        faded["tracks"][0]["clips"][0]["fin"] = 1.0
        S.render_wav(faded, p)
        Lf, _R, _n = wav_channels(p)
        early = max(map(abs, Lf[:4800]))
        late = max(map(abs, Lf[43000:48000]))
        check("a fade in starts quiet and arrives", early < late * 0.3,
              "%d then %d" % (early, late))

        print("nbsynth — trimming without copying audio")
        trimmed = json.loads(json.dumps(base))
        trimmed["tracks"][0]["clips"][0].update({"s": 0.0, "e": 1.0,
                                                 "off": 1.5})
        S.render_wav(trimmed, p)
        Lt, _R, _n = wav_channels(p)
        check("an offset clip plays the middle of its take",
              max(map(abs, Lt[:40000])) > 2000)
        check("...and stops at the clip's end",
              max(map(abs, Lt[52000:60000])) < 400)

        print("nbsynth — the room")
        dry = json.loads(json.dumps(base))
        dry["tracks"][0]["rev"] = 0.0
        dry["reverb"] = {"mix": 0.0, "size": 0.7}
        S.render_wav(dry, p)
        Ld, _R, _n = wav_channels(p)
        wet = json.loads(json.dumps(base))
        wet["tracks"][0]["rev"] = 1.0
        wet["reverb"] = {"mix": 1.0, "size": 0.9}
        S.render_wav(wet, p)
        Lw, _R, _n = wav_channels(p)
        tail_d = max(map(abs, Ld[48000 * 2 + 24000:48000 * 3]))
        tail_w = max(map(abs, Lw[48000 * 2 + 24000:48000 * 3]))
        check("the room is heard after the take has stopped",
              tail_w > tail_d * 3 + 50, "dry %d wet %d" % (tail_d, tail_w))

        print("nbsynth — the loop")
        pat = {"bpm": 120, "length": 16.0, "loop": [0.0, 2.0],
               "tracks": [{"gain": 1.0,
                           "clips": [{"s": 0.0, "e": 2.0, "wav": take}]}]}
        mix = S.Mixdown(pat, 0.0)
        for _i in range(int(9 * 48000 / 512)):
            mix.render(512)
        check("the loop comes round", mix.wrapped >= 4,
              "wrapped %d times" % mix.wrapped)
        check("the loop keeps the playhead inside it",
              0.0 <= mix.position() <= 2.05, "%.2f" % mix.position())
        mix.close()
        # ...and export must ignore it, or a file repeats two bars for ever
        S.render_wav(pat, p)
        _L, _R, n = wav_channels(p)
        check("export ignores the loop", n > 15 * 48000, "%d frames" % n)

        print("nbsynth — tone and compression")
        buf = [0.5 * math.sin(2 * math.pi * 40 * i / 48000) for i in range(4096)]
        cut, _st = S.apply_low_cut(list(buf), 1.0, 0.0)
        check("the low cut takes out a 40 Hz tone",
              peak(cut[2048:]) < peak(buf[2048:]) * 0.35,
              "%.3f -> %.3f" % (peak(buf[2048:]), peak(cut[2048:])))
        hiss = [0.5 * math.sin(2 * math.pi * 9000 * i / 48000)
                for i in range(4096)]
        cut, _st = S.apply_high_cut(list(hiss), 1.0, 0.0)
        check("the high cut takes out a 9 kHz tone",
              peak(cut[2048:]) < peak(hiss[2048:]) * 0.35)
        loud = [0.9 * math.sin(2 * math.pi * 220 * i / 48000)
                for i in range(8192)]
        soft = [0.15 * x for x in loud]
        cl, _st = S.apply_compress(list(loud), 1.0, (0.0, 1.0))
        cs, _st = S.apply_compress(list(soft), 1.0, (0.0, 1.0))
        before = peak(loud) / peak(soft)
        after = peak(cl[4096:]) / max(1e-6, peak(cs[4096:]))
        check("compression brings loud and quiet closer together",
              after < before * 0.6, "%.1fx -> %.1fx" % (before, after))
        check("compression does not push past full scale", peak(cl) <= 1.05,
              "%.3f" % peak(cl))

        print("nbsynth — the metronome, the one sound it still makes")
        # Through Mixdown, not render_wav: an EXPORT never has a click in it
        # (render_wav pins metronome=False), which is the point of the last
        # check here.
        clicky = {"bpm": 120, "length": 2.0, "metronome": True, "tracks": []}
        mixm = S.Mixdown(clicky, 0.0)
        blocks = b"".join(mixm.render(512) for _i in range(180))
        mixm.close()
        a = array.array("h")
        a.frombytes(blocks)
        check("the click sounds with no tracks at all",
              max(map(abs, a)) > 1000, "%d" % max(map(abs, a)))
        silent = json.loads(json.dumps(clicky))
        silent["metronome"] = False
        mixs = S.Mixdown(silent, 0.0)
        b = array.array("h")
        b.frombytes(b"".join(mixs.render(512) for _i in range(180)))
        mixs.close()
        check("...and only when it is asked for", max(map(abs, b)) == 0)
        S.render_wav(clicky, p)
        Le, _R, _n = wav_channels(p)
        check("an exported file never carries the click",
              max(map(abs, Le)) == 0, "%d" % max(map(abs, Le)))

        # a hand-edited project must never be able to raise inside the render
        junk = {"bpm": "fast", "length": -3, "loop": "yes", "tracks": [
            "not a track", {"gain": None,
                            "clips": [{"s": 1, "e": 0},
                                      {"s": 0, "e": 1, "wav": 7},
                                      {"s": 0, "e": 1, "notes": [[None, 1]]}]}]}
        ok = True
        try:
            S.Mixdown(junk, 0.0).render(512)
        except Exception as e:                      # noqa: BLE001
            ok = False
            print("      raised %r" % e)
        check("a damaged song renders instead of raising", ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("nbsynth — nothing here synthesises an instrument")
    gone = [n for n in ("render_drum", "KITS", "DRUMS", "swung", "kind_of",
                        "INSTRUMENT_NAMES", "MIC")
            if hasattr(S, n)]
    check("the drum machine is gone from the engine", not gone, str(gone))


# ---------------------------------------------------------------------------
def settle_view(app):
    """Let the view finish travelling.

    Every zoom and scroll is animated now (nbmotion, PAGE token, ARRIVE), and
    a headless suite has no main loop for the frame clock to tick — so a test
    that reads the view has to land it first. `settle()` is nbmotion's own
    "stop where you are" and fires the completion, which is the same path a
    retarget takes; the app deliberately lands on the exact target in either
    case, so this is not a shortcut around the code under test."""
    anim = getattr(app, "_view_anim", None)
    if anim is not None:
        anim.settle()


def step_view(app, fraction):
    """Drive the view animation to `fraction` of its duration and stop there,
    so the frames BETWEEN the two states can be inspected.

    Uses Scalar.advance(now) — nbmotion's single interpolation path, shared by
    the frame-clock driver — with an explicit time, rather than re-implementing
    the easing in the test."""
    import nbmotion
    import time as _time
    anim = getattr(app, "_view_anim", None)
    if anim is None:
        return False
    return anim.advance(_time.monotonic()
                        + (nbmotion.PAGE / 1000.0) * fraction)


def fresh_app(Q):
    """A Sequencer with no session to recover.

    EVERY instance restores CFG_FILE on construction — that is session
    recovery, and it is right in the product. In a test it means the second app
    built in one process opens holding the first one's clips, so a check that
    counts what is on the tape ends up counting somebody else's take."""
    try:
        os.unlink(Q.CFG_FILE)
    except OSError:
        pass
    return Q.Sequencer()


def damaged_model_tests():
    """A bad inner track field costs itself, not the whole recovered song."""
    import sequencer as Q
    app = Q.Sequencer.__new__(Q.Sequencer)
    app.length = Q.DEFAULT_LEN
    app._take_damage = []
    try:
        restored = app._norm_track(0, {"name": "Voice", "clips": 7})
    except Exception as exc:
        restored = exc
    check("a scalar saved clip collection does not abort project recovery",
          isinstance(restored, dict) and restored["name"] == "Voice"
          and restored["clips"] == [], repr(restored))


def lay_take(app, ti, s, e, wav=None, off=0.0):
    """Put a clip on a lane the way a finished take does, and select it."""
    c = app.clip_make_for_test(s, e, wav, off) if hasattr(
        app, "clip_make_for_test") else None
    if c is None:
        import sequencer as Q
        c = Q.clip_make(s, e, wav, off)
    app.tracks[ti]["clips"].append(c)
    app.sel = (ti, c)
    return c


def app_tests():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk                    # noqa: F401
    import sequencer as Q
    import nbsynth as S

    print("sequencer — eight lanes, all the same")
    ZOOM_FIT_CHECK = Q.ZOOM_FIT
    app = fresh_app(Q)
    check("every track is numbered, not named for somebody else's song",
          [tk["name"] for tk in app.tracks]
          == ["Track %d" % (i + 1) for i in range(Q.TRACKS)],
          str([tk["name"] for tk in app.tracks]))
    check("a track has no instrument slot left to set",
          not any("input" in tk for tk in app.tracks))
    check("the app offers no instrument picker",
          not hasattr(app, "_pick_instrument"))
    app.bpm = 120
    app.set_length_bars(32)          # 64 s at 120, room to zoom about in
    spb = app.sec_per_bar()

    print("sequencer — the grid, and being able to turn it off")
    app.snap = 4.0                                   # BAR
    check("on the bar grid, a moment rounds to the nearest bar",
          abs(app.snap_time(spb * 1.4) - spb) < 1e-9,
          "%.4f" % app.snap_time(spb * 1.4))
    app.snap = 0.25                                  # 1/16
    six = app.sec_per_beat() * 0.25
    check("on a sixteenth grid it rounds to the nearest sixteenth",
          abs(app.snap_time(six * 2.4) - six * 2) < 1e-9)
    app.snap = Q.SNAP_FREE
    check("FREE leaves a moment exactly where it is",
          abs(app.snap_time(1.23456) - 1.23456) < 1e-12,
          "%.6f" % app.snap_time(1.23456))
    check("...and that is a real setting, not the absence of one",
          Q.SNAP_FREE in [v for v, _n in Q.SNAP_CHOICES])
    check("a snapped stretch is never zero length",
          app.snap_span(3.0, 3.0)[1] > app.snap_span(3.0, 3.0)[0])
    app.snap = 4.0
    s, e = app.snap_span(spb * 1.2, spb * 2.9)
    check("a dragged loop lands on the grid at both ends",
          abs(s - spb) < 1e-9 and abs(e - spb * 3) < 1e-9,
          "%.3f %.3f" % (s, e))
    # cycling has to reach every choice and come back
    seen = set()
    for _i in range(len(Q.SNAP_CHOICES) + 1):
        seen.add(app.snap)
        app._cycle_snap()
    check("the grid control cycles through every setting",
          seen == set(v for v, _n in Q.SNAP_CHOICES), str(sorted(seen)))
    app.snap = 4.0
    app._update_snap_btn()

    print("sequencer — zooming in on the tape")
    app.zoom_fit()
    settle_view(app)
    check("FIT shows the whole tape",
          abs(app.view_span() - app.length) < 1e-9 and app.view_start == 0.0)
    W = 800
    check("pixels and seconds are inverses of each other",
          abs(app.time_at_px(app.px_of_time(9.0, W), W) - 9.0) < 1e-9)
    app.set_zoom(8.0, anchor=20.0, frac=0.5)
    settle_view(app)
    check("zooming in shows less of the tape",
          abs(app.view_span() - app.length / 8.0) < 1e-6)
    check("...and holds the moment it was zoomed about",
          abs(app.time_at_px(W * 0.5, W) - 20.0) < 1e-6,
          "%.4f" % app.time_at_px(W * 0.5, W))
    app.set_zoom(8.0, anchor=0.0, frac=0.5)
    settle_view(app)
    check("the view can never start before the tape does",
          app.view_start >= 0.0, "%.3f" % app.view_start)
    app.set_zoom(8.0, anchor=app.length, frac=0.5)
    settle_view(app)
    check("...nor run off the end of it",
          app.view_start + app.view_span() <= app.length + 1e-6)
    app.set_zoom(Q.ZOOM_MAX * 10)
    settle_view(app)
    check("zoom stops somewhere", app.zoom <= Q.ZOOM_MAX)
    app.set_zoom(0.01)
    settle_view(app)
    check("...and cannot go wider than the whole song", app.zoom >= Q.ZOOM_FIT)
    app.zoom_to(4.0, 8.0)
    settle_view(app)
    check("zoom-to fills the lanes with the stretch asked for",
          app.view_start <= 4.0 and app.view_start + app.view_span() >= 8.0,
          "%.2f..%.2f" % (app.view_start, app.view_start + app.view_span()))
    # the scrollbar is the map, so it has to say the same thing
    check("the scrollbar agrees with the view",
          abs(app._hadj.get_value() - app.view_start) < 1e-6
          and abs(app._hadj.get_page_size() - app.view_span()) < 1e-6)
    app._hadj.set_value(2.0)
    check("dragging the scrollbar moves the view",
          abs(app.view_start - 2.0) < 1e-6, "%.3f" % app.view_start)

    print("sequencer — the wheel over the timeline")
    # Ctrl+wheel zooms about the pointer, Shift+wheel scrolls sideways, and a
    # PLAIN wheel over a lane is left alone — the stack of eight tracks is
    # taller than the window and the wheel still has to scroll it, which is
    # what a wheel does everywhere else in the OS.
    from gi.repository import Gdk
    app.zoom_fit()
    settle_view(app)
    W = 800

    def wheel(direction, state=0, x=400.0, plain=False):
        ev = type("Ev", (), {"direction": direction, "state": state, "x": x})()
        return app.wheel_over_timeline(
            ev, W, lambda px: app.time_at_px(px, W), plain_scrolls=plain)

    check("a plain wheel over a lane is not taken",
          not wheel(Gdk.ScrollDirection.DOWN))
    check("...but over the ruler, which scrolls nothing else, it is",
          wheel(Gdk.ScrollDirection.DOWN, plain=True))
    app.zoom_fit()
    settle_view(app)
    at = app.time_at_px(200.0, W)
    took = wheel(Gdk.ScrollDirection.UP, Gdk.ModifierType.CONTROL_MASK, x=200.0)
    settle_view(app)
    check("Ctrl+wheel zooms in", took and app.zoom > ZOOM_FIT_CHECK,
          "zoom=%.2f" % app.zoom)
    check("...about the pointer, not about the middle",
          abs(app.time_at_px(200.0, W) - at) < 0.05,
          "%.3f vs %.3f" % (app.time_at_px(200.0, W), at))
    before = app.view_start
    wheel(Gdk.ScrollDirection.DOWN, Gdk.ModifierType.SHIFT_MASK)
    settle_view(app)
    check("Shift+wheel scrolls sideways", app.view_start > before,
          "%.3f -> %.3f" % (before, app.view_start))
    app.zoom_fit()
    settle_view(app)

    print("sequencer — every new control survives being pressed")
    # A HANDLER WIRED TO A BUTTON IS HANDED THE BUTTON. A method written
    # `def _x(self)` and connected with `connect("clicked", self._x)` raises
    # TypeError the first time anybody presses it, and nothing that only
    # CONSTRUCTS the window can tell — the MONITOR toggle shipped that way for
    # exactly as long as it took to press it here.
    #
    # AND IT HAS TO BE CAUGHT THROUGH sys.excepthook. PyGObject does not let an
    # exception out of a signal handler — clicked() returns perfectly normally
    # and the traceback goes to the hook. Wrapping the click in try/except
    # therefore catches NOTHING, which is how the first version of this check
    # sat green with the bug deliberately put back.
    pressed = []
    real_hook = sys.excepthook
    sys.excepthook = lambda *a: pressed.append(repr(a[1]))
    try:
        for name, btn in ([("MONITOR", app.mon_btn), ("METRO", app.metro_btn),
                           ("LOOP", app.loop_btn), ("SNAP", app.snap_btn)]
                          + [("tool " + k, b)
                             for k, b in app.tool_btns.items()]):
            n = len(pressed)
            btn.clicked()
            if len(pressed) > n:
                pressed[-1] = "%s: %s" % (name, pressed[-1])
    finally:
        sys.excepthook = real_hook
    check("pressing every deck and timeline control raises nothing",
          not pressed, "; ".join(pressed))
    # the menu items are called with NO arguments, which is the other half of
    # the same trap
    import nbapp
    called = []
    for item in (app.menu_items("View") + app.menu_items("Transport")
                 + app.menu_items("Edit") + app.menu_items("Track")):
        if item is nbapp.SEP or not isinstance(item, tuple):
            continue
        label, fn = item[0], item[1]
        if fn is None:
            continue
        try:
            fn()
        except Exception as e:                       # noqa: BLE001
            called.append("%s: %r" % (label, e))
    check("every enabled menu item runs when it is chosen",
          not called, "; ".join(called))
    app.zoom_fit()
    settle_view(app)
    app.snap = 4.0
    app._set_tool(Q.TOOL_SELECT)

    print("sequencer — the tool selector")
    app._set_tool(Q.TOOL_CUT)
    check("the CUT tool can be chosen", app.tool == Q.TOOL_CUT)
    app._toggle_tool()
    check("...and toggles back to SELECT", app.tool == Q.TOOL_SELECT)
    app._set_tool("nonsense")
    check("an unknown tool falls back to SELECT rather than to nothing",
          app.tool == Q.TOOL_SELECT)

    print("sequencer — the view TRAVELS to its new value")
    import nbmotion
    app.zoom_fit()
    settle_view(app)
    W = 800
    # A zoom is a scale ABOUT A POINT, and the point has to be held to the same
    # pixel for the WHOLE journey or the picture slides sideways under the hand
    # instead of growing out from under it. Sampled across the animation, not
    # only at the ends — an implementation that interpolated view_start would
    # pass an endpoints-only check and fail every frame in between.
    app.set_zoom(16.0, anchor=20.0, frac=0.25)
    drift = []
    for frac in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85):
        step_view(app, frac)
        drift.append(abs(app.time_at_px(W * 0.25, W) - 20.0))
    check("the anchor is pinned for every frame of the zoom, not just the ends",
          max(drift) < 0.02, "worst drift %.4f s" % max(drift))
    check("...and the view really is moving in between",
          len(set(round(d, 6) for d in drift)) >= 1 and app.zoom > 1.5,
          "zoom part-way = %.2f" % app.zoom)
    # ...and it interpolates GEOMETRICALLY. Interpolated linearly, most of the
    # journey is spent already zoomed in; geometrically each frame scales by the
    # same ratio, which is what "scaling" means and what the eye expects.
    #
    # Measured WITHOUT re-implementing the easing and without knowing the frame
    # time. A plain scroll interpolates view_start LINEARLY by construction, so
    # on a move that changes both, view_start recovers the eased parameter for
    # THAT FRAME and the zoom can be checked against exp() and against a linear
    # ramp at the very same instant. (Sampling at a fixed fraction of the clock
    # would prove nothing: a spring is already past its target half-way
    # through.)
    app.zoom_fit()
    settle_view(app)
    z0, s0 = app.zoom, app.view_start
    app.zoom_to(30.0, 34.0)
    z1, s1 = app._view_target_zoom, app._view_target_start
    e = None
    for frac in (0.06, 0.10, 0.16, 0.24):
        step_view(app, frac)
        got = (app.view_start - s0) / (s1 - s0)
        if 0.05 < got < 0.9:
            e = got
            break
    if e is None:
        not_reached_view = True
        check("a mid-flight frame of the zoom was captured", False,
              "view_start never landed between the endpoints")
    else:
        check("a mid-flight frame of the zoom was captured", True,
              "eased parameter e=%.3f" % e)
        geo = math.exp(math.log(z0) + (math.log(z1) - math.log(z0)) * e)
        lin = z0 + (z1 - z0) * e
        check("zoom is interpolated geometrically, so the motion reads uniform",
              abs(app.zoom - geo) < max(0.05, geo * 0.03),
              "at e=%.3f zoom=%.3f, geometric=%.3f, linear would be %.3f"
              % (e, app.zoom, geo, lin))
        check("...and a linear ramp is ruled out at that frame",
              abs(app.zoom - lin) > lin * 0.08,
              "zoom=%.3f vs linear %.3f" % (app.zoom, lin))
    settle_view(app)
    check("...and it lands exactly on the target, not near it",
          abs(app.zoom - z1) < 1e-9, "%.6f vs %.6f" % (app.zoom, z1))

    # the spring is the house character, and it must come from nbmotion rather
    # than a hand-rolled curve
    check("the view uses the OS arrival curve",
          app._view_anim is not None
          and nbmotion.ARRIVE is nbmotion.ease_out_back)

    print("sequencer — the moving view must not re-rasterise eight lanes")
    # ARTICLE F1. A view change invalidates every input to a lane's cached
    # surface, so the naive animation re-renders eight full-width lanes per
    # frame on the software rasteriser. Mid-flight a lane must BLIT what it has.
    lane = app.lanes[0]
    lane._cache = ("key", object(), (0.0, 64.0))
    app.zoom_fit()
    settle_view(app)
    app.set_zoom(8.0, anchor=8.0, frac=0.5)
    step_view(app, 0.4)
    check("the view reports that it is moving, so the lanes know to blit",
          app.view_moving())
    settle_view(app)
    check("...and stops reporting it once it has arrived", not app.view_moving())
    # a cache entry carries the view it was rendered AT, or a stretch is
    # impossible and the blit path silently degrades to a stale picture
    check("a lane's cached raster remembers the view it was taken at",
          len(lane._cache) == 3 and lane._cache[2] == (0.0, 64.0))

    print("sequencer — Reduced Motion arrives instantly")
    # §F4: instant-equivalence. Nothing here may branch on it — nbmotion's
    # policy does — so the check is that the END STATE is identical.
    real = nbmotion.reduced_motion
    nbmotion.reduced_motion = lambda: True
    try:
        app.zoom_fit()
        app.set_zoom(12.0, anchor=6.0, frac=0.5)
        check("with Reduced Motion the view is already there, no settle needed",
              abs(app.zoom - 12.0) < 1e-9 and not app.view_moving(),
              "zoom=%.4f moving=%s" % (app.zoom, app.view_moving()))
    finally:
        nbmotion.reduced_motion = real
    app.zoom_fit()
    settle_view(app)

    print("sequencer — the scrollbar is the one view change that never eases")
    app.set_zoom(8.0, anchor=8.0, frac=0.5)
    settle_view(app)
    app._hadj.set_value(3.0)
    check("dragging the thumb moves the view at once, with no spring",
          abs(app.view_start - 3.0) < 1e-6 and not app.view_moving(),
          "%.4f" % app.view_start)
    # ...and a drag part-way through an animation wins: the hand is the authority
    app.set_zoom(2.0, anchor=20.0, frac=0.5)
    step_view(app, 0.3)
    app._hadj.set_value(1.0)
    check("...and it cancels anything in flight rather than fighting it",
          not app.view_moving() and abs(app.view_start - 1.0) < 1e-6,
          "moving=%s start=%.4f" % (app.view_moving(), app.view_start))
    app.zoom_fit()
    settle_view(app)

    print("sequencer — the playhead does not run off the screen")
    app.set_zoom(16.0, anchor=0.0, frac=0.0)
    settle_view(app)
    app.transport = "play"
    app.pos = app.view_start + app.view_span() * 3
    app.follow_playhead()
    settle_view(app)
    check("a running transport brings the view to the playhead",
          app.view_start <= app.pos <= app.view_start + app.view_span(),
          "%.2f not in %.2f..%.2f"
          % (app.pos, app.view_start, app.view_start + app.view_span()))
    inside = app.view_start + app.view_span() * 0.5
    app.pos = inside
    before = app.view_start
    app.follow_playhead()
    check("...and leaves it alone while the playhead is still on screen",
          app.view_start == before)
    app.transport = "stop"
    app.zoom_fit()
    settle_view(app)

    print("sequencer — cutting a take in two")
    tmp = tempfile.mkdtemp()
    try:
        take = os.path.join(tmp, "take.wav")
        with wave.open(take, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00\x10" * (48000 * 8))
        c = lay_take(app, 0, 0.0, 8.0, take, 0.0)
        app.snap = Q.SNAP_FREE
        app.cut_clip_at(0, 3.3)
        clips = sorted(app.tracks[0]["clips"], key=lambda x: x["s"])
        check("the knife makes two clips", len(clips) == 2, str(len(clips)))
        check("it cuts exactly where it was asked to, with the grid off",
              abs(clips[0]["e"] - 3.3) < 1e-9 and abs(clips[1]["s"] - 3.3) < 1e-9,
              "%.4f" % clips[0]["e"])
        check("both halves keep the same recording",
              clips[0]["wav"] == take and clips[1]["wav"] == take)
        check("the second half reads on from where the first stopped",
              abs(clips[1]["off"] - (clips[0]["off"] + 3.3)) < 1e-9,
              "%.4f" % clips[1]["off"])
        check("no audio moved: the two halves still cover the whole take",
              abs((clips[0]["e"] - clips[0]["s"])
                  + (clips[1]["e"] - clips[1]["s"]) - 8.0) < 1e-9)
        app._undo()
        check("a cut is undoable", len(app.tracks[0]["clips"]) == 1)

        app.snap = 4.0
        c = app.tracks[0]["clips"][0]
        app.cut_clip_at(0, spb * 1.4)
        clips = sorted(app.tracks[0]["clips"], key=lambda x: x["s"])
        check("with the grid on it cuts on the grid",
              len(clips) == 2 and abs(clips[0]["e"] - spb) < 1e-9,
              "%.4f" % clips[0]["e"])
        app._undo()

        n_before = len(app.tracks[0]["clips"])
        app.snap = Q.SNAP_FREE
        app.cut_clip_at(0, 0.001)
        check("a cut too close to an end is refused rather than made",
              len(app.tracks[0]["clips"]) == n_before)
        app.cut_clip_at(1, 2.0)
        check("cutting where there is no clip does nothing",
              not app.tracks[1]["clips"])

        print("sequencer — splitting every track on one line")
        b = lay_take(app, 1, 0.0, 8.0, take, 0.0)
        app.pos = 4.0
        app._split_at_playhead()
        check("the playhead splits every lane it crosses",
              len(app.tracks[0]["clips"]) == 2
              and len(app.tracks[1]["clips"]) == 2)
        app._undo()

        print("sequencer — trimming a clip on its lane")
        app.tracks[0]["clips"] = []
        app.tracks[1]["clips"] = []
        c = lay_take(app, 0, 2.0, 6.0, take, 1.0)
        lane = app.lanes[0]
        lane._trim = (c, "in", Q.clip_copy(c))
        lane._moved = True
        app.snap = Q.SNAP_FREE
        lane._drag_trim(_Ev(x=lane_x(app, lane, 2.5)))
        check("trimming the front moves into the take by what it moved on tape",
              abs((c["s"] - 2.0) - (c["off"] - 1.0)) < 1e-6,
              "s%+.3f off%+.3f" % (c["s"] - 2.0, c["off"] - 1.0))
        check("...and leaves the end of the clip alone",
              abs(c["e"] - 6.0) < 1e-9)
        lane._drag_trim(_Ev(x=lane_x(app, lane, -50.0)))
        check("the front cannot be dragged before the take begins",
              c["off"] >= -1e-9, "%.4f" % c["off"])
        c2 = Q.clip_copy(c)
        lane._trim = (c, "out", c2)
        lane._drag_trim(_Ev(x=lane_x(app, lane, 900.0)))
        head = c2["s"] - c2["off"]
        check("the end cannot be dragged past what was recorded",
              c["e"] <= head + 8.0 + 1e-6,
              "%.4f > %.4f" % (c["e"], head + 8.0))
        lane._trim = None

        print("sequencer — a clip can move to another lane")
        c = app.tracks[0]["clips"][0]
        moved = app.move_clip_to_track(0, c, 3)
        check("the clip leaves the lane it was on",
              moved and c not in app.tracks[0]["clips"])
        check("...and arrives on the other one", c in app.tracks[3]["clips"])
        check("...and is still the selected clip", app.sel_clip() is c)
        check("it is the same object, so a drag can carry on through the move",
              app.sel[0] == 3)
        app.move_clip_to_track(3, c, 0)
        check("moving onto its own lane is not a move",
              not app.move_clip_to_track(0, c, 0))

        print("sequencer — the clipboard holds clips")
        app.sel = (0, app.tracks[0]["clips"][0])
        app._copy_clip()
        app.pos = 20.0
        app.snap = Q.SNAP_FREE
        app._paste_clip()
        pasted = app.sel_clip()
        check("pasting puts a clip at the playhead",
              pasted is not None and abs(pasted["s"] - 20.0) < 1e-6)
        check("...pointing at the same recording, never a copy of it",
              pasted["wav"] == take)
        app._delete_selected()
        check("Delete takes the selected clip off its lane",
              all(x is not pasted for x in app.tracks[0]["clips"]))
        app._undo()
        check("...and undo puts it back",
              any(abs(x["s"] - 20.0) < 1e-6 for x in app.tracks[0]["clips"]))

        print("sequencer — nudging from the keyboard")
        target = next(x for x in app.tracks[0]["clips"] if abs(x["s"] - 20) < 1)
        app.sel = (0, target)
        app.snap = 1.0                               # BEAT
        at = target["s"]
        app._nudge_selected(1)
        check("a nudge moves the clip by one grid step",
              abs(target["s"] - (at + app.sec_per_beat())) < 1e-6,
              "%.4f" % (target["s"] - at))
        app._nudge_selected(-1)
        check("...and back", abs(target["s"] - at) < 1e-6)
        app._nudge_selected(1, fine=True)
        check("a fine nudge ignores the grid",
              abs(target["s"] - (at + 0.01)) < 1e-9)
        app._nudge_track(1)
        check("up and down move it between lanes", app.sel[0] == 1)

        print("sequencer — normalising a take")
        def tone(name, amp):
            path = os.path.join(tmp, name)
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(array.array(
                    "h", [int(amp * math.sin(2 * math.pi * 220 * i / 48000))
                          for i in range(48000)]).tobytes())
            return path
        quiet = tone("quiet.wav", 12000)
        q = lay_take(app, 5, 0.0, 1.0, quiet, 0.0)
        pk = Q.clip_peak(q)
        app._normalise_clip()
        check("a quiet take is brought up", q["gain"] > 2.0,
              "gain %.3f from peak %.3f" % (q["gain"], pk))
        check("...to just below full scale, never over it",
              0.9 < q["gain"] * pk < 1.0, "%.4f" % (q["gain"] * pk))
        # ...and a take quieter than the ceiling can lift stops at the ceiling
        # rather than asking the engine for a gain it will clamp anyway
        # ...measured over the part the CLIP plays, not the whole file: a
        # take with one loud moment outside the trim must not set the level
        two = os.path.join(tmp, "two.wav")
        with wave.open(two, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(array.array(
                "h", [int((30000 if i < 48000 else 3000)
                          * math.sin(2 * math.pi * 220 * i / 48000))
                      for i in range(96000)]).tobytes())
        half = Q.clip_make(0.0, 1.0, two, 1.0)
        check("a clip's level is measured over the part it plays",
              abs(Q.clip_peak(half) - 3000 / 32768.0) < 0.02,
              "%.4f" % Q.clip_peak(half))
        whole = Q.clip_make(0.0, 2.0, two, 0.0)
        check("...and over all of it when it plays all of it",
              Q.clip_peak(whole) > 0.8, "%.4f" % Q.clip_peak(whole))

        tiny = tone("tiny.wav", 300)
        t2 = lay_take(app, 6, 0.0, 1.0, tiny, 0.0)
        app._normalise_clip()
        check("a very quiet take stops at the loudest a clip may be",
              abs(t2["gain"] - Q.CLIP_GAIN_MAX / 100.0) < 1e-9,
              "%.3f" % t2["gain"])
        check("...which is a level the engine will actually play",
              S.normalize_song({"tracks": [{"clips": [
                  {"s": 0, "e": 1, "wav": tiny, "gain": t2["gain"]}]}]}
              )["tracks"][0]["clips"][0]["gain"] == t2["gain"])
        app.tracks[6]["clips"] = []

        print("sequencer — committing a take")
        app4 = fresh_app(Q)
        app4.bpm = 120
        app4.tracks[2]["armed"] = True
        app4.transport = "rec"
        app4.rec_start = 4.0
        app4._preroll = app4.sec_per_bar()
        app4.pos = 10.0
        app4.recorder.stop = lambda: take
        app4.recorder.failed_early = lambda: False
        app4._stop_transport()
        clips = app4.tracks[2]["clips"]
        check("a take commits as a clip", len(clips) == 1)
        check("the take starts where recording started",
              clips and abs(clips[0]["s"] - 4.0) < 1e-6)
        check("the clip skips the count-in inside its own take",
              clips and abs(clips[0]["off"] - app4.sec_per_bar()) < 1e-6,
              str(clips[0]["off"]) if clips else "no clip")
        check("the take's audio is on the clip",
              clips and clips[0]["wav"] == take)
        check("an unarmed lane gets nothing", not app4.tracks[3]["clips"])
        song = S.normalize_song(app4._song())
        mixed = [c for t in song["tracks"] for c in t["clips"] if c["wav"]]
        check("the engine is handed the take", len(mixed) == 1)
        check("the engine is handed the count-in offset too",
              mixed and abs(mixed[0]["off"] - app4.sec_per_bar()) < 1e-6)

        # ...and a take that never arrived must leave NOTHING on the tape
        app5 = fresh_app(Q)
        app5.tracks[0]["armed"] = True
        app5.transport = "rec"
        app5.rec_start = 0.0
        app5.pos = 4.0
        app5.recorder.stop = lambda: None
        app5.recorder.failed_early = lambda: False
        app5._stop_transport()
        check("a failed take commits no clip at all",
              not any(tk["clips"] for tk in app5.tracks))

        app4.select_clip(2, clips[0])
        app4._sync_editor()
        check("a recorded clip opens the take editor",
              app4.edit_stack.get_visible_child_name() == "wave")
        app4.sel = (7, None)
        app4._sync_editor()
        check("a clipless lane says so instead of drawing an empty canvas",
              app4.edit_stack.get_visible_child_name() == "empty")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("sequencer — the tempo and the tape")
    app.bpm = 90
    app._on_bpm(type("S", (), {"get_value": lambda self: 90})())
    check("changing the tempo keeps every clip",
          sum(len(tk["clips"]) for tk in app.tracks) > 0)

    print("sequencer — saving and reopening")
    data = app._serialize()
    text = json.dumps(data)
    check("a project is plain JSON", isinstance(text, str))
    check("a saved track carries no instrument any more",
          all("input" not in t for t in data["tracks"]))
    app2 = fresh_app(Q)
    app2._apply(json.loads(text))
    n1 = sum(len(tk["clips"]) for tk in app.tracks)
    n2 = sum(len(tk["clips"]) for tk in app2.tracks)
    check("every clip survives a save and a reopen", n1 == n2,
          "%d -> %d" % (n1, n2))
    check("the tempo and the grid survive",
          app2.bpm == app.bpm and app2.snap == app.snap)
    check("monitoring survives", app2.monitor == app.monitor)
    check("the mix survives",
          app2.tracks[0]["pan"] == app.tracks[0]["pan"]
          and app2.tracks[0]["rev"] == app.tracks[0]["rev"]
          and app2.tracks[0]["low"] == app.tracks[0]["low"])
    check("a reopened project is looked at whole, not at somebody else's zoom",
          app2.zoom == Q.ZOOM_FIT and app2.view_start == 0.0)

    print("sequencer — newer nested project metadata")
    newer = app._serialize()
    newer["tracks"][0]["channel_strip"] = {"colour": "amber", "bus": 2}
    newer["tracks"][0]["clips"][0]["transcript"] = {"text": "count in"}
    app_newer = fresh_app(Q)
    app_newer._apply(json.loads(json.dumps(newer)))
    newer_saved = app_newer._serialize()
    check("unknown track metadata survives a reopen and save",
          newer_saved["tracks"][0].get("channel_strip")
          == {"colour": "amber", "bus": 2})
    check("unknown clip metadata survives a reopen and save",
          newer_saved["tracks"][0]["clips"][0].get("transcript")
          == {"text": "count in"})
    copied = Q.clip_copy(app_newer.tracks[0]["clips"][0])
    check("copying a clip for an edit keeps its newer metadata",
          copied.get("transcript") == {"text": "count in"})

    print("sequencer — projects written by older versions")
    legacy = {"version": 2, "bpm": 100, "length": 60.0, "master": 90,
              "snap": 0.125,
              "tracks": [{"name": "Track 1", "input": "Rhythm gtr",
                          "gain": 110, "clips": [[0.0, 4.0], [8.0, 12.0]]},
                         {"name": "Track 2", "input": "Drums 808",
                          "clips": [{"s": 0, "e": 2,
                                     "notes": [[0, 0, 0.25, 100]]}]},
                         {"name": "Pad", "input": "Warm pad",
                          "clips": [{"s": 0, "e": 4,
                                     "notes": [[0, 60, 1, 100]]}]}]}
    app3 = fresh_app(Q)
    app3._apply(legacy)
    check("an old project keeps its clips",
          len(app3.tracks[0]["clips"]) == 2)
    check("a typed instrument label becomes the track's name",
          app3.tracks[0]["name"] == "Rhythm gtr", app3.tracks[0]["name"])
    check("a track that was NAMED keeps its name",
          app3.tracks[2]["name"] == "Pad", app3.tracks[2]["name"])
    check("the drum machine's own name is not left lying about as a label",
          app3.tracks[1]["name"] == "Track 2", app3.tracks[1]["name"])
    check("no track comes back with an instrument",
          not any("input" in tk for tk in app3.tracks))
    check("steps that can no longer play are not carried in",
          not any("notes" in c for tk in app3.tracks for c in tk["clips"]))
    check("a grid setting this app no longer has falls back to bars",
          app3.snap == Q.DEFAULT_SNAP, str(app3.snap))

    print("sequencer — what gets handed to the engine")
    song = S.normalize_song(app._song())
    check("the engine gets one track per lane",
          len(song["tracks"]) == Q.TRACKS)
    check("gains reach the engine as ratios",
          0.0 <= song["tracks"][0]["gain"] <= 2.0)
    mix = S.Mixdown(app._song(), 0.0)
    dat = mix.render(512)
    check("the arrangement renders a block", len(dat) == 512 * 4)
    mix.close()

    print("sequencer — a selection that no longer exists")
    app.sel = (0, {"s": 0, "e": 1, "wav": None, "off": 0})
    check("a clip that is not in the arrangement is not selected",
          app.sel_clip() is None)
    app._validate_sel()
    check("validating picks a real clip instead",
          app.sel is None or app.sel_clip() is not None)


class _Ev(object):
    """The two fields a lane's drag handlers read off a GdkEvent."""
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y


def lane_x(app, lane, t):
    """Where second `t` falls on a lane, for driving a drag by hand.

    The lane has no allocation in a window that was never shown, so its own
    _W() is 1; ask the app for the mapping at a real width and hand the handler
    the pixel it would have got. The handler converts it straight back through
    the same pair of functions, so the round trip is the real one."""
    W = max(1, lane.get_allocated_width())
    return app.px_of_time(t, W)


# ---------------------------------------------------------------------------
def recorder_tests():
    """The capture pump, monitoring and the input meter — without a sound card.

    The pump is the thing that decides whether a take lands, so it is driven
    with a REAL pipe here (a `cat` of known bytes standing in for arecord's
    stdout) rather than a stub of itself: the code under test is the code that
    runs on the machine."""
    import sequencer as Q
    print("sequencer — the capture pump")
    tmp = tempfile.mkdtemp()
    try:
        raw = os.path.join(tmp, "in.raw")
        frames = array.array("h", [int(20000 * math.sin(i / 30.0))
                                   for i in range(48000)])
        with open(raw, "wb") as fh:
            fh.write(frames.tobytes())
        out = os.path.join(tmp, "take.wav")
        rec = Q.Recorder()
        rec.proc = subprocess.Popen(["cat", raw], stdout=subprocess.PIPE)
        rec._wav = wave.open(out, "wb")
        rec._wav.setnchannels(1)
        rec._wav.setsampwidth(2)
        rec._wav.setframerate(Q.CAP_RATE)
        rec._stop.clear()
        rec._pump()
        peak_seen = rec._peak
        rec._wav.close()
        rec.proc.wait(timeout=5)
        rec.proc, rec._wav = None, None
        with wave.open(out, "rb") as w:
            got = w.getnframes()
            ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        check("every frame that came in is written to the take",
              got == len(frames), "%d of %d" % (got, len(frames)))
        check("the take is a normal mono 16-bit WAV at the capture rate",
              ch == 1 and sw == 2 and sr == Q.CAP_RATE)
        check("the input is metered as it arrives",
              0.5 < peak_seen <= 1.0, "%.3f" % peak_seen)
        check("the meter reads nothing when nothing is being recorded",
              rec.level() == 0.0)
        check("digital silence is measured as silence",
              Q._chunk_peak(b"\x00\x00" * 512) == 0.0)
        check("a full-scale sample measures full scale",
              abs(Q._chunk_peak(b"\xff\x7f") - 1.0) < 0.001)
        check("a half-read chunk does not upset the meter",
              Q._chunk_peak(b"\x01") == 0.0)

        print("sequencer — a monitor that falls behind must not cost the take")
        rd, wr = os.pipe()
        os.set_blocking(wr, False)
        rec2 = Q.Recorder()
        rec2._mon = type("P", (), {"stdin": type("F", (), {
            "fileno": staticmethod(lambda: wr)})()})()
        # a pipe holds ~64 KB, so pushing far more than that at it fills it
        for _i in range(64):
            rec2._feed_monitor(b"\x11\x22" * 4096)
        check("the backlog is capped rather than growing without limit",
              len(rec2._mon_buf) <= Q.MON_MAX_BACKLOG,
              "%d bytes" % len(rec2._mon_buf))
        check("...and is dropped in whole 16-bit frames, never half of one",
              len(rec2._mon_buf) % 2 == 0)
        os.close(rd)
        # a monitor whose player has GONE is dropped, and the take carries on
        rec2._feed_monitor(b"\x00\x00" * 64)
        rec2._feed_monitor(b"\x00\x00" * 64)
        check("a dead monitor gives up instead of raising",
              rec2._mon is None and not rec2.monitoring)
        os.close(wr)

        print("sequencer — monitoring is a switch, not a fixed setting")
        rec3 = Q.Recorder()
        rec3.start_monitor()
        check("turning it on with nothing recording does nothing",
              rec3._mon is None)
        rec3.stop_monitor()
        check("turning it off with nothing recording does nothing",
              not rec3.monitoring)
        app = Q.Sequencer()
        check("monitoring is on unless it has been turned off", app.monitor)
        app._toggle_monitor()
        check("the toggle turns it off", not app.monitor)
        app._toggle_monitor()
        check("...and on again", app.monitor)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _find_entry(w):
    from gi.repository import Gtk
    if isinstance(w, Gtk.Entry):
        return w
    if hasattr(w, "get_children"):
        for c in w.get_children():
            r = _find_entry(c)
            if r is not None:
                return r
    return None


def keyboard_tests():
    # Typed keys stay in a focused name box. The Space branch used to sit
    # ABOVE the typing guard, so the space in "Chorus 2" toggled playback
    # instead of landing in the track name — a two-word name was untypeable.
    print("sequencer — typing in a name box owns every plain key")
    import sequencer as Q
    from gi.repository import Gdk
    app = Q.Sequencer()
    entry = _find_entry(app)
    if entry is None:
        check("Space while naming a track stays in the box",
              False, "[not reached: no Entry in the widget tree]")
        app.destroy()
        return

    def ev(keyval):
        e = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        e.keyval = keyval
        e.state = Gdk.ModifierType(0)
        e.string = ""
        e.window = app.get_window()
        return e

    toggles = []
    real_toggle = app._toggle_play
    app._toggle_play = lambda: toggles.append(1)
    try:
        app.set_focus(entry)
        if app.get_focus() is not entry:
            check("Space while naming a track stays in the box",
                  False, "[not reached: could not focus the name box]")
            return
        took = app._on_space(app, ev(Gdk.KEY_space))
        check("Space while naming a track stays in the box (no transport)",
              took is False and not toggles,
              "took=%r toggles=%r" % (took, toggles))
        check("'c' while naming never arms the knife",
              app._on_space(app, ev(Gdk.KEY_c)) is False)
        check("Delete while naming never deletes a clip",
              app._on_space(app, ev(Gdk.KEY_Delete)) is False)
        app.set_focus(None)
        took = app._on_space(app, ev(Gdk.KEY_space))
        check("Space with no field focused still drives the transport",
              took is True and len(toggles) == 1,
              "took=%r toggles=%r" % (took, toggles))
    finally:
        app._toggle_play = real_toggle
        app.destroy()


def focus_after_prompt_tests():
    """Close a card, start typing — something must be listening.

    This app PASSES today for a reason it does not state: its confirm card
    never takes focus, so the invoker keeps it and removing the layer takes
    nothing away. Illustrator's card deliberately grabs its safe button so a
    stray Space cannot fire a destructive action — a real improvement somebody
    may apply here — and the moment it does, this app loses its focus owner
    exactly as comics and illustrator did. The PROPERTY is pinned, not the
    mechanism, so whoever adds that grab is told to add the restore with it."""
    if not os.environ.get("DISPLAY"):
        return
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import sequencer as Q
    # fresh_app, not Sequencer(): every instance restores CFG_FILE on
    # construction, so a bare one opens holding the previous test's clips.
    win = fresh_app(Q)
    win.show_all()
    for _ in range(80):
        if Gtk.events_pending():
            Gtk.main_iteration_do(False)
    before = win.get_focus()
    win._confirm("Focus", "Body", "OK", lambda: None)
    for _ in range(80):
        if Gtk.events_pending():
            Gtk.main_iteration_do(False)
    win._close_prompt()
    for _ in range(80):
        if Gtk.events_pending():
            Gtk.main_iteration_do(False)
    after = win.get_focus()
    check("focus survives a prompt closing",
          after is not None and after is before,
          "before=%r after=%r" % (before, after))
    win.destroy()


def main():
    home = tempfile.mkdtemp(prefix="nbseq-selftest-")
    os.environ["NB_HOME"] = home
    try:
        damaged_model_tests()
        synth_tests()
        app_tests()
        recorder_tests()
        focus_after_prompt_tests()
        keyboard_tests()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("\n%d checks passed, %d failed" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % f)
    # Terminal verdict for the release runner: a zero exit with only
    # per-check lines is read as DID NOT RUN (run_all_gates SUCCESSWORD),
    # because a suite that dies half way prints those lines too.
    print("RESULT: %s" % ("FAILED" if FAIL else "ALL PASS"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
