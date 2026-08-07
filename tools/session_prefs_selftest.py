#!/usr/bin/env python3
"""
A saved preference has to survive a restart.

ROADMAP #22. Screen blanking, key repeat and render scale were applied in the
Settings app's `__init__`, so they took effect only while somebody had Settings
OPEN. Nothing starts Settings at boot. After a restart the machine was back on
the defaults — and reopening the page showed the saved value, which reads as
confirmation that it is in force.

Blanking was worse than forgotten. `session.sh` runs `xset s off s noblank
-dpms` on every boot, so the choice was actively undone, every time, by a line
written for walk-up demos.

The fix is `de/nbprefs.py`, which session.sh runs straight after that default
line, and which settings.py's own appliers now delegate to — one implementation,
so what happens when you pick a value and what happens at the next boot cannot
drift apart. That is the whole of the defect, so it is the first thing checked.

WHY THIS ASKS THE X SERVER. Capturing nbprefs' subprocess calls would prove it
INTENDS to blank the screen. `xset q` reports what the server will actually do,
which is the property. The suite restores the server's original state on the
way out.

Run:
    tools/guestrun.sh python3 tools/session_prefs_selftest.py
    tools/guestrun.sh python3 tools/session_prefs_selftest.py --de DIR
"""
import os
import re
import sys
import json
import shutil
import subprocess
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-prefs-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)
SESSION = os.path.join(os.path.dirname(DE), "session.sh")

import nbprefs  # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def write_cfg(**keys):
    d = os.path.join(_HOME, ".config", "notebook")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "settings.json"), "w") as fh:
        json.dump(keys, fh)


def xset_state():
    """(screensaver timeout, dpms standby, dpms enabled) as the server has it."""
    out = subprocess.run(["xset", "q"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT).stdout.decode("utf-8", "replace")
    ss = re.search(r"timeout:\s+(\d+)", out)
    st = re.search(r"Standby:\s+(\d+)", out)
    en = "DPMS is Enabled" in out
    return (int(ss.group(1)) if ss else None,
            int(st.group(1)) if st else None, en)


def main():
    saved_state = xset_state()

    # ---- the defect itself: boot applies it, not the Settings window ----
    block = open(SESSION).read()
    # Strip comments: the paragraph EXPLAINING the fix names every string this
    # would otherwise match, so an unfixed session.sh with a good comment would
    # pass (blind-spot class 7 — three instances in this repo already).
    code = "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("#"))
    called = check("session.sh applies saved preferences at start-up",
                   "nbprefs.py" in code)
    m_def = re.search(r"xset s off s noblank -dpms", code)
    m_pref = re.search(r"nbprefs\.py", code)
    if called and m_def and m_pref:
        # Order is the whole point. Run the applier first and the blanket
        # default below it wipes the choice out again — the shipped bug.
        check("...AFTER the appliance default, so the choice wins",
              m_pref.start() > m_def.start(),
              "default@%d prefs@%d" % (m_def.start(), m_pref.start()))
    else:
        not_reached("no invocation to order",
                    "...AFTER the appliance default, so the choice wins")

    # ---- one implementation, not two ------------------------------------
    src = open(os.path.join(DE, "settings.py")).read()
    scode = "\n".join(ln for ln in src.splitlines()
                      if not ln.lstrip().startswith("#"))
    # The one xset settings.py may still own is `dpms force off` — the
    # "turn the screen off now" BUTTON, which is an action, not the policy.
    # Matching on the bare word `dpms` flags that too (measured: it did).
    xsets = re.findall(r'run\(\["xset",([^\]]*)\]', scode)
    stray = [x.strip() for x in xsets if '"force"' not in x]
    check("settings.py delegates blanking rather than repeating it",
          "nbprefs.apply_blank" in scode and not stray, repr(stray))
    check("...and key repeat", "nbprefs.apply_repeat" in scode
          and 'run(["xset", "r", "rate"' not in scode)
    check("...and it re-applies everything through one call",
          "nbprefs.apply_all" in scode)
    check("nbprefs stays off the boot path's slow road (no Gtk)",
          "gi.repository" not in open(os.path.join(DE, "nbprefs.py")).read())

    # ---- what the X server actually ends up doing ------------------------
    try:
        # A saved blank timeout must reach the server.
        write_cfg(blank_timeout=300)
        nbprefs.apply_all()
        ss, st, en = xset_state()
        got = check("a saved blank timeout reaches the X server",
                    ss == 300, "timeout=%r" % ss)
        if got:
            check("...and DPMS is armed with it", en and st == 300,
                  "enabled=%s standby=%r" % (en, st))
        else:
            not_reached("timeout not set", "...and DPMS is armed with it")

        # "Never" is a choice too, and it must be honoured as one.
        write_cfg(blank_timeout=0)
        nbprefs.apply_all()
        ss, _st, en = xset_state()
        check("a saved 'never' turns blanking off", ss == 0 and not en,
              "timeout=%r enabled=%s" % (ss, en))

        # An ABSENT key must not be applied at a default: session.sh has
        # already set the appliance default, and overriding it here would move
        # the decision into the wrong file.
        subprocess.run(["xset", "s", "600"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        write_cfg(tz="UTC")            # a settings file with no blank key
        done = nbprefs.apply_all()
        ss, _st, _en = xset_state()
        check("an unset preference is left alone, not defaulted",
              ss == 600 and not any(n == "blank_timeout" for n, _ in done),
              "timeout=%r applied=%r" % (ss, done))

        # A damaged file must not stop the session starting.
        d = os.path.join(_HOME, ".config", "notebook")
        with open(os.path.join(d, "settings.json"), "w") as fh:
            fh.write('{"blank_timeout": 3')
        try:
            nbprefs.apply_all()
            check("a truncated settings file does not stop start-up", True)
        except Exception as exc:
            check("a truncated settings file does not stop start-up", False,
                  repr(exc))

        # ---- and the same thing through the real entry point -------------
        # session.sh runs a COMMAND, not a function. An ImportError or a bad
        # __main__ would be invisible to everything above.
        write_cfg(blank_timeout=420, kbd_delay=250, kbd_rate=30)
        env = dict(os.environ)
        env["NB_HOME"] = _HOME
        p = subprocess.run([sys.executable, os.path.join(DE, "nbprefs.py")],
                           env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        ran = check("running nbprefs.py as a command succeeds",
                    p.returncode == 0,
                    p.stderr.decode("utf-8", "replace")[-160:])
        if ran:
            ss, _st, _en = xset_state()
            check("...and the server took the value", ss == 420, "timeout=%r" % ss)
        else:
            not_reached("the command failed", "...and the server took the value")

        # --print must describe the same decision the applier makes, or the
        # two answer different questions and one of them is wrong.
        p = subprocess.run([sys.executable, os.path.join(DE, "nbprefs.py"),
                            "--print"], env=env, stdout=subprocess.PIPE)
        text = p.stdout.decode("utf-8", "replace")
        check("--print lists blanking and key repeat",
              "blank_timeout" in text and "kbd_repeat" in text, repr(text))
        check("--print changes nothing on its own",
              xset_state()[0] == 420)
    finally:
        ss, st, en = saved_state
        subprocess.run(["xset", "s", str(ss or 0)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        subprocess.run(["xset", "+dpms" if en else "-dpms"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if en:
            subprocess.run(["xset", "dpms", str(st or 0), str(st or 0),
                            str(st or 0)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
