#!/usr/bin/env python3
"""Launch EVERY shipped app on the running guest, from the Finder, and look.

The host harness constructs an app's widget tree in this process. That is not
the same as the machine opening it: on target an app also has to pass the trust
gate (a signed manifest — a stale one refuses everything), claim its single
instance, find its fonts, publish its map beacon and paint under software
rendering. construct_all cannot see any of that.

So this drives the REAL desktop over QMP: type the app's name into the Finder's
search field, double-click the one row that matches, wait for something to
paint, screenshot it, press Esc, and go on to the next. What it produces is one
PNG per app plus a one-line verdict, and the verdict is honest about what it
can tell: "painted" means the framebuffer changed from the Finder to something
else, not that the app is correct.

    NB_WORK=/tmp/nb-sweep python3 tools/target_app_sweep.py OUTDIR [app ...]

Run it against a guest that is already up (tools/run-desktop.sh --headless with
the same NB_WORK). See [[guest-fixture-stick]] for the harness rules; the
framebuffer is 1280x800 and guestdrive works in those pixels.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(
    ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

# Where the Finder's chrome sits at 1280x800 (measured from a boot screenshot).
SEARCH_XY = (600, 156)
FIRST_ROW_XY = (400, 246)
SETTLE = 2.0


def drive(*args):
    subprocess.run([sys.executable, os.path.join(HERE, "guestdrive.py")]
                   + [str(a) for a in args], check=False,
                   capture_output=True, timeout=120)


def shot(path):
    subprocess.run([sys.executable, os.path.join(HERE, "guestdrive.py"),
                    "shot", path], check=False, capture_output=True, timeout=120)


# The menu bar's app-name area at 1280x800: the shell prints the FOCUSED app's
# name there ("Finder", "Calculator", "Novel"). It is the only witness on
# screen that says WHICH app owns the machine, and it is what makes this sweep
# about the app rather than about "the picture changed" -- typing a name into
# the search box changes the picture too.
NAME_BOX = (60, 8, 320, 34)


def name_signature(path):
    try:
        from PIL import Image
        im = Image.open(path).convert("L").crop(NAME_BOX).resize((64, 12))
        px = list(im.getdata())
        return sum(px) / float(len(px))
    except Exception:                                             # noqa: BLE001
        return None


def framebuffer_signature(path):
    """A cheap fingerprint of what is on screen: the mean of a coarse grid.

    Enough to answer "did the screen become something else", which is the only
    question this tool is entitled to answer from pixels alone."""
    try:
        from PIL import Image
        im = Image.open(path).convert("L").resize((32, 20))
        px = list(im.getdata())
        return sum(px) / float(len(px))
    except Exception:                                             # noqa: BLE001
        return None


def clear_search():
    """Empty the Finder's search box, whatever is in it.

    Twenty-four backspaces was not "whatever is in it": the app names are
    longer than that together, so each pass left a few characters behind and
    the box quietly accumulated them -- by the ninth app it read
    "Cookbookntactslendarlculator...", every search matched nothing, and the
    sweep was driving a Finder that could not find anything. Select-all and one
    backspace cannot leave a remainder.
    """
    # guestdrive joins a combo with "+", not "-" ("ctrl-a" is two unknown
    # qcodes and sends nothing at all), and one backspace after select-all
    # cannot leave a remainder the way a fixed count of backspaces did.
    drive("key", "ctrl+a")
    time.sleep(0.2)
    drive("key", "backspace")
    time.sleep(0.2)


def visible_apps():
    import finder
    return sorted(name for name in finder.APP_MODULES
                  if name not in finder.HIDDEN_APPS)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    wanted = sys.argv[2:] or visible_apps()

    base = os.path.join(out, "00-desktop.png")
    shot(base)
    desktop = framebuffer_signature(base)
    print("desktop signature: %s" % desktop)

    # THE LAUNCHER HAS TO BE ON SCREEN BEFORE ANY OF THIS MEANS ANYTHING.
    # This tool types into the Finder's search field at a measured position and
    # double-clicks the first row. If no Finder window is up, those clicks land
    # on the desktop board instead: the board opens ITS app, the framebuffer
    # changes, and every row reads PAINTED while nothing under test ever ran.
    # That happened — a boot where the widget board covered the Finder was
    # reported as 29/29 apps painted, and the screenshots showed the board.
    # So: prove the search field is there by typing into it and watching the
    # screen answer, and refuse to sweep if it does not.
    drive("click", *SEARCH_XY)
    time.sleep(0.5)
    clear_search()
    time.sleep(SETTLE)
    # The baseline is re-taken HERE, with the box CLEARED. The first shot was
    # whatever the screen happened to hold -- and when it already held a
    # filtered list (someone had typed before this ran) the "did it answer"
    # comparison was two filtered lists against each other, which reads as no
    # answer while the keyboard was working perfectly.
    shot(base)
    desktop = framebuffer_signature(base)
    # NOT "zzzz": a repeated letter is a PRESS-AND-HOLD to nbdiacritics, which
    # opens the accent palette over the window instead of typing. Four
    # different letters, none of which names an app.
    drive("type", "zqxv")            # matches nothing: the list must empty
    time.sleep(SETTLE)
    probe = os.path.join(out, "00-search-probe.png")
    shot(probe)
    typed = framebuffer_signature(probe)
    for _ in range(6):
        drive("key", "backspace")
    time.sleep(1.0)
    if typed is None or desktop is None or abs(typed - desktop) < 0.4:
        print("the search field did not answer typing — is a Finder window "
              "open at %s? (see %s)" % (SEARCH_XY, probe))
        print("RESULT: FAILED: no Finder window to drive")
        return 1
    print("finder search field answers typing (signature %s -> %s)"
          % (desktop, typed))
    finder_sig = desktop
    finder_name = name_signature(base)
    print("finder menu-bar name signature: %s" % finder_name)

    verdicts = []
    stuck = []
    for i, name in enumerate(wanted, 1):
        tag = "%02d-%s" % (i, name.replace(" ", "-").lower())
        # search for it, so the row we double-click is always the first one
        # Give the Finder time to come back before touching it. The previous
        # row's app has just closed; clicking into the search field while the
        # window is still being remapped lands on nothing, and then the typed
        # name goes nowhere -- which is exactly what made every row open the
        # same app (the double-click hit the same position in an unfiltered
        # list). Click, verify the box answered, and only then type.
        for attempt in range(3):
            drive("click", *SEARCH_XY)
            time.sleep(0.6)
            clear_search()
            drive("type", "zqxv")
            time.sleep(1.2)
            probe_i = os.path.join(out, tag + "-probe.png")
            shot(probe_i)
            if (framebuffer_signature(probe_i) or 0) != (desktop or 0):
                os.remove(probe_i)
                break
            time.sleep(2.0)
        clear_search()
        drive("type", name)
        time.sleep(SETTLE)
        drive("dblclick", *FIRST_ROW_XY)
        time.sleep(9.0)                          # TCG launch, software paint
        path = os.path.join(out, tag + ".png")
        shot(path)
        sig = framebuffer_signature(path)
        # The MENU BAR NAMES THE APP. Comparing whole frames cannot tell an app
        # that opened from a search box that filtered a list -- both change the
        # picture, and the first version of this sweep called the second one
        # PAINTED twenty-nine times over. This asks the question the sweep
        # exists to answer: is the machine showing something other than the
        # Finder now?
        nsig = name_signature(path)
        painted = (nsig is not None and finder_name is not None
                   and abs(nsig - finder_name) > 0.5)
        verdicts.append((name, painted, sig))
        print("%-22s %s  (signature %s)"
              % (name, "PAINTED" if painted else "no change", sig))
        # CLOSE IT, AND CHECK THAT IT CLOSED. Esc alone is not enough: in
        # several apps Esc only LEAVES the pane (house law), so the app stayed
        # up and the next row's double-click landed INSIDE it -- the shot named
        # "illustrator" was Academics, still open from three rows earlier.
        # Ctrl+W is the OS-wide close accelerator (nbapp._on_key), and the
        # screen is compared back to the Finder before moving on.
        for attempt in range(4):
            drive("key", "esc")
            time.sleep(1.0)
            drive("key", "ctrl+w")
            time.sleep(3.0)
            back = os.path.join(out, tag + "-closed.png")
            shot(back)
            sig_back = name_signature(back)
            if (sig_back is not None and finder_name is not None
                    and abs(sig_back - finder_name) <= 0.5):
                os.remove(back)
                break
        else:
            print("   %s DID NOT CLOSE — the sweep cannot trust what follows"
                  % name)
            stuck.append(name)

    print()
    bad = [n for n, ok, _s in verdicts if not ok]
    if stuck:
        print("DID NOT CLOSE: " + ", ".join(stuck))
    print("%d/%d apps painted something of their own" %
          (len(verdicts) - len(bad), len(verdicts)))
    if bad:
        print("NO CHANGE ON SCREEN: " + ", ".join(bad))
        print("RESULT: FAILED")
        return 1
    if stuck:
        print("RESULT: FAILED: %d app(s) would not close" % len(stuck))
        return 1
    print("RESULT: ALL PAINTED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
