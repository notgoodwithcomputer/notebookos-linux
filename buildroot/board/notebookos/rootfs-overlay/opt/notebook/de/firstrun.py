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
import tempfile
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

    nbkeyboard owns these rules now, and it gets the case this body never
    could: a VARIANT INSIDE A MULTI-GROUP CODE. "jp(kana),us" fell through the
    regex whole, so XkbLayout was written as the literal string "jp(kana),us"
    — a layout name the server has never heard of — and the machine came up
    with no keymap. The old body is kept as the fallback, because this screen
    runs before anything has proved the de/ tree is intact.
    """
    try:
        import nbkeyboard                                      # noqa: PLC0415
        return nbkeyboard.xorg_parts(code)
    except Exception:                                          # noqa: BLE001
        pass
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
    shadow_tmp = None
    try:
        try:
            old_stat = os.stat(SHADOW)
        except OSError:
            old_stat = None
        fd, shadow_tmp = tempfile.mkstemp(
            prefix=".shadow.firstrun-", dir=os.path.dirname(SHADOW) or ".")
        with os.fdopen(fd, "w") as fh:
            fh.writelines(out)
            if old_stat is not None:
                os.fchmod(fh.fileno(), old_stat.st_mode & 0o7777)
                try:
                    os.fchown(fh.fileno(), old_stat.st_uid, old_stat.st_gid)
                except PermissionError:
                    pass
            else:
                os.fchmod(fh.fileno(), 0o600)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(shadow_tmp, SHADOW)
        shadow_tmp = None
        dirfd = os.open(os.path.dirname(SHADOW) or ".", os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        return True
    except OSError:
        if shadow_tmp is not None:
            try:
                os.unlink(shadow_tmp)
            except OSError:
                pass
        return False


def write_user_name(name):
    """What the machine calls its owner: shown at sign-in in place of "root".

    THE NAME GOES DOWN WHOLE. This used to cut at 40 characters, silently and
    at the writing end, so a 53-character name reached the disk as
    "Bartholomew Alexander Fitzgerald-Montgom" -- chopped mid-word, while the
    full name sat on screen looking kept, and reported as saved. The sign-in
    screen then greets that stump every morning: this screen never runs again,
    Settings has no owner-name field, and nothing else in the OS asks.

    The other two ends of this file already agree: de/login.py reads up to 120
    and ellipsizes the greeting instead, which is what a cut is supposed to
    look like, and de/installer.py writes the same file with no cut at all. A
    limit belongs where the person can see it -- the entry is capped at that
    same 120 while the name is being typed (see _build) -- and never here,
    after they have been told it was saved.
    """
    try:
        with open(USER_NAME_FILE, "w") as fh:
            fh.write((name or "").strip() + "\n")
        return True
    except OSError:
        return False


class _InLang:
    """nbi18n, answering in the language picked ON THIS SCREEN.

    nbi18n resolves every string through ONE module-level catalog, fixed when
    the process started from the language the machine was installed with. That
    is right everywhere else in the OS and wrong here: this is the screen where
    somebody changes it, and the answer has to be legible to the person who
    just gave it. settings.py reaches for the same catalog by hand for the same
    reason (_on_region_lang).

    Swapping the catalog for the length of a redraw makes BOTH the _t() calls
    in this file and nbi18n's own auto-translate hooks -- which look up every
    string handed to set_text/set_label -- answer in the chosen language. The
    process's own language is put back immediately: what the rest of the
    desktop reads is locale.json, written at Finish.
    """
    _cache = {}

    def __init__(self, code):
        self.code = code
        self._saved = None

    def __enter__(self):
        # Nothing in here may raise. This screen runs before anything has
        # proved the de/ tree is intact, and a setup screen that cannot be
        # finished is a machine that cannot be used: a catalog that will not
        # load leaves the language as the process found it, which is the same
        # graceful English fallback nbi18n itself makes.
        try:
            import nbi18n                                      # noqa: PLC0415
            self._saved = (nbi18n._LANG, nbi18n._CAT)
            if self.code not in self._cache:
                self._cache[self.code] = nbi18n._load_catalog(self.code)
            nbi18n._LANG = self.code
            nbi18n._CAT = self._cache[self.code]
        except Exception:                                      # noqa: BLE001
            self._saved = None
        return self

    def __exit__(self, *_exc):
        if self._saved is None:
            return False
        try:
            import nbi18n                                      # noqa: PLC0415
            nbi18n._LANG, nbi18n._CAT = self._saved
        except Exception:                                      # noqa: BLE001
            pass
        return False


def write_hostname(name):
    """Persist the computer's name, and set it on the running system too.

    THE ANSWER IS THE FILE, AND ONLY THE FILE. Setting the name on the running
    kernel is a nicety that saves a reboot; it is not the state the machine
    keeps, and it used to share this function's one try: a `hostname` that was
    missing, unrunnable or slow enough to hit the timeout raised, this returned
    False, apply() put "Computer name" in the failed list and therefore NEVER
    CLEARED THE MARKER. The name was already correctly on disk, so there was
    nothing for the new owner to correct and no way to correct it: pressing
    Finish again ran the same command and failed the same way, and the machine
    came back to this setup screen on every boot with no route past it -- on
    the one screen that has to be completed before the computer can be used.

    write_keyboard already keeps the two apart for exactly this reason: it
    returns whether the CONFIG FILE was written and applies the layout to the
    running server as a separate best effort.
    """
    try:
        os.makedirs(os.path.dirname(HOSTNAME_FILE), exist_ok=True)
        with open(HOSTNAME_FILE, "w") as fh:
            fh.write(name + "\n")
    except Exception:
        return False
    # Take effect now as well, so the name is right without a reboot. Best
    # effort, deliberately outside the answer above.
    try:
        subprocess.run(["hostname", name], capture_output=True, timeout=10)
    except Exception:
        pass
    return True


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
    # Through nbi18n rather than assembled here: the argv it returns also
    # CLEARS the options the server is already carrying, so a machine that
    # loaded a dual layout earlier in this same session does not keep Alt+Shift
    # bound after moving to a single one.
    try:
        import nbi18n                                          # noqa: PLC0415
        result = subprocess.run(nbi18n.xkb_args(code), capture_output=True,
                                timeout=10)
        if result.returncode != 0:
            ok = False
    except Exception:
        ok = False
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
              "password": "Password", "completion": "Finish"}


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
    keyboard_ok = write_keyboard(answers["kbd"])
    if not keyboard_ok:
        failed.append("keyboard")
    if not write_locale(answers["lang"], answers["kbd"]):
        failed.append("language")
    # Never hash a password while the running keyboard differs from the layout
    # just persisted for boot.  Retrying setup is recoverable; storing bytes
    # typed through the wrong keymap can lock the owner out after restart.
    if not keyboard_ok:
        pass
    elif answers.get("password"):
        hashed = hash_password(answers["password"])
        if hashed is None or not set_root_password(hashed):
            failed.append("password")
    else:
        # No password: leave root locked, exactly as the image ships. login.py
        # then has nothing to ask for and the machine starts straight in.
        if not set_root_password(None):
            failed.append("password")
    if not failed and not clear_marker():
        failed.append("completion")
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
                border: 1px solid #C9C4B6; border-radius: 8px; color: #1A1916; }
    /* A FIELD THAT CANNOT BE TYPED INTO MUST LOOK IT. The rule above sets
       colour, background and border unconditionally at APPLICATION priority,
       so it repainted the two password fields to look exactly alive after
       "start straight into the desktop without a password" had switched them
       off -- the one tick on this screen that decides a machine has no
       password at all, and it showed nothing. These are Papertone's own
       UNAVAILABLE tones (inkoff/hairoff/paperoff), written out because this
       string must stay ASCII and self-contained. */
    .fr-entry:disabled { color: #A9A395; background: #F1EEE6;
                border-color: #DDD8CB; }
    /* The tick that goes off with them, for the same reason: the theme's
       `* { color: ... }` reaches every label in the OS, so a switched-off
       checkbutton keeps full-strength ink in its caption unless something
       says otherwise. */
    .fr-bg checkbutton:disabled label { color: #A9A395; }
    /* The 6px between a tick box and its own caption is written in the theme
       as `margin-right` -- a physical side with no right-to-left counterpart
       -- so in Yiddish, the one RTL interface language, the gap lands on the
       far side of the box and the caption's last letter touches the border.
       Mirrored here for the first screen a Yiddish owner meets; the OS-wide
       fix belongs in Papertone's own `check, radio` rule. */
    .fr-bg check:dir(rtl), .fr-bg radio:dir(rtl) {
                margin-right: 0; margin-left: 6px; }
    .fr-go { background: #1A1916; border: 1px solid #1A1916; border-radius: 8px;
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
            # The language this screen is SPEAKING, and what it is saying in
            # it. Both start as the process's own; _on_lang moves them.
            self._lang = nbi18n.current_lang()
            self._said = []                 # (widget, its English source)
            self._line = None               # what _say() last put up
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
                self._said.append((mark, "Notebook OS"))
            mark.set_margin_bottom(10)
            outer.pack_start(mark, False, False, 0)

            t = Gtk.Label(label=_t("Setup"))
            t.get_style_context().add_class("fr-title")
            self._said.append((t, "Setup"))
            # The scene-setting line under this title is gone; its bottom margin
            # moves here so the gap above the fields is unchanged.
            t.set_margin_bottom(22)
            outer.pack_start(t, False, False, 0)

            grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            grid.set_size_request(420, -1)

            # `src` is the ENGLISH source, not the finished caption: the
            # language can change while this screen is open (see _on_lang), and
            # a widget cannot be asked what it used to say.
            def field(src, widget):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                lb = Gtk.Label(label=_t(src), xalign=0)
                lb.get_style_context().add_class("fr-label")
                lb.set_mnemonic_widget(widget)
                self._said.append((lb, src))
                try:
                    widget.get_accessible().set_name(lb.get_text())
                except Exception:
                    pass
                box.pack_start(lb, False, False, 0)
                box.pack_start(widget, False, False, 0)
                return box

            self.e_user = Gtk.Entry()
            self.e_user.get_style_context().add_class("fr-entry")
            # WHICH name, and what it is for. "NAME" sits directly above
            # "COMPUTER NAME" with nothing to tell the two questions apart, and
            # leaving it empty is not an error here -- it just leaves the
            # sign-in screen greeting "root" every morning, on a screen that
            # never runs again and that nothing else in the OS can re-ask.
            # These are the installer's own words for the same field, and as a
            # placeholder they show exactly when the field is empty, which is
            # the case they are about. Not a line UNDER the field, which is
            # what installer.py can afford: this form already measures 737px in
            # ja/zh/ko against a 740px panel, and a second line would be 20 of
            # them.
            self.e_user.set_placeholder_text(_t("Shown on the sign-in screen"))
            self._said.append((self.e_user, "Shown on the sign-in screen"))
            # The only limit, and it is visible while typing: what the machine
            # keeps used to be silently cut to 40 characters at write time.
            # 120 is what de/login.py reads back.
            self.e_user.set_max_length(120)
            grid.pack_start(field("NAME", self.e_user),
                            False, False, 0)

            self.e_name = Gtk.Entry()
            self.e_name.set_text("notebook")
            self.e_name.get_style_context().add_class("fr-entry")
            grid.pack_start(field("COMPUTER NAME",
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
            # What the screen is SPEAKING is whatever the drop-down ended up
            # showing, not what the file said: a language the list does not
            # carry lands the combo on its first row, and the two must not
            # disagree or _on_lang's "nothing changed" guard reads the wrong
            # one.
            self._lang = self._langs[cur_lang][0]
            # Connected AFTER set_active, exactly as the keyboard below is, so
            # building the form does not count as choosing a language.
            self.c_lang.connect("changed", self._on_lang)
            grid.pack_start(field("LANGUAGE", self.c_lang), False, False, 0)

            self.c_kbd = Gtk.ComboBoxText()
            self._kbds = list(nbi18n.KEYBOARDS)
            cur_kbd = 0
            for i, (code, name) in enumerate(self._kbds):
                self.c_kbd.append_text(name)
                if code == nbi18n.keyboard():
                    cur_kbd = i
            self.c_kbd.set_active(cur_kbd)
            self._kbd_active = cur_kbd
            self._kbd_changing = False
            # Connected AFTER set_active, so choosing the layout the machine is
            # already using does not fire and wipe the fields below.
            self.c_kbd.connect("changed", self._on_kbd)
            grid.pack_start(field("KEYBOARD", self.c_kbd), False, False, 0)

            self.e_pw = Gtk.Entry()
            self.e_pw.set_visibility(False)
            self.e_pw.get_style_context().add_class("fr-entry")
            self.e_pw.set_activates_default(True)
            grid.pack_start(field("PASSWORD", self.e_pw), False, False, 0)

            self.e_pw2 = Gtk.Entry()
            self.e_pw2.set_visibility(False)
            self.e_pw2.get_style_context().add_class("fr-entry")
            self.e_pw2.set_activates_default(True)
            grid.pack_start(field("PASSWORD AGAIN", self.e_pw2),
                            False, False, 0)

            # Read back what is being typed. It matters more here than anywhere
            # else in the OS: the layout above can now change under the fields
            # (see _on_kbd), and on a Russian or Greek keyboard the difference
            # between the password that gets stored and the one the owner
            # thinks they chose is invisible behind dots. There is no way back
            # into this machine afterwards.
            self.cb_show = Gtk.CheckButton(label=_t("Show password"))
            # The id is kept because _on_none turns this tick off, and a
            # set_active fires "toggled" like a click does.
            self._show_h = self.cb_show.connect("toggled", self._on_show)
            self._said.append((self.cb_show, "Show password"))
            grid.pack_start(self.cb_show, False, False, 0)

            self.cb_none = Gtk.CheckButton(
                label=_t("Start straight into the desktop without a password"))
            self.cb_none.connect("toggled", self._on_none)
            self._said.append(
                (self.cb_none,
                 "Start straight into the desktop without a password"))
            grid.pack_start(self.cb_none, False, False, 0)

            note = Gtk.Label(
                label=_t("This password cannot be recovered."), xalign=0)
            note.get_style_context().add_class("fr-note")
            self._said.append((note, "This password cannot be recovered."))
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
            self._said.append((go, "Finish"))
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
            # 12, not 24. CJK line boxes are taller than Latin ones, and the
            # six stacked fields carried that difference into a form 51px
            # taller in ja/zh/ko than in English — 758px against the 1024x740
            # budget, so the one screen a machine cannot be used without had to
            # be scrolled in three languages. Trimming the outer margin is the
            # least invasive 24px available: it touches no type, no field
            # spacing and no rhythm between the rows, and it leaves ja/zh/ko
            # with room rather than exactly on the line, where one longer
            # translation would put them back over.
            # Measured again after the NAME placeholder went in: ja/zh/ko come
            # to 737 (the placeholder makes that one entry 3px taller, because
            # its CJK line box is taller than a Latin one), en/de/ru to 683,
            # against the 740 panel. Still fits with the button whole; the
            # scroller below is what carries anything a future translation
            # adds.
            outer.set_margin_top(12)
            outer.set_margin_bottom(12)
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

        def _say(self, src=None, parts=(), problem=True, about=""):
            """Put one line on this screen's single message row, or take it
            down.

            The ENGLISH source is kept as well as the finished sentence,
            because the language can change while the screen is open (see
            _on_lang) and the line has to change with it. `problem` picks the
            ink -- a complaint is red, something that merely happened is a
            note in the quiet grey -- since this one row now carries both.
            `about` says what the line concerns, so a line about a password
            can be taken down when there is not going to be one.

            The literal stays at the CALL SITE and is translated here, which
            is also what keeps these sentences visible to the text gates: a
            message parked in a module constant is a string no scanner
            following _t() can see.
            """
            self._line = (src, tuple(parts), problem, about) if src else None
            with _InLang(self._lang):
                text = _t(src) if src else ""
                if text and parts:
                    text = text % ", ".join(_t(p) for p in parts)
            ctx = self.err.get_style_context()
            ctx.remove_class("fr-note" if problem else "fr-err")
            ctx.add_class("fr-err" if problem else "fr-note")
            self.err.set_text(text)

        def _render_line(self):
            """Whatever the line says, said again in the language now chosen."""
            if self._line:
                self._say(*self._line)

        def _on_lang(self, combo):
            """Say the whole screen again in the language just chosen.

            THIS SCREEN HAS TO ANSWER. The keyboard drop-down below applies to
            the running server the moment it is picked; the language one
            changed nothing anybody could see -- on the one screen built for
            somebody handed a machine set up by a shop or a school, who may not
            read the language it was installed in. They chose their own, got no
            answer at all, and then had to finish setup, every caption and
            every error message included, in a language they could not read.

            Only the words change. Nothing typed is touched, and the keyboard
            is left exactly where the owner put it: following the language with
            its default layout would move a choice they may have just made by
            hand and would clear the password fields with it. The answer is
            still WRITTEN at Finish, by write_locale, which is what the rest of
            the desktop reads.
            """
            i = combo.get_active()
            if not (0 <= i < len(self._langs)):
                return
            # By INDEX, never get_active_text(): the visible text is the
            # translated language name, not the code it was built from.
            code = self._langs[i][0]
            if code == self._lang:
                return
            self._lang = code
            self._redraw()

        def _redraw(self):
            """Every string this screen shows, said again in self._lang."""
            with _InLang(self._lang):
                for w, source in self._said:
                    text = _t(source)
                    if isinstance(w, Gtk.Entry):
                        w.set_placeholder_text(text)
                    elif isinstance(w, Gtk.Button):
                        w.set_label(text)
                    else:
                        w.set_text(text)
            self._render_line()
            # Yiddish reads the other way, and one call mirrors packing,
            # alignment and widget order for the whole process -- the same
            # thing nbapp.apply_direction() does at import for an app that
            # STARTS in a right-to-left language.
            Gtk.Widget.set_default_direction(
                Gtk.TextDirection.RTL if self._lang in nbi18n.RTL
                else Gtk.TextDirection.LTR)

        def _on_none(self, cb):
            """Ticking this is how a machine ends up with no password at all,
            so it has to LOOK like it happened.

            The two fields go insensitive -- and the .fr-entry:disabled rule
            above is what makes that visible, since the rule beside it used to
            repaint them alive again. They are also EMPTIED, because _finish()
            throws their contents away and locks the account: a password left
            standing in a dead field says the machine will have one when it
            will not. Show password goes off with them; there is nothing left
            to show.
            """
            on = cb.get_active()
            self.e_pw.set_sensitive(not on)
            self.e_pw2.set_sensitive(not on)
            self.cb_show.set_sensitive(not on)
            if not on:
                return
            self.e_pw.set_text("")
            self.e_pw2.set_text("")
            if self.cb_show.get_active():
                # set_active fires "toggled" exactly as a click does, so the
                # handler is blocked and its one job done here instead.
                self.cb_show.handler_block(self._show_h)
                self.cb_show.set_active(False)
                self.cb_show.handler_unblock(self._show_h)
                self.e_pw.set_visibility(False)
                self.e_pw2.set_visibility(False)
            if self._line and self._line[3] == "password":
                self._say()          # it was about a password nobody will have

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
            if self._kbd_changing:
                return
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
                result = subprocess.run(nbi18n.xkb_args(code),
                                        capture_output=True, timeout=10)
                if result.returncode != 0:
                    raise RuntimeError("setxkbmap failed")
            except Exception:
                self._kbd_changing = True
                combo.set_active(self._kbd_active)
                self._kbd_changing = False
                self._say("This could not be saved: %s. Try again.",
                          ("Keyboard",))
                return
            self._kbd_active = i
            # AND SAY THAT THE PASSWORD WENT WITH IT. The clearing is right
            # (see above) and it used to happen in silence: the fields simply
            # emptied, with no message, no highlight and nothing where the
            # message line is, and the next thing the owner met was "Choose a
            # password", which names the wrong problem. Said only when
            # something was actually cleared, so choosing a layout on an
            # untouched form stays quiet.
            if self.e_pw.get_text() or self.e_pw2.get_text():
                self.e_pw.set_text("")
                self.e_pw2.set_text("")
                self._say("The keyboard changed, so the password was "
                          "cleared. Type it again.",
                          problem=False, about="password")
            else:
                self._say()

        def _finish(self, *_a):
            name = self.e_name.get_text().strip()
            if not valid_hostname(name):
                self._say("Use letters, digits and - for the name.")
                self.e_name.grab_focus()
                return
            pw = ""
            if not self.cb_none.get_active():
                pw = self.e_pw.get_text()
                if not pw:
                    self._say("Choose a password, or tick the box below "
                              "to start without one.", about="password")
                    self.e_pw.grab_focus()
                    return
                if pw != self.e_pw2.get_text():
                    self._say("The two passwords are different.",
                              about="password")
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
                self._say("This could not be saved: %s. Try again.",
                          [PART_NAMES.get(f, f) for f in failed])
                return
            Gtk.main_quit()

    FirstRun()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
