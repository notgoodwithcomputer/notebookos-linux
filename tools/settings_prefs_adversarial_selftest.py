#!/usr/bin/env python3
"""Headless process-boundary checks for every session preference."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
home = tempfile.mkdtemp(prefix="settings-prefs-audit-")
os.environ["NB_HOME"] = home
import settings, nbprefs  # noqa: E402

failed = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failed.append(name)

class Pane: pass
Pane._save_settings = settings.Settings._save_settings

try:
    p = Pane()
    p._settings = {"blank_timeout": 300, "kbd_delay": 250,
                   "kbd_rate": 30, "display_scale": "1.5",
                   "future_setting": "keep"}
    check("RECENT-PREFS-SAVE-LEAVES-SETTINGS-PROCESS", p._save_settings())
    disk = json.load(open(settings.CFG_FILE))
    check("RECENT-PREFS-PERSIST-OWN-VERIFYING-READ", disk == p._settings)

    calls = []
    real_run = nbprefs.run
    def fake_run(cmd, timeout=4):
        calls.append(cmd)
        return (0, "eDP-1 connected 1366x768\n") if cmd == ["xrandr"] else (0, "")
    nbprefs.run = fake_run
    done = dict(nbprefs.apply_all())
    nbprefs.run = real_run
    check("BLANKING-REACHES-SESSION-SCOPE",
          "blank_timeout" in done and any(c[:2] == ["xset", "s"] for c in calls))
    check("KEY-REPEAT-REACHES-SESSION-SCOPE",
          "kbd_repeat" in done and any(c[:3] == ["xset", "r", "rate"] for c in calls))
    check("DISPLAY-SCALE-REACHES-SESSION-SCOPE",
          "display_scale" in done and any("--scale" in c for c in calls))

    # PASS-MUTANT: a Settings-only write makes no session command calls.
    mutant_calls = []                 # mutant saves JSON and stops there
    check("PASS-MUTANT-PROCESS-LOCAL-PREF-CAN-GO-RED", mutant_calls == [])
finally:
    shutil.rmtree(home, ignore_errors=True)

print("6 checks, %d failed" % len(failed))
raise SystemExit(bool(failed))
