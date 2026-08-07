#!/usr/bin/env python3
"""nbgame — run a fullscreen SDL game (the vbam GBA core) on the Notebook OS
desktop, working around matchbox's single-app model.

THE PROBLEM (see the gba-emulator-vbam memory for the full trace): matchbox only
shows ONE app window at a time and UNMAPS the rest. vbam is a second, raw SDL
window, so matchbox unmaps it and the game never appears. Every attempt to raise
it from outside failed — but matchbox *does* map and float DIALOG windows above
the current app (that is how the media-key OSD and the desktop widgets show).

THE APPROACH: build a fullscreen DIALOG "stage" (matchbox floats it above the
launcher), then REPARENT vbam's X window into the stage. Once reparented, vbam is
no longer a top-level window matchbox manages, so it cannot be unmapped — it just
renders inside the stage, which matchbox keeps visible.

RELIABILITY (why this file is defensive): we could not runtime-test the embed on
real hardware, so every step is made to fail *safely* and *visibly*:
  * The game window is located by its process id (SDL2 sets _NET_WM_PID), which
    is exact — not by guessing a WM_CLASS string. Class/name are only fallbacks.
  * There are THREE independent ways to quit, so a black "stage that won't go
    away" is impossible: a server-side Ctrl+Esc key grab (fires even while the
    game holds the keyboard), a GTK key handler on the stage (catches Ctrl+Esc
    or plain Esc whenever the game has not embedded and the stage has focus), and
    an always-visible, always-clickable "Exit game" button (the pointer can't be
    grabbed away from it).
  * Every step logs to the Emulator Log (File ▸ Emulator Log in the launcher),
    so if a game still misbehaves the log says exactly where it stopped.

All the X plumbing (reparent, focus, key grab) is ctypes-to-libX11, the same
idiom de/nbmediakeys.py uses for its global media-key grab.
"""
import ctypes
import os
import subprocess
from ctypes import (c_int, c_uint, c_ulong, c_long, c_void_p, c_char_p,
                    Structure)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

XK_Escape = 0x0000ff1b
ControlMask = 1 << 2
LockMask = 1 << 1          # CapsLock
Mod2Mask = 1 << 4          # NumLock (typical)
GrabModeAsync = 1
KeyPress = 2
RevertToParent = 2
CurrentTime = 0
_ESC_KEYCODE = 9          # Escape is X keycode 9 (evdev 1 + 8) everywhere
_BANNER_H = 46            # stage banner height; keep the game below it


class _XKeyEvent(Structure):
    # padded to a full XEvent (192 bytes on x86_64); we only read type + keycode
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", c_int),
        ("display", c_void_p), ("window", c_ulong), ("root", c_ulong),
        ("subwindow", c_ulong), ("time", c_ulong),
        ("x", c_int), ("y", c_int), ("x_root", c_int), ("y_root", c_int),
        ("state", c_uint), ("keycode", c_uint), ("same_screen", c_int),
        ("_pad", c_long * 16),
    ]


def _load_x11():
    x = ctypes.CDLL("libX11.so.6")
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    x.XCloseDisplay.argtypes = [c_void_p]
    x.XDefaultRootWindow.restype = c_ulong
    x.XDefaultRootWindow.argtypes = [c_void_p]
    x.XKeysymToKeycode.restype = c_int
    x.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
    x.XReparentWindow.argtypes = [c_void_p, c_ulong, c_ulong, c_int, c_int]
    x.XMoveResizeWindow.argtypes = [c_void_p, c_ulong, c_int, c_int, c_uint,
                                    c_uint]
    x.XMapRaised.argtypes = [c_void_p, c_ulong]
    x.XRaiseWindow.argtypes = [c_void_p, c_ulong]
    x.XSetInputFocus.argtypes = [c_void_p, c_ulong, c_int, c_ulong]
    x.XGrabKey.argtypes = [c_void_p, c_int, c_uint, c_ulong, c_int, c_int, c_int]
    x.XNextEvent.argtypes = [c_void_p, c_void_p]
    x.XPending.restype = c_int
    x.XPending.argtypes = [c_void_p]
    x.XConnectionNumber.restype = c_int
    x.XConnectionNumber.argtypes = [c_void_p]
    x.XFlush.argtypes = [c_void_p]
    x.XSync.argtypes = [c_void_p, c_int]
    return x


class GameSession:
    """Own a single running game: the stage window, the vbam process, the
    Ctrl+Esc grab. Call run(); `on_exit` fires (on the GLib main loop) when the
    game ends by any route."""

    def __init__(self, parent, vbam, rom, on_exit, scale_filter="17"):
        self.parent = parent
        self.vbam = vbam
        self.rom = rom
        self.on_exit = on_exit
        self.scale_filter = scale_filter
        self.proc = None
        self.stage = None
        self._banner = None
        self._grab_dpy = None
        self._grab_watch = None
        self._esc_codes = set()
        self._x = None
        self._poll_id = 0
        self._embed_id = 0
        self._embed_tries = 0
        self._embedded = False
        self._embed_win = 0
        self._logfh = None
        self._done = False

    # ---- public -----------------------------------------------------------
    def run(self):
        try:
            self._x = _load_x11()
        except Exception as e:
            self._x = None
            self._log("libX11 load failed: %r (game cannot embed; "
                      "Ctrl+Esc/Exit still work)" % e)
        self._build_stage()
        self.stage.show_all()
        # give the stage keyboard focus so its Ctrl+Esc / Esc handler works even
        # if the game never embeds; matchbox floats the dialog above the launcher
        try:
            self.stage.present()
            gw = self.stage.get_window()
            if gw is not None:
                gw.focus(CurrentTime)
        except Exception:
            pass
        # give the stage a moment to map (matchbox floats the dialog), then launch
        GLib.timeout_add(250, self._launch)

    def stop(self):
        """End the game (used by every exit route: Ctrl+Esc grab, the stage key
        handler, the Exit button, and external close)."""
        if self._done:
            return
        self._log("exit requested")
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        # tear the stage down promptly so the desktop returns even if vbam is slow
        # to die on SIGTERM; _finish is idempotent and _poll would call it anyway.
        GLib.timeout_add(150, self._finish)

    # ---- stage ------------------------------------------------------------
    def _build_stage(self):
        sw, sh = _screen_size(self.parent)
        st = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        st.set_decorated(False)
        # A FULLSCREEN APP window, exactly like any nbapp app — NOT a dialog.
        #
        # The stage was originally a DIALOG because matchbox floats dialogs above
        # the current app. But the desktop's menu bar is a DOCK, and our matchbox
        # patch (0003-panel-menu-bar-above-dialogs) deliberately stacks that DOCK
        # *above* dialogs so panel dropdowns can cover the Finder. A dialog stage
        # therefore always played the game UNDERNEATH the desktop menu bar —
        # which is what a user sees as the emulator "showing the toolbar".
        #
        # That same patch keeps its hands off the stacking when a mapped
        # MBCLIENT_TYPE_APP holds _NET_WM_STATE_FULLSCREEN, so the panel falls
        # back behind it. Being a normal fullscreen app is thus the supported way
        # to own the whole screen here, and it costs us nothing: matchbox only
        # unmaps *raw* second windows (that was vbam's problem), while GTK app
        # windows are promoted normally, and vbam is reparented INSIDE this one.
        st.set_skip_taskbar_hint(True)
        st.set_default_size(sw, sh)
        st.move(0, 0)
        # pin the opaque visual BEFORE realise, as every other toplevel in the
        # OS does — otherwise a compositor can hand this window the RGBA visual
        # and the banner bar's unpainted pixels become transparent holes
        try:
            import nbapp
            nbapp.force_opaque_visual(st)
        except Exception:
            pass
        st.fullscreen()
        st.set_can_focus(True)
        st.get_style_context().add_class("gamestage")
        # a stuck black screen must always be escapable — catch Ctrl+Esc (and, so
        # long as no game has embedded and swallowed focus, plain Esc) at the GTK
        # level as a backup to the server-side X grab.
        st.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        st.connect("key-press-event", self._on_stage_key)
        _install_css()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # top bar: the hint + an always-clickable Exit button (the pointer is
        # never grabbed away from it, so this is the one exit that cannot fail).
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("gamebar")
        bar.set_size_request(-1, _BANNER_H)
        banner = Gtk.Label(label="Loading game…")
        banner.get_style_context().add_class("gamehint")
        banner.set_xalign(0.5)
        banner.set_hexpand(True)
        bar.pack_start(banner, True, True, 0)
        exit_btn = Gtk.Button(label="Exit game  (Ctrl+Esc)")
        exit_btn.set_relief(Gtk.ReliefStyle.NONE)
        exit_btn.get_style_context().add_class("gameexit")
        exit_btn.set_can_focus(False)          # never steal keys from the game
        exit_btn.connect("clicked", lambda *_: self.stop())
        bar.pack_end(exit_btn, False, False, 0)
        box.pack_start(bar, False, False, 0)

        # the game reparents into this filler area; keep it black
        filler = Gtk.DrawingArea()
        filler.set_hexpand(True)
        filler.set_vexpand(True)
        box.pack_start(filler, True, True, 0)
        st.add(box)
        self.stage = st
        self._banner = banner
        # fade the banner text after a few seconds so it does not sit over play
        # forever (the Exit button stays fully visible)
        GLib.timeout_add_seconds(6, self._dim_banner)

    def _dim_banner(self):
        try:
            self._banner.get_style_context().add_class("dim")
        except Exception:
            pass
        return False

    def _set_banner(self, text):
        try:
            self._banner.set_text(text)
        except Exception:
            pass

    def _on_stage_key(self, _w, ev):
        # Ctrl+Esc always quits. Plain Esc quits too while the game has not
        # embedded (nothing is running to receive it) — so a black stage that
        # never got a game is never a trap.
        if ev.keyval in (Gdk.KEY_Escape,):
            ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
            if ctrl or not self._embedded:
                self.stop()
                return True
        return False

    # ---- launch + embed ---------------------------------------------------
    def _launch(self):
        # -f <n> is vbam's software stretch (17 = 4x = 960x640); its window is a
        # fixed size that matchbox/-F fullscreen won't grow, so we scale here and
        # centre the window inside the fullscreen stage.
        cmd = [self.vbam, "-f", self.scale_filter, self.rom]
        try:
            self._logfh = open(_log_path(), "w")
        except Exception:
            self._logfh = None
        # Pin vbam's X WM_CLASS so the (fallback) class search is exact regardless
        # of how it was launched; the primary search is by _NET_WM_PID.
        env = dict(os.environ)
        env["SDL_VIDEO_X11_WMCLASS"] = "vbam"
        # On a machine with no GPU driver the kernel's KMS device is `simpledrm`
        # (that is what real hardware boots with here). Mesa then tries to load
        # /usr/lib/dri/simpledrm_dri.so, which does not and cannot exist, and
        # vbam's GL output silently renders NOTHING — the game came up as a
        # black stage even though the emulator was running and its window had
        # been embedded correctly. Point Mesa at its software rasteriser
        # instead; swrast_dri.so does ship. Only when the session did not find
        # real acceleration, so a machine with a working GPU still uses it.
        if os.environ.get("NB_ACCEL") != "1":
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
            self._log("no GPU acceleration (NB_ACCEL=%r) — forcing Mesa's "
                      "software renderer so the game can draw"
                      % os.environ.get("NB_ACCEL"))
        self._log("$ %s" % " ".join(cmd))
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=(self._logfh or subprocess.DEVNULL),
                stderr=subprocess.STDOUT, env=env,
                cwd=os.path.dirname(self.rom) or os.path.expanduser("~"))
        except Exception as e:
            self._log("launch failed: %r" % e)
            self._set_banner("Could not start the emulator. Press Ctrl+Esc to exit.")
            self._finish()
            return False
        self._log("vbam pid=%d" % self.proc.pid)
        self._start_ctrlesc_grab()
        self._embed_tries = 0
        self._embed_id = GLib.timeout_add(400, self._embed)
        self._poll_id = GLib.timeout_add(500, self._poll)
        return False

    def _xdo(self, args):
        try:
            r = subprocess.run(["xdotool"] + args, capture_output=True,
                               timeout=3, text=True)
            return r.stdout.split()
        except Exception:
            return []

    def _find_vbam_window(self):
        """Return (xid, how) for the running vbam window, or (0, '').
        By PID first (SDL2 sets _NET_WM_PID, so this is exact), then by the
        WM_CLASS / title that SDL derives from the binary name."""
        pid = self.proc.pid if self.proc is not None else 0
        attempts = []
        if pid:
            attempts.append(("pid", ["search", "--pid", str(pid)]))
        attempts += [
            ("class", ["search", "--class", "vbam"]),
            ("classname", ["search", "--classname", "vbam"]),
            ("name", ["search", "--name", "VBA-M"]),
        ]
        # Take the BIGGEST candidate that is at least GBA-sized, not simply the
        # last one xdotool printed. During start-up SDL briefly owns a tiny
        # helper window with the same class/pid, and we used to embed THAT and
        # stop looking — it was destroyed moments later, while the real game
        # window appeared afterwards, stayed a matchbox top-level, got unmapped
        # by the single-app WM, and the player was left staring at a black stage.
        best, best_area, best_how = 0, 0, ""
        for how, args in attempts:
            for tok in self._xdo(args):
                try:
                    win = int(tok)
                except ValueError:
                    continue
                w, h = _vbam_geom(win)
                if w < 240 or h < 160:      # a GBA screen is 240x160
                    continue
                if w * h > best_area:
                    best, best_area, best_how = win, w * h, how
            if best:
                return best, best_how
        return 0, ""

    def _embed(self):
        if self._done or self.proc is None or self.proc.poll() is not None:
            return False
        if self.stage is None or self.stage.get_window() is None \
                or self._x is None:
            self._embed_tries += 1
            if self._x is None:
                return False        # no X plumbing: leave exit routes running
            return self._embed_tries < 40
        win, how = self._find_vbam_window()
        if not win:
            self._embed_tries += 1
            if self._embed_tries == 12:
                self._log("still looking for the game window…")
            if self._embed_tries >= 40:     # ~16s: stop hunting, keep exit alive
                self._log("game window not found after %d tries — the ROM may "
                          "have failed to open (see errors above)"
                          % self._embed_tries)
                self._set_banner("The game did not open. Press Ctrl+Esc to exit.")
                return False
            return True
        try:
            stage_xid = self.stage.get_window().get_xid()
            sw, sh = _screen_size(self.parent)
            gw, gh = _vbam_geom(win)
            x = max(0, (sw - gw) // 2)
            y = max(_BANNER_H, (sh - gh) // 2)      # never behind the top bar
            dpy = self._x.XOpenDisplay(None)
            # reparent vbam INTO the stage (it stops being a matchbox top-level,
            # so it can no longer be unmapped), position it centred, map + focus.
            self._x.XReparentWindow(dpy, win, stage_xid, x, y)
            self._x.XMapRaised(dpy, win)
            self._x.XRaiseWindow(dpy, win)
            self._x.XSetInputFocus(dpy, win, RevertToParent, CurrentTime)
            self._x.XSync(dpy, 0)
            self._x.XCloseDisplay(dpy)
            self._embedded = True
            self._embed_win = win
            self._log("embedded window %d (found via %s), %dx%d at %d,%d"
                      % (win, how, gw, gh, x, y))
            self._set_banner("Press  Ctrl + Esc  to exit")
            # re-assert map/raise/focus a few times: this guards the race where
            # matchbox is still settling the stacking AND catches vbam swapping
            # its window out from under us (see _reassert).
            for delay in (400, 1200, 2500, 5000):
                GLib.timeout_add(delay, self._reassert)
        except Exception as e:
            self._log("reparent failed: %r (retrying)" % e)
            self._embed_tries += 1
            return self._embed_tries < 40
        return False       # embedded: stop the embed loop

    def _reassert(self):
        """Re-map/raise the embedded game, and catch a window SDL swapped out.

        vbam can replace its window after we have embedded one (it recreates the
        surface when the video mode settles). The replacement is born as a
        matchbox top-level, so if we do not notice it the WM unmaps it and the
        player sees a black stage. If the window we embedded has gone away, or a
        bigger vbam window has appeared outside the stage, embed that instead."""
        if self._done or not self._embedded or self._x is None \
                or not self._embed_win:
            return False
        w, h = _vbam_geom(self._embed_win)
        if w == 0:
            self._log("embedded window %d disappeared — looking again"
                      % self._embed_win)
            self._embedded = False
            self._embed_win = 0
            self._embed_tries = 0
            self._embed_id = GLib.timeout_add(200, self._embed)
            return False
        cur, _how = self._find_vbam_window()
        if cur and cur != self._embed_win:
            self._log("vbam swapped its window (%d -> %d) — re-embedding"
                      % (self._embed_win, cur))
            self._embedded = False
            self._embed_win = 0
            self._embed_tries = 0
            self._embed_id = GLib.timeout_add(50, self._embed)
            return False
        try:
            dpy = self._x.XOpenDisplay(None)
            self._x.XMapRaised(dpy, self._embed_win)
            self._x.XRaiseWindow(dpy, self._embed_win)
            self._x.XSetInputFocus(dpy, self._embed_win, RevertToParent,
                                   CurrentTime)
            self._x.XSync(dpy, 0)
            self._x.XCloseDisplay(dpy)
        except Exception:
            pass
        return False

    # ---- global Ctrl+Esc --------------------------------------------------
    def _start_ctrlesc_grab(self):
        if self._x is None:
            return
        try:
            self._grab_dpy = self._x.XOpenDisplay(None)
            if not self._grab_dpy:
                self._log("Ctrl+Esc grab: could not open display")
                return
            root = self._x.XDefaultRootWindow(self._grab_dpy)
            # Grab BOTH the keymap's Escape keycode and the hardware keycode 9
            # (like nbmediakeys does for the media keys), so this works even on a
            # keymap where XKeysymToKeycode is odd.
            codes = {_ESC_KEYCODE}
            c = self._x.XKeysymToKeycode(self._grab_dpy, XK_Escape)
            if c:
                codes.add(int(c))
            self._esc_codes = codes
            # cover Caps/Num-lock states so Ctrl+Esc fires whatever the lock state
            for code in codes:
                for mod in (ControlMask, ControlMask | LockMask,
                            ControlMask | Mod2Mask,
                            ControlMask | LockMask | Mod2Mask):
                    self._x.XGrabKey(self._grab_dpy, code, mod, root,
                                     False, GrabModeAsync, GrabModeAsync)
            self._x.XFlush(self._grab_dpy)
            fd = self._x.XConnectionNumber(self._grab_dpy)
            self._grab_watch = GLib.io_add_watch(fd, GLib.IO_IN, self._on_grab_fd)
            self._log("Ctrl+Esc grab active (keycodes %s)" % sorted(codes))
        except Exception as e:
            self._log("Ctrl+Esc grab failed: %r" % e)
            self._grab_dpy = None

    def _on_grab_fd(self, *_a):
        ev = _XKeyEvent()
        try:
            while self._x.XPending(self._grab_dpy) > 0:
                self._x.XNextEvent(self._grab_dpy, ctypes.byref(ev))
                if ev.type == KeyPress and ev.keycode in self._esc_codes:
                    self.stop()          # Ctrl+Esc -> quit the game
        except Exception:
            pass
        return True

    # ---- lifecycle --------------------------------------------------------
    def _poll(self):
        if self.proc is None or self.proc.poll() is not None:
            self._finish()
            return False
        return True

    def _finish(self):
        if self._done:
            return False
        self._done = True
        for src in ("_poll_id", "_embed_id"):
            sid = getattr(self, src, 0)
            if sid:
                try:
                    GLib.source_remove(sid)
                except Exception:
                    pass
                setattr(self, src, 0)
        if self._grab_watch:
            try:
                GLib.source_remove(self._grab_watch)
            except Exception:
                pass
            self._grab_watch = None
        if self._grab_dpy and self._x is not None:
            try:
                self._x.XCloseDisplay(self._grab_dpy)
            except Exception:
                pass
            self._grab_dpy = None
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        try:
            if self._logfh:
                self._logfh.close()
        except Exception:
            pass
        self._logfh = None
        if self.stage is not None:
            try:
                self.stage.destroy()
            except Exception:
                pass
            self.stage = None
        try:
            self.on_exit()
        except Exception:
            pass
        return False

    # ---- log --------------------------------------------------------------
    def _log(self, msg):
        line = "[nbgame] %s\n" % msg
        try:
            if self._logfh:
                self._logfh.write(line)
                self._logfh.flush()
                return
        except Exception:
            pass
        try:
            with open(_log_path(), "a") as fh:
                fh.write(line)
        except Exception:
            pass


# ---- helpers ---------------------------------------------------------------
def _screen_size(parent):
    try:
        d = Gdk.Display.get_default()
        mon = d.get_primary_monitor() or d.get_monitor(0)
        g = mon.get_geometry()
        if g.width > 1 and g.height > 1:
            return g.width, g.height
    except Exception:
        pass
    return 1920, 1080


def _vbam_geom(win):
    """(width, height) of an X window, or (0, 0) if it cannot be measured.

    (0, 0) matters: it is how the caller tells a real game window apart from a
    short-lived one SDL creates and destroys during start-up, and from a window
    id that has already gone stale."""
    try:
        r = subprocess.run(["xdotool", "getwindowgeometry", str(win)],
                           capture_output=True, timeout=3, text=True)
        for ln in r.stdout.splitlines():
            if "Geometry:" in ln:
                wh = ln.split("Geometry:")[1].strip()
                w, h = wh.split("x")
                return int(w), int(h)
    except Exception:
        pass
    return 0, 0


def _log_path():
    home = os.environ.get("NB_HOME", os.path.expanduser("~"))
    d = os.path.join(home, ".config", "notebook")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, "vbam.log")


_CSS_DONE = False


def _install_css():
    global _CSS_DONE
    if _CSS_DONE:
        return
    css = b"""
    .gamestage { background: #1A1916; }
    .gamestage .gamebar { background: #1A1916; padding: 0 8px 0 18px; }
    .gamestage .gamehint { color: #F1EEE6;
        font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 15px;
        letter-spacing: 0.04em; }
    .gamestage .gamehint.dim { color: #6E695E; }
    .gamestage .gameexit { color: #F1EEE6; background: transparent;
        border: 1px solid #3A362E; border-radius: 8px; box-shadow: none;
        font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 13px;
        padding: 5px 14px; margin: 6px 0; }
    .gamestage .gameexit:hover { background: #C8341E; border-color: #C8341E;
        color: #FCFBF8; }
    """
    try:
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _CSS_DONE = True
    except Exception:
        pass
