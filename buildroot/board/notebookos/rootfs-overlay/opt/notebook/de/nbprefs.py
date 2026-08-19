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
import re
import subprocess

CFG_DIR = ".config/notebook"
CFG_NAME = "settings.json"

# The scales the Displays page offers. Anything else in the file is ignored
# rather than passed to xrandr, which would take an arbitrary float.
SCALES = ("1.0", "1.25", "1.5", "2.0")
MODE_RE = re.compile(r"^[1-9][0-9]{2,4}x[1-9][0-9]{2,4}$")


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


def tz_of(settings):
    """The POSIX TZ string this machine should be keeping time in, or "".

    The POSIX form ("EST5EDT,M3.2.0,M11.1.0"), not the IANA name: NO ZONEINFO
    SHIPS in this image, so /etc/localtime cannot be pointed anywhere and the
    IANA name on its own would tell the C library nothing. The name is kept
    beside it in `tz` only so the Settings page can show the row it belongs to.
    """
    tz = settings.get("tz_posix")
    return tz if isinstance(tz, str) and tz.strip() else ""


def apply_timezone(settings=None, base=None):
    """Put the saved zone into THIS process, and say what was applied.

    The whole difficulty of time zones on this machine is that the only lever
    is the TZ environment variable — with no zoneinfo there is no /etc/localtime
    to re-point — and an environment variable reaches exactly one process. So
    changing the zone in Settings updated Settings and nothing else: the panel
    clock, Calendar and the Journal each kept the zone their own process had
    started with.

    Three places therefore call this. session.sh exports TZ for the session at
    start-up; the shell re-applies it when the setting changes, which fixes the
    clock AND every app launched afterwards (they inherit the shell's
    environment); and settings.py applies it as it saves.

    Already-open apps keep the zone they started in — no process can reach into
    another one's environment — so they show the new zone the next time they are
    opened. Closing that last gap means every app re-checking on focus, which is
    a change to nbapp and therefore to all forty of them; it is worth doing
    deliberately rather than as a side effect of this fix.
    """
    settings = load(base) if settings is None else settings
    tz = tz_of(settings)
    if not tz:
        # Empty means the appliance default, which session.sh represents by
        # leaving TZ unset.  This function also runs in the long-lived shell:
        # merely returning here would retain the previously selected zone and
        # pass that stale environment to every subsequently launched app.
        os.environ.pop("TZ", None)
        try:
            import time
            time.tzset()
        except (ImportError, AttributeError):
            pass
        return ""
    if os.environ.get("TZ") != tz:
        os.environ["TZ"] = tz
    try:
        import time
        time.tzset()
    except (ImportError, AttributeError):
        pass          # a platform without tzset keeps whatever it had
    return tz


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
        results = [run(["xset", "s", str(secs)]),
                   run(["xset", "+dpms"]),
                   run(["xset", "dpms", str(secs), str(secs), str(secs)])]
    else:
        results = [run(["xset", "s", "off"]),
                   run(["xset", "-dpms"])]
    return all(rc == 0 for rc, _output in results)


def apply_repeat(delay, rate):
    try:
        delay = int(delay)
        rate = int(rate)
    except (TypeError, ValueError):
        return False
    rc, _output = run(["xset", "r", "rate", str(delay), str(rate)])
    return rc == 0


def _display_snapshot():
    rc, output = run(["xrandr"])
    return output if rc == 0 else ""


def _connected_outputs(snapshot):
    return [line.split()[0] for line in snapshot.splitlines()
            if " connected" in line and line[:1].strip()]


def _active_mode(snapshot, output):
    for line in snapshot.splitlines():
        if line.startswith(output + " connected"):
            match = re.search(r"\b([0-9]+)x([0-9]+)\+[0-9]+\+[0-9]+", line)
            if match:
                return int(match.group(1)), int(match.group(2))
    return None


def _mirror_canvas(primary, width, height, snapshot):
    """Map every other connected output onto the primary's logical canvas."""
    canvas = "%dx%d" % (width, height)
    for output in _connected_outputs(snapshot):
        if output == primary:
            continue
        rc, _text = run(["xrandr", "--output", output, "--auto",
                         "--scale-from", canvas, "--same-as", primary])
        if rc != 0:
            # The primary preference has already succeeded and cannot be
            # transactionally rolled back without another race. A TV may have
            # vanished since the snapshot, or reject the transform. Do not
            # report the whole setting as failed (which leaves UI/disk lying
            # about the visibly changed panel). Re-read the connector state
            # before deciding whether there is still a sink to reconcile.
            fresh = _display_snapshot()
            if output not in _connected_outputs(fresh):
                continue                 # hot-unplug: nothing remains to fix
            # XRandR can be briefly busy while the primary CRTC changes. Retry
            # once against fresh topology; if the sink still rejects it, leave
            # its existing picture intact rather than turning a connected TV
            # black. A later hotplug/settings apply will reconcile it again.
            run(["xrandr", "--output", output, "--auto",
                 "--scale-from", canvas, "--same-as", primary])
    return True


def apply_scale(scale, out=None):
    scale = str(scale)
    if scale not in SCALES:
        return False    # anything outside the offered list is not ours
    snapshot = _display_snapshot()
    out = out if out is not None else x_output(snapshot)
    if out:
        rc, _output = run(["xrandr", "--output", out, "--scale",
                           "%sx%s" % (scale, scale)])
        if rc != 0:
            return False
        mode = _active_mode(snapshot, out)
        if mode:
            width = max(1, int(round(mode[0] * float(scale))))
            height = max(1, int(round(mode[1] * float(scale))))
            return _mirror_canvas(out, width, height, snapshot)
        return True
    return False


def apply_resolution(mode, out=None):
    """Apply a saved RandR mode to the internal/primary screen when valid."""
    mode = str(mode)
    if not MODE_RE.match(mode):
        return False
    snapshot = _display_snapshot()
    out = out if out is not None else x_output(snapshot)
    if out:
        rc, _output = run(["xrandr", "--output", out, "--mode", mode])
        if rc != 0:
            return False
        width, height = (int(part) for part in mode.split("x"))
        return _mirror_canvas(out, width, height, snapshot)
    return False


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
    mode = str(settings.get("display_resolution", ""))
    if MODE_RE.match(mode):
        out.append(("display_resolution", mode))
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
    # Before the rest: everything below shells out, and a subprocess inherits
    # this process's environment, so the zone wants to be right first.
    apply_timezone(settings)
    if "blank_timeout" in names:
        if not apply_blank(cfg_int(settings, "blank_timeout", 0)):
            done = [item for item in done if item[0] != "blank_timeout"]
    if "kbd_repeat" in names:
        if not apply_repeat(cfg_int(settings, "kbd_delay", 500),
                            cfg_int(settings, "kbd_rate", 25)):
            done = [item for item in done if item[0] != "kbd_repeat"]
    if "display_resolution" in names:
        if not apply_resolution(settings.get("display_resolution")):
            done = [item for item in done if item[0] != "display_resolution"]
    if "display_scale" in names:
        if not apply_scale(settings.get("display_scale")):
            done = [item for item in done if item[0] != "display_scale"]
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
