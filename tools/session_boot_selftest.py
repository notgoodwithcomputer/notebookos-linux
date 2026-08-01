#!/usr/bin/env python3
"""session_boot_selftest — the order the desktop session actually starts in.

    python3 tools/session_boot_selftest.py

opt/notebook/session.sh is the first hour of this machine: it paints the field,
puts the loading screen up, gates on the sign-in screen and then starts the
desktop. None of that can be checked by importing anything, and booting it needs
an X server, so this runs the REAL script under `sh` with a stub PATH — every
external command replaced by a small logger — and asserts the sequence.

What it is here to hold:

  * THE LOADING SCREEN AND THE SIGN-IN SCREEN ARE NEVER UP TOGETHER. Both are
    full-screen keep-above windows and matchbox stacks by focus, so having both
    on screen is a race whose losing side is a person staring at "STARTING UP"
    with the password prompt underneath it. The splash is retired (via its
    /tmp/nb-ready flag) before the sign-in screen is drawn, and a fresh one goes
    up afterwards — the splash's own 30-second failsafe expires while somebody
    is still typing, so without that the desktop finished starting behind a
    bare backdrop.
  * THE KEYBOARD LAYOUT IS APPLIED BEFORE A PASSWORD IS ASKED FOR. The layout
    job is backgrounded for boot speed, and the password is the first thing
    ever typed on the machine. On a French, Russian or Greek install, typing it
    on the US layout the session starts with produces a wrong password with
    nothing on screen to explain why.
  * NOTHING OF THE DESKTOP EXISTS BEHIND THE SIGN-IN SCREEN. The Finder, the
    widget column and the shell must all start after it returns.
  * A machine with NO password pays nothing: `login.py --needed` answers before
    Gtk is imported, and no second splash is started.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/"
                             "opt/notebook/session.sh")

FAILURES = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(what)
        print("  FAIL  %s" % what)
    return bool(cond)


# Every command session.sh can reach, replaced by one that appends its argv to
# $NB_TRACE. python3 additionally honours NB_NEEDED to answer --needed, and
# prints the keyboard command when asked for it.
STUB_PY = r"""#!/bin/sh
printf '%s\n' "python3 $*" >> "$NB_TRACE"
case "$*" in
  *login.py\ --needed*) exit "${NB_NEEDED:-1}" ;;
  # A machine set up for somebody else owes a first-run screen. Default 1 =
  # nothing owed, which is every machine installed the ordinary way.
  *firstrun.py\ --needed*) exit "${NB_FIRSTRUN:-1}" ;;
  *-c*nbi18n*)          echo "setxkbmap fr" ; exit 0 ;;
esac
exit 0
"""

STUB_GENERIC = r"""#!/bin/sh
printf '%s\n' "$(basename "$0") $*" >> "$NB_TRACE"
exit 0
"""

# `touch` and `rm` have to be real: the script's control flow depends on the
# flag file existing. `kill` and `sleep` likewise.
REAL = ("touch", "rm", "kill", "sleep", "sed", "head", "awk", "grep",
        "basename", "readlink", "printf", "cat", "test", "expr", "sh", "dirname")
STUBBED = ("python3", "xrandr", "xset", "xsetroot", "matchbox-window-manager",
           "dbus-launch", "amixer", "alsactl", "dmesg", "picom", "xcompmgr",
           "setxkbmap")


def make_bin():
    d = tempfile.mkdtemp(prefix="nbstub-")
    for name in STUBBED:
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            fh.write(STUB_PY if name == "python3" else STUB_GENERIC)
        os.chmod(p, 0o755)
    # Real coreutils, reached by absolute path from a shim so PATH stays ours.
    for name in REAL:
        src = None
        for base in ("/bin", "/usr/bin"):
            if os.path.exists(os.path.join(base, name)):
                src = os.path.join(base, name)
                break
        if src is None:
            continue
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            fh.write('#!/bin/sh\nexec %s "$@"\n' % src)
        os.chmod(p, 0o755)
    return d


def run_session(needed, firstrun=False):
    """Run the real session.sh to completion and return its trace lines."""
    d = make_bin()
    trace = os.path.join(d, "trace")
    open(trace, "w").close()
    env = {
        "PATH": d, "NB_TRACE": trace, "NB_NEEDED": "0" if needed else "1",
        "NB_FIRSTRUN": "0" if firstrun else "1",
        "HOME": d, "SHELL": "/bin/sh",
    }
    r = subprocess.run(["/bin/sh", SESSION], env=env, capture_output=True,
                       text=True, timeout=120)
    lines = [ln for ln in open(trace).read().splitlines() if ln.strip()]
    return r, lines


def idx(lines, needle, start=0):
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i
    return -1


def test(needed, firstrun=False):
    if firstrun:
        label = "a machine set up for somebody else (first-run owed)"
    else:
        label = "a machine WITH a password" if needed else \
                "a machine with NO password (every live boot)"
    print("-- %s" % label)
    r, lines = run_session(needed, firstrun)
    check(r.returncode == 0,
          "session.sh runs to the end (rc=%d)\n%s"
          % (r.returncode, r.stderr[-400:]))

    i_splash = idx(lines, "splash.py")
    i_needed = idx(lines, "login.py --needed")
    i_login = -1
    for i, ln in enumerate(lines):
        if "login.py" in ln and "--needed" not in ln:
            i_login = i
            break
    i_kb = idx(lines, "nbi18n")
    i_finder = idx(lines, "finder.py")
    i_widgets = idx(lines, "widgets.py")
    i_shell = idx(lines, "shell.py")
    i_bg = idx(lines, "desktopbg.py")

    check(i_splash >= 0, "a loading screen is started")
    check(i_needed >= 0, "the sign-in gate is asked BEFORE anything is drawn")
    check(i_splash < i_needed,
          "the loading screen goes up before the gate is asked")
    check(i_finder >= 0 and i_widgets >= 0 and i_shell >= 0,
          "the Finder, the widget column and the shell are all started")
    # (Background jobs — the mixer, the widget column — finish in whatever
    #  order they like, so only the ORDERED launches are asserted here. That
    #  the shell is exec'd last is a property of the source, checked below.)
    check(i_bg >= 0 and i_bg < i_finder,
          "the backdrop is painted before any window opens on it")
    check(i_kb >= 0 and i_kb < i_needed,
          "the keyboard layout job is started before the sign-in gate")

    if firstrun:
        i_fr = -1
        for i, ln in enumerate(lines):
            if "firstrun.py" in ln and "--needed" not in ln:
                i_fr = i
                break
        check(i_fr >= 0, "the first-run setup screen is actually run")
        check(i_fr < i_finder and i_fr < i_widgets and i_fr < i_shell,
              "NOTHING of the desktop is started behind first-run setup")
        splashes = [i for i, ln in enumerate(lines) if "splash.py" in ln]
        check(len(splashes) == 2,
              "one loading screen before it and one after, not three "
              "(saw %d)" % len(splashes))
        if len(splashes) == 2:
            check(splashes[0] < i_fr < splashes[1],
                  "the second loading screen goes up AFTER setup, not over it")
    if needed:
        check(i_login >= 0, "the sign-in screen is actually run")
        check(i_needed < i_login, "the gate is asked before the screen is drawn")
        check(i_login < i_finder and i_login < i_widgets and i_login < i_shell,
              "NOTHING of the desktop is started behind the sign-in screen")
        # The splash must have been told to retire before the prompt appears,
        # and a fresh one put up after it.
        splashes = [i for i, ln in enumerate(lines) if "splash.py" in ln]
        check(len(splashes) == 2,
              "a fresh loading screen covers the rest of the start-up "
              "(saw %d splash launches)" % len(splashes))
        if len(splashes) == 2:
            check(splashes[0] < i_login < splashes[1],
                  "the second loading screen goes up AFTER the sign-in, not "
                  "over it")
            # (Its position relative to the Finder is checked on the SOURCE in
            #  test_flag_hygiene: both are backgrounded, so which of the two
            #  stub processes writes its trace line first is a coin toss.)
    else:
        check(i_login < 0,
              "no sign-in window is constructed when there is no password")
        if not firstrun:
            # Only when NOTHING is asked before the desktop. A first-run
            # machine legitimately retires the splash and puts a fresh one up
            # around its setup screen, which the firstrun branch asserts.
            check(len([i for i, ln in enumerate(lines)
                       if "splash.py" in ln]) == 1,
                  "exactly one loading screen on a machine with no password")

    return lines


def test_flag_hygiene():
    """The splash dismisses on /tmp/nb-ready. A stale flag from a previous boot
    would retire it instantly; the session must clear it before starting one."""
    print("-- the /tmp/nb-ready flag, and the shape of the script")
    src = open(SESSION).read()
    code = [ln.strip() for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    rm = next((i for i, ln in enumerate(code)
               if ln.startswith("rm -f /tmp/nb-ready")), -1)
    splash = next((i for i, ln in enumerate(code) if "splash.py" in ln), -1)
    check(0 <= rm < splash,
          "a stale ready-flag is cleared before the first loading screen")
    check(len([ln for ln in code if "splash.py" in ln]) >= 2,
          "the session knows how to put a second loading screen up")
    check(len([ln for ln in code if "login.py" in ln]) >= 2,
          "the sign-in is asked about first, then drawn")
    check(any("--needed" in ln for ln in code),
          "the sign-in is gated on the cheap probe, not a full Gtk start")
    check(code[-1].startswith("exec ") and "shell.py" in code[-1],
          "the last thing the session does is exec the shell: %r" % code[-1])
    login = next((i for i, ln in enumerate(code)
                  if "login.py" in ln and "--needed" not in ln), -1)
    splash2 = [i for i, ln in enumerate(code) if "splash.py" in ln][-1]
    check(login >= 0 and login < splash2,
          "the second loading screen is started after the sign-in returns")
    for name in ("finder.py", "widgets.py", "shell.py"):
        j = next((i for i, ln in enumerate(code) if name in ln), -1)
        check(login >= 0 and j > login,
              "%s is started after the sign-in, never behind it" % name)
        check(j > splash2,
              "%s starts under the second loading screen, not before it"
              % name)
    check(not any(ln.rstrip().endswith("&") and "login.py" in ln
                  for ln in code),
          "the sign-in screen is never backgrounded")

    # Audio routing (de/nbaudio.py) writes /etc/asound.conf and then runs a sweep
    # of amixer calls. Nothing on screen waits for any of it, and every one of
    # those is a process spawn on a machine that is booting off a compressed
    # read-only root -- so it belongs BEHIND the loading screen and INSIDE the
    # backgrounded block, never on the path to the first pixel.
    audio = next((i for i, ln in enumerate(code) if "nbaudio.py" in ln), -1)
    if audio >= 0:
        check(audio > splash,
              "the audio routing runs behind the loading screen, not in front "
              "of the first pixel")
        tail = " ".join(code[audio:audio + 20])
        check(") &" in tail,
              "...and inside the backgrounded block, so the boot never waits "
              "on a mixer")


def main():
    if not os.path.exists("/bin/sh"):
        print("no /bin/sh")
        return 2
    test(needed=True)
    test(needed=False)
    # A machine prepared for somebody else: first-run setup is owed,
    # and there is no password yet because that is what it will set.
    test(needed=False, firstrun=True)
    test_flag_hygiene()
    print()
    if FAILURES:
        print("SESSION BOOT SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        return 1
    print("SESSION BOOT SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
