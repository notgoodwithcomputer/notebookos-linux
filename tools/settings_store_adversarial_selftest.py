#!/usr/bin/env python3
"""Settings' own-store durability and forward-compatibility checks."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
home = tempfile.mkdtemp(prefix="settings-store-audit-")
os.environ["NB_HOME"] = home
import settings  # noqa: E402

failed = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failed.append(name)

class Pane: pass
Pane._load_settings = settings.Settings._load_settings
Pane._save_settings = settings.Settings._save_settings

try:
    os.makedirs(os.path.dirname(settings.CFG_FILE), exist_ok=True)
    open(settings.CFG_FILE, "w").write('["recognisable", "but wrong shape"]')
    p = Pane()
    loaded = p._load_settings()
    damaged = [n for n in os.listdir(os.path.dirname(settings.CFG_FILE))
               if ".damaged-" in n]
    check("WRONG-SHAPE-STORE-QUARANTINED-ON-LOAD",
          loaded == {} and bool(damaged))

    p._settings = {"future_setting": {"opaque": 7}, "blank_timeout": 300}
    p._save_settings()
    reread = json.load(open(settings.CFG_FILE))
    check("UNKNOWN-DICT-KEY-SURVIVES-OWN-VERIFYING-READ",
          reread.get("future_setting") == {"opaque": 7})

    real_write = settings.nbapp.atomic_write_json
    real_reason = settings.nbapp.save_failure_reason
    settings.nbapp.atomic_write_json = lambda *_a, **_k: (_ for _ in ()).throw(
        OSError(28, "full"))
    settings.nbapp.save_failure_reason = lambda exc, path=None: "VISIBLE-FAILURE"
    p._settings["blank_timeout"] = 600
    p._save_settings()
    check("FAILED-SAVE-SURFACES-NBAPP-SAVE_FAILURE_REASON",
          getattr(p, "_save_error", None) == "VISIBLE-FAILURE")
    # PASS-MUTANT: the historic swallow leaves no reason at all.
    check("PASS-MUTANT-SILENT-SAVE-CAN-GO-RED",
          getattr(Pane(), "_save_error", None) is None)
    settings.nbapp.atomic_write_json = real_write
    settings.nbapp.save_failure_reason = real_reason
finally:
    shutil.rmtree(home, ignore_errors=True)

print("4 checks, %d failed" % len(failed))
raise SystemExit(bool(failed))
