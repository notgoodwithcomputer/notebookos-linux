"""Behavioural checks on the new class management + print routing."""
import os, sys, shutil, json
# Per-PROCESS home. A fixed one is shared by two copies of this suite, and the
# NB_HOME is what scopes nbapp's single-instance marker dir: the loser of the
# race is os._exit(0)ed by claim_single_instance() with NO output and EXIT
# STATUS 0, which reads as a pass while nothing was tested.
H="/tmp/nbhome-acadclass-%d" % os.getpid(); os.environ["NB_HOME"]=H
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
import academics
R=[]
def check(n, ok, d=""):
    R.append(bool(ok)); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "   <- %s" % (d,)))
def pump(n=200):
    i=0
    while Gtk.events_pending() and i<n: Gtk.main_iteration_do(False); i+=1

w = academics.Academics(); pump()
w.classes = [
  {"label":"Chem","color":"#9A7B4F","room":"D2210","instructor":"Peraza","meets":[{"day":0,"start":"09:00","end":"10:20","room":""}]},
  {"label":"Maths","color":"#4A5E73","room":"W7500","instructor":"Iyer","meets":[{"day":1,"start":"13:30","end":"14:50","room":""}]},
  {"label":"Hist","color":"#6E7B57","room":"B154","instructor":"Almeida","meets":[]}]
w.lectures = [{"cls":1,"num":"01","title":"Eigen","date":"2026-07-27","meta":"","notes":"x","ranges":{}}]
w.homework = [{"title":"PS4","cls":0,"due":"2026-07-30","done":False,"note":""},
              {"title":"Read","cls":1,"due":"2026-07-29","done":False,"note":""},
              {"title":"Essay","cls":2,"due":"2026-07-28","done":True,"note":""}]
w.active = 0
w._refresh_sidebar(); pump()

# 1. Deleting class 0 must NOT re-tag the other assignments.
w._confirm = lambda *a, **k: True
w._delete_class_at(0); pump()
labels = {h["title"]: (w._class_label(h["cls"]) if h["cls"] >= 0 else "-") for h in w.homework}
check("assignment of the deleted class loses its tag", labels.get("PS4") == "-", labels)
check("Maths assignment still says Maths", labels.get("Read") == "Maths", labels)
check("Hist assignment still says Hist", labels.get("Essay") == "Hist", labels)
check("the lecture followed its class down an index",
      w.lectures and w._class_label(w.lectures[0]["cls"]) == "Maths",
      [w._class_label(l["cls"]) for l in w.lectures])

# 2. A class with no lecture is editable (the old blocker).
ci = 1                                   # Hist, which has no lecture at all
w._class_dialog = lambda *a, **k: {"label":"History","color":"#6E7B57","room":"B200","instructor":"A"}
w._edit_class(ci); pump()
check("a class with no lectures can be renamed", w.classes[ci]["label"] == "History",
      [c["label"] for c in w.classes])
check("...and its room is saved too", w.classes[ci]["room"] == "B200")

# 3. Adding a class from the Schedule makes NO phantom lecture.
n_lec = len(w.lectures)
w._class_dialog = lambda *a, **k: {"label":"Physics","color":"#8A6D5B","room":"","instructor":""}
w._new_class_only(); pump()
check("Add a class creates the class", w.classes[-1]["label"] == "Physics")
check("...and no phantom lecture with it", len(w.lectures) == n_lec, len(w.lectures))

# 4. Print/Export follow the view.
w._set_view("schedule"); pump()
check("Schedule prints the timetable", w._print_target()[1] == "Timetable")
w._set_view("homework"); pump()
check("Homework prints the homework", w._print_target()[1] == "Homework")
w._set_view("notes"); pump()
check("Notes prints the lecture", w._print_target()[1] == "Lecture")

# 5. The two new renderers actually produce a PDF.
for view, fn in (("schedule", w._make_schedule_pdf), ("homework", w._make_homework_pdf)):
    path = "/tmp/acad-%s.pdf" % view
    try:
        fn(path); ok = os.path.getsize(path) > 800
        err = os.path.getsize(path) if os.path.exists(path) else "missing"
    except Exception as exc:
        ok, err = False, repr(exc)
    check("%s renders a real PDF" % view, ok, err)

# 6. The sidebar swaps its primary action with the view.
for view, want in (("notes","New Lecture"),("schedule","Add a class"),
                   ("homework","Add an assignment")):
    w._set_view(view); pump()
    check("sidebar button on %s says %r" % (view, want),
          w.newbtn_label.get_text() == want, w.newbtn_label.get_text())

print("\n%d checks, %d passed, %d failed" % (len(R), sum(R), len(R)-sum(R)))
print("RESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
