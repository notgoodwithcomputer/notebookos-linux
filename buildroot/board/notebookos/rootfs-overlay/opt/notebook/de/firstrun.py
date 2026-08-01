#!/usr/bin/env python3
"""
First-run setup — the screen a person meets when they switch on a machine
somebody else installed for them.

WHY THIS EXISTS. The installer asks for the computer's name, its keyboard, its
language and a password. All four are answers only the person who will USE the
machine can give, and on an OEM install that person is not in the room: a
parent, a school or a shop sets the computer up and hands it over. Making the
installer's operator invent a password for somebody else is the worst of both
worlds — they have to remember it long enough to pass it on, and the new owner
inherits a secret they did not choose.

So the installer, in "set it up for someone else" mode, writes every part of
the system that is impersonal and leaves a marker (OEM_MARKER). On the first
start, session.sh sees the marker and runs this, BEFORE the sign-in screen and
before any of the desktop is drawn. When it finishes it writes those four
answers, removes the marker, and never appears again.

It deliberately looks like login.py rather than like the installer: this is the
new owner's first moment with the machine, not a continuation of somebody
else's admin task.

Run standalone:  python3 firstrun.py          (exits 0 when done or not needed)
                 python3 firstrun.py --needed (exit 0 if setup is pending)
"""
import json
import os
import re
import subprocess
import sys
import time

# The marker the installer leaves. Under /var so it survives on the installed
# root and is obviously machine state rather than a user document.
OEM_MARKER = "/var/lib/notebookos/first-run"

HOSTNAME_FILE = "/etc/hostname"
USER_NAME_FILE = "/etc/notebookos-user"
SHADOW = "/etc/shadow"
XKB_CONF = "/etc/X11/xorg.conf.d/00-keyboard.conf"


def pending():
    """Is a first-run setup owed? Answered from one stat, with no imports that
    cost anything, so session.sh can ask on every boot for free."""
    try:
        return os.path.isfile(OEM_MARKER)
    except OSError:
        return False


def _xkb_parts(code):
    """An nbi18n layout code -> (layout, variant, options).

    Same rules the installer uses, and for the same reason: "jp(kana)" is a
    VARIANT (writing it as XkbLayout yields no keymap at all) and "ru,us" is a
    dual layout that needs a switch key, or its Latin half is unreachable — and
    for Russian, Hindi, Greek and Yiddish that means no way to type a password.
    """
    variant = ""
    m = re.match(r"^([^(]+)\((.+)\)$", code or "")
    if m:
        code, variant = m.group(1), m.group(2)
    options = "grp:alt_shift_toggle" if "," in (code or "") else ""
    return code or "us", variant, options


def valid_hostname(name):
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", name or ""))


# --------------------------------------------------------------------------
# Applying the answers. Kept free of Gtk so it can be tested without a display.
# --------------------------------------------------------------------------
def hash_password(pw):
    """A crypt hash for `pw`, or None when this machine cannot make one.

    The guest ships Python's `crypt`; openssl is NOT in the image, so it is only
    a fallback for a host that lacks the module. Returning None rather than
    raising lets the caller keep the password step open instead of writing a
    shadow entry no password could ever satisfy — the brick that login.py's
    has_password() guards against from the other side.
    """
    try:
        import crypt as _crypt
        return _crypt.crypt(pw, _crypt.mksalt(_crypt.METHOD_SHA512))
    except Exception:
        pass
    try:
        out = subprocess.run(["openssl", "passwd", "-6", pw],
                             capture_output=True, text=True, timeout=20)
        got = (out.stdout or "").strip()
        if out.returncode == 0 and got.startswith("$6$"):
            return got
    except Exception:
        pass
    return None


def set_root_password(hashed):
    """Put `hashed` in root's /etc/shadow entry, or lock the account when it is
    None. Rewrites the whole file through a temp + rename so an interrupted
    write can never leave the machine with a shadow nobody can log in against.

    A shadow with NO usable root line gets one APPENDED rather than quietly
    skipped, and a root line too short to hold a hash is rebuilt rather than
    left alone. Both used to fall straight through the loop and return True
    having written nothing: this screen then reported success, removed its
    marker and handed over a machine that starts straight into the desktop with
    no password at all — for somebody who had just chosen one, on a machine
    with no way to find out why.

    Exactly ONE root line comes out, whatever went in. Two would be worse than
    none: de/login.py reads the FIRST match, so a malformed line left above a
    good one takes the machine back to "no password".
    """
    try:
        with open(SHADOW) as fh:
            lines = fh.readlines()
    except OSError:
        return False
    field = hashed if hashed else "*"
    # Field 3 of a shadow line is the day the password was last changed. The
    # installer writes it and busybox's own tools expect a number there.
    lastchg = str(int(time.time() // 86400))
    out = []
    found = False
    for ln in lines:
        parts = ln.rstrip("\n").split(":")
        if parts and parts[0] == "root":
            if found:
                continue                      # never leave a second root line
            while len(parts) < 9:
                parts.append("")
            parts[1] = field
            parts[2] = lastchg
            ln = ":".join(parts) + "\n"
            found = True
        out.append(ln)
    if not found:
        out.append("root:%s:%s:0:99999:7:::\n" % (field, lastchg))
    shadow_tmp = SHADOW + ".firstrun"
    try:
        with open(shadow_tmp, "w") as fh:
            fh.writelines(out)
        os.chmod(shadow_tmp, 0o600)
        os.replace(shadow_tmp, SHADOW)
        return True
    except OSError:
        try:
            os.unlink(shadow_tmp)
        except OSError:
            pass
        return False


def write_user_name(name):
    """What the machine calls its owner: shown at sign-in in place of "root"."""
    try:
        with open(USER_NAME_FILE, "w") as fh:
            fh.write((name or "").strip()[:40] + "\n")
        return True
    except OSError:
        return False


def write_hostname(name):
    try:
        os.makedirs(os.path.dirname(HOSTNAME_FILE), exist_ok=True)
        with open(HOSTNAME_FILE, "w") as fh:
            fh.write(name + "\n")
        # Take effect now as well, so the name is right without a reboot.
        subprocess.run(["hostname", name], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def write_keyboard(code):
    """Persist the X keyboard layout AND apply it to the running server."""
    layout, variant, options = _xkb_parts(code)
    conf = ['Section "InputClass"',
            '    Identifier "system-keyboard"',
            '    MatchIsKeyboard "on"',
            '    Option "XkbLayout" "%s"' % layout]
    if variant:
        conf.append('    Option "XkbVariant" "%s"' % variant)
    if options:
        conf.append('    Option "XkbOptions" "%s"' % options)
    conf.append("EndSection")
    ok = True
    try:
        os.makedirs(os.path.dirname(XKB_CONF), exist_ok=True)
        with open(XKB_CONF, "w") as fh:
            fh.write("\n".join(conf) + "\n")
    except OSError:
        ok = False
    try:
        args = ["setxkbmap", layout]
        if variant:
            args += ["-variant", variant]
        if options:
            args += ["-option", options]
        subprocess.run(args, capture_output=True, timeout=10)
    except Exception:
        pass
    return ok


def write_locale(lang_code, kbd_code):
    """The file the DESKTOP reads for its language and keyboard.

    THE KEY NAMES HERE ARE nbi18n's, NOT THIS FILE'S, and they were not. This
    wrote "language" while nbi18n.current_lang() reads "lang", so the language
    a new owner chose on this screen was written to a key nothing reads: every
    machine set up for somebody else came up in English whatever they picked,
    with no error, nothing on screen to say why, and the one place to change it
    buried in a Settings page they cannot read. The keyboard key was right,
    which is what made it look like the language had simply not been asked.

    So the write is VERIFIED by reading it back through nbi18n itself — the
    code that will read this file on every later boot. A writer and a reader
    that disagree is the entire bug, and asserting the file "has the key I just
    wrote" is what missed it the first time.
    """
    home = os.environ.get("NB_HOME", "/root")
    path = os.path.join(home, ".config", "notebook", "locale.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {}
        try:
            with open(path) as fh:
                got = json.load(fh)
            if isinstance(got, dict):
                data = got
        except (OSError, ValueError):
            data = {}
        data["lang"] = lang_code
        data["keyboard"] = kbd_code
        # The shared crash-safe writer, not a hand-rolled temp: it fsyncs and
        # it is the one place the whole OS's write safety is maintained.
        import nbapp
        nbapp.atomic_write_json(path, data)
    except Exception:
        return False
    try:
        import nbi18n
        # $NB_LANG outranks the file, so a session that pins it cannot answer
        # the question; the write itself already succeeded.
        if os.environ.get("NB_LANG"):
            return True
        return (nbi18n.current_lang() == lang_code
                and nbi18n.keyboard() == kbd_code)
    except Exception:
        return True


def clear_marker():
    """Setup is done. Removing this is the LAST thing that happens, so a
    machine switched off half-way through simply asks again rather than
    starting up half-configured with no way back to the questions.

    Answers "is the marker gone?", not "did unlink() succeed?" — a marker some
    earlier run already removed is not a failure, and the only thing the next
    boot cares about is whether the file is there."""
    try:
        os.unlink(OEM_MARKER)
    except OSError:
        pass
    try:
        return not os.path.exists(OEM_MARKER)
    except OSError:
        return False


# What each answer is called on the form, so a part that did not stick is named
# the way the person just read it. Every one of these is an interface string the
# catalogs already carry; the raw lowercase keys this used to report ("name",
# "keyboard") are in no catalog at all, so the one sentence on this screen that
# reports a failure came out half in English on all sixteen other languages.
# "name" also stood for BOTH the owner's name and the computer's, which named
# the wrong field as often as the right one.
PART_NAMES = {"username": "Name", "hostname": "Computer name",
              "keyboard": "Keyboard", "language": "Language",
              "password": "Password"}


def apply(answers):
    """Write every answer. Returns a list of the parts that did not stick, so
    the caller can say something true rather than claiming success.

    THE MARKER IS CLEARED LAST, AND ONLY ON A CLEAN RUN. It is the one thing
    standing between a half-configured machine and a machine that never asks
    again, so anything that did not stick keeps this screen owed."""
    failed = []
    if answers.get("username") and not write_user_name(answers["username"]):
        failed.append("username")
    if not write_hostname(answers["hostname"]):
        failed.append("hostname")
    if not write_keyboard(answers["kbd"]):
        failed.append("keyboard")
    if not write_locale(answers["lang"], answers["kbd"]):
        failed.append("language")
    if answers.get("password"):
        hashed = hash_password(answers["password"])
        if hashed is None or not set_root_password(hashed):
            failed.append("password")
    else:
        # No password: leave root locked, exactly as the image ships. login.py
        # then has nothing to ask for and the machine starts straight in.
        set_root_password(None)
    if not failed:
        clear_marker()
    return failed


def main():
    if "--needed" in sys.argv:
        return 0 if pending() else 1
    if not pending():
        return 0

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib     # noqa: F401
    import nbi18n
    from nbi18n import _t
    import nbapp

    # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
    # stylesheet, and this is the first screen a new owner ever sees.
    CSS = b"""
    .fr-bg { background: #FCFBF8; }
    /* The scroller and its viewport are painted too. A GTK viewport's own
       bin-window is unreachable by any later rule, and on the software render
       stack an unpainted one is BLACK -- which is what a frame of black around
       the setup form would have been. */
    .fr-bg scrolledwindow, .fr-bg viewport { background: #FCFBF8; }
    .fr-bg scrollbar { background: #FCFBF8; border: none; }
    .fr-bg scrollbar trough { background: #F1EEE6; border: none;
                border-radius: 8px; margin: 2px; }
    .fr-bg scrollbar slider { background: #C9C4B6; border-radius: 8px;
                min-width: 9px; min-height: 30px; border: 2px solid #F1EEE6; }
    .fr-card { background: #FCFBF8; }
    .fr-title { font-family: "Newsreader","Liberation Serif",serif;
                font-size: 34px; color: #1A1916; }
    .fr-sub { font-size: 14px; color: #6E695E; }
    .fr-label { font-size: 12px; letter-spacing: 0.08em; color: #6E695E; }
    .fr-entry { font-size: 15px; padding: 8px 10px; background: #FCFBF8;
                border: 1px solid #C9C4B6; border-radius: 3px; color: #1A1916; }
    .fr-go { background: #1A1916; border: 1px solid #1A1916; border-radius: 3px;
             padding: 9px 26px; font-size: 15px; }
    .fr-go label { color: #FCFBF8; }
    .fr-err { font-size: 13px; color: #C8341E; }
    .fr-note { font-size: 12px; color: #9A9484; }
    """

    class FirstRun(Gtk.Window):
        def __init__(self):
            Gtk.Window.__init__(self, title="Welcome")
            self.set_decorated(False)
            self.set_app_paintable(True)
            self.get_style_context().add_class("fr-bg")
            prov = Gtk.CssProvider()
            prov.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            # Fill the screen with no window manager to help: same lesson
            # login.py paid for, where a natural-size window landed as a small
            # panel in the corner of the display.
            self._fit_screen()
            self.connect("map-event", lambda *_a: self._fit_screen())
            self.connect("destroy", Gtk.main_quit)
            self._build()

        def _fit_screen(self):
            try:
                scr = Gdk.Screen.get_default()
                w, h = scr.get_width(), scr.get_height()
                if w > 0 and h > 0:
                    self.set_default_size(w, h)
                    self.resize(w, h)
                    self.move(0, 0)
            except Exception:
                pass

        def _build(self):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            outer.set_halign(Gtk.Align.CENTER)
            outer.set_valign(Gtk.Align.CENTER)
            outer.get_style_context().add_class("fr-card")
            outer.set_margin_start(24)
            outer.set_margin_end(24)

            # The brand mark is a PNG asset, not an nbicons glyph -- there is
            # no "snail" in nbicons and asking for one draws its empty-box
            # fallback, which is what shipped here first. login.py learned this
            # already; use the same source so the two screens match.
            # The brand mark is a PNG asset, not an nbicons glyph -- there is
            # no "snail" in nbicons and asking for one draws its empty-box
            # fallback, which is what shipped here first. If the asset cannot
            # be loaded the NAME stands in, exactly as on the sign-in screen:
            # this is the first thing a new owner sees and it must never open
            # with a hole where the mark should be.
            mark = None
            try:
                pb = nbapp._logo_pixbuf()
                if pb is not None:
                    mark = Gtk.Image.new_from_pixbuf(pb)
            except Exception:
                mark = None
            if mark is None:
                mark = Gtk.Label(label=_t("Notebook OS"))
                mark.get_style_context().add_class("fr-sub")
            mark.set_margin_bottom(10)
            outer.pack_start(mark, False, False, 0)

            t = Gtk.Label(label=_t("Setup"))
            t.get_style_context().add_class("fr-title")
            # The scene-setting line under this title is gone; its bottom margin
            # moves here so the gap above the fields is unchanged.
            t.set_margin_bottom(22)
            outer.pack_start(t, False, False, 0)

            grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            grid.set_size_request(420, -1)

            def field(label_text, widget):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                lb = Gtk.Label(label=label_text, xalign=0)
                lb.get_style_context().add_class("fr-label")
                box.pack_start(lb, False, False, 0)
                box.pack_start(widget, False, False, 0)
                return box

            self.e_user = Gtk.Entry()
            self.e_user.get_style_context().add_class("fr-entry")
            grid.pack_start(field(_t("NAME"), self.e_user),
                            False, False, 0)

            self.e_name = Gtk.Entry()
            self.e_name.set_text("notebook")
            self.e_name.get_style_context().add_class("fr-entry")
            grid.pack_start(field(_t("COMPUTER NAME"),
                                  self.e_name), False, False, 0)

            self.c_lang = Gtk.ComboBoxText()
            self._langs = sorted(nbi18n.LANG_NAMES.items(),
                                 key=lambda kv: kv[1].lower())
            cur_lang = 0
            for i, (code, name) in enumerate(self._langs):
                self.c_lang.append_text(name)
                if code == nbi18n.current_lang():
                    cur_lang = i
            self.c_lang.set_active(cur_lang)
            grid.pack_start(field(_t("LANGUAGE"), self.c_lang), False, False, 0)

            self.c_kbd = Gtk.ComboBoxText()
            self._kbds = list(nbi18n.KEYBOARDS)
            cur_kbd = 0
            for i, (code, name) in enumerate(self._kbds):
                self.c_kbd.append_text(name)
                if code == nbi18n.keyboard():
                    cur_kbd = i
            self.c_kbd.set_active(cur_kbd)
            # Connected AFTER set_active, so choosing the layout the machine is
            # already using does not fire and wipe the fields below.
            self.c_kbd.connect("changed", self._on_kbd)
            grid.pack_start(field(_t("KEYBOARD"), self.c_kbd), False, False, 0)

            self.e_pw = Gtk.Entry()
            self.e_pw.set_visibility(False)
            self.e_pw.get_style_context().add_class("fr-entry")
            self.e_pw.set_activates_default(True)
            grid.pack_start(field(_t("PASSWORD"), self.e_pw), False, False, 0)

            self.e_pw2 = Gtk.Entry()
            self.e_pw2.set_visibility(False)
            self.e_pw2.get_style_context().add_class("fr-entry")
            self.e_pw2.set_activates_default(True)
            grid.pack_start(field(_t("PASSWORD AGAIN"), self.e_pw2),
                            False, False, 0)

            # Read back what is being typed. It matters more here than anywhere
            # else in the OS: the layout above can now change under the fields
            # (see _on_kbd), and on a Russian or Greek keyboard the difference
            # between the password that gets stored and the one the owner
            # thinks they chose is invisible behind dots. There is no way back
            # into this machine afterwards.
            self.cb_show = Gtk.CheckButton(label=_t("Show password"))
            self.cb_show.connect("toggled", self._on_show)
            grid.pack_start(self.cb_show, False, False, 0)

            self.cb_none = Gtk.CheckButton(
                label=_t("Start straight into the desktop without a password"))
            self.cb_none.connect("toggled", self._on_none)
            grid.pack_start(self.cb_none, False, False, 0)

            note = Gtk.Label(
                label=_t("This password cannot be recovered."), xalign=0)
            note.get_style_context().add_class("fr-note")
            note.set_line_wrap(True)
            note.set_max_width_chars(48)
            grid.pack_start(note, False, False, 0)

            self.err = Gtk.Label(label="")
            self.err.get_style_context().add_class("fr-err")
            self.err.set_line_wrap(True)
            self.err.set_max_width_chars(46)
            grid.pack_start(self.err, False, False, 0)

            go = Gtk.Button(label=_t("Finish"))
            go.get_style_context().add_class("fr-go")
            go.set_halign(Gtk.Align.CENTER)
            go.connect("clicked", self._finish)
            go.set_can_default(True)
            grid.pack_start(go, False, False, 0)

            outer.pack_start(grid, False, False, 0)
            # THE FINISH BUTTON MUST BE REACHABLE ON EVERY PANEL THIS RUNS ON.
            # This form is six fields, two ticks and a note tall, and it does
            # not shrink: measured across all eighteen interface languages the
            # tallest (German) asks for 710px, against a smallest-supported
            # panel of 740. That is thirty pixels of margin on the one screen
            # that HAS to be completed before the machine can be used at all --
            # and past the bottom of the glass there is no scrollbar, no
            # keyboard route and no way to press Finish. A scroller costs
            # nothing when the form fits (it stays centred, exactly as before)
            # and is the difference between a usable machine and a brick when
            # it does not.
            #
            # NEVER horizontally: the form has a fixed measure, so a horizontal
            # scrollbar could only ever mean the window is narrower than its own
            # minimum, which is a layout bug rather than something to scroll.
            outer.set_margin_top(24)
            outer.set_margin_bottom(24)
            # `page` is a FULL-WIDTH painted box that the centred form sits in,
            # and it is not decoration. A GTK viewport's own bin-window is not
            # reachable by any CSS rule, and on the software render stack an
            # unpainted one is BLACK -- rendered offscreen at 1024x600 this
            # screen came back as the form in a black field. Giving the viewport
            # a child that expands to fill it, and painting THAT, is the same
            # fix installer.py's _page_scaffold carries for the same reason.
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            page.get_style_context().add_class("fr-card")
            page.set_hexpand(True)
            page.set_vexpand(True)
            page.pack_start(outer, True, True, 0)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_shadow_type(Gtk.ShadowType.NONE)
            scroll.get_style_context().add_class("fr-bg")
            scroll.add(page)
            self.add(scroll)
            self.show_all()
            self.set_default(go)
            self.e_user.grab_focus()

        def _on_none(self, cb):
            on = cb.get_active()
            self.e_pw.set_sensitive(not on)
            self.e_pw2.set_sensitive(not on)
            self.cb_show.set_sensitive(not on)

        def _on_show(self, cb):
            vis = cb.get_active()
            self.e_pw.set_visibility(vis)
            self.e_pw2.set_visibility(vis)

        def _on_kbd(self, combo):
            """Put the chosen layout on the RUNNING keyboard, now.

            THE PASSWORD IS TYPED ON THIS SCREEN AND CHECKED ON THE NEXT BOOT,
            and the layout used to be applied only at the end, from apply().
            So somebody who chose French here and then typed their password was
            still typing on the US layout the machine had started with: the
            hash stored is of "qwerty" while every later boot turns those same
            keys into "azerty". The sign-in screen then rejects the password
            its owner actually chose, forever — no network, no getty on tty1,
            no recovery. Every non-US keyboard on an OEM machine was one
            drop-down away from that.

            Applying it here means the password is typed on exactly the layout
            that will be asked to check it. The two password fields are cleared
            with it, because anything typed before the change was produced by
            the old layout and is no longer what the person meant.
            """
            i = combo.get_active()
            if not (0 <= i < len(self._kbds)):
                return
            # By INDEX, never get_active_text(): nbi18n translates what a
            # ComboBoxText shows, so the visible text is not the code it was
            # built from (the same defect that broke Writer's style menus).
            code = self._kbds[i][0]
            try:
                # nbi18n owns the argv: "ru,us" needs grp:alt_shift_toggle or
                # the Latin half is unreachable and a password with a digit or
                # a Latin letter in it cannot be typed at all.
                subprocess.run(nbi18n.xkb_args(code), capture_output=True,
                               timeout=10)
            except Exception:
                pass
            self.e_pw.set_text("")
            self.e_pw2.set_text("")

        def _finish(self, *_a):
            name = self.e_name.get_text().strip()
            if not valid_hostname(name):
                self.err.set_text(_t("Use letters, digits and - for the name."))
                self.e_name.grab_focus()
                return
            pw = ""
            if not self.cb_none.get_active():
                pw = self.e_pw.get_text()
                if not pw:
                    self.err.set_text(_t("Choose a password, or tick the box "
                                         "below to start without one."))
                    self.e_pw.grab_focus()
                    return
                if pw != self.e_pw2.get_text():
                    self.err.set_text(_t("The two passwords are different."))
                    self.e_pw2.set_text("")
                    self.e_pw2.grab_focus()
                    return
            answers = {"hostname": name,
                       "username": self.e_user.get_text().strip(),
                       "lang": self._langs[max(0, self.c_lang.get_active())][0],
                       "kbd": self._kbds[max(0, self.c_kbd.get_active())][0],
                       "password": pw}
            failed = apply(answers)
            if failed:
                # Say what did not stick and STAY on the screen: finishing on a
                # half-written machine would hand somebody a computer whose
                # password is not the one they just chose.
                self.err.set_text(
                    _t("This could not be saved: %s. Try again.")
                    % ", ".join(_t(PART_NAMES.get(f, f)) for f in failed))
                return
            Gtk.main_quit()

    FirstRun()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
