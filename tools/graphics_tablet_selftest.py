#!/usr/bin/env python3
"""graphics_tablet_selftest — can a USB pen tablet actually draw?

    DISPLAY=:0 python3 tools/graphics_tablet_selftest.py

"Supported" for a drawing tablet is a CHAIN, and it is worth nothing if any
link is missing. This file walks the chain link by link and proves each one
against the artefact that ships, not against a description of it:

1. **The kernel binds the hardware.** hid-wacom and hid-uclogic cover Wacom and
   the UC-Logic silicon inside Huion / XP-Pen / UGEE / Gaomon — between them,
   essentially every tablet on sale. Checked in the tracked seed AND in the
   live build config, because two sources of truth that disagree is exactly the
   defect docs/SECURITY-MODEL.md files as F4.
2. **X routes the pen to the driver that speaks pressure.** The OS forces the
   evdev driver for absolute pointers (a real fix — libinput freezes the QEMU
   usb-tablet). evdev cannot carry tablet-tool pressure or the eraser identity,
   so a pen must NOT be caught by that rule. This is checked by parsing the
   .conf files that ship and replaying xorg's "last InputClass naming a Driver
   wins" against a modelled device, not by eyeballing the file.
3. **The pressure maps to something the pixel engine can express.** This engine
   writes exact bytes and never blends, so pressure drives WIDTH.
4. **Illustrator actually paints narrower when pressed lighter** — driven as a
   real gesture and read back off the surface, which is the only check that can
   see the size argument being threaded through but then ignored.

Every family ends with a MUTANT: the check is re-run against deliberately
broken input and must go RED. A gate that cannot go red is not a gate.

Exit status is the number of failures.
"""
import os
import fnmatch
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
SEED = os.path.join(REPO, "tools/desktop.config")
LIVE = os.path.join(REPO, "kbuild-desktop/.config")
ETC_XCONF = os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/etc/X11/xorg.conf.d")
ABS_UDEV = os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/etc/udev/rules.d",
    "70-notebookos-absolute-pointer.rules")
SYS_XCONF = os.path.join(
    REPO, "buildroot/output/target/usr/share/X11/xorg.conf.d")

sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="tablet-selftest-")

FAILS = []
SKIPS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def skip(name, why):
    """Skip VISIBLY. A gate that quietly degrades to nothing when its input is
    missing reports the same green as one that passed."""
    SKIPS.append(name)
    print("SKIP %s   -> %s" % (name, why))


def mutant(name, ok_when_broken):
    """`ok_when_broken` is the re-run verdict under sabotage: it must be False,
    or the check above it proved nothing."""
    CHECKS[0] += 1
    caught = not ok_when_broken
    print("%-4s MUTANT %s%s" % ("ok" if caught else "FAIL", name,
                                "" if caught else
                                "   -> sabotage went UNDETECTED"))
    if not caught:
        FAILS.append("MUTANT " + name)


# ==================================================== 1. the kernel binds it
print("--- 1. the kernel has a driver for the hardware -----------------")

# =m throughout: nothing is resident until a tablet is plugged in and MODALIAS
# pulls the module, so the idle attack surface is unchanged.
WANT = {
    "CONFIG_HID_WACOM": "m",       # Intuos, One, Bamboo, Cintiq, pen displays
    "CONFIG_HID_UCLOGIC": "m",     # Huion, XP-Pen, UGEE, Gaomon, Parblo, Veikk
    "CONFIG_HID_KYE": "m",         # Genius
    "CONFIG_HID_WALTOP": "m",      # Waltop OEM boards
    "CONFIG_HID_VIEWSONIC": "m",   # ViewSonic / Signotec pen displays
}
# Deliberately OFF: bespoke USB protocols for hardware discontinued in the
# 2000s. Unmaintained parsers with no consumer are what the attack-surface rule
# in docs/SECURITY-MODEL.md exists to keep out. Asserted so that "enable every
# tablet symbol" cannot quietly become the policy.
WANT_OFF = ("CONFIG_TABLET_USB_ACECAD", "CONFIG_TABLET_USB_AIPTEK",
            "CONFIG_TABLET_USB_HANWANG", "CONFIG_TABLET_USB_KBTAB",
            "CONFIG_TABLET_USB_PEGASUS")


def kconfig(path):
    """{symbol: value} for set symbols; unset symbols are simply absent."""
    out = {}
    if not os.path.exists(path):
        return None
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def config_verdict(cfg, want=WANT):
    return cfg is not None and all(cfg.get(k) == v for k, v in want.items())


seed = kconfig(SEED)
check("the tracked seed enables every graphics-tablet driver, all =m",
      config_verdict(seed),
      {k: (seed or {}).get(k, "UNSET") for k in WANT})

for sym in WANT_OFF:
    check("policy: %s stays off (no consumer)" % sym,
          (seed or {}).get(sym) is None, (seed or {}).get(sym))

live = kconfig(LIVE)
if live is None:
    skip("the live build config agrees with the seed",
         "kbuild-desktop/.config absent — kernel tree not configured here")
else:
    disagree = {k: (seed.get(k), live.get(k))
                for k in WANT if seed and seed.get(k) != live.get(k)}
    check("the live build config agrees with the seed (one source of truth)",
          not disagree, disagree)

# Red-proof: a seed that forgot the load-bearing driver must not pass.
broken = dict(seed or {})
broken.pop("CONFIG_HID_WACOM", None)
mutant("a seed missing CONFIG_HID_WACOM", config_verdict(broken))
broken = dict(seed or {})
broken["CONFIG_HID_UCLOGIC"] = "n"
mutant("a seed with CONFIG_HID_UCLOGIC=n", config_verdict(broken))


# ================================== 1b. the module autoloads when plugged in
print("\n--- 1b. the built module claims the hardware (MODALIAS autoload) -")

# A driver compiled but never loaded is the same as no driver. udev matches the
# device's MODALIAS against modules.alias, which depmod builds from the aliases
# baked into each .ko — so the aliases have to actually be there.
KO = os.path.join(REPO, "kbuild-desktop/drivers/hid")
# modinfo lives in /sbin, which is not on a normal user's PATH. Resolving it
# matters more than it looks: `modinfo ... 2>/dev/null | wc -l` on a host
# without it returns 0, which is indistinguishable from a module that really
# has no aliases. Measured-zero and could-not-measure are different answers.
MODINFO = None
for cand in ("modinfo", "/sbin/modinfo", "/usr/sbin/modinfo"):
    if os.path.isabs(cand):
        if os.path.exists(cand):
            MODINFO = cand
            break
    else:
        from shutil import which
        if which(cand):
            MODINFO = which(cand)
            break

# (module, a vendor ID it must claim, whose hardware that is)
VENDORS = [
    ("wacom", "056A", "Wacom"),
    ("hid-uclogic", "28BD", "XP-Pen"),
    ("hid-uclogic", "5543", "UC-Logic / Huion"),
    ("hid-kye", "0458", "Genius"),
]

if MODINFO is None:
    skip("module aliases", "no modinfo on this host (it lives in /sbin)")
elif not os.path.isdir(KO):
    skip("module aliases", "%s absent — run make -C kbuild-desktop modules" % KO)
else:
    import subprocess

    def aliases(mod):
        path = os.path.join(KO, mod + ".ko")
        if not os.path.exists(path):
            return None
        try:
            out = subprocess.run([MODINFO, "-F", "alias", path],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return [ln for ln in out.stdout.splitlines() if ln.strip()]

    missing = [m for m in ("wacom", "hid-uclogic", "hid-kye", "hid-waltop",
                           "hid-viewsonic")
               if not os.path.exists(os.path.join(KO, m + ".ko"))]
    if missing:
        skip("module aliases", "not built yet: %s" % ", ".join(missing))
    else:
        for mod, vid, who in VENDORS:
            al = aliases(mod)
            if al is None:
                skip("%s claims %s (%s)" % (mod, vid, who),
                     "modinfo could not read the module")
                continue
            hits = [a for a in al
                    if re.search(r"v0*%s" % vid, a, re.I)]
            check("%s claims USB vendor %s (%s): %d device aliases"
                  % (mod, vid, who, len(hits)), bool(hits))


# ============================================ 2. X routes the pen correctly
print("\n--- 2. X gives the pen to the driver that speaks pressure -------")

try:
    _abs_rule = open(ABS_UDEV, encoding="utf-8").read()
except OSError:
    _abs_rule = ""
_cap = re.search(r'ATTRS\{capabilities/abs\}=="([^"]+)"', _abs_rule)
_cap_pattern = _cap.group(1) if _cap else ""
check("udev scopes the absolute-pointer tag to input event mouse devices",
      all(token in _abs_rule for token in
          ('SUBSYSTEM=="input"', 'KERNEL=="event*"',
           'ENV{ID_INPUT_MOUSE}=="1"')))
check("udev and Xorg use the same absolute-pointer tag",
      'ENV{ID_INPUT.tags}="notebook-absolute-pointer"' in _abs_rule)
check("the udev capability mask requires both ABS_X and ABS_Y",
      bool(_cap_pattern)
      and all(fnmatch.fnmatchcase(value, _cap_pattern)
              for value in ("3", "7", "b", "f", "1000003"))
      and not any(fnmatch.fnmatchcase(value, _cap_pattern)
                  for value in ("0", "1", "2", "4", "1000000")))

# Attributes xorg derives from udev's ID_INPUT_* tags (config/udev.c), and the
# MatchIs* directive each one answers (hw/xfree86/common/xf86Xinput.c).
MATCH_ATTR = {
    "matchispointer": "pointer",
    "matchiskeyboard": "keyboard",
    "matchistouchpad": "touchpad",
    "matchistouchscreen": "touchscreen",
    "matchistablet": "tablet",
    "matchistabletpad": "tabletpad",
    "matchisjoystick": "joystick",
}


def parse_inputclasses(text):
    """[(identifier, {directive: value}, driver)] in file order."""
    out = []
    for body in re.findall(r'(?is)^\s*Section\s+"InputClass"(.*?)^\s*EndSection',
                           text, re.M):
        directives, driver, ident = {}, None, ""
        for line in body.splitlines():
            m = re.match(r'\s*(\w+)\s+"([^"]*)"\s*$', line)
            if not m:
                continue
            key, val = m.group(1).lower(), m.group(2)
            if key == "driver":
                driver = val
            elif key == "identifier":
                ident = val
            else:
                directives[key] = val
        out.append((ident, directives, driver))
    return out


def conf_files():
    """Every InputClass xorg will read, in the order it reads them: the system
    directory first, then /etc — which is why 60-notebookos-input.conf can
    override 40-libinput.conf at all."""
    sections = []
    for d in (SYS_XCONF, ETC_XCONF):
        if not os.path.isdir(d):
            return None
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".conf"):
                text = open(os.path.join(d, fn),
                            encoding="utf-8", errors="replace").read()
                sections.extend(parse_inputclasses(text))
    return sections


def section_matches(directives, attrs, path, product="", tags=()):
    for key, val in directives.items():
        if key in MATCH_ATTR:
            want = val.strip().lower() in ("on", "true", "yes", "1")
            if (MATCH_ATTR[key] in attrs) != want:
                return False
        elif key == "matchdevicepath":
            # the only glob these files use
            if not re.match(val.replace("*", ".*") + "$", path):
                return False
        elif key == "matchproduct":
            # Xorg treats MatchProduct as a case-insensitive substring match.
            if val.lower() not in product.lower():
                return False
        elif key == "matchtag":
            if val not in tags:
                return False
    return True


def driver_for(sections, attrs, path="/dev/input/event7", product="", tags=()):
    """Replay xorg's rule: every matching section applies in order, and the
    LAST one that names a Driver is the one the device gets."""
    chosen = None
    for _ident, directives, driver in sections:
        if driver and section_matches(directives, attrs, path, product, tags):
            chosen = driver
    return chosen


SECTIONS = conf_files()
if SECTIONS is None:
    skip("X routing", "%s absent — build output/target to check this layer"
         % SYS_XCONF)
else:
    check("a pen tablet lands on libinput (pressure, tilt, eraser identity)",
          driver_for(SECTIONS, {"tablet"}) == "libinput",
          driver_for(SECTIONS, {"tablet"}))
    # The rule the OS deliberately keeps: an absolute pointer freezes under
    # libinput, and a tablet with no kernel driver arrives looking like one.
    check("a tagged absolute pointer lands on evdev (cursor-freeze fix)",
          driver_for(SECTIONS, {"pointer"},
                     tags={"notebook-absolute-pointer"}) == "evdev",
          driver_for(SECTIONS, {"pointer"},
                     tags={"notebook-absolute-pointer"}))
    check("an ordinary relative mouse stays on libinput",
          driver_for(SECTIONS, {"pointer"}) == "libinput",
          driver_for(SECTIONS, {"pointer"}))
    check("a touchscreen still lands on libinput (XI2 touch sequences)",
          driver_for(SECTIONS, {"touchscreen"}) == "libinput",
          driver_for(SECTIONS, {"touchscreen"}))
    check("a touchpad stays on libinput (scrolling, taps, palm rejection)",
          driver_for(SECTIONS, {"pointer", "touchpad"},
                     tags={"notebook-absolute-pointer"}) == "libinput",
          driver_for(SECTIONS, {"pointer", "touchpad"},
                     tags={"notebook-absolute-pointer"}))
    check("a keyboard still lands on libinput",
          driver_for(SECTIONS, {"keyboard"}) == "libinput",
          driver_for(SECTIONS, {"keyboard"}))
    check("the rule model honours MatchProduct",
          section_matches({"matchproduct": "QEMU USB Tablet"}, {"pointer"},
                          "/dev/input/event7", "QEMU USB Tablet")
          and not section_matches({"matchproduct": "QEMU USB Tablet"},
                                  {"pointer"}, "/dev/input/event7",
                                  "Definitely Not QEMU"))

    # Red-proof: a later section stealing tablets for evdev is precisely the
    # regression this check exists to catch, so it must be caught.
    hostile = SECTIONS + [("hostile", {"matchistablet": "on"}, "evdev")]
    mutant("a later section forcing tablets onto evdev",
           driver_for(hostile, {"tablet"}) == "libinput")
    # ...and the mirror: if the pointer rule ever broadened to swallow tablets.
    widened = [(i, d, drv) for (i, d, drv) in SECTIONS]
    widened.append(("widened pointer rule",
                    {"matchisjoystick": "off"}, "evdev"))
    mutant("a catch-all evdev rule with no device-class restriction",
           driver_for(widened, {"tablet"}) == "libinput")
    no_touchpad_override = [
        row for row in SECTIONS if "touchpad" not in row[0].lower()
    ]
    mutant("removing the touchpad libinput override",
           driver_for(no_touchpad_override, {"pointer", "touchpad"},
                      tags={"notebook-absolute-pointer"})
           == "libinput")


# ============================================== 3. the pressure -> width map
print("\n--- 3. pressure maps onto something the engine can express ------")

import illustrator                                            # noqa: E402

ps = illustrator.pen_size
SZ = 16
check("full pressure reaches the chosen size exactly",
      ps(SZ, 1.0) == SZ, ps(SZ, 1.0))
check("a firm press (0.85) already reaches it — a hand rarely hits 1.0",
      ps(SZ, 0.85) == SZ, ps(SZ, 0.85))
check("the lightest touch still marks 1 px, never 0 (no gap in the line)",
      ps(SZ, 0.0) == 1 and ps(SZ, 0.001) == 1, (ps(SZ, 0.0), ps(SZ, 0.001)))
check("half pressure is between the two, not at either end",
      1 < ps(SZ, 0.5) < SZ, ps(SZ, 0.5))
check("width never decreases as pressure rises",
      all(ps(SZ, p / 100.0) <= ps(SZ, (p + 1) / 100.0) for p in range(100)))
check("width never exceeds the chosen size at any pressure",
      all(ps(SZ, p / 100.0) <= SZ for p in range(101)))
check("a 1 px brush stays 1 px at every pressure",
      all(ps(1, p / 100.0) == 1 for p in range(101)))
check("out-of-range pressure is clamped, not extrapolated",
      ps(SZ, -5.0) == 1 and ps(SZ, 99.0) == SZ, (ps(SZ, -5.0), ps(SZ, 99.0)))
check("a missing / non-numeric reading falls back to the full size",
      ps(SZ, None) == SZ and ps(SZ, "x") == SZ and ps(SZ, float("nan")) == SZ)


def maps_are_sane(fn):
    return (fn(SZ, 1.0) == SZ and fn(SZ, 0.0) == 1
            and all(fn(SZ, p / 100.0) <= fn(SZ, (p + 1) / 100.0)
                    for p in range(100)))


mutant("a mapping that inverts pressure",
       maps_are_sane(lambda s, p: illustrator.pen_size(s, 1.0 - float(p))))
mutant("a mapping that lets light pressure paint nothing",
       maps_are_sane(lambda s, p: int(s * float(p))))


# =========================================== 4. the app paints what it maps
print("\n--- 4. Illustrator paints narrower when the pen presses lighter -")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk                                 # noqa: E402

INK = illustrator.px4("#1A1916")
WHITE = illustrator.px4("#FFFFFF")


class FakeDevice(object):
    def __init__(self, source):
        self._source = source

    def get_source(self):
        return self._source


class PenEv(object):
    """A GdkEvent as the canvas handlers read it. `pressure=None` models a
    device with no pressure axis at all, which is what a mouse is."""

    def __init__(self, x, y, pressure=None, source=Gdk.InputSource.PEN,
                 button=1, state=0):
        self.x = float(x)
        self.y = float(y)
        self.button = button
        self.state = state
        self._pressure = pressure
        self._source = source

    def get_source_device(self):
        return FakeDevice(self._source)

    def get_axis(self, use):
        if use != Gdk.AxisUse.PRESSURE or self._pressure is None:
            return (False, 0.0)
        return (True, float(self._pressure))


class MouseEv(object):
    """No axis API at all — an older event with nothing a tablet would set."""

    def __init__(self, x, y, button=1, state=0):
        self.x = float(x)
        self.y = float(y)
        self.button = button
        self.state = state


def app(w=48, h=48, tool="pencil", size=9):
    a = illustrator.Illustrator()
    a.cw, a.ch = w, h
    a.layers = [illustrator.Layer("Background", w, h, fill_white=True)]
    a.active = 0
    a.zoom = 1
    a.tool = tool
    a.size = size
    a.color = "#1A1916"
    a.sym_x = a.sym_y = False
    a.fill_shapes = False
    a._new_scratch()
    return a


def painted(a, px=INK, layer=0):
    surf = a.layers[layer].surface
    surf.flush()
    data = surf.get_data()
    stride = surf.get_stride()
    return {(x, y) for y in range(a.ch) for x in range(a.cw)
            if bytes(data[y * stride + x * 4:y * stride + x * 4 + 4]) == px}


def stroke_width(pressure, tool="pencil", size=9,
                 source=Gdk.InputSource.PEN):
    """Paint one horizontal stroke at a fixed pressure; return how many pixels
    tall the mark is — that is the brush width, read off the surface."""
    a = app(tool=tool, size=size)
    a._on_press(None, PenEv(8, 24, pressure, source))
    for x in range(9, 40):
        a._on_motion(None, PenEv(x, 24, pressure, source))
    a._on_release(None, PenEv(39, 24, pressure, source))
    hit = painted(a)
    if not hit:
        return 0
    return max(y for _x, y in hit) - min(y for _x, y in hit) + 1


light = stroke_width(0.10)
mid = stroke_width(0.45)
firm = stroke_width(1.0)
check("a firm press paints the full chosen width", firm == 9, firm)
check("a light press paints a NARROWER mark than a firm one",
      light < firm, (light, firm))
check("pressure is graded, not a two-state switch",
      light < mid < firm, (light, mid, firm))
check("the lightest press still leaves an unbroken mark", light >= 1, light)

# The regression that matters most: a mouse must be untouched by all of this.
a = app()
a._on_press(None, MouseEv(8, 24))
for x in range(9, 40):
    a._on_motion(None, MouseEv(x, 24))
a._on_release(None, MouseEv(39, 24))
hit = painted(a)
mouse_w = max(y for _x, y in hit) - min(y for _x, y in hit) + 1
check("a mouse still paints the chosen size (no pressure axis to read)",
      mouse_w == 9, mouse_w)

# A touchscreen reports pressure too. Scaling a fingertip by it would make
# touch strokes wander between widths for no reason the hand can see.
touch_w = stroke_width(0.10, source=Gdk.InputSource.TOUCHSCREEN)
check("a touchscreen is NOT pressure-scaled (source gates it, not the axis)",
      touch_w == 9, touch_w)

# The eraser end of the stylus erases whatever freehand tool is selected.
a = app(tool="pencil")
a._on_press(None, PenEv(8, 24, 1.0, Gdk.InputSource.PEN))
for x in range(9, 40):
    a._on_motion(None, PenEv(x, 24, 1.0, Gdk.InputSource.PEN))
a._on_release(None, PenEv(39, 24, 1.0, Gdk.InputSource.PEN))
drawn = len(painted(a))
a._on_press(None, PenEv(8, 24, 1.0, Gdk.InputSource.ERASER))
for x in range(9, 40):
    a._on_motion(None, PenEv(x, 24, 1.0, Gdk.InputSource.ERASER))
a._on_release(None, PenEv(39, 24, 1.0, Gdk.InputSource.ERASER))
left = len(painted(a))
check("flipping the pen over erases, with the pencil still selected",
      drawn > 0 and left == 0, (drawn, left))

# Lifting the pen drops pressure to ~0. If the release event's own reading were
# used, the last segment would taper to a hairline the hand never drew.
a = app()
a._on_press(None, PenEv(8, 24, 1.0))
a._on_motion(None, PenEv(20, 24, 1.0))
a._on_release(None, PenEv(39, 24, 0.0))     # the lift
hit = painted(a)
tail = {y for x, y in hit if x >= 30}
check("the lift does not taper the last segment to a hairline",
      len(tail) == 9, len(tail))

# Red-proof the whole family: if the size argument were threaded through the
# call chain but then ignored by the stamper, every check above must fail.
_real_stamp = illustrator.Illustrator._stamp_on


def _deaf_stamp(self, surf, pts, px, size=None):
    return _real_stamp(self, surf, pts, px, None)


illustrator.Illustrator._stamp_on = _deaf_stamp
try:
    m_light, m_firm = stroke_width(0.10), stroke_width(1.0)
finally:
    illustrator.Illustrator._stamp_on = _real_stamp
mutant("a stamper that ignores the size it is handed", m_light < m_firm)

_real_pen = illustrator.Illustrator._pen


def _deaf_pen(self, ev):
    return self.size, None


illustrator.Illustrator._pen = _deaf_pen
try:
    m_light, m_firm = stroke_width(0.10), stroke_width(1.0)
finally:
    illustrator.Illustrator._pen = _real_pen
mutant("handlers that never read the pressure axis", m_light < m_firm)


# ============================================================== the verdict
print("\n%d checks, %d passed, %d FAILED%s"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS),
         ", %d skipped" % len(SKIPS) if SKIPS else ""))
if FAILS:
    print("RESULT: FAILED")
    for f in FAILS:
        print("   - %s" % f)
else:
    print("RESULT: ALL PASS")
sys.exit(len(FAILS))
