#!/usr/bin/env python3
"""Headless preservation checks for Packages' shared removed-app store."""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import packages  # noqa: E402


def probe(path):
    app = packages.Packages.__new__(packages.Packages)
    app._removed_apps_path = lambda: path
    app._removed_apps = app._load_removed_apps()
    app.view = "installed"; app.sort_field = None; app.sort_desc = False
    app._save_view_prefs()
    return app


checks = []
with tempfile.TemporaryDirectory(prefix="packages-removed-") as root:
    path = os.path.join(root, "removed_apps.json")
    original = b'{"schema":2,"removed":{"Writer":true},"future":{"x":1}}'
    with open(path, "wb") as fh: fh.write(original)
    probe(path)
    asides = [os.path.join(root, name) for name in os.listdir(root)
              if name.startswith("removed_apps.json.damaged-")]
    checks.append((len(asides) == 1 and open(asides[0], "rb").read() == original,
                   "wrong-shape valid JSON is preserved before close-time save"))
    with open(path, encoding="utf-8") as fh: rebuilt = json.load(fh)
    checks.append((rebuilt.get("removed") == [],
                   "a fresh recognized preference store is written afterward"))

    blocked = os.path.join(root, "blocked.json")
    with open(blocked, "wb") as fh: fh.write(original)
    app = packages.Packages.__new__(packages.Packages)
    app._removed_apps_path = lambda: blocked
    app._removed_apps = app._load_removed_apps()
    app.view = "installed"; app.sort_field = None; app.sort_desc = False
    real_q = packages.nbapp.quarantine_unrecognized
    real_note = packages.nbapp.note_save_failure
    packages.nbapp.quarantine_unrecognized = lambda _path: None
    packages.nbapp.note_save_failure = lambda *_a: None
    try:
        saved = app._save_view_prefs()
    finally:
        packages.nbapp.quarantine_unrecognized = real_q
        packages.nbapp.note_save_failure = real_note
    checks.append((not saved and open(blocked, "rb").read() == original,
                   "failed quarantine blocks replacement of the only bytes"))

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
