#!/usr/bin/env python3
"""
Terminal — the Notebook OS terminal emulator (native GTK, VTE-backed).

A VTE terminal widget running an interactive shell (bash when present, else the
system /bin/sh), styled to the papertone design language: a warm paper card on
the desk, ink-on-paper text, and one signage-red accent for the cursor. It is a
real shell for the Linux tasks the graphical apps do not cover.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os
import json
import signal

import nbapp
from nbi18n import _t  # noqa: E402

# View preferences (font zoom, cursor blink) are the two settings worth
# remembering across launches, so a demanding user who bumps the type up or
# stills the blinking cursor need not redo it every time. They live in this
# app's private JSON file under the shared per-app config dir (NB_HOME defaults
# to /root, as elsewhere). No shell output or history is ever persisted here.
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STATE_FILE = os.path.join(CFG_DIR, "terminal.json")

# The VTE terminal backend is only guaranteed on the built guest, not on the
# host running construct_all.py / the selftests. Guard the require_version +
# import so the module always imports and the window always constructs; VTE_OK
# gates every use of the widget below, and an honest notice replaces the
# terminal when the backend is absent (or built for a different ABI version).
VTE_OK = False
try:
    gi.require_version("Vte", "2.91")
    from gi.repository import Vte  # noqa: E402
    VTE_OK = True
except (ImportError, ValueError):
    Vte = None

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
    menus = ("Session", "Edit", "View")

    def __init__(self):
        # self.term stays None until (and unless) a real VTE widget is built, so
        # every guard below has a stable attribute to test.
        self.term = None
        self._child_pid = None
        self._pending_spawn = False
        # Deferred-callback ownership. _spawned() arms a 250ms one-shot to clear
        # VTE's startup notice; the window can be destroyed inside those 250ms,
        # and a repeated spawn (New Session) can arm it again while an earlier
        # one is still pending. _closed says the widgets are gone (so a callback
        # that outlives them must do nothing), and _startup_notice_source holds
        # the ONE timeout source this window owns, so it can be cancelled or
        # replaced. Both are set before any signal is connected and before any
        # shell is spawned, so every path below has a stable attribute to read.
        self._closed = False
        self._startup_notice_source = 0
        # Without the VTE backend there is nothing to run: the
        # Session/Edit/View menus would be dead controls, so drop them
        # (instance attr shadows the class attr) BEFORE the base builds the
        # menu bar. Only the app menu (About / Close) remains.
        if not VTE_OK:
            self.menus = ()

        super().__init__()
        self._install_css()
        # Teardown: retire anything still scheduled against these widgets, then
        # persist View preferences (zoom, cursor blink). The save is guarded and
        # a no-op when there is no live terminal, so it never writes a spurious
        # file on a host without the VTE backend.
        self.connect("destroy", self._on_destroy)

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
        # The kicker names the APP, the way Journal's says JOURNAL — not the
        # program behind it. It used to read SHELL, with the shell binary's own
        # file name shouted beside it ("ASH", "BASH"), which told a person the
        # one thing about this window they have no use for and no way to
        # change, in the app that already asks the most of them.
        kick = Gtk.Label(label=_t("TERMINAL"), xalign=0)
        kick.get_style_context().add_class("term-kicker")
        head.pack_start(kick, False, False, 0)
        frame.pack_start(head, False, False, 0)

        field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        field.get_style_context().add_class("termfield")
        field.set_hexpand(True)
        field.set_vexpand(True)
        frame.pack_start(field, True, True, 0)

        if VTE_OK:
            self._build_terminal(field)
            frame.pack_start(self._hintbar(), False, False, 0)
        else:
            # Honest empty state: no fabricated output, just a neutral notice.
            notice = Gtk.Label(
                label=_t("The terminal is not available on this system."))
            notice.get_style_context().add_class("term-notice")
            notice.set_line_wrap(True)
            notice.set_halign(Gtk.Align.CENTER)
            notice.set_valign(Gtk.Align.CENTER)
            field.pack_start(notice, True, True, 0)

    def _hintbar(self):
        """A permanent one-line footer under the terminal.

        The shell owns the whole field, so the one fact a reader cannot get from
        the prompt itself -- the way back out -- lives outside it, where the
        shell cannot clear it, scroll it away or overwrite it. It used to carry
        two more clauses teaching what to type and one command worth knowing;
        that is a tutorial, not a label, and it is gone."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("termhint")
        # GTK3 propagates vexpand UP from descendants; pin the bar so it can
        # never become an expanding child and float off the bottom edge.
        bar.set_vexpand(False)
        lbl = Gtk.Label(
            label=_t("Type exit to return to the Finder"),
            xalign=0)
        lbl.get_style_context().add_class("term-hint")
        lbl.set_line_wrap(True)       # degrade to two lines on a narrow panel
        bar.pack_start(lbl, True, True, 0)
        return bar

    def _build_terminal(self, field):
        scale, blink = self._load_prefs()
        self.term = Vte.Terminal()
        # House mono stack, matching the one other monospace surface in the
        # suite (screenplay.py's script pages: 'Courier New','Liberation Mono',
        # monospace) so both terminal and screenplay resolve to the same face.
        self.term.set_font(
            Pango.FontDescription("Courier New, Liberation Mono, Monospace 12"))
        self.term.set_scrollback_lines(10000)
        self.term.set_cursor_blink_mode(
            Vte.CursorBlinkMode.ON if blink else Vte.CursorBlinkMode.OFF)
        self.term.set_mouse_autohide(True)
        self.term.set_scroll_on_output(True)
        self.term.set_scroll_on_keystroke(True)
        try:
            self.term.set_font_scale(scale)
        except Exception:
            pass
        self._apply_colors()

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # No-compositor black-safety: the ScrolledWindow (and its viewport) must
        # paint an OPAQUE paper background so no black shows through the VTE
        # widget during resize / scrollbar overlay on the software stack.
        sw.get_style_context().add_class("termscroll")
        sw.add(self.term)
        field.pack_start(sw, True, True, 0)

        # Track the live shell pid so a deliberate New Session can tell the old
        # shell's exit (window stays open) apart from the user exiting the
        # current shell (window closes). See _on_child_exited.
        self.term.connect("child-exited", self._on_child_exited)
        self._spawn_shell()
        # focus the terminal so typing goes straight to the shell
        self.connect("map-event",
                     lambda *_: (self.term.grab_focus(), False)[1])

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
        # _pending_spawn is cleared by _spawned(), so EVERY path that returns
        # without handing a spawn to VTE must clear it here: nothing else will,
        # and a guard left standing latches on for the life of the window —
        # _on_child_exited then returns early for ever and the window can no
        # longer close when its shell exits.
        if self.term is None:
            self._pending_spawn = False
            return
        home = os.environ.get("NB_HOME", "/root")
        # inherit the real session environment (PATH, DISPLAY, ...) and just
        # override TERM/HOME — building a fresh minimal env silently broke the
        # spawn. envv is a list of "KEY=VALUE" strings.
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["HOME"] = home
        # The image ships no bashrc, so bash would fall back to its built-in
        # prompt — "bash-5.2#", a program name and a version number, which tells
        # someone opening this window nothing. Bash honours an inherited PS1, so
        # hand it one that answers the only question a prompt should: where am
        # I. "~ #" at home, "/etc #" elsewhere. setdefault, so a prompt set in
        # the session environment still wins.
        env.setdefault("PS1", r"\w \$ ")
        envv = ["%s=%s" % (k, v) for k, v in env.items()]
        shell = self._find_shell()
        # a callback surfaces spawn failures instead of failing silently.
        # spawn_async can also raise synchronously (bad pty/cwd) before the
        # callback ever runs — guard that so a spawn failure degrades to an
        # in-terminal notice instead of aborting the window's construction.
        try:
            self.term.spawn_async(
                Vte.PtyFlags.DEFAULT, home, [shell], envv,
                GLib.SpawnFlags.DEFAULT, None, None, -1, None, self._spawned)
        except (GLib.Error, OSError, TypeError):
            # A synchronous raise means _spawned() will never run, so clear the
            # restart guard before surfacing the notice (see above).
            self._pending_spawn = False
            self._feed_notice(self._start_problem(shell))

    @staticmethod
    def _start_problem(shell):
        """A plain sentence for a terminal that would not start.

        The GLib.Error behind this used to be written straight into the
        terminal body — 'Could not start the shell: gi.repository.GLib.GError(
        ...)'. A person reading that learns nothing they can act on, in the one
        window that has no other content to fall back on. Nothing here is
        destructive, so the message says what is true and what to try."""
        if not (shell and os.path.exists(shell)):
            return _t("No shell is installed on this system.")
        return _t("The terminal could not be started. Close this window and "
                  "open the Terminal again.")

    def _feed_notice(self, text):
        # Write a neutral one-line notice into the terminal without pretending
        # to be shell output; crash-safe if the widget cannot accept a feed.
        try:
            self.term.feed(("\r\n  %s\r\n" % text).encode())
        except Exception:
            pass

    def _spawned(self, _term, pid, error):
        if error is not None:
            self._pending_spawn = False
            self._feed_notice(self._start_problem(self._find_shell()))
            return
        # A fresh shell is now the live child: record its pid and clear the
        # restart guard, so this shell's own exit (user typing `exit`) closes
        # the window while an in-flight New Session did not.
        self._child_pid = pid
        self._pending_spawn = False
        # VTE (built without GnuTLS) writes a one-time notice to the pty before
        # the shell starts, warning that its on-disk scrollback stream is
        # unencrypted. It is cosmetic here (single-user offline device) but
        # reads as alarming. Once bash has printed its first prompt, send Ctrl-L
        # so the shell clears the notice and redraws a clean prompt.
        #
        # That one-shot outlives this call, so the window has to OWN it: nothing
        # is armed once the window is gone, an earlier pending one-shot (a
        # second New Session inside 250ms) is retired rather than left to pile
        # up, and the live source id is kept so _on_destroy can cancel it.
        if self._closed:
            return
        if self._startup_notice_source:
            GLib.source_remove(self._startup_notice_source)
        self._startup_notice_source = GLib.timeout_add(
            250, self._clear_startup_notice)

    def _clear_startup_notice(self):
        # Drop the claim FIRST: this source is about to end either way, and a
        # stale id left behind would have _spawned/_on_destroy remove a source
        # that no longer exists.
        self._startup_notice_source = 0
        if self._closed:
            return False      # the terminal is gone; feeding it is not our job
        self._feed_child(b"\x0c")
        return False  # one-shot

    def _on_destroy(self, *_):
        """Window teardown, idempotent (destroy can only be honoured once).

        _closed is raised BEFORE anything else, so a callback that slips past
        the cancellation below still sees a closed window and does nothing, and
        the preferences are written exactly once."""
        if self._closed:
            return False
        self._closed = True
        source_id = self._startup_notice_source
        self._startup_notice_source = 0
        if source_id:
            GLib.source_remove(source_id)
        self._save_prefs()
        return False

    # -- menus --
    # Terminal declares Session/Edit/View but the base menu_items() only knows
    # File/Edit/app-name, so Session and View would return [] (dead controls
    # that open nothing) and the base Edit's Cut/Copy/Paste route through
    # _edit(), which only handles Gtk.Editable/TextView and no-ops on a
    # Vte.Terminal. Override menu_items() to give them real actions and wire
    # VTE's own clipboard. The app-name ("Terminal") menu is also overridden
    # with a live terminal so its Close drops the now-inaccurate "Esc" hint
    # (Esc reaches the shell); with no backend it falls through to super() and
    # keeps the base About/Close (Esc). (When the VTE backend is missing the
    # Session/Edit/View menus are dropped entirely in __init__, so those
    # branches only run with a live terminal.)
    def menu_items(self, name):
        # With a live shell, Esc is a terminal key (it reaches the shell — see
        # _on_key), so the app menu's Close carries no misleading "Esc" hint.
        # The logo, this Close, or typing `exit` return to the Finder.
        if name == self.app_name and VTE_OK:
            return [(_t("About %s") % _t(self.app_name), self._about),
                    nbapp.SEP, (_t("Close"), self.close)]
        # Menu labels below are BARE literals: nbapp._open_menu() already runs
        # every label through _t(), and a label built by a call (or by an
        # inline conditional) is invisible to tools/i18n_check's chrome scan —
        # which is how a whole app's menu bar once shipped half-English in all
        # 17 languages without the tool noticing.
        # "Session", not "Shell": this window already calls itself Terminal,
        # and the menu names what the entries under it act on — the session you
        # are typing in. The kicker dropped SHELL for the same reason.
        if name == "Session":
            return [("New Session", self._new_session), nbapp.SEP,
                    ("Reset", self._shell_reset),
                    ("Clear", self._shell_clear), nbapp.SEP,
                    ("Close", self.close)]
        if name == "Edit":
            # A terminal cannot Cut (its output is not editable), so Edit offers
            # Copy / Paste / Select All. The accelerators are the terminal
            # standard — Ctrl+Shift, leaving plain Ctrl+C free to signal the
            # foreground program.
            return [("Copy    Ctrl+Shift+C", self._term_copy),
                    ("Paste    Ctrl+Shift+V", self._term_paste), nbapp.SEP,
                    ("Select All    Ctrl+Shift+A", self._term_select_all)]
        if name == "View":
            blink = False
            try:
                blink = (self.term.get_cursor_blink_mode()
                         == Vte.CursorBlinkMode.ON)
            except Exception:
                pass
            # "Ctrl+Plus", not "Ctrl +": every other shortcut in the OS is
            # written Ctrl+Key with no spaces, and the old form also used a
            # MINUS SIGN (U+2212) where the key on the keyboard is a hyphen.
            items = [("Zoom In    Ctrl+Plus", lambda: self._zoom(1.1)),
                     ("Zoom Out    Ctrl+Minus", lambda: self._zoom(0.9)),
                     ("Actual Size    Ctrl+0", lambda: self._zoom(None)),
                     nbapp.SEP]
            if blink:
                items.append(("Stop Cursor Blink", self._toggle_blink))
            else:
                items.append(("Blink Cursor", self._toggle_blink))
            return items
        return super().menu_items(name)

    # -- keyboard --
    def _on_key(self, w, ev):
        """Terminal key handling, layered over the base.

        The base treats Esc as "return to the Finder", but in a terminal Esc is
        a real key: vi, less, readline and every TUI need it, and a stray Esc
        must never tear down the shell and whatever is running in it. So with a
        live terminal Esc first dismisses an open About card / menu, and
        otherwise is sent straight to the shell; the logo, Terminal ▸ Close, or
        typing `exit` return to the Finder. Ctrl+Shift+C/V/A and Ctrl +/−/0 are
        the standard terminal clipboard and zoom accelerators, intercepted here
        (as any terminal does) so plain Ctrl+C still signals the shell. Anything
        else falls through to the base and then to the terminal itself."""
        kv = ev.keyval
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)

        if kv == Gdk.KEY_Escape:
            if self._close_about():
                return True
            if self._menu_open is not None:
                self._close_menu()
                return True
            if self.term is not None:
                self.term.grab_focus()
                self._feed_child(b"\x1b")
                return True
            # No live shell (VTE backend absent): keep the base Esc = close.
            return super()._on_key(w, ev)

        if self.term is not None and ctrl:
            if shift and kv in (Gdk.KEY_C, Gdk.KEY_c):
                self._term_copy(); return True
            if shift and kv in (Gdk.KEY_V, Gdk.KEY_v):
                self._term_paste(); return True
            if shift and kv in (Gdk.KEY_A, Gdk.KEY_a):
                self._term_select_all(); return True
            if kv in (Gdk.KEY_plus, Gdk.KEY_KP_Add) or \
                    (kv == Gdk.KEY_equal and not shift):
                self._zoom(1.1); return True
            if kv in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self._zoom(0.9); return True
            if kv in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self._zoom(None); return True

        return super()._on_key(w, ev)

    def _refocus(self):
        # Return keyboard focus to the shell after a menu action, so typing
        # flows straight on without a click back into the terminal.
        if self.term is not None:
            try:
                self.term.grab_focus()
            except Exception:
                pass

    def _feed_child(self, data):
        # VTE API drift (mirrors _clear_startup_notice): newer feed_child takes
        # bytes with auto length, older takes (text, length).
        if self.term is None:
            return
        try:
            try:
                self.term.feed_child(data)
            except TypeError:
                text = data.decode("utf-8", "replace")
                self.term.feed_child(text, len(text))
        except Exception:
            pass

    # -- child lifecycle --
    def _on_child_exited(self, *_):
        # Close the window only when there is no live shell. A New Session
        # terminates the old shell on purpose: that exit arrives here while a
        # fresh shell is being (or has just been) spawned, so the window must
        # stay open. The user exiting the current shell (which VTE has already
        # reaped by the time this fires) does close the window.
        if self._pending_spawn:
            return
        if self._child_pid is not None and self._pid_alive(self._child_pid):
            return
        self.close()

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    # -- Session actions --
    def _new_session(self):
        # Replacing the shell in place terminates whatever is running in it and
        # wipes the scrollback, so confirm first (no undo). Default is Cancel.
        if not self._confirm(
                _t("New Session"),
                _t("Start a new session? Anything running now will stop, and "
                   "everything on screen will be cleared."),
                _t("New Session")):
            return
        self._start_new_session()

    def _start_new_session(self):
        # Start a genuinely fresh shell session: terminate the current shell,
        # clear the screen and scrollback, then spawn a new shell from the
        # configured home with the base environment. This is a real new session
        # (fresh cwd/env, empty scrollback), not `exec` reusing the old
        # process' state in place with no visible effect. Killing the shell
        # BEFORE respawning (while its pid is still ours) avoids signalling a
        # recycled pid; _pending_spawn keeps that deliberate exit from tripping
        # the child-exited -> close path.
        if self.term is None:
            return
        self._pending_spawn = True
        old = self._child_pid
        self._child_pid = None
        if old:
            try:
                os.kill(old, signal.SIGHUP)   # interactive bash exits on SIGHUP
            except OSError:
                pass
        try:
            self.term.reset(True, True)       # clear screen + scrollback
        except Exception:
            pass
        self._spawn_shell()
        self._refocus()

    def _shell_reset(self):
        if self.term is None:
            return
        try:
            self.term.reset(True, True)   # hard reset (like the `reset` command)
        except Exception:
            pass
        self._refocus()

    def _shell_clear(self):
        self._feed_child(b"\x0c")         # Ctrl-L: shell clears and redraws prompt
        self._refocus()

    # -- View actions --
    def _zoom(self, factor):
        # factor None -> back to 1.0; else scale the current font size (clamped).
        if self.term is None:
            return
        try:
            cur = self.term.get_font_scale()
        except Exception:
            cur = 1.0
        new = 1.0 if factor is None else max(0.5, min(3.0, cur * factor))
        try:
            self.term.set_font_scale(new)
        except Exception:
            pass
        self._save_prefs()
        self._refocus()

    def _toggle_blink(self):
        if self.term is None:
            return
        try:
            on = (self.term.get_cursor_blink_mode() == Vte.CursorBlinkMode.ON)
            self.term.set_cursor_blink_mode(
                Vte.CursorBlinkMode.OFF if on else Vte.CursorBlinkMode.ON)
        except Exception:
            pass
        self._save_prefs()
        self._refocus()

    # -- Edit actions on the VTE terminal (base _edit() can't see a Vte.Terminal) --
    def _term_copy(self):
        if self.term is None:
            return
        try:
            try:
                self.term.copy_clipboard_format(Vte.Format.TEXT)  # VTE >= 0.50
            except (AttributeError, TypeError):
                self.term.copy_clipboard()                        # older VTE
        except Exception:
            pass
        self._refocus()

    def _term_paste(self):
        if self.term is None:
            return
        try:
            self.term.paste_clipboard()
        except Exception:
            pass
        self._refocus()

    def _term_select_all(self):
        if self.term is None:
            return
        try:
            self.term.select_all()
        except Exception:
            pass
        self._refocus()

    # -- View preference persistence --
    def _load_prefs(self):
        """Return (font_scale, cursor_blink) from the saved preferences, or the
        defaults (1.0, blinking on) when the file is missing or malformed. Must
        never crash the launch, so every value is range/type checked."""
        scale, blink = 1.0, True
        self._extra = {}
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                known = {"font_scale", "cursor_blink", "_extra"}
                self._extra = (dict(data.get("_extra"))
                               if isinstance(data.get("_extra"), dict) else {})
                self._extra.update((k, v) for k, v in data.items() if k not in known)
                s = data.get("font_scale")
                if isinstance(s, (int, float)) and 0.5 <= s <= 3.0:
                    scale = float(s)
                b = data.get("cursor_blink")
                if isinstance(b, bool):
                    blink = b
        except Exception:
            pass
        return scale, blink

    def _save_prefs(self):
        """Write the current View preferences. A no-op without a live terminal,
        so a host lacking the VTE backend never leaves a stray file behind."""
        if self.term is None:
            return
        try:
            scale = float(self.term.get_font_scale())
        except Exception:
            scale = 1.0
        try:
            blink = (self.term.get_cursor_blink_mode() == Vte.CursorBlinkMode.ON)
        except Exception:
            blink = True
        try:
            payload = dict(getattr(self, "_extra", {}) or {})
            payload.update({"font_scale": round(scale, 3),
                            "cursor_blink": bool(blink),
                            "_extra": getattr(self, "_extra", {})})
            nbapp.atomic_write_json(STATE_FILE, payload)
        except Exception as exc:
            nbapp.save_failure_reason = str(exc)

    # -- destructive-action confirmation --
    def _confirm(self, title, body, ok_label):
        """A small modal Cancel / <ok_label> confirmation for a destructive
        action. Returns True on the positive response. Defaults to Cancel so a
        stray Return never terminates the shell (crash-safe)."""
        try:
            dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
            # Undecorated: a window-manager title bar makes a dialog look like it
            # belongs to another computer. The card already builds its own
            # .dlghead heading, so nothing is lost by dropping the bar.
            dlg.set_decorated(False)
            dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
            ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
            # Name the action that ends the shell, so it is not one of two
            # identical buttons. Cancel stays the default (a stray Return must
            # never kill a running command).
            ok.get_style_context().add_class("destructive-action")
            dlg.set_default_response(Gtk.ResponseType.CANCEL)
            area = dlg.get_content_area()
            area.set_spacing(10)
            area.set_margin_top(18); area.set_margin_bottom(14)
            area.set_margin_start(20); area.set_margin_end(20)
            # The title only reaches the window manager's frame; every other
            # confirmation in the OS states it inside the dialog too, so the
            # question is legible on its own.
            head = Gtk.Label(label=title, xalign=0)
            head.get_style_context().add_class("dlghead")
            area.add(head)
            msg = Gtk.Label(label=body, xalign=0)
            msg.set_line_wrap(True); msg.set_max_width_chars(46)
            area.add(msg)
            dlg.show_all()
            try:
                resp = dlg.run()
            finally:
                dlg.destroy()
            return resp == Gtk.ResponseType.OK
        except Exception:
            # If the dialog cannot be shown, fail safe: do NOT act.
            return False

    def _install_css(self):
        css = b"""
        .termstage { background: #DED4C2; padding: 30px 34px 34px; }

        .termcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                    box-shadow: 4px 4px 0 rgba(26,25,22,0.12); }
        .termcard * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        .termhead { background: #F1EEE6; border-bottom: 1px solid #D7D2C5;
                    padding: 11px 16px; }
        .term-kicker { font-size: 11px; letter-spacing: 0.18em;
                       font-weight: 700; color: #6E695E; }

        .termfield { background: #FCFBF8; padding: 12px 14px; }
        .termscroll, .termscroll viewport { background: #FCFBF8; }
        .term-notice { color: #6E695E; font-size: 14px; padding: 40px; }

        /* standing guidance strip, mirroring the header strip above it so the
           terminal field reads as the card's content between the two */
        .termhint { background: #F1EEE6; border-top: 1px solid #D7D2C5;
                    padding: 9px 16px; }
        .term-hint { font-size: 12px; color: #6E695E; }
        """
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(Terminal)
