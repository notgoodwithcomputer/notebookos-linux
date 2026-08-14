#!/usr/bin/env python3
"""Media motion contract, including diff-only strip arrivals.

MEDIA_MODULE_DIR selects both the imported media.py and the source inspected by
the static checks, so scratch mutants exercise the exact same contract.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
DE = Path(os.environ.get("MEDIA_MODULE_DIR", str(DEFAULT_DE))).resolve()
sys.path.insert(0, str(DE))

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ((" — " + detail) if detail else ""))


try:
    import media
except Exception as exc:
    media = None
    check("media module imports", False, repr(exc))

source_path = DE / "media.py"
try:
    source = source_path.read_text(encoding="utf-8")
except Exception as exc:
    source = ""
    check("selected media source is readable", False, repr(exc))


class Widget:
    def __init__(self, visible=True):
        self.visible = visible
        self.opacity = 1.0

    def show(self): self.visible = True
    def hide(self): self.visible = False
    def set_visible(self, value): self.visible = bool(value)
    def get_visible(self): return self.visible
    def set_opacity(self, value): self.opacity = float(value)
    def get_opacity(self): return self.opacity
    def set_label(self, _value): pass
    def set_tooltip_text(self, _value): pass


def runtime_checks():
    if media is None:
        check("surface fixture reaches transition path", False, "import failed")
        check("fullscreen fixture reaches transition path", False, "import failed")
        check("strip fixture reaches rebuild path", False, "import failed")
        return

    calls = []
    real_fade = media.nbmotion.fade_to

    def fade(widget, target, duration, easing, on_done=None):
        calls.append((widget, target, duration, easing))
        widget.set_opacity(target)
        if on_done is not None:
            on_done(True)

    media.nbmotion.fade_to = fade
    try:
        v = media.MediaViewer.__new__(media.MediaViewer)
        v._empty, v._scroll = Widget(True), Widget(False)
        v._video, v._notice = Widget(False), Widget(False)
        v._stage = Widget(True)
        v._surface_name, v._surface_gen, v._vfull = "empty", 0, False
        v._show_surface("image")
        check("surface fixture reaches transition path",
              len(calls) == 2 and v._scroll.visible and not v._empty.visible,
              "fade_calls=%d" % len(calls))
        check("surface swap uses PAGE depart/arrive easing",
              len(calls) == 2
              and calls[0][2:] == (media.nbmotion.PAGE, media.nbmotion.EASE_IN)
              and calls[1][2:] == (media.nbmotion.PAGE, media.nbmotion.EASE_OUT))

        calls.clear()
        v = media.MediaViewer.__new__(media.MediaViewer)
        v._video = Widget(True)
        v._toolbar_w, v._info_w, v._film_w = Widget(), Widget(), Widget()
        v._menubar, v._vctl, v._v_full_btn = Widget(), Widget(), Widget()
        v._vfull, v._vctl_hide_timer = False, None
        v._menubar_widget = lambda: v._menubar
        v._hide_panel = lambda _hide: None
        v._enter_video_fullscreen()
        v._exit_video_fullscreen()
        out_calls = [c for c in calls if c[1] == 0.0]
        in_calls = [c for c in calls if c[1] == 1.0]
        check("fullscreen fixture reaches transition path",
              len(out_calls) == 4 and len(in_calls) == 4,
              "out=%d in=%d" % (len(out_calls), len(in_calls)))
        check("fullscreen uses surface tokens and directional easing",
              all(c[2:] == (media.nbmotion.SURFACE_OUT, media.nbmotion.DEPART)
                  for c in out_calls)
              and all(c[2:] == (media.nbmotion.SURFACE_IN, media.nbmotion.EASE_OUT)
                      for c in in_calls))
    finally:
        media.nbmotion.fade_to = real_fade

    # This source assertion is deliberately identity-specific. The red proof
    # changes it to set(entries), representing the flash where every old row is
    # restaged, and must fail by this check's name rather than crash.
    check("strip computes genuinely new stable paths only",
          "set(entries) - previous if animate_new else set()" in source)
    check("timer slideshow explicitly disables strip motion",
          "user_caused=False" in source and "_rebuild_strip(False)" in source)
    check("new strip cells use guarded shared Revealers",
          "Gtk.Revealer()" in source and "nbtransitions.reveal(" in source
          and "rev.set_reveal_child(True)" in source)

    # BEHAVIOURAL, because the three checks above are text and text cannot see
    # a transition neutered by its ARGUMENTS. Proven: setting the strip reveal
    # to direction=NONE, duration=0 — which disables the motion completely —
    # left every source assertion above still matching, and the suite passed
    # 11/11. What the strip is HANDED has to be measured, not read.
    reveals = []
    real_reveal = media.nbtransitions.reveal

    def record_reveal(revealer, revealed=True, direction=None, duration=None,
                      **kw):
        reveals.append((direction, duration))
        return real_reveal(revealer, revealed, direction=direction,
                           duration=duration, **kw)

    media.nbtransitions.reveal = record_reveal
    try:
        v = media.MediaViewer.__new__(media.MediaViewer)
        v._strip_sig = ("/a.png",)               # one image already on screen
        v._strip_entries = lambda: ["/a.png", "/b.png"]   # a second arrives
        class Row:
            """The strip container: enough of Gtk.Box for a real rebuild."""
            def __init__(self): self.kids = []
            def get_children(self): return list(self.kids)
            def remove(self, c):
                if c in self.kids:
                    self.kids.remove(c)
            def pack_start(self, c, *_a): self.kids.append(c)
            def show_all(self): pass

        v._strip_row = Row()
        v._strip_btns, v._strip_imgs = {}, {}
        v._media_path = "/a.png"
        v._highlight_strip = lambda: None
        v._scroll_strip_to = lambda _p: None
        v._cancel_thumbs = lambda: None
        v._thumb_cache = {}
        v._strip_empty, v._strip_scroll = Widget(False), Widget(True)
        # A REAL Gtk widget, not the Widget stub: Gtk.Revealer.add() rejects a
        # plain object, and the app catches that and packs the cell plainly —
        # so a fake cell silently routes the fixture down the NO-ANIMATION
        # fallback and every reveal assertion below measures nothing.
        from gi.repository import Gtk as _Gtk
        v._thumb_cell = lambda *a, **k: _Gtk.Box()
        v._strip_cell = lambda *a, **k: _Gtk.Box()
        v._queue_thumb = lambda *a, **k: None
        try:
            v._rebuild_strip(True)
        except Exception as exc:                              # noqa: BLE001
            check("strip fixture drives the real rebuild", False,
                  "[not reached: %s]" % exc)
        else:
            check("strip fixture drives the real rebuild", bool(reveals),
                  "reveal_calls=%d" % len(reveals))
            check("an arriving thumbnail is handed a REAL direction",
                  bool(reveals)
                  and reveals[0][0] not in (None, media.nbtransitions.NONE))
            check("...and a real surface-arriving duration",
                  bool(reveals) and reveals[0][1] not in (None, 0))
    finally:
        media.nbtransitions.reveal = real_reveal


runtime_checks()
check("fullscreen transition is named in the inventory",
      "# nbmotion-inventory: content.media" in source)
check("runtime imports shared motion primitives",
      "import nbmotion" in source and "import nbtransitions" in source)
check("rapid fullscreen reversal drops stale hide completion",
      "_media_fs_gen" in source and
      "getattr(widget, \"_media_fs_gen\", gen) != gen" in source)
check("fullscreen fallback does not gate visibility on opacity support",
      source.find("widget.show()", source.find("def _show_fullscreen_chrome")) >= 0)

passed = sum(results)
print("\nMEDIA MOTION SELFTEST: %d/%d checks passed" % (passed, len(results)))
sys.exit(0 if results and all(results) else 1)
