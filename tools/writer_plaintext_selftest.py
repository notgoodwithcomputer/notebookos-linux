#!/usr/bin/env python3
"""
Saving a document to .txt has to say what it costs.

ROADMAP #2. `.txt` and `.md` sit in Writer's Save As picker as equal choices
next to `.writer`. Picking one dropped every formatting run, every table and
every picture, then reported the same "Saved 19:43" a lossless save gives — so
the only way to find out was to reopen the file, possibly months later, by
which time the .writer original may be long gone. Novel and Screenplay both
guarded this; Writer said nothing.

The guard now names what will be lost, counted ("3 formatting runs, 1 table and
2 pictures"), focuses Cancel so a stray Return cannot drop them, and the save
chip afterwards reads "Saved as text" rather than "Saved".

WHY THIS ANSWERS A REAL DIALOG. Patching `_confirm_plain_text` away and then
checking the file measures the mock: the file survives because nothing tried to
write it. This patches `Gtk.Dialog.run` instead, so the real dialog is BUILT —
the real loss list, the real sentence, the real default button — and the test
reads it before answering. A confirm that is never raised fails here.

Run:
    tools/guestrun.sh python3 tools/writer_plaintext_selftest.py
    tools/guestrun.sh python3 tools/writer_plaintext_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-wtxt-")
os.environ["NB_HOME"] = _HOME
os.makedirs(os.path.join(_HOME, "Documents"), exist_ok=True)

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf  # noqa: E402

import writer  # noqa: E402

FAILED, N = [], [0]
DECOY = b"the file that was already there\n"


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(300):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


def labels(root, out=None):
    out = [] if out is None else out
    if isinstance(root, Gtk.Label):
        out.append(" ".join(root.get_text().split()))
    if isinstance(root, Gtk.Container):
        for kid in root.get_children():
            labels(kid, out)
    return out


class Answer(object):
    """Stands in for the person at the keyboard.

    Records that a dialog was raised and what it said, then gives the answer
    it was told to give. Patching Gtk.Dialog.run — not the app's own method —
    is what keeps the real confirm in the picture.
    """

    def __init__(self, response):
        self.response = response
        self.raised = 0
        self.said = []
        self.default_was = None

    def hook(self):
        """As a plain function: an instance assigned to a class attribute is
        not a descriptor, so `dlg.run()` would call it with no arguments."""
        return lambda dlg: self(dlg)

    def __call__(self, dlg):
        self.raised += 1
        self.said = labels(dlg.get_content_area())
        focus = dlg.get_focus()
        if isinstance(focus, Gtk.Button):
            self.default_was = focus.get_label()
        return self.response


def formatted_doc(w):
    """A document with a run, a table and a picture — one of each thing the
    plain-text write throws away, so the counted sentence has to name all three."""
    buf = w.buf
    buf.set_text("Chapter One")
    buf.apply_tag_by_name("bold", buf.get_start_iter(),
                          buf.get_iter_at_offset(7))
    w._insert_table([["a", "b"], ["c", "d"]])
    png = os.path.join(_HOME, "pic.png")
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 16, 10)
    pb.fill(0x3366ffff)
    pb.savev(png, "png", [], [])
    real = writer.nbpicker.open_file
    writer.nbpicker.open_file = lambda *a, **k: png
    try:
        w._insert_image()
    finally:
        writer.nbpicker.open_file = real
    pump()


def main():
    w = writer.Writer()
    pump()
    formatted_doc(w)

    # ---- what it says will be lost is what is actually there -------------
    losses = w._plain_text_losses()
    joined = " / ".join(losses)
    check("the loss list names formatting", any("run" in x for x in losses), joined)
    check("...and tables", any("table" in x for x in losses), joined)
    check("...and pictures", any("picture" in x for x in losses), joined)

    dest = os.path.join(_HOME, "Documents", "notes.txt")
    with open(dest, "wb") as fh:
        fh.write(DECOY)

    # ---- declining must not touch the destination ------------------------
    ans = Answer(Gtk.ResponseType.CANCEL)
    real_run = Gtk.Dialog.run
    Gtk.Dialog.run = ans.hook()
    try:
        w._write_file(dest)
        pump()
    finally:
        Gtk.Dialog.run = real_run

    raised = check("saving to .txt raises a confirm", ans.raised == 1,
                   "raised %d" % ans.raised)
    if raised:
        msg = " ".join(ans.said)
        check("...that names the file", "notes.txt" in msg, msg[:90])
        check("...and counts what will be lost",
              all(any(part.split()[0] in msg and part.split()[-1][:5] in msg
                      for part in [x]) for x in losses), msg[:140])
        check("...with Cancel focused, so Return cannot drop the formatting",
              (ans.default_was or "").lower().startswith("cancel"),
              repr(ans.default_was))
    else:
        not_reached("no confirm", "...that names the file",
                    "...and counts what will be lost",
                    "...with Cancel focused, so Return cannot drop the formatting")

    with open(dest, "rb") as fh:
        after = fh.read()
    check("declining leaves the file exactly as it was", after == DECOY,
          repr(after[:40]))
    check("...and the document keeps its own path", w._path != dest,
          repr(w._path))

    # ---- accepting writes it, and says what shape reached the disk -------
    ans2 = Answer(Gtk.ResponseType.OK)
    Gtk.Dialog.run = ans2.hook()
    try:
        w._write_file(dest)
        pump()
    finally:
        Gtk.Dialog.run = real_run
    with open(dest, "rb") as fh:
        after = fh.read()
    wrote = check("accepting writes the text", after != DECOY and b"Chapter" in after,
                  repr(after[:40]))
    if wrote:
        said = [c for c in labels(w) if "Saved" in c]
        check("the save chip says it went out as TEXT, not just 'Saved'",
              any("as text" in c.lower() for c in said), repr(said[:2]))
    else:
        not_reached("nothing was written",
                    "the save chip says it went out as TEXT, not just 'Saved'")

    # ---- and a real .writer save must not ask at all ---------------------
    ans3 = Answer(Gtk.ResponseType.OK)
    Gtk.Dialog.run = ans3.hook()
    try:
        w._write_file(os.path.join(_HOME, "Documents", "notes.writer"))
        pump()
    finally:
        Gtk.Dialog.run = real_run
    check("a lossless save asks nothing", ans3.raised == 0,
          "raised %d" % ans3.raised)

    # ---- nor should a document with nothing to lose ----------------------
    w2 = writer.Writer()
    pump()
    w2.buf.set_text("just words")
    ans4 = Answer(Gtk.ResponseType.OK)
    Gtk.Dialog.run = ans4.hook()
    try:
        w2._write_file(os.path.join(_HOME, "Documents", "plain.txt"))
        pump()
    finally:
        Gtk.Dialog.run = real_run
    check("a plain document is written without friction", ans4.raised == 0,
          "raised %d" % ans4.raised)

    for app in (w, w2):
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
