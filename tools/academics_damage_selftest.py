#!/usr/bin/env python3
"""
Open Academics on a DAMAGED store, close it (Esc), and prove the term survived.

THE BUG THIS EXISTS FOR: the loader was all-or-nothing. One lecture pointing at
a class index that no longer existed -- or one section that was not a list --
made it reject the ENTIRE file, so the app opened blank; the close-time save
then wrote that blankness over the user's real data. From the user's chair it
read as "pressing Esc in Academics deletes schedules and homework", which is
exactly what it did.

A malformed entry must now cost ITSELF and nothing more, and a store that
cannot be parsed at all must be moved aside, never overwritten.

ROUND 6 -- COUNTING RECORDS IS NOT ENOUGH. Every case here counted classes,
class times, assignments and lecture text, and every counter stayed green while
a damaged store quietly re-filed the surviving work onto the WRONG CLASSES. Two
blind spots, both now closed: the "class not a dict" case appended its junk
record at the END of the list, where skipping it shifts no index and so tests
nothing; and no case ever asked which class a record came back attached to.
`want_owner` now reads that out of the file by label and asserts it after the
load.

RED PROOF (M1). Removing the index remap in academics._load_from_disk --
    -  cls = class_at.get(cls, -1) if cls >= 0 else -1
    +  (drop it; go back to comparing against len(classes))
-- fails exactly one case, and names the damage:

    FAIL class corrupted mid-list  opened=(2, 3, 2, 1) ... on disk=(2, 3, 2, 1)
         <- work must stay on the class it names;
            misfiled ['Read ch 4-6: Linear Algebra -> (untied)']

Note what the counters say in that run: (2, 3, 2, 1) both in memory and on disk,
identical to the passing control. Nothing was lost. It was just filed under the
wrong name, which is why only an ownership assertion can see it.

SECOND RED PROOF, for the titleless-assignment salvage. Dropping the salvage in
_clean_homework (back to a bare `continue` when the title is empty):

    FAIL homework with no title  opened=(2, 3, 2, 1) ... on disk=(2, 3, 2, 1)
         <- 2 classes / 3 class times / 3 assignments must survive open+Esc

Two records went into that store's homework list and one came out, and the
close-time save wrote the smaller list over the only copy.

ONE CASE PER PROCESS: nbapp keeps a module-level _BACKED_UP set so a store is
backed up once per file per PROCESS. Running every case in one process makes
cases 2..n look like they got no backup, which is a lie the first version of
this test told.

  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/academics_damage_selftest.py [case]
"""
import os, sys, shutil, json
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk, Gdk
H="/tmp/nbhome-dmg1"; os.environ["NB_HOME"]=H
import academics
STORE = H+"/.config/notebook/academics.json"
GOOD = {
 "classes":[{"label":"Organic Chemistry","color":"#9A7B4F","room":"D2210","instructor":"Peraza",
             "meets":[{"day":0,"start":"09:00","end":"10:20","room":""},
                      {"day":2,"start":"09:00","end":"10:20","room":""}]},
            {"label":"Linear Algebra","color":"#4A5E73","room":"W7500","instructor":"Iyer",
             "meets":[{"day":1,"start":"13:30","end":"14:50","room":""}]}],
 "homework":[{"title":"Problem set 4","cls":0,"due":"2026-07-30","done":False,"note":""},
             {"title":"Read ch 4-6","cls":1,"due":"2026-07-29","done":False,"note":""}],
 "lectures":[{"cls":0,"num":"01","title":"Aromatics","date":"2026-07-27","meta":"","notes":"benzene","ranges":{}}],
 "active":0}
def _corrupt_middle(d):
    """Corrupt the class at index 1 and renumber what points past it, so the
    file stays self-consistent: this is a store with one damaged record, not a
    store with dangling references."""
    d["classes"].insert(1, "a class record that got corrupted")
    for rec in list(d["lectures"]) + list(d["homework"]):
        if isinstance(rec, dict) and isinstance(rec.get("cls"), int) \
                and rec["cls"] >= 1:
            rec["cls"] += 1


MUT = {
 "control":        lambda d: None,
 "lecture->missing class": lambda d: d["lectures"].append({"cls":9,"num":"02","title":"Later note","date":"2026-07-28","meta":"","notes":"IMPORTANT TEXT","ranges":{}}),
 "class not a dict":       lambda d: d["classes"].append("junk"),
 # ROUND 6. The case above appends its junk record at the END, where skipping it
 # shifts nothing -- so it never tested the thing that actually breaks. A class
 # record corrupted IN PLACE, with every other record still correctly naming the
 # classes around it, closes a gap in the middle and moves every later class DOWN
 # one index while the lectures and assignments still hold the file's numbering.
 # Measured before the fix: the Linear Algebra assignment came back untied and
 # the lecture belonging to a later class was filed under the first one.
 "class corrupted mid-list": lambda d: _corrupt_middle(d),
 # An assignment record with no title used to be DROPPED outright, taking the
 # note and due date on it with it -- two records in, one out, and the
 # close-time save wrote that over the only copy. Anything with content in it
 # is now salvaged under a placeholder name.
 "homework with no title": lambda d: d["homework"].append(
        {"cls":1,"due":"2026-07-31","done":False,"note":"chapters 4 to 7"}),
 "lectures not a list":    lambda d: d.__setitem__("lectures", {}),
 "cls not a number":       lambda d: d["lectures"].append({"cls":"one","num":"02","title":"x","date":"2026-07-28","meta":"","notes":"KEEP ME","ranges":{}}),
 # ROUND 5. `classes` stored as an OBJECT -- the keyed-by-name wrapper every
 # other store in this OS already tolerates -- was read as "no classes", and a
 # lecture whose class could not be resolved was then DISCARDED. Opening
 # Academics and pressing Esc deleted every lecture note in the file. The
 # homework list survived, so the empty-model guard never fired and nothing
 # warned anybody; only the .bak stood between the user and a lost term.
 "classes is an object":   lambda d: d.__setitem__(
        "classes", {"a": d["classes"][0], "b": d["classes"][1]}),
 # Same wound, no salvage: not one class can be read. The notes must still be
 # there afterwards, parked under a recovery class, not deleted.
 "classes is a number":    lambda d: d.__setitem__("classes", 3),
 "classes not a list":     lambda d: d.__setitem__("classes", None),
 # A class's timetable stored as an object costs that class its meetings.
 "meets is an object":     lambda d: d["classes"][0].__setitem__(
        "meets", {"m0": d["classes"][0]["meets"][0],
                  "m1": d["classes"][0]["meets"][1]}),
 "homework is an object":  lambda d: d.__setitem__(
        "homework", {"h0": d["homework"][0], "h1": d["homework"][1]}),
 "file is not json":       None,
}
CASES = ["control", "lecture->missing class", "class not a dict",
         "class corrupted mid-list", "homework with no title",
         "lectures not a list", "cls not a number",
         "classes is an object", "classes is a number", "classes not a list",
         "meets is an object", "homework is an object", "file is not json"]

# (classes, class times, assignments) that must be on screen AND on disk after
# open+Esc. Everything not listed keeps the whole term intact.
EXPECT = {
 # No class survives the damage, so the notes get ONE recovery class to live
 # under and the assignments keep their titles but lose their class.
 "classes is a number":  (1, 0, 2),
 "classes not a list":   (1, 0, 2),
 "meets is an object":   (2, 3, 2),
 # the salvaged titleless record makes three
 "homework with no title": (2, 3, 3),
}

if len(sys.argv) < 2:
    # Driver: re-invoke ourselves once per case and judge the results.
    import subprocess
    ok = True
    for c in CASES:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), c],
                           capture_output=True, text=True, timeout=180,
                           env=dict(os.environ))
        line = (r.stdout or "").strip().splitlines()
        line = line[-1] if line else "(no output)"
        print(line)
        case_ok = r.returncode == 0 and line.startswith("PASS")
        if not case_ok and r.stderr:
            detail = r.stderr.strip().splitlines()[-1]
            print("      child failed: " + detail)
        ok = ok and case_ok
    print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    raise SystemExit(0 if ok else 1)

name = sys.argv[1]
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
notes = []
want_owner = {}
if name == "file is not json":
    open(STORE,"w").write("{this is not json")
else:
    d = json.loads(json.dumps(GOOD)); MUT[name](d); json.dump(d, open(STORE,"w"))
    # Every word the user typed into a lecture, whatever shape the file is in.
    # This is the thing nothing else can reproduce, and it is what the round-5
    # `classes` bug silently deleted while every other counter looked healthy.
    for lec in (d["lectures"] if isinstance(d.get("lectures"), list) else []):
        if isinstance(lec, dict) and lec.get("notes"):
            notes.append(lec["notes"])
    # WHICH CLASS each assignment names, read out of the file by LABEL. Counting
    # records is not enough and was not enough: every counter stayed healthy
    # while a damaged store quietly re-filed the surviving work onto the wrong
    # classes. Only titles whose class is a readable record with a name are
    # asserted -- where the class list itself is unreadable, losing the tie is
    # the correct outcome, not a regression.
    _cl = d.get("classes") if isinstance(d.get("classes"), list) else []
    for h in (d["homework"] if isinstance(d.get("homework"), list) else []):
        if not (isinstance(h, dict) and h.get("title")):
            continue
        ci = h.get("cls")
        if isinstance(ci, int) and 0 <= ci < len(_cl) and isinstance(_cl[ci], dict):
            lab = _cl[ci].get("label") or ""
            if lab:
                want_owner[h["title"]] = lab
w = academics.Academics()
n=0
while Gtk.events_pending() and n<300: Gtk.main_iteration_do(False); n+=1
loaded = (len(w.classes), sum(len(c.get("meets",[])) for c in w.classes), len(w.homework), len(w.lectures))
misfiled = []
for h in w.homework:
    want = want_owner.get(h.get("title"))
    if not want:
        continue
    got = w._class_label(h.get("cls", -1)) or "(untied)"
    if got != want:
        misfiled.append("%s: %s -> %s" % (h.get("title"), want, got))
ev = Gdk.EventKey(); ev.keyval = Gdk.KEY_Escape; ev.state = 0
w._on_key(w, ev)
w._on_destroy()
try:
    a = json.load(open(STORE))
    disk = (len(a.get("classes",[])), sum(len(c.get("meets",[])) for c in a.get("classes",[])),
            len(a.get("homework",[])), len(a.get("lectures",[])))
except Exception as e:
    disk = "unreadable"
extra = sorted(f for f in os.listdir(H+"/.config/notebook") if f != "academics.json")
# What must be true: a store with a shape problem keeps the whole term, and a
# store that is not JSON at all is preserved rather than replaced.
if name == "file is not json":
    good = any(f.startswith("academics.json.damaged-") for f in extra)
    why = "the unreadable file must be moved aside, not overwritten"
else:
    want = EXPECT.get(name, (2, 3, 2))
    raw = "" if disk == "unreadable" else open(STORE).read()
    lost = [t for t in notes if t not in raw]
    good = (loaded[:3] == want and disk != "unreadable" and disk[:3] == want
            and not lost and not misfiled)
    if lost:
        why = "every lecture note must survive open+Esc; lost %r" % lost
    elif misfiled:
        why = "work must stay on the class it names; misfiled %r" % misfiled
    else:
        why = "%d classes / %d class times / %d assignments must survive open+Esc" % want
print("%-4s %-24s opened=%-14s after Esc on disk=%-14s kept=%s%s"
      % ("PASS" if good else "FAIL", name, loaded, disk, extra or "NONE",
         "" if good else "   <- " + why))
