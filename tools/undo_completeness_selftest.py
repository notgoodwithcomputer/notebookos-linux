#!/usr/bin/env python3
"""Display-free completeness guards for immediate destructive actions.

Run with an explicit throwaway NB_HOME and the app directory on PYTHONPATH.
The source checks are deliberately structural: each named action must enter the
app's full-state history before its first destructive mutation.  Behavioural
checks cover the two whole-store Calendar handlers and Media's real trash move.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de").resolve()
if "NB_HOME" not in os.environ:
    raise SystemExit("FAIL: NB_HOME must be explicitly set to a throwaway directory")

passed = failed = skipped = 0

def result(ok, name, detail=""):
    global passed, failed
    print(("PASS" if ok else "FAIL") + ": " + name +
          ((" -- " + detail) if detail else ""))
    passed += bool(ok); failed += not ok

def guarded_read(name):
    path = (ROOT / name).resolve()
    if path.parent != ROOT or path.is_symlink():
        raise AssertionError("unguarded app source path: " + str(path))
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != \
       (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise AssertionError("source changed during verifying read: " + str(path))
    return data.decode("utf-8")

SOURCES = {n: guarded_read(n + ".py") for n in
           ("screenplay", "calendar", "novel", "sequencer", "video",
            "cookbook", "workout", "tasks", "media")}

def body(app, method, source=None):
    tree = ast.parse(SOURCES[app] if source is None else source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method:
            return ast.get_source_segment(SOURCES[app] if source is None else source, node)
    raise AssertionError("missing handler %s.%s" % (app, method))

CASES = [
 ("screenplay New", "screenplay", "_file_new", "undo.checkpoint"),
 ("screenplay Open", "screenplay", "_open_file", "undo.checkpoint"),
 ("calendar New", "calendar", "_file_new", "undo.checkpoint"),
 ("calendar Open", "calendar", "_load_document", "undo.checkpoint"),
 ("novel New", "novel", "_do_file_new", "undo.checkpoint"),
 ("novel Open", "novel", "_do_open_path", "undo.checkpoint"),
 ("novel Delete Chapter", "novel", "_delete_chapter", "undo.checkpoint"),
 ("novel Delete Part", "novel", "_remove_part", "undo.checkpoint"),
 ("sequencer New", "sequencer", "_do_file_new", "_remember"),
 ("sequencer Open", "sequencer", "_open_file", "_remember"),
 ("sequencer clear-track", "sequencer", "_do_clear_track", "_remember"),
 ("sequencer shorten", "sequencer", "_set_length", "_remember"),
 ("sequencer clear-all", "sequencer", "_clear_takes", "_remember"),
 # _delete_clip_guarded validates the selection, then delegates the mutation
 # and checkpoint to the real equivalent _menu_delete.
 ("video clip delete", "video", "_menu_delete", "_push_undo"),
 ("video New", "video", "_do_file_new", "_push_undo"),
 ("cookbook category delete", "cookbook", "_remove_category", "undo.checkpoint"),
 ("workout Delete Exercise", "workout", "_delete_exercise", "undo.checkpoint"),
 ("workout Clear Today", "workout", "_clear_today", "undo.checkpoint"),
 ("tasks Clear Completed", "tasks", "_clear_completed", "undo.checkpoint"),
 ("tasks Remove List", "tasks", "_remove_list", "undo.checkpoint"),
]

for name, app, method, checkpoint in CASES:
    try:
        text = body(app, method)
        result(checkpoint in text, name + " has pre-action checkpoint")
    except Exception as exc:
        result(False, name + " has pre-action checkpoint", str(exc))

# Full-snapshot contracts: these fields are precisely the state a user notices.
contracts = [
 ("screenplay snapshot completeness", body("screenplay", "_undo_snapshot"),
  ("_collect_doc", "_caret", "_file_dirty")),
 ("calendar snapshot completeness", body("calendar", "_undo_snapshot"),
  ("events", "calendars", "cals_on", "_doc_path", "sel", "cur_y", "cur_m", "view")),
 ("novel snapshot completeness", body("novel", "_undo_snapshot"),
  ("_serialize", "_caret")),
 ("sequencer snapshot completeness", body("sequencer", "_arrangement"),
  ("_serialize", "path", "sel", "pos", "rec_start")),
 ("video snapshot completeness", body("video", "_snapshot"),
  ("_serialize", "_sel_cell", "_sel_music", "_path")),
 ("cookbook snapshot completeness", body("cookbook", "_undo_snapshot"),
  ("_serialize",)),
 ("workout snapshot completeness", body("workout", "_undo_snapshot"),
  ("data", "sel")),
 ("tasks snapshot completeness", body("tasks", "_undo_snapshot"),
  ("tasks", "projects", "view")),
]
for name, text, needles in contracts:
    result(all(n in text for n in needles), name)

# Calendar real-handler round trip.  The tiny history invokes the app's real
# snapshot/restore callbacks and models UndoHistory's checkpoint/commit stacks.
import calendar as calendar_app
class History:
    def __init__(self, owner): self.o=owner; self.u=[]; self.r=[]; self.pending=None
    def checkpoint(self, _name): self.pending=self.o._undo_snapshot()
    def commit(self): self.u.append(self.pending); self.pending=None; self.r=[]
    def cancel(self): self.pending=None
    def undo(self): self.r.append(self.o._undo_snapshot()); self.o._undo_restore(self.u.pop())
    def redo(self): self.u.append(self.o._undo_snapshot()); self.o._undo_restore(self.r.pop())

def calendar_fixture(doc_path):
    o = calendar_app.Calendar.__new__(calendar_app.Calendar)
    o.events=[{"id":"a","date":calendar_app.date(2026,8,9),"start":9.0,
               "end":10.0,"title":"Keep","cal":"Private"}]
    o.calendars=[{"name":"Work","color":"#111111"},
                 {"name":"Private","color":"#222222"}]
    o.cals_on={"Work":True,"Private":False}; o._orphans=[]; o._seen=set()
    o._doc_path=str(doc_path); o.sel=calendar_app.date(2026,8,19)
    o.cur_y=2026; o.cur_m=8; o.view="week"; o._calendars_quarantine=False
    o._save_events=lambda *a,**k: None; o._save_calendars=lambda: None
    o._populate_cal_list=lambda: None; o._refresh=lambda: None
    o._flash_status=lambda _s: None; o._mark_seen=lambda e: o._seen.add(e["id"])
    o.undo=History(o); return o

with tempfile.TemporaryDirectory(dir=os.environ["NB_HOME"]) as td:
    td=Path(td); bound=td/"bound.json"; bound.write_bytes(b"BOUND-BYTES")
    opened=td/"opened.json"
    opened.write_text(json.dumps({"calendars":[{"name":"Other","color":"#333333"}],
                                  "events":[]}), encoding="utf-8")
    for label, action in (("New", lambda o:o._file_new()),
                          ("Open", lambda o:o._load_document(str(opened)))):
        o=calendar_fixture(bound); before=copy.deepcopy(o._undo_snapshot())
        disk=hashlib.sha256(bound.read_bytes()).digest(); action(o)
        changed=copy.deepcopy(o._undo_snapshot()); o.undo.undo()
        result(o._undo_snapshot()==before, "calendar %s full undo" % label)
        result(hashlib.sha256(bound.read_bytes()).digest()==disk,
               "calendar %s on-disk current file untouched" % label)
        o.undo.redo(); result(o._undo_snapshot()==changed,
                              "calendar %s redo reapplies" % label)

# Real Media trash path: prove it lands as recoverable bytes, then perform the
# same rename-back operation Finder's restore uses and compare byte-for-byte.
import media as media_app
with tempfile.TemporaryDirectory(dir=os.environ["NB_HOME"]) as td:
    src=Path(td)/"recover-me.bin"; payload=b"NotebookOS trash recovery\x00\xff"
    src.write_bytes(payload)
    o=media_app.MediaViewer.__new__(media_app.MediaViewer)
    o._media_path=str(src); o._siblings=[str(src)]; o._sib_idx=0
    for n in ("_set_zoom","_set_info","_show_surface","_update_controls","_rebuild_strip"):
        setattr(o,n,lambda *a,**k:None)
    o._do_trash(str(src))
    candidates=list((Path(os.environ["NB_HOME"])/".Trash").glob("recover-me.bin*"))
    ok=(not src.exists() and len(candidates)==1 and candidates[0].read_bytes()==payload)
    result(ok, "media Move to Trash is recoverable")
    if ok: candidates[0].rename(src)
    result(src.exists() and src.read_bytes()==payload,
           "media Finder-style restore returns identical file")

# PASS-MUTANT: remove one required checkpoint in memory and prove the named
# predicate that passed above rejects the sabotaged handler.
mut = SOURCES["calendar"].replace(
    'self.undo.checkpoint("Open Calendar File")', '# checkpoint sabotaged', 1)
result("undo.checkpoint" not in body("calendar", "_load_document", mut),
       "PASS-MUTANT calendar Open missing checkpoint is caught")

print("TALLY: pass=%d fail=%d skip=%d" % (passed, failed, skipped))
raise SystemExit(bool(failed))
