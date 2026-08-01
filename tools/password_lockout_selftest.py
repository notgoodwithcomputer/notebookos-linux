#!/usr/bin/env python3
"""password_lockout_selftest — the machine must end up with the password its
owner chose.

    DISPLAY=:0 python3 tools/password_lockout_selftest.py

WHAT THIS PROTECTS
Two screens in this OS collect the password that will guard the machine
afterwards: de/installer.py's Options step and de/firstrun.py's setup screen.
Neither of them can check its own work, because the machine that will ask for
that password does not exist yet (the installer) or has not booted since (the
first-run screen). If what gets stored is not what the person meant, nothing
finds out until the sign-in screen rejects them — and on this machine there is
no network, no getty on tty1, no second account and no password reset. It is
scrap.

So the invariant is not "a hash was written". It is:

    what the machine ends up with == what the person chose

Three measured ways that failed, all of them fixed here and all of them silent:

 1. THE KEYBOARD. Both screens offer a keyboard drop-down and a password field.
    The layout was written into the installed tree and applied NOWHERE ELSE, so
    somebody who picked French and then typed their password was still typing
    on the US layout the live medium boots with. The stored hash is of
    "qwerty"; every later boot turns those same physical keys into "azerty".
    Every non-US install was one drop-down away from a permanent lock-out.

 2. THE SHADOW FILE. firstrun's set_root_password walked /etc/shadow looking
    for a root line and, when it found none it could use, returned True having
    written nothing. Setup then reported success, removed its marker, and
    handed over a machine that starts straight into the desktop — no password
    at all, for somebody who had just chosen one.

 3. THE MARKER. It has to be the LAST thing removed and only on a clean run, or
    a machine switched off mid-setup comes up configured half-way with no route
    back to the questions.

There is no crypt module on this host's Python 3.13 (the guest ships 3.11 and
has it), so anything needing a real hash is checked against an injected crypt
rather than skipped — the guest is the machine that matters.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

HOME = "/tmp/nb-pwlock-home"
TREE = "/tmp/nb-pwlock-tree"
for d in (HOME, TREE):
    shutil.rmtree(d, ignore_errors=True)
os.makedirs(os.path.join(HOME, ".config", "notebook"), exist_ok=True)
os.makedirs(os.path.join(TREE, "etc"), exist_ok=True)
os.environ["NB_HOME"] = HOME
os.environ.pop("NB_LANG", None)

import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                    # noqa: E402

import nbapp                                                     # noqa: E402
nbapp._APP_DIR = os.path.join(HOME, "nb-apps")
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import nbi18n                                                    # noqa: E402
import firstrun                                                  # noqa: E402
import installer                                                 # noqa: E402

RESULTS = []


def chk(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n       <- %s" % (detail,)))


def head(t):
    print("\n-- %s" % t)


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def calls_get_active_text(path):
    """Does this module CALL combo.get_active_text() anywhere?

    Parsed, not grepped: both files talk about the call in a comment explaining
    why they must not make it, and a grep counts the warning as the offence."""
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_active_text"):
            return True
    return False


def build_firstrun():
    """Construct the real first-run window without entering its main loop.

    Matched by CLASS, never by window title: nbi18n translates a window's
    title, so "Welcome" is "Willkommen" on a German machine and a title match
    finds nothing on sixteen of the eighteen languages."""
    real = Gtk.main
    found = []
    Gtk.main = lambda: found.extend(
        w for w in Gtk.Window.list_toplevels()
        if type(w).__name__ == "FirstRun" and not w.in_destruction())
    try:
        firstrun.main()
    finally:
        Gtk.main = real
    return found[-1] if found else None


# ---------------------------------------------------------------------------
# A recorder standing in for setxkbmap, so the real code's real argv is what
# gets checked. Both modules reach the keyboard through a different door
# (firstrun calls subprocess.run, the installer goes through run_cmd), so both
# doors are watched rather than one being assumed.
# ---------------------------------------------------------------------------
XKB_CALLS = []


def _record_run(argv, *a, **kw):
    XKB_CALLS.append(list(argv))
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _record_run_cmd(argv, timeout=8):
    XKB_CALLS.append(list(argv))
    return 0, ""


firstrun.subprocess = types.SimpleNamespace(run=_record_run)
installer.run_cmd = _record_run_cmd
installer.shutil = types.SimpleNamespace(
    which=lambda n: "/usr/bin/" + n,
    copy2=shutil.copy2, Error=shutil.Error)


# ===========================================================================
head("1. the installer: the password is typed on the layout that will check it")
# ===========================================================================
inst = installer.Installer()
pump()

kbd_codes = [code for _lbl, code in installer.KBD_LAYOUTS]
fr_i = kbd_codes.index("fr")
ru_i = kbd_codes.index("ru,us")

inst._e_pw.set_text("azerty")
inst._e_pw2.set_text("azerty")
XKB_CALLS[:] = []
inst._c_kbd.set_active(fr_i)
pump()

chk("choosing French applies it to the running keyboard, before the password",
    any("setxkbmap" in c[0] and "fr" in c for c in XKB_CALLS), XKB_CALLS)
chk("what was typed on the OLD layout is cleared, not silently stored",
    inst._e_pw.get_text() == "" and inst._e_pw2.get_text() == "",
    (inst._e_pw.get_text(), inst._e_pw2.get_text()))

XKB_CALLS[:] = []
inst._c_kbd.set_active(ru_i)
pump()
ru_call = next((c for c in XKB_CALLS if "setxkbmap" in c[0]), [])
chk("a dual layout carries its switch key (or the Latin half is unreachable)",
    "grp:alt_shift_toggle" in ru_call, ru_call)

# The code the drop-down applies must be the code the target gets configured
# with, or the live keyboard and the installed one disagree again.
XKB_CALLS[:] = []
inst._c_kbd.set_active(fr_i)
pump()
applied = next((c for c in XKB_CALLS if "setxkbmap" in c[0]), [])
inst.cfg["kbd"] = inst._c_kbd.get_active()
inst._post_log = lambda *a, **k: None
shutil.rmtree(TREE, ignore_errors=True)
os.makedirs(os.path.join(TREE, "etc"), exist_ok=True)
inst._configure_keyboard(TREE)
xorg = open(os.path.join(TREE, "etc/X11/xorg.conf.d/00-keyboard.conf")).read()
chk("the layout applied here is the layout written to the target",
    'XkbLayout" "fr"' in xorg and "fr" in applied, (applied, xorg))

# Translated combos are the classic trap: get_active_text() returns the
# TRANSLATION, so a non-English install would apply a layout named "Francais".
chk("the layout is read by INDEX, never combo.get_active_text()",
    not calls_get_active_text(os.path.join(DE, "installer.py")),
    "installer.py calls get_active_text()")

try:
    inst.destroy()
except Exception:
    pass


# ===========================================================================
head("2. first-run setup: the same invariant, on the machine it is handed to")
# ===========================================================================
_real_pending = firstrun.pending
firstrun.pending = lambda: True          # restored before section 5
fr_win = build_firstrun()
chk("the setup screen constructs", fr_win is not None)
pump()

codes = [c for c, _n in fr_win._kbds]
fr_win.e_pw.set_text("azerty")
fr_win.e_pw2.set_text("azerty")
XKB_CALLS[:] = []
fr_win.c_kbd.set_active(codes.index("fr"))
pump()
chk("choosing French applies it to the running keyboard, before the password",
    any("setxkbmap" in c[0] and "fr" in c for c in XKB_CALLS), XKB_CALLS)
chk("what was typed on the OLD layout is cleared, not silently stored",
    fr_win.e_pw.get_text() == "" and fr_win.e_pw2.get_text() == "",
    (fr_win.e_pw.get_text(), fr_win.e_pw2.get_text()))

XKB_CALLS[:] = []
fr_win.c_kbd.set_active(codes.index("ru,us"))
pump()
ru_call = next((c for c in XKB_CALLS if "setxkbmap" in c[0]), [])
chk("a dual layout carries its switch key",
    "grp:alt_shift_toggle" in ru_call, ru_call)

chk("what is being typed can be read back (the layout just changed under it)",
    hasattr(fr_win, "cb_show"))
fr_win.cb_show.set_active(True)
pump()
chk("...and the tick actually reveals both fields",
    fr_win.e_pw.get_visibility() and fr_win.e_pw2.get_visibility())
fr_win.cb_show.set_active(False)
fr_win.cb_none.set_active(True)
pump()
chk("starting without a password takes the password controls with it",
    not fr_win.e_pw.get_sensitive() and not fr_win.cb_show.get_sensitive())
fr_win.cb_none.set_active(False)
pump()

chk("the layout is read by INDEX, never combo.get_active_text()",
    not calls_get_active_text(os.path.join(DE, "firstrun.py")),
    "firstrun.py calls get_active_text()")


# ===========================================================================
head("3. Finish is reachable, on the smallest panel, in every language")
# ===========================================================================
# This is the one screen that HAS to be completed before the machine can be
# used at all, and a Finish button past the bottom of the glass is a machine
# nobody can set up. Two things are asked: the form fits the smallest supported
# panel outright, AND there is a scroller under it so a shorter one still works.
BUDGET_W, BUDGET_H = 1024, 740


def form_of(win):
    """The centred form inside the scroller: scrolledwindow > viewport > page."""
    node = win.get_child()
    chain = [node]
    while hasattr(node, "get_child") and node.get_child() is not None:
        node = node.get_child()
        chain.append(node)
    return chain


chain = form_of(fr_win)
chk("the form sits in a scroller, so a shorter panel can still reach Finish",
    any(isinstance(n, Gtk.ScrolledWindow) for n in chain),
    [type(n).__name__ for n in chain])
sw = next(n for n in chain if isinstance(n, Gtk.ScrolledWindow))
chk("...which never scrolls sideways (a fixed-measure form has nowhere to go)",
    sw.get_policy()[0] == Gtk.PolicyType.NEVER, sw.get_policy())
# The viewport's own bin-window cannot be reached by CSS, so it must be filled
# by a child that expands -- otherwise the software render stack paints it
# BLACK around the form, which is exactly what a first shot of this came back
# with.
page = sw.get_child().get_child()
chk("...over a full-bleed painted box, so no unpainted (black) viewport shows",
    page.get_hexpand() and page.get_vexpand()
    and "fr-card" in page.get_style_context().list_classes(),
    (page.get_hexpand(), page.get_vexpand(),
     page.get_style_context().list_classes()))

# ONE SUBPROCESS PER LANGUAGE, and that is not laziness. nbi18n binds the
# active language when it is IMPORTED, so setting $NB_LANG in this process and
# building the window again measures English eighteen times over and reports it
# as full coverage -- which is exactly what a first version of this check did.
MEASURE = r'''
import os, sys
sys.path.insert(0, %r)
sys.path.insert(0, %r)
os.environ["NB_HOME"] = %r
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
# UNDER PAPERTONE, or the number is a lie. The host's own GTK theme pads a
# widget differently: measured here it made this form 87px taller than it is on
# the guest, which is the difference between "fits with room" and "does not fit
# at all" -- in the wrong direction, on the screen that must be completable.
import uishot; uishot.load_theme()
import nbapp; nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps")
os.makedirs(nbapp._APP_DIR, exist_ok=True)
import firstrun
firstrun.pending = lambda: True
found = []
Gtk.main = lambda: found.extend(w for w in Gtk.Window.list_toplevels()
                                if type(w).__name__ == "FirstRun")
firstrun.main()
w = found[0]
n = 0
while Gtk.events_pending() and n < 500:
    Gtk.main_iteration_do(False); n += 1
page = w.get_child().get_child()          # scrolledwindow > viewport > page
_mn, nat = page.get_preferred_size()
print("%%d %%d" %% (nat.width, nat.height))
''' % (DE, os.path.join(REPO, "tools"), HOME)

sizes = []
for code in sorted(nbi18n.LANG_NAMES):
    env = dict(os.environ, NB_LANG=code)
    p = subprocess.run([sys.executable, "-c", MEASURE], env=env,
                       capture_output=True, text=True, timeout=180)
    nums = (p.stdout or "").strip().split()
    sizes.append((code, int(nums[0]), int(nums[1]))
                 if len(nums) == 2 else (code, -1, -1))
over = [t for t in sizes
        if not (0 < t[1] <= BUDGET_W and 0 < t[2] <= BUDGET_H)]
big = max(sizes, key=lambda t: t[2]) if sizes else ("?", 0, 0)
chk("the form fits %dx%d in all %d languages without scrolling "
    "(tallest: %s at %dx%d)"
    % (BUDGET_W, BUDGET_H, len(sizes), big[0], big[1], big[2]),
    sizes and not over, over)


# ===========================================================================
head("4. /etc/shadow: what the machine ends up with is what was chosen")
# ===========================================================================
SH = os.path.join(TREE, "etc", "shadow")
firstrun.SHADOW = SH

# A crypt this host does not have. The guest's is glibc SHA-512; what matters
# here is only that a hash goes in and comes back out of the same field.
HASH = "$6$saltsalt$" + "z" * 86

SHAPES = [
    ("the shipped shadow", "root:*:::::::\ndaemon:*:::::::\n"),
    ("no root line at all", "daemon:*:::::::\nbin:*:::::::\n"),
    ("a root line with nothing after it", "root\ndaemon:*:::::::\n"),
    ("a root line truncated to one colon", "root:\ndaemon:*:::::::\n"),
    ("two root lines (a half-finished earlier run)",
     "root:!:::::::\nroot:*:::::::\ndaemon:*:::::::\n"),
    ("an empty file", ""),
]
import login                                                     # noqa: E402
login.SHADOW = SH
for label, body in SHAPES:
    open(SH, "w").write(body)
    ok = firstrun.set_root_password(HASH)
    text = open(SH).read()
    roots = [ln for ln in text.splitlines() if ln.split(":")[0] == "root"]
    chk("%s -> exactly one root line carrying the chosen hash" % label,
        ok and len(roots) == 1 and roots[0].split(":")[1] == HASH,
        (ok, roots))
    chk("%s -> the hash is the one de/login.py will read" % label,
        login._shadow_hash("root") == HASH, login._shadow_hash("root"))

# ...and locking is the same story in reverse: it must actually LOCK.
open(SH, "w").write("daemon:*:::::::\n")
firstrun.set_root_password(None)
chk("no password chosen -> root is locked, so no screen ever asks",
    login._shadow_hash("root") == "*" and not login.has_password("root"),
    login._shadow_hash("root"))

# The whole reason has_password() exists: a stored string this machine's crypt
# cannot match is not a password, it is a screen nobody can get past.
open(SH, "w").write("root:$y$j9T$saltsalt$hashhash:::::::\n")   # yescrypt
chk("a hash this machine cannot verify never raises a sign-in screen",
    not login.has_password("root"), login._shadow_hash("root"))


# ===========================================================================
head("5. the marker is cleared LAST, and only on a clean run")
# ===========================================================================
firstrun.pending = _real_pending          # the real one, from here on
MARK = os.path.join(TREE, "var", "lib", "notebookos", "first-run")
firstrun.OEM_MARKER = MARK
firstrun.HOSTNAME_FILE = os.path.join(TREE, "etc", "hostname")
firstrun.USER_NAME_FILE = os.path.join(TREE, "etc", "notebookos-user")
firstrun.XKB_CONF = os.path.join(TREE, "etc/X11/xorg.conf.d/00-keyboard.conf")
firstrun.hash_password = lambda pw: HASH


def fresh_marker():
    os.makedirs(os.path.dirname(MARK), exist_ok=True)
    open(MARK, "w").write("owed\n")
    open(SH, "w").write("root:*:::::::\n")


ANSWERS = {"hostname": "benbook", "username": "Ben", "lang": "fr",
           "kbd": "fr", "password": "nb1234"}

fresh_marker()
failed = firstrun.apply(dict(ANSWERS))
chk("a clean run leaves nothing owed", not failed, failed)
chk("...and the marker is gone, so it never asks again",
    not firstrun.pending())
chk("...and the password chosen is the one on the machine",
    login._shadow_hash("root") == HASH, login._shadow_hash("root"))

# The language is the measured regression: this wrote a key nbi18n does not
# read, so every machine set up for somebody else came up in English.
chk("the language chosen is the language the desktop will start in",
    nbi18n.current_lang() == "fr", nbi18n.current_lang())
chk("...and the keyboard with it",
    nbi18n.keyboard() == "fr", nbi18n.keyboard())
loc = json.load(open(os.path.join(HOME, ".config/notebook/locale.json")))
chk("...written under the key nbi18n reads, not one of this file's own",
    loc.get("lang") == "fr", loc)

# A part that did not stick keeps the screen owed.
fresh_marker()
_real_hostname = firstrun.write_hostname
firstrun.write_hostname = lambda name: False
failed = firstrun.apply(dict(ANSWERS))
firstrun.write_hostname = _real_hostname
chk("a step that failed is reported", failed == ["hostname"], failed)
chk("...and setup stays owed rather than half-applied and forgotten",
    firstrun.pending())
chk("...named with a word the catalogs carry, not a raw key",
    all(p in firstrun.PART_NAMES for p in failed), failed)

CAT = json.load(open(os.path.join(DE, "lang_fr.json")))
chk("every one of those names is already translated in all 17 languages",
    all(v in CAT for v in firstrun.PART_NAMES.values()),
    [v for v in firstrun.PART_NAMES.values() if v not in CAT])

fresh_marker()
firstrun.hash_password = lambda pw: None      # a build with no crypt/openssl
failed = firstrun.apply(dict(ANSWERS))
chk("a password that cannot be hashed is reported, not silently dropped",
    failed == ["password"], failed)
chk("...and setup stays owed, so nobody is handed a machine with no password",
    firstrun.pending())
chk("...and root is left locked rather than half-written",
    not login.has_password("root"), login._shadow_hash("root"))
firstrun.hash_password = lambda pw: HASH

os.unlink(MARK)
chk("clear_marker answers 'the marker is gone', not 'unlink worked'",
    firstrun.clear_marker())


# ===========================================================================
head("6. the installer never writes a hostname the machine cannot carry")
# ===========================================================================
inst2 = installer.Installer()
inst2._post_log = lambda *a, **k: None
pump()
for label, given in (("blank", ""), ("spaces", "   "),
                     ("illegal characters", "my box!"),
                     ("a leading dash", "-box")):
    shutil.rmtree(TREE, ignore_errors=True)
    os.makedirs(os.path.join(TREE, "etc"), exist_ok=True)
    open(os.path.join(TREE, "etc", "shadow"), "w").write("root:*:::::::\n")
    open(os.path.join(TREE, "etc", "inittab"), "w").write("::sysinit:/bin/true\n")
    inst2.cfg["oem"] = True          # OEM skips the Options step's validation
    inst2.cfg["hostname"] = given
    inst2._configure_target(TREE)
    got = open(os.path.join(TREE, "etc", "hostname")).read().strip()
    chk("a %s hostname becomes a usable one" % label,
        got == installer.Installer.DEFAULT_HOSTNAME, repr(got))
inst2.cfg["hostname"] = "benbook"
inst2._configure_target(TREE)
chk("a valid hostname is written unchanged",
    open(os.path.join(TREE, "etc", "hostname")).read().strip() == "benbook")

print("\nPASSWORD LOCKOUT SELFTEST: %d checks, %s"
      % (len(RESULTS),
         "all pass" if all(RESULTS)
         else "%d FAILED" % (len(RESULTS) - sum(RESULTS))))
sys.exit(0 if all(RESULTS) else 1)
