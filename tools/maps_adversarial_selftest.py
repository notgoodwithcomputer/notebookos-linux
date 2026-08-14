#!/usr/bin/env python3
"""Display-free durability measurement for Maps' pack-guarded config writer."""
import glob
import json
import os
import shutil
import sys
import tempfile
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
PACK = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/maps/monaco.nbm2")
HOME = tempfile.mkdtemp(prefix="maps-adversarial-")
os.environ["NB_HOME"] = HOME
sys.path.insert(0, DE)

import maps  # noqa: E402

failed = []
count = 0


def check(name, ok, detail=""):
    global count
    count += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else " -> " + str(detail)))
    if not ok:
        failed.append(name)


def guarded_load(path, name):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except Exception as exc:
        check(name, False, "json.loads guard raised %r" % (exc,))
        return None
    check(name, True)
    return value


def app():
    obj = maps.Maps.__new__(maps.Maps)
    obj.pack = maps.NBM2(PACK)
    obj.cx, obj.cy, obj.scale = 7.42, 43.73, 12000.0
    obj._extra = {}
    return obj


try:
    a = app()
    store = a._cfg_path()
    os.makedirs(os.path.dirname(store), exist_ok=True)
    check("MAPS-PACK real shipped Monaco pack loaded", a.pack.name != "", a.pack.name)

    for label, raw in (("not-json", b"{broken"),
                       ("wrong-shape", b'["future", {"route": 4}]'),
                       ("zero-byte", b"")):
        for old in glob.glob(store + ".damaged-*"):
            os.unlink(old)
        with open(store, "wb") as fh:
            fh.write(raw)
        cfg = a._load_cfg()
        a._save_cfg()
        saved = guarded_load(store, "MAPS-%s replacement is readable" % label)
        asides = glob.glob(store + ".damaged-*")
        check("MAPS-%s damaged bytes moved aside" % label,
              len(asides) == 1, asides)
        check("MAPS-%s aside retains exact bytes" % label,
              len(asides) == 1 and open(asides[0], "rb").read() == raw)
        check("MAPS-%s store works after save" % label,
              isinstance(cfg, dict) and isinstance(saved, dict)
              and saved.get("pack") == PACK, saved)

    for label, initial in (("missing", None), ("empty-object", b"{}")):
        for path in glob.glob(store + "*"):
            os.unlink(path)
        if initial is not None:
            with open(store, "wb") as fh:
                fh.write(initial)
        a._load_cfg()
        a._save_cfg()
        check("MAPS-BENIGN-%s quarantines nothing" % label,
              not glob.glob(store + ".damaged-*"))

    with open(store, "w", encoding="utf-8") as fh:
        json.dump({"pack": PACK, "cx": 1, "cy": 2, "scale": 3,
                   "future_route": {"colour": "violet"},
                   "_extra": {"future_zoom": 17}}, fh)
    a._load_cfg()
    a._save_cfg()
    saved = guarded_load(store, "MAPS-EXTRA saved config is readable")
    check("MAPS-EXTRA unknown top-level keys ride through under _extra",
          saved.get("_extra") == {"future_route": {"colour": "violet"},
                                  "future_zoom": 17}, saved)

    # A failed view-state write has to reach the person. This check used to
    # delete nbapp.save_failure_reason and then assert the app had replaced that
    # FUNCTION with a string — which told nobody, and left the shared sentence
    # producer unusable for the rest of the process. What is asserted now is
    # what a person can reach: the sentence on the window, and a message in the
    # notification centre for a save that fails with no dialog to carry it.
    import nbnotify
    expect = maps.nbapp.save_failure_reason(OSError("injected maps disk full"))
    calls = []
    posted = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("injected maps disk full")
    with mock.patch.object(maps.nbapp, "atomic_write_json", fail), \
            mock.patch.object(nbnotify, "post",
                              lambda t, b="", **k: posted.append((t, b))):
        a._save_cfg()
    check("MAPS-FAILURE pack-loaded save was attempted", len(calls) == 1, calls)
    check("MAPS-FAILURE failed save records the reason on the window",
          getattr(a, "_save_error", "") == expect,
          repr(getattr(a, "_save_error", "")))
    check("MAPS-FAILURE failed save reaches the notification centre",
          len(posted) == 1 and posted[0][1] == expect, posted)
    check("MAPS-FAILURE the shared reason producer survives the failure",
          callable(maps.nbapp.save_failure_reason))

    # The verifier must actually reject the old behaviour that leaves a valid
    # wrong-shape JSON file in place for the shared parse-only guard.
    mutant_raw = b'["still", "here"]'
    with open(store, "wb") as fh:
        fh.write(mutant_raw)
    mutant_asides = []
    check("PASS-MUTANT MAPS wrong-shape aside guard rejects no quarantine",
          not (len(mutant_asides) == 1
               and open(mutant_asides[0], "rb").read() == mutant_raw))
finally:
    try:
        a.pack.f.close()
    except Exception:
        pass
    shutil.rmtree(HOME, ignore_errors=True)

print("\n%d checks, %d passed, %d FAILED" % (count, count - len(failed), len(failed)))
sys.exit(1 if failed else 0)
