#!/usr/bin/env python3
"""
uishot — render Notebook OS UI to a PNG on the host, faithfully and without
popping a visible window.

The desktop can't be screenshotted reliably inside the guest (TCG software paint
is unreliable, there is no KVM here). But every app is plain GTK3 + Python
against the overlay's de/, and the host has a real X server, so we can render any
widget tree OFFSCREEN under the SAME theme and fonts the guest uses and save the
result as a PNG to actually look at.

Faithful means:
  * the Papertone GTK theme (usr/share/themes/Papertone/gtk-3.0/gtk.css) is
    loaded at THEME priority, exactly as the guest loads it — so base widget
    chrome (buttons, entries, SCROLLBARS, switches) matches;
  * the guest fonts resolve, if the caller points FONTCONFIG_FILE at a conf
    whose <dir> is the target font tree (see the font selftests);
  * Gtk.OffscreenWindow renders the tree to a pixbuf with no on-screen window,
    so running this against a live :0 disturbs nothing.

Library use:
    import uishot
    uishot.load_theme()                      # once
    uishot.shot(build_widget, 480, 320, "out.png", app_css=SOME_APP_CSS)

`build_widget` is a zero-arg callable returning the Gtk.Widget to render.
`app_css` (optional bytes) is loaded at APPLICATION priority, mimicking an app's
own install_css() layered over the theme.

CLI smoke test:
    DISPLAY=:0 python3 uishot.py            # renders a scrollbar demo to /tmp
"""
import os
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

_OVERLAY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "buildroot", "board", "notebookos", "rootfs-overlay")
THEME_CSS = os.path.join(_OVERLAY, "usr", "share", "themes",
                         "Papertone", "gtk-3.0", "gtk.css")

# Between THEME (200) and APPLICATION (600): high enough to beat the HOST's own
# GTK theme (which sits at THEME priority and would otherwise colour our render
# with the developer desktop's blue), low enough that an app's own install_css
# at APPLICATION priority still layers on top exactly as it does on the guest.
_THEME_PRIORITY = 500
_theme_loaded = False


def load_theme(path=THEME_CSS):
    """Load Papertone so it decisively wins over the host's GTK theme.

    On the guest, Papertone is THE theme and nothing competes; on this host the
    developer's own theme is already loaded at THEME priority, so loading
    Papertone at the same priority renders the host's scrollbars (blue, with
    steppers) instead of ours. Loading it just under APPLICATION priority makes
    the render faithful to the guest while leaving room for app CSS on top."""
    global _theme_loaded
    prov = Gtk.CssProvider()
    prov.load_from_path(path)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, _THEME_PRIORITY)
    _theme_loaded = True
    return prov


def shot(build, w, h, path, app_css=None, settle=60):
    """Render build() at w x h to `path` (PNG). Returns the saved size."""
    if not _theme_loaded:
        load_theme()
    if app_css is not None:
        prov = Gtk.CssProvider()
        prov.load_from_data(app_css if isinstance(app_css, bytes)
                            else app_css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    widget = build()
    off = Gtk.OffscreenWindow()
    widget.set_size_request(w, h)
    off.add(widget)
    off.show_all()
    ctx = GLib.MainContext.default()
    for _ in range(settle):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        ctx.iteration(False)
    pb = off.get_pixbuf()
    if pb is None:
        raise RuntimeError("get_pixbuf() returned None for " + path)
    pb.savev(path, "png", [], [])
    return pb.get_width(), pb.get_height()


def shot_window(win, w, h, path, settle=80, after_show=None):
    """Render a fully-constructed toplevel (an nbapp app, the Finder, ...) to a
    PNG without ever mapping it on screen.

    An app builds all its chrome into a single child of its Gtk.Window (the
    menu-bar + content overlay for nbapp apps; the frame overlay for the
    Finder). We lift that child out of the never-shown window and into a
    Gtk.OffscreenWindow, so the render is the app's REAL UI — its own CSS
    (registered at construction via install_css) layered over Papertone — at a
    chosen size. The app's window is left childless and can be destroyed after.
    """
    if not _theme_loaded:
        load_theme()
    child = win.get_child()
    if child is None:
        raise RuntimeError("window has no child to render")
    win.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_size_request(w, h)
    # The real app window paints a paper base (Papertone `window {background}`
    # plus force_opaque_visual), so any pixel a widget leaves untouched shows
    # paper on the guest. A bare OffscreenWindow has no such base and renders
    # those pixels transparent, which saves as BLACK in the PNG and masquerades
    # as a "black background" bug. Back the child with a paper EventBox so the
    # render matches what the guest window provides — a REAL black-painting
    # container still paints black over this and shows up honestly.
    bg = Gtk.EventBox()
    _prov = Gtk.CssProvider()
    _prov.load_from_data(b"* { background-color: #FCFBF8; }")
    bg.get_style_context().add_provider(_prov, _THEME_PRIORITY - 1)
    # Carry the WINDOW's style classes onto the holder. Lifting the child out of
    # its toplevel drops them, and every rule an app scopes under its window
    # class -- `.finder .x`, `.nbapp .y`, `.nbpicker .z` -- then silently stops
    # matching. The render still looks plausible, which is worse than failing:
    # it invents clipped bars, wrong padding and stray borders that do not exist
    # on the guest, and people go and "fix" them.
    _wctx = win.get_style_context()
    for _cls in _wctx.list_classes():
        bg.get_style_context().add_class(_cls)
    bg.add(child)
    off.add(bg)
    off.show_all()
    # Some widgets only honour state changes once shown — e.g. Gtk.Stack ignores
    # set_visible_child_name until its pages are visible, so an editor selected
    # before show_all reverts to the first page. after_show runs here, post
    # show_all, to re-assert such state (the guest hits this naturally because
    # its window is already shown when the user acts).
    if after_show is not None:
        after_show(win)
    ctx = GLib.MainContext.default()
    for _ in range(settle):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        ctx.iteration(False)
    pb = off.get_pixbuf()
    if pb is None:
        raise RuntimeError("get_pixbuf() returned None for " + path)
    pb.savev(path, "png", [], [])
    return pb.get_width(), pb.get_height()


def _demo():
    out = os.environ.get("UISHOT_OUT", "/tmp/uishot-demo.png")
    load_theme()

    def build():
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i in range(40):
            box.pack_start(Gtk.Label(label="Line %d — papertone content" % i),
                           False, False, 4)
        sw.add(box)
        outer = Gtk.Box()
        outer.pack_start(sw, True, True, 0)
        return outer

    print("theme:", THEME_CSS)
    print("saved:", shot(build, 360, 240, out), "->", out)


if __name__ == "__main__":
    _demo()
