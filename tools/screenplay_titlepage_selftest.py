#!/usr/bin/env python3
"""
The byline reaches the page.

Screenplay's title page carries a second field under the title, placeholder
*"written by"*. It was captured, persisted to the .json, restored by undo, and
shown on screen — and `_build_pages` drew only the title, so every printed and
exported script came out with no name on it (ROADMAP #37). A writer typed
"written by Alexander Hamilton", saw it, and printed an anonymous screenplay.

This renders a REAL PDF through the app's own `_build_pages` and reads the text
back out with `pdftotext`. Asserting on the drawing calls would prove only that
a function was called; the failure mode here is "it is not on the page", so the
page is what gets read.

Guarded against the trap that caught Novel's zine print: a clip-based or
path-outlined renderer produces a PDF whose text cannot be extracted at all, and
`pdftotext` then returns nothing for both a correct and a broken page. So the
suite first asserts the TITLE — which was always drawn — comes back. If that
fails, extraction is broken rather than the byline, and every later assertion is
reported as not reached rather than allowed to fail misleadingly.

Run:
    tools/guestrun.sh python3 tools/screenplay_titlepage_selftest.py
    tools/guestrun.sh python3 tools/screenplay_titlepage_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile
import subprocess

_HOME = tempfile.mkdtemp(prefix="nb-splay-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import screenplay  # noqa: E402
import nbprint  # noqa: E402

TITLE = "THE LAST TRAIN"
BYLINE = "written by Alexander Hamilton"

FAILED, N = [], [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    """Record dependent assertions as failed, with the REASON they could not be
    evaluated. Two different preconditions guard these checks and saying the
    wrong one would send the next reader to the wrong place."""
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump(n=300):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


def page_text(pdf, page):
    out = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page),
                          "-layout", pdf, "-"],
                         capture_output=True, text=True)
    return out.stdout


def render(app, name):
    path = os.path.join(_HOME, name)
    count, draw = app._build_pages()
    nbprint.simple_pdf(path, count, draw)
    return path, count


def main():
    app = screenplay.Screenplay()
    pump()
    app.scripttitle.set_text(TITLE)
    app.scriptsubtitle.set_text(BYLINE)
    buf = app.body.get_buffer()
    buf.set_text("FADE IN:\n\nINT. CARRIAGE - NIGHT\n\nThe last train pulls out.")
    pump()

    pdf, count = render(app, "with-byline.pdf")
    check("a PDF is produced", os.path.exists(pdf) and os.path.getsize(pdf) > 900)
    check("it has a title page plus body", count >= 2)

    p1 = page_text(pdf, 1)
    # The control: the title was ALWAYS drawn, so if it does not come back the
    # extraction is broken and nothing below can be trusted.
    extractable = check("the title page's text can be read back (%r)"
                        % p1.strip()[:40], TITLE in p1)
    if not extractable:
        not_reached("the PDF's text could not be read at all",
                    "the byline is on the title page",
                    "the byline is not shouted in capitals",
                    "the byline sits below the title",
                    "an empty byline leaves the title page alone")
    else:
        present = check("the byline is on the title page", BYLINE in p1)
        if present:
            # Both of these are only meaningful once something is there: with
            # nothing drawn, "not shouted in capitals" is trivially true.
            check("the byline is not shouted in capitals",
                  BYLINE.upper() not in p1)
            # Order in a -layout extraction follows the page, top to bottom.
            check("the byline sits below the title",
                  p1.index(TITLE) < p1.index(BYLINE))
        else:
            not_reached("no byline was drawn",
                        "the byline is not shouted in capitals",
                        "the byline sits below the title")

        # ---- and an empty byline must not draw anything ------------
        app.scriptsubtitle.set_text("")
        pump()
        pdf2, _ = render(app, "no-byline.pdf")
        p1b = page_text(pdf2, 1)
        check("an empty byline leaves the title page alone",
              TITLE in p1b and "written by" not in p1b.lower())

    # ---- the body still renders, unchanged ---------------------------
    p2 = page_text(pdf, 2)
    check("the script itself still reaches page 2", "FADE IN" in p2.upper())

    # A title is user text, not a filename-limited label. It must remain on the
    # half-letter sheet even when it is longer than one physical line.
    long_title = ("THE EXTRAORDINARILY LONG JOURNEY THROUGH WINTER "
                  "AND HOME AGAIN")
    app.scripttitle.set_text(long_title)
    app.scriptsubtitle.set_text("Écrit par Zoë Álvarez")
    pump()
    pdf3, _ = render(app, "long-title.pdf")
    p1c = page_text(pdf3, 1)
    title_lines = [line.strip() for line in p1c.splitlines()
                   if any(word in line for word in ("EXTRAORDINARILY",
                                                     "WINTER", "HOME"))]
    check("a very long title wraps to multiple title-page lines",
          len(title_lines) >= 2)
    check("a byline with diacritics survives the title page",
          "Écrit par Zoë Álvarez" in p1c)
    check("MUTANT: drawing the long title on one line DOES exceed the page",
          screenplay._pdf_w(__import__("cairo").Context(
              __import__("cairo").ImageSurface(
                  __import__("cairo").FORMAT_ARGB32, 10, 10)),
              long_title, 13.0) > nbprint.HALF_W_PT)

    try:
        app.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
