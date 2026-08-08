#!/usr/bin/env python3
"""Adversarial checks for Settings backup preflight and read-back."""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="settings-backup-audit-")
import settings  # noqa: E402

failed = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failed.append(name)

class Stop:
    def hide(self): pass
    def set_sensitive(self, _v): pass

class Pane:
    pass

Pane._backup_verify = settings.Settings._backup_verify
Pane._backup_preflight = getattr(settings.Settings, "_backup_preflight",
                                 lambda _self, _free, _need: False)
Pane._free_bytes = settings.Settings._free_bytes
Pane._backup_worker = settings.Settings._backup_worker
Pane._backup_dest_dir = settings.Settings._backup_dest_dir
Pane._copy_error = settings.Settings._copy_error

root = tempfile.mkdtemp(prefix="settings-backup-readback-")
try:
    src = os.path.join(root, "source")
    dest = os.path.join(root, "dest")
    os.makedirs(src); os.makedirs(os.path.join(dest, "source"))
    open(os.path.join(src, "file"), "wb").write(b"GOOD")
    open(os.path.join(dest, "source", "file"), "wb").write(b"EVIL")
    p = Pane(); p._alive = True; p._bk_working = True; p._bk_stop = Stop()
    p._backup_sources = lambda: [src]
    p._update_backup_button = lambda: None
    p.results = []
    p._show_backup_result = lambda text, warn=False: p.results.append((text, warn))
    p._backup_verify(dest, 1, 4, 0)
    check("CORRUPTED-SAME-SIZE-COPY-FAILS-READBACK",
          p.results and p.results[-1][1] is True)

    check("EXACT-FREE-SPACE-PASSES-PREFLIGHT",
          settings.Settings._backup_preflight(4096, 4096))
    check("ONE-BYTE-SHORT-FAILS-PREFLIGHT",
          not settings.Settings._backup_preflight(4095, 4096))
    check("UNKNOWN-FREE-SPACE-FAILS-CLOSED",
          not settings.Settings._backup_preflight(None, 4096))
    real_statvfs = settings.os.statvfs
    class Stat:
        f_bavail = 9
        f_frsize = 512
    settings.os.statvfs = lambda _path: Stat()
    check("STATVFS-512-BYTE-REMAINDER-IS-NOT-ROUNDED-AWAY",
          p._free_bytes(dest) == 4608)
    settings.os.statvfs = real_statvfs

    class Job:
        def checkpoint(self): pass
        def progress(self, *_a): pass
    real_copy2 = settings.shutil.copy2
    real_run = settings.run
    settings.shutil.copy2 = lambda *_a, **_k: (_ for _ in ()).throw(
        OSError(28, "full mid-copy"))
    settings.run = lambda *_a, **_k: (0, "")
    result = p._backup_worker(Job(), dest, 4)
    settings.shutil.copy2 = real_copy2
    settings.run = real_run
    check("STICK-FILLS-MID-COPY-NEVER-REPORTS-SUCCESS",
          result.get("outcome") == "failed" and result.get("copied") == 0)

    # PASS-MUTANT: size/count-only verification certifies the corrupt fixture.
    same_metadata = (os.path.getsize(os.path.join(src, "file")) ==
                     os.path.getsize(os.path.join(dest, "source", "file")))
    check("PASS-MUTANT-METADATA-VERIFY-CAN-GO-RED", same_metadata)
finally:
    shutil.rmtree(root, ignore_errors=True)

print("7 checks, %d failed" % len(failed))
raise SystemExit(bool(failed))
