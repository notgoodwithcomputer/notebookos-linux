#!/usr/bin/env python3
"""End Program uses the process identity sampled into its visible row."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="sysmon-pid-"))
import sysmon  # noqa: E402


class Model:
    values = {0: "Writer", 1: 123, 6: "start-A"}
    def get_value(self, _it, col): return self.values[col]


class Selection:
    def get_selected(self): return Model(), object()


class Tree:
    def get_selection(self): return Selection()


app = sysmon.SystemMonitor.__new__(sysmon.SystemMonitor)
app.tree = Tree()
messages, confirms = [], []
app._flash = messages.append
app._confirm = lambda *args: confirms.append(args)
real_start = sysmon.proc_start_time
sysmon.proc_start_time = lambda _pid: "start-B"
try:
    app._end_process()
finally:
    sysmon.proc_start_time = real_start
assert not confirms and messages and "finished" in messages[-1]
print("PASS reused PID cannot reach End Program confirmation")
print("RESULT: PASS")
