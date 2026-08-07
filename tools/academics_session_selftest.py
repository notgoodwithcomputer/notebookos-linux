#!/usr/bin/env python3
"""One whole term, driven end to end, the way a person would drive it.

Every other academics suite tests a mechanism. This one tests the JOIN: start on
a fresh install with nothing, build a term up, live in it, break bits of it, undo
some of that, close the app and open it again — asserting after every step that
the model is still coherent and that nothing the user typed has gone missing.

The bugs this shape catches are the ones no unit check can see, because they only
appear when one operation leaves state the NEXT operation reads differently. Two
already found in this app were exactly that shape: saving before the canvas had
been rebuilt copied the outgoing lecture's text over a surviving one, and
deleting a class shifted lecture indices but not homework's.

INVARIANTS, asserted after every single step:
  * every lecture and every assignment points at a real class, or at nothing
  * every class record carries the whole schema
  * nothing the user typed has vanished from the model

THE SCENARIO HAD TO BE BUILT TO BITE. Its first version deleted the LAST class,
which shifts no index at all — so breaking the reindex in _delete_class_at
(`elif c > ci: h["cls"] = c - 1` -> `pass`) left this entire suite GREEN. A
walkthrough that never reaches the branch is a demo, not a test. There are now
three classes and the MIDDLE one is deleted, with work hanging off the class
after it.

RED PROOF (M1), measured with that same mutation:

    FAIL after deleting a class the model is coherent
         <- assignment 2 -> class 2 of 2
    FAIL work on a LATER class still points at that class after the delete
         <- 'Read chapters 9 and 10' now reads ''
    RESULT: 2 FAILED

A second mutation (undo restoring only the first lecture) makes this suite CRASH
with an IndexError rather than report — which is a real failure signal, but only
because the runner distinguishes a crashed suite from a failed one. A crashed
suite tested nothing; do not let one be counted as "one failure".
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acadsession-%d" % os.getpid()
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
TYPED = []          # every sentence the "user" has typed and not deleted


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


SHAPE = {"label", "color", "room", "instructor", "meets"}


def invariants(app, step):
    """The three things that must be true after EVERY step."""
    bad = []
    for i, c in enumerate(app.classes):
        missing = SHAPE - set(c)
        if missing:
            bad.append("class %d has no %s" % (i, ", ".join(sorted(missing))))
    for i, l in enumerate(app.lectures):
        if not -1 <= l.get("cls", -1) < len(app.classes):
            bad.append("lecture %d -> class %d of %d"
                       % (i, l.get("cls"), len(app.classes)))
    for i, h in enumerate(app.homework):
        if not -1 <= h.get("cls", -1) < len(app.classes):
            bad.append("assignment %d -> class %d of %d"
                       % (i, h.get("cls"), len(app.classes)))
    check("after %s the model is coherent" % step, not bad, "; ".join(bad))


def typing_survives(app, step):
    body = " ".join(l.get("notes", "") for l in app.lectures)
    lost = [t for t in TYPED if t not in body]
    check("after %s nothing typed has been lost" % step, not lost, lost)


def type_into(app, text):
    buf = app.body.get_buffer()
    buf.set_text(text)
    pump()
    app._capture_active()
    TYPED.append(text)


# ---------------------------------------------------------------- fresh start
app = academics.Academics()
pump()
check("a fresh install opens empty",
      app.classes == [] and app.lectures == [] and app.homework == [],
      (app.classes, app.lectures, app.homework))
invariants(app, "opening empty")

# --------------------------------------------------- first lecture, first class
app._new_lecture()
pump()
check("the first lecture makes itself a class",
      len(app.classes) == 1 and len(app.lectures) == 1,
      (len(app.classes), len(app.lectures)))
invariants(app, "the first lecture")
type_into(app, "sp3 carbon is tetrahedral")

# ----------------------------------------------------------- name that class
app._name_dialog = lambda *a, **k: "Organic Chemistry"
app._rename_class()
pump()
check("renaming the class took",
      app.classes[0]["label"] == "Organic Chemistry", app.classes[0])
invariants(app, "renaming a class")
typing_survives(app, "renaming a class")

# ------------------------------------------------------------- a second class
app._class_dialog = lambda *a, **k: {"label": "Linear Algebra",
                                     "color": academics.CLASS_COLORS[1],
                                     "room": "Wean 7500", "instructor": "Iyer"}
app._new_class_only()
pump()
check("a second class was added", len(app.classes) == 2,
      [c["label"] for c in app.classes])
invariants(app, "adding a class")

# --------------------------------------------------------------- class times
app._meeting_dialog = lambda *a, **k: (0, 0, "09:00", "10:20", "Doherty 2210")
app._add_meeting()
pump()
app._meeting_dialog = lambda *a, **k: (1, 2, "13:30", "14:50", "")
app._add_meeting()
pump()
check("both class times are on the timetable",
      sum(len(c.get("meets") or []) for c in app.classes) == 2,
      [(c["label"], c.get("meets")) for c in app.classes])
invariants(app, "adding class times")

# ---------------------------------------------- a lecture in the second class
app._select(0)
pump()
app._new_lecture()
pump()
# _new_lecture picks its class from the TIMETABLE (whichever class meets now, or
# next), so which one it lands in depends on the wall clock — fine for a person,
# useless for a check that has to mean the same thing at 09:00 and at 23:00.
# File it on Linear Algebra explicitly so the delete below has something of that
# class to take with it.
app.lectures[-1]["cls"] = 1
app._refresh_sidebar()
app._refresh_canvas()
pump()
type_into(app, "det(A - lambda I) = 0")
check("the second lecture belongs to the second class",
      app.lectures[-1]["cls"] == 1, app.lectures[-1])
invariants(app, "a second lecture")
typing_survives(app, "a second lecture")

# ------------------------------------------------------- homework and an exam
app._homework_dialog = lambda *a, **k: {
    "title": "Problem set 4", "cls": 0, "due": "2026-08-20", "note": "Q1-Q9",
    "kind": "work"}
app._new_homework()
pump()
app._homework_dialog = lambda *a, **k: {
    "title": "Midterm", "cls": 1, "due": "2026-08-20", "note": "",
    "kind": "exam"}
app._new_homework()
pump()
check("both pieces of work are on the list", len(app.homework) == 2,
      app.homework)
check("the exam is stored as an exam",
      any(h.get("kind") == "exam" for h in app.homework), app.homework)
invariants(app, "adding work")

# ------------------------------------------------------------- tick one off
app._on_hw_toggle(type("B", (), {"get_active": lambda s: True})(), 0)
pump()
check("ticking an assignment marks it done", app.homework[0]["done"] is True,
      app.homework[0])
invariants(app, "ticking work off")

# ------------------------------------------------------------- a third class
# Deleting the LAST class shifts nothing, so a scenario that only ever does that
# cannot see a broken reindex — measured: breaking the `elif c > ci` branch in
# _delete_class_at left this whole suite green. There has to be a class AFTER
# the one that gets deleted, with work hanging off it.
app._class_dialog = lambda *a, **k: {"label": "Modern Europe",
                                     "color": academics.CLASS_COLORS[2],
                                     "room": "Baker 154", "instructor": ""}
app._new_class_only()
pump()
app._homework_dialog = lambda *a, **k: {
    "title": "Read chapters 9 and 10", "cls": 2, "due": "2026-08-21",
    "note": "", "kind": "work"}
app._new_homework()
pump()
check("the third class has work of its own",
      any(h["cls"] == 2 for h in app.homework), app.homework)
invariants(app, "a third class")

# ------------------------------------------- delete a class, then take it back
europe_work = "Read chapters 9 and 10"
before_lectures = len(app.lectures)
before_typed = list(TYPED)
# The lecture of the class being deleted goes with it, deliberately and with a
# confirm — so its text is expected to leave the model too, and only comes back
# with the undo below.
TYPED.remove("det(A - lambda I) = 0")
app._confirm = lambda *a, **k: True
app._delete_class_at(1)
pump()
invariants(app, "deleting a class")
check("deleting a class took its lectures with it",
      len(app.lectures) < before_lectures,
      "%d -> %d" % (before_lectures, len(app.lectures)))
check("the assignment of the deleted class survived, untied",
      any(h["cls"] == -1 for h in app.homework), app.homework)
# THE ONE THAT MATTERS. Modern Europe was at index 2; deleting Linear Algebra at
# index 1 slides it down to 1, and its assignment has to slide with it. When it
# did not, an assignment silently changed which class it belonged to — the same
# wound, by a different road, as the loader bug in this app's history.
still = [h for h in app.homework if h["title"] == europe_work]
check("work on a LATER class still points at that class after the delete",
      still and app._class_label(still[0]["cls"]) == "Modern Europe",
      "%r now reads %r" % (europe_work,
                           app._class_label(still[0]["cls"]) if still
                           else "(gone)"))

app.undo.undo()
pump()
invariants(app, "undoing a class delete")
check("undo brought the class back", len(app.classes) == 3,
      [c["label"] for c in app.classes])
check("undo brought its lectures back", len(app.lectures) == before_lectures,
      "%d, expected %d" % (len(app.lectures), before_lectures))
TYPED[:] = before_typed
typing_survives(app, "undoing a class delete")

# ------------------------------------------------------------------- search
app.search.set_text("eigen")
app._filter_tick()
pump()
app._clear_search()
pump()
invariants(app, "searching and clearing")
typing_survives(app, "searching and clearing")

# ------------------------------------------------------- every view survives
for view in ("schedule", "homework", "notes"):
    app._set_view(view)
    pump()
    invariants(app, "switching to %s" % view)
typing_survives(app, "switching views")

# ------------------------------------------------------------- export to PDF
docs = os.path.join(H, "Documents")
os.makedirs(docs, exist_ok=True)
for view in ("notes", "schedule", "homework"):
    app._set_view(view)
    pump()
    target = os.path.join(H, "out-%s.pdf" % view)
    try:
        {"notes": app._make_active_pdf, "schedule": app._make_schedule_pdf,
         "homework": app._make_homework_pdf}[view](target)
        ok = os.path.getsize(target) > 800
        why = os.path.getsize(target) if os.path.exists(target) else "missing"
    except Exception as exc:
        ok, why = False, repr(exc)
    check("the %s view renders a real PDF" % view, ok, why)
invariants(app, "exporting")

# ------------------------------------------------ close it and open it again
app._on_destroy()
pump()
app.destroy()
pump()

reopened = academics.Academics()
pump()
check("the term is still there after closing and reopening",
      len(reopened.classes) == 3 and len(reopened.lectures) == 2
      and len(reopened.homework) == 3,
      (len(reopened.classes), len(reopened.lectures), len(reopened.homework)))
invariants(reopened, "reopening")
typing_survives(reopened, "reopening")
check("the exam is still an exam after reopening",
      any(h.get("kind") == "exam" for h in reopened.homework),
      reopened.homework)
check("the finished assignment is still finished",
      any(h["done"] for h in reopened.homework), reopened.homework)

# And the file on disk agrees with what is on screen.
with open(STORE) as f:
    disk = json.load(f)
check("the file on disk matches the model in memory",
      len(disk.get("classes", [])) == len(reopened.classes)
      and len(disk.get("lectures", [])) == len(reopened.lectures)
      and len(disk.get("homework", [])) == len(reopened.homework),
      (len(disk.get("classes", [])), len(disk.get("lectures", [])),
       len(disk.get("homework", []))))

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
