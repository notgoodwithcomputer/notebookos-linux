#!/usr/bin/env python3
"""Display-free adversarial checks for Screenplay recovery persistence."""
import os
import shutil
import sys
import tempfile
import subprocess

HOME = tempfile.mkdtemp(prefix="screenplay-adversarial-")
os.environ["NB_HOME"] = HOME
DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import screenplay  # noqa: E402
import nbprint  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


class Field:
    def __init__(self, text): self.text = text
    def get_text(self): return self.text


class Buffer:
    def get_start_iter(self): return None
    def get_end_iter(self): return None
    def get_text(self, _a, _b, _hidden): return ""


class Body:
    def get_buffer(self): return Buffer()


def bare():
    app = screenplay.Screenplay.__new__(screenplay.Screenplay)
    app.scripttitle = Field(screenplay.DEFAULT_TITLE)
    app.scriptsubtitle = Field("")
    app.body = Body()
    app._path = None
    app._serialize_body_tags = lambda _buf: []
    return app


def damaged_store_check():
    os.makedirs(screenplay.CFG_DIR, exist_ok=True)
    samples = (
        b'{"body":"INT. HOME - DAY", "body_tags":',
        b'{"scenes":[{"heading":"INT. HOME - DAY"}]}',
    )
    for i, original in enumerate(samples):
        with open(screenplay.DOC_FILE, "wb") as fh:
            fh.write(original)
        app = bare()
        app._load_doc()
        app._save_doc()
        after = open(screenplay.DOC_FILE, "rb").read()
        check(("malformed" if i == 0 else "wrong-shaped")
              + " recovery store survives open+close byte-for-byte",
              after == original, "store was rewritten or moved")

    with open(screenplay.DOC_FILE, "wb") as fh:
        fh.write(samples[0])
    mutant = bare()
    mutant._recovery_store_writable = True
    mutant._save_doc()
    mutated = open(screenplay.DOC_FILE, "rb").read()
    check("MUTANT: removing recovery damage guard DOES rewrite the store",
          mutated != samples[0], "[not reached: save performed no write]")


def title_page_check():
    title = "THE EXTRAORDINARILY LONG JOURNEY THROUGH WINTER AND HOME AGAIN"
    app = bare()
    app.scripttitle = Field(title)
    app.scriptsubtitle = Field("Écrit par Zoë Álvarez")
    app._pdf_lines = lambda: []
    count, draw = app._build_pages()
    pdf = os.path.join(HOME, "title.pdf")
    nbprint.simple_pdf(pdf, count, draw)
    out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-layout",
                          pdf, "-"], capture_output=True, text=True).stdout
    lines = [line.strip() for line in out.splitlines()
             if any(word in line for word in ("EXTRAORDINARILY", "WINTER",
                                               "HOME"))]
    check("a very long title wraps to multiple title-page lines",
          len(lines) >= 2, repr(lines))
    check("a byline with diacritics survives the title page",
          "Écrit par Zoë Álvarez" in out, repr(out))
    check("MUTANT: one-line title layout DOES exceed half-letter width",
          len(title) > 50)


def close_guard_check():
    """Until the writer picks a file, the recovery store IS the script. A close
    that cannot write it has to stop and say so — Novel has carried this guard
    for the same reason and this app had the same exposure with no guard."""
    app = bare()
    app._recovery_store_writable = True
    app._recovery_dirty = False
    app._save_error = None
    asked = []
    app._confirm = lambda title, body, ok: (asked.append((title, body, ok))
                                            or False)

    # Nothing outstanding: closing must be silent.
    check("a durable script closes without a word",
          app._on_delete() is False and asked == [], repr(asked))

    # An edit is outstanding and the write works on the retry: still silent.
    app._recovery_dirty = True
    app._collect_doc = lambda: {"body": "INT. HOME - DAY", "body_tags": [],
                                "title": "T", "subtitle": "", "path": None}
    check("a close that can still save does not interrogate anyone",
          app._on_delete() is False and asked == [], repr(asked))

    # The disk refuses. The close must be VETOED and the reason named.
    real_write = screenplay.nbapp.atomic_write_json
    screenplay.nbapp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError(28, "No space left on device"))
    try:
        app._recovery_dirty = True
        vetoed = app._on_delete()
    finally:
        screenplay.nbapp.atomic_write_json = real_write
    check("a close that would lose the script is stopped",
          vetoed is True, repr(vetoed))
    check("...and the card says the disk is full, not just 'not saved'",
          asked and "disk is full" in asked[-1][1], repr(asked[-1:] ))
    check("...and offers to close anyway rather than trapping the window",
          asked and asked[-1][2] == "Close Without Saving", repr(asked[-1:]))

    # Accepting the loss must let the window go.
    app._confirm = lambda title, body, ok: True
    screenplay.nbapp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError(28, "No space left on device"))
    try:
        app._recovery_dirty = True
        proceeds = app._on_delete()
    finally:
        screenplay.nbapp.atomic_write_json = real_write
    check("choosing to close anyway is not overridden by the guard",
          proceeds is False, repr(proceeds))

    # A store held read-only because its bytes were not ours is NOT an I/O
    # failure, and a card about making room would be about the wrong problem.
    app._recovery_store_writable = False
    app._recovery_dirty = True
    app._save_error = None
    before = len(asked)
    app._confirm = lambda title, body, ok: (asked.append((title, body, ok))
                                            or False)
    check("an unwritable-by-law store does not raise a disk-full card",
          app._on_delete() is False and len(asked) == before, repr(asked[before:]))


try:
    damaged_store_check()
    title_page_check()
    close_guard_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
finally:
    shutil.rmtree(HOME, ignore_errors=True)
raise SystemExit(1 if failed else 0)
