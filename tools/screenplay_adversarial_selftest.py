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


try:
    damaged_store_check()
    title_page_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
finally:
    shutil.rmtree(HOME, ignore_errors=True)
raise SystemExit(1 if failed else 0)
