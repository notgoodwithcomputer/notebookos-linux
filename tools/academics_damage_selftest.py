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
MUT = {
 "control":        lambda d: None,
 "lecture->missing class": lambda d: d["lectures"].append({"cls":9,"num":"02","title":"Later note","date":"2026-07-28","meta":"","notes":"IMPORTANT TEXT","ranges":{}}),
 "class not a dict":       lambda d: d["classes"].append("junk"),
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
        ok = ok and not line.startswith("FAIL")
    print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    raise SystemExit(0 if ok else 1)

name = sys.argv[1]
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
notes = []
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
w = academics.Academics()
n=0
while Gtk.events_pending() and n<300: Gtk.main_iteration_do(False); n+=1
loaded = (len(w.classes), sum(len(c.get("meets",[])) for c in w.classes), len(w.homework), len(w.lectures))
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
            and not lost)
    why = ("every lecture note must survive open+Esc; lost %r" % lost) if lost \
        else "%d classes / %d class times / %d assignments must survive open+Esc" % want
print("%-4s %-24s opened=%-14s after Esc on disk=%-14s kept=%s%s"
      % ("PASS" if good else "FAIL", name, loaded, disk, extra or "NONE",
         "" if good else "   <- " + why))
