#!/usr/bin/env python3
"""Headless damaged-store preservation for Widget Settings."""
import os
import sys
import tempfile

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="widget-store-home-"))
import widgetsettings  # noqa: E402

failed = 0
def check(value, label):
    global failed
    print(("PASS " if value else "FAIL ") + label)
    failed += not value

with tempfile.TemporaryDirectory(prefix="widget-store-") as folder:
    store = os.path.join(folder, "widgets.json")
    old_store = widgetsettings.STORE
    widgetsettings.STORE = store
    try:
        with open(store, "w", encoding="utf-8") as fh:
            fh.write('{"tiles":{"schedule":false},')
        app = widgetsettings.WidgetSettings.__new__(widgetsettings.WidgetSettings)
        app._store_quarantine_pending = ""
        app._save_error = ""
        app.data, app.order = app._load()
        damaged = [p for p in os.listdir(folder)
                   if p.startswith("widgets.json.damaged-")]
        check(bool(damaged), "truncated widget settings are preserved")

        # Model an unwritable/failed quarantine: the original remains, so save
        # must not replace it with defaults.
        with open(store, "w", encoding="utf-8") as fh:
            fh.write("{broken again")
        app._store_quarantine_pending = "damaged"
        original = open(store, "rb").read()
        real_preserve = widgetsettings.nbapp.preserve_damaged
        widgetsettings.nbapp.preserve_damaged = lambda _path: None
        try:
            saved = app._save()
        finally:
            widgetsettings.nbapp.preserve_damaged = real_preserve
        check(saved is False, "failed preservation blocks replacement save")
        check(open(store, "rb").read() == original,
              "failed preservation keeps the original bytes")
    finally:
        widgetsettings.STORE = old_store

print("RESULT: %s" % ("PASS" if not failed else "FAILED"))
raise SystemExit(bool(failed))
