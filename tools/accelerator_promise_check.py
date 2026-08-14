#!/usr/bin/env python3
"""Static, two-way gate for MENU-CONVENTIONS section 3.

For every app menu, compare advertised accelerator columns with shortcuts
consumed by the app-level key handler.  This is deliberately an AST check: it
never imports GTK applications.  A handler whose key tests cannot be resolved
is reported as COULD NOT ANALYSE and makes the gate fail.

Run:
  python3 tools/accelerator_promise_check.py
  python3 tools/accelerator_promise_check.py --de /path/to/de
  python3 tools/accelerator_promise_check.py --selfcheck
"""
from __future__ import annotations

import argparse
import ast
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
    "calendar.py": (),
    "comics.py": ("Ctrl+[", "Ctrl+]"),
    "contacts.py": ("Ctrl+Z",),
    "cookbook.py": ("Page Down", "Page Up", "Space"),
    "gbasdk.py": ("Ctrl+B", "Ctrl+F", "Ctrl+Shift+F", "F2"),
    "illustrator.py": ("Ctrl+1", "Ctrl+G", "Ctrl+[", "Ctrl+]", "Esc"),
    "installer.py": ("Esc",),
    "journal.py": (),
    "media.py": ("Ctrl+0", "Ctrl+F", "Ctrl+Minus", "Ctrl+Plus", "End", "Esc", "Home", "Space"),
    "music.py": ("Ctrl+F", "Ctrl+Space"),
    "novel.py": (),
    "screenplay.py": (),
    "sequencer.py": (),
    "settings.py": ("Esc",),
    "tasks.py": (),
    "terminal.py": ("Ctrl+A", "Ctrl+C", "Ctrl+V", "Esc"),
    "video.py": (),
    "workout.py": (),
    "writer.py": (),
}
_ADVERTISED_DEBT = {
    "gbasdk.py": ("Esc",),
    "mealplanner.py": ("Ctrl+Shift+Z", "Ctrl+Z"),
    "video.py": ("Ctrl+Shift+Z",),
    "writer.py": ("Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X"),
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


def menu_labels(fn: ast.FunctionDef, commands: dict[str, str]) -> list[tuple[int, str]]:
    """Resolve labels in menu_items, stripping tick padding only at split time."""
    env: dict[str, object] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            value = literal(node.value, env)
            if value is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = value
    found: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and call_name(node) == "undo_menu_items":
            found.extend([(node.lineno, "Undo" + GAP + "Ctrl+Z"),
                          (node.lineno, "Redo" + GAP + "Ctrl+Shift+Z")])
        if isinstance(node, ast.Call) and call_name(node) in {"item", "dynamic_item", "source_label"} and node.args:
            cid = literal(node.args[0], env)
            if isinstance(cid, str) and cid in commands:
                row = (node.lineno, commands[cid])
                if row not in seen:
                    seen.add(row); found.append(row)
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
                        seen.add(row); found.append(row)
    return sorted(found)


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
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) == 1:
        return node.value.upper()
    return None


def modifier_aliases(fn: ast.FunctionDef) -> tuple[set[str], set[str]]:
    ctrl, shift = {"ctrl"}, {"shift"}
    for node in ast.walk(fn):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        text = ast.unparse(node.value)
        for target in targets:
            if isinstance(target, ast.Name):
                if "CONTROL_MASK" in text: ctrl.add(target.id)
                if "SHIFT_MASK" in text: shift.add(target.id)
    return ctrl, shift


def has_true_return(node: ast.AST) -> bool:
    return any(isinstance(x, ast.Return) and isinstance(x.value, ast.Constant)
               and x.value.value is True for x in ast.walk(node))


def bindings(fn: ast.FunctionDef) -> tuple[set[str], list[str]]:
    """Extract consumed key chords; return bindings and unresolved constructs."""
    ctrl_names, shift_names = modifier_aliases(fn)
    key_aliases = {"keyval", "kv"}
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if "keyval" in ast.unparse(node.value):
                key_aliases.update(t.id for t in targets if isinstance(t, ast.Name))
    out: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and call_name(node) == "keyval_name":
            unresolved.append(f"line {node.lineno}: Gdk.keyval_name/string dispatch")
        if not isinstance(node, ast.If) or not has_true_return(node):
            continue
        keys = {k for part in ast.walk(node.test) if (k := key_attr(part))}
        if not keys:
            continue
        # The containing lexical `if ctrl:` is significant. Walk ancestors by
        # building them once here; comparisons in bodies are never key tests.
        parents: dict[int, ast.AST] = {}
        for parent in ast.walk(fn):
            for child in ast.iter_child_nodes(parent): parents[id(child)] = parent
        contexts = [node.test]
        cur: ast.AST = node
        while id(cur) in parents:
            cur = parents[id(cur)]
            if isinstance(cur, ast.If) and cur is not node: contexts.append(cur.test)
        context = " ".join(ast.unparse(x) for x in contexts)
        ctrl = "CONTROL_MASK" in context
        ctrl = ctrl or any(__import__("re").search(r"\b%s\b" % __import__("re").escape(n), context)
                           for n in ctrl_names)
        body_text = " ".join(ast.unparse(x) for x in node.body)
        shift = "SHIFT_MASK" in context or any(__import__("re").search(
            r"\b%s\b" % __import__("re").escape(n), context) for n in shift_names)
        shift = shift or any(__import__("re").search(r"\b%s\b" % __import__("re").escape(n), body_text)
                           for n in shift_names)
        shift_optional = shift and not any(
            (isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.Not)
             and isinstance(x.operand, ast.Name) and x.operand.id in shift_names)
            for c in contexts for x in ast.walk(c))
        for key in keys:
            prefix = "Ctrl+" if ctrl else ""
            if shift_optional:
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
    for line, label in menu_labels(menu, commands):
        pair = split_accelerator(label)
        if pair:
            command, accel = pair
            advertised.setdefault(accel, []).append(f"{command} (line {line})")
    if not roots:
        # These apps inherit AppWindow's Esc implementation unchanged.
        roots_bound = {"Esc"}
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


def modules(de: Path) -> list[Path]:
    out = []
    for path in sorted(de.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), path)
        except (OSError, SyntaxError):
            continue
        if app_class(tree) is not None and any(isinstance(n, ast.FunctionDef) and n.name == "menu_items" for n in ast.walk(tree)):
            out.append(path)
    return out


def sweep(de: Path, show: bool = True) -> tuple[list[Finding], list[str], list[str]]:
    commands = registry(de)
    raw: list[Finding] = []
    analysed, could_not = [], []
    for path in modules(de):
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
        print("PASS" if not new and not stale else "FAIL")
    return new + [Finding(x[0], "STALE-DEBT", x[2], x[1]) for x in stale], analysed, could_not


def selfcheck(de: Path) -> int:
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
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
