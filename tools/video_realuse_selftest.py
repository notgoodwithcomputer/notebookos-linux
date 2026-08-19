#!/usr/bin/env python3
"""video_realuse_selftest — what a real drive-through of the Video Editor found.

Thirteen findings came out of driving this app the way a person uses it (import,
place, caption, trim, transition, save, reopen, export). The ones that were real
are held down here, each by a check that goes RED without its fix, so none of
them can come back quietly:

  * the export could not choose a container for its hidden draft, so EVERY
    export failed with "The video could not be saved";
  * a title card whose text was deliberately cleared came back reading "Title";
  * timeline lane cells were minimum widths, so one second of footage was a
    different length in every lane and none of them matched the ruler;
  * typing a caption re-decoded the frame per keystroke and dropped the stage to
    the dark placeholder in between;
  * a trim that shortened a clip left the film's own length stale;
  * a transient notice was written over the Project row and never left;
  * a subtitle typed on a card never reached the preview stage;
  * a transition could be "applied" to the first clip, which has no lead-in;
  * Trim offered in-points the render clamps away from;
  * the Audio lane promised sound a video-only file does not have;
  * placing a clip left the Edit menu saying a bare "Undo";
  * reopening forgot which named project the session was in.

Run:  tools/guestrun.sh python3 tools/video_realuse_selftest.py

Real widgets and the real handlers throughout — the app object is the shipped
one. Media fixtures are built with the ffmpeg under test, so a fixture can never
be the reason a check fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

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
    return bool(ok)


def part(fn):
    """Run one finding's block; a crash inside it fails BY NAME, never by
    traceback, so the suite always reports on every finding."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:                                         # noqa: BLE001
            check(False, "%s did not run to the end" % fn.__name__,
                  traceback.format_exc(limit=3).replace("\n", " | "))
    wrapped.__name__ = fn.__name__
    return wrapped


def run(argv, timeout=300):
    return subprocess.run(argv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


def main():
    if not FFMPEG or not FFPROBE:
        print("SKIP: no ffmpeg/ffprobe on PATH — the media fixtures, the export "
              "render and the sound probe all need them")
        return 0

    tmp = tempfile.mkdtemp(prefix="nbvid-realuse-")
    home = os.path.join(tmp, "home")
    for d in ("Videos", "Pictures", "Documents", ".config/notebook"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    os.environ["NB_HOME"] = home

    # ---- fixtures: one clip with sound, one video-only clip, one still ----
    sound = os.path.join(home, "Videos", "clipA.mp4")
    silent = os.path.join(home, "Videos", "clipB.mp4")
    still = os.path.join(home, "Pictures", "still.png")
    jobs = [
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x240:duration=2:rate=10", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p",
         sound],
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x240:duration=2:rate=10", "-pix_fmt", "yuv420p",
         silent],
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x240:duration=1:rate=1", "-frames:v", "1", still],
    ]
    for j in jobs:
        r = run(j)
        if r.returncode != 0:
            print("cannot build fixtures: %s"
                  % r.stderr.decode("utf-8", "replace")[-400:])
            return 2

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib                          # noqa: F401
    import video

    def pump(seconds):
        """Run the real main loop for wall time, so a coalesced refresh and a
        self-clearing notice get their turn exactly as they would on screen."""
        ctx = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.01)

    BIN = [
        {"path": sound, "name": "clipA.mp4", "kind": "video", "dur": 2},
        {"path": silent, "name": "clipB.mp4", "kind": "video", "dur": 2},
        {"path": still, "name": "still.png", "kind": "image", "dur": 4},
    ]
    SOUND, SILENT, STILL = 0, 1, 2

    app = video.VideoEditor()
    app._bin = [dict(m) for m in BIN]

    def clip(mi, kind, dur, **kw):
        c = video._new_clip(mi, kind, dur)
        c.update(kw)
        return c

    def reset(clips, sel=None):
        # the media bin too: a check that loads a project of its own (F2)
        # replaces it, and every clip index below points into this one
        app._bin = [dict(m) for m in BIN]
        app.clips = list(clips)
        app._sel_music = False
        app._sel_cell = None
        app._render_all()
        if sel is not None:
            app._select_cell(sel)
        pump(0.05)

    # =================================================================
    @part
    def f1_export_container():
        print("\n== F1 the export names its own container ==")
        draft = os.path.join(tmp, ".nbvid-My Film.mp4.part")
        cmd, _total, err = app._build_ffmpeg_cmd(
            [clip(STILL, "image", 1)], draft, None)
        if not check(cmd is not None, "the export command is assembled",
                     str(err)):
            return
        pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1)]
        after = cmd.index("-filter_complex")
        muxed = [i for i, (a, b) in enumerate(pairs)
                 if a == "-f" and b == "mp4" and i > after]
        check(bool(muxed),
              "the export names the mp4 muxer instead of leaving it to the "
              "draft's file extension", " ".join(cmd[-6:]))
        check(cmd[-1] == draft, "the render still writes to the hidden draft")
        r = run(cmd)
        size = os.path.getsize(draft) if os.path.exists(draft) else 0
        if not check(r.returncode == 0 and size > 0,
                     "a real export to the draft path succeeds",
                     "exit %s: %s" % (r.returncode,
                                      r.stderr.decode("utf-8", "replace")[-220:])):
            return
        pr = run([FFPROBE, "-v", "error", "-show_entries",
                  "stream=codec_type", "-of", "csv=p=0", draft])
        check(b"video" in (pr.stdout or b""),
              "and the finished draft decodes as a video",
              (pr.stdout or b"").decode("utf-8", "replace"))
        os.remove(draft)

    # =================================================================
    @part
    def f2_blank_title_card():
        print("\n== F2 a cleared title card stays cleared ==")
        app.clips = [video._new_title("", "only a subtitle")]
        data = json.loads(json.dumps(app._serialize()))
        app._apply_data(data)
        check(app.clips and app.clips[0].get("cardtext") == "",
              "a title card whose text was cleared reopens cleared",
              repr(app.clips[0].get("cardtext") if app.clips else None))
        check(app.clips and app.clips[0].get("cardsub") == "only a subtitle",
              "and its subtitle survives with it")
        app._apply_data({"bin": [], "clips": [{"kind": "title"}]})
        check(app.clips and app.clips[0].get("cardtext") == "Title",
              "a card record carrying no text field at all still gets the "
              "default word",
              repr(app.clips[0].get("cardtext") if app.clips else None))

    # =================================================================
    @part
    def f3_lane_widths():
        print("\n== F3 a lane cell is exactly its clip's seconds wide ==")
        reset([clip(SOUND, "video", 1, title="Summer 2026"),
               video._new_title("Title", ""),
               clip(SILENT, "video", 1),
               clip(SILENT, "video", 1),
               video._new_title("The End", "")], sel=0)
        pps = app._pps()
        layout, _total = app._layout()
        wrong = []
        for k, cells in sorted(app._timeline_clip_cells.items()):
            _off, d, td = layout[k]
            want = max(6, int(round((d - td) * pps)))
            for cell in cells:
                got = cell.get_preferred_width()
                if (got.minimum_width, got.natural_width) != (want, want):
                    wrong.append("clip %d: wanted %d, got %s"
                                 % (k, want, tuple(got)))
        check(not wrong,
              "every timeline lane cell is exactly its clip's seconds wide, in "
              "every lane", "; ".join(wrong[:3]))
        widths = {k: {tuple(c.get_preferred_width()) for c in cells}
                  for k, cells in app._timeline_clip_cells.items()}
        check(all(len(v) == 1 for v in widths.values()),
              "so the Video, Audio and Titles lanes line up with each other",
              str(widths))
        over = []
        for k, cells in sorted(app._timeline_clip_cells.items()):
            for cell in cells:
                box = cell.get_child()
                room = cell.get_preferred_width().minimum_width
                for ch in box.get_children():
                    need = ch.get_preferred_width().minimum_width
                    if need > room:
                        over.append("clip %d: %dpx chip in a %dpx cell"
                                    % (k, need, room))
        check(not over,
              "and no chip is drawn wider than the clip it belongs to",
              "; ".join(over[:3]))

    # =================================================================
    @part
    def f4_caption_typing():
        print("\n== F4 typing a caption ==")
        reset([clip(SOUND, "video", 2), clip(SILENT, "video", 2)], sel=0)
        calls = []
        real = app._update_preview
        app._update_preview = lambda: calls.append(1)
        try:
            for text in ("S", "Su", "Sum", "Summ", "Summe", "Summer"):
                app._prop_title.set_text(text)
            check(not calls,
                  "typing a caption does not re-render the stage per keystroke",
                  "%d refreshes for 6 keys" % len(calls))
            pump(1.0)
            check(len(calls) == 1,
                  "and refreshes it once, after the typing settles",
                  "%d refreshes" % len(calls))
        finally:
            app._update_preview = real
        check(app.clips[0].get("title") == "Summer",
              "the caption itself is on the clip either way")

        # the stage keeps the picture it has while the same shot re-composites
        app._frame_cache.clear()
        c = app.clips[0]
        app._prev_shot = app._frame_key(c)
        shown = []
        realph = app._show_placeholder
        app._show_placeholder = lambda text: shown.append(text)
        try:
            c["title"] = "Summer 2026"
            app._update_preview()
            check(not shown,
                  "the stage keeps its frame while the same shot re-composites",
                  str(shown))
            app._sel_cell = 1
            app._frame_cache.clear()
            app._update_preview()
            check(bool(shown),
                  "but a different clip still falls back to the placeholder")
        finally:
            app._show_placeholder = realph
            app._pv_teardown()

    # =================================================================
    @part
    def f5_trim_totals():
        print("\n== F5 a trim that shortens a clip ==")
        reset([clip(SOUND, "video", 2), clip(SOUND, "video", 2)], sel=0)
        before = app._prop_vals["Duration"].get_text()
        app._prop_trim.set_value(1.0)
        pump(0.2)
        total = app._fmt_hms(app._total())
        check(app.clips[0]["duration"] == 1,
              "the clip is shortened to what its source has left", before)
        check(app._prop_vals["Duration"].get_text() == total,
              "the Duration row follows a trim immediately",
              "row says %s, film is %s"
              % (app._prop_vals["Duration"].get_text(), total))
        check(app._tc.get_text() == "00:00:00 / " + total,
              "and so does the transport total", app._tc.get_text())

    # =================================================================
    @part
    def f6_notice_line():
        print("\n== F6 a transient notice ==")
        getattr(app, "_clear_flash", lambda: None)()
        row = app._prop_vals["Project"]
        row.show()
        pump(0.05)
        was = row.get_text()
        width = row.get_preferred_width().minimum_width
        app._flash("clipA.mp4 is already in Media.")
        pump(0.05)
        check(row.get_text() == was,
              "a notice never overwrites the Project row", row.get_text())
        check(row.get_preferred_width().minimum_width == width,
              "and never stretches it (which shifted the preview stage)",
              "%d -> %d" % (width, row.get_preferred_width().minimum_width))
        # getattr, not the attribute: without the notice line there is nothing
        # to ask, and these must still fail BY NAME rather than by traceback
        line = getattr(app, "_prop_flash", None)
        check(line is not None and line.get_visible()
              and line.get_text() == "clipA.mp4 is already in Media.",
              "the notice is on a line of its own",
              repr(line.get_text()) if line is not None else "no notice line")
        check(line is not None and line.get_line_wrap()
              and line.get_max_width_chars() > 0,
              "which wraps a sentence inside a bounded width")
        pump(6.0)
        check(line is not None and not line.get_visible(),
              "and it takes itself away again a few seconds later",
              repr(line.get_text()) if line is not None else "no notice line")

    # =================================================================
    @part
    def f7_card_subtitle():
        print("\n== F7 a subtitle typed on a card ==")
        reset([video._new_title("Title", "")], sel=0)
        calls = []
        real = app._update_preview
        app._update_preview = lambda: calls.append(1)
        try:
            app._prop_cardsub.set_text("only a subtitle")
            pump(1.0)
            check(len(calls) == 1,
                  "a subtitle typed on a title card reaches the preview stage",
                  "%d stage refreshes" % len(calls))
        finally:
            app._update_preview = real
        check(app.clips[0].get("cardsub") == "only a subtitle",
              "and is on the card itself")

    # =================================================================
    @part
    def f9_first_clip_transition():
        print("\n== F9 the first clip has no lead-in ==")
        reset([clip(SOUND, "video", 2), clip(SOUND, "video", 2)], sel=0)
        btns = [cell.get_parent() for cell in app._trans_cells.values()]
        check(btns and not any(b.get_sensitive() for b in btns),
              "the transition palette is unavailable on the first clip",
              str([b.get_sensitive() for b in btns]))
        app._on_transition_click(None, "trblack")
        check(app.clips[0].get("transition") is None,
              "so a transition cannot be applied where nothing precedes it",
              repr(app.clips[0].get("transition")))
        items = [it for it in app.menu_items("Clip")
                 if isinstance(it, (list, tuple))]
        addtr = [cb for lab, cb in items if lab.startswith("Add Transition")]
        check(addtr and addtr[0] is None,
              "and Clip > Add Transition is off there too")
        app.clips[0]["transition"] = "trfade"     # e.g. an older project file
        app._select_cell(0)
        lead = getattr(app, "_lead_in", None)
        check(lead is not None and lead(0) is None
              and app._active_transition is None,
              "a transition stored on the first clip is not claimed anywhere",
              repr(app._active_transition))
        app._select_cell(1)
        check(all(b.get_sensitive() for b in btns),
              "a clip with something before it can take one")
        app._on_transition_click(None, "trblack")
        check(app.clips[1].get("transition") == "trblack",
              "and applying it still works")
        dots = []

        def walk(w):
            dots.append(w)
            if isinstance(w, Gtk.Container):
                for ch in w.get_children():
                    walk(ch)
        walk(app._story_row)
        setdots = [w for w in dots
                   if "transdotset" in w.get_style_context().list_classes()]
        check(len(setdots) == 1,
              "the storyboard draws the connector for exactly the join that "
              "has one", str(len(setdots)))

    # =================================================================
    @part
    def f10_trim_bounds():
        print("\n== F10 Trim only offers in-points the render can use ==")
        reset([clip(SOUND, "video", 2)], sel=0)
        c = app.clips[0]
        srcdur = app._clip_srcdur(c)
        lo, hi = app._prop_trim.get_range()
        check(lo == 0 and abs(hi - max(0.0, srcdur - 1.0)) < 0.01,
              "Trim stops one clip-length short of the end of the source",
              "range 0..%s of %.2fs source" % (hi, srcdur))
        app._prop_trim.set_value(500)             # far past the end
        pump(0.2)
        rendered = app._render_start(c, app._clip_dur(c),
                                     float(c.get("speed", 1.0)))
        check(abs(app._prop_trim.get_value() - c["start"]) < 0.01
              and abs(rendered - c["start"]) < 0.01,
              "so the in-point the panel states is the one that is rendered",
              "panel %.2f, clip %.2f, render %.2f"
              % (app._prop_trim.get_value(), c["start"], rendered))

    # =================================================================
    @part
    def f11_audio_lane():
        print("\n== F11 the Audio lane ==")
        reset([clip(SOUND, "video", 6), clip(SILENT, "video", 6)])
        app._audio_probe_cache = {sound: True, silent: False}
        app._render_timeline()
        pump(0.05)

        def audio_chip(k):
            cell = app._timeline_clip_cells[k][1].get_child()
            return any("tlchipaudio" in ch.get_style_context().list_classes()
                       for ch in cell.get_children())
        check(audio_chip(0), "a clip that carries sound keeps its Audio chip")
        check(not audio_chip(1),
              "a video-only clip is not given one the export cannot honour")
        check(app._probe_has_audio(silent) is False,
              "and the probe agrees with the file")

    # =================================================================
    @part
    def f12_undo_names():
        print("\n== F12 the Edit menu names what it takes back ==")
        reset([])
        app._undo = []
        app._undo_names = []
        app.sel_media = SOUND
        app._append_selected_media()
        pump(0.05)
        check(app._undo_names[-1] == "Add clip",
              "placing a clip records a NAMED undo step",
              repr(app._undo_names[-1] if app._undo_names else None))
        first = [it[0] for it in app.menu_items("Edit")
                 if isinstance(it, (list, tuple))][0]
        check(first.startswith("Undo Add clip"),
              "so the Edit menu says what Ctrl+Z would undo", first)
        app._prop_title.set_text("Summer 2026")
        pump(0.05)
        check(app._undo_names[-1] == "Caption",
              "and a typed caption is named too",
              repr(app._undo_names[-1]))

    # =================================================================
    @part
    def f13_session_project():
        print("\n== F13 reopening comes back in the same project ==")
        proj = os.path.join(home, "Documents", "myfilm.json")
        reset([clip(SOUND, "video", 2)])
        check(app._write_file(proj), "the project is saved to a named file")
        app._path = proj
        app._save_project()
        auto = json.load(open(video.PROJECT_FILE))
        check(auto.get("path") == proj,
              "the autosave remembers which project the session was in",
              repr(auto.get("path")))
        named = json.load(open(proj))
        check("path" not in named,
              "and a named project file does not carry a path it can be "
              "copied away from")
        second = video.VideoEditor()
        try:
            check(second._path == proj,
                  "reopening restores the open project", repr(second._path))
            check(second._prop_vals["Project"].get_text() == "myfilm",
                  "and the Project row names it, not 'Untitled'",
                  second._prop_vals["Project"].get_text())
        finally:
            second.destroy()

    f1_export_container()
    f2_blank_title_card()
    f3_lane_widths()
    f4_caption_typing()
    f5_trim_totals()
    f6_notice_line()
    f7_card_subtitle()
    f9_first_clip_transition()
    f10_trim_bounds()
    f11_audio_lane()
    f12_undo_names()
    f13_session_project()

    app._pv_teardown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d checks, %d passed, %d FAILED"
          % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    print("RESULT: %s" % ("ALL PASS" if not FAIL else "SOME FAILED"))
    for f in FAIL:
        print("   -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
