#!/usr/bin/env python3
"""Failed protective moves must never be followed by replacement writes."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-quarantine-home-"))
import contacts  # noqa: E402
import cookbook  # noqa: E402


def exercise(module, path_name, save, label):
    with tempfile.TemporaryDirectory(prefix="nb-quarantine-") as root:
        path = os.path.join(root, "store.json")
        Path(path).write_bytes(b'{"foreign":"irreplaceable"}')
        old_path = getattr(module, path_name)
        setattr(module, path_name, path)
        real_replace = module.os.replace
        real_write = module.nbapp.atomic_write_json
        writes = []
        module.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("read only"))
        module.nbapp.atomic_write_json = lambda *_args: writes.append(_args)
        try:
            app = save()
        finally:
            module.os.replace = real_replace
            module.nbapp.atomic_write_json = real_write
            setattr(module, path_name, old_path)
        assert Path(path).read_bytes() == b'{"foreign":"irreplaceable"}', label
        assert not writes, (label, writes)
        assert app._quarantine_pending is True, label
        print("PASS %s keeps original bytes and retry state" % label)


def cookbook_app():
    app = cookbook.Cookbook.__new__(cookbook.Cookbook)
    app._quarantine_pending = True
    app._save_warned = False
    app._serialize = lambda: {"recipes": []}
    app._flash_status = lambda _message: None
    assert app._save_state() is False
    return app


def contacts_app():
    app = contacts.Contacts.__new__(contacts.Contacts)
    app._quarantine_pending = True
    app._save_warned = False
    app._extra = {}
    app.people = []
    app._flash = lambda _message: None
    assert app._save() is False
    return app


exercise(cookbook, "COOKBOOK_FILE", cookbook_app, "Cookbook")
exercise(contacts, "CONTACTS_FILE", contacts_app, "Contacts")
print("RESULT: ALL PASS")
