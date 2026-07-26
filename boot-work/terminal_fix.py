#!/usr/bin/env python3
"""
Terminal — the Notebook OS terminal emulator (native GTK, VTE-backed).

A real shell on the paper desk: a VTE terminal widget running bash, styled to
the papertone design language (warm cream field, ink text) rather than the
usual black box. This is the machine's front door for every Linux task the
graphical apps don't cover.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Vte, GLib, Pango  # noqa: E402

import os

import nbapp

# papertone terminal palette: ink on warm paper, with muted ANSI colours that
# read on a light field (not the usual bright-on-black). BG/FG/CURSOR track the
# design language exactly (app paper #FCFBF8, ink #1A1916, one signage red).
BG = "#FCFBF8"
FG = "#1A1916"
CURSOR = "#C8341E"
PALETTE = [
    "#2A2620", "#B23A2B", "#5E7D53", "#9A7B26",   # blk red grn yel
    "#3E6C8E", "#8A5A9E", "#3E8B84", "#57534B",   # blu mag cyn wht
    "#6E695E", "#C8341E", "#7FA98C", "#C79A2E",   # bright variants
    "#5E8FB4", "#A97BC0", "#5FB0A6", "#2A2620",
]


class Terminal(nbapp.AppWindow):
    app_name = "Terminal"
    menus = ("Shell", "Edit", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stage.get_style_context().add_class("termstage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        self.content.pack_start(stage, True, True, 0)

        # a hairlined paper "card" so the terminal sits on the desk like the
        # other apps (soft shadow, no heavy border) — the desk gutter is CSS
        # padding on the stage so the card breathes.
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class("termcard")
        frame.set_hexpand(True)
        frame.set_vexpand(True)
        stage.pack_start(frame, True, True, 0)

        # calm uppercase header strip, mirroring the kicker row on the
        # mockup-driven apps (calculator's SCIENTIFIC / DEGREES).
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("termhead")
        kick = Gtk.Label(label="SHELL", xalign=0)
        kick.get_style_context().add_class("term-kicker")
        head.pack_start(kick, False, False, 0)
        shell_lbl = Gtk.Label(
            label=os.path.basename(self._find_shell()).upper(), xalign=1)
        shell_lbl.get_style_context().add_class("term-shell")
        head.pack_end(shell_lbl, False, False, 0)
        frame.pack_start(head, False, False, 0)

        field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        field.get_style_context().add_class("termfield")
        field.set_hexpand(True)
        field.set_vexpand(True)
        frame.pack_start(field, True, True, 0)

        self.term = Vte.Terminal()
        self.term.set_font(Pango.FontDescription("Monospace 12"))
        self.term.set_scrollback_lines(10000)
        self.term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.term.set_mouse_autohide(True)
        self.term.set_scroll_on_output(True)
        self.term.set_scroll_on_keystroke(True)
        self._apply_colors()

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.term)
        field.pack_start(sw, True, True, 0)

        self.term.connect("child-exited", lambda *_: self.close())
        self._spawn_shell()
        # focus the terminal so typing goes straight to the shell
        self.connect("map-event", lambda *_: (self.term.grab_focus(), False)[1])

    def _apply_colors(self):
        fg = Gdk.RGBA(); fg.parse(FG)
        bg = Gdk.RGBA(); bg.parse(BG)
        cur = Gdk.RGBA(); cur.parse(CURSOR)
        pal = []
        for c in PALETTE:
            rgba = Gdk.RGBA(); rgba.parse(c); pal.append(rgba)
        self.term.set_colors(fg, bg, pal)
        self.term.set_color_cursor(cur)

    @staticmethod
    def _find_shell():
        # Prefer a real bash (best interactive shell) over $SHELL, which the
        # login sets to busybox /bin/sh; fall back to $SHELL then plain sh.
        cand = ["/bin/bash", "/usr/bin/bash",
                os.environ.get("SHELL", ""), "/bin/sh"]
        for c in cand:
            if c and os.path.exists(c):
                return c
        return "/bin/sh"

    def _spawn_shell(self):
        home = os.environ.get("NB_HOME", "/root")
        # inherit the real session environment (PATH, DISPLAY, ...) and just
        # override TERM/HOME — building a fresh minimal env silently broke the
        # spawn. envv is a list of "KEY=VALUE" strings.
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["HOME"] = home
        envv = ["%s=%s" % (k, v) for k, v in env.items()]
        shell = self._find_shell()
        # a callback surfaces spawn failures instead of failing silently.
        # spawn_async can also raise synchronously (bad pty/cwd) before the
        # callback ever runs — guard that so a spawn failure degrades to an
        # in-terminal notice instead of aborting the window's construction.
        try:
            self.term.spawn_async(
                Vte.PtyFlags.NO_LASTLOG | Vte.PtyFlags.NO_UTMP
                | Vte.PtyFlags.NO_WTMP, home, [shell], envv,
                GLib.SpawnFlags.DEFAULT, None, None, -1, None, self._spawned)
        except (GLib.Error, OSError, TypeError) as e:
            try:
                self.term.feed(
                    ("\r\n  could not start the shell: %s\r\n" % e).encode())
            except Exception:
                pass

    def _spawned(self, _term, pid, error):
        if error is not None:
            try:
                self.term.feed(
                    ("\r\n  could not start the shell: %s\r\n"
                     % error).encode())
            except Exception:
                pass

    def _install_css(self):
        css = b"""
        .termstage { background: #F4F2EC; padding: 30px 34px 34px; }

        .termcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                    box-shadow: 0 12px 34px rgba(26,25,22,0.10); }
        .termcard * { font-family: "Helvetica Neue","Helvetica",sans-serif; }

        .termhead { background: #F1EEE6; border-bottom: 1px solid #D7D2C5;
                    padding: 11px 16px; }
        .term-kicker { font-size: 11px; letter-spacing: 0.18em;
                       font-weight: 700; color: #6E695E; }
        .term-shell  { font-size: 11px; letter-spacing: 0.18em;
                       font-weight: 700; color: #9A9484; }

        .termfield { background: #FCFBF8; padding: 12px 14px; }
        """
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(Terminal)
