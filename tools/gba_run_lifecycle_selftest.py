#!/usr/bin/env python3
"""The GBA runner stops and reaps exactly the emulator child it owns."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "gba_run.py")
spec = importlib.util.spec_from_file_location("gba_run", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Process:
    def __init__(self, running):
        self.running = running
        self.kills = 0
        self.waits = 0

    def poll(self):
        return None if self.running else 0

    def kill(self):
        self.kills += 1
        self.running = False

    def wait(self):
        self.waits += 1
        return 0


running = Process(True)
module._stop_process(running)
assert running.kills == 1
assert running.waits == 1

exited = Process(False)
module._stop_process(exited)
assert exited.kills == 0
assert exited.waits == 1

work = tempfile.mkdtemp(prefix="gba-run-owned-")
open(os.path.join(work, "oam.bin"), "wb").close()
cleaned = Process(False)
module._cleanup_run(cleaned, work)
assert not os.path.exists(work)

source = open(PATH, encoding="utf-8").read()
finally_block = source[source.index("    finally:", source.index("def run_rom")):
                       source.index("    report.update", source.index("def run_rom"))]
assert "_cleanup_run(proc, work)" in finally_block

print("GBA RUN LIFECYCLE SELFTEST: 6 checks, all pass")
print("RESULT: ALL PASS")
