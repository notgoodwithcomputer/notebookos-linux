#!/usr/bin/env python3
"""
login.py — the sign-in screen, at startup and on waking from sleep.

    login.py            sign in at startup
    login.py --lock     lock the screen (waking from sleep)
    login.py --needed   ask NOTHING; exit 0 if a sign-in screen would be shown

Exits 0 when the person is who they say they are, and only then. The session
script waits on that exit code, so nothing of the desktop is started — and on
--lock, nothing is revealed — until it returns.

`--needed` exists because session.sh has to know the answer BEFORE it decides
what to put on screen: the boot loading screen and this screen are both
full-screen keep-above windows, and two of those at once is a stacking race
nobody wins. It answers from two small file reads and one crypt call, ahead of
the GTK import below, so on a machine with no password (every live ISO) the
whole sign-in path now costs a bare interpreter start instead of loading Gtk,
nbapp and nbicons only to return 0.

WHY THIS EXISTS BEYOND THE OBVIOUS
The installer already asks for a username and a password, writes them properly
into /etc/passwd and /etc/shadow with a SHA-512 hash, and then nothing ever
used them: the session hardcoded NB_HOME=/root and ran as root, so the account
existed but never guarded anything. Asking somebody to invent a password and
then ignoring it is worse than not asking. This screen makes that account mean
something.

IT MUST NEVER LOCK SOMEBODY OUT
`has_password()` is the whole safety story. The shipped image locks root (this
build writes `root:*`; an image built with root login enabled would ship
`root::`, an EMPTY field) and an account that cannot be authenticated cannot be
asked about, so there is nothing to prompt for. In that case this screen does
not appear at all and the desktop starts as before. A lock screen that demands
a password nobody set is not security, it is a brick.

That rule is NOT satisfied by looking at the shape of the stored string, and
this file used to do exactly that. Six shapes were measured getting past it —
`root:` with no fields after it, a hash with a stray newline or carriage return
still attached, a whitespace-only field, an algorithm this machine's libcrypt
does not implement (`$y$` yescrypt on glibc), a corrupt field, and a Python
with no `crypt` module at all. Every one of them made has_password() answer
"yes" while verify() could never answer "yes" to anything typed: the screen
appears, every attempt fails, and the owner is locked out of their own offline
computer for good. So the question this file now asks is not "does this look
like a password?" but "can crypt on THIS machine match this string against
something a person could type?" — see `_can_verify`.
"""
import os
import sys
import time

SHADOW = "/etc/shadow"
PASSWD = "/etc/passwd"

INK = "#1A1916"
MUTED = "#6E695E"
PAPER = "#FCFBF8"

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


USER_NAME_FILE = "/etc/notebookos-user"


def display_name(user):
    """The name to greet at sign-in.

    The account really is root -- this is a single-user appliance whose desktop
    runs as the administrator -- but "root" is a word about the machine, not
    about the person sitting at it, and it is the first thing they read every
    morning. The installer (or first-run setup) writes what they call
    themselves here; without it we fall back to the account name, which is what
    shipped before.
    """
    try:
        with open(USER_NAME_FILE) as fh:
            got = fh.read().strip()
        if got:
            return got[:40]
    except OSError:
        pass
    return user


def desktop_user():
    """Whose password this screen should ask for.

    Prefer a REAL person's account — the one the installer created (uid >= 1000
    with a login shell) — over root. That matters now that root is locked: the
    session still RUNS as root and NB_HOME is still /root, so keying off either
    would find a locked account, conclude there is nothing to ask, and skip the
    sign-in screen on exactly the installed machines that need it.

    Falls back to whoever owns NB_HOME, then to root, so a live image (no user
    account, root locked) still resolves to something and simply shows no
    screen."""
    best = None
    try:
        with open(PASSWD, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split(":")
                if len(parts) < 7:
                    continue
                name, uid, shell = parts[0], parts[2], parts[6].strip()
                try:
                    uid = int(uid)
                except ValueError:
                    continue
                # A real seat: a human uid with a shell they could log into.
                if uid >= 1000 and not shell.endswith(("nologin", "false")):
                    if has_password(name):
                        return name
                    best = best or name
    except OSError:
        pass
    if best:
        return best
    home = os.environ.get("NB_HOME", "/root")
    try:
        with open(PASSWD, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split(":")
                if len(parts) > 5 and parts[5].rstrip("/") == home.rstrip("/"):
                    return parts[0]
    except OSError:
        pass
    return "root"


def _shadow_hash(user):
    """The stored hash for `user`, or None if it cannot be read.

    STRIPPED, and that is not cosmetic. When the hash is the last field on the
    line — a shadow truncated to `root:$6$...` by a full disk or a torn write —
    the field still carries the line's own "\\n", and crypt(3) hands back a
    string WITHOUT it, so the comparison in verify() can never be equal. The
    screen then appears over a password that is right and is rejected forever.
    A crypt string never contains whitespace, so stripping it is always safe;
    a field that was ONLY whitespace correctly collapses to "no password"."""
    try:
        with open(SHADOW, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split(":")
                if parts and parts[0] == user:
                    return (parts[1] if len(parts) > 1 else "").strip()
    except OSError:
        return None            # not root, or no shadow file — cannot verify
    return None


# Fed to crypt only to see whether crypt is willing to answer at all for a
# given stored string. Plain ASCII on purpose: crypt(3) takes a C string, so an
# embedded NUL would raise and turn this whole check into a blanket "no".
_PROBE = "notebookos-crypt-probe"


def _can_verify(stored):
    """Could ANYTHING a person types ever match `stored` on THIS machine?

    Asked of crypt rather than inferred from the string, because the shape of a
    hash says nothing about whether the C library in front of us implements it:

      * no `crypt` module at all — the guest's Python 3.11 has it, 3.13 removed
        it, and a build that lands on 3.13 would otherwise show a sign-in
        screen that no password on earth can satisfy;
      * an algorithm this libcrypt does not implement (glibc, which this image
        uses, has no yescrypt, so a `$y$` entry is unmatchable here) — glibc
        answers NULL, which reaches Python as None, and libxcrypt answers a
        deliberately-impossible "*0";
      * a corrupt or truncated field, which crypt reads as some other
        algorithm entirely and answers in a form that cannot equal the stored
        string.

    A working crypt always echoes the SALT back in its answer, so the test is
    that the answer carries the same salt the stored string does."""
    if not stored:
        return False
    try:
        import crypt                                           # noqa: PLC0415
    except Exception:                                          # noqa: BLE001
        return False
    try:
        probe = crypt.crypt(_PROBE, stored)
    except Exception:                                          # noqa: BLE001
        return False
    if not isinstance(probe, str) or not probe or probe.startswith("*"):
        return False
    if stored.startswith("$"):
        # "$id$params$salt$hash": everything before the LAST "$" is the salt,
        # and crypt reproduces it exactly.
        return probe.rsplit("$", 1)[0] == stored.rsplit("$", 1)[0]
    # Traditional DES: a two-character salt and a thirteen-character answer.
    return len(probe) == len(stored) and probe[:2] == stored[:2]


def has_password(user=None):
    """Is there a password that CAN be checked?

    False for an empty field, for a locked account (`!`, `*`, `!!`), for an
    unreadable shadow file, and — the part that shape-matching missed — for any
    stored string this machine's crypt cannot match against typed input. Every
    one of those means "do not show a sign-in screen", because none of them can
    ever be satisfied by typing, and a screen that cannot be satisfied is a
    brick, not security."""
    h = _shadow_hash(user or desktop_user())
    if h is None or h == "":
        return False
    if h.startswith("!") or h.startswith("*"):
        return False
    return _can_verify(h)


def verify(user, password):
    """True if `password` is this account's password.

    Uses crypt with the STORED HASH as the salt, which is how a modern crypt
    string carries its own algorithm and parameters — so this keeps working if
    the installer ever changes the hash type."""
    stored = _shadow_hash(user)
    if not stored or stored.startswith("!") or stored.startswith("*"):
        return False
    try:
        import crypt                                           # noqa: PLC0415
        return crypt.crypt(password, stored) == stored
    except Exception:                                          # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Everything ABOVE this line answers "would a sign-in screen appear?" using
# nothing but the standard library, so session.sh can ask that question for the
# price of an interpreter start. Everything BELOW draws the screen.
# ---------------------------------------------------------------------------
if __name__ == "__main__" and "--needed" in sys.argv:
    raise SystemExit(0 if has_password() else 1)

import gi                                                      # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango                # noqa: E402

DE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DE_DIR)

import nbapp                                                   # noqa: E402
import nbicons                                                 # noqa: E402, F401

# The sign-in screen has to come up even if the desktop tree is damaged: a
# catalog this cannot read must cost the machine its translations, never its
# way in. splash.py guards the same import for the same reason.
try:
    from nbi18n import _t                                      # noqa: E402
except Exception:                                              # noqa: BLE001
    def _t(s):
        return s


class Login(Gtk.Window):
    """The sign-in screen. Deliberately not an nbapp.AppWindow: it has no menu
    bar, must not register as a running app, and must sit above everything."""

    def __init__(self, lock=False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.lock = lock
        self.user = desktop_user()
        self._tries = 0

        self.set_decorated(False)
        self.set_app_paintable(False)
        nbapp.force_opaque_visual(self)
        # A FULLSCREEN APP WINDOW — deliberately NOT a splash-type window.
        #
        # This screen carried _NET_WM_WINDOW_TYPE_SPLASH, and matchbox's
        # wm.c routes that atom straight to dialog_client_new(): it becomes a
        # DIALOG. Our own WM patch (package/matchbox/0003-panel-menu-bar-
        # above-dialogs) then stacks the desktop's menu bar — a DOCK — ABOVE
        # every dialog on screen, and it keeps its hands off only when a mapped
        # MBCLIENT_TYPE_APP holds _NET_WM_STATE_FULLSCREEN. So on the LOCK
        # screen, which is raised over a running desktop, the menu bar sat on
        # top of it: the clock, the app menus and Shut Down were all visible
        # and clickable over a screen whose entire job is to reveal nothing.
        #
        # A plain toplevel with no type hint is MBCLIENT_TYPE_APP, and
        # fullscreen() sets the state the patch looks for, so the panel falls
        # back behind us. It also puts this window in matchbox's single-app
        # slot, which unmaps the user's open windows for the duration — for a
        # lock screen that is the correct behaviour, not a side effect — and
        # main_client_unmap() re-activates the app below when we exit.
        # de/nbgame.py reached the same conclusion for the same reason.
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)

        # ASK FOR THE SCREEN'S SIZE OUTRIGHT, do not rely on fullscreen().
        #
        # Measured on an installed machine at 1920x1080: this screen came up as
        # a ~350x365 panel in the TOP-LEFT CORNER with a hairline frame, on the
        # bare desktop field, with the footer line clipped at its edge — the
        # first thing anyone saw on their own computer. fullscreen() is only a
        # REQUEST, and matchbox acts on _NET_WM_STATE_FULLSCREEN in
        # main_client_check_for_state_hints(), i.e. for MBCLIENT_TYPE_APP
        # clients only; as a splash/dialog client this window was simply given
        # the size it asked for, which was the natural size of its centre
        # column. Nothing had booted an installed system, and the live ISO has
        # no password, so the screen that only appears after an install was the
        # one screen never seen.
        #
        # The type hint above is half the fix. This is the other half, and it
        # is the half that does not depend on a window manager at all:
        # splash.py has always done exactly this, with the same comment, which
        # is why the loading screen filled the display and this did not.
        self._fit_screen()
        self.fullscreen()
        self.connect("destroy", Gtk.main_quit)
        # Escape must NOT dismiss a lock screen; that would make it decorative.
        self.connect("key-press-event", self._on_key)
        # matchbox honours EWMH state requests only AFTER a window is mapped
        # (splash.py carries the same note for the same reason), and at boot
        # this screen maps into a session that may still have the full-screen
        # loading screen up. Re-assert once we are on screen so the sign-in
        # prompt is never the thing underneath.
        self.connect("map-event", self._on_map)

        self._build()
        self._install_css()
        GLib.timeout_add_seconds(30, self._tick_clock)

    # -- ui ------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet, and this is the first screen anyone sees.
        css = b"""
        .lg-root { background: #FCFBF8; }
        .lg-root * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .lg-time { font-size: 64px; font-weight: 300; color: #1A1916; }
        .lg-date { font-size: 15px; color: #6E695E; letter-spacing: 0.02em; }
        .lg-who { font-size: 19px; color: #1A1916; }
        .lg-prompt { font-size: 13px; color: #6E695E; }
        .lg-field { font-size: 15px; padding: 10px 14px; color: #1A1916;
                    background: #FFFFFF; border: 1px solid #C9C4B6;
                    border-radius: 3px; }
        .lg-field:focus { border-color: #6E695E; }
        /* The one accent on this screen means exactly one thing: that did not
           work. Nothing else here is red. */
        .lg-field.wrong { border-color: #C8341E; }
        .lg-error { font-size: 13px; color: #C8341E; }
        .lg-hint { font-size: 12px; color: #9A9484; }
        .lg-recall { font-size: 12.5px; color: #6E695E; }
        .lg-show { font-size: 12.5px; color: #6E695E; }
        .lg-go { background: #1A1916; border: 1px solid #1A1916;
                 border-radius: 3px; padding: 10px 26px; font-size: 14px;
                 color: #FCFBF8; box-shadow: none; }
        .lg-go:hover { background: #33302A; border-color: #33302A; }
        /* The colour MUST be set on the label node, not just the button: a
           colour inherited by the label loses to anything targeting the label
           itself, which is how the shared file picker shipped a grey-on-red
           Save button. Here it rendered ink-on-ink and the word vanished. */
        .lg-go label { color: #FCFBF8; }
        .lg-go:disabled label { color: #9A9484; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                      # noqa: BLE001
            pass          # styling is cosmetic; never block sign-in

    def _build(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.get_style_context().add_class("lg-root")
        self.add(root)

        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        centre.set_valign(Gtk.Align.CENTER)
        centre.set_halign(Gtk.Align.CENTER)
        # A margin on both sides so nothing on this screen can ever be read off
        # the edge of the glass, at any size the display turns out to be.
        centre.set_margin_start(24)
        centre.set_margin_end(24)
        root.pack_start(centre, True, True, 0)

        # The clock is the anchor: on a machine with no network it is also the
        # quickest way to see the time is wrong before you trust anything else.
        self.clock = Gtk.Label(xalign=0.5)
        self.clock.get_style_context().add_class("lg-time")
        centre.pack_start(self.clock, False, False, 0)
        self.date = Gtk.Label(xalign=0.5)
        self.date.get_style_context().add_class("lg-date")
        self.date.set_margin_top(2)
        centre.pack_start(self.date, False, False, 0)
        self._tick_clock()

        # The real brand mark the panel uses, not an app glyph — there is no
        # "snail" in nbicons and the fallback would have been the
        # unrecognised-file icon, on the first screen anyone ever sees.
        logo = None
        try:
            pb = nbapp._logo_pixbuf()
            if pb is not None:
                logo = Gtk.Image.new_from_pixbuf(pb)
        except Exception:
            logo = None
        if logo is None:
            logo = Gtk.Label(label=_t("Notebook OS"))
            logo.get_style_context().add_class("lg-who")
        logo.set_margin_top(46)
        centre.pack_start(logo, False, False, 0)

        who = Gtk.Label(label=display_name(self.user), xalign=0.5)
        who.get_style_context().add_class("lg-who")
        who.set_margin_top(12)
        centre.pack_start(who, False, False, 0)

        prompt = Gtk.Label(
            label=_t("Locked") if self.lock else _t("Password"),
            xalign=0.5)
        prompt.get_style_context().add_class("lg-prompt")
        prompt.set_margin_top(3)
        centre.pack_start(prompt, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_visibility(False)
        self.entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.entry.set_width_chars(24)
        self.entry.set_alignment(0.5)
        self.entry.set_activates_default(True)
        self.entry.get_style_context().add_class("lg-field")
        self.entry.set_margin_top(22)
        self.entry.connect("activate", self._try)
        self.entry.connect("changed", self._clear_error)
        centre.pack_start(self.entry, False, False, 0)

        # Typing a password you cannot see, on the one screen that will not let
        # you past, is where a first morning is actually lost. Both of these
        # appear only after an attempt has failed, so nothing clutters the
        # screen for the person who simply types it correctly — and neither
        # weakens anything: on a single-seat offline machine, being able to
        # read back what you typed is the difference between getting in and
        # not, and the reminder names no secret, only where the secret came
        # from. The installer's own password page offers the same tick.
        self.error = Gtk.Label(xalign=0.5)
        self.error.get_style_context().add_class("lg-error")
        self.error.set_margin_top(8)
        self.error.set_no_show_all(True)
        centre.pack_start(self.error, False, False, 0)

        self._show = Gtk.CheckButton(label=_t("Show password"))
        self._show.get_style_context().add_class("lg-show")
        self._show.set_halign(Gtk.Align.CENTER)
        self._show.set_margin_top(10)
        self._show.set_no_show_all(True)
        self._show.connect("toggled", self._on_show_toggle)
        centre.pack_start(self._show, False, False, 0)

        self._recall = Gtk.Label(xalign=0.5)
        self._recall.get_style_context().add_class("lg-recall")
        self._recall.set_margin_top(10)
        self._recall.set_line_wrap(True)
        self._recall.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._recall.set_max_width_chars(46)
        # xalign centres the BLOCK; justify centres the lines within it. Every
        # other line on this screen is centred, and translated this one wraps
        # to two or three lines in most languages.
        self._recall.set_justify(Gtk.Justification.CENTER)
        self._recall.set_no_show_all(True)
        centre.pack_start(self._recall, False, False, 0)

        go = Gtk.Button(label=_t("Unlock") if self.lock else _t("Sign In"))
        go.get_style_context().add_class("lg-go")
        go.set_halign(Gtk.Align.CENTER)
        go.set_margin_top(18)
        go.set_can_default(True)
        go.connect("clicked", self._try)
        centre.pack_start(go, False, False, 0)
        self._go = go

        # No standing footer here. It read "This computer is not connected
        # to anything. Your password never leaves it." -- a reassurance nobody
        # asked for, about a fact the sign-in screen is not the place to argue,
        # sitting under the one control the screen exists for. The recall hint
        # that appears AFTER a wrong password is kept: that one is asked for,
        # by getting it wrong.

    def do_realize(self):
        Gtk.Window.do_realize(self)
        self.set_default(self._go)
        self.entry.grab_focus()

    def _fit_screen(self):
        """Cover the whole display, whatever the window manager does or does
        not do about it. Returns the size used, so callers can re-apply it."""
        w = h = 0
        try:
            scr = Gdk.Screen.get_default()
            if scr is not None:
                w, h = scr.get_width(), scr.get_height()
        except Exception:                                      # noqa: BLE001
            w = h = 0
        if not (w > 1 and h > 1):
            # Last resort. Too big is recoverable (the card stays centred in
            # the visible part); too small is the bug this exists to prevent.
            w, h = 1920, 1080
        try:
            self.set_default_size(w, h)
            self.resize(w, h)
            self.move(0, 0)
        except Exception:                                      # noqa: BLE001
            pass
        return w, h

    def _on_map(self, *_a):
        # See the connect() above: matchbox only acts on these once the window
        # is mapped, and the keyboard has to land HERE and nowhere else. The
        # size is re-applied too — a WM that reparents on map can hand back a
        # geometry of its own choosing, and this window must not accept one.
        for call in (self._fit_screen, self.fullscreen,
                     lambda: self.set_keep_above(True),
                     self.present, self.entry.grab_focus):
            try:
                call()
            except Exception:                                  # noqa: BLE001
                pass
        return False

    def _tick_clock(self, *_a):
        now = time.localtime()
        self.clock.set_text(time.strftime("%H:%M", now))
        self.date.set_text("%s %d %s" % (_t(DAY_NAMES[now.tm_wday]),
                                         now.tm_mday,
                                         _t(MONTHS[now.tm_mon - 1])))
        return True

    # -- actions -------------------------------------------------------------

    def _clear_error(self, *_a):
        self.entry.get_style_context().remove_class("wrong")
        self.error.hide()

    def _on_show_toggle(self, btn):
        self.entry.set_visibility(btn.get_active())
        self.entry.grab_focus()

    def _on_key(self, _w, ev):
        # Escape is swallowed: a lock screen you can dismiss is decoration.
        if ev.keyval in (Gdk.KEY_Escape,):
            return True
        return False

    def _try(self, *_a):
        if verify(self.user, self.entry.get_text()):
            self.hide()
            Gtk.main_quit()
            return
        self._tries += 1
        self.entry.set_text("")
        self.entry.get_style_context().add_class("wrong")
        self.error.set_text(_t("That password did not work."))
        self.error.show()
        # First failure: offer to show what is being typed, and say where the
        # password came from. Somebody who has forgotten it has no other route
        # back into this machine, so the one true reminder we have is worth
        # more than a tidier screen.
        self._show.set_no_show_all(False)
        self._show.show()
        self._recall.set_text(
            _t("The administrator password set when Notebook OS was "
               "installed."))
        self._recall.set_no_show_all(False)
        self._recall.show()
        self.entry.grab_focus()
        # A short, growing pause after repeated failures. Not a lockout — being
        # locked out of your own offline computer is a worse outcome than a
        # slow guess — just enough that guessing is not free.
        if self._tries >= 3:
            self.entry.set_sensitive(False)
            self._go.set_sensitive(False)
            GLib.timeout_add_seconds(min(5, self._tries), self._re_enable)

    def _re_enable(self):
        self.entry.set_sensitive(True)
        self._go.set_sensitive(True)
        self.entry.grab_focus()
        return False


def main():
    lock = "--lock" in sys.argv
    user = desktop_user()
    # Nothing to ask for: no password set, account locked, or shadow unreadable.
    # Start the desktop rather than stranding somebody at a prompt that cannot
    # be satisfied.
    if not has_password(user):
        return 0
    nbapp.install_css()
    win = Login(lock=lock)
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
