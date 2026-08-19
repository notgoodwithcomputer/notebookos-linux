#!/usr/bin/env python3
"""gba_run — build a project if asked, EXECUTE the ROM, and report what drew.

Until 2026-08-07 nothing in this tree ever ran a ROM: every suite verified by
compiling and inspecting, and all of them stayed green across a bug that hung
every game at its first VBlankIntrWait. This tool is the executable form of the
lesson: the only vantage that can see that class of defect is a running frame.

    tools/gba_run.py game.gba                      run 4s, report, exit 0 iff a sprite is visible
    tools/gba_run.py game.gba --shot out.png       also capture the frame as a PNG
    tools/gba_run.py --project p.json out/         build p.json to out/ first, then run it
    tools/gba_run.py game.gba --seconds 8 --json   machine-readable report

Hardware-state facts it reports: visible hardware OAM entries (attr0's hide
bit), DISPCNT, IE/IF/IME, the BIOS IRQ vector at 0x03007FFC and the BIOS IF
mirror at 0x03007FF8 — the five numbers that located the no-draw bug.

Traps this tool exists to encode, each paid for once:
* vbam's globals (`oam`, `internalRAM`, `ioMem`) carry no debug types; when
  ATTACHED, `(char*)oam` evaluates to the pointer VALUE and works. Launched
  under gdb from the start they read nil until CPUInit has run.
* vbam dies with exit 0377 if gdb runs it through a shell with redirections;
  attaching to an already-running process avoids the whole problem.
* A flat single-colour frame is not "blank": it is the room's backdrop, which
  means the ROM IS running. FORCE_BLANK reads as solid near-white. Tell them
  apart by setting a distinctive backdrop in the project under test.
* Guest symbol addresses move between builds. Everything here that needs one
  reads it from the ELF of the build it is inspecting, never from a memory of
  a previous build.
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
RT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/gbaruntime")


def find_vbam():
    override = os.environ.get("NB_GBA_VBAM")
    for p in ([override] if override else []) + [
            os.path.join(ROOT, "buildroot/output/build/vbam-2.1.4/vbam"),
            os.path.join(ROOT, "buildroot/output/target/usr/bin/vbam")]:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def find_toolchain_dir():
    for base, _dirs, files in os.walk(os.path.join(ROOT, "vendor-dl")):
        if "arm-none-eabi-gcc" in files:
            return os.path.dirname(base)
    return None


def gdb_read(pid, exprs, timeout=45):
    """printf a list of (label, format, expression) from an attached gdb."""
    cmds = []
    for label, fmt, expr in exprs:
        cmds += ["-ex", 'printf "%s=%s\\n", %s' % (label, fmt, expr)]
    r = subprocess.run(["gdb", "-p", str(pid), "-batch"] + cmds
                       + ["-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=timeout)
    out = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _stop_process(proc):
    """Stop and reap the emulator child owned by this invocation."""
    if proc.poll() is None:
        proc.kill()
    # kill() only requests termination. wait() is what releases the process
    # table entry and closes Popen's child-side bookkeeping on every path.
    proc.wait()


def _cleanup_run(proc, work):
    try:
        _stop_process(proc)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_rom(rom, seconds, shot=None):
    vb = find_vbam()
    if not vb:
        raise SystemExit("no host vbam (build the vbam package, or set NB_GBA_VBAM)")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    proc = subprocess.Popen([vb, "--no-opengl", rom], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {"rom": rom, "seconds": seconds}
    work = tempfile.mkdtemp(prefix="gbarun-")
    oam_bin = os.path.join(work, "oam.bin")
    oam_data = b""
    try:
        time.sleep(seconds)
        state = gdb_read(proc.pid, [
            ("dispcnt", "%04x", '*(unsigned short*)((char*)ioMem+0x00)'),
            ("ie",      "%04x", '*(unsigned short*)((char*)ioMem+0x200)'),
            ("if",      "%04x", '*(unsigned short*)((char*)ioMem+0x202)'),
            ("ime",     "%04x", '*(unsigned short*)((char*)ioMem+0x208)'),
            ("biosif",  "%04x", '*(unsigned short*)((char*)internalRAM+0x7FF8)'),
            ("irqvec",  "%08x", '*(unsigned int*)((char*)internalRAM+0x7FFC)'),
        ])
        subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                        "-ex", "dump binary memory %s (char*)oam ((char*)oam)+1024"
                        % oam_bin,
                        "-ex", "detach", "-ex", "quit"],
                       capture_output=True, text=True, timeout=45)
        if shot:
            # vbam's screenshot is bound to an input button; calling its
            # implementation directly is the only headless route to a frame.
            subprocess.run(["gdb", "-p", str(proc.pid), "-batch",
                            "-ex", "call (void)systemScreenCapture(0)",
                            "-ex", "detach", "-ex", "quit"],
                           capture_output=True, text=True, timeout=45)
            time.sleep(0.5)
            auto = os.path.join(os.path.dirname(os.path.abspath(rom)),
                                os.path.basename(rom)[:-4] + "00.png")
            if os.path.exists(auto):
                os.replace(auto, shot)
                report["shot"] = shot
        if os.path.exists(oam_bin):
            with open(oam_bin, "rb") as fh:
                oam_data = fh.read()
    finally:
        _cleanup_run(proc, work)
    report.update(state)
    visible = []
    if oam_data:
        for k in range(0, min(len(oam_data), 1024), 8):
            a0, a1, a2 = struct.unpack_from("<HHH", oam_data, k)
            # OBJ_HIDE is attr0 bit9 with bit8 clear; anything else is drawn
            # (regular, affine, or affine-double-size).
            if (a0 & 0x0300) != 0x0200:
                visible.append({"slot": k // 8, "attr0": a0, "attr1": a1,
                                "attr2": a2})
    report["visible"] = len(visible)
    report["entries"] = visible[:16]
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", nargs="?", help=".gba to execute")
    ap.add_argument("--project", help="build this project JSON first")
    ap.add_argument("outdir", nargs="?", help="build output dir (with --project)")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--shot", help="write the current frame as a PNG here")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rom = a.rom
    if a.project:
        sys.path.insert(0, DE)
        import gbabuild                                    # noqa: E402
        with open(a.project, encoding="utf-8") as fh:
            model = json.load(fh)
        outdir = a.outdir or tempfile.mkdtemp(prefix="gbarun-build-")
        built, rom, log = gbabuild.build_rom(model, outdir, runtime_dir=RT,
                                             toolchain_dir=find_toolchain_dir())
        if not built:
            print("build failed:\n" + (log or "")[-800:])
            return 2

    if not rom:
        ap.error("a ROM path, or --project, is required")
    rep = run_rom(rom, a.seconds, shot=a.shot)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print("ran %s for %.1fs" % (os.path.basename(rom), a.seconds))
        print("  DISPCNT=%s  IE=%s IF=%s IME=%s" % (rep.get("dispcnt"),
              rep.get("ie"), rep.get("if"), rep.get("ime")))
        print("  IRQ vector=%s  BIOS IF mirror=%s" % (rep.get("irqvec"),
              rep.get("biosif")))
        print("  visible hardware sprites: %d" % rep["visible"])
        if rep.get("shot"):
            print("  frame written to %s" % rep["shot"])
    return 0 if rep["visible"] >= 1 else 2


if __name__ == "__main__":
    sys.exit(main())
