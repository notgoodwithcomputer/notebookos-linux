#!/usr/bin/env python3
"""
gba_integration_selftest — one project that uses everything, built end to end.

Run as:  python3 tools/gba_integration_selftest.py

WHY THIS EXISTS
---------------
Every subsystem in this SDK has its own checks, and every one of them passes
against a project built to exercise that subsystem alone. None of them would
notice a feature that works by itself and breaks the moment it shares a
cartridge with another: two subsystems both taking timer 1, two generated
arrays colliding on a name, a struct field added in one place and read in
another, an emitted symbol that only links when some other feature happens to
be absent.

So this builds ONE project that uses all of it -- every resource kind, every
runtime subsystem, the sheet actions and hand-written C in both languages -- and
requires a clean compile, no reported problems, and a valid cartridge at the end.

It also boots a small cartridge in VBA-M when an inspectable host emulator is
available.  That last stage checks the hardware OAM image, not merely generated
code or the runtime's shadow copy.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/"
                        "opt/notebook/de")
RT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/"
                        "opt/notebook/gbaruntime")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="gbaint-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gbabuild                                            # noqa: E402

FAIL = []


def ok(cond, label, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def find_gcc():
    for base, _dirs, files in os.walk(os.path.join(ROOT, "vendor-dl")):
        if "arm-none-eabi-gcc" in files:
            return os.path.join(base, "arm-none-eabi-gcc")
    return None


def find_arm_tool(name, gcc=None):
    if gcc:
        beside = os.path.join(os.path.dirname(gcc), name)
        if os.path.isfile(beside):
            return beside
    for base, _dirs, files in os.walk(os.path.join(ROOT, "vendor-dl")):
        if name in files:
            return os.path.join(base, name)
    return None


def find_vbam():
    override = os.environ.get("NB_GBA_VBAM")
    candidates = ([override] if override else []) + [
        os.path.join(ROOT, "buildroot/output/build/vbam-2.1.4/vbam"),
        os.path.join(ROOT, "buildroot/output/target/usr/bin/vbam"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def execution_project():
    pixels = [0x7FFF] * 64
    return {
        "name": "OAM execution probe",
        "sprites": [{"id": "spr", "name": "Sprite", "w": 8, "h": 8,
                     "ox": 0, "oy": 0, "anim_speed": 0,
                     "frames": [pixels]}],
        "tilesets": [], "sounds": [], "tables": [], "scripts": [],
        "objects": [{"id": "obj", "name": "Object", "sprite": "spr",
                     "visible": True, "solid": False, "depth": 0,
                     "events": []}],
        "rooms": [{"id": "room", "name": "Room", "w": 240, "h": 160,
                   "speed": 60, "bg": "#102030", "tiles": None,
                   "far": None,
                   "instances": [{"object": "obj", "x": 40, "y": 40}]}],
        "start_room": "room",
    }


def execute_oam_check(gcc):
    """Return (result, detail): result is True, False, or None for SKIP."""
    probe = os.environ.get("NB_GBA_VBAM_PROBE")
    vbam = probe or find_vbam()
    gdb = "/usr/bin/gdb"
    if not vbam:
        return None, "no host VBA-M executable was found"
    if not probe and not os.path.isfile(gdb):
        return None, "/usr/bin/gdb is unavailable for emulator state inspection"
    # A stripped frontend can run a ROM, but cannot expose VBA-M's emulated OAM.
    nm = find_arm_tool("arm-none-eabi-nm", gcc)
    if not nm:
        return None, "arm-none-eabi-nm is unavailable for fresh ELF symbols"
    host_nm = subprocess.run(["nm", vbam], capture_output=True, text=True)
    if host_nm.returncode or not re.search(r"\b(oam|internalRAM)$", host_nm.stdout,
                                           re.M):
        return None, "host VBA-M is stripped; its OAM globals are not inspectable"

    outdir = tempfile.mkdtemp(prefix="gbaint-exec-")
    built, rom, log = gbabuild.build_rom(execution_project(), outdir,
                                         runtime_dir=RT,
                                         toolchain_dir="/nonexistent")
    if not built:
        return False, "execution cartridge did not build: " + (log or "")[-300:]
    elf = os.path.join(outdir, "game.elf")
    syms = subprocess.run([nm, "-n", elf], capture_output=True, text=True)
    match = re.search(r"^([0-9a-fA-F]+)\s+\w\s+g_oam$", syms.stdout, re.M)
    if not match:
        return False, "fresh ELF has no g_oam symbol"
    shadow_off = int(match.group(1), 16) - 0x03000000
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    if probe:
        env["NB_G_OAM_OFFSET"] = str(shadow_off)
        try:
            run = subprocess.run([probe, "--no-opengl", rom], capture_output=True,
                                 text=True, env=env, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, "VBA-M probe could not run: %s" % exc
        output = run.stdout + run.stderr
        state = re.search(r"NBPROBE .*hw_oam=([0-9a-f]+),([0-9a-f]+),"
                          r"([0-9a-f]+).*shadow_oam=([0-9a-f]+)", output, re.I)
        if not state:
            return None, "VBA-M probe produced no frame state: " + output[-240:]
        hw0, hw1, hw2, shadow0 = (int(v, 16) for v in state.groups())
        visible = (hw0 & 0x0300) != 0x0200
        detail = "hardware OAM=%04x,%04x,%04x; shadow attr0=%04x" % (
            hw0, hw1, hw2, shadow0)
        return visible, detail
    commands = [
        "set pagination off",
        "set confirm off",
        "set args --no-opengl " + rom,
        "break systemDrawScreen()",
        "ignore 1 10",
        "run",
        "set $op = *(unsigned char **)&oam",
        "set $ip = *(unsigned char **)&internalRAM",
        ('printf "NBEXEC hw=%%04x,%%04x,%%04x shadow=%%04x\\n", '
         '*(unsigned short*)$op, *(unsigned short*)($op+2), '
         '*(unsigned short*)($op+4), *(unsigned short*)($ip+%d)' % shadow_off),
        "kill",
    ]
    cmd = [gdb, "-q", "-nx", "-batch", vbam]
    for command in commands:
        cmd += ["-ex", command]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, env=env,
                             timeout=20)
    except subprocess.TimeoutExpired:
        return None, "VBA-M state inspection timed out"
    output = run.stdout + run.stderr
    if "ptrace: Operation not permitted" in output or \
            "Could not trace the inferior process" in output:
        return None, "sandbox forbids ptrace; VBA-M state could not be inspected"
    state = re.search(r"NBEXEC hw=([0-9a-f]+),([0-9a-f]+),([0-9a-f]+) "
                      r"shadow=([0-9a-f]+)", output, re.I)
    if not state:
        return None, "VBA-M produced no inspectable frame state: " + output[-240:]
    hw0, hw1, hw2, shadow0 = (int(v, 16) for v in state.groups())
    visible = (hw0 & 0x0300) != 0x0200
    detail = "hardware OAM=%04x,%04x,%04x; shadow attr0=%04x" % (
        hw0, hw1, hw2, shadow0)
    return visible, detail


# --------------------------------------------------------------------------
# One project that touches every phase.
# --------------------------------------------------------------------------
def kitchen_sink():
    px16 = [0x001F if (i // 4 + i % 4) % 2 else 0x03E0 for i in range(256)]
    tile8 = [0x7C00 if i % 3 else 0x0000 for i in range(64)]

    # A script exercising the runtime directly: interrupts, timers, DMA, the
    # affine matrix, windows, blending, mosaic, the clock, the link cable, the
    # profiler and a lookup table, all in one translation unit.
    script = """
static u16 wave[160];
static s32 spin;
static volatile s32 ticks;

static void on_tick(void) { ticks++; }

void hw_setup(void)
{
    int y;
    rt_prof(1);
    rt_vwf(1);
    rt_irq_set(IRQ_VBLANK, 0);
    rt_timer_start(0, 273, TM_FREQ_1024 | TM_IRQ);
    rt_irq_set(IRQ_TIMER0, on_tick);
    for (y = 0; y < 160; y++)
        wave[y] = (u16)((rt_sin8((y * 4) & 255) * 6) >> 8);
    rt_hdma_start(0, (void *)&REG_BG0HOFS, wave, 1);
    rt_dma(3, (void *)0x06004000, wave, 80, DMA_32);
    rt_window(0, 40, 40, 96, 96, WIN_ALL, WIN_ALL | WIN_BLEND);
    rt_blend_alpha(BLD_A_OBJ, BLD_B_BG0, 10, 8);
    rt_mosaic(1, 1, 2, 2);
    rt_link_open(SIO_9600);
    rt_say_voice(0);
}

void hw_frame(void)
{
    nb_DateTime now;
    rt_prof_begin(4);
    spin = (spin + 2) & 255;
    rt_bg_affine(2, 120, 80, 120, 80, spin, 256);
    rt_obj_affine(0, spin, 384);
    if (rt_link_ready()) {
        rt_link_send((u16)ticks);
        if (rt_link_parent()) rt_link_start();
        rt_link_poll();
    }
    if (!rt_rtc_read(&now)) { /* no clock in this cartridge */ }
    rt_prof_end(4);
}

s32 price_of(int i)
{
    if (i < 0 || i >= nb_items_count) return 0;
    return nb_items[i].Price;
}

void show_cost(void)
{
    rt_draw_int_pad(1, 1, price_of(0), 5, 0);
    rt_prof_overlay();
}
"""
    return {
        "name": "Everything",
        "sprites": [
            {"id": "spr_hero", "name": "Hero", "w": 16, "h": 16, "ox": 8,
             "oy": 8, "anim_speed": 4, "frames": [px16, px16]},
            {"id": "spr_coin", "name": "Coin", "w": 16, "h": 16, "ox": 8,
             "oy": 8, "anim_speed": 0, "frames": [px16], "pal_bank": 3},
        ],
        "tilesets": [
            {"id": "ts_world", "name": "World", "size": 8,
             "solid": [False, True] + [False] * 14,
             "auto_base": 0,
             "tiles": [tile8 for _ in range(16)]},
        ],
        "sounds": [
            {"id": "snd_song", "name": "Song", "tempo": 6, "loop": True,
             "steps": 8, "lead": [60, 0, 64, 0, 67, 0, 64, 0],
             "bass": [36] * 8, "drum": [1, 0, 2, 0, 1, 0, 2, 4],
             "kind": 0, "duty": 2, "vol": 0, "decay": 0, "prio": 0},
            {"id": "snd_voice", "name": "Voice", "tempo": 8, "loop": False,
             "steps": 4, "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
             "kind": 1, "duty": 0, "vol": 12, "decay": 3, "prio": 5,
             "pcm": [((i * 7) % 200) - 100 for i in range(2048)]},
        ],
        "tables": [
            {"id": "items", "columns": [{"name": "Name", "type": "text"},
                                        {"name": "Price", "type": "int"},
                                        {"name": "Rare", "type": "bool"}],
             "rows": [["Potion", 300, False], ['Say "hi"', 700, True],
                      ["Elixir", 1200, True]]},
        ],
        "scripts": [{"id": "scr_hw", "name": "Hardware", "code": script}],
        "objects": [
            {"id": "obj_hero", "name": "Hero", "sprite": "spr_hero",
             "visible": True, "solid": False, "tilecol": 1, "depth": 0,
             "bb_inset": 2, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "hw_setup();"},
                     {"kind": "input_lock", "on": "on"},
                     {"kind": "glide", "x": 120, "y": 80, "frames": 45},
                     {"kind": "menu", "a": "Fight", "b": "Bag", "c": "Run",
                      "d": "", "var": "choice"},
                     {"kind": "set_alarm", "alarm": "0", "steps": 60}]},
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "hw_frame();"},
                     {"kind": "execute_code",
                      "code": "x = x; score += 0;"},
                     {"kind": "if_var", "var": "choice", "op": "==",
                      "value": 0, "children": [
                          {"kind": "say",
                           "text": "The gate opens.{p}{s:0}Coins {v:0}.\\n"
                                   "{c:3}Onward."},
                          {"kind": "play_sound", "sound": "sfx:coin"}]},
                     {"kind": "draw_number", "value": 1, "x": 1, "y": 3}]},
                 {"type": "alarm", "alarm": 0, "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "show_cost();"},
                     {"kind": "input_lock", "on": "off"},
                     {"kind": "play_sound", "sound": "snd_song"}]},
                 {"type": "collision", "object": "obj_coin", "actions": [
                     {"kind": "play_sound", "sound": "snd_voice"},
                     {"kind": "add_score", "value": 10}]}]},
            {"id": "obj_coin", "name": "Coin", "sprite": "spr_coin",
             "visible": True, "solid": False, "tilecol": 0, "depth": 1,
             "bb_inset": 0, "events": [
                 {"type": "step", "actions": [
                     {"kind": "wrap"}]}]},
        ],
        "rooms": [
            {"id": "rm_town", "name": "Town", "w": 480, "h": 320,
             "speed": 60, "bg": "#0C2818",
             "tiles": [1] * ((480 // 8) * (320 // 8)),
             "far": [2] * 1024, "far_div": 3, "edge_open": False,
             "warps": [{"x": 8, "y": 8, "w": 16, "h": 16, "room": "rm_cave",
                        "tx": 120, "ty": 140}],
             "instances": [{"object": "obj_hero", "x": 64, "y": 64},
                           {"object": "obj_coin", "x": 128, "y": 96}]},
            {"id": "rm_cave", "name": "Cave", "w": 240, "h": 160,
             "speed": 60, "bg": "#080810", "tiles": None,
             "far": None, "far_div": 2, "edge_open": True,
             "warps": [{"x": 112, "y": 152, "w": 16, "h": 8,
                        "room": "rm_town", "tx": 16, "ty": 16}],
             "instances": [{"object": "obj_hero", "x": 120, "y": 140}]},
        ],
        "start_room": "rm_town",
    }


print("\n== one project that uses everything ==")
proj = kitchen_sink()

# --- inline C must be able to NAME what the editors made ---------------------
# Actions resolve an object to its index and emit the bare number. Inline C had
# no way to do that, so bespoke behaviour -- the whole point of Execute Code --
# had to hard-code an index that silently repoints when objects are reordered.
# The two halves each worked and did not compose. These constants are the join,
# so the invariant that matters is that they agree with the array they index.
_c_names = gbabuild.generate_c(kitchen_sink())
_defs = dict((m.group(1), int(m.group(2))) for m in
             re.finditer(r"#define\s+(NB_(?:OBJ|SPR|SND|ROOM)_\w+)\s+(\d+)",
                         _c_names))
ok(len(_defs) >= 7, "the generator names objects, sprites, sounds and rooms",
   "%d constants: %s" % (len(_defs), sorted(_defs)[:6]))
_p = kitchen_sink()
_bad = []
for _pre, _key in (("NB_OBJ", "objects"), ("NB_SPR", "sprites"),
                   ("NB_SND", "sounds"), ("NB_ROOM", "rooms")):
    for _i, _it in enumerate(_p[_key]):
        _want = [k for k, v in _defs.items()
                 if k.startswith(_pre + "_") and v == _i]
        if not _want:
            _bad.append("%s index %d unnamed" % (_key, _i))
ok(not _bad, "every one of them is named at its own index",
   "; ".join(_bad[:4]))
# Emitted before any user script, or a script naming one will not compile.
ok(_c_names.index("#define NB_OBJ") < _c_names.index("hw_setup"),
   "the names are in scope from the first line of authored code",
   "a script appears before the constants it may use")

problems = gbabuild.check_project(proj)
# A problem is not a build failure, which is exactly why it has to be asserted:
# the ROM would come out fine and quietly do less than the project says.
ok(not problems, "a project using every feature reports no problems",
   "; ".join(problems[:4]))

bad_colour = kitchen_sink()
bad_colour["rooms"][0]["bg"] = "#GGGGGG"
colour_problems = gbabuild.check_project(bad_colour)
ok(any("background colour" in problem and "#RRGGBB" in problem
       for problem in colour_problems),
   "a malformed room colour is reported before build",
   "; ".join(colour_problems[:4]))
ok(gbabuild._rgb15("#GGGGGG", 0x1234) == 0x1234,
   "malformed hexadecimal colour safely uses its fallback")

c = gbabuild.generate_c(proj)
for name, needle in (
        ("sprites", "nb_Sprite nb_sprites[]"),
        ("background tiles", "nb_bg_tiles[]"),
        ("solid tiles", "nb_tile_solid[]"),
        ("sounds", "nb_Sound nb_sounds[]"),
        ("a drum track", "snd_drum_"),
        ("a sample", "snd_pcm_"),
        ("a data table", "nb_row_"),
        ("a script", "hw_setup"),
        ("menu lines", "nb_menu_"),
        ("objects", "nb_Object nb_objects[]"),
        ("rooms", "nb_Room nb_rooms[]"),
        ("warps", "nb_Warp"),
        ("a parallax layer", "_far[]"),
        ("dialogue", "rt_say("),
        ("a glide", "rt_glide("),
        ("an input lock", "rt_input_lock("),
        ("a built-in effect", "rt_sfx(")):
    ok(needle in c, "the C carries %s" % name)

# Names are generated from author text in four different places -- tables,
# scripts, menus, sprites. A collision between any two is a compile error in
# generated code, which names a line nobody wrote.
_defs = re.findall(r"^(?:static\s+)?const\s+[\w *]*?\b(nb_[A-Za-z0-9_]+)\[\]",
                   c, re.M)
_dupes = sorted({n for n in _defs if _defs.count(n) > 1})
ok(not _dupes, "no two generated arrays share a name", str(_dupes))
ok(len(_defs) > 8, "...and there are plenty of them to collide",
   str(len(_defs)))

print("\n== and it compiles ==")
gcc = find_gcc()
if not gcc:
    print("  SKIP (no arm-none-eabi-gcc in vendor-dl)")
else:
    os.environ["PATH"] = os.path.dirname(gcc) + os.pathsep + os.environ["PATH"]
    outdir = tempfile.mkdtemp(prefix="gbaint-build-")
    built, rom, log = gbabuild.build_rom(proj, outdir, runtime_dir=RT,
                                         toolchain_dir="/nonexistent")
    ok(built, "the whole thing builds", (log or "")[-400:])
    if built:
        # A warning here is a real signal: this project is the only one that
        # puts every subsystem through the compiler at once.
        ok("warning" not in (log or "").lower(),
           "...with no compiler warnings",
           "\n".join(l for l in (log or "").split("\n")
                     if "warning" in l.lower())[:300])
        b = open(rom, "rb").read()
        ck = (-(sum(b[0xA0:0xBD]) + 0x19)) & 0xFF
        ok(ck == b[0xBD], "...to a cartridge a console will boot")
        ok(b[3] == 0xEA, "...with a branch at the entry point")
        ok(b[0xB2] == 0x96, "...and the fixed header byte")
        ok(b"SRAM_V" in b, "...carrying the save signature emulators look for")
        ok(b'Say "hi"' in b or b"Say " in b,
           "...and the project's own data")
        print("     ROM %d bytes" % len(b))

        # The multiboot path shares the generator and a different linker
        # script; a project this size is where the 256 KB limit gets tested.
        mok, mb, mlog = gbabuild.build_rom(proj, outdir, runtime_dir=RT,
                                           toolchain_dir="/nonexistent",
                                           multiboot=True)
        ok(mok, "...and as a link-cable image", (mlog or "")[-300:])

    print("\n== and it executes ==")
    executed, detail = execute_oam_check(gcc)
    if executed is None:
        print("  SKIP hardware OAM execution check  -- " + detail)
    else:
        ok(executed, "a rendered sprite reaches hardware OAM", detail)

print("\n%s  (%d failed)" % ("FAILURES: " + ", ".join(FAIL) if FAIL
                             else "all checks pass", len(FAIL)))
sys.exit(1 if FAIL else 0)
