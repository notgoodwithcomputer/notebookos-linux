#!/usr/bin/env python3
"""Session preferences, applied at start-up.

WHY THIS FILE EXISTS. The Settings app knew how to apply screen blanking, key
repeat and render scale, and did it in its own `__init__` — so a preference
took effect only if you OPENED SETTINGS. Nothing starts Settings at boot, so
after a restart the machine was back on the defaults while the page went on
displaying the choice, which reads as confirmation. Screen blanking was the
worst of the three: `session.sh` runs `xset s off s noblank -dpms` every boot,
so the setting was not merely forgotten, it was actively undone.

`session.sh` now runs this straight after that line. What is saved wins; what
is not saved keeps the appliance default above.

NO GTK IMPORT, deliberately. This runs on the boot path, and pulling in Gtk to
call three `xset` commands would add most of a second to every start-up for
nothing. That is also why it is a module of its own rather than an argv branch
in settings.py.

ONE IMPLEMENTATION. settings.py's `_apply_blank` / `_apply_repeat` /
`_x_output` now delegate here, so the code that runs when you pick a value and
the code that runs at the next boot cannot drift apart — the failure this whole
item is made of.

    python3 nbprefs.py          # apply everything saved
    python3 nbprefs.py --print  # say what would be applied, change nothing
"""
import os
import sys
import json
import subprocess

CFG_DIR = ".config/notebook"
CFG_NAME = "settings.json"

# The scales the Displays page offers. Anything else in the file is ignored
# rather than passed to xrandr, which would take an arbitrary float.
SCALES = ("1.25", "1.5", "2.0")


def run(cmd, timeout=4):
    """Run a command, return (rc, stdout) — never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception:
        return 1, ""


def home():
    return os.environ.get("NB_HOME", os.path.expanduser("~"))


def config_path(base=None):
    return os.path.join(base or home(), CFG_DIR, CFG_NAME)


def load(base=None):
    """The saved settings, or {} — a damaged file must not stop the session."""
    try:
        with open(config_path(base), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def cfg_int(settings, key, default):
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def x_output(o=None):
    """The screen these controls act on: the one the user is sitting at.

    The internal panel first — the same rule display.sh applies. xrandr lists
    outputs in the server's order, so on a machine that enumerates HDMI before
    eDP this would otherwise drive the television.
    """
    if o is None:
        _rc, o = run(["xrandr"])
    connected = [line.split()[0] for line in o.splitlines()
                 if " connected" in line and line[:1].strip()]
    for name in connected:
        if name.startswith(("eDP", "LVDS", "DSI")):
            return name
    return connected[0] if connected else ""


def apply_blank(secs):
    """Screen blanking, in seconds. 0 means never."""
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        secs = 0
    if secs > 0:
        run(["xset", "s", str(secs)])
        run(["xset", "+dpms"])
        run(["xset", "dpms", str(secs), str(secs), str(secs)])
    else:
        run(["xset", "s", "off"])
        run(["xset", "-dpms"])


def apply_repeat(delay, rate):
    try:
        delay = int(delay)
        rate = int(rate)
    except (TypeError, ValueError):
        return
    run(["xset", "r", "rate", str(delay), str(rate)])


def apply_scale(scale, out=None):
    scale = str(scale)
    if scale not in SCALES:
        return          # 1.0 is native and a no-op; anything else is not ours
    out = out if out is not None else x_output()
    if out:
        run(["xrandr", "--output", out, "--scale", "%sx%s" % (scale, scale)])


def planned(settings):
    """[(name, detail)] for what apply_all would do — the --print view, and
    what the gate reads, so the report and the action share one decision."""
    out = []
    if "blank_timeout" in settings:
        secs = cfg_int(settings, "blank_timeout", 0)
        out.append(("blank_timeout",
                    ("blank after %ds" % secs) if secs > 0 else "never blank"))
    if "kbd_delay" in settings or "kbd_rate" in settings:
        out.append(("kbd_repeat", "delay %d, rate %d"
                    % (cfg_int(settings, "kbd_delay", 500),
                       cfg_int(settings, "kbd_rate", 25))))
    scale = str(settings.get("display_scale", "1.0"))
    if scale in SCALES:
        out.append(("display_scale", "supersample %sx" % scale))
    return out


def apply_all(settings=None, base=None):
    """Apply every saved session preference. Returns what it applied.

    A key that is ABSENT is left alone rather than applied at its default:
    session.sh has already set the appliance defaults, and re-asserting them
    here would mean this file, not that one, decides what a fresh machine does.
    """
    settings = load(base) if settings is None else settings
    done = planned(settings)
    names = {n for n, _ in done}
    if "blank_timeout" in names:
        apply_blank(cfg_int(settings, "blank_timeout", 0))
    if "kbd_repeat" in names:
        apply_repeat(cfg_int(settings, "kbd_delay", 500),
                     cfg_int(settings, "kbd_rate", 25))
    if "display_scale" in names:
        apply_scale(settings.get("display_scale"))
    return done


def main(argv):
    settings = load()
    if "--print" in argv:
        for name, detail in planned(settings):
            print("%-15s %s" % (name, detail))
        return 0
    apply_all(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
