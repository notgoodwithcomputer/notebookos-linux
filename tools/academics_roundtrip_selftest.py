#!/usr/bin/env python3
"""Opening and closing Academics must not make the store smaller.

The loader normalises every record to a fixed schema and the next save writes
that normalisation straight back over the file. Anything the running version did
not recognise was therefore DELETED by the mere act of opening the app — no user
action, no warning. Measured before the fix, on one open-and-save:

    top-level "term"      'Autumn 2026'         -> gone
    meeting  "zoom"       'https://example/x'   -> gone
    lecture  "starred"    True                  -> gone
    homework "weight"     0.3                   -> gone
    homework records      2 in                  -> 1 out

This is survivable only while exactly one program ever writes the file AND the
schema never changes, and neither of those stays true — a store written by a
newer build, or hand-edited, or extended by a later version of this app, loses
whatever the reader happens not to know about. Unknown keys are now carried
through untouched, at every level: top of file, class, meeting, lecture,
assignment.

WHAT IS DELIBERATELY NOT PRESERVED. A record that cannot be understood AT ALL
(a bare string where a class should be) is still skipped, and its skip is
recorded so the save path can protect it — that is the damage suite's territory,
not this one. This file is about records that ARE understood, carrying fields
that are not.

RED PROOF (M1), measured. Reverting any one of the five `rec = dict(...)` /
`rec.update(...)` pairs fails the matching check. Reverting all of them:

    FAIL a top-level key this version does not know survives   <- 'term' gone
    FAIL a class keeps a field this version does not know      <- 'syllabus' gone
    FAIL a meeting keeps a field this version does not know    <- 'zoom' gone
    FAIL a lecture keeps a field this version does not know    <- 'starred' gone
    FAIL an assignment keeps a field this version does not know <- 'weight' gone
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acadrt-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/academics.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import academics                                              # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=300):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


# A store written by a build that knows more than this one does.
BEFORE = {
    "term": "Autumn 2026",
    "schema": 4,
    "classes": [{
        "label": "Organic Chemistry", "color": "#9A7B4F", "room": "D2210",
        "instructor": "Peraza", "syllabus": "chem-201.pdf",
        "meets": [{"day": 0, "start": "09:00", "end": "10:20", "room": "",
                   "zoom": "https://example.invalid/x"}],
    }],
    "lectures": [{"cls": 0, "num": "01", "title": "Aromatics",
                  "date": "2026-08-03", "meta": "", "notes": "benzene",
                  "ranges": {}, "starred": True}],
    "homework": [{"title": "Problem set 4", "cls": 0, "due": "2026-08-11",
                  "done": False, "note": "", "weight": 0.3}],
    "active": 0,
}

with open(STORE, "w") as f:
    json.dump(BEFORE, f)

app = academics.Academics()
pump()
app._save_to_disk()
pump()
app.destroy()
pump()

with open(STORE) as f:
    after = json.load(f)

check("a top-level key this version does not know survives",
      after.get("term") == "Autumn 2026" and after.get("schema") == 4,
      "term=%r schema=%r" % (after.get("term"), after.get("schema")))

cls = (after.get("classes") or [{}])[0]
check("a class keeps a field this version does not know",
      cls.get("syllabus") == "chem-201.pdf", cls)

meet = (cls.get("meets") or [{}])[0]
check("a meeting keeps a field this version does not know",
      meet.get("zoom") == "https://example.invalid/x", meet)

lec = (after.get("lectures") or [{}])[0]
check("a lecture keeps a field this version does not know",
      lec.get("starred") is True, lec)

hw = (after.get("homework") or [{}])[0]
check("an assignment keeps a field this version does not know",
      hw.get("weight") == 0.3, hw)

# ...and the known fields are still normalised, not merely echoed back. If the
# carry-through were implemented by writing the raw record straight out, this
# suite would pass while the loader had stopped doing its job.
check("the known fields are still normalised",
      lec.get("notes") == "benzene" and cls.get("label") == "Organic Chemistry"
      and meet.get("start") == "09:00", (cls.get("label"), meet.get("start")))

# Nothing was invented either: the file must not grow keys nobody wrote, beyond
# the fields this version is entitled to add: `name` and `course` are derived by
# _save_to_disk on purpose for the desktop board to read, and `kind` is a real
# schema field (work / exam) that the loader defaults onto every assignment
# written before it existed. Anything else appearing here is the loader leaking.
extra_top = set(after) - set(BEFORE)
check("no unexplained keys appear at the top level", not extra_top, extra_top)
extra_cls = set(cls) - set(BEFORE["classes"][0]) - {"name"}
check("no unexplained keys appear on a class", not extra_cls, extra_cls)
extra_hw = set(hw) - set(BEFORE["homework"][0]) - {"course", "kind"}
check("no unexplained keys appear on an assignment", not extra_hw, extra_hw)
check("an assignment written before `kind` existed reads as ordinary work",
      hw.get("kind") == "work", hw.get("kind"))

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
