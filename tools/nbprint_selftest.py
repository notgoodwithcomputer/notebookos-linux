#!/usr/bin/env python3
"""Headless lifecycle gate for the shared asynchronous print dialog."""

import ast
import importlib
import inspect
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

import nbjobs  # noqa: E402
import nbmotion  # noqa: E402
import nbprint  # noqa: E402

FAILED = []


def check(value, label):
    print("%-72s %s" % (label, "ok" if value else "FAIL"))
    if not value:
        FAILED.append(label)


def drain_until(owner, dispatcher, timeout=5.0):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        dispatcher.drain()
        if owner.join(0.01):
            dispatcher.drain()
            return True
    return False


def test_async_end_to_end():
    dispatcher = nbjobs.ManualDispatcher()
    owner = nbjobs.JobOwner(dispatch=dispatcher, name="print-test")
    entered = threading.Event()
    release = threading.Event()
    callbacks = []
    progress = []
    spool = []
    main_ident = threading.get_ident()
    real_submit = nbprint.submit_pdf

    def render(path):
        entered.set()
        release.wait(5.0)
        with open(path, "wb") as output:
            output.write(b"%PDF-1.4\n")

    def submit(path, **kwargs):
        spool.append((path, kwargs, threading.get_ident()))
        return True, "sent"

    nbprint.submit_pdf = submit
    try:
        started = time.monotonic()
        job = owner.start(
            nbprint.PRINT_KEY,
            lambda running: nbprint._print_worker(
                running, render, "Fake", 1, {"media": "Letter"}, "Test"),
            on_done=lambda value: callbacks.append((value, threading.get_ident())),
            on_progress=lambda fraction, phase: progress.append(
                (fraction, phase, threading.get_ident())))
        elapsed = time.monotonic() - started
        check(job is not None and elapsed < 0.25,
              "dialog action returns before rendering finishes (%.3fs)" % elapsed)
        check(entered.wait(2.0), "rendering starts on the background job")
        check(threading.get_ident() == main_ident and not callbacks,
              "the calling thread remains available while rendering waits")
        release.set()
        check(drain_until(owner, dispatcher), "render and send worker leaves no thread")
        fractions = [item[0] for item in progress]
        check(fractions and fractions == sorted(fractions) and fractions[-1] == 1.0,
              "progress fractions advance monotonically to completion")
        check(all(item[2] == main_ident for item in progress) and
              all(item[1] == main_ident for item in callbacks),
              "progress and completion fire on the main-loop dispatcher")
        check(len(spool) == 1 and spool[0][2] != main_ident,
              "the complete print file is sent from the worker")
    finally:
        release.set()
        owner.close()
        owner.join(5.0)
        nbprint.submit_pdf = real_submit


def test_cancel_before_send():
    dispatcher = nbjobs.ManualDispatcher()
    owner = nbjobs.JobOwner(dispatch=dispatcher, name="cancel-test")
    page = threading.Event()
    continue_page = threading.Event()
    spool = []
    cancelled = []
    real_submit = nbprint.submit_pdf

    def render(path):
        page.set()
        continue_page.wait(5.0)
        nbprint._render_step(1, 3)
        with open(path, "wb") as output:
            output.write(b"partial")

    nbprint.submit_pdf = lambda *a, **k: (spool.append(1) or (True, "sent"))
    try:
        owner.start(nbprint.PRINT_KEY,
                    lambda job: nbprint._print_worker(
                        job, render, "Fake", 1, {}, "Test"),
                    on_cancel=lambda: cancelled.append(threading.get_ident()))
        check(page.wait(2.0), "cancel fixture reaches a page boundary")
        check(owner.cancel(nbprint.PRINT_KEY), "Cancel requests the active print job to stop")
        continue_page.set()
        check(drain_until(owner, dispatcher), "cancelled print leaves no worker thread")
        check(spool == [], "cancel before handoff makes no partial send call")
        check(cancelled == [threading.get_ident()],
              "cancel completion returns on the main-loop dispatcher")
    finally:
        continue_page.set()
        owner.close()
        owner.join(5.0)
        nbprint.submit_pdf = real_submit


def test_wiring_contracts():
    source = inspect.getsource(nbprint)
    check("nbmotion.Scalar" in source and "easing=nbmotion.LINEAR" in source,
          "progress uses Scalar with the linear easing token")
    now = [0.0]
    frames = []
    scalar = nbmotion.Scalar(None, 0.0, on_frame=frames.append, duration=160,
                             easing=nbmotion.LINEAR, manual=True,
                             clock=lambda: now[0])
    scalar.animate_to(1.0)
    for tick in (0.02, 0.04, 0.08, 0.12, 0.16):
        now[0] = tick
        scalar.advance(tick)
    check(len(frames) >= 5 and frames == sorted(frames) and frames[-1] == 1.0,
          "linear Scalar supplies continuous monotone progress frames")
    body = inspect.getsource(nbprint._print_body)
    check("owner.start(" in body and "make_print_file(make_pdf)" not in body,
          "the Print handler never invokes render work synchronously")
    dialog = inspect.getsource(nbprint._print_dialog)
    check('win.connect("key-press-event", _escape)' in dialog and
          'getattr(win, "_nbprint_cancel", None)' in dialog,
          "Escape invokes the same cancellation action as the Cancel button")
    check("Gtk.Window(type=Gtk.WindowType.TOPLEVEL)" in inspect.getsource(nbprint._dialog),
          "the dialog construction path still creates a GTK window")
    check(str(inspect.signature(nbprint.print_document)) ==
          "(parent, make_pdf, job_name='Document', media='Letter')",
          "print_document public signature is unchanged")
    check(str(inspect.signature(nbprint.print_booklet)) ==
          "(parent, make_pdf, job_name='Booklet')",
          "print_booklet public signature is unchanged")


CALLERS = {
    "accounting.py": "print_document(self, self._render_pdf, job_name='Report')",
    "academics.py": "print_document(self, render, job_name=job)",
    "bills.py": "print_document(self, self._render_pdf, job_name='Bills')",
    "contacts.py": "print_document(self, self._make_pdf, job_name='Contacts')",
    "cookbook.py": "print_document(self, make_pdf, job_name='Recipe')",
    "journal.py": "print_document(self, self._make_pdf, job_name='Journal')",
    "novel.py": "print_booklet(self, make_pdf, 'Novel')",
    "screenplay.py": "print_document(...) and print_booklet(...), job_name='Screenplay'",
    "writer.py": "print_document(self, self._render_pdf, job_name=..., media=...)",
}


def test_callers():
    found = {}
    for filename in CALLERS:
        module = importlib.import_module(filename[:-3])
        check(module.nbprint is nbprint,
              "%s imports the shared nbprint module" % filename[:-3])
        path = os.path.join(DE, filename)
        with open(path, encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read(), filename=filename)
        calls = []
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            if (isinstance(func, ast.Attribute) and
                    isinstance(func.value, ast.Name) and func.value.id == "nbprint" and
                    func.attr in ("print_document", "print_booklet")):
                calls.append(node)
                sig = inspect.signature(getattr(nbprint, func.attr))
                positional = [None] * len(node.args)
                keywords = {kw.arg: None for kw in node.keywords if kw.arg}
                try:
                    sig.bind(*positional, **keywords)
                except TypeError:
                    continue
        found[filename] = len(calls)
    check(all(found.values()) and len(found) == 9,
          "all nine current caller modules keep a compatible call shape")


if __name__ == "__main__":
    test_async_end_to_end()
    test_cancel_before_send()
    test_wiring_contracts()
    test_callers()
    if FAILED:
        print("nbprint_selftest: FAIL (%d checks)" % len(FAILED))
        sys.exit(1)
    print("nbprint_selftest: OK")
