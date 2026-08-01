#!/usr/bin/env python3
"""login_shadow_selftest — the shadow matrix for de/login.py.

    DISPLAY=:0 python3 tools/login_shadow_selftest.py

WHY THIS FILE EXISTS
de/login.py is the only code in this OS that can permanently lock its owner out
of their own computer. There is no getty on tty1, root is locked on a shipped
image, the serial debug shell only runs with `nbdebug` on the kernel command
line, and the machine has no network. If the sign-in screen appears over a
stored hash that nothing on the machine can verify, the owner's own password is
rejected forever and the machine is scrap.

So the invariant this file exists to hold is not "has_password() returns the
right boolean". It is:

    has_password(u) is True  =>  verify(u, the-real-password) is True

Six shapes were measured breaking that invariant before this test existed:
`root:` with nothing after it, a hash carrying the line's own newline, one
carrying a carriage return, a whitespace-only field, an algorithm the machine's
libcrypt does not implement, and a Python with no `crypt` module at all.

THE CRYPT PROBLEM, STATED HONESTLY
The guest runs Python 3.11, which HAS `crypt`; this host runs 3.13, which does
NOT. A test that simply imported crypt would therefore test nothing that ships.
So each case is run against three INJECTED crypt modules, and the guest's is
the one that matters:

  glibc     what the target actually links (output/target/lib/libcrypt.so.1 is
            glibc's): DES, $1$, $5$, $6$ — and NULL, which reaches Python as
            None, for anything else. This is the guest.
  libxcrypt the wider implementation (yescrypt, bcrypt); modelled from this
            host's own libcrypt.so.1 when it has one.
  absent    no crypt module at all — the Python 3.13 shape, and the one that
            used to brick the machine silently.

Every flavour is driven through the real de/login.py functions on a real
temporary /etc/shadow, and the GUI failure path is driven on a real window.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/"
                        "opt/notebook/de")
sys.path.insert(0, DE)

FAILURES = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(what)
        print("  FAIL  %s" % what)
    return bool(cond)


# ---------------------------------------------------------------- crypt shims
def _libcrypt():
    """This host's crypt(3), or None. Used to MAKE the fixture hashes and to
    model the libxcrypt flavour; never to decide what the guest does."""
    for name in ("libcrypt.so.1", "libcrypt.so.2", "libcrypt.so"):
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.crypt.restype = ctypes.c_char_p
        lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]

        def call(word, salt, _lib=lib):
            try:
                r = _lib.crypt(word.encode("utf-8", "surrogateescape"),
                               salt.encode("utf-8", "surrogateescape"))
            except Exception:
                return None
            return r.decode("utf-8", "replace") if r else None
        return call
    return None


HOST_CRYPT = _libcrypt()
if HOST_CRYPT is None:
    print("cannot load libcrypt.so on this host — the matrix needs a real "
          "crypt(3) to build its fixtures")
    raise SystemExit(2)

# The algorithms glibc's libcrypt implements. Anything else it answers NULL to,
# which is exactly the case that used to show an unsatisfiable sign-in screen.
_GLIBC_PREFIXES = ("$1$", "$5$", "$6$")


def _glibc_crypt(word, salt):
    if salt.startswith("$") and not salt.startswith(_GLIBC_PREFIXES):
        return None                      # unimplemented algorithm -> NULL
    return HOST_CRYPT(word, salt)


def _make_module(fn):
    m = types.ModuleType("crypt")
    m.crypt = fn
    m.METHOD_SHA512 = "6"
    m.mksalt = lambda _m=None: "$6$notebook"
    return m


FLAVOURS = [
    ("glibc (the guest)", _make_module(_glibc_crypt)),
    ("libxcrypt", _make_module(HOST_CRYPT)),
    ("absent (py3.13)", None),
]


class _CryptFlavour:
    """Install one crypt flavour for the duration of a `with` block, including
    making `import crypt` itself fail for the 'absent' flavour."""

    def __init__(self, module):
        self.module = module

    def __enter__(self):
        import builtins
        self._saved = sys.modules.pop("crypt", None)
        self._import = builtins.__import__
        if self.module is None:
            def guard(name, *a, **k):
                if name == "crypt":
                    raise ImportError("No module named 'crypt'")
                return self._import(name, *a, **k)
            builtins.__import__ = guard
        else:
            sys.modules["crypt"] = self.module
        return self

    def __exit__(self, *_exc):
        import builtins
        builtins.__import__ = self._import
        sys.modules.pop("crypt", None)
        if self._saved is not None:
            sys.modules["crypt"] = self._saved
        return False


# ------------------------------------------------------------------- fixtures
PW = "correct horse"
H6 = HOST_CRYPT(PW, "$6$notebook")          # SHA-512, what the installer writes
H5 = HOST_CRYPT(PW, "$5$notebook")          # SHA-256
H1 = HOST_CRYPT(PW, "$1$notebook")          # MD5
HDES = HOST_CRYPT(PW, "ab")                 # traditional DES

# Algorithms glibc does NOT implement. Hashed for real where this host can (so
# the invariant is tested against a genuine hash, not a made-up string), and
# left as a plausible literal where it cannot. Under the glibc flavour these
# must make has_password() answer False; under libxcrypt they are ordinary
# verifiable passwords.
HY = HOST_CRYPT(PW, "$y$j9T$saltsaltsaltsa$") \
    or "$y$j9T$saltsaltsaltsa$" + "a" * 43
HB = HOST_CRYPT(PW, "$2b$05$abcdefghijklmnopqrstuv") \
    or "$2b$05$abcdefghijklmnopqrstuv" + "a" * 31
# A hash is only a fixture for "the password works" if it really round-trips
# here; otherwise the case is a shape test with no password behind it.
GOOD_Y = PW if HOST_CRYPT(PW, HY) == HY else None
GOOD_B = PW if HOST_CRYPT(PW, HB) == HB else None

PASSWD_INSTALLED = ("root:x:0:0:root:/root:/bin/sh\n"
                    "nobody:x:65534:65534:nobody:/home:/bin/false\n")

TMP = tempfile.mkdtemp(prefix="login-selftest-")


def _write(name, text, mode=0o600):
    p = os.path.join(TMP, name)
    with open(p, "w") as fh:
        fh.write(text)
    os.chmod(p, mode)
    return p


# (label, shadow file contents or None to delete, the password that must work
#  or None when NOTHING may be accepted)
CASES = [
    ("no shadow file at all",       None,                                None),
    ("empty shadow file",           "",                                  None),
    ("user absent from shadow",     "daemon:*:::::::\n",                 None),
    ("locked  !",                   "root:!:::::::\n",                   None),
    ("locked  !!",                  "root:!!:::::::\n",                  None),
    ("locked  *  (this build)",     "root:*:::::::\n",                   None),
    ("locked hash  !$6$...",        "root:!%s:19000:0:99999:7:::\n" % H6, None),
    ("empty field  root::",         "root::19000:0:99999:7:::\n",        None),
    ("whitespace-only field",       "root:   :19000::::::\n",            None),
    ("line truncated to the name",  "root\n",                            None),
    ("line truncated to 'root:'",   "root:\n",                           None),
    ("garbage in the hash field",   "root:NOTAHASH:19000::::::\n",       None),
    ("binary junk file",            "\x00\x01\x02\xff\n",                None),
    ("no trailing newline, locked", "root:*:19000::::::",                None),
    ("$6$  (the installer's own)",  "root:%s:19000:0:99999:7:::\n" % H6, PW),
    ("$6$ as the LAST field",       "root:%s\n" % H6,                    PW),
    ("$6$ with a stray CR",         "root:%s\r\n" % H6,                  PW),
    ("$6$ with no trailing NL",     "root:%s:19000::::::" % H6,          PW),
    ("$5$ SHA-256",                 "root:%s:19000::::::\n" % H5,        PW),
    ("$1$ MD5",                     "root:%s:19000::::::\n" % H1,        PW),
    ("DES, 13 chars",               "root:%s:19000::::::\n" % HDES,      PW),
    ("$y$ yescrypt (not in glibc)", "root:%s:19000::::::\n" % HY,        GOOD_Y),
    ("$2b$ bcrypt (not in glibc)",  "root:%s:19000::::::\n" % HB,        GOOD_B),
    ("truncated $6$ (salt only)",   "root:$6$notebook:19000::::::\n",    None),
]


def run_matrix(login):
    login.PASSWD = _write("passwd", PASSWD_INSTALLED, 0o644)
    for flavour, module in FLAVOURS:
        print("\n-- crypt flavour: %s" % flavour)
        for label, shadow, good in CASES:
            if shadow is None:
                login.SHADOW = os.path.join(TMP, "no-such-shadow")
                try:
                    os.unlink(login.SHADOW)
                except OSError:
                    pass
            else:
                login.SHADOW = _write("shadow", shadow)
            with _CryptFlavour(module):
                try:
                    hp = login.has_password("root")
                except Exception as e:                         # noqa: BLE001
                    check(False, "%s / %s: has_password RAISED %r"
                          % (flavour, label, e))
                    continue

                # THE INVARIANT. Showing the screen is only ever allowed when
                # the real password actually gets through it.
                if hp:
                    try:
                        ok = login.verify("root", good) if good else False
                    except Exception as e:                     # noqa: BLE001
                        ok = "RAISED %r" % e
                    check(ok is True,
                          "%s / %s: has_password=True but the real password "
                          "does NOT verify (%r) -> LOCKED OUT"
                          % (flavour, label, ok))
                else:
                    # Skipping the screen is always safe, but it must not be
                    # skipped on the one shape the installer actually writes.
                    if good and module is not None \
                            and label.startswith(("$6$", "$5$", "$1$", "DES")):
                        check(False, "%s / %s: a verifiable password was "
                                     "treated as no password -> the machine "
                                     "would start with NO sign-in screen"
                              % (flavour, label))

                # The brick case, stated positively: on the library the target
                # actually links, an algorithm it cannot compute must never
                # raise a prompt.
                if flavour.startswith("glibc") \
                        and label.startswith(("$y$", "$2b$")):
                    check(hp is False,
                          "glibc / %s: an algorithm this libcrypt cannot "
                          "compute must NOT raise a sign-in screen" % label)

                # A wrong password must never get in, whatever the shape.
                try:
                    bad = login.verify("root", "not-the-password")
                except Exception as e:                         # noqa: BLE001
                    bad = "RAISED %r" % e
                check(bad is False,
                      "%s / %s: a WRONG password was accepted (%r)"
                      % (flavour, label, bad))

                # The empty string is what an impatient user presses Enter on.
                try:
                    blank = login.verify("root", "")
                except Exception as e:                         # noqa: BLE001
                    blank = "RAISED %r" % e
                check(blank is False,
                      "%s / %s: the EMPTY password was accepted (%r)"
                      % (flavour, label, blank))
        print("   %d cases" % len(CASES))


def run_unreadable(login):
    """A shadow the process cannot read is 'nothing to ask for', never a
    prompt: login.py runs as root today, but a build that ever stopped doing so
    must fail open rather than strand somebody."""
    print("\n-- unreadable / unopenable shadow")
    with _CryptFlavour(FLAVOURS[0][1]):
        p = _write("shadow", "root:%s:19000::::::\n" % H6, 0o000)
        login.SHADOW = p
        if os.geteuid() == 0:
            print("   (running as root: chmod 000 is not a barrier, skipped)")
        else:
            check(login.has_password("root") is False,
                  "chmod 000 shadow: has_password must be False")
        os.chmod(p, 0o600)

        d = os.path.join(TMP, "shadow-is-a-directory")
        os.makedirs(d, exist_ok=True)
        login.SHADOW = d
        check(login.has_password("root") is False,
              "shadow is a directory: has_password must be False")

        login.SHADOW = os.path.join(TMP, "nope", "deeper", "shadow")
        check(login.has_password("root") is False,
              "shadow under a missing directory: has_password must be False")


def run_desktop_user(login):
    """Who the screen asks for. Getting this wrong either asks for an account
    nobody set (a brick) or skips the screen on a machine that has one."""
    print("\n-- desktop_user()")
    login.SHADOW = _write("shadow", "root:%s:19000::::::\n" % H6)
    os.environ["NB_HOME"] = "/root"
    with _CryptFlavour(FLAVOURS[0][1]):
        cases = [
            ("installed machine: root only, root has the password",
             PASSWD_INSTALLED, "root"),
            ("no passwd file at all", None, "root"),
            ("passwd is junk", "\x00\x01 not a passwd file\n", "root"),
            ("service accounts only, none loginable",
             "daemon:x:1:1:daemon:/usr/sbin:/bin/false\n"
             "nobody:x:65534:65534:nobody:/home:/bin/false\n", "root"),
            ("short lines are skipped, not crashed on",
             "broken\nalso:broken:2\n" + PASSWD_INSTALLED, "root"),
            ("a non-numeric uid is skipped, not crashed on",
             "weird:x:notanumber:0:w:/home/w:/bin/sh\n" + PASSWD_INSTALLED,
             "root"),
        ]
        for label, passwd, want in cases:
            if passwd is None:
                login.PASSWD = os.path.join(TMP, "no-such-passwd")
                try:
                    os.unlink(login.PASSWD)
                except OSError:
                    pass
            else:
                login.PASSWD = _write("passwd", passwd, 0o644)
            try:
                got = login.desktop_user()
            except Exception as e:                             # noqa: BLE001
                got = "RAISED %r" % e
            check(got == want, "desktop_user, %s: got %r want %r"
                  % (label, got, want))
    login.PASSWD = _write("passwd", PASSWD_INSTALLED, 0o644)


# Runs the REAL login.py as __main__ with --needed, against fixture files, in a
# fresh interpreter. /etc/shadow and /etc/passwd are redirected at builtins.open
# rather than by editing the module, so what is executed is exactly what ships.
# The exit code carries both answers: +10 means the fast path pulled in Gtk,
# which would put a GTK import on every boot of every machine, password or not.
#
# The subprocess is this host's Python 3.13, which has no `crypt`; the guest's
# 3.11 does. So the driver installs the GUEST's crypt (glibc's crypt(3), via
# ctypes) unless asked not to — `with_crypt=0` is then the honest test of the
# 3.13 shape, where the answer must be "no screen".
_NEEDED_DRIVER = r"""
import builtins, ctypes, runpy, sys, types
if %d:
    _lib = ctypes.CDLL("libcrypt.so.1")
    _lib.crypt.restype = ctypes.c_char_p
    _lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    def _c(word, salt):
        if salt.startswith("$") and not salt.startswith(("$1$", "$5$", "$6$")):
            return None                     # glibc: unimplemented -> NULL
        r = _lib.crypt(word.encode(), salt.encode())
        return r.decode() if r else None
    m = types.ModuleType("crypt"); m.crypt = _c
    m.METHOD_SHA512 = "6"; m.mksalt = lambda _m=None: "$6$notebook"
    sys.modules["crypt"] = m
MAP = {"/etc/shadow": %r, "/etc/passwd": %r}
_open = builtins.open
builtins.open = lambda f, *a, **k: _open(MAP.get(f, f), *a, **k)
sys.argv = ["login.py", "--needed"]
code = 99
try:
    runpy.run_path(%r, run_name="__main__")
except SystemExit as e:
    code = 0 if e.code in (0, None) else int(e.code)
sys.exit(code + (10 if "gi" in sys.modules else 0))
"""


def run_needed_flag():
    """`--needed` is what session.sh branches on. It must give the same answer
    has_password() does, and give it WITHOUT importing Gtk — session.sh runs it
    on every single boot, including every live boot that has no password."""
    print("\n-- login.py --needed  (the real script, as __main__)")
    env = dict(os.environ)
    env["PYTHONPATH"] = DE
    script = os.path.join(DE, "login.py")
    d = tempfile.mkdtemp(prefix="needed-")
    pp = os.path.join(d, "passwd")
    with open(pp, "w") as fh:
        fh.write(PASSWD_INSTALLED)

    def ask(shadow, with_crypt=1):
        sp = os.path.join(d, "shadow")
        with open(sp, "w") as fh:
            fh.write(shadow)
        r = subprocess.run(
            [sys.executable, "-c",
             _NEEDED_DRIVER % (with_crypt, sp, pp, script)],
            env=env, capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stderr or "")[-300:]

    for label, shadow, crypt_on, want in (
            ("a real $6$ password", "root:%s:19000::::::\n" % H6, 1, 0),
            ("a locked account", "root:*:::::::\n", 1, 1),
            ("an empty password", "root::19000::::::\n", 1, 1),
            ("a $6$ hash as the last field on the line",
             "root:%s\n" % H6, 1, 0),
            ("yescrypt, which glibc cannot compute",
             "root:%s:19000::::::\n" % HY, 1, 1),
            ("a truncated line", "root:\n", 1, 1),
            ("a real $6$ password but NO crypt module (the py3.13 shape)",
             "root:%s:19000::::::\n" % H6, 0, 1)):
        rc, err = ask(shadow, crypt_on)
        check(rc == want,
              "--needed on %s: exit %d, want %d%s"
              % (label, rc, want,
                 "  (Gtk was imported on the fast path)" if rc >= 10 else
                 ("  " + err if err else "")))

    # ...and against this machine's own real /etc/shadow: whatever it finds, it
    # must exit cleanly and print no traceback.
    r = subprocess.run([sys.executable, script, "--needed"], env=env,
                       capture_output=True, text=True, timeout=120)
    check(r.returncode in (0, 1) and "Traceback" not in r.stderr,
          "login.py --needed against the real system: rc=%d stderr=%r"
          % (r.returncode, (r.stderr or "")[-300:]))


def run_geometry(login):
    """The size the sign-in window asks for, BEFORE anything maps it.

    Measured on a real installed machine: this screen came up as a ~350x365
    panel in the top-left corner, on the bare desktop field, with the footer
    line clipped at its edge — the first thing the owner ever saw. fullscreen()
    is only a request, and matchbox acts on _NET_WM_STATE_FULLSCREEN for APP
    clients only, so as a splash/dialog client the window was simply granted
    the natural size of its centre column. Nobody caught it because the live
    ISO has no password, so the screen only exists after an install.

    Checked without mapping anything: the size has to be right in the window's
    own request, not merely arranged by a window manager that may not be up."""
    print("\n-- the size the sign-in window asks for")
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk
    login.SHADOW = _write("shadow", "root:%s:19000::::::\n" % H6)
    scr = Gdk.Screen.get_default()
    sw, sh = scr.get_width(), scr.get_height()
    with _CryptFlavour(FLAVOURS[0][1]):
        win = login.Login()
        gw, gh = win.get_size()
        check((gw, gh) == (sw, sh),
              "the window asks for the whole screen: %dx%d, screen is %dx%d"
              % (gw, gh, sw, sh))
        check(gw > 800 and gh > 500,
              "...and that is a screen-sized request, not a panel: %dx%d"
              % (gw, gh))
        check(win._fit_screen() == (sw, sh),
              "_fit_screen reports the display size")

        # No display to ask? It must still choose something that covers a
        # screen, never fall back to a natural size.
        real = Gdk.Screen.get_default
        try:
            Gdk.Screen.get_default = staticmethod(lambda: None)
            w2, h2 = win._fit_screen()
        finally:
            Gdk.Screen.get_default = real
        check(w2 >= 1024 and h2 >= 740,
              "with no screen to ask, it still covers a display: %dx%d"
              % (w2, h2))

        # Nothing on the screen may have a minimum bigger than the smallest
        # panel this OS supports, or it is clipped and unreachable there.
        minsz, _nat = win.get_preferred_size()
        check(minsz.width <= 1024 and minsz.height <= 740,
              "the sign-in screen fits the smallest supported panel: "
              "needs %dx%d" % (minsz.width, minsz.height))
        try:
            win.destroy()
        except Exception:                                      # noqa: BLE001
            pass


def run_gui(login):
    """The window itself: it must construct, and a wrong password must produce
    the recovery affordances rather than a dead end."""
    print("\n-- the window")
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    login.SHADOW = _write("shadow", "root:%s:19000::::::\n" % H6)
    with _CryptFlavour(FLAVOURS[0][1]):
        win = login.Login()
        win.show_all()
        n = 0
        while Gtk.events_pending() and n < 400:
            Gtk.main_iteration()
            n += 1
        check(win.user == "root", "the window asks for %r" % win.user)

        # The window TYPE decides whether the desktop's menu bar can sit on
        # top of the lock screen. matchbox routes _NET_WM_WINDOW_TYPE_SPLASH
        # to dialog_client_new(), and our WM patch stacks the DOCK panel above
        # every dialog unless a mapped fullscreen APP is present — so anything
        # but a plain fullscreen app leaves the panel (clock, app menus, Shut
        # Down) visible and clickable over a locked screen.
        from gi.repository import Gdk
        hint = win.get_type_hint()
        check(hint == Gdk.WindowTypeHint.NORMAL,
              "the sign-in window must be a normal fullscreen app window, not "
              "%r — see package/matchbox/0003-panel-menu-bar-above-dialogs"
              % hint)
        check(win.get_skip_taskbar_hint() is True,
              "the sign-in window stays out of any task list")
        src = open(os.path.join(DE, "login.py"), encoding="utf-8").read()
        check("self.fullscreen()" in src,
              "the sign-in window asks to be fullscreen (the state the WM "
              "patch looks for)")
        check("map-event" in src,
              "...and re-asserts it after mapping, which is the only point "
              "matchbox acts on EWMH requests")
        check(win._show.get_visible() is False,
              "'Show password' must be hidden until something has failed")
        check(win._recall.get_visible() is False,
              "the reminder must be hidden until something has failed")
        check(win.entry.get_visibility() is False,
              "the field must start masked")

        win.entry.set_text("wrong")
        win._try()
        n = 0
        while Gtk.events_pending() and n < 400:
            Gtk.main_iteration()
            n += 1
        check(win._tries == 1, "a failed attempt is counted")
        check(win.entry.get_text() == "", "the field is cleared after a failure")
        check(win.error.get_visible() is True, "the failure is stated")
        check(win._show.get_visible() is True,
              "'Show password' appears after a failure")
        check(win._recall.get_visible() is True,
              "the reminder appears after a failure")
        check(bool(win._recall.get_text().strip()),
              "the reminder actually says something")

        win._show.set_active(True)
        check(win.entry.get_visibility() is True,
              "'Show password' really reveals the field")
        win._show.set_active(False)
        check(win.entry.get_visibility() is False,
              "unticking it masks the field again")

        # Three failures throttle, and the throttle must RELEASE.
        for _ in range(2):
            win.entry.set_text("wrong")
            win._try()
        check(win.entry.get_sensitive() is False,
              "the field is held after three failures")
        win._re_enable()
        check(win.entry.get_sensitive() is True,
              "the hold releases; a slow guesser is never locked out")
        try:
            win.destroy()
        except Exception:                                      # noqa: BLE001
            pass


def main():
    os.environ.setdefault("NB_HOME", os.path.join(TMP, "home"))
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    import login
    run_matrix(login)
    run_unreadable(login)
    run_desktop_user(login)
    run_needed_flag()
    try:
        run_geometry(login)
    except Exception as e:                                     # noqa: BLE001
        check(False, "the sign-in window could not be measured: %r" % e)
    try:
        run_gui(login)
    except Exception as e:                                     # noqa: BLE001
        check(False, "the sign-in window could not be driven: %r" % e)

    print()
    if FAILURES:
        print("LOGIN SHADOW SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        return 1
    print("LOGIN SHADOW SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
