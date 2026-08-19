#!/usr/bin/env python3
"""Display-free forward-compatible Packages/Finder store round trip."""
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import packages  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-packages-store-") as root:
    path = os.path.join(root, "removed_apps.json")
    Path(path).write_text(json.dumps({
        "removed": ["Maps"],
        "view": "installed",
        "future_policy": {"pinned": True},
    }))
    app = packages.Packages.__new__(packages.Packages)
    app._removed_apps_path = lambda: path
    app.view, app.sort_field, app.sort_desc = "installed", None, False
    app._save_failure_told = False
    app._removed_apps = app._load_removed_apps()
    assert app._save_view_prefs() is True
    saved = json.loads(Path(path).read_text())
    assert saved["future_policy"] == {"pinned": True}
    assert saved["removed"] == ["Maps"]
    print("PASS unknown shared-store fields survive an open/close round trip")

print("RESULT: ALL PASS")
