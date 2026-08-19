#!/usr/bin/env python3
"""Display-free preservation-failure checks for visible data apps."""
import os
from pathlib import Path
import sys
import tempfile
import types


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-visible-q-home-"))
import accounting  # noqa: E402
import bills  # noqa: E402
import maps  # noqa: E402
import workout  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-visible-q-") as root:
    # Bills must retain a valid foreign store when its protective move fails.
    path = os.path.join(root, "bills.json")
    original = b'{"foreign":"bill ledger"}'
    Path(path).write_bytes(original)
    old = bills.STORE
    bills.STORE = path
    app = bills.Bills.__new__(bills.Bills)
    app._quarantine_pending = False
    app._save_error = ""
    real_q = bills.nbapp.quarantine_unrecognized
    real_write = bills.nbapp.atomic_write_json
    writes = []
    bills.nbapp.quarantine_unrecognized = lambda _path: None
    bills.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        app.bills = app._load()
        app.sort = "due"
        assert app._save() is False
    finally:
        bills.nbapp.quarantine_unrecognized = real_q
        bills.nbapp.atomic_write_json = real_write
        bills.STORE = old
    assert Path(path).read_bytes() == original and not writes
    assert app._quarantine_pending is True and app._save_error
    print("PASS Bills blocks writes after failed store preservation")

    # Workout load records a failed protective move and save retries/aborts.
    path = os.path.join(root, "workout.json")
    Path(path).write_bytes(b'{"unfinished":')
    old = workout.STORE
    workout.STORE = path
    app = workout.Workout.__new__(workout.Workout)
    real_preserve = workout.nbapp.preserve_damaged
    real_q = workout.nbapp.quarantine_unrecognized
    real_write = workout.nbapp.atomic_write_json
    writes = []
    workout.nbapp.preserve_damaged = lambda _path: None
    workout.nbapp.quarantine_unrecognized = lambda _path: None
    workout.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        app.data = app._load()
        app._stamp_today_goal = lambda: None
        assert app._save() is False
    finally:
        workout.nbapp.preserve_damaged = real_preserve
        workout.nbapp.quarantine_unrecognized = real_q
        workout.nbapp.atomic_write_json = real_write
        workout.STORE = old
    wrote_map = any(args and args[0] == path for args in writes)
    assert Path(path).read_bytes() == b'{"unfinished":' and not wrote_map, \
        (Path(path).read_bytes(), writes)
    assert app._quarantine_pending is True and app._load_error
    print("PASS Workout blocks writes until history preservation succeeds")

    # Accounting must not fall through after a failed valid-shape quarantine.
    path = os.path.join(root, "accounting.json")
    original = b'{"foreign":"ledger"}'
    Path(path).write_bytes(original)
    old = accounting.TX_FILE
    accounting.TX_FILE = path
    ledger = accounting.Accounting.__new__(accounting.Accounting)
    ledger._quarantine_pending = True
    ledger._quarantine = lambda: False
    ledger._extra, ledger.opening, ledger.tx = {}, 0.0, []
    ledger._chart_shown = True
    ledger._save_warned = False
    ledger._flash = lambda _message: None
    real_write = accounting.nbapp.atomic_write_json
    writes = []
    accounting.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        assert ledger._autosave() is False
    finally:
        accounting.nbapp.atomic_write_json = real_write
        accounting.TX_FILE = old
    assert Path(path).read_bytes() == original and not writes
    print("PASS Accounting blocks writes after failed ledger preservation")

    # Maps must turn a parse failure into a retryable preservation state.
    home = os.path.join(root, "maps-home")
    path = os.path.join(home, ".config", "notebook", "maps.json")
    os.makedirs(os.path.dirname(path))
    Path(path).write_bytes(b'{"unfinished":')
    old_home = os.environ.get("NB_HOME")
    os.environ["NB_HOME"] = home
    view = maps.Maps.__new__(maps.Maps)
    real_preserve = maps.nbapp.preserve_damaged
    real_q = maps.nbapp.quarantine_unrecognized
    real_write = maps.nbapp.atomic_write_json
    writes = []
    maps.nbapp.preserve_damaged = lambda _path: None
    maps.nbapp.quarantine_unrecognized = lambda _path: None
    maps.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        assert view._load_cfg() == {} and view._cfg_writable is False
        view.pack = types.SimpleNamespace(path="/map.nbm2")
        view.cx = view.cy = 0.0
        view.scale = 1.0
        view._save_failure_told = False
        assert view._save_cfg() is False
    finally:
        maps.nbapp.preserve_damaged = real_preserve
        maps.nbapp.quarantine_unrecognized = real_q
        maps.nbapp.atomic_write_json = real_write
        if old_home is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = old_home
    wrote_map = any(args and args[0] == path for args in writes)
    assert Path(path).read_bytes() == b'{"unfinished":' and not wrote_map, \
        (Path(path).read_bytes(), writes)
    print("PASS Maps blocks writes until view-state preservation succeeds")

print("RESULT: ALL PASS")
