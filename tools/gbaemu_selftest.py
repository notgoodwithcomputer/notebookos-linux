#!/usr/bin/env python3
"""
Headless selftest for the GBA Emulator (de/gbaemu.py) and, above all, for the
handoff from the GBA SDK to it: a game somebody just made must be findable and
playable, and a file that is not a game must be refused with something they can
act on.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/gbaemu_selftest.py

The two apps meet at exactly one place — the .gba file the SDK writes into the
user's Documents and the emulator's scan of the Home folder — and neither side
tests it. What this covers:

  * the library scan really finds a ROM anywhere under Home, including the
    Documents folder Compile & Export saves into, and ignores everything else;
  * a ROM the SDK actually compiled (built here with the real toolchain when
    one is reachable) is accepted;
  * empty, truncated and mistyped-name files are refused BEFORE vbam is
    launched, each with a sentence about the file rather than "the game closed
    right away — see the emulator log";
  * a missing emulator core, and a game that has been deleted since the
    library was drawn, both say so instead of doing nothing.

Nothing here launches vbam; the launch path is stubbed at the one function that
starts a process, so the suite is safe to run on the build host.
"""
import os
import sys
import json
import shutil
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

HOME = tempfile.mkdtemp(prefix="gbaemu-selftest-")
for d in (".config/notebook", "Documents", "Desktop", "Games/old",
          ".hidden-cache"):
    os.makedirs(os.path.join(HOME, d), exist_ok=True)
os.environ["NB_HOME"] = HOME

import nbapp                                               # noqa: E402
nbapp._APP_DIR = os.path.join(HOME, "nb-apps")             # see gbasdk_selftest
os.makedirs(nbapp._APP_DIR)

import nbgame                                              # noqa: E402
import gbaemu                                              # noqa: E402
import gbabuild                                            # noqa: E402
import gbasdk                                              # noqa: E402

OVERLAY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "buildroot", "board", "notebookos", "rootfs-overlay")
RUNTIME = os.path.join(OVERLAY, "opt", "notebook", "gbaruntime")
TOOLCHAIN = os.path.join(OVERLAY, "opt", "gba-toolchain")

RESULTS = []
FAILED = []


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append(ok)
    if not ok:
        FAILED.append(name)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   <- %s" % (detail,)))
    return ok


def section(title):
    print("\n--- %s" % title)


def pump(n=300):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def fake_rom(path, size=64 * 1024):
    """A file that looks like a real GBA cartridge, header and all."""
    d = bytearray(size)
    d[0:4] = b"\x2e\x00\x00\xea"                  # b start
    d[4:0xA0] = gbabuild.NINTENDO_LOGO
    with open(path, "wb") as fh:
        fh.write(d)
    return path


def fake_gb(path, size=32 * 1024):
    d = bytearray(size)
    d[0x104:0x114] = bytes.fromhex("ceed6666cc0d000b03730083000c000d")
    with open(path, "wb") as fh:
        fh.write(d)
    return path


def app():
    w = gbaemu.GbaEmu()
    pump()
    w._alerts = []
    w._launched = []
    w._alert = lambda heading, body: w._alerts.append((heading, body))
    return w


# =============================================================== the library
section("library: what the emulator finds")
real = fake_rom(os.path.join(HOME, "Documents", "My Game.gba"))
fake_rom(os.path.join(HOME, "Games", "old", "Deep.gba"))
fake_gb(os.path.join(HOME, "Desktop", "Classic.gb"))
fake_rom(os.path.join(HOME, ".hidden-cache", "Cached.gba"))
open(os.path.join(HOME, "Documents", "notes.txt"), "w").write("not a game")
open(os.path.join(HOME, "Documents", "mine.gbaproj"), "w").write("{}")

w = app()
names = sorted(m["name"] for m in w._roms)
check("a game exported into Documents is in the library", "My Game" in names, names)
check("a game in a sub-folder is found too", "Deep" in names, names)
check("a Game Boy ROM is found and named as one",
      any(m["name"] == "Classic" and m["system"] == "Game Boy" for m in w._roms),
      [(m["name"], m["system"]) for m in w._roms])
check("hidden folders are skipped", "Cached" not in names, names)
check("a text file is not offered as a game", "notes" not in names, names)
check("the SDK's project file is not offered as a game", "mine" not in names, names)
check("the library is sorted by name", names == sorted(names))
check("every card in the library points at a file that exists",
      all(os.path.isfile(m["path"]) for m in w._roms))

# a game exported while the emulator is open appears on Look for New Games
later = fake_rom(os.path.join(HOME, "Documents", "Just Made.gba"))
check("a game exported after the window opened is not there yet",
      "Just Made" not in [m["name"] for m in w._roms])
w._scan_roms()
w._render_library()
pump()
check("...and Look for New Games finds it",
      "Just Made" in [m["name"] for m in w._roms],
      [m["name"] for m in w._roms])
os.unlink(later)
w.destroy()

# ============================================================ refusing rubbish
section("a file that is not a game")
cases = [
    ("empty.gba", b"", "empty"),
    ("half.gba", b"\x2e\x00\x00\xea" + bytes(60), "far too small"),
    ("renamed.gba", b"This is a letter to my aunt.\n" * 400, "does not look like"),
    ("zeros.gba", bytes(64 * 1024), "does not look like"),
    ("empty.gb", b"", "empty"),
    ("renamed.gb", b"plain text " * 200, "does not look like"),
]
for name, blob, want in cases:
    p = os.path.join(HOME, "Documents", name)
    with open(p, "wb") as fh:
        fh.write(blob)
    why = gbaemu.rom_problem(p)
    check("%-12s is refused" % name, bool(why), "it would be handed to vbam")
    check("  ...and the reason says '%s'" % want, why and want in why, why)
    check("  ...in a sentence, not a code", why and why.endswith("."), why)

good = fake_rom(os.path.join(HOME, "Documents", "Good.gba"))
check("a real GBA cartridge is accepted", gbaemu.rom_problem(good) is None,
      gbaemu.rom_problem(good))
# homebrew that never had the logo written still boots in an emulator
nologo = os.path.join(HOME, "Documents", "Homebrew.gba")
d = bytearray(8192)
d[0:4] = b"\x2e\x00\x00\xea"
open(nologo, "wb").write(bytes(d))
check("homebrew with no boot logo but a real entry point is still accepted",
      gbaemu.rom_problem(nologo) is None, gbaemu.rom_problem(nologo))
check("a real Game Boy cartridge is accepted",
      gbaemu.rom_problem(fake_gb(os.path.join(HOME, "Documents", "Ok.gb"))) is None)
check("a .zip is left for the emulator to judge",
      gbaemu.rom_problem(os.path.join(HOME, "Documents", "pack.zip")) is None)
check("a file that is not there at all is reported, not crashed on",
      isinstance(gbaemu.rom_problem(os.path.join(HOME, "nope.gba")), str))

# =============================================================== the launch
section("launching")
launched = []
nbgame.GameSession = lambda *a, **k: type(
    "S", (), {"run": lambda s: launched.append(a[2]),
              "stop": lambda s: None, "_finish": lambda s: None})()
w = app()
w._vbam_path = lambda: "/usr/bin/true"
w._play(good)
check("a real game is handed to the emulator", launched == [good], launched)
check("...with no complaint shown", not w._alerts, w._alerts)
launched[:] = []
w._session = None
w._play(os.path.join(HOME, "Documents", "empty.gba"))
check("a broken file never reaches the emulator", launched == [], launched)
check("...and the player is told why, in a card",
      len(w._alerts) == 1 and "empty" in w._alerts[0][1], w._alerts)
w._alerts[:] = []
w._play(os.path.join(HOME, "Documents", "gone-away.gba"))
check("a game deleted since the library was drawn says so",
      len(w._alerts) == 1 and "moved or deleted" in w._alerts[0][1], w._alerts)
w._alerts[:] = []
w._vbam_path = lambda: None
w._play(good)
check("with no emulator core installed, nothing is launched and it says so",
      launched == [] and (w._alerts or "installed" in (w._ctrl_label.get_text() or "")),
      (launched, w._alerts, w._ctrl_label.get_text()))
w.destroy()

# an unplayable library still draws, with its warning
w = app()
w._vbam_path = lambda: None
w._render_library()
pump()
check("a library with no emulator core still lists the games",
      len(w._roms) > 0)
w.destroy()

# ================================================== the SDK -> emulator handoff
section("the handoff from the GBA SDK")
gcc = gbabuild.find_gcc(TOOLCHAIN)
if not gcc:
    print("SKIP  no arm-none-eabi-gcc under %s — cannot compile a real ROM"
          % TOOLCHAIN)
else:
    outdir = os.path.join(HOME, "build")
    ok, gba, log = gbabuild.build_rom(gbasdk.GbaSdk._example_project(None),
                                      outdir, runtime_dir=RUNTIME,
                                      toolchain_dir=TOOLCHAIN)
    check("the SDK's example game compiles", ok, log[-800:])
    if ok:
        # exactly what Compile & Export does with it
        dest = os.path.join(HOME, "Documents", "Example.gba")
        shutil.copyfile(gba, dest)
        check("a game the SDK really built is accepted by the emulator",
              gbaemu.rom_problem(dest) is None, gbaemu.rom_problem(dest))
        w = app()
        check("...and appears in the library by the name it was saved under",
              "Example" in [m["name"] for m in w._roms],
              [m["name"] for m in w._roms])
        check("...listed as a Game Boy Advance game",
              any(m["name"] == "Example" and m["system"] == "Game Boy Advance"
                  for m in w._roms))
        w.destroy()
        # the half-copied stick: the failure this whole check exists for
        part = os.path.join(HOME, "Documents", "Half Copied.gba")
        with open(dest, "rb") as fh, open(part, "wb") as out:
            out.write(fh.read(64))
        check("a game pulled off a USB stick half-way through is refused",
              gbaemu.rom_problem(part) is not None)

# gbaemu keeps NO settings file. It had two keys and neither could act: a game
# always runs fullscreen (nbgame must reparent vbam into a fullscreen app window
# or the single-app WM unmaps it), and `scale` had no control at all. Both were
# removed, so what is asserted now is the removal itself — and the upgrade path,
# because a machine coming from an older build still has the file on disk.
section("settings")
cfg = os.path.join(HOME, ".config", "notebook", "gbaemu.json")
check("the app keeps no settings of its own",
      not hasattr(gbaemu.GbaEmu, "_load_settings")
      and not hasattr(gbaemu.GbaEmu, "_save_settings"))
# Comments stripped before matching. The word survives in the comment that
# RECORDS the removal, and a check for the feature that trips on the note
# saying the feature is gone reports the documentation, not the code. (Exactly
# what had music_transport_accessibility_selftest sitting red on a fixed
# defect — and I wrote this line an hour after fixing that one.)
_src = "\n".join(l for l in open(gbaemu.__file__, encoding="utf-8").read()
                 .splitlines() if not l.strip().startswith("#"))
check("and offers no Fullscreen toggle to write them with",
      "Fullscreen" not in _src)

# left over from a previous build: must be ignored, not read, not rewritten
os.makedirs(os.path.dirname(cfg), exist_ok=True)
with open(cfg, "w") as fh:
    json.dump({"fullscreen": False, "scale": 4}, fh)
before = open(cfg).read()
w = app()
w.destroy()
check("a settings file left by an older build does not stop the app opening",
      True)
check("...and is not rewritten on the way out", open(cfg).read() == before)

# and a corrupt one is equally uninteresting, because nothing reads it
open(cfg, "w").write("{ not json at all")
w = app()
w.destroy()
check("a damaged leftover settings file is simply ignored", True)

print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
if FAILED:
    print("\nFAILED:")
    for n in FAILED:
        print("  - " + n)
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(0 if all(RESULTS) else 1)
