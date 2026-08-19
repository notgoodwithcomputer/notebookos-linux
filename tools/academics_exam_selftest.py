#!/usr/bin/env python3
"""An exam is a thing this app can hold.

Homework was the only dated item in Academics, so the single date a term is
actually organised around — when you sit the exam — could not be recorded at
all. An exam rides the same list rather than becoming a fourth concept: same
due-date grouping, same class tie, same tick when it is behind you, plus a
`kind` field that is either "work" or "exam".

The compatibility rule is the important one. `kind` did not exist until now, so
every assignment in every existing store lacks it, and a store written by an
older build must keep working: anything that is not the word "exam" reads as
ordinary work. Nothing is migrated, nothing is rewritten, nothing is lost.

THIS SUITE WAS VACUOUS ON ITS FIRST WRITING, and the red proof is what showed
it. The language checks exist to catch a dialog that decides the kind by
comparing a widget's LABEL to "Exam" — nbi18n translates labels in place, so
that would store "work" for every exam in every language but English. Mutating
the app to do exactly that left this suite GREEN in French and Chinese, because
"Exam" and "Assignment" are brand-new source strings no catalog has been merged
with yet: `_t()` returned them unchanged everywhere, so the French label WAS the
English word and there was nothing to catch. The child now injects catalog
entries for those two keys itself, and asserts the label really did change —
a check that cannot fail is not a check, and "it passed in three languages" was
worth nothing until the languages actually differed.

RED PROOFS (M1), measured:

  1. drop the loader default (`"kind": ("exam" if ... else "work")` removed)
       FAIL an assignment from a store with no `kind` reads as work   <- None
       FAIL `kind` is case-insensitive            <- ... 'kind': 'EXAM' ...
       FAIL an unknown kind reads as ordinary work <- ... 'kind': 'quiz' ...
       FAIL a non-string kind reads as ordinary work
       RESULT: 4 FAILED
  2. read the radio by LABEL instead of position
     (`kind_btns["exam"].get_active()` -> `... and get_label() == "Exam"`)
       FAIL the real dialog returns an exam as an exam in en
            <- {'exam': 'work', 'work': 'work', 'lang': 'en', 'label': '‹exam›'}
       ...and the same in fr and zh. RESULT: 3 FAILED
       (Before the catalog injection above, this mutation produced ALL PASS.)
  3. drop the exam-first tiebreak in _homework_buckets
       FAIL an exam outranks work due the same day   <- ['Essay', 'Midterm']
       RESULT: 1 FAILED
"""
import os
import sys
import json
import shutil
import subprocess

H = "/tmp/nbhome-acadexam-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/academics.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, DE)

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


# ------------------------------------------- a store from before `kind` existed
OLD = {"classes": [{"label": "Chem", "color": "#9A7B4F", "room": "",
                    "instructor": "", "meets": []}],
       "lectures": [],
       "homework": [{"title": "Problem set 4", "cls": 0, "due": "2026-08-20",
                     "done": False, "note": ""}],
       "active": -1}
with open(STORE, "w") as f:
    json.dump(OLD, f)

app = academics.Academics()
pump()
check("an assignment from a store with no `kind` reads as work",
      app.homework[0].get("kind") == "work", app.homework[0].get("kind"))
check("nothing else about it changed",
      app.homework[0]["title"] == "Problem set 4"
      and app.homework[0]["due"] == "2026-08-20", app.homework[0])

# ---------------------------------------------------- garbage in the kind field
app.homework = academics.Academics._clean_homework(
    [{"title": "A", "cls": 0, "due": "", "done": False, "kind": "EXAM"},
     {"title": "B", "cls": 0, "due": "", "done": False, "kind": "quiz"},
     {"title": "C", "cls": 0, "due": "", "done": False, "kind": 7},
     {"title": "D", "cls": 0, "due": "", "done": "false"},
     {"title": "E", "cls": 0, "due": "", "done": True}], {0: 0})
check("`kind` is case-insensitive", app.homework[0]["kind"] == "exam",
      app.homework[0])
check("an unknown kind reads as ordinary work",
      app.homework[1]["kind"] == "work", app.homework[1])
check("a non-string kind reads as ordinary work",
      app.homework[2]["kind"] == "work", app.homework[2])
check("the string 'false' does not complete an assignment",
      app.homework[3]["done"] is False, app.homework[3])
check("JSON true still completes an assignment",
      app.homework[4]["done"] is True, app.homework[4])

# --------------------------------------------------- the dialog round-trips it
app.classes = [{"label": "Chem", "color": "#9A7B4F", "room": "",
                "instructor": "", "meets": []}]
app.homework = []
app._homework_dialog = lambda *a, **k: {
    "title": "Midterm", "cls": 0, "due": "2026-08-20", "note": "",
    "kind": "exam"}
app._new_homework()
pump()
check("an exam added through the dialog is stored as one",
      app.homework and app.homework[0].get("kind") == "exam", app.homework)

# It must survive a save and a reload, which is where a field that is written
# but never read back would show up.
app._save_to_disk()
pump()
app.destroy()
pump()
app2 = academics.Academics()
pump()
check("an exam survives a save and reload",
      app2.homework and app2.homework[0].get("kind") == "exam", app2.homework)

# ------------------------------------------------------- exams sort first
app2.homework = [
    {"title": "Essay", "cls": 0, "due": "2026-08-20", "done": False,
     "note": "", "kind": "work"},
    {"title": "Midterm", "cls": 0, "due": "2026-08-20", "done": False,
     "note": "", "kind": "exam"}]
order = []
for _key, _name, idxs in app2._homework_buckets():
    order += [app2.homework[i]["title"] for i in idxs]
check("an exam outranks work due the same day", order[:2] == ["Midterm", "Essay"],
      order)

# ------------------------------------------------------- it reaches the paper
pdf = os.path.join(H, "hw.pdf")
app2._make_homework_pdf(pdf)
ok = os.path.exists(pdf) and os.path.getsize(pdf) > 800
text = ""
if ok:
    try:
        text = subprocess.run(["pdftotext", pdf, "-"], capture_output=True,
                              text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        text = ""
if text:
    check("the printed list says which one is the exam", "Exam" in text,
          repr(text[:200]))
else:
    check("the homework PDF renders at all", ok, "no pdftotext, size only")

app2.destroy()
pump()

# ------------------------------------- the radio is read by POSITION, not label
# nbi18n translates widget labels in place, so a dialog that decided the kind by
# comparing get_label() to "Exam" would store "work" for every exam in every
# language but English. Driven in French, in its own process because nbi18n
# reads NB_LANG at import.
CHILD = r'''
import os, sys, json, shutil
H = os.environ["NB_HOME"]
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
sys.path.insert(0, %r)
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbi18n

# FORCE these two labels to differ from their English source, whatever the
# catalogs currently hold. "Exam" and "Assignment" are new strings that no
# catalog has been merged with yet, so _t() returns them unchanged in every
# language -- which made this whole check VACUOUS: a label-based read passed in
# French and Chinese because the French and Chinese labels were still the
# English words. Measured, by mutating the app to compare get_label() to "Exam"
# and watching this suite stay green. Injecting the entries makes the check real
# today and keeps it real after the campaign session merges the catalogs.
nbi18n._CAT["Exam"] = "\u2039exam\u203a"
nbi18n._CAT["Assignment"] = "\u2039assignment\u203a"

import academics

app = academics.Academics()
app.classes = [{"label": "Chem", "color": "#9A7B4F", "room": "",
                "instructor": "", "meets": []}]

# Drive the REAL dialog. Only Gtk.Dialog.run is replaced -- the radio buttons,
# their translated labels and the code that reads them back are all the real
# ones. Stubbing _homework_dialog itself (the easy way) would test nothing:
# a suite that bypasses a dialog cannot see a dialog bug.
real_shell = app._dialog_shell
def shell(title):
    d = real_shell(title)
    d.run = lambda: Gtk.ResponseType.OK
    return d
app._dialog_shell = shell

out = {}
for want in ("exam", "work"):
    got = academics.Academics._homework_dialog(
        app, "t", name="Midterm", kind=want)
    out[want] = got.get("kind") if isinstance(got, dict) else repr(got)
out["lang"] = os.environ.get("NB_LANG", "en")
# Proof the labels were not left in English: if they were, the checks above
# cannot see a label-based read and are decoration.
out["label"] = academics._t("Exam")
print(json.dumps(out))
''' % (DE,)


def drive(lang):
    home = "/tmp/nbhome-acadexam-%s-%d" % (lang, os.getpid())
    env = dict(os.environ, NB_LANG=lang, NB_HOME=home)
    r = subprocess.run([sys.executable, "-c", CHILD], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None, (r.stderr or "")[-300:]
    return json.loads(r.stdout.strip().splitlines()[-1]), ""


# nbi18n translates widget labels IN PLACE, so a dialog that decided the kind by
# comparing get_label() to "Exam" would store "work" for every exam in every
# language but English. Driven in three, each in its own process because nbi18n
# reads NB_LANG at import time.
for lang in ("en", "fr", "zh"):
    got, err = drive(lang)
    if got is None:
        check("the %s run completed" % lang, False, err)
        continue
    check("the real dialog returns an exam as an exam in %s" % lang,
          got.get("exam") == "exam", got)
    check("...and the %s label really was translated (not a vacuous pass)"
          % lang, got.get("label") not in ("Exam", None), got)
    check("...and ordinary work as work in %s" % lang,
          got.get("work") == "work", got)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
