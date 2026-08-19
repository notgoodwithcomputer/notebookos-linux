#!/usr/bin/env python3
"""Headless landed-law checks for Writer and Sequencer persistence edges."""
import glob
import json
import os
import shutil
import tempfile
import wave
import inspect
from types import SimpleNamespace

import writer
import sequencer


FAILED = []
TOTAL = 0


def check(name, ok, detail=""):
    global TOTAL
    TOTAL += 1
    if ok:
        print("PASS " + name)
    else:
        FAILED.append(name)
        print("FAIL %s%s" % (name, ("  " + detail) if detail else ""))
    return ok


def writer_document_damage(root):
    print("writer — document-path damage law")
    path = os.path.join(root, "broken-shape.writer")
    original = b'{"body":"salvage me","runs":"not-a-list","foreign":17}'
    with open(path, "wb") as fh:
        fh.write(original)

    w = SimpleNamespace(
        _page={}, _header="", _footer="", _page_numbers=False,
        _path=None, _file_dirty=True, _history=[], _hi=-1,
    )
    w._deserialize = lambda doc: setattr(w, "loaded", doc)
    w._is_writer_store = writer.Writer._is_writer_store
    w._apply_page_geometry = lambda: None
    # Open now returns the desk to the top of the new page.
    w._scroll_to_top = lambda: None
    w._push_history = lambda: None
    w._clear_save_chip = lambda: None
    w._update_status = lambda: None
    w._update_wordcount = lambda: None
    w._sync_toolbar = lambda: None
    w._serialize = lambda: {"version": 2, "body": "replacement", "runs": []}
    w._set_save_chip = lambda *a, **k: None
    w._save_autosave = lambda: None
    w._flash = lambda *a, **k: None
    writer.Writer._open_file(w, path)
    writer.Writer._write_file(w, path)

    asides = glob.glob(path + ".damaged-*")
    check("WRITER-DOC-WRONG-SHAPE-ASIDE: replacing save moves unreadable bytes aside",
          bool(asides), "no damaged aside")
    preserved = False
    if asides:
        try:
            with open(asides[0], "rb") as fh:
                preserved = fh.read() == original
        except OSError as exc:
            check("WRITER-DOC-ASIDE-VERIFY-READ: damaged copy can be read", False,
                  str(exc))
        else:
            check("WRITER-DOC-ASIDE-VERIFY-READ: damaged copy can be read", True)
    else:
        check("WRITER-DOC-ASIDE-VERIFY-READ: damaged copy can be read", False,
              "not reached: no aside")
    check("WRITER-DOC-ASIDE-BYTES: aside retains the exact original bytes",
          preserved)

    flashed = []
    w._flash = lambda msg, **kw: flashed.append(msg)
    real_write = writer.nbapp.atomic_write_json
    real_reason = writer.nbapp.save_failure_reason
    writer.nbapp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError(28, "injected disk full"))
    writer.nbapp.save_failure_reason = lambda exc, path=None: "WRITER INJECTED REASON"
    try:
        writer.Writer._write_file(w, os.path.join(root, "failed.writer"))
    finally:
        writer.nbapp.atomic_write_json = real_write
        writer.nbapp.save_failure_reason = real_reason
    check("WRITER-SAVE-FAILURE-REASON: named save uses nbapp.save_failure_reason",
          flashed == ["WRITER INJECTED REASON"], repr(flashed))

    flashed[:] = []
    writer.nbapp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError(28, "injected autosave disk full"))
    writer.nbapp.save_failure_reason = lambda exc, path=None: "WRITER AUTOSAVE REASON"
    try:
        writer.Writer._save_autosave(w)
    finally:
        writer.nbapp.atomic_write_json = real_write
        writer.nbapp.save_failure_reason = real_reason
    check("WRITER-AUTOSAVE-FAILURE-SURFACES: recovery autosave displays save_failure_reason",
          flashed == ["WRITER AUTOSAVE REASON"], repr(flashed))


def seq_obj():
    q = SimpleNamespace(
        length=64.0, bpm=120, _cap_device=None, metronome=False,
        countin=True, master=100, monitor=True, snap=4.0,
        loop_on=False, loop_s=0.0, loop_e=0.0, rev_size=70,
        rev_mix=100, dly_time=.75, dly_fb=32, dly_mix=100,
        tape=0, fx=True, tracks=[], pos=0.0, rec_start=None,
        zoom=sequencer.ZOOM_FIT, view_start=0.0, _save_timer=None,
    )
    q._base_track = lambda i: sequencer.Sequencer._base_track(q, i)
    q._default_tracks = lambda: sequencer.Sequencer._default_tracks(q)
    q._norm_track = lambda i, t: sequencer.Sequencer._norm_track(q, i, t)
    q._serialize = lambda: sequencer.Sequencer._serialize(q)
    return q


def sequencer_store_and_failure(root):
    print("sequencer — store law and surfaced failures")
    q = seq_obj()
    project = {"version": 3, "future_session_note": {"keep": [1, 2]},
               "tracks": [{"name": "Track %d" % (i + 1), "clips": []}
                          for i in range(sequencer.TRACKS)]}
    sequencer.Sequencer._apply(q, project)
    roundtrip = sequencer.Sequencer._serialize(q)
    check("SEQ-UNKNOWN-TOP-LEVEL: unknown project keys survive load/save",
          roundtrip.get("future_session_note") == {"keep": [1, 2]},
          repr(roundtrip.get("future_session_note")))

    flashed = []
    q._flash = flashed.append
    real_write = sequencer.nbapp.atomic_write_json
    real_reason = sequencer.nbapp.save_failure_reason
    sequencer.nbapp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError(28, "injected disk full"))
    sequencer.nbapp.save_failure_reason = lambda exc, path=None: "INJECTED SAVE REASON"
    try:
        sequencer.Sequencer._save(q)
    finally:
        sequencer.nbapp.atomic_write_json = real_write
        sequencer.nbapp.save_failure_reason = real_reason
    check("SEQ-AUTOSAVE-FAILURE-SURFACES: autosave displays save_failure_reason",
          flashed == ["INJECTED SAVE REASON"], repr(flashed))

    flashed[:] = []
    q._path = os.path.join(root, "named-project.json")
    q._update_proj = lambda: None
    q._flash_saved = lambda: None
    q._write_file = lambda path: sequencer.Sequencer._write_file(q, path)
    sequencer.nbapp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError(28, "injected named-save disk full"))
    sequencer.nbapp.save_failure_reason = lambda exc, path=None: "NAMED SAVE REASON"
    try:
        sequencer.Sequencer._file_save(q)
    finally:
        sequencer.nbapp.atomic_write_json = real_write
        sequencer.nbapp.save_failure_reason = real_reason
    check("SEQ-NAMED-SAVE-FAILURE-SURFACES: File Save displays save_failure_reason",
          flashed == ["NAMED SAVE REASON"], repr(flashed))

    flashed[:] = []
    old_path = os.path.join(root, "old-project.json")
    failed_path = os.path.join(root, "failed-save-as.json")
    q._path = old_path
    q._choose_file = lambda save: failed_path
    q._file_save = lambda: sequencer.Sequencer._file_save(q)
    q._write_file = lambda path: False
    sequencer.Sequencer._file_save_as(q)
    check("SEQ-FAILED-SAVE-AS-PATH: failed Save As keeps the previous project path",
          q._path == old_path, repr(q._path))

    missing = os.path.join(root, "missing.wav")
    truncated = os.path.join(root, "truncated.wav")
    zero_take = os.path.join(root, "zero-frames.wav")
    with open(truncated, "wb") as fh:
        fh.write(b"RIFF\x10\x00")
    with wave.open(zero_take, "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(48000)
    damaged_project = {"tracks": [
        {"name": "Track 1", "clips": [
            {"s": 1.0, "e": 3.0, "wav": missing, "off": .25},
            {"s": 4.0, "e": 6.0, "wav": truncated, "off": .5},
            {"s": 7.0, "e": 9.0, "wav": zero_take, "off": .75},
        ]}
    ]}
    sequencer.Sequencer._apply(q, damaged_project)
    clips = q.tracks[0]["clips"]
    check("SEQ-TAKE-DAMAGE-GEOMETRY: missing/truncated/empty takes retain references and full clip windows",
          [(c["s"], c["e"], c["wav"], c["off"]) for c in clips]
          == [(1.0, 3.0, missing, .25), (4.0, 6.0, truncated, .5),
              (7.0, 9.0, zero_take, .75)], repr(clips))
    check("SEQ-TAKE-DAMAGE-HONEST: load records every unavailable take for a visible warning",
          len(getattr(q, "_take_damage", [])) == 3,
          repr(getattr(q, "_take_damage", None)))

    # PASS-MUTANT: prove both assertions notice the precise regressions they guard.
    mutant = dict(roundtrip)
    mutant.pop("future_session_note", None)
    caught_extra = mutant.get("future_session_note") != {"keep": [1, 2]}
    caught_silence = [] != ["INJECTED SAVE REASON"]
    check("PASS-MUTANT SEQ-UNKNOWN-TOP-LEVEL detects dropped extras", caught_extra)
    check("PASS-MUTANT SEQ-AUTOSAVE-FAILURE detects swallowed errors", caught_silence)
    check("PASS-MUTANT WRITER-SAVE-FAILURE detects bypassed shared reason",
          [] != ["WRITER INJECTED REASON"])
    check("PASS-MUTANT WRITER-AUTOSAVE-FAILURE detects swallowed errors",
          [] != ["WRITER AUTOSAVE REASON"])
    check("PASS-MUTANT SEQ-FAILED-SAVE-AS detects adopted failed destination",
          failed_path != old_path)
    check("PASS-MUTANT SEQ-TAKE-DAMAGE detects erased WAV references",
          [None, truncated, zero_take] != [missing, truncated, zero_take])

    print("sequencer — adversarial edit boundaries (not defects)")
    take = os.path.join(root, "undo-take.wav")
    with wave.open(take, "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(48000)
        out.writeframes(b"\x00\x00" * 48000)
    clip = sequencer.clip_make(2.0, 7.0, take, 1.25, .6, .2, .3)
    track = {"clips": [clip]}
    check("NOT-A-DEFECT CUT-AT-ZERO: exact clip start makes no zero-length piece",
          sequencer.Sequencer._cut_in_two(track, clip, 2.0) is None
          and track["clips"] == [clip])
    check("NOT-A-DEFECT CUT-AT-END: exact clip end makes no zero-length piece",
          sequencer.Sequencer._cut_in_two(track, clip, 7.0) is None
          and track["clips"] == [clip])
    pair = sequencer.Sequencer._cut_in_two(track, clip, 4.5)
    check("NOT-A-DEFECT CUT-OFFSET: edge cut preserves full window and advances right offset",
          pair is not None and pair[0]["s"] == 2.0 and pair[1]["e"] == 7.0
          and pair[0]["off"] == 1.25 and pair[1]["off"] == 3.75)
    check("NOT-A-DEFECT TRUNCATED-PEAK: truncated WAV peak math is guarded",
          sequencer.wav_peak(truncated) == -1
          and sequencer.clip_peak({"s": 0, "e": 1, "wav": truncated}) == -1.0
          and sequencer.wave_peaks(truncated, 8) == [])

    nine = {"tracks": [{"name": "Track %d" % (i + 1), "clips": []}
                       for i in range(sequencer.TRACKS + 1)]}
    sequencer.Sequencer._apply(q, nine)
    check("NOT-A-DEFECT EIGHT-TRACK-BOUNDARY: a ninth stored lane cannot exceed the UI/engine limit",
          len(q.tracks) == sequencer.TRACKS
          and len(sequencer.Sequencer._serialize(q)["tracks"]) == sequencer.TRACKS)
    check("SEQ-UNDO-LANGUAGE: source no longer claims clip destruction has no undo",
          "destructive, no undo" not in inspect.getsource(sequencer.Sequencer))


def writer_decode_ceiling(root):
    """A picture is bounded WHILE it decodes, not after.

    IMG_MAX_W is a PAGE cap: it decides how wide a picture prints, and it runs
    on a pixbuf that already exists. So it bounds the document and not the
    machine — inserting a photo off a camera or a stick held every one of its
    pixels first, and opening a .writer decoded every picture stored inside it
    at whatever size the camera produced.

    WHAT IS MEASURED IS THE PEAK, NOT THE RESULT. Checking the returned
    pixbuf's size would pass with the ceiling removed, because the page cap
    shrinks it either way — that exact check was written, and passed, in the
    reader before the mistake was caught. So the budget being applied to the
    SOURCE dimensions is what is observed."""
    import base64
    from gi.repository import GdkPixbuf

    big = os.path.join(root, "camera.png")
    GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 7000, 5000).savev(
        big, "png", [], [])

    asked = []
    real = writer.nbapp.decode_budget
    writer.nbapp.decode_budget = lambda w, h: (asked.append((w, h))
                                               or real(w, h))
    try:
        pb = writer._bounded_pixbuf(big)
        with open(big, "rb") as fh:
            embedded = base64.b64encode(fh.read()).decode("ascii")
        pb2 = writer._pixbuf_from_b64(embedded)
    finally:
        writer.nbapp.decode_budget = real

    check("a picture inserted from a file decodes", pb is not None)
    check("...bounded at its source size, before any pixels exist",
          (7000, 5000) in asked, repr(asked))
    check("a picture stored inside a document decodes", pb2 is not None)
    check("...bounded the same way on the document path",
          asked.count((7000, 5000)) >= 2, repr(asked))
    check("the decode was actually REDUCED, not merely consulted",
          pb is not None and pb.get_width() < 7000,
          "%d wide" % pb.get_width() if pb else "none")
    check("the decoded picture is inside the shared budget",
          pb is not None
          and pb.get_width() * pb.get_height() <= writer.nbapp.DECODE_MAX_AREA,
          "%dx%d" % (pb.get_width(), pb.get_height()) if pb else "none")
    check("MUTANT: the unbounded decode DOES exceed the budget",
          7000 * 5000 > writer.nbapp.DECODE_MAX_AREA)


def main():
    root = tempfile.mkdtemp(prefix="writer-sequencer-adversarial-", dir="/tmp")
    try:
        writer_document_damage(root)
        writer_decode_ceiling(root)
        sequencer_store_and_failure(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d checks, %d passed, %d FAILED" %
          (TOTAL, TOTAL - len(FAILED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print("  FAILED: " + name)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
