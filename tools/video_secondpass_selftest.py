#!/usr/bin/env python3
"""video_secondpass_selftest — what a SECOND drive-through of the Video Editor
found, after the first wave of fixes landed.

Eight findings came out of driving the app again the way a person uses it
(import, place, caption, effect, transition, play, arrange, export). Each is
held down here by a check that goes RED without its fix:

  * Play never streamed anything. Both playback entry points asked the CLIP
    dict for "path", and a clip has never carried one (it carries an index into
    the media bin), so _play_clip_live returned False on its fourth line for
    every clip ever made: the gtksink stream was never opened, the film played
    as one still a second, and a clip with sound played silent — on a machine
    whose player was available the whole time.
  * A still carrying a visual effect or a caption showed NO picture: the
    preview decode put -ss before -i on a single-frame input, ffmpeg emitted
    zero frames and exited 0, so the stage fell back to the file-name
    placeholder and the storyboard card stayed a black rectangle. The export
    rendered the same still perfectly throughout.
  * "Delete Clip…" still promised a dialog after the confirmation was retired.
  * Giving a clip a transition ERASED its name from the timeline's Video lane:
    the transition mark pushed the chip 18px over the width of the clip's own
    seconds and the whole chip — name included — was dropped.
  * The "+" between two storyboard cards was a plain Box: the one gesture the
    picture asks for did nothing at all.
  * "◀ Move" / "Move ▶" in Properties stayed enabled on the first and last clip
    and did nothing, while the Clip menu greyed the same two actions out.
  * The three Properties entries, both menus and the volume slider had no
    accessible name, beside two spin buttons that do.
  * "Saved · …" and three picker titles were raw English literals whose
    translations were sitting in all seventeen catalogs, unreachable.

Run:  tools/guestrun.sh python3 tools/video_secondpass_selftest.py

Real widgets and the real handlers throughout — the app object is the shipped
one. Media fixtures are built with the ffmpeg under test, so a fixture can
never be the reason a check fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)

FFMPEG = shutil.which("ffmpeg")

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
                  traceback.format_exc(limit=4).replace("\n", " | "))
    wrapped.__name__ = fn.__name__
    return wrapped


def run(argv, timeout=180, env=None):
    return subprocess.run(argv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, env=env)


def main():
    if not FFMPEG:
        print("SKIP: no ffmpeg on PATH — the media fixtures and the preview "
              "decode both need it")
        return 0

    tmp = tempfile.mkdtemp(prefix="nbvid-2nd-")
    home = os.path.join(tmp, "home")
    for d in ("Videos", "Pictures", "Documents", ".config/notebook"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    os.environ["NB_HOME"] = home

    movie = os.path.join(home, "Videos", "harbour.mp4")
    photo = os.path.join(home, "Pictures", "lighthouse.jpg")
    jobs = [
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x240:duration=3:rate=10", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p",
         movie],
        # a JPEG on purpose: it is what a camera and a phone hand over, and it
        # is the format that yields no frame for ANY input -ss
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "gradients=size=640x360:duration=1:rate=1", "-frames:v", "1", photo],
    ]
    for j in jobs:
        r = run(j)
        if r.returncode != 0:
            print("cannot build fixtures: %s"
                  % r.stderr.decode("utf-8", "replace")[-400:])
            return 2

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk                                # noqa: F401
    import video

    BIN = [
        {"path": movie, "name": "harbour.mp4", "kind": "video", "dur": 3},
        {"path": photo, "name": "lighthouse.jpg", "kind": "image", "dur": 4},
    ]
    MOVIE, PHOTO = 0, 1

    app = video.VideoEditor()
    app._bin = [dict(m) for m in BIN]

    def clip(mi, kind, dur, **kw):
        c = video._new_clip(mi, kind, dur)
        c.update(kw)
        return c

    def reset(clips, sel=None):
        app._bin = [dict(m) for m in BIN]
        app.clips = list(clips)
        app._sel_music = False
        app._sel_cell = None
        app._render_all()
        if sel is not None:
            app._select_cell(sel, user_caused=False)

    # ---------------------------------------------------------------- 1 -----
    @part
    def f1_play_streams_the_file():
        """Play opens the clip's own file on the player."""
        reset([clip(MOVIE, "video", 3)], sel=0)

        class Recorder:
            available = True
            widget = None

            def __init__(self):
                self.opened = []

            def open_async(self, path, at=0.0, play=False, rate=1.0,
                           done=None):
                self.opened.append(path)
                if done is not None:
                    done(True)
                return True

            def stop(self):
                pass

        rec = Recorder()
        app._player = rec
        app._playing = True
        app._live_clip = None
        app._live_pending = None
        app._live_failed = None
        try:
            app._playback_step(0, 0.0)
        finally:
            app._playing = False
        check(rec.opened == [movie],
              "Play streams the clip's own media file", repr(rec.opened))
        check(app._live_clip == 0,
              "and the stage is handed to the stream that prerolled",
              repr(app._live_clip))
        app._player = None
        app._live_clip = None

    # ---------------------------------------------------------------- 2 -----
    @part
    def f2_still_with_a_look_decodes():
        """A still carrying an effect still has a picture."""
        c = clip(PHOTO, "image", 4, effect="sepia")
        reset([c], sel=0)
        argv = app._pv_frame_cmd(FFMPEG, c, photo, 0.0, os.path.join(tmp, "x.png"))
        check("-ss" not in argv,
              "a single-frame input is never seeked into",
              " ".join(argv))
        out = os.path.join(tmp, "still-effect.png")
        r = run(app._pv_frame_cmd(FFMPEG, c, photo, 0.0, out))
        size = os.path.getsize(out) if os.path.isfile(out) else 0
        check(r.returncode == 0 and size > 0,
              "and a still with a visual effect decodes to a real frame",
              "rc=%s size=%s" % (r.returncode, size))
        # the same still with a CAPTION burned in goes through the same chain
        c2 = clip(PHOTO, "image", 4, title="Low tide at six")
        out2 = os.path.join(tmp, "still-caption.png")
        r2 = run(app._pv_frame_cmd(FFMPEG, c2, photo, 0.0, out2))
        size2 = os.path.getsize(out2) if os.path.isfile(out2) else 0
        check(r2.returncode == 0 and size2 > 0,
              "and so does a still with a caption on it",
              "rc=%s size=%s" % (r2.returncode, size2))

    # ---------------------------------------------------------------- 3 -----
    @part
    def f3_delete_promises_no_dialog():
        """The Clip menu's Delete carries no ellipsis."""
        reset([clip(MOVIE, "video", 3)], sel=0)
        labels = [i[0] for i in app.menu_items("Clip")
                  if isinstance(i, (tuple, list))]
        dels = [lab for lab in labels if lab.startswith("Delete")]
        check(dels and not any(lab.rstrip().endswith("…") for lab in dels),
              "Delete Clip does not promise a dialog it no longer shows",
              repr(dels))

    # ---------------------------------------------------------------- 4 -----
    @part
    def f4_transition_keeps_the_name():
        """A lead-in transition never costs a clip its name in the lane."""
        reset([clip(MOVIE, "video", 3), clip(MOVIE, "video", 3)], sel=1)
        pps = app._pps()
        w = int(round(3 * pps))          # three seconds at the default zoom

        def names(cell):
            out = []
            stack = [cell]
            while stack:
                x = stack.pop()
                if isinstance(x, Gtk.Label):
                    out.append(x.get_text())
                if isinstance(x, Gtk.Container):
                    stack.extend(x.get_children())
            return out

        app.clips[1]["transition"] = None
        plain = names(app._lane_cell("Video", app.clips[1], w, 1))
        app.clips[1]["transition"] = "trdissolve"
        marked = names(app._lane_cell("Video", app.clips[1], w, 1))
        check("harbour.mp4" in plain,
              "a clip is named in the Video lane", repr(plain))
        check("harbour.mp4" in marked,
              "and giving it a transition does not take that name away",
              repr(marked))
        # the cell is still exactly the clip's own seconds against the ruler
        hit = app._lane_cell("Video", app.clips[1], w, 1)
        hit.show_all()          # GTK3 measures an invisible widget as zero
        got = hit.get_child().get_preferred_width().minimum_width
        check(got == w,
              "and the cell is still exactly as wide as the clip is long",
              "want %d got %d" % (w, got))

    # ---------------------------------------------------------------- 5 -----
    @part
    def f5_connector_is_a_control():
        """The + between two cards makes the transition it draws."""
        reset([clip(MOVIE, "video", 3), clip(MOVIE, "video", 3)], sel=None)
        conn = app._story_connector(1)
        check(hasattr(conn, "clicked"),
              "the storyboard connector is a control, not a picture",
              type(conn).__name__)
        if hasattr(conn, "clicked"):
            conn.clicked()
            check(bool(app.clips[1].get("transition")),
                  "and clicking an empty join makes a transition there",
                  repr(app.clips[1].get("transition")))
            was = app.clips[1].get("transition")
            app._render_story()
            again = [w for w in app._story_row.get_children()
                     if "transhit" in w.get_style_context().list_classes()]
            if again:
                again[0].clicked()
            check(app.clips[1].get("transition") == was,
                  "and clicking a join that has one does not take it away",
                  repr(app.clips[1].get("transition")))
            check(bool(app._undo),
                  "and the step it made is one Ctrl+Z away", repr(app._undo))

    # ---------------------------------------------------------------- 6 -----
    @part
    def f6_move_buttons_follow_the_selection():
        """Move is offered only where it can move something."""
        reset([clip(MOVIE, "video", 3), clip(MOVIE, "video", 3),
               clip(MOVIE, "video", 3)], sel=0)
        check(not app._prop_mvl.get_sensitive()
              and app._prop_mvr.get_sensitive(),
              "on the first clip only Move right is offered",
              "%s / %s" % (app._prop_mvl.get_sensitive(),
                           app._prop_mvr.get_sensitive()))
        check(bool(app._prop_mvl.get_tooltip_text()),
              "and the one that is off says why",
              repr(app._prop_mvl.get_tooltip_text()))
        app._select_cell(1, user_caused=False)
        check(app._prop_mvl.get_sensitive() and app._prop_mvr.get_sensitive(),
              "in the middle both are offered")
        app._select_cell(2, user_caused=False)
        check(app._prop_mvl.get_sensitive()
              and not app._prop_mvr.get_sensitive(),
              "on the last clip only Move left is offered",
              "%s / %s" % (app._prop_mvl.get_sensitive(),
                           app._prop_mvr.get_sensitive()))

    # ---------------------------------------------------------------- 7 -----
    @part
    def f7_every_field_announces_itself():
        """Nothing in Properties is silent to a screen reader."""
        reset([clip(MOVIE, "video", 3)], sel=0)
        fields = [("title card text", app._prop_cardtext),
                  ("title card subtitle", app._prop_cardsub),
                  ("caption", app._prop_title),
                  ("trim", app._prop_trim),
                  ("length", app._prop_dur),
                  ("visual effect", app._prop_effect),
                  ("pan & zoom", app._prop_kb),
                  ("volume", app._prop_vol),
                  ("background music volume", app._mus_vol)]
        silent = [n for n, w in fields
                  if not (w.get_accessible().get_name() or "").strip()]
        check(not silent,
              "every Properties field announces what it is", repr(silent))

    # ---------------------------------------------------------------- 8 -----
    @part
    def f8_translations_are_reachable():
        """The export result and the picker titles go through the catalog."""
        # Its OWN NB_HOME. nbapp scopes the single-instance lock by NB_HOME,
        # and this process is already holding the Video Editor's — a child
        # sharing it stands down with os._exit(0) and prints nothing at all,
        # which reads as a check that did not run.
        lang_home = tempfile.mkdtemp(prefix="nbvid-2nd-fr-")
        os.makedirs(os.path.join(lang_home, "Videos"), exist_ok=True)
        os.makedirs(os.path.join(lang_home, "Music"), exist_ok=True)
        script = r'''
import os, sys, json
sys.path.insert(0, %r)
os.environ["NB_HOME"] = %r
import gi
gi.require_version("Gtk", "3.0")
import nbpicker, video
from nbi18n import _t

opens, saves = [], []
nbpicker.open_file = lambda p, title="", **k: opens.append(title)
nbpicker.save_file = lambda p, title="", **k: saves.append(title)

app = video.VideoEditor()
app._choose_file(save=False)
app._choose_file(save=True)
app._add_music()

# a finished render, so the real success line is the one measured
out = os.path.join(os.environ["NB_HOME"], "Videos", "film.mp4")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out + ".draft", "wb").write(b"x" * 32)
app._exp_out, app._exp_draft = out, out + ".draft"
app._exp_errfh = None
app._exp_gone = []
app._audio_unknown = []
said = []
app._exp_show_status = lambda t, error=False: said.append(t)
app.notify = lambda *a, **k: None
class B:
    def set_label(self, *a): pass
    def set_sensitive(self, *a): pass
app._exp_go = app._exp_cancel = B()
class P:
    def set_fraction(self, *a): pass
app._exp_prog = P()
app._exp_finish(0)
print(json.dumps({"open": opens[0] if opens else None,
                  "save": saves[0] if saves else None,
                  "music": opens[1] if len(opens) > 1 else None,
                  "saved": said[-1] if said else "",
                  "t_saved": _t("Saved"), "t_open": _t("Open Project"),
                  "t_save": _t("Save Project As"),
                  "t_music": _t("Add Background Music")}))
''' % (DE, lang_home)
        env = dict(os.environ, NB_LANG="fr", PYTHONPATH=DE,
                   NB_HOME=lang_home)
        r = run([sys.executable, "-c", script], env=env)
        line = [ln for ln in r.stdout.decode("utf-8", "replace").splitlines()
                if ln.startswith("{")]
        if not check(bool(line), "the language run produced an answer",
                     "rc=%s out=%r err=%r" % (
                         r.returncode,
                         r.stdout.decode("utf-8", "replace")[-400:],
                         r.stderr.decode("utf-8", "replace")[-800:])):
            return
        got = json.loads(line[-1])
        check(got["saved"].startswith(got["t_saved"]),
              "the export says Saved in the language the OS is in",
              "%r vs %r" % (got["saved"], got["t_saved"]))
        for key, tkey, what in (("open", "t_open", "Open Project"),
                                ("save", "t_save", "Save Project As"),
                                ("music", "t_music",
                                 "Add Background Music")):
            check(got[key] == got[tkey],
                  "the %s picker is titled in that language too" % what,
                  "%r vs %r" % (got[key], got[tkey]))

    f1_play_streams_the_file()
    f2_still_with_a_look_decodes()
    f3_delete_promises_no_dialog()
    f4_transition_keeps_the_name()
    f5_connector_is_a_control()
    f6_move_buttons_follow_the_selection()
    f7_every_field_announces_itself()
    f8_translations_are_reachable()

    app._pv_teardown()
    try:
        app.destroy()
    except Exception:                                             # noqa: BLE001
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d checks, %d passed, %d FAILED"
          % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    print("RESULT: %s" % ("ALL PASS" if not FAIL else "SOME FAILED"))
    for f in FAIL:
        print("   -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
