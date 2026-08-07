#!/usr/bin/env python3
"""gba_fixtures — composition slices that are BUILT, EXECUTED, and ASSERTED.

The suites verify the SDK's parts; these verify that the parts compose into a
game, on the only vantage that can see it: a running frame. Each fixture is a
whole project put through the real generator and the real ARM toolchain, run
headlessly on the vendored VBA-M core, and judged on emulated hardware state
and pixels — because "it builds" was green for as long as every ROM hung at
its first VBlankIntrWait.

    python3 tools/gba_fixtures.py            build + run + assert every slice
    python3 tools/gba_fixtures.py bullet     one slice by name

Fixtures are importable (`from gba_fixtures import bullet_hell`) so other
suites can reuse them instead of growing private copies that drift.
"""
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
RT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/gbaruntime")
sys.path.insert(0, DE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gbabuild                                            # noqa: E402
from gba_run import find_toolchain_dir, run_rom            # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _soul():
    return [0x7C1F if (i % 16) in (7, 8) or (i // 16) in (7, 8) else 0
            for i in range(256)]


def bullet_hell(nbullets_room=1):
    """The Undertale movement slice: a pool-saturating ring pattern.

    Exercises: inline C naming generated objects (NB_OBJ_*), rt_create under
    pool pressure, sub-pixel velocities, collision events, the profiler
    overlay, and 128 simultaneous hardware sprites."""
    bone = [0x7FFF if (i // 8) % 4 else 0 for i in range(64)]
    script = r"""
static s32 wave_t;

void fire_ring(s32 n, s32 speed)
{
    s32 i;
    for (i = 0; i < n; i++) {
        Instance* b = rt_create(NB_OBJ_BONE, 120, 60);
        if (!b) break;
        rt_set_speed_dir(b, (i * 256) / n, speed);
    }
}

void encounter_step(void)
{
    wave_t++;
    if ((wave_t & 7) == 0) fire_ring(16, 64);
    rt_prof_overlay();
}
"""
    return {
        "name": "Encounter",
        "sprites": [
            {"id": "spr_soul", "name": "Soul", "w": 16, "h": 16, "ox": 8,
             "oy": 8, "anim_speed": 0, "frames": [_soul()]},
            {"id": "spr_bone", "name": "Bone", "w": 8, "h": 8, "ox": 4,
             "oy": 4, "anim_speed": 0, "frames": [bone], "pal_bank": 2},
        ],
        "tilesets": [], "tables": [],
        "sounds": [{"id": "snd_hit", "name": "Hit", "tempo": 8, "loop": False,
                    "steps": 4, "lead": [0] * 4, "bass": [0] * 4,
                    "drum": [1, 0, 0, 0], "kind": 1, "duty": 0, "vol": 10,
                    "decay": 4, "prio": 5}],
        "scripts": [{"id": "scr", "name": "Encounter", "code": script}],
        "objects": [
            {"id": "obj_soul", "name": "Soul", "sprite": "spr_soul",
             "visible": True, "solid": False, "tilecol": 0, "depth": 0,
             "bb_inset": 4, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "nb_health = 20; rt_prof(1);"}]},
                 {"type": "step", "actions": [
                     {"kind": "move_keys", "speed": 2},
                     {"kind": "execute_code", "lang": "C",
                      "code": "encounter_step();"}]},
                 {"type": "collision", "object": "obj_bone", "actions": [
                     {"kind": "play_sound", "sound": "snd_hit"},
                     {"kind": "add_health", "value": -1}]}]},
            {"id": "obj_bone", "name": "Bone", "sprite": "spr_bone",
             "visible": True, "solid": False, "tilecol": 0, "depth": 1,
             "bb_inset": 1, "events": [
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "if (self->x < -8 || self->x > 248 || "
                              "self->y < -8 || self->y > 168) "
                              "rt_destroy(self);"}]}]},
        ],
        "rooms": [{"id": "rm", "name": "Fight", "w": 240, "h": 160,
                   "speed": 60, "bg": "#2040A0", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_soul", "x": 120, "y": 110}]
                   + [{"object": "obj_bone", "x": 8 + (i % 28) * 8,
                       "y": 8 + (i // 28) * 8} for i in range(nbullets_room)]}],
        "start_room": "rm",
    }


def dialogue_scene():
    """The Undertale text slice: typewriter dialogue with control codes.

    Exercises: the say action ({p} pause, {s:n} sound, {v:n} variable,
    {c:n} colour), the dialogue panel over a sprite scene, a voice blip
    sound, and input lock during a scripted line. The words are chosen long
    enough that the typewriter is still printing at capture time."""
    return {
        "name": "Scene",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [_soul()]}],
        "tilesets": [], "tables": [],
        "sounds": [{"id": "snd_blip", "name": "Blip", "tempo": 8,
                    "loop": False, "steps": 2, "lead": [72, 0], "bass": [0, 0],
                    "drum": [0, 0], "kind": 0, "duty": 1, "vol": 8,
                    "decay": 2, "prio": 4}],
        "scripts": [],
        "objects": [{"id": "obj_n", "name": "Narrator", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "set_var", "var": "gold", "value": 42},
                             {"kind": "say_voice", "sound": "snd_blip"},
                             {"kind": "say",
                              "text": "The corridor holds its breath.{p}"
                                      "You are carrying {v:0} gold.{p}"
                                      "{c:3}Something moves behind the "
                                      "pillars, slowly, without hurry."}]}]}],
        "rooms": [{"id": "rm", "name": "Hall", "w": 240, "h": 160,
                   "speed": 60, "bg": "#183018", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_n", "x": 120, "y": 40}]}],
        "start_room": "rm",
    }


# ---------------------------------------------------------------------------
# pixel evidence
# ---------------------------------------------------------------------------
def decode_png(path):
    """(w, h, rows-of-RGB-bytes) for the 8-bit RGB PNGs vbam writes."""
    d = open(path, "rb").read()
    i, idat, w, h = 8, b"", 0, 0
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        typ = d[i + 4:i + 8]
        if typ == b"IHDR":
            w, h = struct.unpack(">II", d[i + 8:i + 16])
        elif typ == b"IDAT":
            idat += d[i + 8:i + 8 + ln]
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 3
    rows, prev, pos = [], bytearray(stride), 0
    for _y in range(h):
        f = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if f == 1:
            for x in range(3, stride):
                line[x] = (line[x] + line[x - 3]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - 3] if x >= 3 else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - 3] if x >= 3 else 0
                b = prev[x]
                c = prev[x - 3] if x >= 3 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc
                                      else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, rows


def region_activity(png, y0, y1):
    """Fraction of pixels in rows y0..y1 that are not the region's own
    dominant colour — how much something DREW there, independent of what the
    backdrop happens to be."""
    w, h, rows = decode_png(png)
    from collections import Counter
    c = Counter()
    for y in range(max(0, y0), min(h, y1)):
        r = rows[y]
        for x in range(0, w * 3, 3):
            c[r[x:x + 3]] += 1
    total = sum(c.values())
    return 1.0 - (c.most_common(1)[0][1] / float(total)) if total else 0.0


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------
def _run(name, model, seconds, check):
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    shot = os.path.join(out, "frame.png")
    rep = run_rom(rom, seconds, shot=shot)
    rep["shot_path"] = shot
    err = check(rep)
    if err:
        print("FAIL %s: %s  (report: visible=%s dispcnt=%s)"
              % (name, err, rep.get("visible"), rep.get("dispcnt")))
        return False
    print("PASS %s  visible=%d  %s" % (name, rep["visible"], shot))
    return True


def check_bullet(rep):
    # After 4s the ring pattern has long since saturated the pool: the frame
    # must hold many hardware sprites, not merely one.
    if rep["visible"] < 32:
        return "expected a saturated sprite pool, saw %d" % rep["visible"]
    if not os.path.exists(rep.get("shot_path", "")):
        return "no frame captured"
    # The overlay's two text rows live at the top; bullets everywhere. The top
    # sixteen rows must show drawing beyond the backdrop.
    if region_activity(rep["shot_path"], 0, 16) < 0.02:
        return "profiler overlay not visible in the frame"
    return None


def check_dialogue(rep):
    if rep["visible"] < 1:
        return "the scene sprite is not on screen"
    if not os.path.exists(rep.get("shot_path", "")):
        return "no frame captured"
    # The dialogue panel occupies the lower rows; the typewriter must have
    # drawn there by capture time. One-in-twenty pixels differing from the
    # panel's own dominant colour is glyphs, not noise.
    if region_activity(rep["shot_path"], 108, 160) < 0.05:
        return "no dialogue panel activity in the lower rows"
    return None




def persist_scene():
    """The save slice: state that must survive a power cycle.

    First boot: no save exists, so the game sets a score, saves, and records
    -1 in a probe global. Second boot, same battery file: the load must
    succeed and the probe must hold the restored score. This exercises the
    SRAM magic, the WAITCNT save timing, the emulator's save-type detection
    (driven by the SRAM_V113 signature the runtime embeds), and the load path
    on the data-safety side of the ledger."""
    return {
        "name": "Persist",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": [],
        "scripts": [{"id": "scr", "name": "Probe",
                     "code": "volatile s32 probe_loaded;\n"}],
        "objects": [{"id": "obj_p", "name": "Keeper", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "if (rt_game_load()) { "
                                      "probe_loaded = nb_score; } else { "
                                      "nb_score = 1234; rt_game_save(); "
                                      "probe_loaded = -1; }"}]}]}],
        "rooms": [{"id": "rm", "name": "Vault", "w": 240, "h": 160,
                   "speed": 60, "bg": "#302010", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_p", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def _probe_offset(elf):
    """probe_loaded's offset within its RAM region, from THIS build's ELF —
    addresses move between builds, so they are never carried over."""
    nm = None
    for base, _d, files in os.walk(os.path.join(ROOT, "vendor-dl")):
        if "arm-none-eabi-nm" in files:
            nm = os.path.join(base, "arm-none-eabi-nm")
            break
    out = subprocess.run([nm, elf], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "probe_loaded":
            addr = int(parts[0], 16)
            if addr >> 24 == 0x03:
                return "internalRAM", addr - 0x03000000
            return "workRAM", addr - 0x02000000
    return None, None


def _read_probe(pid, region, off):
    r = subprocess.run(["gdb", "-p", str(pid), "-batch",
                        "-ex", 'printf "probe=%%d\n", *(int*)((char*)%s+0x%x)'
                        % (region, off),
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
    for line in (r.stdout or "").splitlines():
        if line.startswith("probe="):
            return int(line.split("=", 1)[1])
    return None


def run_persist(name="persist", save_type=None, want_sav_size=None):
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = persist_scene()
    if save_type:
        model["save_type"] = save_type
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    region, off = _probe_offset(os.path.join(out, "game.elf"))
    if not region:
        print("FAIL %s: probe_loaded not in the ELF" % name)
        return False

    from gba_run import find_vbam
    vb = find_vbam()
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")

    def boot(seconds):
        proc = subprocess.Popen([vb, "--no-opengl", rom], env=env, cwd=out,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        time.sleep(seconds)
        return proc

    # ---- first boot: fresh cartridge, saves, and is told to flush ----
    proc = boot(3.0)
    first = _read_probe(proc.pid, region, off)
    # vbam writes the battery file on clean exit, and this process is about
    # to be killed. Its own frontend flush is callable, which is the entire
    # reason gdb is here.
    subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                    "-ex", "call (void)sdlWriteBattery()",
                    "-ex", "detach", "-ex", "quit"],
                   capture_output=True, text=True, timeout=45)
    proc.kill()
    proc.wait()
    savs = [f for f in os.listdir(out) if f.endswith(".sav")]
    if first != -1:
        print("FAIL %s: first boot expected a fresh cartridge (probe -1), "
              "read %r" % (name, first))
        return False
    if not savs:
        print("FAIL %s: no battery file written on first boot" % name)
        return False

    # ---- second boot: same battery file; the save must come back ----
    proc = boot(3.0)
    second = _read_probe(proc.pid, region, off)
    proc.kill()
    proc.wait()
    if second != 1234:
        print("FAIL %s: power cycle lost the save: probe=%r, want 1234"
              % (name, second))
        return False
    if want_sav_size is not None:
        size = os.path.getsize(os.path.join(out, savs[0]))
        if size != want_sav_size:
            print("FAIL %s: battery file is %d bytes, the part holds %d --"
                  " the signature did not size it" % (name, size, want_sav_size))
            return False
    print("PASS %s  first boot saved, second boot restored 1234 from %s"
          % (name, savs[0]))
    return True




def encounter_full():
    """The whole loop in one cartridge: dialogue, then waves, then damage,
    then a save — the pillars the other slices prove alone, composed.

    Phases, visible to the harness through probe globals: 1 while the opening
    line types, 2 while rings fire at a soul parked on their centre (so damage
    is deterministic, not a matter of aim), 3 once four seconds of waves end
    in rt_game_save(). Health starts at 200 so surviving is never in doubt;
    what is asserted is that it DROPPED, the phase REACHED 3, bullets were IN
    FLIGHT at capture, and the battery file exists after the flush."""
    bone = [0x7FFF if (i // 8) % 4 else 0 for i in range(64)]
    script = r"""
volatile s32 probe_phase;
volatile s32 probe_health;
static s32 wave_t;

void fire_ring(s32 n, s32 speed)
{
    s32 i;
    for (i = 0; i < n; i++) {
        Instance* b = rt_create(NB_OBJ_BONE, 120, 60);
        if (!b) break;
        rt_set_speed_dir(b, (i * 256) / n, speed);
    }
}

void waves_step(void)
{
    if (probe_phase < 2) return;
    wave_t++;
    if ((wave_t & 15) == 0) fire_ring(12, 48);
    if (wave_t == 240 && probe_phase == 2) {
        nb_score = 777;
        rt_game_save();
        probe_phase = 3;
    }
    probe_health = nb_health;
}
"""
    return {
        "name": "Encounter loop",
        "sprites": [
            {"id": "spr_soul", "name": "Soul", "w": 16, "h": 16, "ox": 8,
             "oy": 8, "anim_speed": 0, "frames": [_soul()]},
            {"id": "spr_bone", "name": "Bone", "w": 8, "h": 8, "ox": 4,
             "oy": 4, "anim_speed": 0, "frames": [bone], "pal_bank": 2},
        ],
        "tilesets": [], "tables": [],
        "sounds": [
            {"id": "snd_blip", "name": "Blip", "tempo": 8, "loop": False,
             "steps": 2, "lead": [72, 0], "bass": [0, 0], "drum": [0, 0],
             "kind": 0, "duty": 1, "vol": 8, "decay": 2, "prio": 4},
            {"id": "snd_hit", "name": "Hit", "tempo": 8, "loop": False,
             "steps": 4, "lead": [0] * 4, "bass": [0] * 4,
             "drum": [1, 0, 0, 0], "kind": 1, "duty": 0, "vol": 10,
             "decay": 4, "prio": 5},
        ],
        "scripts": [{"id": "scr", "name": "Waves", "code": script}],
        "objects": [
            {"id": "obj_soul", "name": "Soul", "sprite": "spr_soul",
             "visible": True, "solid": False, "tilecol": 0, "depth": 0,
             "bb_inset": 2, "hurt_frames": 30, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "nb_health = 200; probe_phase = 1;"},
                     {"kind": "say_voice", "sound": "snd_blip"},
                     {"kind": "say",
                      "text": "It steps out from behind the pillar.{p}"
                              "There is nowhere to go but through."},
                     {"kind": "set_alarm", "alarm": "0", "steps": 60}]},
                 {"type": "alarm", "alarm": 0, "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "probe_phase = 2;"}]},
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "waves_step();"}]},
                 {"type": "collision", "object": "obj_bone", "actions": [
                     {"kind": "play_sound", "sound": "snd_hit"},
                     {"kind": "add_health", "value": -1}]}]},
            {"id": "obj_bone", "name": "Bone", "sprite": "spr_bone",
             "visible": True, "solid": False, "tilecol": 0, "depth": 1,
             "bb_inset": 1, "events": [
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "if (self->x < -8 || self->x > 248 || "
                              "self->y < -8 || self->y > 168) "
                              "rt_destroy(self);"}]}]},
        ],
        "rooms": [{"id": "rm", "name": "Corridor", "w": 240, "h": 160,
                   "speed": 60, "bg": "#101828", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_soul", "x": 120, "y": 60}]}],
        "start_room": "rm",
    }


def _offsets(elf, names):
    """{name: (vbam region, offset)} from THIS build's ELF."""
    nm = None
    for base, _d, files in os.walk(os.path.join(ROOT, "vendor-dl")):
        if "arm-none-eabi-nm" in files:
            nm = os.path.join(base, "arm-none-eabi-nm")
            break
    out = subprocess.run([nm, elf], capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in names:
            addr = int(parts[0], 16)
            region = "internalRAM" if addr >> 24 == 0x03 else "workRAM"
            base = 0x03000000 if addr >> 24 == 0x03 else 0x02000000
            found[parts[2]] = (region, addr - base)
    return found


def _read_ints(pid, offs):
    """{name: value} for a dict of name -> (region, offset), one attach."""
    cmds = []
    for name, (region, off) in sorted(offs.items()):
        cmds += ["-ex", 'printf "%s=%%d\n", *(int*)((char*)%s+0x%x)'
                 % (name, region, off)]
    r = subprocess.run(["gdb", "-p", str(pid), "-batch"] + cmds
                       + ["-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
    out = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            try:
                out[k.strip()] = int(v)
            except ValueError:
                pass
    return out


def run_encounter():
    name = "encounter"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = encounter_full()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    offs = _offsets(os.path.join(out, "game.elf"),
                    {"probe_phase", "probe_health"})
    if len(offs) != 2:
        print("FAIL %s: probes missing from the ELF: %s" % (name, offs))
        return False

    from gba_run import find_vbam
    vb = find_vbam()
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([vb, "--no-opengl", rom], env=env, cwd=out,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # Mid-dialogue: the panel must be typing while the phase is still 1-2.
        time.sleep(2.0)
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)systemScreenCapture(0)",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        early = _read_ints(proc.pid, offs)
        # Past the save point: phase 3, health spent, bullets still flying.
        time.sleep(5.5)
        late = _read_ints(proc.pid, offs)
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)sdlWriteBattery()",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        oam_bin = os.path.join(out, "oam.bin")
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "dump binary memory %s (char*)oam ((char*)oam)"
                        "+1024" % oam_bin,
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
    finally:
        proc.kill()
        proc.wait()

    shot = None
    auto = os.path.join(out, "game00.png")
    if os.path.exists(auto):
        shot = os.path.join(out, "frame_dialogue.png")
        os.replace(auto, shot)
    visible = 0
    if os.path.exists(oam_bin):
        data = open(oam_bin, "rb").read()
        visible = sum(1 for k in range(0, len(data), 8)
                      if (struct.unpack_from("<H", data, k)[0] & 0x0300)
                      != 0x0200)
    savs = [f for f in os.listdir(out) if f.endswith(".sav")]

    checks = [
        (early.get("probe_phase") in (1, 2),
         "dialogue phase at 2s (phase=%r)" % early.get("probe_phase")),
        (shot is not None and region_activity(shot, 108, 160) >= 0.05,
         "the opening line is on the panel at 2s"),
        (late.get("probe_phase") == 3,
         "the loop reached the save (phase=%r)" % late.get("probe_phase")),
        (late.get("probe_health", 200) < 200,
         "the waves cost health (health=%r)" % late.get("probe_health")),
        # Thirty mercy frames cap contact at two hits a second. Before
        # them, the same waves cost 230 health in four seconds; with
        # them, more than ~14 hits by the save point means the mercy
        # window is not being honoured.
        (late.get("probe_health", 0) >= 180,
         "mercy frames slowed the bleeding (health=%r)"
         % late.get("probe_health")),
        (late.get("probe_health", -1) >= 0,
         "health never went below zero (health=%r)"
         % late.get("probe_health")),
        (visible >= 8,
         "bullets in flight at capture (visible=%d)" % visible),
        (bool(savs), "battery file written"),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  phase %r->3, health %r, %d sprites, %s"
          % (name, early.get("probe_phase"), late.get("probe_health"),
             visible, savs[0]))
    return True




def jukebox_scene():
    """The two-voice audio slice: a looping sampled soundtrack under one-shot
    effects. The soundtrack is a second of low saw on PCM voice B, looping by
    the sound's own flag; a half-second effect fires on voice A most of the
    time. What is asserted is the HARDWARE arrangement: both Direct Sound
    channels enabled, each FIFO fed by its own DMA, one shared sample timer."""
    track = [((i * 3) % 160) - 80 for i in range(16384)]
    hit = [((i * 11) % 200) - 100 for i in range(8192)]
    return {
        "name": "Jukebox",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [_soul()]}],
        "tilesets": [], "tables": [], "scripts": [],
        "sounds": [
            {"id": "snd_track", "name": "Track", "tempo": 6, "loop": True,
             "steps": 4, "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
             "kind": 1, "duty": 0, "vol": 0, "decay": 0, "prio": 0,
             "pcm": track},
            {"id": "snd_hit", "name": "Hit", "tempo": 6, "loop": False,
             "steps": 4, "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
             "kind": 1, "duty": 0, "vol": 0, "decay": 0, "prio": 5,
             "pcm": hit},
        ],
        "objects": [{"id": "obj_dj", "name": "Player", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "play_sound", "sound": "snd_track"},
                             {"kind": "set_alarm", "alarm": "0",
                              "steps": 20}]},
                         {"type": "alarm", "alarm": 0, "actions": [
                             {"kind": "play_sound", "sound": "snd_hit"},
                             {"kind": "set_alarm", "alarm": "0",
                              "steps": 24}]}]}],
        "rooms": [{"id": "rm", "name": "Booth", "w": 240, "h": 160,
                   "speed": 60, "bg": "#201030", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_dj", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_jukebox():
    name = "jukebox"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = jukebox_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    from gba_run import find_vbam
    vb = find_vbam()
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([vb, "--no-opengl", rom], env=env, cwd=out,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(3.0)
        r = subprocess.run(
            ["gdb", "-p", str(proc.pid), "-batch",
             "-ex", 'printf "sndh=%04x\n", *(unsigned short*)((char*)ioMem+0x82)',
             "-ex", 'printf "dma1dad=%08x dma2dad=%08x\n", *(unsigned int*)((char*)ioMem+0xC0), *(unsigned int*)((char*)ioMem+0xCC)',
             "-ex", 'printf "tm1=%04x\n", *(unsigned short*)((char*)ioMem+0x106)',
             "-ex", "detach", "-ex", "quit"],
            capture_output=True, text=True, timeout=45)
    finally:
        proc.kill()
        proc.wait()
    v = dict(kv.split("=", 1) for ln in (r.stdout or "").splitlines()
             for kv in ln.split() if "=" in kv)
    sndh = int(v.get("sndh", "0"), 16)
    checks = [
        (sndh & 0x0008, "voice B at full volume (SOUNDCNT_H bit 3)"),
        (sndh & 0x4000, "voice B clocked by timer 1"),
        (sndh & 0x3000, "voice B routed to a speaker"),
        (sndh & 0x0004, "voice A at full volume while the effect plays"),
        (v.get("dma1dad") == "040000a0", "DMA1 feeds FIFO A"),
        (v.get("dma2dad") == "040000a4", "DMA2 feeds FIFO B"),
        (int(v.get("tm1", "0"), 16) & 0x0080, "the shared sample timer runs"),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s  (sndh=%04x dma1=%s dma2=%s tm1=%s)"
              % (name, "; ".join(bad), sndh, v.get("dma1dad"),
                 v.get("dma2dad"), v.get("tm1")))
        return False
    print("PASS %s  SOUNDCNT_H=%04x, both FIFOs fed, timer shared" % (name, sndh))
    return True




def palette_scene():
    """The cycling slice: one BG range and one OBJ range rotating, everything
    else still. Proof is two samples of emulated palette RAM half a second
    apart: the cycled ranges must differ between samples, the uncycled
    neighbours must not, and each cycled range must hold the same SET of
    colours both times -- rotation rearranges, it never invents."""
    # Both cycled ranges must hold REAL colours, or the test is vacuous:
    # rotating a run of zeros produces zeros, and the first version of this
    # fixture did exactly that -- no tileset, so BG entries 1..5 were never
    # populated and "did it rotate" was a question about nothing.
    shades = [0x7C00, 0x03E0, 0x001F, 0x7FFF, 0x0421]
    tile = [shades[(i // 8) % 5] for i in range(64)]
    spr = [(0x7C1F, 0x03FF, 0x7FE0)[(i // 16) % 3] for i in range(256)]
    return {
        "name": "Shimmer",
        "sprites": [{"id": "spr_gem", "name": "Gem", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [spr]}],
        "tilesets": [{"id": "ts", "name": "Falls", "size": 8,
                      "solid": [False] * 16, "auto_base": 0,
                      "tiles": [tile for _ in range(16)]}],
        "tables": [], "sounds": [],
        "scripts": [],
        "objects": [{"id": "obj_w", "name": "Water", "sprite": "spr_gem",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "rt_pal_cycle(0, 1, 5, 8); "
                                      "rt_pal_cycle(1, 1, 3, 6);"}]}]}],
        "rooms": [{"id": "rm", "name": "Falls", "w": 240, "h": 160,
                   "speed": 60, "bg": "#103050",
                   "tiles": [1] * ((240 // 8) * (160 // 8)), "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_w", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def _read_pal(pid, out, frame_off=None):
    """Both palettes (1 KiB) and, when asked, the runtime frame
    counter FROM THE SAME ATTACH -- wall-clock gaps mean nothing
    under load, so rotation checks must be phrased in frames."""
    path = os.path.join(out, "pal_%d.bin" % int(time.time() * 1000000))
    cmds = ["-ex", "dump binary memory %s (char*)paletteRAM "
            "((char*)paletteRAM)+1024" % path]
    if frame_off is not None:
        cmds += ["-ex", 'printf "frames=%%u\\n", '
                 '*(unsigned int*)((char*)internalRAM+0x%x)' % frame_off]
    r = subprocess.run(["gdb", "-p", str(pid), "-batch"] + cmds
                       + ["-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
    frame = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("frames="):
            frame = int(line.split("=", 1)[1])
    data = open(path, "rb").read() if os.path.exists(path) else b""
    return (data, frame) if frame_off is not None else data


def run_palette():
    name = "palette"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = palette_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    frame_off = None
    offs = _offsets(os.path.join(out, "game.elf"), {"g_frames"})
    if "g_frames" in offs and offs["g_frames"][0] == "internalRAM":
        frame_off = offs["g_frames"][1]
    if frame_off is None:
        print("FAIL %s: g_frames not locatable; the rotation check "
              "cannot be phrased in frames" % name)
        proc.kill()
        proc.wait()
        return False
    # Wall-clock means nothing here: under load the emulator runs at
    # whatever rate it can, and a sample gap can land on an exact
    # multiple of a cycle period twice in a row -- it did, which is
    # how a rotating range photographed still three times. So every
    # sample carries ITS OWN frame number, and pairs are only judged
    # when their frame delta is NOT a multiple of the period.
    samples = []
    try:
        time.sleep(2.0)
        for k in range(14):
            samples.append(_read_pal(proc.pid, out, frame_off))
            # A FIXED cadence resonates: 0.25s of sleep plus the dump
            # pause came to almost exactly the BG period once, and every
            # pair delta was a multiple of 40. The growing sleep makes
            # consecutive deltas unequal, so no period can lock on.
            time.sleep(0.12 + 0.09 * k)
            fr = [f for _d, f in samples if f is not None]
            if len(fr) >= 3 \
                    and any((y - x) % 40 for x in fr for y in fr if y > x) \
                    and any((y - x) % 18 for x in fr for y in fr if y > x):
                break
    finally:
        proc.kill()
        proc.wait()
    samples = [(d, f) for d, f in samples
               if len(d) >= 1024 and f is not None]
    if len(samples) < 2:
        print("FAIL %s: palette RAM not readable" % name)
        return False

    def entries(buf, base, first, count):
        off = base + first * 2
        return [buf[off + i * 2] | (buf[off + i * 2 + 1] << 8)
                for i in range(count)]

    def judge(base, first, count, period):
        """(rotated, conserved): rotated must hold for SOME pair whose
        frame delta is not a period multiple; conserved for all."""
        rot, seen_valid = False, False
        rows = [(entries(d, base, first, count), f) for d, f in samples]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                (ra, fa), (rb, fb) = rows[i], rows[j]
                if (fb - fa) % period == 0:
                    continue
                seen_valid = True
                if ra != rb:
                    rot = True
        cons = all(sorted(r) == sorted(rows[0][0]) for r, _f in rows)
        if not seen_valid:
            # Not a verdict about the cycle at all: the harness never
            # obtained two samples a non-multiple of the period apart.
            return None, cons
        return rot, cons
    bg_rot, bg_cons = judge(0, 1, 5, 40)
    ob_rot, ob_cons = judge(512, 1, 3, 18)
    stills = [entries(d, 0, 8, 8) for d, _f in samples]
    checks = [
        (bg_rot is not None,
         "the sampler obtained frame-distinct pairs for the BG period"),
        (bool(bg_rot), "the BG range rotates (frame-indexed pairs)"),
        (bg_cons, "...and holds the same colours, rearranged"),
        (ob_rot is not None,
         "the sampler obtained frame-distinct pairs for the OBJ period"),
        (bool(ob_rot), "the OBJ range rotates (frame-indexed pairs)"),
        (ob_cons, "...same colours there too"),
        (all(x == stills[0] for x in stills),
         "entries outside the ranges stayed put"),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        for d, f in samples:
            print("    frame=%-8s bg=%s ob=%s"
                  % (f, entries(d, 0, 1, 5), entries(d, 512, 1, 3)))
        return False
    print("PASS %s  both ranges rotate, neighbours still, colour sets conserved"
          % name)
    return True




def mortal_scene():
    """The death slice: three health, no mercy frames, contact every frame.
    The floor must hold at zero and the no_health event must fire exactly
    once -- the engine latches it until health rises, so the counter probe
    reading 1 is the whole assertion."""
    bone = [0x7FFF if (i // 8) % 4 else 0 for i in range(64)]
    return {
        "name": "Mortal",
        "sprites": [
            {"id": "spr_soul", "name": "Soul", "w": 16, "h": 16, "ox": 8,
             "oy": 8, "anim_speed": 0, "frames": [_soul()]},
            {"id": "spr_bone", "name": "Bone", "w": 8, "h": 8, "ox": 4,
             "oy": 4, "anim_speed": 0, "frames": [bone], "pal_bank": 2},
        ],
        "tilesets": [], "tables": [], "sounds": [],
        "scripts": [{"id": "scr", "name": "Probes",
                     "code": "volatile s32 probe_deaths;\n"
                             "volatile s32 probe_health_now;\n"}],
        "objects": [
            {"id": "obj_soul", "name": "Soul", "sprite": "spr_soul",
             "visible": True, "solid": False, "tilecol": 0, "depth": 0,
             "bb_inset": 0, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "nb_health = 3;"}]},
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "probe_health_now = nb_health;"}]},
                 {"type": "collision", "object": "obj_bone", "actions": [
                     {"kind": "add_health", "value": -1}]},
                 {"type": "no_health", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "probe_deaths++;"}]}]},
            {"id": "obj_bone", "name": "Bone", "sprite": "spr_bone",
             "visible": True, "solid": False, "tilecol": 0, "depth": 1,
             "bb_inset": 0, "events": []},
        ],
        "rooms": [{"id": "rm", "name": "End", "w": 240, "h": 160,
                   "speed": 60, "bg": "#181018", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_soul", "x": 120, "y": 80},
                                 {"object": "obj_bone", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_mortal():
    name = "mortal"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = mortal_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    offs = _offsets(os.path.join(out, "game.elf"),
                    {"probe_deaths", "probe_health_now"})
    if len(offs) != 2:
        print("FAIL %s: probes missing from the ELF: %s" % (name, offs))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(3.0)
        v = _read_ints(proc.pid, offs)
    finally:
        proc.kill()
        proc.wait()
    checks = [
        (v.get("probe_health_now") == 0,
         "health sits exactly on the floor (health=%r)"
         % v.get("probe_health_now")),
        (v.get("probe_deaths") == 1,
         "the no_health event fired exactly once (fired=%r)"
         % v.get("probe_deaths")),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  floor held at 0, death fired once" % name)
    return True




def objwin_scene():
    """The sprite-shaped window: a solid 16x16 stencil over a bright tiled
    room, with everything outside all windows reduced to the backdrop. The
    frame must show the tile pattern ONLY inside a sprite-sized region --
    the stencil is a hole, not a drawing."""
    bright = [0x7FE0] * 64
    stencil = [0x7FFF] * 256
    return {
        "name": "Spotlight",
        "sprites": [{"id": "spr_hole", "name": "Hole", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [stencil]}],
        "tilesets": [{"id": "ts", "name": "Wall", "size": 8,
                      "solid": [False] * 16, "auto_base": 0,
                      "tiles": [bright for _ in range(16)]}],
        "tables": [], "sounds": [], "scripts": [],
        "objects": [{"id": "obj_h", "name": "Torch", "sprite": "spr_hole",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "rt_set_objwin(self, 1); "
                                      "rt_window_obj(WIN_BG0); "
                                      "rt_window(0, 0, 0, 0, 0, 0, 0);"}]}]}],
        "rooms": [{"id": "rm", "name": "Dark", "w": 240, "h": 160,
                   "speed": 60, "bg": "#080810",
                   "tiles": [1] * ((240 // 8) * (160 // 8)), "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_h", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_objwin():
    name = "objwin"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = objwin_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    shot = os.path.join(out, "frame.png")
    rep = run_rom(rom, 3.0, shot=shot)
    if not os.path.exists(shot):
        print("FAIL %s: no frame captured" % name)
        return False
    w, h, rows = decode_png(shot)
    # Anything that is not the near-black backdrop came through the stencil.
    # Judging by channel guesses went wrong once already: 0x7FE0 is GREEN in
    # BGR555, not the yellow the hex suggests to RGB eyes.
    lit = 0
    for y in range(h):
        r = rows[y]
        for x in range(0, w * 3, 3):
            if abs(r[x] - 8) > 24 or abs(r[x + 1] - 8) > 24 \
                    or abs(r[x + 2] - 16) > 24:
                lit += 1
    # A 16x16 stencil at 240x160 scales to whatever the shot size is; the
    # captured frames are native 240x160, so the lit region is ~256 px.
    checks = [
        (rep.get("dispcnt") and int(rep["dispcnt"], 16) & 0x8000,
         "the OBJ window is enabled in DISPCNT (%s)" % rep.get("dispcnt")),
        (0 < lit <= 1024,
         "the tiles show only through the stencil (lit=%d, screen=%d)"
         % (lit, w * h)),
        (rep["visible"] >= 1,
         "the stencil occupies a hardware OBJ slot"),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  window on, %d lit pixels through a 256-pixel stencil"
          % (name, lit))
    return True


FIXTURES = {
    "bullet": (bullet_hell, 4.0, check_bullet),
    "dialogue": (dialogue_scene, 5.0, check_dialogue),
}
RUNNERS = {"persist": run_persist, "encounter": run_encounter,
           "persist_flash": lambda: run_persist("persist_flash",
                                               "flash128", 131072),
           "jukebox": run_jukebox, "palette": run_palette,
           "mortal": run_mortal, "objwin": run_objwin}


def main(argv):
    names = argv or (sorted(FIXTURES) + sorted(RUNNERS))
    ok = True
    for n in names:
        if n in RUNNERS:
            ok = RUNNERS[n]() and ok
            continue
        make, seconds, check = FIXTURES[n]
        ok = _run(n, make(), seconds, check) and ok
    print("RESULT: " + ("all slices compose" if ok else "A SLICE DOES NOT"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
