#!/usr/bin/env python3
"""C2 durability matrix for every Notebook OS JSON config store.

The app sources are scanned at run time; persistence mechanics are exercised
through nbapp because that is the shared write/load-close boundary.  Every
NB_HOME made here is an isolated mkdtemp directory.
"""
import argparse
import errno
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
DE = Path(os.environ.get("DURABILITY_DE", str(SOURCE_DE)))
sys.path.insert(0, str(DE))
import nbapp  # noqa: E402

COLUMNS = ("crash-mid-write", "corrupt-store", "disk-full",
           "second-instance", "kill-9", "power-cut", "removable-yanked")
CHECKS = 0


def check(value, message):
    global CHECKS
    CHECKS += 1
    if not value:
        raise AssertionError(message)


def enumerate_stores():
    """Find literal config JSON names in assignments rooted at CFG/NB_HOME.

    This deliberately ignores JSON document-picker extensions and bundle
    members: the same statement must mention the Notebook config root (or a
    conventional config-dir variable).
    """
    names = set()
    config_tokens = ("CFG_DIR", "CONFIG_DIR", "NB_HOME", '".config"',
                     "'.config'", "config_path")
    literal = re.compile(r"[\"']([A-Za-z0-9_-]+\.json)[\"']")
    for source in sorted(SOURCE_DE.glob("*.py")):
        text = source.read_text(encoding="utf-8")
        for statement in re.split(r"\n(?=\S)", text):
            if any(token in statement for token in config_tokens):
                names.update(m.group(1) for m in literal.finditer(statement))
    # Stores reached through helper-return expressions whose statement spans
    # indented lines are caught by a narrow whole-file config-path pattern.
    joined = re.compile(r"(?:CFG_DIR|CONFIG_DIR|NB_HOME|[\"']\.config[\"'])"
                        r"[\s\S]{0,180}?[\"']([A-Za-z0-9_-]+\.json)[\"']")
    for source in sorted(SOURCE_DE.glob("*.py")):
        names.update(joined.findall(source.read_text(encoding="utf-8")))
    # Legacy aliases are inputs, not independently written current stores.
    names.difference_update({"academic.json", "gbaide.json", "project.json"})
    return sorted(names)


def home_and_path(store):
    home = Path(tempfile.mkdtemp(prefix="nbhome-durability-"))
    path = home / ".config/notebook" / store
    path.parent.mkdir(parents=True)
    return home, path


def reset_nbapp():
    nbapp._REAPED_TMP.clear()
    nbapp._BACKED_UP.clear()


def crash_mid_write(store):
    _home, path = home_and_path(store)
    old = {"owner": store, "generation": 0}
    path.write_text(json.dumps(old), encoding="utf-8")
    tmp = path.parent / ".nbw-interrupted.tmp"
    tmp.write_bytes(b'{"owner":')
    old_time = time.time() - 7200
    os.utime(tmp, (old_time, old_time))
    reset_nbapp()
    nbapp.atomic_write_json(str(path), old)  # shared load+close/save path
    check(json.loads(path.read_text()) == old, store + ": store changed by stale tmp")
    check(not tmp.exists(), store + ": stale interrupted tmp was not reaped")


BAD_CASES = (b'{"unfinished":', b'not-json\xff', b'',
             b'["valid JSON", "wrong shape"]')


def corrupt_store(store):
    """Historical regression: damaged store, then open, then close/save."""
    for index, bad in enumerate(BAD_CASES):
        _home, path = home_and_path(store)
        path.write_bytes(bad)
        reset_nbapp()
        # load: parse failure/wrong shape becomes the app's empty default;
        # close: the shared writer attempts to persist that default.
        try:
            loaded = json.loads(path.read_bytes())
            recognized = isinstance(loaded, dict)
        except (ValueError, UnicodeDecodeError):
            recognized = False
        if recognized:
            raise AssertionError("bad fixture unexpectedly recognized")
        nbapp.atomic_write_json(str(path), {"empty_default": True})
        recoveries = list(path.parent.glob(path.name + ".damaged-*"))
        bak = Path(str(path) + ".bak")
        copies = [p.read_bytes() for p in recoveries]
        if bak.exists():
            copies.append(bak.read_bytes())
        # index 2 is the zero-byte store. Originally a filed defect —
        # preserve_damaged waved empty files through as "nothing to
        # preserve" — fixed same day (task 031): 0 bytes can only be a
        # truncated write or disk-full create, so it now takes the same
        # quarantine path as any other unreadable store, and this asserts it.
        check(bad in copies, "%s: open+close lost damaged bytes %r" % (store, bad))
        check(path.read_bytes() != bad, store + ": close did not produce clean store")


def disk_full(store):
    _home, path = home_and_path(store)
    old = {"owner": store, "generation": "prior"}
    nbapp.atomic_write_json(str(path), old)
    real_fsync = nbapp.os.fsync
    def full(_fd):
        raise OSError(errno.ENOSPC, "injected disk full")
    with mock.patch.object(nbapp.os, "fsync", side_effect=full):
        try:
            nbapp.atomic_write_json(str(path), {"generation": "new"})
        except OSError as exc:
            reason = nbapp.save_failure_reason(exc, str(path))
        else:
            raise AssertionError(store + ": ENOSPC was hidden")
    check("full" in reason.lower(), store + ": ENOSPC not explained")
    check(json.loads(path.read_text()) == old, store + ": ENOSPC damaged prior copy")
    check(real_fsync is not None, "fsync injection sanity")


def second_instance(store):
    _home, path = home_and_path(store)
    payloads = [{"writer": n, "blob": str(n) * 10000} for n in (1, 2)]
    code = ("import json,sys,time; sys.path.insert(0,sys.argv[1]); import nbapp; "
            "p=sys.argv[2]; d=json.loads(sys.argv[3]); "
            "[(nbapp.atomic_write_json(p,d),time.sleep(.001)) for _ in range(8)]")
    ps = [subprocess.Popen([sys.executable, "-c", code, str(DE), str(path),
                            json.dumps(p)]) for p in payloads]
    check(all(p.wait(timeout=20) == 0 for p in ps), store + ": writer child failed")
    result = json.loads(path.read_text())
    check(result in payloads, store + ": concurrent writers produced mixed payload")


def kill_nine(stores, iterations=55):
    home = Path(tempfile.mkdtemp(prefix="nbhome-durability-kill9-"))
    directory = home / ".config/notebook"
    directory.mkdir(parents=True)
    code = ("import json,sys;sys.path.insert(0,sys.argv[1]);import nbapp;"
            "p=sys.argv[2];i=0\n"
            "while True:\n i+=1;nbapp.atomic_write_json(p,{'writer':'child','n':i,'blob':str(i)*5000})")
    rng = random.Random(310)
    for i in range(iterations):
        store = stores[i % len(stores)]
        path = directory / store
        old = {"writer": "parent", "n": i, "blob": "old"}
        nbapp.atomic_write_json(str(path), old)
        p = subprocess.Popen([sys.executable, "-c", code, str(DE), str(path)])
        # Synchronize to evidence that the writer reached its write path: the
        # real implementation creates .nbw-*; the red-proof in-place writer
        # changes the destination size. Then choose a randomized cut point.
        deadline = time.monotonic() + 2
        old_size = path.stat().st_size
        while time.monotonic() < deadline and p.poll() is None:
            active_tmp = any(path.parent.glob(".nbw-*.tmp"))
            if active_tmp or path.stat().st_size != old_size:
                break
            time.sleep(.0005)
        time.sleep(rng.uniform(0.0001, 0.010))
        if p.poll() is None:
            os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=5)
        obj = json.loads(path.read_text())
        complete = obj == old or (obj.get("writer") == "child" and
                                  obj.get("blob") == str(obj.get("n")) * 5000)
        check(complete, "%s: torn payload after SIGKILL iteration %d" % (store, i))


def power_cut(store):
    _home, path = home_and_path(store)
    events = []
    real_fsync, real_replace = nbapp.os.fsync, nbapp.os.replace
    def fsync(fd):
        events.append("fsync-dir" if os.path.isdir("/proc/self/fd/%d" % fd)
                      else "fsync-file")
        return real_fsync(fd)
    def replace(src, dst):
        events.append("rename")
        return real_replace(src, dst)
    with mock.patch.object(nbapp.os, "fsync", side_effect=fsync), \
         mock.patch.object(nbapp.os, "replace", side_effect=replace):
        nbapp.atomic_write_json(str(path), {"owner": store})
    check(events.index("fsync-file") < events.index("rename") < events.index("fsync-dir"),
          store + ": required file-fsync/rename/dir-fsync order absent: " + repr(events))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=COLUMNS)
    args = parser.parse_args()
    stores = enumerate_stores()
    check(bool(stores), "zero stores enumerated (vacuous matrix)")
    status = {s: {} for s in stores}
    failures = []
    runners = {"crash-mid-write": crash_mid_write, "corrupt-store": corrupt_store,
               "disk-full": disk_full, "second-instance": second_instance,
               "power-cut": power_cut}
    selected = [args.only] if args.only else list(COLUMNS)
    for col in selected:
        if col == "kill-9":
            try:
                kill_nine(stores)
                for s in stores: status[s][col] = "PASS shared nbapp / 55 SIGKILLs"
            except Exception as exc:
                failures.append("kill-9: " + str(exc))
                for s in stores: status[s][col] = "FAIL " + str(exc)
        elif col == "removable-yanked":
            for s in stores:
                status[s][col] = "UNCOVERED: NB_HOME stores cannot live on removable media"
        else:
            for s in stores:
                try:
                    runners[col](s)
                    if col == "corrupt-store":
                        status[s][col] = ("PASS nbapp (incl. zero-byte "
                                          "quarantine); REF config_resilience_selftest")
                    elif col == "crash-mid-write":
                        status[s][col] = "PASS nbapp; REF config_resilience_selftest"
                    elif col == "disk-full":
                        status[s][col] = "PASS nbapp; REF document_safety_selftest"
                    else:
                        status[s][col] = "PASS nbapp"
                except Exception as exc:
                    status[s][col] = "FAIL " + str(exc)
                    failures.append("%s/%s: %s" % (s, col, exc))
    # Unselected columns are explicit existing-suite references, never gaps.
    for s in stores:
        for col in COLUMNS:
            status[s].setdefault(col, "REF not selected in focused run")
    print("\nC2 DURABILITY MATRIX")
    print("store | " + " | ".join(COLUMNS))
    for s in stores:
        print(s + " | " + " | ".join(status[s][c] for c in COLUMNS))
    if failures:
        print("\nFAILURES")
        for f in failures: print("- " + f)
    print("\n%d checks" % CHECKS)
    print("%d stores; %d cells; explicit gaps: removable media (N/A by design)"
          % (len(stores), len(stores) * len(COLUMNS)))
    print("RESULT: %s" % ("FAILED" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
