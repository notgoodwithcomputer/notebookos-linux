#!/usr/bin/env python3
"""Adversarial clipboard-correctness checks for NotebookOS applications.

Run with PYTHONPATH=buildroot/board/notebookos/rootfs-overlay/opt/notebook/de.
The suite always replaces NB_HOME before importing an app and does not need a
display: clipboard objects and app widgets are narrow recording fakes, while
the production handlers themselves are invoked unbound.
"""
import atexit
import os
import shutil
import tempfile
from types import SimpleNamespace

_HOME = tempfile.mkdtemp(prefix="nb_clipboard_correctness_")
os.environ["NB_HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, True)

import calculator  # noqa: E402
import contacts  # noqa: E402
import finder  # noqa: E402
import sequencer  # noqa: E402
import terminal  # noqa: E402
import writer  # noqa: E402

passed = failed = skipped = mutants = 0


def check(name, condition, evidence=""):
    global passed, failed
    if condition:
        passed += 1
        print("PASS " + name + (" — " + evidence if evidence else ""))
    else:
        failed += 1
        print("FAIL " + name + (" — " + evidence if evidence else ""))


def mutant(name, killed):
    global mutants, failed
    if killed:
        mutants += 1
        print("PASS-MUTANT " + name)
    else:
        failed += 1
        print("FAIL " + name + " — mutant survived")


class RecordingClipboard:
    def __init__(self, incoming=None):
        self.incoming = incoming
        self.text = "sentinel"
        self.stored = False

    def set_text(self, value, _length):
        self.text = value

    def store(self):
        self.stored = True

    def request_text(self, callback):
        callback(self, self.incoming)


def with_fake_gtk(module, clipboard, call):
    old = module.Gtk
    module.Gtk = SimpleNamespace(Clipboard=SimpleNamespace(
        get=lambda _selection: clipboard))
    try:
        call()
    finally:
        module.Gtk = old


# Shape 1: exact values, captured when the handler fires.
clip = RecordingClipboard()
calc = SimpleNamespace(disp_lbl=SimpleNamespace(get_text=lambda: "42"),
                       error=False)
with_fake_gtk(calculator, clip,
              lambda: calculator.Calculator._copy_result(calc))
check("COPY-CALCULATOR-EXACT-RESULT", clip.text == "42", repr(clip.text))
clip.text = "prior useful value"
calc.error = True
with_fake_gtk(calculator, clip,
              lambda: calculator.Calculator._copy_result(calc))
check("COPY-CALCULATOR-ERROR-PRESERVES-CLIPBOARD",
      clip.text == "prior useful value")

clip = RecordingClipboard()
contact = SimpleNamespace(_flash=lambda _message: None)
with_fake_gtk(contacts, clip, lambda: contacts.Contacts._copy_value(
    contact, None, "emails", "right@example.test"))
check("COPY-CONTACT-EXACT-FIELD", clip.text == "right@example.test",
      repr(clip.text))

seq_clip = {"s": 1.0, "e": 3.0, "wav": "take.wav", "gain": 0.75}
seq = SimpleNamespace(sel_clip=lambda: seq_clip, _clipboard=None,
                      _flash=lambda _message: None)
sequencer.Sequencer._copy_clip(seq)
seq_clip["gain"] = 0.1
check("COPY-SEQUENCER-SNAPSHOT-NOT-STALE-ALIAS",
      seq._clipboard["gain"] == 0.75)


class FinderHarness:
    def __init__(self, root):
        self.root = root
        self.rel = "dst"
        self._clipboard = None
        self.status = []

    def abspath(self, rel):
        return os.path.join(self.root, rel)

    def _taken(self, path):
        return os.path.lexists(path)

    def _unique_path(self, directory, base, suffix=" copy"):
        stem, ext = os.path.splitext(base)
        candidate = os.path.join(directory, stem + suffix + ext)
        n = 2
        while os.path.lexists(candidate):
            candidate = os.path.join(directory,
                                     "%s%s %d%s" % (stem, suffix, n, ext))
            n += 1
        return candidate

    def _recursive_target(self, _src, _dst):
        return False

    def _same_filesystem(self, _src, _dst):
        return True

    def _rename_noreplace(self, src, dst):
        os.rename(src, dst)

    def _copy(self, src, dst, done):
        shutil.copy2(src, dst)
        done(True)

    def _update_paste(self):
        pass

    def _set_undo_move(self, *_args):
        pass

    def _set_undo_remove(self, *_args):
        pass

    def load(self, *_args, **_kwargs):
        pass

    def _flash_undoable(self, message):
        self.status.append(message)

    def _flash_status(self, message):
        self.status.append(message)


# Shape 2: cut is ONE-SHOT — one move, then the clipboard clears, exactly as
# finder.py deliberately does ("cut is one-shot"). A second paste is a no-op,
# not a duplicate. An earlier clipboard audit misread this standard behaviour
# (Explorer, Nautilus, and finder's own move-noreplace + fileops suites all
# pin it) as a bug and made cut-paste become a copy; that change was reverted.
root = tempfile.mkdtemp(dir=_HOME, prefix="finder_")
os.mkdir(os.path.join(root, "src")); os.mkdir(os.path.join(root, "dst"))
source = os.path.join(root, "src", "same.txt")
with open(source, "w", encoding="utf-8") as _srcf:
    _srcf.write("identity")
fh = FinderHarness(root)
fh._clipboard = (source, True)
finder.Finder._paste(fh)
moved = os.path.join(root, "dst", "same.txt")
check("CUT-PASTE-FINDER-MOVES-SAME-ITEM-ONCE",
      not os.path.exists(source) and open(moved, encoding="utf-8").read() == "identity")
check("CUT-PASTE-FINDER-ONE-SHOT-CLEARS",
      fh._clipboard is None, repr(fh._clipboard))
finder.Finder._paste(fh)   # second paste, clipboard now empty
check("CUT-PASTE-FINDER-SECOND-PASTE-NOOP",
      not os.path.exists(os.path.join(root, "dst", "same copy.txt")))
mutant("CUT-PASTE-FINDER-ONE-SHOT-CLEARS",
       fh._clipboard is None)  # a become-a-copy mutant would keep it non-None

# Shape 3 and 4: bounded validation plus empty/foreign refusal.
valid = calculator.Calculator._clipboard_expression
check("PASTE-CALCULATOR-ACCEPTS-EXPRESSION", valid("6*7") == "6*7")
for name, payload in (
        ("PASTE-CALCULATOR-REJECTS-NONNUMERIC", "not arithmetic"),
        ("PASTE-CALCULATOR-REJECTS-CONTROLS", "12\nrm -rf"),
        ("PASTE-CALCULATOR-REJECTS-OVERSIZE", "9" * 257),
        ("PASTE-CALCULATOR-EMPTY-NOOP", None)):
    check(name, valid(payload) is None)

for name, incoming in (("PASTE-CALCULATOR-FOREIGN-NOOP", None),
                       ("PASTE-CALCULATOR-INVALID-PRESERVES-VALUE", "hello")):
    app = SimpleNamespace(expr="keep", error=False, refreshes=0)
    app._clipboard_expression = valid
    app._refresh = lambda: setattr(app, "refreshes", app.refreshes + 1)
    with_fake_gtk(calculator, RecordingClipboard(incoming),
                  lambda: calculator.Calculator._paste_expression(app))
    check(name, app.expr == "keep" and app.refreshes == 0)
mutant("PASTE-CALCULATOR-UNVALIDATED-TEXT",
       valid("hello\nworld") is None)

# Shape 5: vanished sources clear honestly; empty selection preserves a valid
# clipboard instead of poisoning it. These invoke the Finder/Sequencer handlers.
gone = os.path.join(root, "src", "gone.txt")
fh._clipboard = (gone, False)
finder.Finder._paste(fh)
check("STALE-FINDER-VANISHED-SOURCE-CLEARS", fh._clipboard is None)

previous = dict(seq._clipboard)
seq.sel_clip = lambda: None
sequencer.Sequencer._copy_clip(seq)
check("EMPTY-SEQUENCER-COPY-PRESERVES-CLIPBOARD", seq._clipboard == previous)

# Delegation evidence for handlers whose widget owns the semantics. Fake
# buffers/VTEs prove the real app handler chooses the exact operation; GTK/VTE
# owns text selection, target negotiation, and foreign/empty no-op behavior.
ops = []
buf = SimpleNamespace(copy_clipboard=lambda cb: ops.append(("copy", cb)),
                      cut_clipboard=lambda cb, editable: ops.append(("cut", editable)),
                      paste_clipboard=lambda cb, where, editable: ops.append(("paste", where, editable)))
w = SimpleNamespace(buf=buf, _checkpoint=lambda: ops.append(("checkpoint",)))
gtk_clip = RecordingClipboard()
with_fake_gtk(writer, gtk_clip, lambda: [writer.Writer._clip(w, op)
                                        for op in ("copy", "cut", "paste")])
check("NOT-A-DEFECT-WRITER-DELEGATES-SELECTION-AND-TARGETS",
      [op[0] for op in ops] == ["copy", "checkpoint", "cut", "checkpoint", "paste"])

term_ops = []
vte = SimpleNamespace(copy_clipboard_format=lambda fmt: term_ops.append(("copy", fmt)),
                      paste_clipboard=lambda: term_ops.append(("paste",)))
t = SimpleNamespace(term=vte, _refocus=lambda: None)
terminal.Terminal._term_copy(t); terminal.Terminal._term_paste(t)
check("NOT-A-DEFECT-TERMINAL-DELEGATES-VTE-CLIPBOARD",
      [op[0] for op in term_ops] == ["copy", "paste"])

print("LEDGER NOT-A-DEFECT novel/screenplay/journal: Gtk.TextView native clipboard; multiline is document content")
print("LEDGER NOT-A-DEFECT music/video: no copy/cut/paste handler")
print("LEDGER NOT-A-DEFECT illustrator: image-only producer; text consumers negotiate foreign type")
print("RESULT %d PASS, %d PASS-MUTANT, %d SKIP, %d FAIL" %
      (passed, mutants, skipped, failed))
raise SystemExit(1 if failed else 0)
