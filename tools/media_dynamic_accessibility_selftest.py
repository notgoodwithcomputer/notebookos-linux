#!/usr/bin/env python3
"""Headless semantic-name checks for Media's changing transport actions."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import media  # noqa: E402


class Context:
    def add_class(self, _name): pass
    def remove_class(self, _name): pass


class Accessible:
    def __init__(self): self.name = None; self.description = None
    def set_name(self, name): self.name = name
    def set_description(self, text): self.description = text


class Button:
    def __init__(self): self.tip = None; self.acc = Accessible()
    def set_tooltip_text(self, text): self.tip = text
    def get_accessible(self): return self.acc
    def get_style_context(self): return Context()


class Scale(Button):
    def set_value(self, value): self.value = value


class Probe:
    _set_video_glyph = media.MediaViewer._set_video_glyph
    _set_slide_active = media.MediaViewer._set_slide_active
    _set_seek = media.MediaViewer._set_seek
    _update_seek_accessible = media.MediaViewer._update_seek_accessible
    _fmt_ns = staticmethod(media.MediaViewer._fmt_ns)
    def __init__(self):
        self._v_play = Button(); self._v_play_img = object()
        self._btn = {"play": Button()}; self._btn_img = {}
        self._v_seek = Scale(); self._v_duration_ns = 120 * 1000000000


app = Probe()
real_icon = media.nbicons.set_image
media.nbicons.set_image = lambda *_a, **_k: None
try:
    app._set_video_glyph("pause")
    pause = app._v_play.tip == app._v_play.acc.name == media._t("Pause")
    app._set_video_glyph("play")
    play = app._v_play.tip == app._v_play.acc.name == media._t("Play")
    app._set_slide_active(True)
    stop = (app._btn["play"].tip == app._btn["play"].acc.name
            == media._t("Stop slideshow"))
    app._set_slide_active(False)
    start = (app._btn["play"].tip == app._btn["play"].acc.name
             == media._t("Start slideshow"))
    app._set_seek(500)
    seek = (app._v_seek.value == 500
            and app._v_seek.acc.description == "1:00 / 2:00")
finally:
    media.nbicons.set_image = real_icon

for ok, name in ((pause, "playing video exposes Pause"),
                 (play, "paused video exposes Play"),
                 (stop, "running slideshow exposes Stop"),
                 (start, "idle slideshow exposes Start"),
                 (seek, "video seek exposes current and total time")):
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all((pause, play, stop, start, seek)) else "FAILED"))
raise SystemExit(not all((pause, play, stop, start, seek)))
