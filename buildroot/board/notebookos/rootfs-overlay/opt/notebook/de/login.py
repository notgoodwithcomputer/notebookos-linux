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

IT MUST BE POSSIBLE TO TYPE THE PASSWORD
The second way this screen can strand somebody has nothing to do with the hash.
On a Russian, Greek, Hindi or Yiddish machine the saved layout is a DUAL one
("ru,us"), and the half that is live when the keymap loads is the non-Latin
one. A password made of Latin letters — which is what anybody has who set the
machine up in English and changed the language afterwards, or who pressed
Alt+Shift while choosing it — cannot be typed at this prompt at all. The field
is masked, so the keys produce Cyrillic invisibly and the only fact on screen
is "that password did not work", every time, forever. Nothing here said which
alphabet the keys were producing, and nothing offered the other one.

So this screen now shows the live keyboard group and can switch it (see
`_build_kbd`), guarantees a group that can type ASCII exists at all (kana had
none), names the script in the failure message, and REMEMBERS which half the
sign-in succeeded on so it is the default next time.

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
import subprocess
import time

SHADOW = "/etc/shadow"
PASSWD = "/etc/passwd"

INK = "#1A1916"
MUTED = "#6E695E"
PAPER = "#FCFBF8"

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
            # Cut only what could not be a name at all -- a binary file, a
            # runaway one -- and never at a tidy-looking length: a 49-character
            # name came out as "Maximilian Bartholomew Fitzgerald-Worthi", a
            # hard stop mid-word with nothing to say it was a stop. The
            # greeting label ellipsizes instead (see _build), which is what a
            # cut is supposed to look like.
            return got[:120]
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
    return _password_state(user or desktop_user()) == "password"


def _password_state(user):
    """Distinguish intentional passwordless state from credential failure."""
    h = _shadow_hash(user)
    if h is None:
        return "error"
    if h == "" or h.startswith("!") or h.startswith("*"):
        return "none"
    return "password" if _can_verify(h) else "error"


def _may_skip_login(user, lock=False, state=None):
    state = _password_state(user) if state is None else state
    return state == "none" or (state == "error" and not lock)


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
gi.require_version("Atk", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, Atk           # noqa: E402

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
try:
    import nbi18n                                              # noqa: E402
except Exception:                                              # noqa: BLE001
    nbi18n = None
try:
    import nbkeyboard                                          # noqa: E402
except Exception:                                              # noqa: BLE001
    nbkeyboard = None


class Login(Gtk.Window):
    """The sign-in screen. Deliberately not an nbapp.AppWindow: it has no menu
    bar, must not register as a running app, and must sit above everything."""

    def __init__(self, lock=False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.lock = lock
        self.user = desktop_user()
        self._tries = 0
        # THESE THREE EXIST BEFORE ANY CALLBACK CAN RUN, which is why they are
        # here rather than beside the code that arms them: _build() below ticks
        # the clock directly, and a destroy can arrive from the window manager
        # at any moment after that. A timer callback that finds its own id
        # missing cannot clear it, and a handler that has to ask whether the
        # window is gone must never be the thing that decides it is not.
        self._wait_id = 0       # the pause after repeated failures, if pending
        self._count_id = 0      # the 1s tick that counts that pause down
        self._wait_left = 0     # seconds of that pause still to run
        self._clock_id = 0      # the every-30s clock tick, once armed
        self._closed = False    # this window is being torn down
        self.ok = False

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
        # NOT Gtk.main_quit directly: the timers this window arms outlive the
        # widgets they touch. See _on_destroy.
        self.connect("destroy", self._on_destroy)
        # A window-manager close (Alt+F4 / _NET_CLOSE_WINDOW) is not a sign-in.
        # Escape is handled below, but without a delete-event veto the WM could
        # destroy this window, end Gtk.main(), and reveal the running desktop.
        self.connect("delete-event", self._on_delete)
        # Escape must NOT dismiss a lock screen; that would make it decorative.
        self.connect("key-press-event", self._on_key)
        # matchbox honours EWMH state requests only AFTER a window is mapped
        # (splash.py carries the same note for the same reason), and at boot
        # this screen maps into a session that may still have the full-screen
        # loading screen up. Re-assert once we are on screen so the sign-in
        # prompt is never the thing underneath.
        self.connect("map-event", self._on_map)
        # A group switch made with Alt+Shift has to reach the indicator, or it
        # says one alphabet while the keys type another — which is the whole
        # defect, moved. The group is read off the events themselves because X
        # reports the live group nowhere else a client can poll.
        self.connect("key-release-event", self._on_key_release)

        self._setup_keyboard()
        self._build()
        self._install_css()
        # Kept, so the tear-down below has something to cancel. Fired and
        # forgotten, this timer outlives the window and goes on setting text on
        # labels that are no longer there.
        self._clock_id = GLib.timeout_add_seconds(30, self._tick_clock)

    # -- ui ------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet, and this is the first screen anyone sees.
        css = b"""
        .lg-root { background: #FCFBF8; }
        .lg-root * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .lg-time { font-size: 64px; font-weight: 300; color: #1A1916; }
        .lg-date { font-size: 15px; color: #6E695E; letter-spacing: 0.02em; }
        .lg-who { font-size: 20px; color: #1A1916; }
        .lg-prompt { font-size: 13px; color: #6E695E; }
        .lg-field { font-size: 15px; padding: 10px 14px; color: #1A1916;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; }
        .lg-field:focus { border-color: #6E695E; }
        /* The one accent on this screen means exactly one thing: that did not
           work. Nothing else here is red. */
        .lg-field.wrong { border-color: #C8341E; }
        /* Held: after repeated failures this field stops accepting keys for a
           few seconds, and it used to look EXACTLY like a field that accepts
           them -- measured, the two crops were the same bytes -- so every
           keystroke vanished with nothing on screen to explain it. Last of the
           field rules on purpose: at equal specificity this wins over .wrong,
           because while the pause runs "not listening" is the more useful
           fact and the red sentence below still says why. */
        .lg-field:disabled { color: #9A9484; background: #F5F2EA;
                             border-color: #DEDACC; }
        .lg-error { font-size: 13px; color: #C8341E; }
        .lg-hint { font-size: 12px; color: #9A9484; }
        .lg-recall { font-size: 12px; color: #6E695E; }
        /* The alphabet switch. Two quiet chips, the live one filled: the
           same ink-on-paper pair the Sign In button uses, at half the weight,
           because this is a fact about the field above and not a second
           thing to press. (ASCII only in here, as the note above says.) */
        .lg-kbdcap { font-size: 12px; color: #6E695E; }
        .lg-kbd button { padding: 3px 12px; font-size: 12px;
                         background: #FCFBF8; border: 1px solid #C9C4B6;
                         border-radius: 8px; box-shadow: none; }
        .lg-kbd button label { color: #1A1916; }
        .lg-kbd button:checked { background: #1A1916; border-color: #1A1916; }
        .lg-kbd button:checked label { color: #FCFBF8; }
        .lg-show { font-size: 12px; color: #6E695E; }
        .lg-go { background: #1A1916; border: 1px solid #1A1916;
                 border-radius: 8px; padding: 10px 26px; font-size: 14px;
                 color: #FCFBF8; box-shadow: none; }
        .lg-go:hover { background: #3A362E; border-color: #3A362E; }
        /* The colour MUST be set on the label node, not just the button: a
           colour inherited by the label loses to anything targeting the label
           itself, which is how the shared file picker shipped a grey-on-red
           Save button. Here it rendered ink-on-ink and the word vanished. */
        .lg-go label { color: #FCFBF8; }
        .lg-go:disabled label { color: #9A9484; }
        /* The way out, at the foot of the screen and deliberately quiet: it is
           not what this screen is for. Without it somebody who cannot remember
           the password -- or who meets the lock screen on a machine whose
           credential file cannot be read, where nothing typed can ever be
           accepted -- has no way to stop this computer but the mains.
           The class goes on each BUTTON rather than on a box around them:
           the one button down there that is NOT quiet is the confirm, which
           carries .lg-go, and a `.lg-pwr button` descendant rule outranks a
           plain class and would paint it quiet too. */
        .lg-pwr { background: transparent; background-image: none;
                  border: 1px solid transparent; border-radius: 8px;
                  padding: 6px 12px; font-size: 12px; box-shadow: none; }
        .lg-pwr label { color: #6E695E; }
        .lg-pwr:hover { border-color: #C9C4B6; }
        /* ...and the question those buttons ask before they act. */
        .lg-ask { font-size: 12px; color: #6E695E; }
        /* The confirm in that strip: the Sign In weight (.lg-go, listed
           above) at the foot's own size, so the strip is exactly as tall
           with the question in it as with the two buttons, and the column
           above it does not move when the question opens. Padding, not a
           size group -- a GtkSizeGroup ignores the strip that is hidden,
           which is always one of these two. */
        .lg-pwrgo { padding: 6px 16px; font-size: 12px; }
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
        root = self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
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
        self._build_power(root)

        # NOTHING ABOVE THE FIELD MAY MOVE.
        #
        # Everything this screen says back to a wrong password appears BELOW
        # the field, and this column is centred on the glass, so every one of
        # those lines used to lift the clock, the name and the field itself:
        # measured at 44px up on the first failure, and 11px back down on the
        # next keystroke, i.e. the screen reflowing under the caret at the
        # moment somebody is being told they were wrong.
        #
        # This empty box carries the same height as that answer block, above
        # the clock, so the two cancel exactly: the block grows downward, the
        # button below it steps down, and the top of the column stays where it
        # was. See _balance_answer.
        self._ballast = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        centre.pack_start(self._ballast, False, False, 0)

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

        # The person's OWN name, out of /etc/notebookos-user. This screen
        # already refuses to translate the keyboard-layout buttons for the
        # same reason; a greeting that renames somebody called May, Grace or
        # Sunny is the first thing they see every morning.
        who = Gtk.Label(xalign=0.5)
        self._set_verbatim(who, display_name(self.user))
        who.get_style_context().add_class("lg-who")
        who.set_margin_top(12)
        # A name too long for the column ends in an ellipsis rather than
        # stopping mid-word, and -- with the field pinned below -- it no longer
        # drags the password field out to its own width on the way.
        who.set_ellipsize(Pango.EllipsizeMode.END)
        who.set_max_width_chars(30)
        centre.pack_start(who, False, False, 0)

        prompt = Gtk.Label(
            label=_t("Locked") if self.lock else _t("Password"),
            xalign=0.5)
        prompt.get_style_context().add_class("lg-prompt")
        prompt.set_margin_top(3)
        centre.pack_start(prompt, False, False, 0)

        self.entry = Gtk.Entry()
        # The lock heading says "Locked", which describes the screen rather
        # than the field.  Give the authentication control its own stable name
        # so assistive technology never has to infer it from visual proximity.
        self.entry.get_accessible().set_name(_t("Password"))
        self.entry.set_visibility(False)
        self.entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.entry.set_width_chars(24)
        self.entry.set_alignment(0.5)
        # ITS OWN WIDTH, not the column's. Every child of this box is stretched
        # to the box by default, so the field took the width of whatever the
        # widest line under it happened to be: 222px at launch, 303px the
        # moment the reminder appeared beneath it, 370px beside a long name,
        # and a different number again in every language. A password field
        # that changes size while somebody is typing into it is the machine
        # flinching.
        self.entry.set_halign(Gtk.Align.CENTER)
        # NOT set_activates_default(True). With it, ONE Enter submitted TWICE:
        # the "activate" handler below runs first, and GtkEntry's own default
        # handler for that same signal then activates the default widget --
        # which is the Sign In button, connected to the same _try. So a single
        # wrong Enter counted as two failed attempts, the pause meant for the
        # third one arrived on the second, and a right one paid for a second
        # SHA-512 crypt after the screen had already gone. The "activate"
        # signal is emitted on Enter either way, so nothing is lost: this
        # removes the duplicate path, not the key.
        self.entry.get_style_context().add_class("lg-field")
        self.entry.set_margin_top(22)
        self.entry.connect("activate", self._try)
        self.entry.connect("changed", self._clear_error)
        centre.pack_start(self.entry, False, False, 0)

        # Directly under the field, because the alphabet the keys are in is a
        # fact about what is going INTO it, and because somebody who needs it
        # needs it before they type rather than after they have failed.
        self._build_kbd(centre)

        # Typing a password you cannot see, on the one screen that will not let
        # you past, is where a first morning is actually lost. Both of these
        # appear only after an attempt has failed, so nothing clutters the
        # screen for the person who simply types it correctly — and neither
        # weakens anything: on a single-seat offline machine, being able to
        # read back what you typed is the difference between getting in and
        # not, and the reminder names no secret, only where the secret came
        # from. The installer's own password page offers the same tick.
        #
        # EVERYTHING THIS SCREEN SAYS BACK GOES IN HERE, so that its height
        # is one number the ballast at the top of the column can carry. The
        # ballast above the clock is set to whatever this box grows to, the
        # column grows by twice that, and — being centred — its top ends up
        # exactly where it started. Nothing above the field moves. See
        # _balance_answer, which is called wherever this box changes.
        self._answer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        centre.pack_start(self._answer, False, False, 0)

        self.error = Gtk.Label(xalign=0.5)
        self.error.get_style_context().add_class("lg-error")
        self.error.set_margin_top(8)
        self.error.set_no_show_all(True)
        self.error.get_accessible().set_role(Atk.Role.ALERT)
        self._answer.pack_start(self.error, False, False, 0)

        # While the field is held after repeated failures it is not accepting
        # keys, and a held field that says nothing is a keyboard that appears
        # to have died: measured, every keystroke during the pause vanished
        # with nothing on screen about where it went. The greyed field says
        # "not now" (see .lg-field:disabled); this says how long for.
        self._wait = Gtk.Label(xalign=0.5)
        self._wait.get_style_context().add_class("lg-hint")
        self._wait.set_margin_top(6)
        self._wait.set_no_show_all(True)
        self._answer.pack_start(self._wait, False, False, 0)

        self._show = Gtk.CheckButton(label=_t("Show password"))
        self._show.get_style_context().add_class("lg-show")
        self._show.set_halign(Gtk.Align.CENTER)
        self._show.set_margin_top(10)
        self._show.set_no_show_all(True)
        self._show.connect("toggled", self._on_show_toggle)
        self._answer.pack_start(self._show, False, False, 0)

        # TWO ANSWERS, TWO LABELS. The alphabet sentence and the reminder are
        # answers to two different questions ("why did that fail" and "what
        # was the password"), and joined with a single newline inside ONE
        # centred label they rendered as a single run-on block — four centred
        # lines in ru/ja/pl/de with nothing to mark where the subject changes.
        # Separate labels put a paragraph's worth of space between them, and
        # let the alphabet sentence appear on its own when it is the only one
        # that applies.
        self._kbdnote = self._make_recall_label()
        self._answer.pack_start(self._kbdnote, False, False, 0)
        self._recall = self._make_recall_label()
        self._answer.pack_start(self._recall, False, False, 0)

        go = Gtk.Button(label=_t("Unlock") if self.lock else _t("Sign In"))
        go.get_style_context().add_class("lg-go")
        go.set_halign(Gtk.Align.CENTER)
        go.set_margin_top(18)
        go.set_can_default(True)
        go.connect("clicked", self._try)
        centre.pack_start(go, False, False, 0)
        self._go = go

        # No standing footer of REASSURANCE here. It read "This computer is
        # not connected to anything. Your password never leaves it." -- a
        # reassurance nobody asked for, about a fact the sign-in screen is not
        # the place to argue, sitting under the one control the screen exists
        # for. The recall hint that appears AFTER a wrong password is kept:
        # that one is asked for, by getting it wrong. So is the power row at
        # the foot, which is a control and not a claim.

    def _balance_answer(self):
        """Carry the answer block's height above the clock as well.

        The block grows downward from the field; the ballast at the top of the
        column grows by the same amount; the column is centred, so it now
        grows by twice as much and its top stays exactly where it was. That is
        the whole trick, and it is why this is called from every place the
        block changes -- the failure, the countdown ending, the error clearing
        on the next keystroke (which is the 11px the column used to drop back
        while somebody was already typing).

        AS MUCH OF IT AS THE SCREEN CAN AFFORD. Doubled, a tall answer in a
        wordy language ran the Sign In button into the power row at the foot:
        Japanese, one failure, 1024x740, measured. A collision is worse than a
        shift, so the ballast is capped at the space actually left and the
        column gives up whatever it must -- from the top, evenly, rather than
        by overlapping anything. Measured per language and per screen, because
        no constant is right for both.
        """
        try:
            self._ballast.set_size_request(-1, 0)
            want = self._answer.get_preferred_height()[1]
            avail = self._root.get_allocated_height()
            if avail < 2:
                avail = self._fit_screen()[1]
            used = self._root.get_preferred_height()[1]
            self._ballast.set_size_request(-1, max(0, min(want, avail - used)))
        except Exception:                                      # noqa: BLE001
            pass                    # unmeasurable: the column moves as before

    @staticmethod
    def _set_paragraph(lbl, text):
        """Put `text` on `lbl`, wrapped into even lines.

        Pango wraps greedily: it fills each line to the width it is given and
        leaves whatever is left over on the last one. For the English
        reminder that last one was the single word "installed." — under a
        centred block a one-word line reads as something gone wrong rather
        than the end of a sentence, and ja left "です。" the same way.

        So the width is MEASURED rather than picked: how many lines does this
        text need at the widest this screen allows, and what is the narrowest
        wrap that still needs no more than that many? That width is the one
        where the lines come out even, and it is a different number in every
        language — which is exactly why it cannot be a constant. Nothing is
        hidden and no line is added; the block just comes out square.
        """
        lbl.set_text(text)
        try:
            ctx = lbl.get_pango_context()
            # NOT None: the shipped Pango (1.50) has no (nullable) annotation
            # on this argument and dereferences the NULL. A segfault HERE is
            # the login screen, i.e. an unusable machine. See novel.py's
            # _sync_placeholder_position for the measurement.
            _lang = ctx.get_language() or Pango.Language.get_default()
            char = (ctx.get_metrics(ctx.get_font_description(), _lang)
                    .get_approximate_char_width() / Pango.SCALE)
            cap = int(lbl.get_max_width_chars() * char)
            lay = lbl.create_pango_layout(text)
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
            lay.set_width(-1)
            if cap <= 0 or lay.get_pixel_size()[0] <= cap:
                return                     # one line: nothing to even out
            lay.set_width(cap * Pango.SCALE)
            want = lay.get_line_count()
            lo, hi, best = 1, cap, cap
            while lo <= hi:                # the narrowest wrap of `want` lines
                mid = (lo + hi) // 2
                lay.set_width(mid * Pango.SCALE)
                if lay.get_line_count() <= want:
                    best, hi = mid, mid - 1
                else:
                    lo = mid + 1
            lay.set_width(best * Pango.SCALE)
            raw = text.encode("utf-8")
            out = []
            for i in range(lay.get_line_count()):
                ln = lay.get_line(i)
                out.append(raw[ln.start_index:ln.start_index + ln.length]
                           .decode("utf-8", "replace").strip())
            # The breaks are made HERE because they were measured here: the
            # label is laid out at whatever width the column turns out to be,
            # and that width is a different one in every language.
            lbl.set_text("\n".join(out))
        except Exception:                                      # noqa: BLE001
            pass          # unmeasurable: the label wraps as it always did

    @staticmethod
    def _make_recall_label():
        """One paragraph of the answer under a wrong password."""
        lbl = Gtk.Label(xalign=0.5)
        lbl.get_style_context().add_class("lg-recall")
        lbl.set_margin_top(10)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Wide enough that the English reminder does not leave its last word
        # alone on a line, narrow enough that the block stays a column rather
        # than a banner. Measured at this size in en/de/ru/ja.
        lbl.set_max_width_chars(46)
        # xalign centres the BLOCK; justify centres the lines within it. Every
        # other line on this screen is centred, and translated this one wraps
        # to two or three lines in most languages.
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_no_show_all(True)
        return lbl

    def _build_power(self, root):
        """Restart and Shut Down, quietly, at the foot of the screen.

        Until now there were none, on either surface. Somebody who cannot
        produce the password — forgotten it, or met the lock screen on a
        machine whose credential file cannot be read, where NOTHING typed can
        ever be accepted — had no way to stop this computer but holding the
        mains button in; the image ships no acpid, so even a tap of the power
        key does nothing. main()'s own comment already promised these ("offers
        its normal power controls") and _build never made them.

        Both ask first, exactly as the desktop's menu does: on the lock screen
        there is a session underneath this one with unsaved work in it. The
        question REPLACES the two buttons in the same strip rather than
        opening under them — this screen's rule is that nothing above the
        field may move, and a taller foot moves the column with it.
        """
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        foot.set_halign(Gtk.Align.CENTER)
        foot.set_margin_bottom(28)
        foot.set_margin_start(24)
        foot.set_margin_end(24)

        self._pwr_mode = ""
        self._pwr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=6)
        for verb, mode in (("Restart", "reboot"), ("Shut Down", "poweroff")):
            b = Gtk.Button(label=_t(verb + "\u2026"))
            b.get_style_context().add_class("lg-pwr")
            b.connect("clicked", self._ask_power, mode, verb)
            self._pwr_row.pack_start(b, False, False, 0)
        foot.pack_start(self._pwr_row, False, False, 0)

        self._pwr_ask = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=8)
        self._pwr_ask.set_no_show_all(True)
        self._pwr_q = Gtk.Label(xalign=0.5)
        self._pwr_q.get_style_context().add_class("lg-ask")
        # One line, whatever the language: wrapping it would make this strip
        # taller than the one it replaces, which is the movement above the
        # field this screen does not allow.
        self._pwr_q.set_ellipsize(Pango.EllipsizeMode.END)
        self._pwr_q.set_max_width_chars(72)
        self._pwr_ask.pack_start(self._pwr_q, False, False, 0)
        no = Gtk.Button(label=_t("Cancel"))
        no.get_style_context().add_class("lg-pwr")
        no.connect("clicked", self._cancel_power)
        self._pwr_ask.pack_start(no, False, False, 0)
        self._pwr_yes = Gtk.Button()
        self._pwr_yes.get_style_context().add_class("lg-go")
        # ...at the foot's size, so this strip is exactly as tall as the two
        # buttons it replaces: the column above is centred in what is left of
        # the screen, and four pixels of foot moved the clock four pixels.
        self._pwr_yes.get_style_context().add_class("lg-pwrgo")
        self._pwr_yes.connect("clicked", self._confirm_power)
        self._pwr_ask.pack_start(self._pwr_yes, False, False, 0)
        foot.pack_start(self._pwr_ask, False, False, 0)
        root.pack_end(foot, False, False, 0)

    # -- keyboard ------------------------------------------------------------
    #
    # THE PASSWORD HAS TO BE TYPEABLE — see the header. Every method here is
    # guarded down to a no-op: a machine that cannot answer "which layout?"
    # must still show a prompt, because no prompt at all is the brick.

    def _setup_keyboard(self):
        """Decide which keyboard groups this screen offers, and load them.

        Three separate measured failures are handled here, in this order:

          1. A US QWERTY group is GUARANTEED to exist, on EVERY language.
             This started as "a group that can type ASCII", which "jp(kana)"
             had none of — its letter keys produce kana. That guarantee was
             too weak: it is satisfied by any layout holding the ASCII
             alphabet ANYWHERE, and where the characters sit is the whole
             question at a masked prompt. Plain "jp" passed it and still
             locked a Japanese machine out, because JIS keeps _ \\ | on the RO
             key that ANSI hardware does not have. See nbkeyboard.
             ensure_qwerty for the measurement across all 17 languages.
          2. The half the last successful sign-in was typed on is made the
             live one. Without it, somebody whose password is Latin and whose
             interface is Russian has to press Alt+Shift before typing on
             every boot for the life of the machine, with nothing to remind
             them that they must.
          3. The keymap is loaded ONLY if it differs from what session.sh has
             already put on the server. setxkbmap forks xkbcomp to compile a
             keymap, and this is a screen somebody is waiting in front of.

        ...and the load is BELIEVED ONLY IF IT WORKED. A machine with no
        setxkbmap, or one whose xkbcomp will not compile the code, keeps the
        layout session.sh loaded — so adopting the requested code regardless
        made this screen state, in the one indicator that exists to answer it,
        that the keys were in an alphabet they were not. On the machine case 2
        was written for (Cyrillic saved, a Latin half remembered) it filled the
        "English (US)" chip over keys still typing Russian, AND silenced
        _kbd_warning(), because that sentence is only offered when the live
        group cannot type ASCII. The masked field shows nothing either way, so
        the sole remaining fact on screen was "that password did not work" —
        the exact lock-out this row was added to end, pointing the wrong way.
        _set_kbd_group already refuses to believe a failed apply; so does this.
        """
        self._kb_groups = []        # display order, fixed for this screen
        self._kb_order = []         # X group index -> index into _kb_groups
        self._kb_active = 0
        self._kb_code = ""          # the offered groups, in display order
        self._kb_loaded = ""        # what setxkbmap was last given
        self._kb_saved = ""         # what the machine is configured for
        self._kb_syncing = False
        self._kb_btns = []
        if nbkeyboard is None or nbi18n is None:
            return
        try:
            saved = nbi18n.keyboard() or "us"
            code = nbkeyboard.ensure_qwerty(saved)
            pref = nbi18n.login_keyboard()
            if pref:
                for i, (lay, var) in enumerate(nbkeyboard.parse(code)):
                    if pref in (lay, nbkeyboard.join([(lay, var)])):
                        code = nbkeyboard.reorder(code, i)
                        break
            self._kb_saved = saved
            if code != saved and not nbkeyboard.apply(code):
                # Nothing was loaded: what is live is still what session.sh
                # put there. Describe THAT, so the chip and the warning are
                # about the keys somebody is actually pressing.
                code = nbkeyboard.join(nbkeyboard.parse(saved))
            elif code == saved:
                # session.sh may have fallen back to US after a failed saved
                # keymap load. Describe the server, not the desired JSON.
                live = nbkeyboard.live_code()
                if live:
                    code = live
            self._kb_code = self._kb_loaded = code
            self._kb_groups = nbkeyboard.parse(code)
            self._kb_order = list(range(len(self._kb_groups)))
        except Exception:                                      # noqa: BLE001
            self._kb_groups = []
            self._kb_order = []

    def _build_kbd(self, box):
        """The alphabet switch, on the machines that have two to be in.

        One button per group rather than a drop-down: a menu on this screen is
        a popup over a full-screen keep-above window, which is the stacking
        case matchbox already loses (the panel's own menus land behind a
        focused window). Two buttons need no popup, and they show BOTH answers
        at once, which is the fact somebody staring at a rejected password is
        missing. Nothing is drawn only when the machine's layout IS plain US
        (English, Dutch and Chinese ship it) — there, the switch would offer
        the arrangement already loaded. Every other language draws it.
        """
        if len(self._kb_groups) < 2 or nbkeyboard is None:
            return
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("lg-kbd")
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_top(12)

        cap = Gtk.Label(label=_t("Keyboard"))
        cap.get_style_context().add_class("lg-kbdcap")
        cap.set_margin_end(2)
        row.pack_start(cap, False, False, 0)

        for i, (lay, var) in enumerate(self._kb_groups):
            b = Gtk.ToggleButton()
            # VERBATIM. A layout names itself, the way nbi18n's own language
            # list does: the button that types Latin letters has to be
            # recognisable AS the Latin one, and "ענגליש (אַמעריקאַנער)" in Hebrew
            # script is not. nbi18n's auto-translate layer had "English (US)"
            # in the catalog and was rewriting it on a Russian, Greek, Hindi
            # and Yiddish screen — while the sentence below, which substitutes
            # the same name AFTER translation, went on saying "English (US)".
            # The message pointed at a button by a name the button did not
            # have, on the one screen where getting it wrong strands somebody.
            self._set_verbatim(b, nbkeyboard.group_name(lay, var))
            b.set_active(i == self._kb_active)
            # Keep these in the focus chain: a person may need to switch
            # alphabets before the password can be entered at all.  The
            # activation handler returns focus to the password field once the
            # live layout (or its failure rollback) is known.
            b.set_can_focus(True)
            b.connect("toggled", self._on_kbd_pick, i)
            row.pack_start(b, False, False, 0)
            self._kb_btns.append(b)
        box.pack_start(row, False, False, 0)

    @staticmethod
    def _set_verbatim(widget, text):
        """Label `widget` with text the catalog must not touch."""
        if nbi18n is not None and hasattr(nbi18n, "set_verbatim"):
            try:
                nbi18n.set_verbatim(widget, text)
                return
            except Exception:                                  # noqa: BLE001
                pass
        widget.set_label(text)

    def _sync_kbd(self):
        """Make the buttons show the group that is actually live."""
        self._kb_syncing = True
        try:
            for i, b in enumerate(self._kb_btns):
                b.set_active(i == self._kb_active)
        finally:
            self._kb_syncing = False

    def _on_kbd_pick(self, btn, index):
        if self._kb_syncing:
            return
        if not btn.get_active():
            # Pressing the live one again would otherwise leave every button
            # off, i.e. a keyboard in no alphabet at all.
            self._sync_kbd()
            # ...and the button now holds focus (it is in the focus chain so
            # a keyboard-only person can reach it): hand the caret back, or
            # the next keystroke lands on the button.
            self._caret_back()
            return
        self._set_kbd_group(index)

    def _set_kbd_group(self, index):
        """Make group `index` the live one.

        Done by re-loading the layout with that group FIRST, because a client
        cannot ask X to lock a group any other way from here. The display
        order this screen shows never changes — only which X group maps to
        which button — so the two buttons do not swap places under the cursor
        of somebody who is mid-password."""
        if nbkeyboard is None or not (0 <= index < len(self._kb_groups)):
            return
        code = nbkeyboard.reorder(self._kb_code, index)
        if nbkeyboard.apply(code):
            n = len(self._kb_groups)
            self._kb_order = [index] + [j for j in range(n) if j != index]
            self._kb_active = index
            self._kb_loaded = code
        # On failure this puts the buttons back to what is still true, rather
        # than leaving one lit for a layout that never loaded.
        self._sync_kbd()
        # The caret goes back exactly where it was -- see _caret_back, which
        # is where the rule this switch was the first to need now lives.
        self._caret_back()

    def _track_group(self, ev):
        """Follow a switch made with Alt+Shift, from the key events.

        X tells a client the live group only as a field on the events it
        already delivers, so this is where it comes from. Reading it on
        RELEASE as well as press is what makes the indicator change the moment
        Alt+Shift is let go, instead of one keystroke later."""
        if not self._kb_btns:
            return
        g = getattr(ev, "group", 0) or 0
        if 0 <= g < len(self._kb_order):
            i = self._kb_order[g]
            if i != self._kb_active:
                self._kb_active = i
                self._sync_kbd()

    def _kbd_warning(self):
        """The sentence to add after a wrong password, or "".

        Shown when the live group is not plain US QWERTY and a US group is
        there to switch to. It used to require that the live group could not
        type ASCII at all, to avoid telling somebody with a Russian password
        that their keyboard is Russian. That test let the reported lock-out
        through: on "jp" the live group CAN type ASCII, so this said nothing
        while the underscore in somebody's password sat on a key their
        keyboard does not have.

        The trade is the one nbkeyboard's header already makes. A redundant
        sentence costs a line after a sign-in that had already failed; a
        missing one costs the machine. It is never shown before a failure.
        """
        if nbkeyboard is None or not self._kb_groups:
            return ""
        lay, var = self._kb_groups[self._kb_active]
        if (lay, var) == (nbkeyboard.LATIN_FALLBACK, ""):
            return ""
        for other in self._kb_groups:
            if other == (nbkeyboard.LATIN_FALLBACK, ""):
                return _t("The keys are typing %s. Press Alt+Shift, or the "
                          "button above, to type in %s.") % (
                              nbkeyboard.group_name(lay, var),
                              nbkeyboard.group_name(*other))
        return ""

    def _finish_keyboard(self, signed_in):
        """Remember what worked, then hand the machine's own layout back.

        REMEMBER: the half a sign-in succeeded on becomes the default for the
        next one, so switching alphabets to type a Latin password is a thing
        somebody does once rather than every morning. Only the layout code is
        stored — no part of the password reaches this.

        RESTORE: the order this screen may have chosen is a sign-in
        preference, not the machine's, and the desktop belongs in the alphabet
        its owner configured. The guaranteed Latin group stays, though: a
        layout with no way to type ASCII is the defect above, not a setting.
        """
        if nbkeyboard is None or not self._kb_groups:
            return
        try:
            if signed_in and nbi18n is not None:
                live = nbkeyboard.join([self._kb_groups[self._kb_active]])
                if live != nbi18n.login_keyboard():
                    nbi18n.set_login_keyboard(live)
        except Exception:                                      # noqa: BLE001
            pass
        try:
            want = nbkeyboard.ensure_latin(self._kb_saved or "us")
            # Two ways this screen can leave the keyboard somewhere else, and
            # both have to be undone: a different keymap LOADED (the buttons),
            # and the same keymap with a different group LIVE (Alt+Shift,
            # which no client performs and which re-loading resets).
            switched = bool(self._kb_order) and \
                self._kb_active != self._kb_order[0]
            if want != self._kb_loaded or switched:
                nbkeyboard.apply(want)
        except Exception:                                      # noqa: BLE001
            pass

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

    def _on_destroy(self, *_a):
        """Take this screen's timers down WITH the screen, then quit the loop.

        `destroy` was connected straight to Gtk.main_quit, which leaves both
        timers running over a window whose widgets have been finalised: the
        clock tick every 30 seconds, and the failure pause for up to 5. Their
        callbacks then set text on, and hand focus to, a destroyed GtkLabel and
        a destroyed GtkEntry -- the shape of thing that ends the process with a
        GTK criticals or a segfault at exactly the moment the desktop is
        starting. On the LOCK screen that is worse than untidy: main() runs
        _finish_keyboard() after Gtk.main() returns, and a crash on the way out
        hands the desktop back in whichever alphabet this screen chose.

        Closed is marked FIRST, so a callback that is already queued behind us
        -- one GLib had dispatched before the source was removed -- finds the
        flag set and touches nothing. Cancelling is best-effort: a source that
        has already fired and returned False is gone, and asking GLib to remove
        it raises rather than shrugging.

        Idempotent, and it has to be: destroy can be emitted more than once
        (hide-then-destroy, a WM delete on the way out), and a second
        Gtk.main_quit would pop a main loop this screen does not own.
        """
        if self._closed:
            return False
        self._closed = True
        for attr in ("_clock_id", "_wait_id", "_count_id"):
            sid = getattr(self, attr, 0)
            setattr(self, attr, 0)
            if sid:
                try:
                    GLib.source_remove(sid)
                except Exception:                              # noqa: BLE001
                    pass
        Gtk.main_quit()
        return False

    def _on_delete(self, *_a):
        """Veto every window-manager close until authentication succeeds."""
        return not self.ok

    def _tick_clock(self, *_a):
        # Nothing here may touch a widget once the window is gone -- see
        # _on_destroy. Returning False also drops the source, so a tick that
        # somehow outlives the cancel above stops of its own accord.
        if self._closed:
            self._clock_id = 0
            return False
        now = time.localtime()
        self.clock.set_text(time.strftime("%H:%M", now))
        # ONE STRING, TRANSLATED WHOLE — never word by word into a pattern.
        # Built piecewise the words were right and the ORDER was not: a
        # positional catalog pattern cannot move the month in front of the
        # day, so a Japanese machine's first screen read "月曜日、17日 8月"
        # where its own panel clock, two seconds later, reads "8月17日 月曜日".
        # nbi18n's date route (_date_lookup/_zh_date) takes a whole
        # strftime-shaped string apart and lays it out per language — CJK
        # big-endian, ru/pl lower-cased, es with its "de" — and it only ever
        # sees one if it is handed one. shell.py's clock and workout.py both
        # go this way already; this screen was the outlier.
        self.date.set_text(_t("%s %d %s" % (time.strftime("%A", now),
                                            now.tm_mday,
                                            time.strftime("%B", now))))
        return True

    # -- actions -------------------------------------------------------------

    def _clear_error(self, *_a):
        self.entry.get_style_context().remove_class("wrong")
        was = self.error.get_visible()
        self.error.hide()
        if was:
            self._balance_answer()
        if hasattr(self.entry, "get_accessible"):
            acc = self.entry.get_accessible()
            acc.set_description("")
            acc.notify_state_change(Atk.StateType.INVALID_ENTRY, False)

    def _caret_back(self):
        """Put the caret back in the password field, where it was.

        WITHOUT SELECTING. Gtk.Entry.grab_focus() selects the entry's whole
        contents -- even when that entry already held the caret -- so every
        handler here that "returns focus to the field" was arming the next
        keystroke to replace the password typed so far. Measured on this
        widget: "secret", tick Show password, "X" gave "X", and on the untick
        the field is masked again so nothing on screen showed what was lost.
        _set_kbd_group had already been through this; this is the same rule in
        one place, for every control on the screen that takes the caret away.

        Nothing happens while the field is held after repeated failures: it
        cannot take the caret then, and this is called from controls that are
        still live while it is (the tick, the alphabet chips, the power row).
        """
        if self.entry.get_sensitive():
            self.entry.grab_focus_without_selecting()

    def _on_show_toggle(self, btn):
        self.entry.set_visibility(btn.get_active())
        self._caret_back()

    def _on_key(self, _w, ev):
        self._track_group(ev)
        # Escape is swallowed: a lock screen you can dismiss is decoration.
        # It does back out of the power question, which is the only thing on
        # this screen it can honestly mean — and the screen still stands.
        if ev.keyval in (Gdk.KEY_Escape,):
            if getattr(self, "_pwr_mode", ""):
                self._cancel_power()
            return True
        return False

    def _on_key_release(self, _w, ev):
        self._track_group(ev)
        return False

    def _try(self, *_a):
        # The pause below is the ONE source of truth about whether this screen
        # is accepting an attempt. A submission that arrives while it is
        # running -- a click already queued when the field went insensitive, a
        # second activation of the same keypress -- used to be counted like any
        # other and to arm a SECOND pause on top of the first, so the failure
        # count (and with it the length of every later pause) ran ahead of what
        # anybody had actually typed, and the field came back at whichever
        # timer happened to fire first.
        # A window on its way out is the same case one step further along: the
        # click was queued before the screen went, and there is nothing left
        # here to mark wrong or hand focus back to.
        if self._closed:
            return
        if self._wait_id or self.ok:
            return
        if verify(self.user, self.entry.get_text()):
            self.ok = True
            self.hide()
            Gtk.main_quit()
            return
        self._tries += 1
        self.entry.set_text("")
        self.entry.get_style_context().add_class("wrong")
        message = _t("That password did not work.")
        self.error.set_text(message)
        if hasattr(self.error, "get_accessible"):
            self.error.get_accessible().set_name(message)
        self.error.show()
        if hasattr(self.entry, "get_accessible"):
            entry_acc = self.entry.get_accessible()
            entry_acc.set_description(message)
            entry_acc.notify_state_change(Atk.StateType.INVALID_ENTRY, True)
        # First failure: offer to show what is being typed, and say where the
        # password came from. Somebody who has forgotten it has no other route
        # back into this machine, so the one true reminder we have is worth
        # more than a tidier screen.
        self._show.set_no_show_all(False)
        self._show.show()
        # The alphabet comes FIRST when it is wrong: it is the one thing here
        # that can be acted on, and on a Cyrillic, Greek, Devanagari or Hebrew
        # keyboard it is the likelier of the two explanations by far — the
        # field is masked, so nothing else on this screen can reveal that the
        # keys were never producing the letters they are printed with.
        recall = _t("The administrator password set when Notebook OS was "
                    "installed.")
        kbd = self._kbd_warning()
        # Two paragraphs, not one run-on: they are answers to two different
        # questions ("why did that fail" and "what was the password"), and run
        # together they read as one long apology nobody finishes. Two labels,
        # so the break between them is a paragraph's worth of space and not
        # one more wrapped line (see _make_recall_label).
        if kbd:
            self._set_paragraph(self._kbdnote, kbd)
            self._kbdnote.set_no_show_all(False)
            self._kbdnote.show()
        self._set_paragraph(self._recall, recall)
        self._recall.set_no_show_all(False)
        self._recall.show()
        self._balance_answer()
        self.entry.grab_focus()
        # A short, growing pause after repeated failures. Not a lockout — being
        # locked out of your own offline computer is a worse outcome than a
        # slow guess — just enough that guessing is not free.
        if self._tries >= 3:
            self.entry.set_sensitive(False)
            self._go.set_sensitive(False)
            secs = min(5, self._tries)
            self._wait_id = GLib.timeout_add_seconds(secs, self._re_enable)
            # ...and SAY SO. The field goes grey, but nothing said that the
            # keys were going nowhere or for how long, so the pause read as a
            # dead keyboard on the one screen with no other way forward.
            self._wait_left = secs
            self._say_wait()
            self._balance_answer()
            self._count_id = GLib.timeout_add_seconds(1, self._tick_wait)

    def _say_wait(self):
        """State the pause, in the seconds still to run."""
        n = self._wait_left
        self._wait.set_text(_t("Try again in %d second%s.")
                            % (n, "" if n == 1 else "s"))
        self._wait.set_no_show_all(False)
        self._wait.show()

    def _tick_wait(self):
        """Count that pause down once a second. Nothing here may touch a
        widget once the window is gone -- see _on_destroy, which cancels this
        source with the others."""
        if self._closed:
            self._count_id = 0
            return False
        self._wait_left -= 1
        if self._wait_left <= 0:
            self._count_id = 0
            return False
        self._say_wait()
        return True

    def _drop_countdown(self):
        """Stop the countdown source, from either end. Touches no widget, so
        it is safe on the way out as well."""
        self._wait_left = 0
        sid = self._count_id
        self._count_id = 0
        if sid:
            try:
                GLib.source_remove(sid)
            except Exception:                                  # noqa: BLE001
                pass

    def _re_enable(self):
        # Ownership is released FIRST, whichever way this returns: the id is
        # this source's own, it is about to be gone either way, and leaving it
        # set would have _on_destroy try to remove a source that has already
        # returned False. The countdown is dropped here for the same reason:
        # the pause it was counting is over.
        self._wait_id = 0
        self._drop_countdown()
        if self._closed:
            return False
        self._wait.hide()
        self._balance_answer()
        self.entry.set_sensitive(True)
        self._go.set_sensitive(True)
        self.entry.grab_focus()
        return False

    def state_unverifiable(self):
        """Nothing typed here can be accepted: say so, and stop taking it.

        main() puts the lock screen into this state when the credential file
        cannot be READ at all -- a running desktop must not be revealed
        because /etc/shadow was briefly unreadable. It used to read "Password:
        That file could not be read.", which names a file nobody can see and
        asks for a password the machine has already decided it cannot check,
        on a screen that (until the row below existed) had no way off it.
        """
        self.error.set_text(_t("The password file on this computer could not "
                               "be read, so nothing typed here can be "
                               "accepted."))
        self.error.show()
        self._balance_answer()
        self.entry.set_sensitive(False)
        self._go.set_sensitive(False)

    # -- the way out ---------------------------------------------------------

    def _ask_power(self, _btn, mode, verb):
        """Put the question in place of the two buttons, in the same strip."""
        self._pwr_mode = mode
        ask = _t("%s?") % _t(verb)
        if self.lock:
            # There is a session under a lock screen, with open apps in it.
            # At sign-in there is nothing yet, and saying otherwise would be
            # a warning about something that cannot happen.
            ask += "  " + _t("Unsaved work in open apps will be lost.")
        self._pwr_q.set_text(ask)
        self._pwr_yes.set_label(_t(verb))
        self._pwr_row.hide()
        self._pwr_ask.set_no_show_all(False)
        self._pwr_ask.show_all()
        # The safe half is the one that gets the keyboard.
        for w in self._pwr_ask.get_children():
            if isinstance(w, Gtk.Button) and w is not self._pwr_yes:
                w.grab_focus()
                break

    def _cancel_power(self, *_a):
        self._pwr_mode = ""
        self._pwr_ask.hide()
        self._pwr_ask.set_no_show_all(True)
        self._pwr_row.show()
        self._caret_back()

    def _confirm_power(self, *_a):
        mode = self._pwr_mode
        self._pwr_mode = ""
        if mode:
            self._do_power(mode)

    @staticmethod
    def _do_power(mode):
        """busybox provides poweroff and reboot; the desktop's own menu goes
        the same way (shell.py _do_power). Only ever reached through the
        confirmation above."""
        try:
            subprocess.Popen([mode])
        except OSError:
            pass


def main():
    lock = "--lock" in sys.argv
    sleep = "--sleep" in sys.argv
    user = desktop_user()
    # Nothing to ask for: no password set, account locked, or shadow unreadable.
    # Start the desktop rather than stranding somebody at a prompt that cannot
    # be satisfied.
    password_state = _password_state(user)
    if _may_skip_login(user, lock=lock, state=password_state):
        if sleep:
            _blank_for_sleep()
        return 0
    nbapp.install_css()
    win = Login(lock=lock)
    if password_state == "error":
        # A running desktop must never be revealed because its credential file
        # was briefly unreadable. There is no password we can honestly check,
        # so keep the lock surface up -- with the power controls _build puts
        # at the foot of it, which are the only way off this one.
        win.state_unverifiable()
    if sleep:
        blanked = [False]

        def blank_after_map(*_args):
            if not blanked[0]:
                blanked[0] = True
                _blank_for_sleep()
            return False

        # Run after Login's own map handler has reasserted fullscreen/above.
        win.connect_after("map-event", blank_after_map)
    win.show_all()
    Gtk.main()
    # Hand the machine's own keyboard layout back before anything else starts,
    # and remember which half the password was typed on. session.sh re-applies
    # the layout after this returns as well, so a screen that is killed rather
    # than signed into cannot leave the desktop in the other alphabet.
    win._finish_keyboard(win.ok)
    # Zero is the authentication grant. An unexpected loop/window exit must
    # not be indistinguishable from a verified password to session.sh.
    return 0 if win.ok else 1


def _blank_for_sleep():
    """Enable DPMS and blank only after the lock surface is safe to reveal."""
    try:
        subprocess.run(["xset", "+dpms"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3)
        subprocess.run(["xset", "dpms", "force", "off"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=3)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
