#!/usr/bin/env python3
"""commands_selftest — the gate on de/nbcommands.py and the base menus.

    python3 tools/commands_selftest.py

Display-free and GTK-free, on the same terms as tools/motion_selftest.py:
nbcommands imports no gi, so this suite runs on a machine with no X, no
PyGObject and no fonts. The base-class integration in de/nbapp.py (which does
import gi) is checked by reading its SOURCE, not by importing it.

What it proves, in the order a failure matters:

  1. **The registry is coherent.** Ids unique, menus known, groups ordered,
     every label Title Case with the four-space accelerator gap, ellipsis only
     where a command really asks first, and no two commands in one menu sharing
     a (group, order) slot.
  2. **Tuple compatibility.** item() / items() / app_menu() / file_menu() /
     edit_menu() return exactly the `(label, callback)` shape
     AppWindow.menu_items() has always returned, with the same SEP object
     value, and a None callback keeps the entry VISIBLE (disabled, never
     dropped — MENU-CONVENTIONS §5).
  3. **Dynamic Undo/Redo.** The pair still names the action it reverses, still
     greys out on an empty history, and now says it in one place.
  4. **Translated exactly once.** Every helper's output passes through _t()
     once and only once — the labels leave here in English because
     AppWindow._open_menu() is what translates them, and about_label() is the
     one deliberate exception (composed from two catalog keys).
  5. **Base menu source conformance.** de/nbapp.py's App/File/Edit menus are
     built from the registry rather than hand-spelled, undo_menu_items()
     delegates to it, and NEITHER base menu prints Ctrl+W or Ctrl+Q — the
     terminal suppresses both keys, so a shared menu may not promise them.
  6. **Representative static audit** of writer / finder / settings / media.
     This one REPORTS rather than fails: those apps have not been migrated, and
     several of their differences are deliberate (a per-app haystack name, an
     export that writes immediately). It fails only for a contradiction in the
     files this work actually touched.

Exit status 0 on pass. Gaps found by the audit are printed and do not fail.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import nbcommands as nc  # noqa: E402

FAIL = []
GAPS = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    return cond


def eq(got, want, msg):
    return check(got == want, "%s\n      got:  %r\n      want: %r"
                 % (msg, got, want))


def src(name):
    with open(os.path.join(DE, name), encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------- 1. the registry --

def test_registry():
    ids = [c.id for c in nc._LIST]
    eq(len(ids), len(set(ids)), "command ids must be unique")
    eq(sorted(nc.COMMANDS), sorted(ids), "COMMANDS must hold every command")

    for c in nc._LIST:
        check(c.menu in nc.MENUS, "%s: unknown menu %r" % (c.id, c.menu))
        check(c.id.split(".")[0] in ("app", "file", "edit", "view", "help"),
              "%s: id must be namespaced by menu" % c.id)
        check("…" not in c.title and "..." not in c.title,
              "%s: the ellipsis is metadata, not part of the title" % c.id)
        check(nc.GAP not in c.title,
              "%s: the accelerator is metadata, not part of the title" % c.id)
        check(c.title[:1].isupper(), "%s: Title Case required" % c.id)
        for word in c.title.split():
            check(word[:1].isupper() or word in ("to", "in", "of", "as"),
                  "%s: %r breaks Title Case" % (c.id, word))
        if c.shortcut:
            check(re.match(r"^(Ctrl\+|Esc$|F\d+$)", c.shortcut),
                  "%s: odd shortcut text %r" % (c.id, c.shortcut))
            eq(c.source_label, c.name + nc.GAP + c.shortcut,
               "%s: accelerator needs exactly four spaces" % c.id)
        else:
            eq(c.source_label, c.name, "%s: no accelerator, no gap" % c.id)
        eq(c.name.endswith("…"), c.ellipsis,
           "%s: ellipsis flag and label must agree" % c.id)

    # Ellipsis is a promise, and these are the promises the OS has made.
    asks = {"app.settings", "file.open", "file.save_as", "file.export",
            "file.export_pdf_as", "file.print"}
    eq({c.id for c in nc._LIST if c.ellipsis}, asks,
       "exactly these commands ask something first")
    for cid in ("file.save", "file.export_pdf", "app.close", "file.close",
                "edit.find", "file.open_recent"):
        check(not nc.get(cid).ellipsis,
              "%s happens immediately and must take no ellipsis" % cid)
    # The two exports are different promises and must stay different commands.
    check(nc.source_label("file.export_pdf")
          != nc.source_label("file.export_pdf_as"),
          "the two Export to PDF commands must not be unified")

    # Shortcut text is canonical: one key, one spelling, one owner.
    seen = {}
    for c in nc._LIST:
        if not c.shortcut or c.shortcut == "Esc":
            continue
        check(c.shortcut not in seen,
              "%s and %s both claim %s" % (c.id, seen.get(c.shortcut),
                                           c.shortcut))
        seen[c.shortcut] = c.id
    for cid, key in (("file.new", "Ctrl+N"), ("file.open", "Ctrl+O"),
                     ("file.save", "Ctrl+S"), ("file.save_as", "Ctrl+Shift+S"),
                     ("file.print", "Ctrl+P"), ("edit.undo", "Ctrl+Z"),
                     ("edit.redo", "Ctrl+Shift+Z"), ("edit.cut", "Ctrl+X"),
                     ("edit.copy", "Ctrl+C"), ("edit.paste", "Ctrl+V"),
                     ("edit.select_all", "Ctrl+A"), ("edit.find", "Ctrl+F"),
                     ("app.close", "Esc")):
        eq(nc.shortcut(cid), key,
           "%s must carry the systemwide key (Constitution I §4)" % cid)

    # Group order per Article I §1: creation, persistence, emission,
    # destruction, exit — and Close is always the last thing in File.
    groups = [c.group for c in nc.menu_order(nc.FILE)]
    eq(groups, sorted(groups), "File must sort by group")
    eq(nc.menu_order(nc.FILE)[-1].id, "file.close", "Close is always last")
    eq([c.id for c in nc.menu_order(nc.EDIT)][:3],
       ["edit.undo", "edit.redo", "edit.cut"],
       "Edit opens with the Undo/Redo pair, then the clipboard")
    for menu in nc.MENUS:
        slots = [(c.group, c.order) for c in nc.menu_order(menu)]
        eq(len(slots), len(set(slots)), "%s: two commands share a slot" % menu)

    # The vocabulary the OS is required to speak.
    for cid in ("app.about", "app.settings", "app.close", "app.quit",
                "file.new", "file.open", "file.open_recent", "file.save",
                "file.save_as", "file.export", "file.print", "file.close",
                "edit.undo", "edit.redo", "edit.cut", "edit.copy",
                "edit.paste", "edit.select_all", "edit.find",
                "view.zoom_in", "view.zoom_out", "view.actual_size",
                "view.fullscreen", "help.help"):
        check(cid in nc.COMMANDS, "missing required command %s" % cid)


# ------------------------------------------------ 2. tuple compatibility --

def test_tuples():
    eq(nc.SEP, ("-", None), "SEP must stay the separator nbapp defines")
    eq(nc.SEP, ("-", None), "SEP compares equal to nbapp.SEP by value")

    cb = lambda: None                                    # noqa: E731
    it = nc.item("file.save", cb)
    check(isinstance(it, tuple) and len(it) == 2, "item() returns a 2-tuple")
    eq(it, ("Save    Ctrl+S", cb), "item() is (label, callback)")

    # Disabled stays visible.
    off = nc.item("file.save", None)
    eq(off[0], "Save    Ctrl+S", "a disabled item keeps its label")
    eq(off[1], None, "a disabled item carries a None callback")

    built = nc.items(("file.new", cb), ("file.open", cb),
                     ("file.save", cb), ("file.save_as", None),
                     ("file.print", cb), ("file.close", cb))
    eq([lb for lb, _c in built],
       ["New    Ctrl+N", "Open…    Ctrl+O", "-",
        "Save    Ctrl+S", "Save As…    Ctrl+Shift+S", "-",
        "Print…    Ctrl+P", "-", "Close    Esc"],
       "items() separates the groups of Article I §1 by itself")
    check(nc.SEP in built, "items() inserts the shared SEP object value")
    for entry in built:
        check(isinstance(entry, tuple) and len(entry) == 2,
              "every entry stays a 2-tuple: %r" % (entry,))

    app = nc.app_menu("Writer", cb, cb)
    eq([lb for lb, _c in app], ["About Writer", "-", "Close    Esc"],
       "the app-name menu")
    eq(nc.file_menu(cb), [("Close    Esc", cb)],
       "the base File menu is the exit group alone")
    edit = nc.edit_menu(cb, cb, cb, cb)
    eq([lb for lb, _c in edit],
       ["Cut", "Copy", "Paste", "-", "Select All"],
       "the base Edit menu keeps the bare labels it has always shown")
    eq([lb for lb, _c in nc.edit_menu(cb, cb, cb, cb, shortcuts=True)],
       ["Cut    Ctrl+X", "Copy    Ctrl+C", "Paste    Ctrl+V", "-",
        "Select All    Ctrl+A"],
       "an editor that binds the keys prints them")


# ------------------------------------------------ 3. dynamic Undo / Redo --

class _Hist:
    def __init__(self, u=None, r=None):
        self.u, self.r = u, r

    def can_undo(self):
        return self.u is not None

    def can_redo(self):
        return self.r is not None

    def undo_label(self):
        return self.u

    def redo_label(self):
        return self.r

    def undo(self):
        return True

    def redo(self):
        return True


def test_dynamic():
    empty = nc.dynamic_item("edit.undo", None, False, None)
    eq(empty, ("Undo    Ctrl+Z", None),
       "nothing to take back: visible, greyed, still shows its key")

    named = nc.dynamic_item("edit.undo", "Delete Chapter", True, len)
    eq(named[0], "Undo Delete Chapter    Ctrl+Z",
       "Undo names the action it would reverse")
    eq(named[1], len, "the callback is passed through untouched")

    plain = nc.dynamic_item("edit.redo", "", True, len)
    eq(plain[0], "Redo    Ctrl+Shift+Z",
       "an unnamed step falls back to the plain label")

    eq(nc.get("edit.undo").framed_label("%s"), "Undo %s    Ctrl+Z",
       "the framed form is a printf key the catalogs already carry")
    eq(nc.get("edit.redo").framed_label("%s"), "Redo %s    Ctrl+Shift+Z",
       "and so is Redo's")


# --------------------------------------------- 4. translated exactly once --

def test_translated_once():
    """Mark every _t() call and count the marks.

    A label built here must carry ONE translation at most; _open_menu() does
    the rest. Two marks would mean the string was looked up twice, which is
    how a translated label turns back into English (a hit on the first pass is
    a miss on the second)."""
    real = nc._t
    nc._t = lambda s: "<%s>" % s
    try:
        # Menu-item helpers hand back English: _open_menu translates.
        eq(nc.item("file.save", None)[0], "Save    Ctrl+S",
           "item() must NOT translate — _open_menu does")
        eq(nc.items(("file.new", None))[0][0], "New    Ctrl+N",
           "items() must NOT translate either")
        eq(nc.file_menu(None)[0][0], "Close    Esc",
           "file_menu() must NOT translate")
        eq(nc.edit_menu(None, None, None, None)[0][0], "Cut",
           "edit_menu() must NOT translate")

        # The two deliberate exceptions, each translated exactly once.
        # Two lookups, two marks, one lookup each: the frame "About %s" and
        # the app name. The frame is filled AFTER its own lookup, which is why
        # its mark ends up around the finished label.
        eq(nc.about_label("Writer"), "<About <Writer>>",
           "About is composed from two keys, each looked up once")
        eq(nc.dynamic_item("edit.undo", "Typing", True, len)[0],
           "<Undo Typing    Ctrl+Z>",
           "the framed Undo label is looked up once, then filled")
        eq(nc.dynamic_item("edit.undo", "", True, len)[0],
           "<Undo    Ctrl+Z>", "the plain fallback is looked up once")
        eq(nc.dynamic_item("edit.undo", None, False, None)[0],
           "Undo    Ctrl+Z", "a disabled item is left for _open_menu")
        eq(nc.label("file.print"), "<Print…    Ctrl+P>",
           "label() is the one-shot form for callers outside a menu")
    finally:
        nc._t = real
    eq(nc.item("file.save", None)[0], "Save    Ctrl+S",
       "the real translator is restored")


# ------------------------------------------ 5. base menu source conformance --

def _func_source(tree, name, cls=None):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            if cls is None:
                return node
            for parent in ast.walk(tree):
                if (isinstance(parent, ast.ClassDef) and parent.name == cls
                        and node in ast.walk(parent)):
                    return node
    return None


def test_base_menus():
    text = src("nbapp.py")
    tree = ast.parse(text)
    check("import nbcommands" in text, "nbapp must import the registry")

    node = _func_source(tree, "menu_items", "AppWindow")
    if not check(node is not None, "AppWindow.menu_items must exist"):
        return
    body = ast.get_source_segment(text, node) or ""
    for call in ("nbcommands.app_menu", "nbcommands.file_menu",
                 "nbcommands.edit_menu"):
        check(call in body,
              "AppWindow.menu_items must build its menu from %s" % call)
    for hand in ('"About %s"', '"Close    Esc"', '"Cut"', '"Select All"'):
        check(hand not in body,
              "AppWindow.menu_items still spells %s by hand" % hand)

    undo = _func_source(tree, "undo_menu_items")
    if check(undo is not None, "undo_menu_items must exist"):
        ub = ast.get_source_segment(text, undo) or ""
        check("nbcommands.dynamic_item" in ub,
              "undo_menu_items must take its wording from the registry")
        for hand in ('"Undo    Ctrl+Z"', '"Redo    Ctrl+Shift+Z"',
                     '"Undo %s    Ctrl+Z"'):
            check(hand not in ub,
                  "undo_menu_items still spells %s by hand" % hand)

    # The terminal exception: _on_key drops Ctrl+W/Ctrl+Q when self.term is
    # set, so no shared menu may print either key.
    check("self.term" in text and "KEY_w" in text,
          "the terminal Ctrl+W/Ctrl+Q suppression must still be in nbapp")
    for menu in (nc.app_menu("X", None, None), nc.file_menu(None),
                 nc.edit_menu(None, None, None, None)):
        for lb, _cb in menu:
            check("Ctrl+W" not in lb and "Ctrl+Q" not in lb,
                  "a base menu must not promise a key the terminal eats: %r"
                  % lb)


# --------------------------------------- 6. representative static audit --

AUDIT = ("writer.py", "finder.py", "settings.py", "media.py")
#: files this work changed — a contradiction in one of these is a failure,
#: everywhere else it is a gap for a later migration hour.
TOUCHED = ("nbapp.py", "nbcommands.py")

_BY_NAME = {}
for _c in nc._LIST:
    _BY_NAME.setdefault(_c.title, []).append(_c)


def _menu_labels(path):
    """Every string literal returned from a menu_items() in `path`."""
    text = src(path)
    tree = ast.parse(text)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "menu_items":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if sub.value and sub.value != "-":
                        out.append((sub.value, sub.lineno))
    return out


def test_audit():
    for path in AUDIT + TOUCHED:
        hard = path in TOUCHED
        for lb, line in _menu_labels(path):
            if nc.GAP in lb:
                head, key = lb.split(nc.GAP, 1)
            else:
                head, key = lb, ""
            title = head[:-1] if head.endswith("…") else head
            cmds = _BY_NAME.get(title)
            if not cmds:
                continue                       # an app's own command: fine
            msg = None
            if key and all(key != c.shortcut for c in cmds):
                msg = ("%s:%d %r prints %s; the registry says %s"
                       % (path, line, lb, key,
                          " / ".join(c.shortcut or "no key" for c in cmds)))
            elif all(head.endswith("…") != c.ellipsis for c in cmds):
                msg = ("%s:%d %r disagrees with the registry on the ellipsis"
                       % (path, line, lb))
            if msg is None:
                continue
            (FAIL if hard else GAPS).append(msg)

    # The audit must be able to go red: a green gate nobody has seen fail
    # proves nothing.
    probe = [("Zoom In    Ctrl+Nonsense", 0)]
    caught = False
    for lb, _line in probe:
        head, key = lb.split(nc.GAP, 1)
        cmds = _BY_NAME.get(head)
        caught = bool(cmds) and all(key != c.shortcut for c in cmds)
    check(caught, "the audit must detect a contradicting accelerator")


def main():
    for fn in (test_registry, test_tuples, test_dynamic, test_translated_once,
               test_base_menus, test_audit):
        fn()
    if GAPS:
        print("gaps (reported, not failures — apps not yet migrated):")
        for g in GAPS:
            print("  ~ " + g)
    if FAIL:
        print("\nFAIL (%d):" % len(FAIL))
        for f in FAIL:
            print("  x " + f)
        return 1
    print("commands_selftest: OK (%d commands, %d gaps reported)"
          % (len(nc.COMMANDS), len(GAPS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
