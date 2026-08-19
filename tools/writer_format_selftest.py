#!/usr/bin/env python3
"""Display-free adversarial checks for Writer's bold/italic/underline path.

Set WRITER_MODULE_DIR to drive the same checks at a scratch copy.  The normal
run also makes and sabotages such a copy, proving the named checks can go red.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DEFAULT_DE = os.path.join(REPO, "buildroot", "board", "notebookos",
                          "rootfs-overlay", "opt", "notebook", "de")
DE = os.path.abspath(os.environ.get("WRITER_MODULE_DIR", DEFAULT_DE))
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-writer-format-"))

import writer  # noqa: E402

passed = 0
failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


class StyleContext:
    def __init__(self):
        self.classes = set()
    def add_class(self, name):
        self.classes.add(name)
    def remove_class(self, name):
        self.classes.discard(name)


class Button:
    def __init__(self):
        self.ctx = StyleContext()
        self.checked = False
    def get_style_context(self):
        return self.ctx
    def set_active(self, value):
        self.checked = bool(value)
    def get_active(self):
        # the app mirrors state through nbapp.set_active_quietly, which reads
        # the current state first (a no-op when it already matches), exactly
        # as a real Gtk.ToggleButton would be asked
        return self.checked


class Combo:
    def __init__(self):
        self.active = -1
    def set_active(self, value):
        self.active = value


class Entry:
    def __init__(self):
        self.text = ""
    def set_text(self, value):
        self.text = value


class EntryCombo(Combo):
    def __init__(self):
        super().__init__()
        self.child = Entry()
    def get_child(self):
        return self.child


def bare():
    w = writer.Writer.__new__(writer.Writer)
    w.buf = Gtk.TextBuffer()
    w._pending = set()
    w._pending_para = {}
    w._loading = False
    w._restoring = False
    w._syncing = False
    w._smart_busy = False
    w._img_meta = {}
    w._tables = {}
    w._page = "Letter"
    w._header = ""
    w._footer = ""
    w._page_numbers = False
    w._path = None
    w._file_dirty = False
    w._undo_timer = None
    w._save_timer = None
    w._count_timer = None
    w._history = []
    w._hi = -1
    w._fmt_btns = {n: Button() for n in
                   ("bold", "italic", "underline", "strike", "super", "sub",
                    "align:left", "align:center", "align:right", "align:fill",
                    "list:bullet", "list:number")}
    w.style_combo = Combo()
    w.font_combo = Combo()
    w.size_combo = EntryCombo()
    w.spacing_combo = Combo()
    w._setup_base_tags()
    w._checkpoint = lambda: None
    w._mark_dirty = lambda *a: None
    w._update_wordcount = lambda: None
    w.buf.connect_after("insert-text", w._on_inserted)
    w.buf.connect("mark-set", w._on_mark_set)
    return w


def select(w, a, b):
    w.buf.select_range(w.buf.get_iter_at_offset(a), w.buf.get_iter_at_offset(b))


def whole(w, name, a, b):
    return w._range_has_tag(w.buf.get_iter_at_offset(a),
                            w.buf.get_iter_at_offset(b), name)


def active(w, name):
    button = w._fmt_btns[name]
    return "on" in button.ctx.classes and button.checked


def attr_kinds(attrs):
    out = set()
    it = attrs.get_iterator()
    while True:
        for attr in it.get_attrs():
            out.add(attr.klass.type)
        if not it.next():
            break
    return out


def main_checks():
    # Collapsed-caret pending formatting must describe a typing run, not just
    # the first emitted character.
    for cmd in ("bold", "italic", "underline"):
        w = bare()
        w.buf.set_text("x")
        w.buf.place_cursor(w.buf.get_end_iter())
        w._toggle_char(cmd)
        for char in "abc":
            w.buf.insert_at_cursor(char)
        check("pending %s survives caret motion while typing" % cmd,
              whole(w, cmd, 1, 4), repr(w._serialize()["runs"]))

    for cmd, style in (("bold", "Heading 1"), ("italic", "Quote")):
        w = bare()
        w.buf.set_text("styled\n")
        w.buf.apply_tag_by_name("style:" + style, w.buf.get_start_iter(),
                                w.buf.get_end_iter())
        w.buf.place_cursor(w.buf.get_iter_at_offset(3))
        w._toggle_char(cmd)
        w.buf.insert_at_cursor("x")
        names = {t.get_property("name") for t in
                 w.buf.get_iter_at_offset(3).get_tags()}
        check("collapsed %s toggles style-derived formatting OFF for typing" % cmd,
              cmd + ":off" in names, repr(names))

    # A mixed selection whose first run is ON is displayed as ON. Activating
    # that control means turn the whole selection OFF, not fill its gaps.
    for cmd in ("bold", "italic", "underline"):
        w = bare()
        w.buf.set_text("abcdef")
        w.buf.apply_tag_by_name(cmd, w.buf.get_start_iter(),
                                w.buf.get_iter_at_offset(3))
        select(w, 0, 6)
        w._sync_toolbar()
        was_on = active(w, cmd)
        w._toggle_char(cmd)
        check("mixed %s selection toggles OFF from active toolbar" % cmd,
              was_on and not whole(w, cmd, 0, 3),
              "toolbar=%r runs=%r" % (was_on, w._serialize()["runs"]))

    w = bare()
    w.buf.set_text("abcdef")
    w.buf.apply_tag_by_name("bold", w.buf.get_iter_at_offset(0),
                            w.buf.get_iter_at_offset(2))
    w.buf.apply_tag_by_name("italic", w.buf.get_iter_at_offset(2),
                            w.buf.get_iter_at_offset(4))
    w.buf.apply_tag_by_name("underline", w.buf.get_iter_at_offset(4),
                            w.buf.get_iter_at_offset(6))
    states = []
    for off in (1, 3, 5):
        w.buf.place_cursor(w.buf.get_iter_at_offset(off))
        w._sync_toolbar()
        states.append(tuple(active(w, n) for n in
                            ("bold", "italic", "underline")))
    check("toolbar B/I/U state follows the cursor through formatted spans",
          states == [(True, False, False), (False, True, False),
                     (False, False, True)], repr(states))

    # Style formatting participates in the same effective state. Explicitly
    # turning it off must survive the document format and print pipeline.
    for cmd, style, attr_type in (
            ("bold", "Heading 1", Pango.AttrType.WEIGHT),
            ("italic", "Quote", Pango.AttrType.STYLE)):
        w = bare()
        w.buf.set_text("Styled line\n")
        w.buf.apply_tag_by_name("style:" + style, w.buf.get_start_iter(),
                                w.buf.get_end_iter())
        w.buf.place_cursor(w.buf.get_iter_at_offset(3))
        w._sync_toolbar()
        check("%s toolbar reflects %s paragraph style" % (cmd, style),
              active(w, cmd))
        select(w, 0, 11)
        w._toggle_char(cmd)
        doc = w._serialize()
        off_name = cmd + ":off"
        check("%s OFF override is saved" % cmd,
              any(r[2] == off_name for r in doc["runs"]), repr(doc["runs"]))
        w2 = bare()
        w2._deserialize(doc)
        names = {t.get_property("name") for t in
                 w2.buf.get_iter_at_offset(3).get_tags()}
        check("%s OFF override survives reload" % cmd, off_name in names,
              repr(names))
        kinds = attr_kinds(w2._line_attrs(w2.buf.get_start_iter(),
                                          w2.buf.get_iter_at_offset(11)))
        check("print path suppresses styled %s after override" % cmd,
              attr_type not in kinds, repr(kinds))

    # Ctrl+B/I/U must dispatch through exactly the toolbar command path.
    for cmd, key in (("bold", Gdk.KEY_b), ("italic", Gdk.KEY_i),
                     ("underline", Gdk.KEY_u)):
        w = bare()
        called = []
        w._toggle_char = called.append
        ev = type("Event", (), {"state": Gdk.ModifierType.CONTROL_MASK,
                                 "keyval": key})()
        check("Ctrl+%s dispatches the %s toolbar command" %
              (cmd[0].upper(), cmd), w._on_key(None, ev) and called == [cmd],
              repr(called))


def mutant_check():
    if os.environ.get("WRITER_FORMAT_MUTANT_CHILD"):
        return
    scratch_root = os.path.join(REPO, ".codex-scratch")
    os.makedirs(scratch_root, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="writer-format-mutant-", dir=scratch_root)
    mutant_de = os.path.join(scratch, "de")
    shutil.copytree(DEFAULT_DE, mutant_de)
    path = os.path.join(mutant_de, "writer.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    needle = 'for cmd in ("bold", "italic", "underline", "strike",\n'
    replacement = 'for cmd in ("strike",\n'
    if needle not in src:
        check("MUTANT: sabotaged toolbar sync makes named checks red", False,
              "sabotage target missing")
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src.replace(needle, replacement, 1))
    env = os.environ.copy()
    env["WRITER_MODULE_DIR"] = mutant_de
    env["WRITER_FORMAT_MUTANT_CHILD"] = "1"
    proc = subprocess.run([sys.executable, __file__], env=env,
                          text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    named_red = "FAIL bold toolbar reflects Heading 1 paragraph style" in proc.stdout
    check("MUTANT: sabotaged toolbar sync makes named checks red",
          proc.returncode != 0 and named_red,
          "child rc=%d output=%r" % (proc.returncode, proc.stdout[-500:]))


if __name__ == "__main__":
    main_checks()
    mutant_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
    raise SystemExit(1 if failed else 0)
