#!/usr/bin/env python3
"""Media Viewer: leaving the video stage must leave video-fullscreen.

Display-free. The window is NOT constructed (that needs an X server and the
whole widget tree); the real MediaViewer._show_surface is driven on a bare
instance whose four stage surfaces and chrome are stand-ins that only record
what was asked of them. That is enough, because the defect is in the surface
switch itself: _show_surface used to swap the stage without touching _vfull,
so opening a picture with Ctrl+O mid-film — or a clip that failed to decode,
or trashing the last file — left the menu bar, the toolbar and the filmstrip
hidden and the desktop panel stood down, on a surface with no Fullscreen
button to press to get any of them back.

  python3 tools/media_selftest.py     ->  exit 0 on ALL PASS
"""
import os
import sys

# no X server, and no shared NB_HOME (nbapp's single-instance marker is keyed
# off it; the copy that loses that race is _exit(0)ed -- a silent false pass)
os.environ["DISPLAY"] = ""
os.environ["NB_HOME"] = "/tmp/nbhome-mediaself-%d" % os.getpid()
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import media  # noqa: E402

R = []


def chk(name, ok, detail=""):
    R.append(ok)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "  <- %s" % (detail,)))


class FakeWidget(object):
    """Records show/hide the way a Gtk.Widget answers them."""

    def __init__(self, visible=True):
        self.visible = visible

    def set_visible(self, on):
        self.visible = bool(on)

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def get_visible(self):
        return self.visible

    def set_label(self, _t):
        pass

    def set_tooltip_text(self, _t):
        pass


def viewer():
    """A MediaViewer with the pieces _show_surface / _exit_video_fullscreen
    touch, and nothing else — no Gtk.Window, so no display is needed."""
    v = media.MediaViewer.__new__(media.MediaViewer)
    v._empty = FakeWidget()
    v._scroll = FakeWidget()
    v._video = FakeWidget()
    v._notice = FakeWidget()
    v._toolbar_w = FakeWidget()
    v._info_w = FakeWidget()
    v._film_w = FakeWidget()
    v._menubar = FakeWidget()
    v._vctl = FakeWidget()
    v._v_full_btn = FakeWidget()
    v._vfull = False
    v._vctl_hide_timer = None
    v.panel_hidden = None
    v._menubar_widget = lambda: v._menubar
    v._hide_panel = lambda hide: setattr(v, "panel_hidden", bool(hide))
    return v


def in_fullscreen():
    """A viewer mid-film with the chrome collapsed, as _enter_video_fullscreen
    leaves it."""
    v = viewer()
    v._video.show()
    v._enter_video_fullscreen()
    return v


v = in_fullscreen()
chk("entering video fullscreen hides the chrome",
    v._vfull and not v._toolbar_w.visible and not v._menubar.visible
    and not v._film_w.visible and v.panel_hidden is True,
    "vfull=%r toolbar=%r menubar=%r film=%r panel_hidden=%r"
    % (v._vfull, v._toolbar_w.visible, v._menubar.visible,
       v._film_w.visible, v.panel_hidden))

# THE REGRESSION: Ctrl+O opens a picture while a film is fullscreen.
v = in_fullscreen()
v._show_surface("image")
chk("opening an image leaves video fullscreen", not v._vfull, "still fullscreen")
chk("...and the toolbar comes back", v._toolbar_w.visible, "toolbar still hidden")
chk("...and the menu bar comes back", v._menubar.visible, "menubar still hidden")
chk("...and the filmstrip comes back", v._film_w.visible, "filmstrip still hidden")
chk("...and the desktop panel stands up again", v.panel_hidden is False,
    "panel still hidden (flag file left behind)")

# a clip that cannot be decoded swaps the stage for the neutral note
v = in_fullscreen()
v._show_surface("notice")
chk("a video that fails to play leaves fullscreen too",
    not v._vfull and v._toolbar_w.visible and v.panel_hidden is False,
    "vfull=%r toolbar=%r panel_hidden=%r"
    % (v._vfull, v._toolbar_w.visible, v.panel_hidden))

# trashing the last file in the folder empties the stage
v = in_fullscreen()
v._show_surface("empty")
chk("an emptied stage leaves fullscreen too",
    not v._vfull and v._menubar.visible and v.panel_hidden is False,
    "vfull=%r menubar=%r panel_hidden=%r"
    % (v._vfull, v._menubar.visible, v.panel_hidden))

# staying on the video stage must NOT drop out of fullscreen: the poll and the
# transport re-show the same surface while a film runs edge to edge.
v = in_fullscreen()
v._show_surface("video")
chk("re-showing the video stage stays fullscreen",
    v._vfull and not v._toolbar_w.visible and v.panel_hidden is True,
    "dropped out of fullscreen")

# and the surface switch itself still does its own job
v = viewer()
v._show_surface("image")
chk("the stage still shows exactly one surface",
    v._scroll.visible and not v._empty.visible and not v._video.visible
    and not v._notice.visible,
    "empty=%r image=%r video=%r notice=%r"
    % (v._empty.visible, v._scroll.visible, v._video.visible,
       v._notice.visible))

print("\nRESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
