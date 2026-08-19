#!/usr/bin/env python3
"""appdrive — drive a Notebook OS app the way a person does, on the host.

The offscreen renderers (uishot/appshot) take ONE picture of a freshly built
app. A real-use drive-through is a SEQUENCE: launch → new → type/draw → save →
reopen → export → menus → resize → close, looking at the screen after every
step. This module hosts an app's real widget tree in an OffscreenWindow held
at the panel size, keeps it there for the whole session, and gives the driver
three honest instruments:

  * ``pump(seconds)``   — run the real GLib main loop for wall time (idles,
                          timers, nbjobs deliveries all fire);
  * ``shot(path)``      — pixels via a SYNCHRONOUS ``widget.draw()`` after a
                          forced layout pass (an OffscreenWindow never ticks
                          the frame clock, so get_pixbuf() serves ghosts —
                          see the harness memory);
  * ``key(...)`` / ``press(...)`` — events pushed through ``Gtk.main_do_event``
                          so the window's real key ladder / the canvas's real
                          button handlers run, not a handler called by name.

Menus and buttons are activated through their own API (``clicked()``,
``menu_action(label)``) because their input windows are private to GTK.

    tools/guestrun.sh python3 -c '
    import appdrive; d = appdrive.Drive("tasks"); d.shot("/tmp/t0.png")
    d.type("Buy milk"); d.key("Return"); d.pump(0.5); d.shot("/tmp/t1.png")'

Run under tools/guestrun.sh so the theme and fonts are the guest's.
"""
import os
import sys
import time
import inspect
import importlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# NB_DRIVE_DE points the driver at a SCRATCH COPY of the app modules. It exists
# for red-proofs: a check that cannot be run against a sabotaged copy of the
# app cannot show that it goes red, and sabotaging the working tree to find out
# is how a mutation gets left behind in a release tree.
DE = os.environ.get("NB_DRIVE_DE") or os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, DE)

# The host desktop may run ibus/fcitx; a synthesized key never reaches such
# a daemon, so typing would land nowhere. The guest uses GTK's simple IM.
os.environ["GTK_IM_MODULE"] = "gtk-im-context-simple"
import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402
import cairo  # noqa: E402
import uishot  # noqa: E402

PANEL = (1024, 740)      # the smallest panel the OS supports (740 = 768 - 28)


def _prep_home(home):
    """A private NB_HOME so the drive never touches the developer's data, and
    a private single-instance registry so a real app open on this desktop
    cannot make the drive stand down silently (nbapp os._exit(0)s)."""
    os.environ["NB_HOME"] = home
    os.makedirs(home, exist_ok=True)
    import nbapp
    nbapp._APP_DIR = os.path.join(home, "nb-apps")
    nbapp.APP_DIR = nbapp._APP_DIR
    os.makedirs(nbapp._APP_DIR, exist_ok=True)
    return nbapp


def app_class(mod, name=""):
    if name:
        return getattr(mod, name)
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    return None


def pump(seconds=0.0):
    """Drain pending events; if `seconds` > 0, keep iterating for that long so
    timers and idle handlers get their turn (like a person waiting)."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        while ctx.pending():
            ctx.iteration(False)
        if time.monotonic() >= end:
            break
        ctx.iteration(True) if ctx.pending() else time.sleep(0.01)


class Drive:
    """One app, hosted for the whole drive.

    ``Drive("tasks")`` imports de/tasks.py, pins the screen to PANEL, builds
    the app's window class, lifts its tree into an offscreen holder of exactly
    PANEL size, and shows it. ``self.app`` is the real app object; every one of
    its handlers, stores and menus is the real one.
    """

    def __init__(self, modname, cls="", size=PANEL, home=None, setup=None,
                 motion=False):
        self.w, self.h = size
        home = home or os.path.join(
            os.environ.get("NB_DRIVE_HOME_ROOT", "/tmp/nb-drive"), modname)
        self.home = home
        self.nbapp = _prep_home(home)
        uishot.load_theme()
        if not motion:
            # Tweens ride the frame clock, which never ticks in an offscreen
            # holder: a launch fade-in would leave the whole app at opacity 0
            # and every shot blank. Land motion instantly (what Reduced Motion
            # does) unless the caller is studying motion itself.
            import nbmotion
            nbmotion.policy = lambda duration=0, fade=False: 0
        self.nbapp.screen_size = lambda: (self.w, self.h)
        if modname in sys.modules:
            del sys.modules[modname]
        self.mod = importlib.import_module(modname)
        c = app_class(self.mod, cls)
        if c is None:
            raise RuntimeError("no Gtk.Window class in " + modname)
        self.app = c()
        if setup is not None:
            setup(self.app)
        self._host()

    # ---- hosting -----------------------------------------------------------
    def _host(self):
        win = self.app
        child = win.get_child()
        if child is None:
            raise RuntimeError("app window has no child")
        hidden = []
        if child.get_visible():
            def _walk(wgt):
                if not wgt.get_visible():
                    hidden.append(wgt)
                if isinstance(wgt, Gtk.Container):
                    for ch in wgt.get_children():
                        _walk(ch)
            _walk(child)
        win.remove(child)
        off = Gtk.OffscreenWindow()
        off.set_size_request(self.w, self.h)
        bg = Gtk.EventBox()
        prov = Gtk.CssProvider()
        prov.load_from_data(b"* { background-color: #FCFBF8; }")
        bg.get_style_context().add_provider(prov, uishot._THEME_PRIORITY - 1)
        for cls in win.get_style_context().list_classes():
            bg.get_style_context().add_class(cls)
        bg.add(child)
        clamp = uishot._PanelClamp(self.w, self.h)
        clamp.add(bg)
        off.add(clamp)
        off.show_all()
        for wgt in hidden:
            wgt.hide()
        self.off, self.child, self.bg = off, child, bg
        # The tree now lives in `off`, so GTK's focus bookkeeping is off's.
        # The app's own code asks self.get_focus()/set_focus(); route those
        # to where the widgets actually are, so a handler that consults focus
        # sees the truth. (Instance attributes shadow the C methods.)
        win.get_focus = off.get_focus
        win.set_focus = off.set_focus
        # The app window's CLASS key handler (bindings: space -> activate-focus,
        # Return -> activate-default) would eat keys the ladder let through,
        # because the window itself now has no focus widget. On the guest those
        # bindings run only AFTER the focus widget declined the key. Stop the
        # emission after the app's own connected handlers so the key can go on
        # to the real focus widget in the holder (see _deliver_key).
        self._fell_through = False

        def _sentinel(w, ev, sig):
            self._fell_through = True
            w.stop_emission_by_name(sig)
            return False
        win.connect("key-press-event", _sentinel, "key-press-event")
        win.connect("key-release-event", _sentinel, "key-release-event")
        # The app object still routes keys through ITS window's handlers, and
        # its own get_focus() is what its code consults. Keep the app window
        # realised (unmapped) so event.window has something real to be.
        try:
            win.realize()
        except Exception:                                         # noqa: BLE001
            pass
        pump(0.2)
        off.check_resize()
        pump(0.1)

    def resize(self, w, h):
        """Change the panel box mid-drive (a resize a user could do)."""
        self.w, self.h = w, h
        self.nbapp.screen_size = lambda: (self.w, self.h)
        clamp = self.off.get_child()
        clamp._w, clamp._h = w, h
        self.off.set_size_request(w, h)
        clamp.queue_resize()
        pump(0.1)
        self.off.check_resize()
        pump(0.1)

    # ---- instruments -------------------------------------------------------
    def pump(self, seconds=0.0):
        pump(seconds)
        try:
            self.off.check_resize()
        except Exception:                                         # noqa: BLE001
            pass

    def shot(self, path, note=""):
        """Synchronous render of the whole app to a PNG; returns the path."""
        self.pump(0.05)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        cr = cairo.Context(surf)
        cr.set_source_rgb(0xFC / 255, 0xFB / 255, 0xF8 / 255)
        cr.paint()
        self.off.get_child().draw(cr)
        surf.flush()
        surf.write_to_png(path)
        if note:
            print("shot %-40s %s" % (os.path.basename(path), note))
        return path

    def focus(self):
        return self.app.get_focus()

    # ---- input -------------------------------------------------------------
    def key(self, name, ctrl=False, shift=False, alt=False, string=None):
        """A real key press+release through the window's key ladder.

        `name` is a Gdk key name ("Return", "Escape", "a", "Delete", "F2")."""
        if isinstance(name, int):
            kv = name                       # a raw keyval (CJK etc.)
        else:
            kv = Gdk.keyval_from_name(name)
            if (kv == 0 or kv == Gdk.KEY_VoidSymbol) and len(name) == 1:
                kv = Gdk.unicode_to_keyval(ord(name))
            if kv == 0 or kv == Gdk.KEY_VoidSymbol:
                raise ValueError("unknown key name %r" % name)
        state = Gdk.ModifierType(0)
        if ctrl:
            state |= Gdk.ModifierType.CONTROL_MASK
        if shift:
            state |= Gdk.ModifierType.SHIFT_MASK
        if alt:
            state |= Gdk.ModifierType.MOD1_MASK
        for et in (Gdk.EventType.KEY_PRESS, Gdk.EventType.KEY_RELEASE):
            ev = Gdk.Event.new(et)
            ev.keyval = kv
            ev.state = state
            ev.time = Gtk.get_current_event_time() or 0
            try:
                seat = Gdk.Display.get_default().get_default_seat()
                ev.set_device(seat.get_keyboard())
                km = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
                ok, keys = km.get_entries_for_keyval(kv)
                ev.hardware_keycode = keys[0].keycode if ok and keys else 0
            except Exception:                                     # noqa: BLE001
                ev.hardware_keycode = 0
            if string is None:
                u = Gdk.keyval_to_unicode(kv)
                ev.string = chr(u) if u else ""
            else:
                ev.string = string
            ev.window = self.app.get_window()
            self._deliver_key(ev)
        pump()

    def _deliver_key(self, ev):
        """Route the way GTK does for a toplevel: the window's key-press
        signal (the app's ladder), then the focus widget and its ancestors."""
        sig = ("key-press-event" if ev.type == Gdk.EventType.KEY_PRESS
               else "key-release-event")
        # 1) the app window's ladder (nbpinyin, nbdiacritics, the app's own
        #    _on_key) — exactly the handlers a real key meets first;
        self._fell_through = False
        handled = self.app.emit(sig, ev)
        if handled and not self._fell_through:
            return True
        # 2) then what gtk_window_propagate_key_event does: the focus widget,
        #    then each ancestor up to (not including) the toplevel. The
        #    holder is unmapped-inactive, so its own default handler is not
        #    trusted to do this for us.
        w = self.off.get_focus()
        while w is not None and w is not self.off and w is not self.bg:
            if w.get_sensitive() and w.get_realized():
                if w.emit(sig, ev):
                    return True
            w = w.get_parent()
        return False

    def type(self, text, gap=0.03):
        """Type text a character at a time into whatever has focus, with a
        human-speed gap between keys. Zero gap is NOT a real person: the
        press-and-hold accent palette (nbdiacritics) rightly reads a re-press
        of the same key within 20ms as X autorepeat, so "Call" typed with no
        gap became "Cał" — a harness artefact, not an app defect."""
        for ch in text:
            if gap:
                pump(gap)
            if ch == "\n":
                self.key("Return")
                continue
            kv = Gdk.unicode_to_keyval(ord(ch))
            shift = ch.isalpha() and ch.isupper()
            # pass the keyval itself: Gdk.keyval_name gives "U+65E5" for a
            # CJK character and keyval_from_name does not read that back
            self.key(kv, shift=shift, string=ch)

    def press(self, widget, x, y, button=1, double=False, release=True):
        """A real button press (and release) at (x, y) inside `widget`, which
        must own its own GdkWindow (a DrawingArea, EventBox, TreeView...)."""
        gw = self._event_window(widget)
        types = [Gdk.EventType.BUTTON_PRESS]
        if double:
            types = [Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_PRESS,
                     Gdk.EventType._2BUTTON_PRESS]
        for et in types:
            ev = Gdk.Event.new(et)
            ev.window = gw
            ev.x, ev.y = float(x), float(y)
            ev.button = button
            ev.state = Gdk.ModifierType(0)
            ev.time = 0
            ev.send_event = True
            widget.emit("button-press-event", ev)
            pump()
        if release:
            ev = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
            ev.window = gw
            ev.x, ev.y = float(x), float(y)
            ev.button = button
            ev.state = Gdk.ModifierType.BUTTON1_MASK
            ev.time = 0
            widget.emit("button-release-event", ev)
            pump()

    @staticmethod
    def _event_window(widget):
        """The GdkWindow a pointer event on `widget` belongs to: a TextView's
        text window, otherwise the widget's own."""
        gw = None
        if isinstance(widget, Gtk.TextView):
            gw = widget.get_window(Gtk.TextWindowType.TEXT)
        if gw is None:
            gw = widget.get_window()
        if gw is None:
            raise RuntimeError("widget has no GdkWindow to press into")
        return gw

    def drag(self, widget, points, button=1):
        """Press at points[0], motion through the rest, release at the end."""
        gw = self._event_window(widget)
        x0, y0 = points[0]
        ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        ev.window, ev.x, ev.y, ev.button = gw, float(x0), float(y0), button
        ev.state = Gdk.ModifierType(0)
        widget.emit("button-press-event", ev)
        pump()
        for (x, y) in points[1:]:
            mv = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
            mv.window, mv.x, mv.y = gw, float(x), float(y)
            mv.state = Gdk.ModifierType.BUTTON1_MASK
            widget.emit("motion-notify-event", mv)
            pump()
        xe, ye = points[-1]
        rl = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        rl.window, rl.x, rl.y, rl.button = gw, float(xe), float(ye), button
        rl.state = Gdk.ModifierType.BUTTON1_MASK
        widget.emit("button-release-event", rl)
        pump()

    # ---- widget lookup -----------------------------------------------------
    def walk(self, root=None):
        root = root or self.child
        out = [root]
        i = 0
        while i < len(out):
            w = out[i]
            i += 1
            if isinstance(w, Gtk.Container):
                out.extend(w.get_children())
        return out

    def find(self, kind=None, label=None, name=None, visible=True):
        """Widgets matching a Gtk class, a label text and/or a widget name."""
        hits = []
        for w in self.walk():
            if kind is not None and not isinstance(w, kind):
                continue
            if name is not None and w.get_name() != name:
                continue
            if visible and not w.get_visible():
                continue
            if label is not None:
                txt = None
                if hasattr(w, "get_label"):
                    try:
                        txt = w.get_label()
                    except Exception:                             # noqa: BLE001
                        txt = None
                if txt is None and hasattr(w, "get_text"):
                    try:
                        txt = w.get_text()
                    except Exception:                             # noqa: BLE001
                        txt = None
                if txt is None and isinstance(w, Gtk.Bin):
                    c = w.get_child()
                    if isinstance(c, Gtk.Label):
                        txt = c.get_text()
                if txt != label:
                    continue
            hits.append(w)
        return hits

    def button(self, label):
        hits = [w for w in self.find(Gtk.Button, label=label)]
        if not hits:
            raise LookupError("no visible button labelled %r" % label)
        return hits[0]

    def click(self, label):
        """Click the visible button labelled `label` (its real handler)."""
        b = self.button(label)
        if not b.get_sensitive():
            print("NOTE click(%r): button is insensitive" % label)
        b.clicked()
        pump()

    def menu(self, menu_name):
        """The app's menu items for `menu_name` (label, callback, ...) via the
        app's own menu_items(); returns the raw list."""
        items = self.app.menu_items(menu_name)
        return items

    def menu_action(self, menu_name, label_prefix):
        """Fire the menu item whose label starts with label_prefix (the label
        carries the accelerator after spaces: 'Save    Ctrl+S')."""
        for it in self.menu(menu_name):
            if it is None or it is getattr(self.nbapp, "SEP", None):
                continue
            lab = it[0] if isinstance(it, (tuple, list)) else None
            if lab is None or not lab.startswith(label_prefix):
                continue
            cb = it[1] if len(it) > 1 else None
            if cb is None:
                continue
            if callable(cb):
                cb()
                pump()
                return True
            raise RuntimeError("menu item %r has no callable" % lab)
        raise LookupError("no item %r in menu %s" % (label_prefix, menu_name))

    def open_menu(self, menu_name):
        """Open the app's real in-window dropdown by clicking its menubar
        button, so the menu itself can be looked at. Returns the overlay
        layer widget (or None)."""
        btn = self.app._menu_buttons.get(menu_name)
        if btn is None:
            raise LookupError("no menu button %r" % menu_name)
        btn.clicked()
        self.pump(0.1)
        return getattr(self.app, "_menu_layer", None)

    def close_menu(self):
        try:
            self.app._close_menu()
        except Exception:                                         # noqa: BLE001
            pass
        self.pump(0.05)

    def texts(self):
        """Every visible label/entry text, for a quick 'what does it say'."""
        out = []
        for w in self.walk():
            if not w.get_visible():
                continue
            if isinstance(w, Gtk.Label):
                out.append(w.get_text())
            elif isinstance(w, Gtk.Entry):
                out.append("[entry:%s]" % w.get_text())
        return out

    def close(self):
        try:
            self.app.destroy()
        except Exception:                                         # noqa: BLE001
            pass
        try:
            self.off.destroy()
        except Exception:                                         # noqa: BLE001
            pass
        pump(0.1)


if __name__ == "__main__":
    mod = sys.argv[1] if len(sys.argv) > 1 else "tasks"
    d = Drive(mod)
    d.shot("/tmp/appdrive-%s.png" % mod, "launch")
    print("focus:", d.focus())
    d.close()
