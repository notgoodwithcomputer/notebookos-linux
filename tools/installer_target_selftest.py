#!/usr/bin/env python3
"""installer_target_selftest — what de/installer.py actually WRITES.

    DISPLAY=:0 python3 tools/installer_target_selftest.py

The installer is the one app whose output is a machine somebody then lives on,
and every one of its mistakes is discovered after the disk it was told to erase
has already been erased. The destructive half (sgdisk/mkfs/tar) cannot be run
here, but everything it writes into the extracted tree CAN: this drives the real
_configure_target against a real copy of the shipped /etc, then hands the result
to de/login.py — the code that will read it on the installed machine's first
boot — and checks the machine that comes out is one a person can get into.

The regression that started this file: _configure_login appended
`tty1::respawn:/sbin/getty 38400 tty1` to the installed inittab, while
S99notebookos starts the desktop with `xinit ... -- :0 vt1`. Every installed
machine therefore had busybox init respawning a getty on the same virtual
terminal the X server owns. The shipped inittab says "NO GETTY ON tty1. X owns
tty1" in as many words; the installer wrote one anyway, because the file has no
`tty1::` line to replace and the replace loop fell through to an append.
"""
import ctypes
import os
import shutil
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay")
DE = os.path.join(OVERLAY, "opt/notebook/de")
TARGET = os.path.join(REPO, "buildroot/output/target")
sys.path.insert(0, DE)

FAILURES = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(what)
        print("  FAIL  %s" % what)
    return bool(cond)


# The guest's Python 3.11 has `crypt`; this host's 3.13 does not. Install the
# guest's (glibc crypt(3) through ctypes) so the password path under test is
# the one that ships, not a skipped branch.
def _install_guest_crypt():
    lib = ctypes.CDLL("libcrypt.so.1")
    lib.crypt.restype = ctypes.c_char_p
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]

    def c(word, salt):
        r = lib.crypt(word.encode(), salt.encode())
        return r.decode() if r else None
    m = types.ModuleType("crypt")
    m.crypt = c
    m.METHOD_SHA512 = "6"
    m.mksalt = lambda _m=None: "$6$nbselftest"
    sys.modules["crypt"] = m
    return m


PASSWORD = "a real password"


def make_tree():
    """A target tree shaped like the one `tar xpf rootfs.tar` produces, built
    from the files that are actually shipped — not a hand-written stand-in, so
    a change to the shipped inittab or shadow shows up here."""
    root = tempfile.mkdtemp(prefix="nbtarget-")
    os.makedirs(os.path.join(root, "etc"))
    shutil.copy(os.path.join(OVERLAY, "etc/inittab"),
                os.path.join(root, "etc/inittab"))
    for name, fallback in (("shadow", "root:*:::::::\n"),
                           ("passwd", "root:x:0:0:root:/root:/bin/sh\n")):
        src = os.path.join(TARGET, "etc", name)
        dst = os.path.join(root, "etc", name)
        if os.path.exists(src):
            shutil.copy(src, dst)
        else:
            open(dst, "w").write(fallback)
    open(os.path.join(root, "etc/fstab"), "w").write(
        "# /etc/fstab\ntmpfs /run tmpfs mode=0755,nosuid,nodev 0 0\n")
    open(os.path.join(root, "etc/profile"), "w").write("# /etc/profile\n")
    return root


def blank_installer(installer, **cfg):
    """The real Installer with no GTK construction: _configure_target and its
    helpers are plain file writes and must be exercised as themselves."""
    inst = installer.Installer.__new__(installer.Installer)
    inst._post_log = lambda _m: None
    inst.cfg = {"hostname": "notebook", "kbd": 0, "locale": 0,
                "password": PASSWORD, "root_passwordless": False,
                "swap": False, "swap_mib": 2048, "disk": "/dev/sda",
                "disk_size": 0, "disk_model": "", "disk_contents": ""}
    inst.cfg.update(cfg)
    return inst


def getty_lines(root):
    return [ln.rstrip("\n")
            for ln in open(os.path.join(root, "etc/inittab"))
            if "getty" in ln and not ln.lstrip().startswith("#")]


def xinit_vt():
    """Which virtual terminal the desktop's X server takes. Read from the real
    init script so this test cannot drift away from it."""
    p = os.path.join(OVERLAY, "etc/init.d/S99notebookos")
    for ln in open(p):
        # The init script may invoke a configurable command ("$XINIT") so its
        # lifecycle selftest can substitute a harmless fake.  Match either the
        # literal program or that variable; matching only lowercase `xinit`
        # never inspected the uppercase command line carrying `vt1`.
        if "xinit" in ln or "$XINIT" in ln:
            for tok in ln.split():
                if tok.startswith("vt") and tok[2:].isdigit():
                    return tok
    return ""


# ------------------------------------------------------------------ the tests
def test_password_install(installer, login):
    print("-- a normal install, with a password")
    root = make_tree()
    inst = blank_installer(installer)
    inst._configure_target(root)

    vt = xinit_vt()
    check(vt == "vt1", "the desktop's X server is on %r" % vt)
    lines = getty_lines(root)
    check(len(lines) == 1, "exactly one console getty is written: %r" % lines)
    for ln in lines:
        tty = ln.split("::", 1)[0]
        check(tty != "tty1",
              "the console getty must NOT be on tty1 — X owns it (%r)" % ln)
        check(tty != "tty" + vt[2:],
              "the console getty must not share the X server's terminal "
              "(X on %s, getty on %s)" % (vt, tty))
        check(tty.startswith("tty") and tty[3:].isdigit() and tty != "tty1",
              "the console getty is on a free virtual terminal: %r" % ln)
        check("/sbin/getty" in ln and " -l " not in ln and " -n " not in ln,
              "the console getty asks who you are (no -n/-l shell): %r" % ln)

    # ...and the machine that results is one login.py lets its owner into.
    login.SHADOW = os.path.join(root, "etc/shadow")
    login.PASSWD = os.path.join(root, "etc/passwd")
    os.environ["NB_HOME"] = "/root"
    who = login.desktop_user()
    check(who == "root", "the sign-in screen asks for %r" % who)
    check(login.has_password(who) is True,
          "a sign-in screen appears on an installed machine")
    check(login.verify(who, PASSWORD) is True,
          "THE password the user typed into the installer opens the machine")
    check(login.verify(who, PASSWORD + " ") is False,
          "a near-miss password does not open the machine")
    check(login.verify(who, "") is False,
          "an empty password does not open the machine")
    stored = login._shadow_hash(who)
    check(stored.startswith("$6$"),
          "the password is stored as SHA-512: %r" % (stored or "")[:8])
    check(stored == stored.strip(),
          "the stored hash carries no stray whitespace")

    # the rest of the tree
    host = open(os.path.join(root, "etc/hostname")).read().strip()
    check(host == "notebook", "hostname written: %r" % host)
    rel = open(os.path.join(root, "etc/os-release")).read()
    check("Notebook OS" in rel, "os-release written")
    prof = open(os.path.join(root, "etc/profile")).read()
    check("export LANG=" in prof, "the locale is exported from /etc/profile")
    kb = os.path.join(root, "etc/X11/xorg.conf.d/00-keyboard.conf")
    check(os.path.exists(kb), "the X keyboard layout is written")
    loc = os.path.join(root, "root/.config/notebook/locale.json")
    check(os.path.exists(loc), "the desktop's own locale.json is written")
    import json
    data = json.load(open(loc))
    check("keyboard" in data and "lang" in data,
          "locale.json names a keyboard and a language: %r" % data)
    shutil.rmtree(root, ignore_errors=True)


def test_passwordless_install(installer, login):
    print("-- an install with 'start straight into the desktop'")
    root = make_tree()
    inst = blank_installer(installer, root_passwordless=True)
    inst._configure_target(root)
    check(getty_lines(root) == [],
          "no console getty is added: %r" % getty_lines(root))
    login.SHADOW = os.path.join(root, "etc/shadow")
    login.PASSWD = os.path.join(root, "etc/passwd")
    check(login.has_password("root") is False,
          "no sign-in screen is raised on a machine with no password")
    check(login.verify("root", "") is False,
          "and the locked account still accepts nothing")
    shutil.rmtree(root, ignore_errors=True)


def test_second_install(installer, login):
    print("-- installing a second time over the first")
    root = make_tree()
    blank_installer(installer)._configure_target(root)
    first = open(os.path.join(root, "etc/inittab")).read()
    blank_installer(installer, hostname="again",
                    password="second one")._configure_target(root)
    lines = getty_lines(root)
    check(len(lines) == 1,
          "a second install leaves ONE console getty, not two: %r" % lines)
    second = open(os.path.join(root, "etc/inittab")).read()
    check(second.count("getty 38400") == 1,
          "the inittab does not accumulate getty lines")
    check(len(second) - len(first) < 200,
          "the inittab does not grow on every install (%d -> %d bytes)"
          % (len(first), len(second)))

    login.SHADOW = os.path.join(root, "etc/shadow")
    login.PASSWD = os.path.join(root, "etc/passwd")
    check(login.verify("root", "second one") is True,
          "the SECOND install's password is the one that works")
    check(login.verify("root", PASSWORD) is False,
          "the first install's password no longer works")
    shadow = open(os.path.join(root, "etc/shadow")).read()
    check(len([ln for ln in shadow.splitlines() if ln.startswith("root:")]) == 1,
          "there is exactly one root line in /etc/shadow")
    check(len(shadow.splitlines()[0].split(":")) == 9,
          "the rewritten shadow line still has nine fields")

    # A tree whose shadow has no root line at all must gain one, not silently
    # produce a machine with no way in.
    open(os.path.join(root, "etc/shadow"), "w").write("nobody:*:::::::\n")
    blank_installer(installer, password="third")._configure_target(root)
    check(login.verify("root", "third") is True,
          "a shadow with no root line gets one written")
    shutil.rmtree(root, ignore_errors=True)


def test_swap_fstab(installer):
    print("-- the swap partition is actually switched on")
    root = make_tree()
    blank_installer(installer, swap=False)._configure_target(root)
    fstab = open(os.path.join(root, "etc/fstab")).read()
    check("swap" not in fstab, "no swap line when swap was not asked for")
    root2 = make_tree()
    blank_installer(installer, swap=True, swap_mib=512)._configure_target(root2)
    fstab = open(os.path.join(root2, "etc/fstab")).read()
    check("LABEL=%s" % installer.SWAP_LABEL in fstab and "\tswap\t" in fstab,
          "the swap partition is named in fstab by LABEL: %r"
          % fstab.splitlines()[-1:])
    for r in (root, root2):
        shutil.rmtree(r, ignore_errors=True)


def test_keyboard_variants(installer):
    print("-- keyboard layouts that need a variant or a switch key")
    xp = installer.Installer._xkb_parts
    for code, want in (("us", ("us", "", "")),
                       ("fr", ("fr", "", "")),
                       ("jp(kana)", ("jp", "kana", "")),
                       ("ru,us", ("ru,us", "", "grp:alt_shift_toggle")),
                       ("", ("us", "", "")),
                       (None, ("us", "", ""))):
        got = xp(code)
        check(got == want, "_xkb_parts(%r) = %r, want %r" % (code, got, want))
    # Every layout the Options step can offer must survive it, or an install in
    # that language comes up on a keyboard nobody can type a password on.
    for label, code in installer.KBD_LAYOUTS:
        layout, _v, _o = xp(code)
        check(bool(layout) and "(" not in layout,
              "layout %r (%s) parses to something xorg.conf can use: %r"
              % (code, label, layout))


def test_refuses_before_erasing(installer):
    print("-- refusals happen BEFORE the disk is touched")
    inst = installer.Installer.__new__(installer.Installer)
    inst.cfg = {"disk": "/dev/sda", "disk_size": 500 * 10 ** 9,
                "root_passwordless": False, "swap": False, "swap_mib": 0}
    inst.tools = {}
    inst.payload_bytes = 900 * 1024 * 1024
    inst.can_hash = True
    inst._partuuid_other = ""

    inst.medium_ok = False
    inst.missing_tools = []
    ok, why = inst._install_ready()
    check(ok is False and "medium" in why.lower(),
          "no live medium: refused, and says so (%r)" % why[:60])

    inst.medium_ok = True
    inst.missing_tools = ["sgdisk"]
    ok, why = inst._install_ready()
    check(ok is False and why, "missing tools: refused, and says so")

    inst.missing_tools = []
    inst.can_hash = False
    ok, why = inst._install_ready()
    check(ok is False and why,
          "no password hashing: refused BEFORE the erase, not at 90%%")
    check("Options" in why or "password" in why.lower(),
          "...and the message names what to do about it: %r" % why[:80])
    inst.cfg["root_passwordless"] = True
    ok, _why = inst._install_ready()
    check(ok is True,
          "...and it is not refused when no password was asked for")
    inst.cfg["root_passwordless"] = False
    inst.can_hash = True

    inst.cfg["disk"] = None
    ok, why = inst._install_ready()
    check(ok is False and why, "no disk chosen: refused, and says so")

    inst.cfg["disk"] = "/dev/sda"
    inst.cfg["disk_size"] = 100 * 1024 * 1024        # smaller than the payload
    ok, why = inst._install_ready()
    check(ok is False and "too small" in why.lower(),
          "a disk too small is refused on the screen, not after the wipe")

    inst.cfg["disk_size"] = 500 * 10 ** 9
    ok, why = inst._install_ready()
    check(ok is True and why == "", "a good plan is accepted (%r)" % why)

    # Turning swap on AFTER choosing the disk can make it stop fitting.
    inst.cfg["disk_size"] = 2 * 1024 ** 3
    inst.cfg["swap"] = False
    check(inst._disk_too_small(inst.cfg["disk_size"]) is False,
          "2 GB fits a 900 MB payload with no swap")
    check(inst._disk_too_small(inst.cfg["disk_size"], 8192) is True,
          "...and stops fitting once 8 GB of swap is asked for")

    # No payload at all: gate off, so the disk list is not greyed out wholesale
    # for the wrong reason (_install_ready already refuses on medium_ok).
    inst.payload_bytes = 0
    check(inst._min_disk_bytes() == 0 and
          inst._disk_too_small(1) is False,
          "with no payload measured, no disk is called too small")


def test_partuuid_clash(installer):
    print("-- a second disk already carrying the fixed root PARTUUID")
    inst = installer.Installer.__new__(installer.Installer)
    inst.tools = {"lsblk": "/bin/lsblk"}
    inst.cfg = {"disk": "/dev/sdb"}
    U = installer.ROOT_PARTUUID
    orig = installer.run_cmd
    try:
        installer.run_cmd = lambda a, timeout=8: (0, (
            'NAME="sda" PARTUUID="" TYPE="disk" PKNAME=""\n'
            'NAME="sda2" PARTUUID="%s" TYPE="part" PKNAME="sda"\n'
            'NAME="sdb" PARTUUID="" TYPE="disk" PKNAME=""\n' % U))
        inst._partuuid_other = inst._partuuid_clash()
        check(inst._partuuid_other == "/dev/sda",
              "the other install is found: %r" % inst._partuuid_other)
        check("/dev/sda" in inst._clash_line() and inst._clash_line(),
              "and it is said in words: %r" % inst._clash_line()[:70])

        # The disk being installed ONTO does not count as a clash.
        inst.cfg["disk"] = "/dev/sda"
        check(inst._partuuid_clash() == "",
              "re-installing over the same disk is not reported as a clash")

        # Nothing found, and a broken probe, both say nothing at all.
        installer.run_cmd = lambda a, timeout=8: (0, 'NAME="sda" TYPE="disk"\n')
        check(inst._partuuid_clash() == "", "no clash -> nothing said")
        installer.run_cmd = lambda a, timeout=8: (1, "")
        check(inst._partuuid_clash() == "", "a failed probe -> nothing said")
        inst.tools = {}
        check(inst._partuuid_clash() == "", "no lsblk -> nothing said")
    finally:
        installer.run_cmd = orig


def main():
    _install_guest_crypt()
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbhome-"))
    import installer
    import login
    test_password_install(installer, login)
    test_passwordless_install(installer, login)
    test_second_install(installer, login)
    test_swap_fstab(installer)
    test_keyboard_variants(installer)
    test_refuses_before_erasing(installer)
    test_partuuid_clash(installer)
    print()
    if FAILURES:
        print("INSTALLER TARGET SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        return 1
    print("INSTALLER TARGET SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
