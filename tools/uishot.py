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


def _save_offscreen(off, path):
    """Write an OffscreenWindow's contents to `path` at FULL DEVICE RESOLUTION.

    WHY NOT get_pixbuf(). Under GDK_SCALE=2 GTK really does render the widget
    tree at twice the pixels -- measured, the offscreen surface for a 200x100
    logical window is 400x200 with device_scale (2.0, 2.0). But
    Gtk.OffscreenWindow.get_pixbuf() hands back a LOGICAL-size pixbuf, so every
    2x screenshot this harness produced was the 2x render thrown away and
    resampled back down to 1x.

    That is the worst possible failure for a fidelity tool: the HiDPI work was
    verified structurally (surface sizes and device scales measured directly),
    but any attempt to verify it BY LOOKING was quietly comparing two 1x images
    and would have shown no difference no matter how broken the real 2x path
    was. Taking the surface directly keeps the pixels GTK actually drew.

    Returns (width, height) of what was written, in real pixels."""
    surf = None
    try:
        surf = off.get_surface()
    except Exception:                                             # noqa: BLE001
        surf = None
    if surf is not None:
        try:
            sx, _sy = surf.get_device_scale()
        except Exception:                                         # noqa: BLE001
            sx = 1
        # Only take the surface path when there is something extra to keep; at
        # 1x the pixbuf route is equivalent and better tested.
        if sx and sx > 1:
            surf.flush()
            surf.write_to_png(path)
            return surf.get_width(), surf.get_height()
    # 1x fallback. NOTE: this block is the ORIGINAL capture code, and a bulk
    # replace of it across this file rewrote it here too -- turning this
    # function's own fallback into a call to itself. Infinite recursion, caught
    # only because the edit was read back. Leave it written out.
    pb = off.get_pixbuf()
    if pb is None:
        raise RuntimeError("get_pixbuf() returned None for " + path)
    pb.savev(path, "png", [], [])
    return pb.get_width(), pb.get_height()


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
    return _save_offscreen(off, path)


class _PanelClamp(Gtk.Bin):
    """Hold the app to an EXACT panel box, the way the hardware frame does.

    Gtk.OffscreenWindow sizes itself to its child's NATURAL request, and
    set_size_request is only a MINIMUM -- so an app whose content is wider or
    taller than the panel renders at that larger size and the PNG shows a
    layout the hardware can never display. app-improve caught bills rendering
    at its 1172px developer-monitor width inside a nominal 1024 shot
    (2026-08-09), which had quietly invalidated every design-fidelity and
    eyeball review of any app wider than the panel. This container reports the
    panel box as BOTH its minimum and its natural size and hands the child
    exactly that box, so content past the panel edge is clipped by the
    offscreen surface -- the honest "this does not fit" picture instead of an
    invented one. It does NOT rebuild the child: an app that read screen_size at
    construct must have been built under the matching pin (appshot / uishot_all
    do this); the clamp then simply stops the frame from growing past the panel.
    """
    def __init__(self, w, h):
        super().__init__()
        self._w, self._h = w, h

    def do_get_preferred_width(self):
        return (self._w, self._w)

    def do_get_preferred_height(self):
        return (self._h, self._h)

    def do_get_preferred_width_for_height(self, _h):
        return (self._w, self._w)

    def do_get_preferred_height_for_width(self, _w):
        return (self._h, self._h)

    def do_size_allocate(self, alloc):
        self.set_allocation(alloc)
        child = self.get_child()
        if child is not None:
            child.size_allocate(alloc)


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
    # Hold the whole tree to the panel box so an app wider/taller than the
    # panel renders CLIPPED at the panel edge, not at its own natural size.
    clamp = _PanelClamp(w, h)
    clamp.add(bg)
    off.add(clamp)
    # show_all() UN-HIDES what an app deliberately hid, and several apps hide
    # AFTER their own show_all() — Disc Burner ends __init__ with
    # `self.show_all(); self.prog.hide(); self.stop_btn.hide()`. Rendering it
    # here therefore drew a live Stop button and a progress bar over an idle
    # app, and the picture invited a bug report about a control that is
    # correctly hidden on the guest. The render has to show what the app left
    # on screen, so: if the app already showed itself, remember which widgets
    # it had hidden and put them back after show_all. An app that never called
    # show_all has nothing to preserve and is shown as before.
    hidden = []
    if child.get_visible():
        def _walk(wgt):
            if not wgt.get_visible():
                hidden.append(wgt)
            if isinstance(wgt, Gtk.Container):
                for ch in wgt.get_children():
                    _walk(ch)
        _walk(child)
    off.show_all()
    for wgt in hidden:
        wgt.hide()
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
    return _save_offscreen(off, path)


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
