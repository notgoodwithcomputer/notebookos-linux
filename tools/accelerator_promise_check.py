#!/usr/bin/env python3
"""Static, two-way gate for MENU-CONVENTIONS section 3.

For every app menu, compare advertised accelerator columns with the shortcuts
something actually honours.  This is deliberately an AST check: it never
imports GTK applications.  A handler whose key tests cannot be resolved is
reported as COULD NOT ANALYSE and makes the gate fail.

"Honoured" has two halves, because a menu's promise is to the USER, not to a
particular source file:

  * the app-level key handler consumes the chord — read with the polarity of
    the branch it sits in, so `not ctrl` and the else arm of `if ctrl:` are
    read as the BARE key rather than as a Ctrl chord; and
  * the focused GTK text widget consumes it — GtkTextView and GtkEntry carry
    Cut / Copy / Paste / Select All as class bindings, and an editor that
    re-implemented them in its own handler would be the defect, not the fix.
    See NATIVE_TEXT_COMMANDS for the three conditions that claim has to meet.

Run:
  python3 tools/accelerator_promise_check.py
  python3 tools/accelerator_promise_check.py --de /path/to/de
  python3 tools/accelerator_promise_check.py --selfcheck
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
GAP = "    "

# Accepted mismatches, each with its own specific reason.  This is an exact
# two-way ledger: a new mismatch or a stale entry fails the gate.
_BOUND_DEBT = {
    "academics.py": (),
    "accounting.py": (),
    "animation.py": ("Ctrl+E", "Ctrl+Enter", "Space"),
    "bills.py": ("Ctrl+Z",),
    "calculator.py": ("Ctrl+M", "Ctrl+S", "Esc"),
    "calendar.py": (),
    "comics.py": (),
    "contacts.py": ("Ctrl+Z",),
    "cookbook.py": ("Page Down", "Page Up", "Space"),
    "g2048.py": ("Esc",),
    "gbasdk.py": ("Ctrl+B", "Ctrl+Shift+F", "F2"),
    "illustrator.py": ("Esc",),
    "installer.py": ("Esc",),
    "journal.py": (),
    "media.py": ("End", "Esc", "F11", "Home", "Page Down", "Page Up", "Space"),
    "music.py": ("Ctrl+F", "Space"),
    "novel.py": (),
    "packages.py": ("Esc",),
    "screenplay.py": (),
    "sequencer.py": (),
    "settings.py": ("Esc",),
    "tasks.py": (),
    "video.py": (),
    "workout.py": (),
    "writer.py": (),
}
_ADVERTISED_DEBT = {
    "gbasdk.py": ("Esc",),
    "video.py": (),
    "writer.py": (),
}
DEBT: dict[tuple[str, str, str], str] = {}
for _module, _keys in _BOUND_DEBT.items():
    for _key in _keys:
        if _key == "Ctrl+Y":
            _reason = (f"{_module[:-3]} accepts Ctrl+Y as its alternate redo chord while "
                       "its menu prints the primary Ctrl+Shift+Z chord.")
        elif _key == "Esc":
            _reason = (f"{_module[:-3]} consumes Esc for its app-specific transient state, "
                       "separately from AppWindow's menu Close command.")
        else:
            _reason = (f"{_module[:-3]}'s handler consumes {_key}, but its current menus "
                       "have no accelerator column for that app command.")
        DEBT[(_module, "BOUND-BUT-UNPRINTED", _key)] = _reason
for _module, _keys in _ADVERTISED_DEBT.items():
    for _key in _keys:
        DEBT[(_module, "ADVERTISED-BUT-UNBOUND", _key)] = (
            f"{_module[:-3]}'s menu advertises {_key}, but its app handler does not consume that chord.")

KEY_NAMES = {
    "Escape": "Esc", "space": "Space", "comma": ",", "period": ".",
    "Page_Up": "Page Up", "Page_Down": "Page Down", "BackSpace": "Backspace",
    "plus": "Plus", "minus": "Minus", "equal": "=", "slash": "/",
    "bracketleft": "[", "bracketright": "]", "Delete": "Delete",
    "Home": "Home", "End": "End", "Left": "Left", "Right": "Right",
    "Up": "Up", "Down": "Down", "Return": "Enter", "KP_Enter": "Enter",
}


@dataclass(frozen=True)
class Finding:
    module: str
    direction: str
    accelerator: str
    detail: str


def literal(node: ast.AST, env: dict[str, object] | None = None):
    """Resolve literals, _t(), concatenation and simple local aliases."""
    env = env or {}
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [literal(x, env) for x in node.elts]
        return values if all(x is not None for x in values) else None
    if isinstance(node, ast.Call) and node.args:
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "_t":
            return literal(node.args[0], env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal(node.left, env), literal(node.right, env)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    return None


def registry(de: Path) -> dict[str, str]:
    path = de / "nbcommands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), path)
    env: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            value = literal(node.value, env)
            if isinstance(value, (str, int)):
                env[node.targets[0].id] = value
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_LIST" for t in node.targets)
                and isinstance(node.value, ast.List)):
            continue
        for call in node.value.elts:
            if not isinstance(call, ast.Call) or len(call.args) < 2:
                continue
            cid, title = literal(call.args[0], env), literal(call.args[1], env)
            kw = {x.arg: literal(x.value, env) for x in call.keywords}
            if isinstance(cid, str) and isinstance(title, str):
                label = title + ("…" if kw.get("ellipsis") else "")
                if kw.get("shortcut"):
                    label += GAP + str(kw["shortcut"])
                out[cid] = label
    return out


def call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    return getattr(node.func, "attr", None) or getattr(node.func, "id", None)


def menu_labels(fn: ast.FunctionDef,
                commands: dict[str, str]) -> list[tuple[int, str, ast.AST | None]]:
    """Resolve labels in menu_items, stripping tick padding only at split time.

    Each row carries the callback node it was written with: an accelerator's
    promise is that the chord does what THAT row does, so the row's action is
    part of the evidence, not just its text."""
    env: dict[str, object] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            value = literal(node.value, env)
            if value is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = value
    found: list[tuple[int, str, ast.AST | None]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and call_name(node) == "undo_menu_items":
            found.extend([(node.lineno, "Undo" + GAP + "Ctrl+Z", None),
                          (node.lineno, "Redo" + GAP + "Ctrl+Shift+Z", None)])
        if isinstance(node, ast.Call) and call_name(node) in {"item", "dynamic_item", "source_label"} and node.args:
            cid = literal(node.args[0], env)
            if isinstance(cid, str) and cid in commands:
                row = (node.lineno, commands[cid])
                if row not in seen:
                    seen.add(row)
                    found.append(row + (node.args[-1] if len(node.args) > 1 else None,))
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        for item in node.elts:
            if isinstance(item, (ast.List, ast.Tuple)) and item.elts:
                label = literal(item.elts[0], env)
                if label is None and isinstance(item.elts[0], ast.Call) \
                        and call_name(item.elts[0]) == "_ticked" \
                        and len(item.elts[0].args) >= 2:
                    label = literal(item.elts[0].args[1], env)
                if isinstance(label, str) and label != "-":
                    row = (item.lineno, label)
                    if row not in seen:
                        seen.add(row)
                        found.append(row + (item.elts[1] if len(item.elts) > 1 else None,))
    return sorted(found, key=lambda r: (r[0], r[1]))


def split_accelerator(label: str) -> tuple[str, str] | None:
    # Leading whitespace is state padding, never the accelerator separator.
    parts = label.lstrip().split(GAP, 1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    accel = parts[1].strip()
    aliases = {"Return": "Enter", "Del": "Delete", "+": "Plus",
               "−": "Minus", "-": "Minus"}
    if accel in aliases:
        return parts[0].strip(), aliases[accel]
    bits = accel.split("+")
    bits[-1] = aliases.get(bits[-1], bits[-1])
    return parts[0].strip(), "+".join(bits)


# GTK's own text widgets answer these chords with no app handler at all:
# GtkTextView and GtkEntry carry Cut / Copy / Paste / Select All as CLASS key
# bindings, so in a focused editor the key is consumed by the widget under the
# cursor. An app handler that also claimed them would be the defect — it would
# have to re-implement, for every editor in the OS, what the toolkit already
# does correctly for the focused widget (including inside a find bar, a
# filename field, or any other entry the same window happens to own).
#
# So "bound" cannot mean only "the app's key handler tests for it". It means
# SOMETHING honours the chord. Three things have to be true for the toolkit to
# be that something, and all three are checked below — no app is exempt by
# name:
#   1. the chord is the one this OS prints for that command (read out of
#      nbcommands, so the rule follows the registry if the OS re-keys it);
#   2. the menu row is that same command — not a different command wearing a
#      clipboard chord (Calculator's "Copy Result    Ctrl+C" is NOT this, and
#      Calculator really does bind it by hand);
#   3. the row's own callback performs the operation GTK's binding performs,
#      on a GTK text object, and the app really builds a text widget for the
#      binding to live on.
NATIVE_TEXT_COMMANDS = {
    "edit.cut": ("cut_clipboard",),
    "edit.copy": ("copy_clipboard",),
    "edit.paste": ("paste_clipboard",),
    "edit.select_all": ("select_range", "select_region"),
}
TEXT_WIDGETS = {"TextView", "Entry", "SearchEntry", "SpinButton"}


def hosts_text_widget(cls: ast.ClassDef) -> bool:
    """Does the app build a widget that carries GTK's text key bindings?"""
    for node in ast.walk(cls):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in TEXT_WIDGETS \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "Gtk":
            return True
    return False


def performs(callback: ast.AST | None, methods: dict[str, ast.FunctionDef],
             wanted: tuple[str, ...], seen: set[str] | None = None,
             depth: int = 0) -> bool:
    """Does this menu row's callback reach one of `wanted` GTK calls?

    Follows `self.method` references and `self.method(...)` calls of the same
    class, a bounded number of hops — the shapes menus actually use
    (``self._select_all``; ``lambda: self._clip("cut")``)."""
    if callback is None or depth > 3:
        return False
    seen = set() if seen is None else seen
    if (isinstance(callback, ast.Attribute) and isinstance(callback.value, ast.Name)
            and callback.value.id == "self" and callback.attr in methods
            and callback.attr not in seen):
        seen.add(callback.attr)
        return performs(methods[callback.attr], methods, wanted, seen, depth + 1)
    for part in ast.walk(callback):
        if isinstance(part, ast.Attribute) and part.attr in wanted:
            return True
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute) \
                and isinstance(part.func.value, ast.Name) \
                and part.func.value.id == "self" and part.func.attr in methods \
                and part.func.attr not in seen:
            seen.add(part.func.attr)
            if performs(methods[part.func.attr], methods, wanted, seen, depth + 1):
                return True
    return False


def native_bindings(rows: list[tuple[str, str, ast.AST | None]],
                    cls: ast.ClassDef, methods: dict[str, ast.FunctionDef],
                    commands: dict[str, str]) -> set[str]:
    """Advertised chords the FOCUSED GTK text widget honours by itself."""
    if not hosts_text_widget(cls):
        return set()
    out: set[str] = set()
    for cid, wanted in NATIVE_TEXT_COMMANDS.items():
        pair = split_accelerator(commands.get(cid, ""))
        if pair is None:
            continue
        title, accel = pair
        for row_accel, row_title, callback in rows:
            if row_accel == accel and row_title == title \
                    and performs(callback, methods, wanted):
                out.add(accel)
    return out


def key_attr(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and node.attr.startswith("KEY_"):
        raw = node.attr[4:]
        if raw.startswith("KP_") and raw not in {"KP_Enter"}:
            raw = raw[3:]
        if raw in KEY_NAMES:
            return KEY_NAMES[raw]
        if len(raw) == 1:
            return raw.upper()
        if raw.startswith("F") and raw[1:].isdigit():
            return raw
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in KEY_NAMES:
            return KEY_NAMES[node.value]
        if len(node.value) == 1:
            return node.value.upper()
    return None


def key_expression(node: ast.AST, aliases: set[str], events: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return (node.attr == "keyval" and isinstance(node.value, ast.Name)
                and node.value.id in events)
    if isinstance(node, ast.Call) and call_name(node) == "keyval_name":
        return bool(node.args) and key_expression(node.args[0], aliases, events)
    return False


def compared_keys(test: ast.AST, aliases: set[str], events: set[str]) -> set[str]:
    """Key literals only when compared with the event's tracked key value."""
    found: set[str] = set()
    for comparison in (n for n in ast.walk(test) if isinstance(n, ast.Compare)):
        operands = [comparison.left] + list(comparison.comparators)
        key_indexes = {i for i, operand in enumerate(operands)
                       if key_expression(operand, aliases, events)}
        if not key_indexes:
            continue
        for index, operand in enumerate(operands):
            if index in key_indexes:
                continue
            for part in ast.walk(operand):
                key = key_attr(part)
                if key:
                    found.add(key)
    return found


def aliases_before(fn: ast.FunctionDef, target: ast.AST,
                   events: set[str]) -> tuple[set[str], set[str], set[str]]:
    """Aliases established on the lexical path before ``target``.

    Only assignments in an enclosing block before the statement dominate a
    key test.  A later assignment, or one hidden in a sibling branch, cannot
    retroactively make an earlier comparison safe.
    """
    key_names: set[str] = set()
    ctrl, shift = {"ctrl"}, {"shift"}

    def assigned(stmt):
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            return
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        value = stmt.value
        text = ast.unparse(value)
        for dest in targets:
            if not isinstance(dest, ast.Name):
                continue
            if key_expression(value, key_names, events): key_names.add(dest.id)
            if "CONTROL_MASK" in text: ctrl.add(dest.id)
            if "SHIFT_MASK" in text: shift.add(dest.id)

    def contains(node):
        return node is target or any(part is target for part in ast.walk(node))

    def walk_block(body):
        for stmt in body:
            if stmt is target:
                return True
            if contains(stmt):
                for field in ("body", "orelse", "finalbody"):
                    child = getattr(stmt, field, None)
                    if child and any(contains(x) for x in child):
                        return walk_block(child)
                handlers = getattr(stmt, "handlers", [])
                for handler in handlers:
                    if any(contains(x) for x in handler.body):
                        return walk_block(handler.body)
                return True
            assigned(stmt)
        return False

    walk_block(fn.body)
    return key_names, ctrl, shift


def parent_map(fn: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(fn):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def enclosing_tests(node: ast.If, parents: dict[int, ast.AST]) -> list[tuple[ast.AST, bool]]:
    """The if-chain around ``node``, each test paired with the polarity the
    key test needs from it.  Sitting in an ``if``'s body needs the test true;
    sitting in its ``else``/``elif`` arm needs it FALSE."""
    out: list[tuple[ast.AST, bool]] = [(node.test, True)]
    child: ast.AST = node
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.If) and cur is not node:
            if any(x is child for x in cur.body):
                out.append((cur.test, True))
            elif any(x is child for x in cur.orelse):
                out.append((cur.test, False))
        child = cur
        cur = parents.get(id(cur))
    return out


def _mentions(text: str, names: set[str]) -> bool:
    """Is any of `names` used as a whole word in this expression?"""
    return any(re.search(r"\b%s\b" % re.escape(n), text) for n in names)


def _record(node: ast.AST, positive: bool, ctrl_names: set[str],
            shift_names: set[str], out: dict[str, object]) -> None:
    """Walk one boolean test, tracking negation, and record what each
    modifier is REQUIRED to be for the branch to run."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _record(node.operand, not positive, ctrl_names, shift_names, out)
        return
    if isinstance(node, ast.BoolOp):
        # `a and b` under a true test, and `a or b` under a false one, both
        # require EVERY operand; the other two shapes require none of them.
        if isinstance(node.op, ast.And) == positive:
            for value in node.values:
                _record(value, positive, ctrl_names, shift_names, out)
        return
    if isinstance(node, ast.Call) and node.args and \
            (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == "bool":
        _record(node.args[0], positive, ctrl_names, shift_names, out)
        return
    text = ast.unparse(node)
    for modifier, mask, names in (("ctrl", "CONTROL_MASK", ctrl_names),
                                  ("shift", "SHIFT_MASK", shift_names)):
        if mask in text or _mentions(text, names):
            if modifier not in out:
                out[modifier] = positive
            elif out[modifier] is not positive:
                # Two rungs of the same chain demanding opposite polarities is
                # not resolvable; leave the modifier unknown rather than guess.
                out[modifier] = None


def modifiers_required(contexts: list[tuple[ast.AST, bool]], ctrl_names: set[str],
                       shift_names: set[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for test, positive in contexts:
        _record(test, positive, ctrl_names, shift_names, out)
    return out


def has_true_return(node: ast.AST) -> bool:
    return any(isinstance(x, ast.Return) and isinstance(x.value, ast.Constant)
               and x.value.value is True for x in ast.walk(node))


def bindings(fn: ast.FunctionDef) -> tuple[set[str], list[str]]:
    """Extract consumed key chords; return bindings and unresolved constructs."""
    params = [arg.arg for arg in fn.args.args if arg.arg != "self"]
    event_names = {params[-1]} if params else set()
    out: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or not has_true_return(node):
            continue
        key_aliases, ctrl_names, shift_names = aliases_before(
            fn, node, event_names)
        keys = compared_keys(node.test, key_aliases, event_names)
        if not keys:
            continue
        # The containing lexical `if ctrl:` is significant, and so is WHICH
        # branch of it the key test sits in.  Read the enclosing chain with
        # its polarity: `not ctrl`, and the else-arm of `if ctrl:`, both mean
        # the chord requires Control to be ABSENT.  Reading the chain as flat
        # text made the media viewer's bare F11 read as "Ctrl+F11" and its
        # explicitly-unshifted undo read as "Ctrl+Shift+Z" — chords the app
        # refuses, reported as chords it consumes.
        parents = parent_map(fn)
        contexts = enclosing_tests(node, parents)
        required = modifiers_required(contexts, ctrl_names, shift_names)
        ctrl = required.get("ctrl") is True
        body_text = " ".join(ast.unparse(x) for x in node.body)
        # A branch that reads Shift in its BODY ("save_as if shift else save")
        # answers the chord both ways; the test itself, when it names Shift,
        # decides which one.
        body_shift = "SHIFT_MASK" in body_text or _mentions(body_text, shift_names)
        shift = required.get("shift")
        for key in keys:
            prefix = "Ctrl+" if ctrl else ""
            if shift is True:
                out.add(prefix + "Shift+" + key)
            elif shift is False:
                out.add(prefix + key)
            elif body_shift:
                out.add(prefix + key)
                out.add(prefix + "Shift+" + key)
            else:
                out.add(prefix + key)
    # Shared undo helper is explicitly part of the app handler contract.
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and isinstance(n.func.value, ast.Name) and n.func.value.id == "nbapp"
           and n.func.attr == "undo_keys" for n in ast.walk(fn)):
        out.update({"Ctrl+Z", "Ctrl+Shift+Z", "Ctrl+Y"})
    # Equivalent physical spellings collapse to the menu vocabulary.
    if "Ctrl+=" in out:
        out.remove("Ctrl+="); out.add("Ctrl+Plus")
    # Text-entry and canvas editing keys are input grammar, not menu
    # accelerators. Bare keys count here only for OS-level transport/navigation
    # commands; modified command chords all count (except modified navigation,
    # which remains selection/range grammar).
    command_bare = {"Esc", "Space", ",", ".", "Home", "End",
                    "Page Up", "Page Down"}
    return out, unresolved


def app_class(tree: ast.Module) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id == "AppWindow") or
                (isinstance(b, ast.Attribute) and b.attr == "AppWindow")
                for b in node.bases):
            return node
    return None


def analyse(path: Path, commands: dict[str, str]) -> tuple[list[Finding], bool, list[str], set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), path)
    cls = app_class(tree)
    if cls is None:
        return [], False, ["no AppWindow subclass"], set(), set()
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    menu = methods.get("menu_items")
    if menu is None:
        return [], False, ["no menu_items method"], set(), set()
    roots: set[str] = set()
    if "_on_key" in methods:
        roots.add("_on_key")
    for node in ast.walk(cls):
        if isinstance(node, ast.Call) and call_name(node) == "connect" and len(node.args) >= 2 \
                and literal(node.args[0]) == "key-press-event":
            cb = node.args[1]
            if isinstance(cb, ast.Attribute) and isinstance(cb.value, ast.Name) \
                    and cb.value.id == "self" and cb.attr in methods:
                # Only a window-level connection promises app commands.
                owner = getattr(node.func, "value", None)
                if isinstance(owner, ast.Name) and owner.id == "self":
                    roots.add(cb.attr)
    advertised: dict[str, list[str]] = {}
    rows: list[tuple[str, str, ast.AST | None]] = []
    for line, label, callback in menu_labels(menu, commands):
        pair = split_accelerator(label)
        if pair:
            command, accel = pair
            advertised.setdefault(accel, []).append(f"{command} (line {line})")
            rows.append((accel, command, callback))
    native = native_bindings(rows, cls, methods, commands)
    if not roots:
        # These apps inherit AppWindow's Esc implementation unchanged.
        roots_bound = {"Esc"} | native
        findings = [Finding(path.name, "ADVERTISED-BUT-UNBOUND", key,
                            ", ".join(advertised[key]))
                    for key in sorted(set(advertised) - roots_bound)]
        return findings, True, [], set(advertised), roots_bound
    bound: set[str] = set()
    unresolved: list[str] = []
    for name in sorted(roots):
        got, unknown = bindings(methods[name])
        bound.update(got)
        unresolved.extend(f"{name}: {x}" for x in unknown)
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and isinstance(n.func.value, ast.Call)
           and isinstance(n.func.value.func, ast.Name) and n.func.value.func.id == "super"
           and n.func.attr == "_on_key" for name in roots for n in ast.walk(methods[name])):
        bound.add("Esc")
    bound |= native
    findings = [Finding(path.name, "ADVERTISED-BUT-UNBOUND", key,
                        ", ".join(advertised[key]))
                for key in sorted(set(advertised) - bound)]
    command_bare = {"Esc", "Space", ",", ".", "Home", "End",
                    "Page Up", "Page Down"}
    reportable = {key for key in bound if key in command_bare or
                  (key.startswith("F") and key[1:].isdigit()) or
                  (key.startswith("Ctrl+") and key.split("+")[-1] not in
                   {"Left", "Right", "Up", "Down", "Page Up", "Page Down", "Delete"})}
    # Decided once, in one place, for the whole OS: nbapp.undo_keys binds
    # Ctrl+Z, Ctrl+Shift+Z AND Ctrl+Y, and its own docstring says why —
    # "BOTH redo conventions ... Ctrl+Shift+Z (what this OS prints in its
    # menus) and Ctrl+Y (what a user arriving from Windows will try first)".
    # Ctrl+Y is deliberately bound and deliberately not printed. Carried as
    # ONE rule with the helper's own justification rather than as seventeen
    # identical per-app debts: a rule that convicts a shared helper in every
    # app that uses it is a rule about the rule.
    reportable -= {"Ctrl+Y"}
    findings += [Finding(path.name, "BOUND-BUT-UNPRINTED", key, "app key handler")
                 for key in sorted(reportable - set(advertised))]
    if unresolved:
        findings = []
    return findings, not unresolved, unresolved, set(advertised), bound


def modules(de: Path) -> tuple[list[Path], list[tuple[str, str]]]:
    out = []
    failures: list[tuple[str, str]] = []
    for path in sorted(de.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), path)
        except (OSError, SyntaxError) as exc:
            failures.append((path.name, str(exc)))
            continue
        if app_class(tree) is not None and any(isinstance(n, ast.FunctionDef) and n.name == "menu_items" for n in ast.walk(tree)):
            out.append(path)
    return out, failures


def sweep(de: Path, show: bool = True) -> tuple[list[Finding], list[str], list[str]]:
    commands = registry(de)
    raw: list[Finding] = []
    analysed, could_not = [], []
    paths, discovery_failures = modules(de)
    for name, detail in discovery_failures:
        could_not.append(name)
        if show:
            print(f"COULD NOT ANALYSE {name}: {detail}")
    for path in paths:
        try:
            findings, ok, unknown, _shown, _bound = analyse(path, commands)
        except (OSError, SyntaxError, ValueError) as exc:
            findings, ok, unknown = [], False, [str(exc)]
        raw.extend(findings)
        (analysed if ok else could_not).append(path.name)
        if show and not ok:
            print(f"COULD NOT ANALYSE {path.name}: {'; '.join(unknown)}")
    current = {(f.module, f.direction, f.accelerator): f for f in raw}
    new = [f for key, f in sorted(current.items()) if key not in DEBT]
    stale = sorted(key for key in DEBT if key not in current)
    if show:
        for f in new:
            print(f"{f.direction} {f.module}: {f.accelerator} — {f.detail}")
        for module, direction, accel in stale:
            print(f"STALE-DEBT {module}: {direction} {accel} — prune its ledger entry")
        print(f"COVERAGE: analysed {len(analysed)}/{len(analysed) + len(could_not)} app handlers; "
              f"COULD NOT ANALYSE: {len(could_not)}")
        print("ANALYSED: " + (", ".join(analysed) or "none"))
        print("COULD-NOT-ANALYSE: " + (", ".join(could_not) or "none"))
        print(f"DEBT: {len(DEBT)}")
        for key, reason in sorted(DEBT.items()):
            print(f"DEBT {key[0]}: {key[1]} {key[2]} — {reason}")
        # A bare "PASS" is not a verdict run_all_gates recognises (SUCCESSWORD),
        # so a clean run was recorded as DID NOT RUN — the gate went quiet at
        # the exact moment it started protecting something.
        print("RESULT: %s" % ("ALL PASS"
                              if not new and not stale and not could_not
                              and analysed else "FAILED"))
    return new + [Finding(x[0], "STALE-DEBT", x[2], x[1]) for x in stale], analysed, could_not


def selfcheck(de: Path) -> int:
    real_handler = ast.parse(
        "def handler(self, event):\n"
        "    name = Gdk.keyval_name(event.keyval)\n"
        "    if name == 'Escape':\n"
        "        return True\n").body[0]
    unrelated_handler = ast.parse(
        "def handler(self, event):\n"
        "    if self.mode == 'Escape':\n"
        "        return True\n").body[0]
    unrelated_keyval = ast.parse(
        "def handler(self, event):\n"
        "    if self.saved.keyval == Gdk.KEY_Escape:\n"
        "        return True\n").body[0]
    late_alias = ast.parse(
        "def handler(self, event):\n"
        "    if key == Gdk.KEY_F1:\n"
        "        return True\n"
        "    key = event.keyval\n").body[0]
    early_alias = ast.parse(
        "def handler(self, event):\n"
        "    key = event.keyval\n"
        "    if key == Gdk.KEY_F1:\n"
        "        return True\n").body[0]
    assert "Esc" in bindings(real_handler)[0], \
        "selfcheck missed keyval_name Escape comparison"
    assert "Esc" not in bindings(unrelated_handler)[0], \
        "selfcheck treated unrelated state string as Escape binding"
    assert "Esc" not in bindings(unrelated_keyval)[0], \
        "selfcheck treated an unrelated object's keyval as the event key"
    assert "F1" not in bindings(late_alias)[0], \
        "selfcheck let a later assignment establish an earlier key alias"
    assert "F1" in bindings(early_alias)[0], \
        "selfcheck lost a dominating key alias"
    print("SELFCHECK KEY IDENTITY: unrelated Escape state/keyval ignored")

    # Modifier POLARITY: a chord guarded by `not ctrl`, or sitting in the else
    # arm of `if ctrl:`, is the BARE key — reporting it as a Ctrl chord accuses
    # the app of consuming a chord it explicitly declines.
    negated = ast.parse(
        "def handler(self, event):\n"
        "    ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)\n"
        "    if not ctrl and event.keyval == Gdk.KEY_F13:\n"
        "        return True\n").body[0]
    else_arm = ast.parse(
        "def handler(self, event):\n"
        "    if event.state & Gdk.ModifierType.CONTROL_MASK:\n"
        "        pass\n"
        "    else:\n"
        "        if event.keyval == Gdk.KEY_F14:\n"
        "            return True\n").body[0]
    unshifted = ast.parse(
        "def handler(self, event):\n"
        "    if event.state & Gdk.ModifierType.CONTROL_MASK:\n"
        "        if (event.keyval == Gdk.KEY_z\n"
        "                and not (event.state & Gdk.ModifierType.SHIFT_MASK)):\n"
        "            return True\n").body[0]
    assert bindings(negated)[0] == {"F13"}, \
        "selfcheck read `not ctrl` as a Ctrl chord"
    assert bindings(else_arm)[0] == {"F14"}, \
        "selfcheck let an else arm inherit its `if ctrl:` as true"
    assert bindings(unshifted)[0] == {"Ctrl+Z"}, \
        "selfcheck invented a Shift chord the test rules out"
    print("SELFCHECK MODIFIER POLARITY: negated and else-arm modifiers read as absent")

    with tempfile.TemporaryDirectory(prefix="accelerator-promise-") as tmp:
        copy = Path(tmp) / "de"
        shutil.copytree(de, copy)
        target = copy / "animation.py"
        original = target.read_text(encoding="utf-8")
        source = original.replace("'Pixel Grid    G'", "'Pixel Grid    F12'", 1)
        marker = "        ctrl = bool(e.state & Gdk.ModifierType.CONTROL_MASK)"
        injected = marker + "\n        if e.keyval == Gdk.KEY_F11:\n            return True"
        if source == original or marker not in source:
            raise AssertionError("selfcheck could not mutate the real animation app")
        target.write_text(source.replace(marker, injected, 1), encoding="utf-8")
        findings, _ok, _unknown, _shown, _bound = analyse(target, registry(copy))
        advertised = any(f.direction == "ADVERTISED-BUT-UNBOUND" and f.accelerator == "F12"
                         for f in findings)
        bound = any(f.direction == "BOUND-BUT-UNPRINTED" and f.accelerator == "F11"
                    for f in findings)
        print("SELFCHECK ADVERTISED-BUT-UNBOUND: " + ("CAUGHT F12" if advertised else "MISSED F12"))
        print("SELFCHECK BOUND-BUT-UNPRINTED: " + ("CAUGHT F11" if bound else "MISSED F11"))
        if not advertised or not bound:
            raise AssertionError("selfcheck failed to catch both mismatch directions")
        print("SELFCHECK PASS")

        # GTK's own text-widget bindings count as bound — but only while all
        # three legs of that claim hold. Sabotage each leg on the REAL Writer
        # and watch the promise go unbound again.
        writer = copy / "writer.py"
        pristine = writer.read_text(encoding="utf-8")
        _f, _ok, _u, _adv, bound = analyse(writer, registry(copy))
        assert {"Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X"} <= bound, \
            "selfcheck lost GTK's own Cut/Copy/Paste/Select All bindings"
        no_widget = ast.parse(
            "class Bare(AppWindow):\n"
            "    def menu_items(self, name):\n"
            "        return [('Cut    Ctrl+X', self._x)]\n").body[0]
        assert not hosts_text_widget(no_widget), \
            "selfcheck credited a text binding to an app with no text widget"
        sabotages = (
            ('("Select All' + GAP + 'Ctrl+A", self._select_all)',
             '("Highlight Everything' + GAP + 'Ctrl+A", self._select_all)',
             "Ctrl+A", "row renamed off the command GTK performs"),
            ('("Cut' + GAP + 'Ctrl+X", lambda: self._clip("cut"))',
             '("Cut' + GAP + 'Ctrl+X", self._file_save)',
             "Ctrl+X", "row pointed at a callback that does not cut"),
        )
        for old_row, new_row, chord, leg in sabotages:
            if old_row not in pristine:
                raise AssertionError("selfcheck could not find %r in writer.py" % old_row)
            writer.write_text(pristine.replace(old_row, new_row, 1), encoding="utf-8")
            findings, _ok, _u, _adv, _b = analyse(writer, registry(copy))
            caught = any(f.direction == "ADVERTISED-BUT-UNBOUND" and f.accelerator == chord
                         for f in findings)
            print("SELFCHECK NATIVE TEXT BINDING (%s): %s %s"
                  % (leg, "CAUGHT" if caught else "MISSED", chord))
            writer.write_text(pristine, encoding="utf-8")
            if not caught:
                raise AssertionError("a native text binding survived %s" % leg)

        broken = copy / "broken_app.py"
        broken.write_text("class Broken(AppWindow):\n def menu_items(self): [\n",
                          encoding="utf-8")
        _findings, _analysed, could_not = sweep(copy, show=False)
        if "broken_app.py" not in could_not:
            raise AssertionError("syntax-broken app disappeared during discovery")
        print("SELFCHECK PARSE FAILURE: syntax-broken source is loud")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--de", type=Path, default=DEFAULT_DE)
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args()
    if args.selfcheck:
        return selfcheck(args.de)
    findings, _analysed, could_not = sweep(args.de)
    # Unknown coverage is loud and counted, as in offered_range_check; it is
    # not silently converted into either direction of mismatch.
    return 1 if findings or could_not or not _analysed else 0


if __name__ == "__main__":
    raise SystemExit(main())
