#!/usr/bin/env python3
"""
Headless write-safety selftest for locale.json — the OS's most global config.

$NB_HOME/.config/notebook/locale.json holds `lang`, `keyboard` and
`login_keyboard`: the interface language, the layout session.sh loads at every
boot, and the half of a dual layout the sign-in screen starts on. Losing it is
not a cosmetic reset — a machine set up in Russian comes back in English on a
Cyrillic-first keyboard, and nothing ever re-asks (see nbi18n._KB_ALIASES).

Every other store in this OS is written by nbapp.atomic_write_json, which
fsyncs, uses a unique temp name and calls preserve_damaged() first. locale.json
is the exception: nbi18n._update_locale hand-rolls the write, because nbi18n
must stay importable on a machine whose de/ tree is damaged (login.py imports it
before anything else) and so cannot pull in nbapp/Gtk. This test holds that
hand-rolled writer to the same contract.

Pure stdlib: no Gtk, no display, no network. Run as:

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/locale_write_selftest.py
"""
import glob
import json
import os
import shutil
import stat
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

# $NB_LANG outranks the file for reads, so a caller who has it set would mask
# every language assertion below.
os.environ.pop("NB_LANG", None)

import nbi18n  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        FAILS.append(msg)


def fresh_home(seed=None):
    """A throwaway $NB_HOME, optionally seeded with raw locale.json bytes."""
    home = tempfile.mkdtemp(prefix="nb-locale-")
    os.environ["NB_HOME"] = home
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    if seed is not None:
        with open(os.path.join(cfg, "locale.json"), "w") as fh:
            fh.write(seed)
    return home, os.path.join(cfg, "locale.json")


HEALTHY = ('{"lang": "ru", "keyboard": "ru,us", "login_keyboard": "us", '
           '"unknown_future_key": 7}')
# What a locale.json looks like after a write that did not finish: valid-looking
# up to the point the bytes stopped.
TRUNCATED = '{"lang": "ru", "keyboard": "ru,us", "login_keyb'


# ---------------------------------------------------------------------------
def case_damaged_store_is_preserved():
    """A store that will not parse must not be silently thrown away.

    The user's keyboard and sign-in layout are in those bytes. _update_locale
    starts from {} when the parse fails and then replaces the file, so the only
    copy of "ru,us" is gone — with nothing on disk to recover it from. This is
    the case nbapp.preserve_damaged exists for at all ~22 other stores.
    """
    print("case: damaged locale.json is preserved, not overwritten")
    home, path = fresh_home(TRUNCATED)
    try:
        ok = nbi18n.set_lang("fr")
        check(ok is True, "set_lang reports success")
        with open(path) as fh:
            data = json.load(fh)
        check(data.get("lang") == "fr", "the new language is on disk")
        kept = glob.glob(path + ".damaged-*")
        check(bool(kept), "the unreadable bytes are kept aside (.damaged-*)")
        if kept:
            with open(kept[0]) as fh:
                check(fh.read() == TRUNCATED,
                      "the preserved copy holds the original bytes verbatim")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def case_unrelated_keys_survive():
    """An ordinary update touches one key and leaves every other one alone,
    including keys this version of nbi18n knows nothing about."""
    print("case: an ordinary update preserves unrelated keys")
    home, path = fresh_home(HEALTHY)
    try:
        check(nbi18n.set_keyboard("fr") is True, "set_keyboard reports success")
        with open(path) as fh:
            data = json.load(fh)
        check(data.get("keyboard") == "fr", "the keyboard was updated")
        check(data.get("lang") == "ru", "lang survived")
        check(data.get("login_keyboard") == "us", "login_keyboard survived")
        check(data.get("unknown_future_key") == 7,
              "a key this version does not know survived")
        # The lock file is a permanent sibling by design (see _lock_locale);
        # a temp file, a .bak or a quarantine copy would all be litter here.
        strays = [os.path.basename(q) for q in glob.glob(path + "*")
                  if q != path and not q.endswith(".lock")]
        check(not strays, "no temp or leftover file remains: %r" % (strays,))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def case_unencodable_update_fails_safely():
    """A caller's bad value obeys the same false/no-replacement contract."""
    print("case: an unencodable update reports failure without touching state")
    home, path = fresh_home(HEALTHY)
    circular = []
    circular.append(circular)
    try:
        check(nbi18n._update_locale(keyboard=circular) is False,
              "JSON encoding failure is returned, not raised")
        with open(path) as fh:
            check(fh.read() == HEALTHY,
                  "the healthy live locale file remains byte-for-byte intact")
        strays = [p for p in glob.glob(path + "*")
                  if p != path and not p.endswith(".lock")]
        check(not strays, "the failed writer removes its private temp file")
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------------------
# The interleaving harness. json.dump is swapped for one that serialises the
# object into the writer's still-OPEN temp file and then parks the thread there,
# so the test — not the scheduler — decides who reaches os.replace first. The
# production code path is otherwise untouched: real open, real write, real
# rename.
class _PausingJson:
    def __init__(self, real, gate):
        self._real = real
        self._gate = gate

    def __getattr__(self, name):
        return getattr(self._real, name)

    def dump(self, obj, fh, **kw):
        self._real.dump(obj, fh, **kw)
        self._gate(threading.current_thread().name)


def case_concurrent_writers():
    """Two writers must never leave the live file unreadable, and must not
    drop each other's key.

    Settings persists a language and a layout; login.py persists the layout a
    sign-in succeeded on; firstrun and the installer write the same file. They
    are separate processes with no settings service between them (there is no
    session bus on this machine), so the file IS the synchronisation point.
    """
    print("case: interleaved writers leave a readable file and lose nothing")
    home, path = fresh_home('{"lang": "en", "keyboard": "us", '
                            '"login_keyboard": "us"}')
    arrived = {"A": threading.Event(), "B": threading.Event()}
    release = {"A": threading.Event(), "B": threading.Event()}
    results = {}

    def gate(name):
        if name not in arrived:
            return
        arrived[name].set()
        release[name].wait(5.0)

    real_json = nbi18n.json
    nbi18n.json = _PausingJson(real_json, gate)
    try:
        def write(name, fn, value):
            results[name] = fn(value)

        a = threading.Thread(target=write,
                             args=("A", nbi18n.set_lang, "fr"), name="A")
        b = threading.Thread(target=write,
                             args=("B", nbi18n.set_keyboard, "ru,us"), name="B")
        a.start()
        arrived["A"].wait(5.0)          # A is inside its half-written temp file
        b.start()
        # Release A inside the documented 200ms lock deadline. B must then read
        # A's committed value, merge its own key, and commit successfully.
        release["B"].set()
        release["A"].set()
        a.join(5.0)
        b.join(5.0)
        check(not a.is_alive() and not b.is_alive(), "both writers finished")
        check(results == {"A": True, "B": True},
              "both writers report committed success")
    finally:
        nbi18n.json = real_json

    try:
        with open(path) as fh:
            raw = fh.read()
        try:
            data = json.loads(raw)
            readable = isinstance(data, dict)
        except ValueError:
            data, readable = {}, False
        check(readable, "locale.json still parses after the interleave: %r"
              % (raw[:80],))
        check(data.get("lang") == "fr", "the language write survived")
        check(data.get("keyboard") == "ru,us", "the keyboard write survived")
        check(data.get("login_keyboard") == "us",
              "the key neither writer touched survived")
        strays = [p for p in glob.glob(path + "*")
                  if p != path and not p.endswith(".lock")]
        check(not strays, "no temp file left behind: %r" % (strays,))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def case_lock_timeout_fails_closed():
    """A contender past the bounded deadline fails without partial bytes."""
    print("case: a lock timeout fails closed without corrupting committed state")
    home, path = fresh_home('{"lang": "en", "keyboard": "us", '
                            '"login_keyboard": "us"}')
    arrived = threading.Event()
    release = threading.Event()
    results = {}

    def gate(name):
        if name == "A":
            arrived.set()
            release.wait(5.0)

    def write(name, fn, value):
        results[name] = fn(value)

    real_json = nbi18n.json
    nbi18n.json = _PausingJson(real_json, gate)
    try:
        a = threading.Thread(target=write,
                             args=("A", nbi18n.set_lang, "fr"), name="A")
        b = threading.Thread(target=write,
                             args=("B", nbi18n.set_keyboard, "ru,us"), name="B")
        a.start()
        arrived.wait(5.0)
        b.start()
        b.join(2.0)
        check(results.get("B") is False,
              "the timed-out writer reports failure")
        release.set()
        a.join(5.0)
        b.join(5.0)
        check(results.get("A") is True and not a.is_alive() and not b.is_alive(),
              "the lock owner commits and both writers finish")
    finally:
        release.set()
        nbi18n.json = real_json

    try:
        with open(path) as fh:
            data = json.load(fh)
        check(data == {"lang": "fr", "keyboard": "us",
                       "login_keyboard": "us"},
              "the owner's committed state remains intact")
        strays = [p for p in glob.glob(path + "*")
                  if p != path and not p.endswith(".lock")]
        check(not strays, "the timed-out writer leaves no temp file")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def case_unwritable_reports_failure():
    """A write that cannot happen says so, and leaves the old file intact."""
    print("case: an unwritable config directory fails safely")
    if os.geteuid() == 0:
        print("  SKIP  running as root: mode bits do not deny us")
        return
    home, path = fresh_home(HEALTHY)
    cfg = os.path.dirname(path)
    try:
        os.chmod(cfg, stat.S_IRUSR | stat.S_IXUSR)
        check(nbi18n.set_lang("fr") is False, "set_lang reports failure")
        os.chmod(cfg, stat.S_IRWXU)
        with open(path) as fh:
            data = json.load(fh)
        check(data.get("lang") == "ru", "the healthy file was left alone")
        check(data.get("keyboard") == "ru,us", "...with its keyboard intact")
    finally:
        os.chmod(cfg, stat.S_IRWXU)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    case_damaged_store_is_preserved()
    case_unrelated_keys_survive()
    case_unencodable_update_fails_safely()
    case_concurrent_writers()
    case_lock_timeout_fails_closed()
    case_unwritable_reports_failure()
    print()
    if FAILS:
        print("FAILED %d check(s):" % len(FAILS))
        for m in FAILS:
            print("  - " + m)
        sys.exit(1)
    print("locale.json write safety: all checks pass")
# Terminal verdict for the release runner: it will not read success into a
# zero exit with no recognised report (a suite that dies half way also prints
# PASS lines). See tools/run_all_gates.py SUCCESSWORD.
print("RESULT: ALL PASS")
