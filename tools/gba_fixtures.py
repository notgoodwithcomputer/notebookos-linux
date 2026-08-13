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


def frame_budget(out, pid):
    """What fraction of a frame the game spent, read from the runtime's own
    profiler rather than guessed at.

    g_prof_last holds the last completed frame's tick count per slot, in units
    of timer 2 at 1/64 of the CPU clock; PROF_FRAME is one frame's worth. The
    Undertale test says SIXTY FRAMES A SECOND, and until this existed nothing
    asserted it -- a game that runs at 40 fps looks exactly like a game that
    runs, through every other window this harness has.
    """
    offs = _offsets(os.path.join(out, "game.elf"), {"g_prof_last"})
    if "g_prof_last" not in offs:
        return None
    region, off = offs["g_prof_last"]
    cmds = []
    for i in range(3):                       # STEP, MOVE, DRAW
        cmds += ["-ex", 'printf "p%d=%%d\n", '
                 '*(unsigned short*)((char*)%s+0x%x)' % (i, region, off + i * 2)]
    r = subprocess.run(["gdb", "-p", str(pid), "-batch"] + cmds
                       + ["-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
    v = {}
    for line in (r.stdout or "").splitlines():
        if line.startswith("p") and "=" in line:
            k, _, n = line.partition("=")
            try:
                v[k] = int(n)
            except ValueError:
                pass
    if len(v) < 3:
        return None
    # PROF_FRAME: one frame of timer 2 at TM_FREQ_64 -- 16.78 MHz / 64 / 60.
    PROF_FRAME = 16777216 // 64 // 60
    return sum(v.values()) * 100.0 / PROF_FRAME


def run_bullet():
    """The bullet slice, plus the assertion the Undertale test actually makes:
    SIXTY FRAMES A SECOND. Everything else here could pass on a game running
    at forty."""
    name = "bullet"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = bullet_hell()
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
    worst, oam_bin = 0.0, os.path.join(out, "oam.bin")
    try:
        time.sleep(3.0)
        # The pool is saturated by now; sample the busiest frames it has.
        for _ in range(5):
            pct = frame_budget(out, proc.pid)
            if pct is not None:
                worst = max(worst, pct)
            time.sleep(0.2)
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "dump binary memory %s (char*)oam "
                        "((char*)oam)+1024" % oam_bin,
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        shot = os.path.join(out, "frame.png")
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)systemScreenCapture(0)",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        time.sleep(0.4)
        auto = os.path.join(out, "game00.png")
        if os.path.exists(auto):
            os.replace(auto, shot)
    finally:
        proc.kill()
        proc.wait()
    visible = 0
    if os.path.exists(oam_bin):
        d = open(oam_bin, "rb").read()
        visible = sum(1 for k in range(0, len(d), 8)
                      if (struct.unpack_from("<H", d, k)[0] & 0x0300) != 0x0200)
    checks = [
        (visible >= 32,
         "the sprite pool is saturated (%d)" % visible),
        (os.path.exists(shot) and region_activity(shot, 0, 16) >= 0.02,
         "the profiler overlay is in the frame"),
        (worst > 0, "the profiler reported a budget at all (%.0f%%)" % worst),
        (worst < 100,
         "the busiest frame fits inside its 60th of a second (%.0f%%)" % worst),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  %d sprites at %.0f%% of the frame budget -- 60 fps holds"
          % (name, visible, worst))
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
    def entries(buf, base, first, count):
        off = base + first * 2
        return [buf[off + i * 2] | (buf[off + i * 2 + 1] << 8)
                for i in range(count)]

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
        for k in range(10):
            samples.append(_read_pal(proc.pid, out, frame_off))
            # A FIXED cadence resonates: 0.25s of sleep plus the dump
            # pause came to almost exactly the BG period once, and every
            # pair delta was a multiple of 40. The growing sleep makes
            # consecutive deltas unequal, so no period can lock on.
            time.sleep(0.12 + 0.09 * k)
            # Enough when BOTH ranges have shown more than one
            # arrangement -- that is precisely what the rate check
            # needs, and no more.
            good = [d for d, f in samples if len(d) >= 1024 and f is not None]
            if len(good) >= 4:
                bgs = {tuple(entries(d, 0, 1, 5)) for d in good}
                obs = {tuple(entries(d, 512, 1, 3)) for d in good}
                if len(bgs) >= 2 and len(obs) >= 2:
                    break
    finally:
        proc.kill()
        proc.wait()
    samples = [(d, f) for d, f in samples
               if len(d) >= 1024 and f is not None]
    if len(samples) < 2:
        print("FAIL %s: palette RAM not readable" % name)
        return False


    def judge(base, first, count, frames_per_step):
        """Does the range rotate at EXACTLY the rate it was given?

        Three earlier versions of this asked "did it change between two
        samples" and were beaten three different ways by aliasing, because
        that question depends on WHEN you look. This one does not: the cycle
        advances one step every `frames_per_step` frames, so from any sample's
        frame number the arrangement of every other sample is PREDICTED. The
        cycle's tick counter started whenever rt_pal_cycle ran, so the phase
        is unknown -- but there are only `frames_per_step` possible phases, and
        the assertion is that one of them explains every sample at once.

        Returns (verdict, detail). verdict True = rotates at the stated rate,
        False = does not, None = the samples never showed two arrangements, so
        the harness could not ask the question at all."""
        rows = [(entries(d, base, first, count), f) for d, f in samples]
        distinct = {tuple(r) for r, _f in rows}
        if len(distinct) < 2:
            return None, "only one arrangement across %d samples" % len(rows)
        if not all(sorted(r) == sorted(rows[0][0]) for r, _f in rows):
            return False, "the colour set changed; this is not a rotation"
        base_row, base_f = rows[0]
        for phase in range(frames_per_step):
            def steps(f):
                return ((f + phase) // frames_per_step) % count
            ok_all = True
            for r, f in rows:
                k = (steps(f) - steps(base_f)) % count
                # p[0] takes p[count-1] each step: a rotate RIGHT by k.
                want = [base_row[(i - k) % count] for i in range(count)]
                if r != want:
                    ok_all = False
                    break
            if ok_all:
                return True, "phase %d explains every sample" % phase
        return False, "no phase explains all %d samples" % len(rows)

    bg_rot, bg_why = judge(0, 1, 5, 8)
    ob_rot, ob_why = judge(512, 1, 3, 6)
    stills = [entries(d, 0, 8, 8) for d, _f in samples]
    checks = [
        (bg_rot is not None,
         "the BG samples showed more than one arrangement (%s)" % bg_why),
        (bg_rot is not False,
         "the BG range rotates at its stated rate (%s)" % bg_why),
        (ob_rot is not None,
         "the OBJ samples showed more than one arrangement (%s)" % ob_why),
        (ob_rot is not False,
         "the OBJ range rotates at its stated rate (%s)" % ob_why),
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




def chorus_scene():
    """The mixer slice: four one-shot samples audible AT ONCE over a looping
    sampled soundtrack. Before the mixer, a second rt_pcm_play cut the first
    off mid-note -- a footstep silenced a sword. The assertion is that four
    mixer voices are live simultaneously while voice B still loops, and that
    the mixed output is not silence."""
    tones = []
    for k in range(4):
        step = 3 + k * 5
        tones.append([((i * step) % 120) - 60 for i in range(6000)])
    track = [((i * 2) % 100) - 50 for i in range(16384)]
    sounds = [{"id": "snd_track", "name": "Track", "tempo": 6, "loop": True,
               "steps": 4, "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
               "kind": 1, "duty": 0, "vol": 0, "decay": 0, "prio": 0,
               "pcm": track}]
    for k in range(4):
        sounds.append({"id": "snd_v%d" % k, "name": "Voice %d" % k,
                       "tempo": 6, "loop": False, "steps": 4,
                       "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
                       "kind": 1, "duty": 0, "vol": 0, "decay": 0,
                       "prio": 0, "pcm": tones[k]})
    # All four fire within a few frames of each other and each lasts ~0.37s,
    # so every one is still mid-sample when the harness looks.
    fire = [{"kind": "play_sound", "sound": "snd_v%d" % k} for k in range(4)]
    return {
        "name": "Chorus",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": sounds,
        "scripts": [],
        "objects": [{"id": "obj_c", "name": "Choir", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "play_sound", "sound": "snd_track"},
                             {"kind": "set_alarm", "alarm": "0",
                              "steps": 30}]},
                         {"type": "alarm", "alarm": 0, "actions":
                             fire + [{"kind": "set_alarm", "alarm": "0",
                                      "steps": 20}]}]}],
        "rooms": [{"id": "rm", "name": "Choir", "w": 240, "h": 160,
                   "speed": 60, "bg": "#102820", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_c", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_chorus():
    name = "chorus"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = chorus_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    offs = _offsets(os.path.join(out, "game.elf"), {"g_mixv", "g_mixbuf"})
    if "g_mixv" not in offs or "g_mixbuf" not in offs:
        print("FAIL %s: mixer symbols missing from the ELF: %s" % (name, offs))
        return False
    mv_region, mv_off = offs["g_mixv"]
    mb_region, mb_off = offs["g_mixbuf"]
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    best_live, nonzero, sndh = 0, 0, 0
    try:
        # MixVoice is {const s8* data; u32 pos, len; u8 on;} = 16 bytes with
        # padding; `on` sits at +12. Sample repeatedly: the four fire together
        # but retire together too, so one badly-timed look could see none.
        time.sleep(1.2)
        for _ in range(7):
            cmds = []
            for v in range(4):
                cmds += ["-ex", 'printf "on%d=%%d\n", '
                         '*(unsigned char*)((char*)%s+0x%x)'
                         % (v, mv_region, mv_off + v * 16 + 12)]
            cmds += ["-ex", 'printf "sndh=%04x\n", '
                     '*(unsigned short*)((char*)ioMem+0x82)']
            cmds += ["-ex", 'printf "mb=%%d,%%d,%%d,%%d\n", '
                     '*(signed char*)((char*)%s+0x%x), '
                     '*(signed char*)((char*)%s+0x%x), '
                     '*(signed char*)((char*)%s+0x%x), '
                     '*(signed char*)((char*)%s+0x%x)'
                     % (mb_region, mb_off + 40, mb_region, mb_off + 90,
                        mb_region, mb_off + 150, mb_region, mb_off + 220)]
            r = subprocess.run(["gdb", "-p", str(proc.pid), "-batch"] + cmds
                               + ["-ex", "detach", "-ex", "quit"],
                               capture_output=True, text=True, timeout=45)
            v = dict(kv.split("=", 1) for ln in (r.stdout or "").splitlines()
                     for kv in ln.split() if "=" in kv)
            live = sum(1 for k in range(4) if v.get("on%d" % k) == "1")
            best_live = max(best_live, live)
            if v.get("sndh"):
                sndh = int(v["sndh"], 16)
            mb = [int(x) for x in (v.get("mb") or "0,0,0,0").split(",")]
            nonzero = max(nonzero, sum(1 for x in mb if x != 0))
            time.sleep(0.18)
    finally:
        proc.kill()
        proc.wait()
    checks = [
        (best_live == 4,
         "four mixer voices were live at once (best=%d)" % best_live),
        (sndh & 0x0004, "voice A carries the mix (SOUNDCNT_H=%04x)" % sndh),
        (sndh & 0x0008, "voice B still carries the soundtrack"),
        (nonzero >= 2,
         "the mix buffer holds audio, not silence (%d/4 probes)" % nonzero),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  4 voices mixed live over the soundtrack, SOUNDCNT_H=%04x"
          % (name, sndh))
    return True




def power_scene():
    """Power and cartridge GPIO -- with an honest account of what this proves.

    PROVEN: the GPIO sequences and the BIOS halt execute under real hardware
    emulation without faulting, the frame loop survives them, and the reads
    return.

    NOT PROVEN, and why: rumble, the solar sensor and the gyro live in a
    CARTRIDGE. vbam can emulate them (systemGetSensorDarkness and friends)
    but only for ROMs a vba-over.ini marks as carrying one, and this build has
    no such file -- so those pins read plain cartridge ROM and any "sensor
    value" here is a ROM byte. An earlier version of this slice asserted
    0 <= solar <= 255 on that byte and passed: a check that could not fail.
    rt_sleep is likewise unexercised, because waking it needs a keypress the
    headless harness cannot produce and calling it would hang the run. Both
    need real hardware or a configured emulator; this gate says so instead of
    implying coverage."""
    return {
        "name": "Power",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": [],
        "scripts": [{"id": "scr", "name": "Probes",
                     "code": "volatile s32 probe_stage;\n"
                             "volatile s32 probe_solar;\n"
                             "volatile s32 probe_gyro;\n"}],
        "objects": [{"id": "obj_p", "name": "Tester", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "probe_stage = 1; rt_rumble(1);"},
                             {"kind": "set_alarm", "alarm": "0",
                              "steps": 60}]},
                         {"type": "alarm", "alarm": 0, "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "rt_rumble(0); "
                                      "probe_solar = rt_solar(); "
                                      "probe_gyro = rt_gyro(); "
                                      "rt_gpio_release(); "
                                      "rt_wait_vblank(); "
                                      "probe_stage = 2;"}]}]}],
        "rooms": [{"id": "rm", "name": "Bench", "w": 240, "h": 160,
                   "speed": 60, "bg": "#202020", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_p", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_power():
    name = "power"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = power_scene()
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
                    {"probe_stage", "probe_solar", "probe_gyro", "g_frames"})
    if len(offs) != 4:
        print("FAIL %s: probes missing from the ELF: %s" % (name, offs))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # While rumble is asserted (the first second), the GPIO pins must show
        # it: control enabled, bit 3 an output, bit 3 driven high.
        # No GPIO register read here on purpose: with no vba-over.ini
        # entry the cartridge carries no peripheral, and 0x080000C4
        # reads plain ROM. Reporting a ROM byte as a pin state is
        # worse than reporting nothing.
        time.sleep(2.6)
        v = _read_ints(proc.pid, offs)
    finally:
        proc.kill()
        proc.wait()
    checks = [
        (v.get("probe_stage") == 2,
         "the alarm path ran through solar, gyro and a BIOS halt (stage=%r)"
         % v.get("probe_stage")),
        (v.get("g_frames", 0) > 60,
         "rt_wait_vblank idled without stopping the frame loop (frames=%r)"
         % v.get("g_frames")),
        # These establish only that the sequences RAN and returned. With no
        # cartridge peripheral the values carry no meaning, so nothing is
        # asserted about them.
        (v.get("probe_solar") is not None,
         "the solar sequence completed and returned"),
        (v.get("probe_gyro") is not None,
         "the gyro sequence completed and returned"),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  GPIO+halt sequences ran, %s frames; sensor VALUES "
          "unverifiable without a cartridge peripheral"
          % (name, v.get("g_frames")))
    return True




def affine_scene():
    """The Mode 7 slice: a room whose ground is an AFFINE layer, turning.

    Two tiles in a checker so rotation is visible as a change in the pixel
    mix, and a script that advances the angle every frame. Asserted on the
    emulator: the display really is in mode 1 with BG2 on, BG2CNT really is
    an 8bpp affine layer, the affine matrix registers really change as the
    angle advances, and the frame really differs between two angles."""
    a = [0x7C00] * 64
    b = [0x03E0] * 64
    script = r"""
static s32 spin;

void ground_turn(void)
{
    spin = (spin + 3) & 255;
    rt_bg_affine(2, 64, 64, 120, 80, spin, 256);
}
"""
    # 32x32 cells of alternating tile 1 and tile 2 -- a checker at tile scale.
    amap = [1 + ((x + y) & 1) for y in range(32) for x in range(32)]
    return {
        "name": "Mode 7",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0,
                     "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": [],
        "affine_tileset": {"tiles": [[0] * 64, a, b]},
        "scripts": [{"id": "scr", "name": "Ground", "code": script}],
        "objects": [{"id": "obj_g", "name": "Pilot", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "events": [
                         {"type": "step", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "ground_turn();"}]}]}],
        "rooms": [{"id": "rm", "name": "Floor", "w": 240, "h": 160,
                   "speed": 60, "bg": "#000010", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "affine": amap,
                   "instances": [{"object": "obj_g", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_affine():
    name = "affine"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = affine_scene()
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
    def state():
        r = subprocess.run(
            ["gdb", "-p", str(proc.pid), "-batch",
             "-ex", 'printf "dispcnt=%04x bg2cnt=%04x pa=%04x pb=%04x\n", '
             '*(unsigned short*)((char*)ioMem+0x00), '
             '*(unsigned short*)((char*)ioMem+0x0C), '
             '*(unsigned short*)((char*)ioMem+0x20), '
             '*(unsigned short*)((char*)ioMem+0x22)',
             "-ex", "detach", "-ex", "quit"],
            capture_output=True, text=True, timeout=45)
        return dict(kv.split("=", 1) for ln in (r.stdout or "").splitlines()
                    for kv in ln.split() if "=" in kv)
    try:
        time.sleep(1.5)
        a = state()
        shot_a = os.path.join(out, "a.png")
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)systemScreenCapture(0)",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        time.sleep(0.4)
        auto = os.path.join(out, "game00.png")
        if os.path.exists(auto):
            os.replace(auto, shot_a)
        time.sleep(0.7)
        b = state()
    finally:
        proc.kill()
        proc.wait()
    dc = int(a.get("dispcnt", "0"), 16)
    bg2 = int(a.get("bg2cnt", "0"), 16)
    moved = (a.get("pa"), a.get("pb")) != (b.get("pa"), b.get("pb"))
    lit = 0
    if os.path.exists(shot_a):
        w, h, rows = decode_png(shot_a)
        for y in range(h):
            r = rows[y]
            for x in range(0, w * 3, 3):
                if r[x] > 40 or r[x + 1] > 40:
                    lit += 1
    checks = [
        ((dc & 7) == 1, "the display is in mode 1 (dispcnt=%04x)" % dc),
        (dc & 0x0400, "BG2 is on"),
        (not (dc & 0x0800), "BG3 is off, as an affine room requires"),
        (bg2 & 0x0080, "BG2 is 8bpp (bg2cnt=%04x)" % bg2),
        (bg2 & 0x2000, "and wraps"),
        (moved, "the affine matrix advances with the angle (%s -> %s)"
         % (a.get("pa"), b.get("pa"))),
        (lit > 2000, "the ground is actually drawn (%d lit pixels)" % lit),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  mode 1, 8bpp affine BG2 wrapping, matrix turning, %d "
          "lit pixels" % (name, lit))
    return True




def palbank_scene():
    """Per-instance palette banks: four instances of ONE object, wearing four
    different colour sets from the SAME tiles. Also a computed colour written
    straight into palette RAM. The assertion is on pixels: four distinct
    non-backdrop colours must appear on screen, which cannot happen unless the
    bank override reaches OAM."""
    # One sprite, drawn entirely in colour index 1 of whatever bank it wears.
    # NOT 0x7C1F: that is the generator's TRANSPARENT colour, so a sprite
    # filled with it is a sprite of index 0 -- invisible, and the first
    # version of this fixture asked why four colours had not appeared.
    solid = [0x7FFF] * 256
    script = r"""
void tint_setup(void)
{
    /* Four banks, four colours, written at run time -- nothing in the
       project file says what these are. */
    rt_pal_set(1, 1 * 16 + 1, 0x001F);   /* bank 1 */
    rt_pal_set(1, 2 * 16 + 1, 0x03E0);   /* bank 2 */
    rt_pal_set(1, 3 * 16 + 1, 0x7C00);   /* bank 3 */
    rt_pal_set(1, 4 * 16 + 1, 0x7FE0);   /* bank 4 */
}
"""
    objs = [{"id": "obj_t%d" % k, "name": "Tint %d" % k, "sprite": "spr_solid",
             "visible": True, "solid": False, "tilecol": 0, "depth": 0,
             "bb_inset": 0, "hurt_frames": 0, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": ("tint_setup(); " if k == 1 else "")
                              + "rt_set_palbank(self, %d);" % k}]}]}
            for k in range(1, 5)]
    return {
        "name": "Tints",
        "sprites": [{"id": "spr_solid", "name": "Block", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [solid],
                     "pal_bank": 1}],
        "tilesets": [], "tables": [], "sounds": [],
        "scripts": [{"id": "scr", "name": "Tint", "code": script}],
        "objects": objs,
        "rooms": [{"id": "rm", "name": "Swatches", "w": 240, "h": 160,
                   "speed": 60, "bg": "#101010", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_t%d" % k,
                                  "x": 40 + (k - 1) * 48, "y": 80}
                                 for k in range(1, 5)]}],
        "start_room": "rm",
    }


def run_palbank():
    name = "palbank"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = palbank_scene()
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
    from collections import Counter
    seen = Counter()
    for y in range(h):
        r = rows[y]
        for x in range(0, w * 3, 3):
            px = (r[x], r[x + 1], r[x + 2])
            # anything that is not the near-black backdrop
            if max(px) > 60:
                seen[px] += 1
    # Four sprites of 256 pixels each; count colours with a real area, so a
    # stray edge pixel cannot pass for a swatch.
    strong = [c for c, n in seen.items() if n >= 100]
    checks = [
        (rep["visible"] >= 4, "four sprites reached OAM (%d)" % rep["visible"]),
        (len(strong) >= 4,
         "four distinct colours from one tile set (%d: %s)"
         % (len(strong), sorted(strong)[:5])),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  %d instances, %d distinct run-time colours, one sprite"
          % (name, rep["visible"], len(strong)))
    return True




def scale_scene():
    """The Pokemon test at size: 400 species, 350 moves, 200 rooms.

    Part VII asks whether a TEAM can author that much and still ship a
    cartridge. Nothing before this ever put those numbers through the
    generator, so the questions are simply: does it generate, does the C
    compile, does the ROM fit 32 MB, and does the thing BOOT -- a project
    that builds a 30 MB image and hangs on the first frame has answered
    nothing."""
    species = [["Species %d" % i, 40 + i % 60, 30 + i % 50, i % 18, i % 4,
                20 + i % 40, 15 + i % 30, "Route %d" % (i % 40)]
               for i in range(400)]
    moves = [["Move %d" % i, 5 + i % 120, 70 + i % 30, i % 18, i % 8]
             for i in range(350)]
    tile = [0x2E6A if (i // 8) % 2 else 0x1A45 for i in range(64)]
    hero = [0x7FFF if 4 <= (i % 16) < 12 and 4 <= (i // 16) < 12 else 0x7C1F
            for i in range(256)]
    objs = [{"id": "obj_npc%d" % k, "name": "NPC %d" % k,
             "sprite": "spr_hero", "visible": True, "solid": False,
             "tilecol": 1, "depth": 0, "bb_inset": 2, "hurt_frames": 0,
             "events": [{"type": "step", "actions": [
                 {"kind": "if_var", "var": "seen", "op": "==", "value": 0,
                  "children": [{"kind": "set_var", "var": "seen",
                                "value": k}]}]}]}
            for k in range(59)]
    objs.insert(0, {"id": "obj_hero", "name": "Hero", "sprite": "spr_hero",
                    "visible": True, "solid": False, "tilecol": 1,
                    "depth": 0, "bb_inset": 2, "hurt_frames": 0,
                    "events": [{"type": "step", "actions": [
                        {"kind": "move_keys", "speed": 2}]}]})
    rooms = []
    for i in range(200):
        # Each room links to the next, so the world is genuinely CONNECTED
        # rather than 200 islands -- the warp table is part of what scales.
        nxt = (i + 1) % 200
        rooms.append({
            "id": "rm_%d" % i, "name": "Route %d" % i, "w": 240, "h": 160,
            "speed": 60, "bg": "#0C2818",
            "tiles": [1] * (30 * 20), "far": None, "far_div": 2,
            "edge_open": False,
            "warps": [{"x": 224, "y": 72, "w": 16, "h": 16,
                       "room": "rm_%d" % nxt, "tx": 16, "ty": 72}],
            "instances": ([{"object": "obj_hero", "x": 40, "y": 80}]
                          if i == 0 else [])
            + [{"object": "obj_npc%d" % ((i + j) % 59),
                "x": 32 + (j % 6) * 32, "y": 32 + (j // 6) * 32}
               for j in range(20)]})
    return {
        "name": "Scale",
        "sprites": [{"id": "spr_hero", "name": "Hero", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [hero]}],
        "tilesets": [{"id": "ts", "name": "World", "size": 8,
                      "solid": [False, False] + [True] * 14,
                      "auto_base": 0, "tiles": [tile] * 16}],
        "tables": [
            {"id": "species", "columns": [
                {"name": "Name", "type": "text"}, {"name": "HP", "type": "int"},
                {"name": "Atk", "type": "int"}, {"name": "Type", "type": "int"},
                {"name": "Rarity", "type": "int"}, {"name": "Def", "type": "int"},
                {"name": "Spd", "type": "int"}, {"name": "Home", "type": "text"}],
             "rows": species},
            {"id": "moves", "columns": [
                {"name": "Name", "type": "text"}, {"name": "PP", "type": "int"},
                {"name": "Power", "type": "int"}, {"name": "Kind", "type": "int"},
                {"name": "Effect", "type": "int"}],
             "rows": moves},
        ],
        "sounds": [], "scripts": [],
        "objects": objs,
        "rooms": rooms,
        "start_room": "rm_0",
    }


def run_scale():
    name = "scale"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    t0 = time.time()
    model = scale_scene()
    problems = gbabuild.check_project(model)
    t_check = time.time() - t0
    # Problems are not automatically failure here: a project this size may
    # legitimately exceed a budget, and the tool SAYING so is correct. What
    # would be wrong is a crash, or silence about something real.
    t1 = time.time()
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    t_build = time.time() - t1
    if not built:
        print("FAIL %s: did not build after %.1fs: %s"
              % (name, t_build, (log or "")[-400:]))
        return False
    size = os.path.getsize(rom)
    csize = os.path.getsize(os.path.join(out, "game_data.c"))
    rep = run_rom(rom, 4.0)
    checks = [
        (size <= 32 * 1024 * 1024,
         "the ROM fits a cartridge (%.2f MB)" % (size / 1048576.0)),
        (rep.get("dispcnt") and int(rep["dispcnt"], 16) & 0x1000,
         "it booted with sprites enabled (dispcnt=%s)" % rep.get("dispcnt")),
        (rep["visible"] >= 1,
         "and something is on screen (%d OBJ)" % rep["visible"]),
        (t_check < 60, "check_project finished in %.1fs" % t_check),
    ]
    bad = [msg for okc, msg in checks if not okc]
    print("     scale: %d species + %d moves, %d rooms, %d objects; "
          "generated C %.1f MB, ROM %.2f MB; check %.1fs, build %.1fs; "
          "%d problem(s) reported"
          % (len(model["tables"][0]["rows"]), len(model["tables"][1]["rows"]),
             len(model["rooms"]), len(model["objects"]),
             csize / 1048576.0, size / 1048576.0, t_check, t_build,
             len(problems)))
    for q in problems[:3]:
        print("       reported: %s" % q[:100])
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  a Pokemon-sized project builds, fits, and boots" % name)
    return True




# vbam's SDL frontend keeps one byte per key in `sdlButtons`, and
# systemReadJoypad builds REG_KEYINPUT from it — so a debugger can press
# buttons. Mapped empirically rather than assumed: index 0 turned out to be
# LEFT, not A, which is exactly the sort of thing a guessed constant gets
# wrong silently.
PAD = {"LEFT": 0, "RIGHT": 1, "UP": 2, "DOWN": 3, "A": 4, "B": 5,
       "START": 6, "SELECT": 7, "L": 8, "R": 9}


def press(pid, key, hold=0.25):
    """Hold a button long enough for the game to sample it, then release.

    The runtime samples input once per STEP, and rt_key_pressed reports an
    EDGE, so the press has to span at least one step and the release has to
    actually happen or the next press is not an edge at all."""
    idx = PAD[key]
    for value in (1, 0):
        subprocess.run(["gdb", "-p", str(pid), "-batch",
                        "-ex", "set *(unsigned char*)((char*)&sdlButtons+%d) = %d"
                        % (idx, value),
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        if value:
            time.sleep(hold)
    time.sleep(0.15)


def pokemon_scene():
    """The Pokémon test's SYSTEMS, composed into one cartridge.

    Everything below has been proven individually. What has never been shown
    is them working together in a single ROM, which is the only form the
    Definition of Done actually asks for:

      * a nested MENU tree -- a top menu whose choice opens a second menu
      * BRANCHING DIALOGUE driven by that choice
      * a DATA TABLE lookup naming the species the branch picked
      * the REAL-TIME CLOCK read and its answer used
      * the LINK CABLE opened and its state reported
      * a 128 KB FLASH save written and read back in the same run

    A phase probe records how far the chain got, so a failure says WHICH
    system broke the composition rather than that something did."""
    hero = [0x7FFF if 4 <= (i % 16) < 12 and 4 <= (i // 16) < 12 else 0x7C1F
            for i in range(256)]
    species = [["Bulbaclone", 45, 49, 12], ["Charclone", 39, 52, 10],
               ["Squirtclone", 44, 48, 11], ["Pikaclone", 35, 55, 13]]
    script = r"""
volatile s32 probe_phase;
volatile s32 probe_choice;
volatile s32 probe_species_hp;
volatile s32 probe_rtc;
volatile s32 probe_link;
volatile s32 probe_saved;

static const char *const top_items[] = { "FIGHT", "PARTY", "RUN" };
static const char *const party_items[] = { "Bulbaclone", "Charclone",
                                           "Squirtclone", "Pikaclone" };
static s32 stage;

void battle_start(void)
{
    nb_DateTime now;
    probe_phase = 1;
    /* The clock and the cable are read up front, the way a real game checks
       what this cartridge and this session can do. */
    probe_rtc = rt_rtc_read(&now) ? (s32)now.year : -1;
    probe_link = rt_link_open(SIO_9600);
    rt_menu_open(top_items, 3, 2, 10, 10);
    stage = 1;
}

void battle_step(void)
{
    int r;
    if (stage == 1) {
        r = rt_menu_step();
        if (r == 0) {                  /* FIGHT -> the nested menu */
            probe_choice = r;
            rt_menu_open(party_items, 4, 12, 8, 14);
            stage = 2;
            probe_phase = 2;
        } else if (r >= 0) {
            probe_choice = r;
            stage = 3;
            probe_phase = 2;
        }
        return;
    }
    if (stage == 2) {
        r = rt_menu_step();
        if (r >= 0) {
            /* The table lookup: the chosen row's data drives what happens. */
            probe_species_hp = nb_species[r].HP;
            nb_score = nb_species[r].Atk;
            probe_phase = 3;
            stage = 3;
        }
        return;
    }
    if (stage == 3) {
        rt_game_save();
        nb_score = 0;
        if (rt_game_load() && nb_score != 0) probe_saved = nb_score;
        probe_phase = 4;
        stage = 4;
    }
}
"""
    return {
        "name": "Battle",
        "save_type": "flash128",
        "sprites": [{"id": "spr_hero", "name": "Hero", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [hero]}],
        "tilesets": [], "sounds": [],
        "tables": [{"id": "species", "columns": [
            {"name": "Name", "type": "text"}, {"name": "HP", "type": "int"},
            {"name": "Atk", "type": "int"}, {"name": "Type", "type": "int"}],
            "rows": species}],
        "scripts": [{"id": "scr", "name": "Battle", "code": script}],
        "objects": [{"id": "obj_hero", "name": "Trainer", "sprite": "spr_hero",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "hurt_frames": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "set_var", "var": "picked", "value": 0},
                             {"kind": "execute_code", "lang": "C",
                              "code": "battle_start();"}]},
                         {"type": "step", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "battle_step();"},
                             # Branching dialogue: the say fires only down the
                             # branch the menu chose, which is what makes this
                             # a composition and not two features side by side.
                             {"kind": "if_var", "var": "picked", "op": "==",
                              "value": 0, "children": [
                                  {"kind": "say",
                                   "text": "A wild clone blocks the path.{p}"
                                           "It looks entirely unimpressed."},
                                  {"kind": "set_var", "var": "picked",
                                   "value": 1}]}]}]}],
        "rooms": [{"id": "rm", "name": "Arena", "w": 240, "h": 160,
                   "speed": 60, "bg": "#182838", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_hero", "x": 120, "y": 40}]}],
        "start_room": "rm",
    }


def run_pokemon():
    name = "pokemon"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = pokemon_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-400:]))
        return False
    names = {"probe_phase", "probe_choice", "probe_species_hp", "probe_rtc",
             "probe_link", "probe_saved"}
    offs = _offsets(os.path.join(out, "game.elf"), names)
    if len(offs) != len(names):
        print("FAIL %s: probes missing: %s" % (name, sorted(names - set(offs))))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(2.0)
        early = _read_ints(proc.pid, offs)
        # CAPTURE FIRST, while the menu is still up and the dialogue is still
        # typing. The presses below play the menu to completion and close it;
        # taking the picture afterwards photographs an empty screen, which is
        # what the first version of this did and then reported as the menu and
        # dialogue failing to coexist.
        shot = os.path.join(out, "frame.png")
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)systemScreenCapture(0)",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        time.sleep(0.4)
        auto = os.path.join(out, "game00.png")
        if os.path.exists(auto):
            os.replace(auto, shot)
        # Then play it: FIGHT on the top menu, then the third party member on
        # the nested one. Without this the chain stops at the first menu and
        # the nested menu, the table lookup and the flash round trip are never
        # reached -- the slice would pass having proven a third of itself.
        press(proc.pid, "A")               # FIGHT (first row)
        press(proc.pid, "DOWN")
        press(proc.pid, "DOWN")
        press(proc.pid, "A")               # Squirtclone (row 2)
        time.sleep(0.8)
        late = _read_ints(proc.pid, offs)
    finally:
        proc.kill()
        proc.wait()
    checks = [
        (early.get("probe_phase", 0) >= 1,
         "the battle chain started (phase=%r)" % early.get("probe_phase")),
        (early.get("probe_rtc") is not None,
         "the real-time clock was consulted (year=%r)" % early.get("probe_rtc")),
        (early.get("probe_link") is not None,
         "the link cable was opened (%r)" % early.get("probe_link")),
        (os.path.exists(shot) and region_activity(shot, 60, 130) >= 0.03,
         "the menu and dialogue are on screen together"),
        (late.get("probe_phase", 0) >= 3,
         "the NESTED menu resolved (phase=%r)" % late.get("probe_phase")),
        (late.get("probe_species_hp") == 44,
         "the chosen row's data was read from the table (HP=%r, want 44)"
         % late.get("probe_species_hp")),
        (late.get("probe_phase", 0) >= 4,
         "the chain reached the save"),
        (late.get("probe_saved") == 48,
         "128 KB flash wrote and read back the battle state (%r, want 48)"
         % late.get("probe_saved")),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  nested menu played to a table lookup (HP %s) and a flash "
          "round trip (%s), with dialogue, RTC(%s) and link(%s) in ONE cartridge"
          % (name, late.get("probe_species_hp"), late.get("probe_saved"),
             early.get("probe_rtc"), early.get("probe_link")))
    return True




def affine_dialogue_scene():
    """Dialogue INSIDE an affine room — the layer trade, actually tested.

    The spec claims an affine room keeps its dialogue because BG0 and BG1 swap
    duties, putting the text on the lower-numbered layer so it still sits
    above its box. That claim was written from the hardware manual and never
    run. Mode 1 offers two text backgrounds where mode 0 offers four, so if
    the swap is wrong the panel eats the text, the text vanishes, or both go
    missing behind the turning ground."""
    a = [0x7C00] * 64
    b = [0x03E0] * 64
    amap = [1 + ((x + y) & 1) for y in range(32) for x in range(32)]
    script = r"""
static s32 spin;
void ground_turn(void)
{
    spin = (spin + 2) & 255;
    rt_bg_affine(2, 64, 64, 120, 80, spin, 256);
}
"""
    return {
        "name": "Mode 7 talk",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": [],
        "affine_tileset": {"tiles": [[0] * 64, a, b]},
        "scripts": [{"id": "scr", "name": "Ground", "code": script}],
        "objects": [{"id": "obj_g", "name": "Pilot", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "hurt_frames": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "say",
                              "text": "The floor turns beneath the pillars.{p}"
                                      "Nothing here is level for long."}]},
                         {"type": "step", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "ground_turn();"}]}]}],
        "rooms": [{"id": "rm", "name": "Tilt", "w": 240, "h": 160,
                   "speed": 60, "bg": "#000010", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "affine": amap,
                   "instances": [{"object": "obj_g", "x": 120, "y": 40}]}],
        "start_room": "rm",
    }


def run_affine_dialogue():
    name = "affine_talk"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = affine_dialogue_scene()
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
    dc = int(rep.get("dispcnt", "0"), 16)
    w, h, rows = decode_png(shot)
    # The panel is a solid block of one colour; the glyphs on it are a second.
    # Count how many DISTINCT colours with real area appear in the lower rows:
    # ground alone gives two (the checker), a panel adds a third, and text on
    # the panel a fourth. Fewer than four means something was eaten.
    from collections import Counter
    seen = Counter()
    for y in range(104, min(h, 152)):
        r = rows[y]
        for x in range(0, w * 3, 3):
            seen[(r[x], r[x + 1], r[x + 2])] += 1
    strong = [c for c, n in seen.items() if n >= 80]
    checks = [
        ((dc & 7) == 1, "the room is in affine mode 1 (dispcnt=%04x)" % dc),
        (dc & 0x0300 == 0x0300, "both text layers are on for panel and text"),
        (len(strong) >= 4,
         "ground, panel and glyphs are all present in the lower rows "
         "(%d distinct colours: %s)" % (len(strong), sorted(strong)[:5])),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  dialogue survives the affine layer trade (%d colours, "
          "dispcnt=%04x)" % (name, len(strong), dc))
    return True




def roomswap_scene():
    """Walking affine -> flat -> affine, which nothing has exercised.

    Entering an affine room switches the display to mode 1 and gives BG0 and
    BG1 new duties; leaving must put all of it back, or the flat room inherits
    a mode and a layer assignment meant for the other kind. The failure would
    be a room that renders its ground through the wrong layer, or a dialogue
    panel that stops appearing after the first affine room -- and nothing in
    a single-room test can see it."""
    a = [0x7C00] * 64
    b = [0x03E0] * 64
    tile = [0x2E6A if (i // 8) % 2 else 0x1A45 for i in range(64)]
    amap = [1 + ((x + y) & 1) for y in range(32) for x in range(32)]
    script = r"""
volatile s32 probe_mode_a;   /* inside the first affine room */
volatile s32 probe_mode_b;   /* inside the flat room */
volatile s32 probe_mode_c;   /* back inside an affine room */
volatile s32 probe_t;
static s32 t;

/* The GAME records the mode at three known moments, well clear of the
   room-change fade. Sampling from outside raced that fade and attributed a
   transitional mode to the wrong room -- the first version of this reported
   every room as both modes at once, which was the harness, not the runtime.
   A timing decision belongs where the timing is deterministic. */
void hop_step(void)
{
    t++;
    probe_t = t;
    if (t == 60)  probe_mode_a = rt_video_mode_get() + 1;
    if (t == 90)  rt_room_goto(NB_ROOM_FLAT);
    if (t == 150) probe_mode_b = rt_video_mode_get() + 1;
    if (t == 180) rt_room_goto(NB_ROOM_TILT);
    if (t == 240) probe_mode_c = rt_video_mode_get() + 1;
}
"""
    obj = {"id": "obj_h", "name": "Walker", "sprite": "spr_soul",
           "visible": True, "solid": False, "tilecol": 0, "depth": 0,
           "bb_inset": 0, "hurt_frames": 0, "events": [
               {"type": "step", "actions": [
                   {"kind": "execute_code", "lang": "C",
                    "code": "hop_step();"}]}]}
    return {
        "name": "Rooms",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [_soul()]}],
        "tilesets": [{"id": "ts", "name": "Flat", "size": 8,
                      "solid": [False] * 16, "auto_base": 0,
                      "tiles": [tile] * 16}],
        "tables": [], "sounds": [],
        "affine_tileset": {"tiles": [[0] * 64, a, b]},
        "scripts": [{"id": "scr", "name": "Hop", "code": script}],
        "objects": [obj],
        "rooms": [
            {"id": "rm_tilt", "name": "Tilt", "w": 240, "h": 160, "speed": 60,
             "bg": "#000010", "tiles": None, "far": None, "far_div": 2,
             "edge_open": True, "warps": [], "affine": amap,
             "instances": [{"object": "obj_h", "x": 120, "y": 80}]},
            {"id": "rm_flat", "name": "Flat", "w": 240, "h": 160, "speed": 60,
             "bg": "#301818",
             "tiles": [1] * (30 * 20), "far": None, "far_div": 2,
             "edge_open": True, "warps": [],
             "instances": [{"object": "obj_h", "x": 120, "y": 80}]},
        ],
        "start_room": "rm_tilt",
    }


def run_roomswap():
    name = "roomswap"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = roomswap_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-400:]))
        return False
    offs = _offsets(os.path.join(out, "game.elf"),
                    {"probe_mode_a", "probe_mode_b", "probe_mode_c", "probe_t"})
    if len(offs) != 4:
        print("FAIL %s: probes missing: %s" % (name, offs))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # Long enough for all three marks; the probes are written by the ROM,
        # so this only has to outlast t=240 at 60 steps a second.
        time.sleep(6.0)
        v = _read_ints(proc.pid, offs)
    finally:
        proc.kill()
        proc.wait()
    # Probes hold mode+1 so that 0 means "this mark was never reached".
    a, b, c = (v.get("probe_mode_a"), v.get("probe_mode_b"),
               v.get("probe_mode_c"))
    checks = [
        (v.get("probe_t", 0) > 240,
         "the tour ran to its end (t=%r)" % v.get("probe_t")),
        (a == 2, "the first affine room is in mode 1 (%r)" % a),
        (b == 1, "the flat room went BACK to mode 0 (%r)" % b),
        (c == 2, "and the affine room re-entered mode 1 (%r)" % c),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  affine -> flat -> affine restores the display mode each way"
          % name)
    return True




def hiscore_scene(save_type):
    """The high score must survive alongside the save slot, on every backend.

    It lives in the same block as the save data, and on FLASH that block is
    rewritten whole -- an erase wipes the sector, so rt_game_save has to carry
    the score across it. Get that wrong and a player's best score disappears
    the next time the game saves, which is the quietest kind of data loss:
    nothing errors, a number is just smaller than it was.

    Both directions are checked because they are separate code paths: a save
    must not eat the score, and a score submit must not eat the save."""
    return {
        "name": "Best",
        "save_type": save_type,
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": [],
        "scripts": [{"id": "scr", "name": "Probes",
                     "code": "volatile s32 probe_best;\n"
                             "volatile s32 probe_score;\n"
                             "volatile s32 probe_lives;\n"
                             "volatile s32 probe_stage;\n"}],
        "objects": [{"id": "obj_k", "name": "Keeper", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "hurt_frames": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              # 1: submit a best score. 2: an ordinary save
                              # with different data, which on FLASH erases the
                              # whole sector. 3: the best must have survived
                              # that save. 4: and the save must have survived
                              # the submit -- separate code paths, so both
                              # directions are checked.
                              "code":
                              "nb_score = 9000; rt_highscore_submit(); "
                              "nb_score = 42; nb_lives = 7; rt_game_save(); "
                              "probe_best = rt_highscore(); "
                              "nb_score = 0; nb_lives = 0; rt_game_load(); "
                              "probe_score = nb_score; probe_lives = nb_lives; "
                              "probe_stage = 1;"}]}]}],
        "rooms": [{"id": "rm", "name": "Vault", "w": 240, "h": 160,
                   "speed": 60, "bg": "#202028", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_k", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_hiscore(save_type="sram"):
    name = "hiscore_" + save_type
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = hiscore_scene(save_type)
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
                    {"probe_best", "probe_score", "probe_lives", "probe_stage"})
    if len(offs) != 4:
        print("FAIL %s: probes missing: %s" % (name, offs))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(4.0)
        v = _read_ints(proc.pid, offs)
    finally:
        proc.kill()
        proc.wait()
    checks = [
        (v.get("probe_stage") == 1,
         "the sequence completed (stage=%r)" % v.get("probe_stage")),
        (v.get("probe_best") == 9000,
         "the best score survived a later save (%r, want 9000)"
         % v.get("probe_best")),
        (v.get("probe_score") == 42 and v.get("probe_lives") == 7,
         "and the save survived the score submit (score=%r lives=%r, "
         "want 42/7)" % (v.get("probe_score"), v.get("probe_lives"))),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  best 9000 and save 42/7 coexist on %s" % (name, save_type))
    return True




def linkless_scene():
    """The link API with NO CABLE, hammered every frame.

    Link EXCHANGE is unprovable here -- the vendored VBA-M has no link
    emulation compiled in, so a second unit cannot exist, and that limit is
    recorded rather than papered over. What is provable is the failure mode a
    real player actually meets: a game that polls the cable it does not have.
    The header promises "nothing blocks"; this holds it to that with the
    whole API called every frame for five seconds, asserting the game still
    steps one-for-one with the VBlank and every call returns something sane
    for a lone unit."""
    script = r"""
volatile s32 probe_steps;
volatile s32 probe_open;
volatile s32 probe_ready;
volatile s32 probe_id;
volatile s32 probe_recv;
volatile s32 probe_started;

void cable_step(void)
{
    probe_steps++;
    if (probe_steps == 1)
        probe_open = rt_link_open(SIO_9600);
    rt_link_send((u16)probe_steps);
    probe_started = rt_link_start();       /* lone unit: must refuse or no-op */
    rt_link_poll();
    probe_ready = rt_link_ready();
    probe_id = rt_link_id();
    probe_recv = rt_link_recv(1);          /* nobody there: 0xFFFF promised */
    rt_link_busy();
    if (probe_steps == 280) rt_link_close();
}
"""
    return {
        "name": "No cable",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [_soul()]}],
        "tilesets": [], "tables": [], "sounds": [],
        "scripts": [{"id": "scr", "name": "Cable", "code": script}],
        "objects": [{"id": "obj_c", "name": "Caller", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "hurt_frames": 0, "events": [
                         {"type": "step", "actions": [
                             {"kind": "execute_code", "lang": "C",
                              "code": "cable_step();"}]}]}],
        "rooms": [{"id": "rm", "name": "Alone", "w": 240, "h": 160,
                   "speed": 60, "bg": "#102018", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_c", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_linkless():
    name = "linkless"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = linkless_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-300:]))
        return False
    names = {"probe_steps", "probe_open", "probe_ready", "probe_id",
             "probe_recv", "probe_started", "g_frames"}
    offs = _offsets(os.path.join(out, "game.elf"), names)
    if len(offs) != len(names):
        print("FAIL %s: probes missing: %s" % (name, sorted(names - set(offs))))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(2.5)
        a = _read_ints(proc.pid, offs)
        time.sleep(2.5)
        b = _read_ints(proc.pid, offs)
    finally:
        proc.kill()
        proc.wait()
    dsteps = b.get("probe_steps", 0) - a.get("probe_steps", 0)
    dvbl = b.get("g_frames", 0) - a.get("g_frames", 0)
    ratio = (dsteps / dvbl) if dvbl else 0
    checks = [
        (b.get("probe_steps", 0) > 280,
         "the game kept stepping past the close (steps=%r)"
         % b.get("probe_steps")),
        (ratio > 0.97,
         "polling a missing cable costs no frames (%.3f steps/VBlank)" % ratio),
        (b.get("probe_ready") == 0,
         "a lone unit never reports the session ready (%r)"
         % b.get("probe_ready")),
        (b.get("probe_started") == 0,
         "a lone unit's start is refused (%r)" % b.get("probe_started")),
        (b.get("probe_recv") == 0xFFFF,
         "an absent unit reads as 0xFFFF as promised (%r)"
         % b.get("probe_recv")),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  the whole link API, no cable, %.3f steps/VBlank, every "
          "answer sane for a lone unit" % (name, ratio))
    return True




def tracker_scene():
    """The sequenced-music half of the audio row, which had execution proof
    nowhere: a tracker song with lead, bass and drums, asserted at the PSG
    registers. The lead's frequency register must CHANGE as the song advances
    -- a stuck sequencer holds one note forever and passes every check that
    only asks whether sound is on."""
    return {
        "name": "Song",
        "sprites": [{"id": "spr_soul", "name": "Soul", "w": 16, "h": 16,
                     "ox": 8, "oy": 8, "anim_speed": 0, "frames": [_soul()]}],
        "tilesets": [], "tables": [], "scripts": [],
        "sounds": [{"id": "snd_song", "name": "March", "tempo": 8,
                    "loop": True, "steps": 8,
                    "lead": [60, 64, 67, 72, 67, 64, 60, 55],
                    "bass": [36, 0, 43, 0, 36, 0, 43, 0],
                    "drum": [1, 0, 2, 0, 1, 0, 2, 4],
                    "kind": 0, "duty": 2, "vol": 0, "decay": 0, "prio": 0}],
        "objects": [{"id": "obj_m", "name": "Bard", "sprite": "spr_soul",
                     "visible": True, "solid": False, "tilecol": 0,
                     "depth": 0, "bb_inset": 0, "hurt_frames": 0, "events": [
                         {"type": "create", "actions": [
                             {"kind": "play_sound", "sound": "snd_song"}]}]}],
        "rooms": [{"id": "rm", "name": "Hall", "w": 240, "h": 160,
                   "speed": 60, "bg": "#181020", "tiles": None, "far": None,
                   "far_div": 2, "edge_open": True, "warps": [],
                   "instances": [{"object": "obj_m", "x": 120, "y": 80}]}],
        "start_room": "rm",
    }


def run_tracker():
    name = "tracker"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = tracker_scene()
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
    def psg():
        r = subprocess.run(
            ["gdb", "-p", str(proc.pid), "-batch",
             "-ex", 'printf "cntl=%04x s1x=%04x s2h=%04x s4h=%04x\n", '
             '*(unsigned short*)((char*)ioMem+0x80), '
             '*(unsigned short*)((char*)ioMem+0x64), '
             '*(unsigned short*)((char*)ioMem+0x6C), '
             '*(unsigned short*)((char*)ioMem+0x7C)',
             "-ex", "detach", "-ex", "quit"],
            capture_output=True, text=True, timeout=45)
        return dict(kv.split("=", 1) for ln in (r.stdout or "").splitlines()
                    for kv in ln.split() if "=" in kv)
    freqs = set()
    cntl = 0
    try:
        time.sleep(1.5)
        # The song steps every 8 frames; unequal gaps so no sampling cadence
        # can sit on a period -- the palette gate paid for this lesson.
        for k in range(8):
            v = psg()
            if v.get("s1x"):
                freqs.add(int(v["s1x"], 16) & 0x07FF)
            if v.get("cntl"):
                cntl = int(v["cntl"], 16)
            time.sleep(0.21 + 0.07 * k)
    finally:
        proc.kill()
        proc.wait()
    # SOUNDCNT_L: bits 0-2 and 4-6 are MASTER VOLUME; the channel routing
    # bits are 8-11 (right) and 12-15 (left). The first version of these
    # masks tested the volume bits -- two checks passed by coincidence and
    # the noise check failed by the same coincidence, on a register that
    # reads 0xFF77 with every channel routed. The same shape as reading
    # 0x7FE0 as yellow: asserting bits without decoding the layout.
    checks = [
        (cntl & 0x1100, "square 1 is routed to a speaker (SOUNDCNT_L=%04x)"
         % cntl),
        (cntl & 0x2200, "square 2 (the bass) is routed too"),
        (cntl & 0x8800, "and the noise channel for the drums"),
        (len(freqs) >= 3,
         "the lead's frequency register changes as the song advances "
         "(%d distinct values)" % len(freqs)),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  three PSG channels routed, %d lead notes observed -- the "
          "sequencer advances" % (name, len(freqs)))
    return True




def undertale_scene():
    """Part VII's second benchmark, composed: one cartridge that talks, then
    fights on a turning floor, hurts, forgives, sings, and saves.

    Room one is flat: a typewriter line with a voice blip. It warps into room
    two, whose ground is an affine layer being palette-cycled while a PCM
    soundtrack loops under mixed one-shot hits, rings of bones cost mercy-
    framed health, and surviving to the mark saves. Phase probes stage the
    whole run so a failure names the system that broke the composition:
      1 talking   2 arrived in the arena   3 waves running   4 saved."""
    a = [0x7C00] * 64
    b = [0x03E0] * 64
    bone = [0x7FFF if (i // 8) % 4 else 0 for i in range(64)]
    amap = [1 + ((x + y) & 1) for y in range(32) for x in range(32)]
    track = [((i * 3) % 160) - 80 for i in range(16384)]
    hit = [((i * 11) % 200) - 100 for i in range(4096)]
    script = r"""
volatile s32 probe_phase;
volatile s32 probe_talked;   /* sticky: 1 once the dialogue phase ran, ever */
volatile s32 probe_health;
volatile s32 probe_mixer;
static s32 t;

void talk_step(void)
{
    t++;
    if (t == 150) { probe_phase = 2; rt_room_goto(NB_ROOM_ARENA); }
}

void fight_setup(void)
{
    nb_health = 60;
    rt_play_sound(NB_SND_TRACK);
    rt_pal_cycle(0, 1, 2, 6);              /* the arena ground shimmers */
    probe_phase = 3;
}

void fire_ring(s32 n, s32 speed)
{
    s32 i;
    for (i = 0; i < n; i++) {
        Instance* v = rt_create(NB_OBJ_BONE, 120, 60);
        if (!v) break;
        rt_set_speed_dir(v, (i * 256) / n, speed);
    }
}

void fight_step(void)
{
    static s32 ft;
    ft++;
    rt_bg_affine(2, 64, 64, 120, 80, (ft * 2) & 255, 256);
    if ((ft & 31) == 0) fire_ring(10, 56);
    if ((ft & 63) == 0) rt_play_sound(NB_SND_HIT);
    { int v, n = 0;
      for (v = 0; v < 4; v++) n += rt_pcm_playing() ? 1 : 0;
      if (rt_pcm_playing() && rt_pcm_playing_b()) probe_mixer = 1; }
    probe_health = nb_health;
    if (ft == 300) { rt_game_save(); probe_phase = 4; }
}
"""
    return {
        "name": "Undertale slice",
        "save_type": "flash128",
        "sprites": [
            {"id": "spr_soul", "name": "Soul", "w": 16, "h": 16, "ox": 8,
             "oy": 8, "anim_speed": 0, "frames": [_soul()]},
            {"id": "spr_bone", "name": "Bone", "w": 8, "h": 8, "ox": 4,
             "oy": 4, "anim_speed": 0, "frames": [bone], "pal_bank": 2},
        ],
        "tilesets": [], "tables": [],
        "affine_tileset": {"tiles": [[0] * 64, a, b]},
        "sounds": [
            {"id": "snd_blip", "name": "Blip", "tempo": 8, "loop": False,
             "steps": 2, "lead": [72, 0], "bass": [0, 0], "drum": [0, 0],
             "kind": 0, "duty": 1, "vol": 8, "decay": 2, "prio": 4},
            {"id": "snd_track", "name": "Track", "tempo": 6, "loop": True,
             "steps": 4, "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
             "kind": 1, "duty": 0, "vol": 0, "decay": 0, "prio": 0,
             "pcm": track},
            {"id": "snd_hit", "name": "Hit", "tempo": 6, "loop": False,
             "steps": 4, "lead": [0] * 4, "bass": [0] * 4, "drum": [0] * 4,
             "kind": 1, "duty": 0, "vol": 0, "decay": 0, "prio": 5,
             "pcm": hit},
        ],
        "scripts": [{"id": "scr", "name": "Stage", "code": script}],
        "objects": [
            {"id": "obj_teller", "name": "Teller", "sprite": "spr_soul",
             "visible": True, "solid": False, "tilecol": 0, "depth": 0,
             "bb_inset": 0, "hurt_frames": 0, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "probe_phase = 1; probe_talked = 1;"},
                     {"kind": "say_voice", "sound": "snd_blip"},
                     {"kind": "say",
                      "text": "Despite everything, the floor is about to "
                              "move.{p}Hold on to something."}]},
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "talk_step();"}]}]},
            {"id": "obj_soul", "name": "Soul", "sprite": "spr_soul",
             "visible": True, "solid": False, "tilecol": 0, "depth": 0,
             "bb_inset": 2, "hurt_frames": 20, "events": [
                 {"type": "create", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "fight_setup();"}]},
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "fight_step();"}]},
                 {"type": "collision", "object": "obj_bone", "actions": [
                     {"kind": "add_health", "value": -2}]}]},
            {"id": "obj_bone", "name": "Bone", "sprite": "spr_bone",
             "visible": True, "solid": False, "tilecol": 0, "depth": 1,
             "bb_inset": 1, "events": [
                 {"type": "step", "actions": [
                     {"kind": "execute_code", "lang": "C",
                      "code": "if (self->x < -8 || self->x > 248 || "
                              "self->y < -8 || self->y > 168) "
                              "rt_destroy(self);"}]}]},
        ],
        "rooms": [
            {"id": "rm_talk", "name": "Hall", "w": 240, "h": 160,
             "speed": 60, "bg": "#101018", "tiles": None, "far": None,
             "far_div": 2, "edge_open": True, "warps": [],
             "instances": [{"object": "obj_teller", "x": 120, "y": 40}]},
            {"id": "rm_arena", "name": "Arena", "w": 240, "h": 160,
             "speed": 60, "bg": "#000008", "tiles": None, "far": None,
             "far_div": 2, "edge_open": True, "warps": [], "affine": amap,
             "instances": [{"object": "obj_soul", "x": 120, "y": 60}]},
        ],
        "start_room": "rm_talk",
    }


def run_undertale():
    name = "undertale"
    out = tempfile.mkdtemp(prefix="gbafix-%s-" % name)
    model = undertale_scene()
    problems = gbabuild.check_project(model)
    if problems:
        print("FAIL %s: check_project: %s" % (name, problems[:3]))
        return False
    built, rom, log = gbabuild.build_rom(model, out, runtime_dir=RT,
                                         toolchain_dir=find_toolchain_dir())
    if not built:
        print("FAIL %s: did not build: %s" % (name, (log or "")[-400:]))
        return False
    names = {"probe_phase", "probe_talked", "probe_health", "probe_mixer",
             "g_frames"}
    offs = _offsets(os.path.join(out, "game.elf"), names)
    if len(offs) != len(names):
        print("FAIL %s: probes missing: %s" % (name, sorted(names - set(offs))))
        return False
    from gba_run import find_vbam
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([find_vbam(), "--no-opengl", rom], env=env,
                            cwd=out, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    shot = os.path.join(out, "frame.png")
    try:
        # The dialogue room warps at its own t==150 (~2.5s of game time, but
        # the emulator runs faster than realtime here), so the talking phase
        # is only visible early. The trace showed phase 1 at 0.6s and already
        # 3 by 1.4s -- sample inside the window the game actually has.
        time.sleep(0.3)                       # mid-dialogue (short window)
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)systemScreenCapture(0)",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        time.sleep(0.4)
        auto = os.path.join(out, "game00.png")
        if os.path.exists(auto):
            os.replace(auto, shot)
        # Through the fight to the save. ft==300 is the mark; the earlier
        # trace hit phase 4 by ~5s wall, but sprite load varies the emu's
        # speed, so wait generously and confirm the phase advanced rather than
        # assuming a fixed offset.
        time.sleep(8.0)
        late = _read_ints(proc.pid, offs)
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "call (void)sdlWriteBattery()",
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
    finally:
        proc.kill()
        proc.wait()
    savs = [f for f in os.listdir(out) if f.endswith(".sav")]
    checks = [
        # A sticky latch, not the racing live phase: the dialogue room warps
        # to the arena in under a second of wall-clock (the emulator runs
        # several times faster than realtime headless), too tight to sample a
        # transient phase from outside. The ROM records that it happened. This
        # is the third time this session an outside sampler lost to the ROM's
        # own clock; the durable answer is always to latch inside the ROM.
        (late.get("probe_talked") == 1,
         "the dialogue phase ran (talked=%r)" % late.get("probe_talked")),
        (os.path.exists(shot) and region_activity(shot, 104, 152) >= 0.05,
         "the typewriter is on screen in the hall"),
        (late.get("probe_phase") == 4,
         "the run reached the save through the arena (phase=%r)"
         % late.get("probe_phase")),
        (late.get("probe_mixer") == 1,
         "one-shot hits mixed OVER the looping soundtrack"),
        (0 < late.get("probe_health", 0) < 60,
         "mercy frames kept a 60-health soul alive under constant rings "
         "(health=%r)" % late.get("probe_health")),
        (bool(savs), "the flash save was written (%s)" % (savs or "none")),
    ]
    bad = [msg for okc, msg in checks if not okc]
    if bad:
        print("FAIL %s: %s" % (name, "; ".join(bad)))
        return False
    print("PASS %s  talk -> turning arena -> mercy'd damage (%r hp) -> mixed "
          "audio -> flash save, one cartridge" % (name, late.get("probe_health")))
    return True


FIXTURES = {
    "dialogue": (dialogue_scene, 5.0, check_dialogue),
}
RUNNERS = {"bullet": run_bullet, "persist": run_persist, "encounter": run_encounter,
           "persist_flash": lambda: run_persist("persist_flash",
                                               "flash128", 131072),
           "persist_eeprom": lambda: run_persist("persist_eeprom",
                                                 "eeprom8k", 8192),
           "jukebox": run_jukebox, "palette": run_palette,
           "mortal": run_mortal, "objwin": run_objwin,
           "chorus": run_chorus,
           "power": run_power,
           "affine": run_affine,
           "palbank": run_palbank,
           "scale": run_scale,
           "pokemon": run_pokemon,
           "affine_talk": run_affine_dialogue,
           "roomswap": run_roomswap,
           "hiscore_sram": lambda: run_hiscore("sram"),
           "hiscore_flash": lambda: run_hiscore("flash128"),
           "hiscore_eeprom": lambda: run_hiscore("eeprom8k"),
           "linkless": run_linkless, "tracker": run_tracker,
           "undertale": run_undertale}


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
