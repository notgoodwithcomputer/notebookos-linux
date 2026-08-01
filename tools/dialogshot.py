#!/usr/bin/env python3
"""
dialogshot — render an app's REAL Gtk.Dialog offscreen, without mapping it.

Dialogs were the one user-facing surface uishot could not reach. A Gtk.Dialog
is a toplevel, so it cannot be placed inside a Gtk.OffscreenWindow, and simply
calling the app's method pops the dialog onto the live :0 desktop and then
BLOCKS in dlg.run() forever. So the OS's rename/confirm/save/error dialogs were
never actually looked at — an earlier pass concluded they were "coherent by
construction" from reading the code, and that conclusion turned out to rest on
a class (.nbdialog) that only one app defines.

The trick is to intercept at the two points every dialog passes through:

  * Gtk.Dialog.show_all  -> show the dialog's CHILD instead of the dialog, so
    every widget inside is realised and visible but the toplevel never maps
    (nothing flashes on the developer's screen, and a live desktop is safe);
  * Gtk.Dialog.run       -> the dialog is fully built by the time run() is
    called, so lift its child into an OffscreenWindow, snapshot it, put it
    back, and return a response so the caller unblocks and tears down.

What you get is the app's own widget tree under the real Papertone theme and
guest fonts — everything except the window manager's frame.

    import dialogshot
    dialogshot.load_theme()
    dialogshot.capture(lambda: app._choose_name(), "out.png")
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402,F401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uishot  # noqa: E402

load_theme = uishot.load_theme

# Dialogs are sized by their content; these are only the minimum box the
# snapshot is taken in.
DEFAULT_W = 460
DEFAULT_H = 220


def _pump(n=60):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def install_app_css(mod):
    """Load a module's own stylesheet the way its main() would.

    ALWAYS call this before constructing an app for a render. Several modules
    do NOT install their CSS from the window's __init__: finder exposes a
    module-level install_css(), and shell.py loads its CSS inline in main().
    Constructing the window directly therefore renders it UNSTYLED — which
    reads as missing styling that is in fact present. That produced two false
    findings ("the Finder's Delete button has no red", "the Restart button is
    unmarked") before this existed; both classes were defined and correct.

    Returns True if a stylesheet was installed.
    """
    if hasattr(mod, "install_css"):
        try:
            mod.install_css()
            return True
        except Exception:                                       # noqa: BLE001
            pass
    for name in dir(mod):
        if name != "CSS" and not name.endswith("_CSS"):
            continue
        data = getattr(mod, name)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray)):
            continue
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(bytes(data))
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            return True
        except Exception:                                       # noqa: BLE001
            pass
    return False


def capture(open_dialog, out_path, w=DEFAULT_W, h=DEFAULT_H,
            response=Gtk.ResponseType.CANCEL, app_css=None):
    """Call `open_dialog` and save a PNG of whatever dialog it opens.

    Returns the path on success, or None when the call opened no dialog.
    `response` is what run() hands back, so the caller's OK/Cancel branch is
    chosen deterministically (default Cancel: never commits a change).
    """
    if app_css:
        prov = Gtk.CssProvider()
        prov.load_from_data(app_css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    taken = []
    opened = []
    real_run = Gtk.Dialog.run
    real_show_all = Gtk.Dialog.show_all
    real_show = Gtk.Dialog.show
    real_present = Gtk.Dialog.present

    def snapshot(dlg):
        """Lift the dialog's built tree offscreen, save it, hand it back.

        show_all() is the one call BOTH dialog styles in this OS make, and they
        make it last: the blocking kind (build -> show_all -> run) and the
        modeless kind the Finder uses (build -> show_all, responses wired to
        button handlers). So the tree is complete here in either case.
        """
        child = dlg.get_child()
        if child is None or taken:
            return
        dlg.remove(child)
        off = Gtk.OffscreenWindow()
        off.get_style_context().add_class("background")
        off.set_size_request(w, h)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class("nbdialog")
        # Carry the DIALOG's own style classes onto the wrapper. Several apps
        # scope their dialog CSS to a class on the toplevel (`.nbpicker
        # .pickerok`, `.finderinfo`, `.acdlg`), so a wrapper without them
        # renders those dialogs unstyled — which reads as missing styling that
        # is in fact present. That artifact has now produced a false finding
        # three times (the picker's Open button IS signage red; its Places rows
        # ARE flat).
        for cls in dlg.get_style_context().list_classes():
            frame.get_style_context().add_class(cls)
        frame.pack_start(child, True, True, 0)
        off.add(frame)
        off.show_all()
        _pump()
        pb = off.get_pixbuf()
        if pb is not None:
            pb.savev(out_path, "png", [], [])
            taken.append(out_path)
        # give the child back so the caller's destroy() is well-formed
        frame.remove(child)
        off.destroy()
        dlg.add(child)

    def show_all(self):
        # Realise the contents WITHOUT mapping the toplevel, then snapshot.
        child = self.get_child()
        if child is not None:
            child.show_all()
        opened.append(self)
        snapshot(self)

    def noop(self, *_a):
        pass

    def run(self):
        if not taken:
            snapshot(self)
        return response

    Gtk.Dialog.show_all = show_all
    Gtk.Dialog.show = noop
    Gtk.Dialog.present = noop
    Gtk.Dialog.run = run
    try:
        open_dialog()
    finally:
        Gtk.Dialog.run = real_run
        Gtk.Dialog.show_all = real_show_all
        Gtk.Dialog.show = real_show
        Gtk.Dialog.present = real_present
    # A modeless dialog is never destroyed by its caller — it waits for a
    # button. Tear down anything still standing so it cannot leak into the
    # next capture.
    for dlg in opened:
        try:
            dlg.destroy()
        except Exception:                                       # noqa: BLE001
            pass
    _pump(10)
    return taken[0] if taken else None


if __name__ == "__main__":
    # Smoke test: a dialog shaped like the ones the apps build.
    load_theme()
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dialogshot-demo.png"

    def demo():
        dlg = Gtk.Dialog(title="Rename")
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Rename", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(16)
        lbl = Gtk.Label(label="What should this be called?")
        lbl.set_xalign(0.0)
        box.pack_start(lbl, False, False, 6)
        ent = Gtk.Entry()
        ent.set_text("Untitled")
        box.pack_start(ent, False, False, 6)
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    print(capture(demo, out))
