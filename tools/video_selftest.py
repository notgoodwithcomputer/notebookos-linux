#!/usr/bin/env python3
"""video_selftest — build small Video Editor projects and prove a real,
decodable file comes out of each one.

    DISPLAY=:0 python3 tools/video_selftest.py

Why this exists: the Video Editor is the only app whose whole output is produced
by a subprocess, and a broken render is INVISIBLE from the app — ffmpeg exits
non-zero (or worse, exits zero with a file that has no video stream in it) and
the app can only report what it was told. So every check here actually runs the
render and then probes the file: frame size, stream presence, and — the defect
this tool was written for — that the PICTURE is as long as the movie claims.

    The render is driven through video.py's own _build_ffmpeg_cmd, so this
    exercises the shipped pipeline rather than a copy of it.

By default it uses whatever `ffmpeg`/`ffprobe` are on PATH. To check the
TARGET's binaries (the ones that will actually run this) instead of the host's,
put a wrapper on PATH that invokes them through the target's loader:

    LD=buildroot/output/target/lib64/ld-linux-x86-64.so.2
    "$LD" --library-path "…/usr/lib:…/lib" …/usr/bin/ffmpeg "$@"

which is what tools/video_target_ffmpeg.sh writes for you.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(
    HERE, "..", "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de"))
sys.path.insert(0, DE)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

PASS = []
FAIL = []


def check(ok, name, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  [%s]" % detail) if detail and not ok else ""))
    return ok


def run(argv, timeout=600):
    return subprocess.run(argv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


def probe(path):
    """(duration, {codec_type: stream_duration}, frame_count_of_video)."""
    r = run([FFPROBE, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,duration,width,"
             "height", "-of", "json", path])
    info = json.loads(r.stdout.decode("utf-8", "replace"))
    dur = float(info["format"]["duration"])
    streams = {}
    for s in info.get("streams", []):
        streams[s.get("codec_type")] = s
    return dur, streams


# ---------------------------------------------------------------- fixtures
def make_media(home):
    """A few small real media files, generated with the same ffmpeg under test
    so the fixtures can never be the reason a check fails."""
    pics = os.path.join(home, "Pictures")
    vids = os.path.join(home, "Videos")
    mus = os.path.join(home, "Music")
    for d in (pics, vids, mus, os.path.join(home, "Documents"),
              os.path.join(home, ".config", "notebook")):
        os.makedirs(d, exist_ok=True)
    still = os.path.join(pics, "still.png")
    still2 = os.path.join(pics, "still2.png")
    clip = os.path.join(vids, "clip.mp4")
    song = os.path.join(mus, "tone.wav")
    jobs = [
        ([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
          "testsrc=size=800x600:duration=1:rate=1", "-frames:v", "1", still]),
        ([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
          "smptebars=size=1000x400:duration=1:rate=1", "-frames:v", "1",
          still2]),
        ([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
          "testsrc=size=640x480:duration=6:rate=25", "-f", "lavfi", "-i",
          "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset",
          "ultrafast", "-c:a", "aac", "-shortest", clip]),
        ([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
          "sine=frequency=220:duration=20", song]),
    ]
    for j in jobs:
        r = run(j)
        if r.returncode != 0:
            print("cannot build fixtures: %s"
                  % r.stderr.decode("utf-8", "replace")[-400:])
            sys.exit(2)
    return still, still2, clip, song


def main():
    if not FFMPEG or not FFPROBE:
        print("SKIP: no ffmpeg/ffprobe on PATH — nothing to render with")
        return 0

    # SAY WHICH ffmpeg IS UNDER TEST. The machine ships 4.4.4; a modern host
    # ffmpeg (>= 6) fails EVERY transition here, because setpts marks its output
    # variable-frame-rate and xfade then refuses the pad ("The inputs needs to
    # be a constant frame rate; current rate of 1/0 is invalid"). That is a red
    # result about the HOST, not about the shipped app, and without this line it
    # reads as a broken Video Editor. Use tools/video_target_ffmpeg.sh to check
    # the binaries that will actually run this.
    _v = run([FFMPEG, "-version"]).stdout.decode("utf-8", "replace")
    print("ffmpeg under test: %s (%s)"
          % (_v.splitlines()[0] if _v else "?", FFMPEG))

    tmp = tempfile.mkdtemp(prefix="nbvid-selftest-")
    home = os.path.join(tmp, "home")
    os.makedirs(home, exist_ok=True)
    os.environ["NB_HOME"] = home
    still, still2, clip, song = make_media(home)

    import gi
    gi.require_version("Gtk", "3.0")
    import video

    app = video.VideoEditor()
    app._bin = [
        {"path": still, "name": "still.png", "kind": "image", "dur": 4},
        {"path": still2, "name": "still2.png", "kind": "image", "dur": 4},
        {"path": clip, "name": "clip.mp4", "kind": "video", "dur": 6},
        {"path": song, "name": "tone.wav", "kind": "audio", "dur": 20},
    ]
    IMG, IMG2, VID, AUD = 0, 1, 2, 3

    def c(mi, kind, dur, **kw):
        cl = video._new_clip(mi, kind, dur)
        cl.update(kw)
        return cl

    def render(name, clips, music=None):
        """Render a project and return (ok, out_path, want_secs, stderr)."""
        app.clips = [copy.deepcopy(x) for x in clips]
        app.music = music
        out = os.path.join(tmp, name + ".mp4")
        cmd, total, err = app._build_ffmpeg_cmd(app.clips, out, None)
        if cmd is None:
            return False, out, 0, err or "no command"
        r = run(cmd)
        app._exp_cleanup_tmp()
        size = os.path.getsize(out) if os.path.exists(out) else 0
        if r.returncode != 0 or size == 0:
            return (False, out, total,
                    r.stderr.decode("utf-8", "replace")[-500:])
        return True, out, total, ""

    def expect_movie(name, clips, music=None, want_audio=None, size=None):
        """The house check: it renders, it decodes, the picture is as long as
        the movie, and the frame is the size that was asked for."""
        ok, out, want, err = render(name, clips, music)
        if not check(ok, "%s renders" % name, err):
            return
        try:
            dur, streams = probe(out)
        except Exception as e:
            check(False, "%s decodes" % name, str(e))
            return
        check("video" in streams, "%s has a video stream" % name)
        check(abs(dur - want) < 0.5, "%s is %ss long" % (name, want),
              "file is %.2fs" % dur)
        v = streams.get("video") or {}
        if v.get("duration"):
            # THE defect this file was written for: the audio lane was padded
            # to the slot and the picture was not, so the picture stopped early
            # and every later clip played at the wrong time.
            check(abs(float(v["duration"]) - want) < 0.5,
                  "%s picture fills the movie" % name,
                  "picture is %.2fs of %ss" % (float(v["duration"]), want))
        a = streams.get("audio") or {}
        if want_audio is True:
            check(bool(a), "%s has an audio stream" % name)
            if a.get("duration") and v.get("duration"):
                check(abs(float(a["duration"]) - float(v["duration"])) < 0.7,
                      "%s sound and picture are the same length" % name,
                      "%.2f vs %.2f" % (float(a["duration"]),
                                        float(v["duration"])))
        elif want_audio is False:
            check(not a, "%s has no audio stream" % name)
        if size:
            check((v.get("width"), v.get("height")) == size,
                  "%s is %dx%d" % ((name,) + size),
                  "got %sx%s" % (v.get("width"), v.get("height")))

    print("== a movie comes out at all ==")
    expect_movie("one-still", [c(IMG, "image", 3)], want_audio=False,
                 size=(1280, 720))
    expect_movie("two-stills", [c(IMG, "image", 3), c(IMG2, "image", 3)],
                 want_audio=False)
    expect_movie("video-clip", [c(VID, "video", 4)], want_audio=True)
    expect_movie("title-card", [video._new_title("My Movie", "a subtitle", 3)],
                 want_audio=False)

    print("== a caption does not lengthen its clip ==")
    # the overlay used to add a frame per captioned clip, so the reported length
    # and the file's length drifted apart the more captions a film carried
    expect_movie("caption", [c(IMG, "image", 3, title="A caption")],
                 want_audio=False)
    expect_movie("captions-many",
                 [c(IMG, "image", 2, title="One"), c(IMG2, "image", 2,
                                                     title="Two"),
                  c(IMG, "image", 2, title="Three")], want_audio=False)

    print("== every transition folds ==")
    reel = [c(IMG, "image", 2)] + [c(IMG2, "image", 2, transition=k)
                                   for k, _ in video.TRANSITIONS]
    expect_movie("all-transitions", reel, want_audio=False)

    print("== every effect and pan/zoom renders ==")
    expect_movie("all-effects",
                 [c(IMG, "image", 1, effect=k) for k, _ in video.EFFECTS],
                 want_audio=False)
    expect_movie("all-kenburns",
                 [c(IMG, "image", 2, kenburns=k) for k, _ in video.KENBURNS],
                 want_audio=False)

    # Each feature above is exercised ALONE, and transitions are exercised
    # between plain clips. The defect that wrote a 0-byte movie lived in the
    # FOLD — a clip's frame rate and timebase have to survive its own filters
    # before xfade will accept it — so the combinations are what has to hold.
    # A caption goes through a different statement (overlay) from everything
    # else, pan/zoom brings its own fps, and speed rewrites the timestamps.
    print("== a feature and a transition survive each other ==")
    TR = video.TRANSITIONS[0][0]
    expect_movie("kenburns-across-transition",
                 [c(IMG, "image", 2, kenburns="in"),
                  c(IMG2, "image", 2, kenburns="out", transition=TR)],
                 want_audio=False)
    expect_movie("caption-across-transition",
                 [c(IMG, "image", 2, title="A"),
                  c(IMG2, "image", 2, title="B", transition=TR)],
                 want_audio=False)
    expect_movie("caption-on-one-side-only",
                 [c(IMG, "image", 2, title="A"),
                  c(IMG2, "image", 2, transition=TR)], want_audio=False)
    expect_movie("effect-across-transition",
                 [c(IMG, "image", 2, effect="blur"),
                  c(IMG2, "image", 2, effect="bw", transition=TR)],
                 want_audio=False)
    expect_movie("speed-across-transition",
                 [c(VID, "video", 3, speed=2.0),
                  c(VID, "video", 3, speed=0.5, transition=TR)],
                 want_audio=True)
    expect_movie("title-card-across-transition",
                 [video._new_title("One", "sub", 2),
                  c(IMG, "image", 2, transition=TR)], want_audio=False)
    expect_movie("everything-at-once",
                 [video._new_title("Title", "sub", 2),
                  c(IMG, "image", 3, kenburns="in", title="Photo",
                    transition=TR),
                  c(VID, "video", 3, speed=2.0, title="Clip", afade=True,
                    vfade=True, transition=video.TRANSITIONS[-1][0]),
                  c(IMG2, "image", 2, effect="bw", transition=TR)],
                 music={"path": song, "volume": 0.6,
                        "fadein": True, "fadeout": True},
                 want_audio=True)

    print("== the picture is never shorter than its slot ==")
    # each of these used to leave the video track short while the audio ran on
    expect_movie("clip-longer-than-source", [c(VID, "video", 15)],
                 want_audio=True)
    expect_movie("clip-longer-then-still",
                 [c(VID, "video", 15), c(IMG, "image", 4)], want_audio=True)
    expect_movie("trim-near-end", [c(VID, "video", 4, start=4.0)],
                 want_audio=True)
    expect_movie("trim-past-end", [c(VID, "video", 4, start=99.0)],
                 want_audio=True)
    expect_movie("speed-over-run", [c(VID, "video", 5, speed=2.0)],
                 want_audio=True)

    print("== sound ==")
    expect_movie("music-under", [c(IMG, "image", 4), c(VID, "video", 4)],
                 music={"path": song, "name": "tone.wav", "volume": 0.6,
                        "fadein": True, "fadeout": True}, want_audio=True)
    expect_movie("music-shorter-than-movie", [c(IMG, "image", 25)],
                 music={"path": song, "name": "tone.wav", "volume": 0.6,
                        "fadein": True, "fadeout": True}, want_audio=True)
    expect_movie("audio-lane", [c(AUD, "audio", 5)], want_audio=True)
    expect_movie("muted-clip", [c(VID, "video", 4, mute=True)],
                 want_audio=False)
    expect_movie("fades", [c(VID, "video", 4, afade=True, vfade=True)],
                 want_audio=True)

    print("== export size is honoured ==")
    for w, h in video.EXPORT_SIZES:
        app._out_w, app._out_h = w, h
        expect_movie("size-%d" % h, [c(IMG, "image", 2)], want_audio=False,
                     size=(w, h))
    app._out_w, app._out_h = video.EXPORT_W, video.EXPORT_H

    print("== the app's own numbers are the file's numbers ==")
    # a transition OVERLAPS two clips, so the reel is shorter than the slots add
    # up to; the app used to report the sum and export the fold
    app.clips = [c(IMG, "image", 5), c(IMG2, "image", 5, transition="trfade")]
    check(abs(app._total() - 9.0) < 0.01,
          "a crossfade shortens the reported length",
          "reported %.2f, want 9" % app._total())
    ok, out, want, err = render("fold-length", app.clips)
    if ok:
        dur, _s = probe(out)
        check(abs(dur - 9.0) < 0.5, "and the file is that length",
              "file is %.2fs" % dur)

    print("== text is drawn with glyphs, in every script ==")
    # cairo's toy font API drew NOTHING for these scripts, so a title card
    # exported blank in five of the seventeen interface languages
    import cairo
    app._exp_tmp_imgs = []
    for label, text in (("latin", "My Movie"), ("japanese", "こんにちは"),
                        ("chinese", "你好世界"), ("korean", "안녕하세요"),
                        ("hindi", "नमस्ते"), ("yiddish", "שלום"),
                        ("russian", "Привет"), ("greek", "Γεια")):
        for kind, png in (("card", app._render_card_png(text, "")),
                          ("caption", app._render_caption_png(text))):
            ink = 0
            if png:
                surf = cairo.ImageSurface.create_from_png(png)
                W, H = surf.get_width(), surf.get_height()
                data = bytes(surf.get_data())
                stride = surf.get_stride()
                for y in range(0, H, 2):
                    row = data[y * stride:y * stride + W * 4]
                    for x in range(0, W * 4, 8):
                        if (row[x] > 200 and row[x + 1] > 200
                                and row[x + 2] > 200 and row[x + 3] > 200):
                            ink += 1
            check(ink > 40, "%s %s has glyphs" % (kind, label),
                  "%d lit pixels" % ink)
    app._exp_cleanup_tmp()

    print("== a long title stays inside the frame ==")
    long_title = "A Very Long Title That Would Run Off Both Edges Of The Frame"
    app._exp_tmp_imgs = []
    png = app._render_card_png(long_title, "")
    if check(bool(png), "long title card renders"):
        surf = cairo.ImageSurface.create_from_png(png)
        W, H = surf.get_width(), surf.get_height()
        data = bytes(surf.get_data())
        stride = surf.get_stride()
        edge = 0
        for y in range(0, H):
            row = data[y * stride:y * stride + W * 4]
            for x in (0, 4, (W - 2) * 4, (W - 1) * 4):
                if row[x] > 160 and row[x + 1] > 160 and row[x + 2] > 160:
                    edge += 1
        check(edge == 0, "long title does not touch the frame edge",
              "%d lit edge pixels" % edge)
    app._exp_cleanup_tmp()

    print("== a failed render leaves nothing behind, and takes nothing with it ==")
    # A half-written .mp4 in Videos looks like a saved film and plays as a
    # broken one, so a failure must remove its draft. What it must NEVER remove
    # is the destination: exporting over a film you already have and then
    # pressing Stop used to delete that film outright.
    #
    # This block used to plant its bytes AT _exp_out and assert they were gone,
    # which is the defect written down as the contract. The destination now
    # starts with something valuable in it, which is the only way the check can
    # tell the two files apart.
    app.clips = [c(IMG, "image", 2)]
    app._exp_out = os.path.join(tmp, "vacation.mp4")
    app._exp_draft = os.path.join(tmp, ".nbvid-vacation.mp4.part")
    app._exp_done = False
    with open(app._exp_out, "wb") as fh:
        fh.write(b"THE FILM THE PERSON ALREADY HAD")
    with open(app._exp_draft, "wb") as fh:
        fh.write(b"\0" * 2048)
    app._discard_partial_export()
    check(not os.path.exists(app._exp_draft),
          "a failed export removes its part-written draft")
    check(os.path.exists(app._exp_out)
          and open(app._exp_out, "rb").read() == b"THE FILM THE PERSON ALREADY HAD",
          "a failed export leaves the film already there untouched")

    # And the finished film is never taken away by a late teardown.
    app._exp_draft = os.path.join(tmp, ".nbvid-keepme.mp4.part")
    with open(app._exp_draft, "wb") as fh:
        fh.write(b"\0" * 2048)
    app._exp_done = True
    app._discard_partial_export()
    check(os.path.exists(app._exp_draft),
          "a finished export is never removed")
    os.remove(app._exp_draft)

    # The draft is what ffmpeg is given, so nothing is written to the
    # destination until there is a whole film to put there.
    app._exp_out = os.path.join(tmp, "argcheck.mp4")
    app._exp_draft = os.path.join(tmp, ".nbvid-argcheck.mp4.part")
    args, _total, _err = app._build_ffmpeg_cmd(
        [c(IMG, "image", 2)], app._exp_draft, None)
    check(args and args[-1] == app._exp_draft,
          "the render writes to the draft, not to the destination")
    check(app._exp_out not in args,
          "the destination is never handed to ffmpeg")
    # Leave the directory as it was found: a later check is entitled to it, and
    # a stray "<name>.mp4" beside a real render is exactly the lookalike that
    # makes an unrelated assertion fail for the wrong reason.
    for leftover in ("vacation.mp4", "silent.mp4"):
        try:
            os.remove(os.path.join(tmp, leftover))
        except OSError:
            pass

    print("== sound that could not be checked is not passed off as silence ==")
    # ffprobe missing (or timing out) makes _probe_has_audio answer "no", which
    # is the same answer as a genuinely silent clip. The render must treat them
    # alike; the person must not. A holiday video whose sound was dropped
    # because the prober was absent used to come back saying only "Saved".
    import shutil as _shutil
    app._audio_probe_cache = {}
    app._audio_unknown = []
    real_which = _shutil.which
    _shutil.which = lambda tool: None if tool == "ffprobe" else real_which(tool)
    try:
        answered = app._probe_has_audio(clip)
    finally:
        _shutil.which = real_which
    check(answered is False,
          "an unprobeable clip is not claimed to have audio")
    check(app._audio_unknown == [clip],
          "a clip whose sound could not be checked is recorded",
          repr(app._audio_unknown))

    said = []
    app._exp_show_status = lambda text, error=False: said.append(text)
    app._exp_prog = type("P", (), {"set_fraction": lambda self, f: None})()
    app._exp_errfh = None
    app._exp_done = False
    app._exp_out = os.path.join(tmp, "silent.mp4")
    app._exp_draft = os.path.join(tmp, ".nbvid-silent.mp4.part")
    with open(app._exp_draft, "wb") as fh:
        fh.write(b"\0" * 2048)
    app._exp_gone = []
    app._exp_finish(0)
    check(any("plays silent" in t for t in said),
          "the export says the sound is missing instead of only 'Saved'",
          repr(said))
    check(os.path.isfile(app._exp_out),
          "the finished draft is moved onto the destination")
    check(not os.path.exists(app._exp_draft),
          "no draft is left beside the finished film")
    app._audio_unknown = []

    print("== the project survives a render ==")
    before = json.dumps(app._serialize(), sort_keys=True)
    app.clips = [c(IMG, "image", 2), c(VID, "video", 3, transition="trfade")]
    kept = json.dumps(app._serialize(), sort_keys=True)
    render("survives", app.clips)
    check(json.dumps(app._serialize(), sort_keys=True) == kept,
          "a render does not alter the project")
    del before

    print("== missing media is reported, not hidden ==")
    app._bin = [{"path": os.path.join(home, "gone.png"), "name": "gone.png",
                 "kind": "image", "dur": 4}]
    app.clips = [c(0, "image", 3)]
    check(app._missing_media(app.clips) == ["gone.png"],
          "a clip whose file is gone is named")
    ok, out, want, err = render("missing-media", app.clips)
    check(ok, "and the rest of the movie still renders", err)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d checks, %d passed, %d FAILED"
          % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    print("RESULT: %s" % ("ALL PASS" if not FAIL else "SOME FAILED"))
    for f in FAIL:
        print("   -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
