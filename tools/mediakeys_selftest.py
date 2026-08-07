#!/usr/bin/env python3
"""
A media key either does something or says why not.

`de/nbmediakeys.py` had no suite. It grabs the XF86 volume and brightness keys
for the whole session, so it is the only thing standing between those keys and
doing nothing at all.

Two defects, and the file contained the argument against both of them already.
`_OSD.show_note` exists because a volume key that moves an unheard level, or
shows nothing whatever, "reads as a dead key" — and the header six hundred
lines up described exactly that as the intended brightness behaviour: *"with
none present (QEMU, a desktop with no panel) the brightness keys simply no-op —
no OSD, no error."* The same principle, applied to one branch and not its
sibling. Volume with no mixer at all was silent for the same reason.

And `_brightness` could reach **0**, which is a black panel. The controls for
getting back are all on the screen you can no longer read; two presses from 16%
did it.

WHY THIS USES A REAL BACKLIGHT TREE. `_brightness` reads `max_brightness`,
reads `brightness`, computes, and writes it back. A stubbed `_brightness` would
be a test of the stub. `/sys/class/backlight/*` is found by a glob, so pointing
that glob at a directory of real files exercises the real arithmetic and the
real write — and the value is read back off disk afterwards.

Run:
    tools/guestrun.sh python3 tools/mediakeys_selftest.py
    tools/guestrun.sh python3 tools/mediakeys_selftest.py --de DIR
"""
import os
import sys
import glob
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-mk-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import nbmediakeys as MK  # noqa: E402

# MK.glob IS this module's glob — the same object — so a replacement that calls
# glob.glob() calls itself. Hold the real function before touching anything.
REAL_GLOB = glob.glob

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


def pump():
    for _ in range(200):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


class Backlight(object):
    """A real /sys/class/backlight-shaped directory the real code can drive."""

    def __init__(self, mx, cur):
        self.dir = tempfile.mkdtemp(prefix="nb-bl-")
        self.dev = os.path.join(self.dir, "intel_backlight")
        os.makedirs(self.dev)
        self.write("max_brightness", mx)
        self.write("brightness", cur)

    def write(self, name, v):
        with open(os.path.join(self.dev, name), "w") as fh:
            fh.write(str(v))

    def read(self):
        with open(os.path.join(self.dev, "brightness")) as fh:
            return int(fh.read().strip())

    def install(self):
        MK.glob.glob = lambda pat: (sorted(REAL_GLOB(
            os.path.join(self.dir, "*"))) if "backlight" in pat
            else REAL_GLOB(pat))

    def remove(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Recorder(object):
    """The OSD, as far as _on_key can tell: what it was asked to show."""

    def __init__(self):
        self.levels = []
        self.notes = []

    def show_level(self, kind, pct, muted=False):
        self.levels.append((kind, int(pct), bool(muted)))

    def show_note(self, kind, text):
        self.notes.append((kind, text))


def main():
    real_glob = REAL_GLOB

    # ---- the brightness arithmetic, against a real device ----------------
    bl = Backlight(mx=100, cur=50)
    bl.install()
    try:
        pct = MK._brightness(MK.BRIGHT_STEP)
        check("brightness up moves the device", bl.read() == 58 and pct == 58,
              "raw=%d pct=%r" % (bl.read(), pct))
        pct = MK._brightness(-MK.BRIGHT_STEP)
        check("brightness down moves it back", bl.read() == 50 and pct == 50,
              "raw=%d pct=%r" % (bl.read(), pct))

        # THE TRAP. Walk it all the way down and it must stop somewhere the
        # screen is still readable, not at 0.
        for _ in range(40):
            MK._brightness(-MK.BRIGHT_STEP)
        low = bl.read()
        check("holding brightness-down never blacks the screen out", low > 0,
              "raw=%d of 100" % low)
        check("...and stops somewhere still visible", low >= 5, "raw=%d" % low)

        # It must still go all the way UP.
        for _ in range(40):
            MK._brightness(MK.BRIGHT_STEP)
        check("holding brightness-up reaches full", bl.read() == 100,
              "raw=%d" % bl.read())
    finally:
        bl.remove()
        MK.glob.glob = real_glob

    # A panel with a tiny range must not be pinned to a floor it cannot leave.
    bl = Backlight(mx=7, cur=7)
    bl.install()
    try:
        for _ in range(20):
            MK._brightness(-MK.BRIGHT_STEP)
        check("a 7-step panel still dims", bl.read() < 7, "raw=%d of 7" % bl.read())
        check("...but not to nothing", bl.read() >= 1, "raw=%d" % bl.read())
    finally:
        bl.remove()
        MK.glob.glob = real_glob

    # ---- a key with nothing to move must SAY so --------------------------
    mk = MK.MediaKeys.__new__(MK.MediaKeys)
    rec = Recorder()
    mk.osd = rec

    # No backlight at all: this is QEMU, and every desktop with no panel.
    MK.glob.glob = lambda pat: ([] if "backlight" in pat else REAL_GLOB(pat))
    try:
        mk._on_key(MK.XF86_MonBrightnessUp)
        mk._on_key(MK.XF86_MonBrightnessDown)
    finally:
        MK.glob.glob = real_glob
    said = check("with no backlight, a brightness key says something",
                 len(rec.notes) == 2, "notes=%r levels=%r" % (rec.notes, rec.levels))
    if said:
        check("...and shows no level it cannot actually set", not rec.levels,
              repr(rec.levels))
        check("...using the sentence the Displays page already uses",
              all("screen" in t.lower() for _k, t in rec.notes),
              repr(rec.notes[0][1]))
        check("...attributed to brightness, not volume",
              all(k == "brightness" for k, _t in rec.notes))
    else:
        not_reached("no note", "...and shows no level it cannot actually set",
                    "...using the sentence the Displays page already uses",
                    "...attributed to brightness, not volume")

    # No mixer to move either — amixer absent, or no Master control.
    rec2 = Recorder()
    mk.osd = rec2
    real_vol, real_hasvol = MK._volume, MK.nbaudio.has_volume
    MK._volume = lambda delta=None, toggle=False: (None, False)
    MK.nbaudio.has_volume = lambda *a, **k: True     # not the HDMI case
    try:
        for sym in (MK.XF86_AudioRaiseVolume, MK.XF86_AudioLowerVolume,
                    MK.XF86_AudioMute):
            mk._on_key(sym)
    finally:
        MK._volume, MK.nbaudio.has_volume = real_vol, real_hasvol
    vsaid = check("with no mixer, a volume key says something",
                  len(rec2.notes) == 3,
                  "notes=%r levels=%r" % (rec2.notes, rec2.levels))
    if vsaid:
        check("...and shows no level", not rec2.levels, repr(rec2.levels))
        check("...attributed to volume", all(k == "volume" for k, _t in rec2.notes))
    else:
        not_reached("no note", "...and shows no level", "...attributed to volume")

    # The HDMI case must keep its OWN sentence, not be swallowed by the new one.
    rec3 = Recorder()
    mk.osd = rec3
    MK.nbaudio.has_volume = lambda *a, **k: False
    try:
        mk._on_key(MK.XF86_AudioRaiseVolume)
    finally:
        MK.nbaudio.has_volume = real_hasvol
    check("sound over HDMI still names the television",
          len(rec3.notes) == 1 and "televi" in rec3.notes[0][1].lower(),
          repr(rec3.notes))

    # ---- the OSD can actually draw both shapes ---------------------------
    # A note and a level take different paths through _draw; a crash there is
    # invisible to everything above, which only records the call.
    osd = MK._OSD()
    try:
        osd.show_level("volume", 40, False)
        osd.show_level("volume", 0, True)
        osd.show_level("brightness", 100)
        osd.show_note("brightness", "This screen cannot be adjusted from here.")
        pump()
        surf = osd.area.get_window()
        check("the OSD builds and draws a level and a note", True)
    except Exception as exc:
        check("the OSD builds and draws a level and a note", False, repr(exc))
    try:
        osd.destroy()
    except Exception:
        pass

    # ---- every note fits the popup, in every language --------------------
    # The OSD is 300x92 and the note is drawn at (H - lh)/2, which goes
    # NEGATIVE the moment the wrapped text is taller than the popup — the text
    # is then drawn above it and clipped, with nothing to say so. These are
    # plain catalog lookups, so the JSON value is exactly what _t returns and
    # all seventeen can be measured in one process.
    import cairo
    import json as _json
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    NOTES = ("This screen cannot be adjusted from here.",
             "Volume cannot be adjusted from here.",
             "Volume is set on the television")
    osd2 = MK._OSD()
    W, H = osd2._w, osd2._h
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surf)

    def note_height(text):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription("Nimbus Sans 14"))
        lay.set_text(text, -1)
        lay.set_width((W - 66 - 20) * Pango.SCALE)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        return lay.get_pixel_size()[1]

    over, missing = [], []
    for path in sorted(glob.glob(os.path.join(DE, "lang_*.json"))):
        lg = os.path.basename(path)[5:-5]
        cat = _json.load(open(path, encoding="utf-8"))
        for k in NOTES:
            v = cat.get(k)
            if not v:
                missing.append((lg, k))
                continue
            h = note_height(v)
            if h > H:
                over.append((lg, h, v[:30]))
    for k in NOTES:                       # English is the key itself
        h = note_height(k)
        if h > H:
            over.append(("en", h, k[:30]))
    check("every OSD note is translated in all 17 languages", not missing,
          "; ".join("%s %r" % (l, k[:28]) for l, k in missing[:3]))
    check("...and fits the %dx%d popup without clipping" % (W, H), not over,
          "; ".join("%s %dpx %r" % (l, h, t) for l, h, t in over[:3]))
    try:
        osd2.destroy()
    except Exception:
        pass

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
