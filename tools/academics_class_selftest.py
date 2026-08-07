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

# 7. EVERY route that makes a class makes the SAME SHAPE.
# `_append_class` -- the one "New Lecture" takes on a fresh install -- built a
# class from only {label, color}, so it was the only class in the app without
# room, instructor or meets. Nothing crashed, because a class with no `meets`
# key contributes no meetings to the timetable and so has no block to select;
# and the loader fills the fields in, so save-and-reopen quietly healed it.
# Meanwhile _remove_meeting indexes ["meets"] directly, twice. A creator that
# disagrees with the schema is a KeyError waiting for a refactor.
SHAPE = {"label", "color", "room", "instructor", "meets"}
for route, make in (
        ("_append_class", lambda a: a._append_class()),
        ("_new_class_only", lambda a: a._new_class_only()),
        ("_add_meeting on an empty term", lambda a: a._add_meeting())):
    a = academics.Academics(); pump()
    a.classes = []
    a._name_dialog = lambda *ar, **k: "Made by %s" % route
    a._class_dialog = lambda *ar, **k: {"label": "Made by %s" % route,
                                        "color": academics.CLASS_COLORS[0],
                                        "room": "", "instructor": ""}
    a._meeting_dialog = lambda *ar, **k: (0, 0, "09:00", "10:00", "")
    try:
        make(a)
    except Exception as exc:
        check("%s makes a class at all" % route, False, repr(exc))
        continue
    missing = SHAPE - set(a.classes[0]) if a.classes else SHAPE
    check("%s makes a whole class record" % route, not missing,
          "no %s in %r" % (", ".join(sorted(missing)), a.classes[:1]))

# 8. _prune_empty_classes must never DELETE a lecture.
# It used to read `self.lectures = [l for l in self.lectures if 0 <= l["cls"] < n]`
# -- silently dropping every lecture whose class index was out of range, while
# the very next loop merely untied an assignment in exactly the same state.
# Measured side by side before the fix: one orphaned lecture in, ZERO out, its
# text gone from the model; one orphaned assignment in, one out. A note is the
# one thing in this file that cannot be re-derived. Both call sites happen to
# pass in-range indices today, so this never fired -- a dormant shredder behind
# a docstring promising it did not exist, which is how the previous two rounds
# of this bug got in.
a = academics.Academics(); pump()
a.classes = [{"label": "Chem", "color": academics.CLASS_COLORS[0], "room": "",
              "instructor": "", "meets": []}]
a.lectures = [
    {"cls": 0, "num": "01", "title": "Kept", "date": "2026-08-07", "meta": "",
     "notes": "fine", "ranges": {}},
    {"cls": 5, "num": "02", "title": "Orphan", "date": "2026-08-07", "meta": "",
     "notes": "THE ONLY COPY OF THIS SENTENCE", "ranges": {}}]
a.homework = [{"title": "HW", "cls": 5, "due": "", "done": False, "note": "n"}]
a.active = 0
a._prune_empty_classes()
kept = [l["title"] for l in a.lectures]
check("an orphaned lecture is kept, not deleted", "Orphan" in kept, kept)
check("its text is still there",
      any("THE ONLY COPY OF THIS SENTENCE" == l["notes"] for l in a.lectures))
check("it is parked on a real class so the sidebar can show it",
      all(0 <= l["cls"] < len(a.classes) for l in a.lectures),
      [l["cls"] for l in a.lectures])
check("an orphaned assignment is untied rather than destroyed",
      len(a.homework) == 1 and a.homework[0]["cls"] == -1, a.homework)

# 9. The export path must not borrow the LAST class for an untied lecture.
# _pdf_name and _make_active_pdf both did `self.classes[lec["cls"]]`. A cls of
# -1 means "no class", and a negative index is the last element rather than a
# miss -- so an untied lecture exported under whichever class happened to be
# last, in the PDF header AND in the filename. Same trap _class_label and
# _class_color are written the way they are to avoid; the export path had its
# own copy.
a = academics.Academics(); pump()
a.classes = [{"label": "Chem", "color": academics.CLASS_COLORS[0], "room": "",
              "instructor": "", "meets": []},
             {"label": "Maths", "color": academics.CLASS_COLORS[1], "room": "",
              "instructor": "", "meets": []}]
untied = {"cls": -1, "num": "01", "title": "Loose note", "date": "2026-08-07",
          "meta": "", "notes": "x", "ranges": {}}
check("an untied lecture resolves to NO class, not the last one",
      a._class_of(untied) == {}, a._class_of(untied))
name = a._pdf_name(untied)
check("its export filename does not claim the last class",
      "maths" not in name, name)
check("its export filename still names the lecture",
      "loose-note" in name, name)

# 10. A lecture can be re-filed under a different class.
# It could not be. The class was chosen once, at creation, and _new_lecture
# GUESSES it from the timetable (whichever class meets now, or next) -- so a
# note taken in a free period or just before the hour was filed under the wrong
# class permanently. An ASSIGNMENT has had a class combo in its dialog all
# along; the thing you actually write during a lecture had nothing.
a = academics.Academics(); pump()
a.classes = [{"label": "Chem", "color": academics.CLASS_COLORS[0], "room": "",
              "instructor": "", "meets": []},
             {"label": "Maths", "color": academics.CLASS_COLORS[1], "room": "",
              "instructor": "", "meets": []}]
a.lectures = [{"cls": 1, "num": "01", "title": "Already in maths",
               "date": "2026-08-07", "meta": "", "notes": "n", "ranges": {}},
              {"cls": 1, "num": "02", "title": "Also maths",
               "date": "2026-08-07", "meta": "", "notes": "n", "ranges": {}},
              {"cls": 0, "num": "01", "title": "Misfiled",
               "date": "2026-08-07", "meta": "", "notes": "THE TEXT",
               "ranges": {"bold": [[0, 3]]}}]
a.active = 2
a._refresh_sidebar(); a._refresh_canvas(); pump()
# The model above was assigned directly, behind undo's back, so its baseline is
# still the EMPTY notebook this app opened with -- an undo would restore that
# and wipe the fixture. Re-baseline here so the undo below is measuring the
# move and nothing else.
a.undo.reset()

# Drive the REAL dialog; only Gtk.Dialog.run is replaced. The combo, its
# translated class names and the code that reads the choice back are all real.
real_shell = a._dialog_shell
def _shell(title, _r=real_shell):
    d = _r(title)
    d.run = lambda: Gtk.ResponseType.OK
    return d
a._dialog_shell = _shell
def _combo_on(app, idx):
    c = Gtk.ComboBoxText()
    for cl in app.classes:
        c.append_text(cl.get("label", ""))
    c.set_active(idx)
    return c

target = [1]
a._class_combo = lambda selected=-1, allow_none=False: _combo_on(a, target[0])

a._move_lecture(); pump()
check("the lecture moved to the chosen class", a.lectures[2]["cls"] == 1,
      a.lectures[2])
check("its text came with it", a.lectures[2]["notes"] == "THE TEXT",
      a.lectures[2])
check("its formatting came with it", a.lectures[2].get("ranges"),
      a.lectures[2])
# The number is per class, and it is how the sidebar tells one lecture of a
# class from another -- moving in on top of an existing "01" would give that
# class two of them.
nums = [l["num"] for l in a.lectures if l["cls"] == 1]
check("it took the next free number in its new class",
      len(nums) == len(set(nums)), nums)
# TWO moves, then ONE undo. Undoing a single move and landing back on the
# BASELINE would look identical to undoing it correctly when there has only ever
# been one move -- measured: deleting the undo.checkpoint() from _move_lecture
# left the one-move version of this check green, because commit() then folded
# the move into the baseline and the baseline happened to be the right answer.
# Stepping back from the second move to the first is the difference.
target[0] = 0
a._move_lecture(); pump()
check("a second move lands where it was told", a.lectures[2]["cls"] == 0,
      a.lectures[2])
a.undo.undo(); pump()
check("undo steps back ONE move, not all of them", a.lectures[2]["cls"] == 1,
      a.lectures[2])
a.undo.undo(); pump()
check("a second undo reaches the original class", a.lectures[2]["cls"] == 0,
      a.lectures[2])

# TYPING THEN MOVING must be two undo steps, not one. This is what
# undo.checkpoint() is actually for -- it flushes a half-finished typing step so
# the structural edit becomes its own -- and without a scenario that has typing
# IN FLIGHT, removing the checkpoint from _move_lecture changes nothing at all
# and "the mutation was caught" would be a lie. Ctrl+Z after re-filing a lecture
# has to undo the re-filing, not swallow the sentence you just wrote with it.
a.active = 2
a._refresh_canvas(); pump()
a.undo.reset()
buf = a.body.get_buffer()
buf.set_text("a sentence typed just before the move")
pump()                                   # arms the typing step via _mark_editing
a._capture_active()
target[0] = 1
a._move_lecture(); pump()
a.undo.undo(); pump()
check("undoing after a move reverts the MOVE", a.lectures[2]["cls"] == 0,
      a.lectures[2])
check("...and does not swallow the sentence typed before it",
      "a sentence typed just before the move" in a.lectures[2]["notes"],
      a.lectures[2]["notes"][:60])

print("\n%d checks, %d passed, %d failed" % (len(R), sum(R), len(R)-sum(R)))
print("RESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
