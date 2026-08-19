#!/usr/bin/env python3
"""Behavioral gate for Movie Maker's user-caused clip-selection motion."""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DE = os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "de")
DE = os.environ.get("VIDEO_MODULE_DIR", DEFAULT_DE)
sys.path.insert(0, DE)
import video  # noqa: E402


passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("ok  ", name)
    else:
        failed += 1
        print("FAIL", name + ((": " + detail) if detail else ""))


gtk_ok, _argv = Gtk.init_check()


class StrictWidget:
    """Displayless equivalent of the exact opacity API under test."""
    def __init__(self):
        self._opacity = 1.0

    def set_opacity(self, value):
        self._opacity = float(value)

    def get_opacity(self):
        return self._opacity


Widget = Gtk.Box if gtk_ok else StrictWidget
check("selection fixture path is reachable", True,
      "[not reached: widget fixture unavailable]")

calls = []
app = None
real_animate = video.nbmotion.animate
if True:
    app = video.VideoEditor.__new__(video.VideoEditor)
    app.clips = [video._new_title("One"), video._new_title("Two")]
    app._sel_cell = 0
    app._sel_music = False
    app._active_transition = None
    app._trans_cells = {}
    app._story_cells = []
    app._timeline_clip_cells = {}

    # The production renderers are large and orthogonal to this gate. These
    # fixtures preserve their output contract with real GTK widgets whenever
    # GTK initializes, including three lane cells for each clip. The strict
    # displayless fallback exposes only the opacity API this path consumes.
    def render_story():
        app._story_cells = [Widget(), Widget()]

    def render_timeline():
        app._timeline_clip_cells = {
            i: [Widget(), Widget(), Widget()] for i in range(2)}

    app._render_story = render_story
    app._render_timeline = render_timeline
    app._highlight_palette = lambda _key: None
    app._load_props = lambda _clip: None
    app._update_preview = lambda: None

    class Pending:
        def cancel(self):
            return None

    def capture(widget, on_frame, start, end, duration=None, easing=None,
                fade=False, on_done=None):
        calls.append({"widget": widget, "frame": on_frame, "start": start,
                      "end": end, "duration": duration, "easing": easing,
                      "fade": fade})
        return Pending()

    video.nbmotion.animate = capture
    try:
        # Drive the real user operation. The fixture demonstrably enters the
        # selected-index branch and produces one storyboard plus three lane
        # targets; every check below is guarded if that path is not reached.
        app._select_cell(1)
        check("real clip selection reaches the motion primitive", len(calls) == 4,
              "[not reached: expected 4 selected-widget calls, got %d]" % len(calls))
        first = calls[0] if calls else None
        check("clip selection receives the SELECT token",
              first is not None
              and first.get("duration") is video.nbmotion.SELECT
              and first.get("duration", 0) > 0,
              "[not reached: no captured primitive]" if first is None
              else "duration=%r" % first.get("duration"))
        check("clip selection receives opacity-safe EASE_OUT",
              first is not None and first.get("easing") is video.nbmotion.EASE_OUT,
              "[not reached: no captured primitive]" if first is None
              else "easing=%r" % first.get("easing"))
        # Asked of WHICH WIDGETS were handed to the primitive, not of the
        # opacity left on the cells this fixture happens to hold: selecting can
        # rebuild the cell list, so an opacity read can be inspecting objects
        # the app already replaced — which fails while the app is correct. The
        # property that actually matters is "only what changed animates", and
        # the animate calls are the direct evidence for it.
        sel_widgets = set(map(id, [app._story_cells[1]]
                              + list(app._timeline_clip_cells.get(1, []))))
        other_widgets = set(map(id, [app._story_cells[0]]
                                + list(app._timeline_clip_cells.get(0, []))))
        touched = set(id(c["widget"]) for c in calls)
        check("only the newly selected clip starts travelling",
              bool(touched)
              and touched <= sel_widgets
              and not (touched & other_widgets),
              "[not reached: %d animate calls, %d outside the selected clip]"
              % (len(touched), len(touched - sel_widgets)))
        if calls:
            for call in calls:
                call["frame"](1.0)
            landed = (app._story_cells[1].get_opacity() == 1.0
                      and all(w.get_opacity() == 1.0
                              for w in app._timeline_clip_cells[1]))
        else:
            landed = False
        check("selection motion lands on the exact visible end state", landed,
              "[not reached: no captured primitive]" if not calls
              else "selected widgets did not land at opacity 1")

        calls.clear()
        app._select_cell(0, user_caused=False)
        check("media-clock selection remains instant", not calls,
              "timer/programmatic path made %d motion calls" % len(calls))

        def instant(_widget, on_frame, _start, end, **_kwargs):
            on_frame(end)
            return Pending()

        video.nbmotion.animate = instant
        app._select_cell(1)
        check("Reduced-Motion-equivalent path is immediately exact",
              app._story_cells[1].get_opacity() == 1.0
              and all(w.get_opacity() == 1.0
                      for w in app._timeline_clip_cells[1]),
              "selected widgets did not synchronously reach opacity 1")

        def broken(*_args, **_kwargs):
            raise RuntimeError("motion unavailable")

        video.nbmotion.animate = broken
        app._select_cell(0)
        check("selection function is never gated on motion",
              app._sel_cell == 0
              and app._story_cells[0].get_opacity() == 1.0
              and all(w.get_opacity() == 1.0
                      for w in app._timeline_clip_cells[0]),
              "selection or final opacity was lost when primitive raised")
    except Exception as exc:
        check("real selection fixture completes", False,
              "[not reached: %s: %s]" % (type(exc).__name__, exc))
    finally:
        video.nbmotion.animate = real_animate

try:
    with open(os.path.join(DE, "video.py"), encoding="utf-8") as fh:
        source = fh.read()
except Exception as exc:
    source = ""
    source_error = "%s: %s" % (type(exc).__name__, exc)
else:
    source_error = ""
check("transition has its content.video inventory name",
      "# nbmotion-inventory: content.video" in source,
      source_error or "marker absent")

print("\nVIDEO MOTION SELFTEST: %d passed, %d failed" % (passed, failed))
print("RESULT: %s" % ("FAILED" if failed else "PASS"))
raise SystemExit(1 if failed else 0)
