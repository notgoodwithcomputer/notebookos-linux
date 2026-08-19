#!/usr/bin/env python3
"""Self-test for Academics PDF export.

RED PROOFS

The following deliberate mutations were run against this complete suite. After
each run academics.py was restored byte-for-byte from the pre-test copy and the
unmutated suite was run green before proceeding.

1. `_pdf_name`: changed `return base + ".pdf"` to
   `return "../" + base + ".pdf"`.
   Measured output:
   `FAIL PDF filenames cannot escape a directory and are bounded`
   `     <- unsafe names: ['../etc-passwd.pdf', '../lecture.pdf', '../absolute-path.pdf', '../lecture.pdf', '../xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.pdf']`
2. `_make_homework_pdf`: changed the exam label expression from `_t("Exam")`
   to `""`.
   Measured output:
   `FAIL PDF text preserves note, class, assignment, and Exam label`
   `     <- missing extracted text: ['Exam']`
3. `_print_target`: changed the schedule renderer from
   `self._make_schedule_pdf` to `self._make_active_pdf`.
   Measured output:
   `FAIL print target differs across notes, schedule, and homework views`
   `     <- targets: ['_make_active_pdf', '_make_active_pdf', '_make_homework_pdf']`
"""
import os
import shutil
import subprocess
import sys
import tempfile
from types import MethodType, SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = tempfile.mkdtemp(prefix="academics-export-")
os.environ["NB_HOME"] = H
os.makedirs(H + "/.config/notebook", exist_ok=True)

sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import academics  # noqa: E402

results = []
OUT = os.path.join(H, "pdfs")
os.makedirs(OUT, exist_ok=True)


def check(name, fn):
    try:
        detail = fn()
        ok = detail is True
        if not ok and not isinstance(detail, str):
            detail = repr(detail)
    except Exception as exc:
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        print("     <- " + detail)


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def text(path):
    p = subprocess.run(["pdftotext", path, "-"], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def real_pdf(path):
    return (os.path.isfile(path) and os.path.getsize(path) > 800
            and open(path, "rb").read(5) == b"%PDF-")


def localized_lecture_metadata():
    real_t = academics._t
    try:
        academics._t = lambda value: {
            "Saturday 15 August 2026": "Samedi 15 août 2026",
            "Added": "Ajouté",
        }.get(value, value)
        structured = academics._lecture_meta({
            "meta": "Saturday 15 August 2026 · added 14:05",
            "meta_date": "Saturday 15 August 2026",
            "meta_kind": "added", "meta_suffix": "14:05",
        })
        legacy = academics._lecture_meta({
            "meta": "Saturday 15 August 2026 · added 14:05"})
        return (structured == "Samedi 15 août 2026 · Ajouté 14:05"
                and legacy == structured)
    finally:
        academics._t = real_t


check("lecture metadata localizes in both the UI and PDF helper",
      localized_lecture_metadata)


app = SimpleNamespace()
for method in ("_class_of", "_pdf_name", "_make_active_pdf", "_print_target",
               "_have_to_print", "_make_schedule_pdf", "_make_homework_pdf",
               "_render_pdf", "_all_meets", "_class_label", "_set_view"):
    setattr(app, method, MethodType(getattr(academics.Academics, method), app))
app._line_style_at = academics.Academics._line_style_at
app._line_spans = academics.Academics._line_spans
app._capture_active = lambda: None
app.classes = [{
    "label": "Organic Chemistry", "color": "#5b7158", "room": "LAB 12",
    "instructor": "Dr Curie",
    "meets": [{"day": 1, "start": "09:00", "end": "10:15", "room": "LAB 12"}],
}]
app.lectures = [{
    "cls": 0, "num": "7", "title": "Carbon Rings", "date": "2026-08-07",
    "meta": "Week seven", "notes": "FIDELITY_NOTE benzene resonance",
    "ranges": {"bold": [[0, 13]]},
}]
app.homework = [
    {"title": "FIDELITY_ASSIGNMENT", "cls": 0, "due": "2026-08-20",
     "done": False, "note": "chapter 4", "kind": "work"},
    {"title": "Spectroscopy final", "cls": 0, "due": "2026-08-25",
     "done": False, "note": "", "kind": "exam"},
]
app.active = 0

lecture_pdf = os.path.join(OUT, "lecture.pdf")
schedule_pdf = os.path.join(OUT, "schedule.pdf")
homework_pdf = os.path.join(OUT, "homework.pdf")


def populated_renderers():
    app._make_active_pdf(lecture_pdf)
    app._make_schedule_pdf(schedule_pdf)
    app._make_homework_pdf(homework_pdf)
    bad = [p for p in (lecture_pdf, schedule_pdf, homework_pdf) if not real_pdf(p)]
    return True if not bad else "not real PDFs: %r" % bad


check("populated term: all three renderers write real PDFs", populated_renderers)


def degenerates():
    saved = (app.lectures, app.active, app.classes, app.homework)
    made = []
    try:
        # No lectures is genuinely nothing: the public print predicate must
        # decline it, while an existing lecture with an empty body must render.
        app._set_view("notes"); app.lectures = []; app.active = -1
        made.append(not app._have_to_print())
        app.lectures = [{"cls": -1, "num": "", "title": "Empty body",
                         "date": "", "meta": "", "notes": "", "ranges": {}}]
        app.active = 0
        app._make_active_pdf(os.path.join(OUT, "empty-body.pdf"))
        app.classes = [{"label": "No Times", "color": "#555555", "room": "",
                        "instructor": "", "meets": []}]
        app._make_schedule_pdf(os.path.join(OUT, "no-times.pdf"))
        app.homework = []
        app._make_homework_pdf(os.path.join(OUT, "empty-homework.pdf"))
        app.homework = [{"title": "Undated", "cls": -1, "due": "",
                         "done": False, "note": "", "kind": "work"}]
        app._make_homework_pdf(os.path.join(OUT, "undated.pdf"))
        return True if all(made) else "no-lecture predicate claimed printable"
    finally:
        app.lectures, app.active, app.classes, app.homework = saved


check("degenerate inputs do not raise", degenerates)


def fidelity():
    lt, st, ht = text(lecture_pdf), text(schedule_pdf), text(homework_pdf)
    missing = [s for s, hay in (("FIDELITY_NOTE", lt),
                                ("Organic Chemistry", st),
                                ("FIDELITY_ASSIGNMENT", ht), ("Exam", ht))
               if s not in hay]
    return True if not missing else "missing extracted text: %r" % missing


check("PDF text preserves note, class, assignment, and Exam label", fidelity)


def safe_names():
    titles = ["../../etc/passwd", "..", "/absolute/path", "", "x" * 300]
    names = [app._pdf_name({"cls": -1, "title": title}) for title in titles]
    bad = [n for n in names if ("/" in n or ".." in n or not n
                                or not n.endswith(".pdf") or len(n) > 80)]
    return True if not bad else "unsafe names: %r" % bad


check("PDF filenames cannot escape a directory and are bounded", safe_names)


def unicode_names():
    names = [app._pdf_name({"cls": -1, "title": x})
             for x in ("有机化学", "Химия")]
    return (True if all(n and n.endswith(".pdf") for n in names)
            else "bad Unicode names: %r" % names)


check("PDF filenames accept Chinese and Cyrillic titles", unicode_names)


def pagination():
    start, end = "PAGINATION_START_SENTINEL", "PAGINATION_END_SENTINEL"
    paras = []
    words = "orbit cobalt theorem lattice vector enzyme archive prism ".split()
    for p in range(180):
        paras.append(" ".join(words[(p + i) % len(words)] for i in range(36)))
    note = start + "\n\n" + "\n\n".join(paras) + "\n\n" + end
    # Ensure the requested scale even if wording above is edited later.
    while len(note) < 30000:
        note = note[:-len(end)] + " " + " ".join(words) + "\n" + end
    app.lectures[0]["notes"] = note; app.lectures[0]["ranges"] = {}
    app.active = 0
    path = os.path.join(OUT, "pagination.pdf")
    app._make_active_pdf(path)
    got = text(path)
    return (True if start in got and end in got
            else "sentinels present: start=%r end=%r" % (start in got, end in got))


check("long lecture paginates through the final sentinel", pagination)


def routing():
    targets = []
    for view in ("notes", "schedule", "homework"):
        app._set_view(view); pump()
        targets.append(app._print_target()[0].__func__.__name__)
    return True if len(set(targets)) == 3 else "targets: %r" % targets


check("print target differs across notes, schedule, and homework views", routing)

failed = results.count(False)
print("%d checks, %d failed" % (len(results), failed))
print("RESULT: %s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
shutil.rmtree(H, ignore_errors=True)
sys.exit(1 if failed else 0)
