#!/usr/bin/env python3
"""Best-score reset and undo are truthful when persistence fails."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="g2048-best-"))
import g2048  # noqa: E402


def stand(best, undo, result):
    app = g2048.Game2048.__new__(g2048.Game2048)
    app.best, app._best_undo = best, undo
    app._save_best = lambda: result
    app._refresh = lambda: None
    return app


failed = stand(512, 128, False)
failed._do_reset_best()
assert (failed.best, failed._best_undo) == (512, 128)

ok = stand(512, None, True)
ok._do_reset_best()
assert (ok.best, ok._best_undo) == (0, 512)

failed_undo = stand(0, 512, False)
assert failed_undo.undo_reset_best() is False
assert (failed_undo.best, failed_undo._best_undo) == (0, 512)

ok_undo = stand(0, 512, True)
assert ok_undo.undo_reset_best() is True
assert (ok_undo.best, ok_undo._best_undo) == (512, None)
print("PASS best-score reset and undo roll back failed writes")
print("RESULT: PASS")
