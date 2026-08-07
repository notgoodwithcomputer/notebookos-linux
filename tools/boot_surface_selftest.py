#!/usr/bin/env python3
"""boot_surface_selftest — the three surfaces a new machine shows first.

    DISPLAY=:0 python3 tools/boot_surface_selftest.py

The loading screen, the desktop backdrop and the volume that appears when a USB
stick is plugged in. None of them is an app, so none of them was covered by any
existing selftest, and all three are reached in the first hour.

The defects that made this file:

  * THE BACKDROP DID NOT FOLLOW THE SETTING. Settings > Backdrop changed the
    colour with `xsetroot -solid`, which paints the X ROOT — and desktopbg.py
    exists precisely because the root is not what is on screen once a full
    screen desktop-type window is covering it. Picking a colour therefore did
    nothing at all until the machine was restarted. RESOLVED BY REMOVAL: the
    alternate desktop colours are gone (they mis-rendered behind the widget
    board), session.sh passes the one papertone field on the command line, and
    settings.py:_apply_saved_prefs deliberately ignores a "background" left
    behind by an older build. What test_backdrop still guards is the part that
    ships — that whatever desktopbg is handed, the desktop comes up PAINTED,
    because an unpainted backdrop is an alpha hole the compositor fills with
    black, which reads as a dead machine rather than a wrong colour.
  * A USB STICK'S OWN LABEL COULD ESCAPE /media. automount.sh names the mount
    point after the volume's label and replaced only "/" and "\\" in it, so
    "." and ".." went through untouched. Measured: a stick labelled "." was
    mounted ON /media itself (that is the shipped state — /media exists and
    starts empty), hiding every other volume; and "/media/.." is "/", which the
    de-duplication loop only deflects for as long as /media exists at all. A
    label is whatever the last person to format the stick typed.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTEBOOK = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/"
                              "opt/notebook")
DE = os.path.join(NOTEBOOK, "de")
sys.path.insert(0, DE)

FAILURES = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(what)
        print("  FAIL  %s" % what)
    return bool(cond)


def pump(n=600):
    from gi.repository import Gtk
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


# ------------------------------------------------------------------ backdrop
def test_backdrop():
    print("-- the desktop backdrop paints the one papertone field")
    # There is NO backdrop setting. The alternate desktop colours were removed
    # (they mis-rendered behind the widget board), session.sh passes the colour
    # on the command line once, and settings.py:_apply_saved_prefs deliberately
    # IGNORES a "background" left in settings.json by an older build. This test
    # used to drive a settings-following backdrop through Backdrop.watch_settings(),
    # which never existed in any build -- so it raised AttributeError on the
    # first call and took test_splash() and test_automount_labels() down with
    # it, unrun, for as long as it sat in the baseline. What is left is the part
    # that ships: whatever it is handed, the desktop comes up PAINTED.
    import importlib
    import desktopbg
    importlib.reload(desktopbg)

    def rgb(win):
        c = win._rgba
        return (round(c.red, 3), round(c.green, 3), round(c.blue, 3))

    paper = (round(0xDE / 255.0, 3), round(0xD4 / 255.0, 3),
             round(0xC2 / 255.0, 3))
    check(desktopbg.DEFAULT_COLOR == "#DED4C2",
          "the default is the papertone field: %r" % desktopbg.DEFAULT_COLOR)

    win = desktopbg.Backdrop("#123456")
    check(rgb(win) == (round(0x12 / 255.0, 3), round(0x34 / 255.0, 3),
                       round(0x56 / 255.0, 3)),
          "a colour on the command line is the colour painted: %r" % (rgb(win),))

    # A malformed value must never leave the desktop unpainted -- an unpainted
    # backdrop is an alpha hole the compositor fills with BLACK, which reads as
    # a dead machine rather than a wrong colour.
    for junk in ("not a colour", "", "#", "#12345", "#GGGGGG", None, 5, [], {}):
        w = desktopbg.Backdrop(junk)
        got = rgb(w)
        check(got == paper,
              "a backdrop of %r falls back to paper, never black: %r"
              % (junk, got))
        try:
            w.destroy()
        except Exception:                                      # noqa: BLE001
            pass

    # set_color() is the live repaint path. It is not wired to a screen today,
    # but it is public and must hold the same guarantee.
    win.set_color("#DED4C2")
    check(rgb(win) == paper, "set_color repaints: %r" % (rgb(win),))
    win.set_color("garbage")
    check(rgb(win) == paper, "and set_color on junk keeps it painted")

    # The decision itself, guarded: if a "background" key ever starts being
    # honoured again, it must come with a screen that can change it back.
    src = open(os.path.join(DE, "settings.py"), encoding="utf-8").read()
    check("_saved_color" not in open(
              os.path.join(DE, "desktopbg.py"), encoding="utf-8").read(),
          "desktopbg has no settings reader to go stale")
    check("backdrop colour is applied" in src or "background" not in src,
          "settings.py records why a stored background is ignored")
    try:
        win.destroy()
    except Exception:                                          # noqa: BLE001
        pass

# -------------------------------------------------------------------- splash
def test_splash():
    print("-- the loading screen")
    import importlib
    import splash
    importlib.reload(splash)
    ready = tempfile.mkdtemp(prefix="nbready-")
    splash.READY_FLAG = os.path.join(ready, "nb-ready")

    win = splash.Splash()
    # Asked BEFORE the window is realised, on purpose. The bar's own 70ms tick
    # timer is armed in __init__, and mapping a full-screen window on a real X
    # server takes long enough that pumping the queue afterwards can dispatch
    # that first tick -- which made this check fail intermittently on a bar that
    # was behaving exactly as intended. What is under test is the STARTING
    # state, so read it at the start.
    check(win._fraction == 0.0, "the bar starts empty")
    check(win._done is False, "and not finished")
    win.show_all()
    pump()

    # It must creep on its own — a frozen bar reads as a hung machine.
    for _ in range(40):
        win._tick_bar()
    first = win._fraction
    check(0 < first <= 0.9, "the bar advances on its own: %.3f" % first)
    check(first <= 0.9, "but never reaches 100% by itself")
    # Once capped, the timer stops rather than waking forever for no repaint.
    for _ in range(200):
        win._tick_bar()
    check(win._fraction == 0.9, "it caps at 90%%: %.3f" % win._fraction)
    check(win._tick_bar() is False,
          "the tick timer retires at the cap instead of waking every 70ms")

    check(win._poll_ready() is True, "it keeps polling while the desktop starts")
    open(splash.READY_FLAG, "w").close()
    check(win._poll_ready() is False, "and stops the moment the desktop is up")
    check(win._done is True and win._fraction == 1.0,
          "the bar fills to 100% when the desktop is up")

    # The failsafe must be idempotent and must never leave it unfinished.
    win2 = splash.Splash()
    check(win2._failsafe() is False, "the failsafe runs once")
    check(win2._done is True, "and always finishes the splash")
    win2._finish()
    check(win2._done is True, "finishing twice is harmless")
    check(splash.MAX_MS >= 10000,
          "the failsafe is long enough not to fire mid-boot: %dms"
          % splash.MAX_MS)
    for w in (win, win2):
        try:
            w.destroy()
        except Exception:                                      # noqa: BLE001
            pass

    # A missing brand mark must not stop the machine having a boot screen.
    splash.LOGO = "/nonexistent/logo.png"
    try:
        win3 = splash.Splash()
        win3.show_all()
        pump()
        win3.destroy()
        ok = True
    except Exception as e:                                     # noqa: BLE001
        ok = "raised %r" % e
    check(ok is True, "the splash still comes up with no logo file: %s" % ok)


# ----------------------------------------------------------------- automount
# The REAL automount.sh is run, with the commands that would touch this machine
# replaced by loggers: blkid answers with the label under test, and mkdir /
# mount / rmdir only record what they were asked to do. Nothing is copied out
# of the script, so this cannot drift away from what ships.
_STUB = {
    "blkid": '#!/bin/sh\nprintf "%s\\n" "$NB_LABEL"\n',
    "mkdir": '#!/bin/sh\nprintf "mkdir %s\\n" "$*" >> "$NB_TRACE"\n',
    "rmdir": '#!/bin/sh\nprintf "rmdir %s\\n" "$*" >> "$NB_TRACE"\n',
    "umount": '#!/bin/sh\nprintf "umount %s\\n" "$*" >> "$NB_TRACE"\n',
    # Print the LAST argument on its own line: that is the mount point, and it
    # is the whole question this test is asking.
    "mount": ('#!/bin/sh\nfor a in "$@"; do last="$a"; done\n'
              'printf "mountpoint %s\\n" "$last" >> "$NB_TRACE"\nexit 0\n'),
}
_REAL = ("sed", "cut", "tr", "awk", "grep", "ls", "printf", "cat", "sh",
         "basename", "expr", "test")


def _automount_bin():
    d = tempfile.mkdtemp(prefix="nbautomount-")
    for name, body in _STUB.items():
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            fh.write(body)
        os.chmod(p, 0o755)
    for name in _REAL:
        for base in ("/bin", "/usr/bin"):
            src = os.path.join(base, name)
            if os.path.exists(src):
                p = os.path.join(d, name)
                with open(p, "w") as fh:
                    fh.write('#!/bin/sh\nexec %s "$@"\n' % src)
                os.chmod(p, 0o755)
                break
    return d


def test_automount_labels():
    print("-- the mount point a USB stick's own label produces "
          "(the real automount.sh)")
    script = os.path.join(NOTEBOOK, "automount.sh")
    d = _automount_bin()
    trace = os.path.join(d, "trace")

    def mnt(label):
        open(trace, "w").close()
        env = {"PATH": d, "NB_TRACE": trace, "NB_LABEL": label,
               "HOME": d, "SHELL": "/bin/sh"}
        r = subprocess.run(["/bin/sh", script, "add", "sdb1"], env=env,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return "rc=%d %s" % (r.returncode, r.stderr[-120:])
        for ln in open(trace).read().splitlines():
            if ln.startswith("mountpoint "):
                return ln.split(" ", 1)[1]
        return "(never mounted)"

    def resolves(p):
        return os.path.normpath(p)

    for label, why in ((".", "would mount the stick ON /media, hiding every "
                             "other volume"),
                       ("..", '"/media/.." is "/"'),
                       ("...", "a directory that is not a name"),
                       ("." * 60, "an all-dots label, truncated"),
                       ("", "no label at all")):
        got = mnt(label)
        check(got == "/media/sdb1",
              "label %r falls back to the device name (%s): got %r"
              % (label, why, got))
        check(resolves(got).startswith("/media/") and resolves(got) != "/media",
              "...and cannot escape /media: %r -> %r" % (got, resolves(got)))

    for label, want in (("PHOTOS", "/media/PHOTOS"),
                        ("My Backup", "/media/My Backup"),
                        ("..config", "/media/..config"),
                        ("a/b", "/media/a_b"),
                        ("x\\y", "/media/x_y"),
                        ("  spaced  ", "/media/spaced"),
                        ("ev\nil", "/media/evil")):
        got = mnt(label)
        check(got == want,
              "label %r still reads as itself: got %r want %r"
              % (label, got, want))
        r = resolves(got)
        check(r.startswith("/media/") and r != "/" and r != "/media",
              "...and stays inside /media: %r -> %r" % (got, r))


def main():
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbhome-"))
    import gi
    gi.require_version("Gtk", "3.0")
    test_backdrop()
    test_splash()
    test_automount_labels()
    print()
    if FAILURES:
        print("BOOT SURFACE SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        return 1
    print("BOOT SURFACE SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
