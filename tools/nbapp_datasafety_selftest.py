#!/usr/bin/env python3
"""Adversarial gate on nbapp's data-safety PRIMITIVES — the shared
preserve_damaged / quarantine_unrecognized / atomic_write_json / atomic_write_text
/ _bak_would_shrink / UndoHistory that every app's durability rides on.

Why a direct gate. store_damage, save_failure and undo_selftest all drive these
through an APP's store, so they see what an app happens to exercise and miss the
primitive's own edges: the once-per-process .bak guard (a second save must not
refresh the pre-session copy), atomicity when serialisation itself throws (the
original must survive, no temp left), the zero-byte store treated as damage not
absence, and _bak_would_shrink's record-loss test that a fuller weight must not
hide (the real Academics loss). None of those has a gate that fails when the
primitive breaks; this is that gate.

Red-proof: point NBAPP_MODULE_DIR at a sabotaged copy of the de/ tree and the
named check for the sabotaged invariant goes red. The suite reads nbapp from
NBAPP_MODULE_DIR (default: the real overlay), so a red proof is never vacuous
against a hardcoded path.
"""
import glob
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
NBAPP_DIR = os.environ.get("NBAPP_MODULE_DIR", str(DE))
sys.path.insert(0, NBAPP_DIR)

CHECKS = 0
FAILS = []


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s%s" % (name, (": " + detail) if detail else ""))


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def damaged_siblings(path):
    return sorted(glob.glob(path + ".damaged-*"))


# ---------------------------------------------------------------------------
#  preserve_damaged / atomic_write_json — the write path
# ---------------------------------------------------------------------------
def test_write_path(nbapp, tmp):
    # 1. An UNPARSEABLE store is moved aside, never overwritten in place.
    p = os.path.join(tmp, "unparseable.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json ")
    nbapp.atomic_write_json(p, {"fresh": 1})
    aside = damaged_siblings(p)
    kept = ""
    if aside:
        with open(aside[0], "r", encoding="utf-8") as fh:
            kept = fh.read()
    try:
        now = read_json(p)
    except Exception as e:                                    # noqa: BLE001
        now = "[not reached: store unreadable after write: %s]" % e
    check("unparseable store is moved aside, not overwritten",
          len(aside) == 1 and "not json" in kept and now == {"fresh": 1},
          "aside=%r now=%r" % (aside, now))

    # 2. A ZERO-BYTE store is damage, not absence (task 031): quarantined, and
    #    the fresh write still lands.
    z = os.path.join(tmp, "zerobyte.json")
    open(z, "w").close()
    check("zero-byte store exists and is empty before write",
          os.path.isfile(z) and os.path.getsize(z) == 0)
    nbapp.atomic_write_json(z, {"fresh": 2})
    check("zero-byte store is quarantined as damage, not waved through",
          len(damaged_siblings(z)) == 1 and read_json(z) == {"fresh": 2},
          "aside=%r" % damaged_siblings(z))

    # 3+4. A HEALTHY store keeps ONE previous-good .bak on first overwrite, and
    #      a SECOND save in the same process does NOT refresh it (the once-per-
    #      process _BACKED_UP guard: the .bak is "before this session touched
    #      it"; refreshing it would overwrite the copy with newer state).
    h = os.path.join(tmp, "healthy.json")
    nbapp.atomic_write_json(h, {"v": 1})          # seed (adds h to _BACKED_UP)
    # Seeding already claimed h. Exercise the guard on a path we plant by hand
    # so the FIRST atomic write is the one under test.
    g = os.path.join(tmp, "guarded.json")
    with open(g, "w", encoding="utf-8") as fh:
        json.dump({"v": 1}, fh)
    nbapp.atomic_write_json(g, {"v": 2})          # first overwrite -> .bak=v1
    bak1 = read_json(g + ".bak") if os.path.isfile(g + ".bak") else None
    check("healthy store keeps one previous-good .bak on first overwrite",
          bak1 == {"v": 1}, "bak=%r" % (bak1,))
    nbapp.atomic_write_json(g, {"v": 3})          # second overwrite, same run
    bak2 = read_json(g + ".bak") if os.path.isfile(g + ".bak") else None
    check("the .bak is NOT refreshed on a second save in one process",
          bak2 == {"v": 1}, "bak=%r (must stay v1)" % (bak2,))
    check("the store itself still advances across saves",
          read_json(g) == {"v": 3})

    # 5. Atomicity when serialisation THROWS: a non-JSON object must raise,
    #    leave the original byte-for-byte, and drop the temp file. This is the
    #    single promise atomic_write_json exists to make.
    a = os.path.join(tmp, "atomic.json")
    with open(a, "w", encoding="utf-8") as fh:
        json.dump({"good": [1, 2, 3]}, fh)
    raised = False
    try:
        nbapp.atomic_write_json(a, {"bad": {1, 2, 3}})        # a set: not JSON
    except Exception:                                          # noqa: BLE001
        raised = True
    leftover = [f for f in os.listdir(tmp) if f.startswith(".nbw-")]
    check("a failed serialise raises, keeps the original, leaves no temp",
          raised and read_json(a) == {"good": [1, 2, 3]} and not leftover,
          "raised=%s now=%r leftover=%r"
          % (raised, _safe_read(a), leftover))


def _safe_read(p):
    try:
        return read_json(p)
    except Exception as e:                                    # noqa: BLE001
        return "[unreadable: %s]" % e


def test_atomic_text(nbapp, tmp):
    # atomic_write_text has the same atomicity contract for document Saves
    # (Writer .txt, Screenplay .fountain). Force the write to throw with a
    # non-str payload; the original document must survive whole.
    p = os.path.join(tmp, "doc.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("the finished eleven-thousand-word chapter")
    raised = False
    try:
        nbapp.atomic_write_text(p, 12345)                     # not a str
    except Exception:                                          # noqa: BLE001
        raised = True
    with open(p, "r", encoding="utf-8") as fh:
        body = fh.read()
    leftover = [f for f in os.listdir(tmp) if f.startswith(".nbw-")]
    check("atomic_write_text keeps the whole document when the write throws",
          raised and body == "the finished eleven-thousand-word chapter"
          and not leftover,
          "raised=%s body=%r leftover=%r" % (raised, body[:20], leftover))


def test_atomic_via(nbapp, tmp):
    """atomic_write_via is the same contract for a producer that needs a real
    PATH — cairo's PDFSurface, the csv module, an encoder that opens the file
    itself. It exists because the EXPORT paths were doing what the Save paths
    used to do: Writer, Accounting and Contacts rendered straight onto the
    destination, so a render that threw part-way truncated the file the user
    was overwriting — usually their previous export of the same document."""
    p = os.path.join(tmp, "export.pdf")
    with open(p, "wb") as fh:
        fh.write(b"%PDF-1.4 last month's finished export")
    good = open(p, "rb").read()

    def half_a_render(dest):
        # A real cairo failure leaves a partly-written file, so write before
        # raising: a check that only ever saw an untouched empty draft would
        # pass against a direct-to-destination writer too.
        with open(dest, "wb") as fh:
            fh.write(b"%PDF-1.4 half")
        raise RuntimeError("producer failed part-way")

    raised = False
    try:
        nbapp.atomic_write_via(p, half_a_render)
    except Exception:                                          # noqa: BLE001
        raised = True
    leftover = [f for f in os.listdir(tmp) if f.startswith(".nbw-")]
    check("atomic_write_via keeps the previous file when the producer throws",
          raised and open(p, "rb").read() == good and not leftover,
          "raised=%s body=%r leftover=%r"
          % (raised, open(p, "rb").read()[:20], leftover))

    # The producer must be handed a path in the DESTINATION's directory, or
    # the replace crosses a filesystem and stops being atomic.
    seen = {}

    def note(dest):
        seen["path"] = os.path.abspath(dest)
        seen["dir"] = os.path.dirname(os.path.abspath(dest))
        with open(dest, "wb") as fh:
            fh.write(b"%PDF-1.4 the new one")

    nbapp.atomic_write_via(p, note)
    # BOTH halves, or this check cannot tell the two cases apart: a writer
    # that hands the producer the destination ITSELF also satisfies "same
    # directory", so the same-directory clause alone passes against exactly
    # the defect this primitive exists to prevent.
    check("atomic_write_via drafts alongside the destination, not onto it",
          seen.get("dir") == os.path.dirname(os.path.abspath(p))
          and seen.get("path") != os.path.abspath(p),
          repr(seen))
    check("atomic_write_via lands the new bytes on success",
          open(p, "rb").read() == b"%PDF-1.4 the new one")
    check("atomic_write_via leaves no draft behind on success",
          not [f for f in os.listdir(tmp) if f.startswith(".nbw-")])

    # A destination whose directory does not exist yet is created, the way
    # every export into Documents relies on.
    deep = os.path.join(tmp, "Documents", "sub", "new.pdf")
    nbapp.atomic_write_via(deep, lambda d: open(d, "wb").write(b"ok"))
    check("atomic_write_via creates the destination directory",
          os.path.exists(deep) and open(deep, "rb").read() == b"ok")


# ---------------------------------------------------------------------------
#  _bak_would_shrink — the cross-process protector
# ---------------------------------------------------------------------------
def test_bak_would_shrink(nbapp, tmp):
    bak = os.path.join(tmp, "shrink.bak")

    def would(bak_obj, raw_obj):
        with open(bak, "w", encoding="utf-8") as fh:
            json.dump(bak_obj, fh)
        return nbapp._bak_would_shrink(bak, json.dumps(raw_obj))

    check("refreshing a full .bak with an emptier store is refused",
          would({"items": [1, 2, 3]}, {"items": []}) is True)
    check("a legitimately growing store is allowed to refresh the .bak",
          would({"items": [1]}, {"items": [1, 2]}) is False)

    # The Academics loss: the new store has FEWER records but MORE weight
    # (every record decorated with derived fields the loader ignores). Weight
    # alone said "grew"; _loses_records must still see the shorter list.
    old = {"hw": [{"a": 1}, {"a": 1}, {"a": 1}]}                # 3 records, w=3
    new = {"hw": [{"a": 1, "x": 1, "y": 1, "z": 1},
                  {"a": 1, "x": 1, "y": 1, "z": 1}]}            # 2 records, w=8
    check("record-loss is caught even when total weight GREW (Academics)",
          nbapp._payload_weight(new) > nbapp._payload_weight(old)
          and would(old, new) is True,
          "w_new=%d w_old=%d" % (nbapp._payload_weight(new),
                                 nbapp._payload_weight(old)))

    # No previous .bak: nothing to protect, so a refresh is allowed.
    missing = os.path.join(tmp, "nope.bak")
    check("no previous .bak means nothing to protect",
          nbapp._bak_would_shrink(missing, json.dumps({"a": 1})) is False)


# ---------------------------------------------------------------------------
#  quarantine_unrecognized — the load path
# ---------------------------------------------------------------------------
def test_quarantine(nbapp, tmp):
    p = os.path.join(tmp, "wrongshape.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"a key the app never heard of": "the user's prose"}, fh)
    dest = nbapp.quarantine_unrecognized(p)
    moved = ""
    if dest and os.path.isfile(dest):
        with open(dest, "r", encoding="utf-8") as fh:
            moved = fh.read()
    check("a wrong-shape store is moved aside at load, content intact",
          bool(dest) and not os.path.exists(p) and "user's prose" in moved,
          "dest=%r stillthere=%s" % (dest, os.path.exists(p)))

    missing = os.path.join(tmp, "absent.json")
    ok = True
    try:
        r = nbapp.quarantine_unrecognized(missing)
    except Exception as e:                                    # noqa: BLE001
        ok, r = False, "[not reached: raised %s]" % e
    check("quarantine_unrecognized waves through a missing file, never raises",
          ok and r is None, "returned=%r" % (r,))


# ---------------------------------------------------------------------------
#  UndoHistory — the shared checkpoint history
# ---------------------------------------------------------------------------
def test_undo(nbapp, tmp):
    UndoHistory = nbapp.UndoHistory

    # A snapshot that throws must never break the edit around it.
    def boom():
        raise RuntimeError("snapshot exploded")
    h = UndoHistory(boom, lambda s: None)
    safe = True
    try:
        h.checkpoint("Delete Chapter")
        h.commit()
        h.touch()
    except Exception:                                          # noqa: BLE001
        safe = False
    h.cancel()
    check("a snapshot that raises never breaks the edit around it",
          safe and not h.can_undo())

    # Volatile "_"-prefixed keys (the caret) do not consume an undo step:
    # two states differing only there are the SAME state.
    state = {"doc": "hello", "_caret": 0}
    h = UndoHistory(lambda: dict(state), _grab())
    h.checkpoint("first"); h.commit()
    state["_caret"] = 99
    h.checkpoint("caret only"); h.commit()
    check("moving only the caret does not create an undo step",
          not h.can_undo(), "hist depth suggests a caret step was recorded")
    state["doc"] = "hello world"
    h.checkpoint("real edit"); h.commit()
    check("a real content change DOES create an undo step",
          h.can_undo())

    # A new edit after undo drops the redo tail.
    seq = {"n": 0}
    h = UndoHistory(lambda: {"n": seq["n"]}, _grab())
    for i in range(1, 4):
        seq["n"] = i
        h.checkpoint("edit %d" % i); h.commit()
    h.undo(); h.undo()
    check("there is a redo tail to drop after undoing", h.can_redo())
    seq["n"] = 99
    h.checkpoint("branch"); h.commit()
    check("a new edit after undo drops the redo tail", not h.can_redo())

    # restore runs with busy=True (apps guard re-entrancy on it) and busy is
    # cleared afterwards even if restore raises.
    seen = {}
    ref = {}

    def watch(s):
        seen["busy"] = ref["h"].busy
    hh = UndoHistory(lambda: {"n": seq["n"]}, watch)
    ref["h"] = hh
    seq["n"] = 1; hh.checkpoint("a"); hh.commit()
    seq["n"] = 2; hh.checkpoint("b"); hh.commit()
    hh.undo()
    check("restore runs with busy=True and busy clears after",
          seen.get("busy") is True and hh.busy is False,
          "during=%r after=%r" % (seen.get("busy"), hh.busy))

    def blowup(s):
        raise RuntimeError("restore failed")
    hb = UndoHistory(lambda: {"n": seq["n"]}, blowup)
    seq["n"] = 1; hb.checkpoint("a"); hb.commit()
    seq["n"] = 2; hb.checkpoint("b"); hb.commit()
    try:
        hb.undo()
    except Exception:                                          # noqa: BLE001
        pass
    check("busy clears even when the restore callback raises",
          hb.busy is False)

    # is_dirty errs toward dirty: an armed typing timer reads dirty with no
    # main loop running; mark_saved with no further change reads clean. Apps
    # seed a baseline at load (reset -> _hi 0, the pristine document); the
    # first EDIT moves past it, and only that is "dirty".
    val = {"n": 0}
    hd = UndoHistory(lambda: {"n": val["n"]}, _grab())
    hd.reset()                                  # the load-time baseline
    check("a freshly loaded, unedited history reads clean", not hd.is_dirty())
    val["n"] = 1; hd.checkpoint("edit"); hd.commit()
    check("a never-marked history with an edit reads dirty", hd.is_dirty())
    hd.mark_saved()
    check("after mark_saved with no change the history reads clean",
          not hd.is_dirty())
    val["n"] = 2; hd.checkpoint("more"); hd.commit()
    check("after a further edit the history reads dirty again", hd.is_dirty())
    hd.touch()
    check("an armed typing timer reads dirty (errs toward dirty)",
          hd.is_dirty())
    hd.cancel()

    # Depth is bounded by BYTES as well as count: many large snapshots keep a
    # useful floor of steps without the history outgrowing the document.
    big = {"n": 0}
    hc = UndoHistory(lambda: {"blob": ("z%d" % big["n"]) * 400000,
                              "n": big["n"]}, _grab())
    for i in range(1, 60):
        big["n"] = i
        hc.checkpoint("edit %d" % i); hc.commit()
    depth = len(hc._hist)
    check("history depth stays within the count cap under byte pressure",
          depth <= nbapp._UNDO_LIMIT, "depth=%d" % depth)
    check("history keeps at least the minimum floor of steps",
          depth >= nbapp._UNDO_MIN, "depth=%d" % depth)

    # Consecutive snapshots SHARE equal strings (memory), never containers.
    # Build the two equal strings at runtime with a variable operand so CPython
    # cannot constant-fold them into a single object (which would make the
    # "distinct but equal" premise, and this whole check, vacuous).
    span = 2000
    a0 = "x" * span
    a1 = "x" * span
    body = {"cur": a0, "n": 0}
    hs = UndoHistory(lambda: {"cur": body["cur"], "n": body["n"]}, _grab())
    body["cur"], body["n"] = a0, 1
    hs.checkpoint("one"); hs.commit()
    body["cur"], body["n"] = a1, 2
    hs.checkpoint("two"); hs.commit()
    same = (a0 is not a1
            and hs._hist[-1][0]["cur"] is hs._hist[-2][0]["cur"])
    check("consecutive snapshots share equal strings, not copies", same)


class _Grab:
    def __call__(self, s):
        self.last = s


def _grab():
    return _Grab()


# ---------------------------------------------------------------------------
#  PASS-MUTANT: the static guards that keep this suite honest if a child
#  crashes or a readback site is silently deleted from the primitive.
# ---------------------------------------------------------------------------
def test_pass_mutants():
    src = (Path(NBAPP_DIR) / "nbapp.py").read_text(encoding="utf-8")
    requirements = {
        "the once-per-process .bak guard": "if path not in _BACKED_UP",
        "the zero-byte-is-damage raise": 'raise ValueError("zero-byte store")',
        "atomicity via os.replace(tmp, path)": "os.replace(tmp, path)",
        "the damaged store is moved aside": "os.replace(path, dest)",
        "_bak_would_shrink checks record loss": "if _loses_records(",
        "the temp file is removed on failure": "os.unlink(tmp)",
    }
    for label, needle in requirements.items():
        mutant = src.replace(needle, "PASS_MUTANT_REMOVED", 1)
        check("PASS-MUTANT: %s is present (removal is caught)" % label,
              needle in src and mutant != src)


def main():
    tmp = tempfile.mkdtemp(prefix="nb-datasafety-")
    print("nbapp from: %s" % NBAPP_DIR)
    try:
        import nbapp
    except Exception as e:                                    # noqa: BLE001
        print("FAIL could not import nbapp: %s" % e)
        return 1
    try:
        test_write_path(nbapp, tmp)
        test_atomic_text(nbapp, tmp)
        test_atomic_via(nbapp, tmp)
        test_bak_would_shrink(nbapp, tmp)
        test_quarantine(nbapp, tmp)
        test_undo(nbapp, tmp)
        test_pass_mutants()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d checks, %d failed" % (CHECKS, len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
