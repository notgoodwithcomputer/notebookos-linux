#!/usr/bin/env python3
"""Static gate for Notebook OS's destructive-action promises.

Inventory methods whose name or body looks destructive, then classify each as
CONFIRMED, UNDOABLE, SNAPSHOT-AFTER-MUTATE, NEITHER, or UNCLASSIFIED.  The
analysis follows one direct ``self._helper()`` call.  It never imports an app.

Run:
  python3 tools/destructive_action_check.py
  python3 tools/destructive_action_check.py --de /path/to/de
  python3 tools/destructive_action_check.py --selfcheck
"""

# WIRED, as of the persistence pass below.
#
# It was not, and the header said why: the mutation test fired on del,
# .clear(), os.remove and overwrite wherever they occurred, so it reported
# Writer clearing its find-highlight and the tablet daemon writing a flag
# file. persistence_for() now asks whether the thing being mutated is
# something the module SAVES, which took the findings from a dozen pieces of
# UI state to three, each ledgered with its own reason.
#
# One correction to an earlier note in this file: I said the persistence pass
# had been destroyed by an over-greedy regex of mine and rebuilt by hand. The
# regex did damage the file — it took out parse_module's header, which was
# restored from the committed version — but the persistence pass itself
# SURVIVED, further down. My reconstruction of it sat above the originals,
# shadowed by them and doing nothing, until I noticed the duplicate
# definitions and removed it. Everything below is the original.
#
# What it was, before all that:
#
# What works: the classification and the red-proof. Given a genuinely
# destructive method it correctly separates CONFIRMED, UNDOABLE, and the
# case that only LOOKS protected — a snapshot taken AFTER the mutation,
# which records the already-changed state. --selfcheck proves all three
# against illustrator's real _delete_layer.
#
# What does not work yet: telling a document from a scratchpad. The
# mutation test fires on `del`, `.clear()`, os.remove and an overwrite,
# wherever they occur — so it reports Writer clearing its find-highlight,
# its save chip and its tab stops, and the tablet daemon writing a flag
# file. None of those destroy anything a person made.
#
# Wiring it now would mean carrying a ledger of a dozen entries that are
# not defects, and a ledger full of non-defects teaches people to ignore
# ledgers. What it needs is a way to ask whether the thing being mutated is
# something the app PERSISTS — reachable from what its save path writes —
# rather than any attribute at all.
#
# The persistence pass now does that before classification. It traces payloads
# entering the module's JSON/text/image save sinks (and one serializer helper),
# keeps mutations of those exact attribute paths or user-home paths, and counts
# everything else by the reason it was ignored. The production scan leaves a
# small, explicit ledger of persistent operations whose semantics are not a
# loss of authored work; --selfcheck proves both sides of the filter.
from __future__ import annotations

import argparse
import ast
import dataclasses
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
DESTRUCTIVE_PREFIXES = ("_delete", "_remove", "_clear", "_empty", "_reset",
                        "_discard", "_trash")
DELETE_CALLS = {"os.remove", "os.unlink", "shutil.rmtree"}
COLLECTION_DELETE_CALLS = {"pop", "popitem", "remove"}
SNAPSHOT_WORDS = ("snapshot", "checkpoint", "push_undo", "save_undo",
                  "record_undo", "set_undo", "undo_action")
SNAPSHOT_CALLS = {"_push", "_begin_edit", "_remember", "_structure"}
CONFIRM_WORDS = ("confirm", "prompt", "are_you_sure", "ask_delete",
                 "ask_remove", "question_dialog")

# NOTE: the dataclasses and the persistence helpers live BELOW the
# ledger, in their original typed form. A reconstruction of them was
# briefly duplicated here after I damaged the file; the duplicate was
# shadowed by the originals and has been removed.
# Exact two-way ledger.  Every accepted entry has its own reason; a new or
# stale entry makes the gate fail.
DEBT: dict[tuple[str, str, str], str] = {
    ("language.py", "Language._pick_goal", "NEITHER"):
        "Daily Goal is a value-setting dialog, not a deletion; the one-level helper inherits a rollback-only pop false positive from _set_goal.",
    ("language.py", "Language._set_goal", "NEITHER"):
        "The only pop restores an absent prior goal after a failed save; it is rollback of an uncommitted setting, not destruction of user content.",
    ("academics.py", "Academics._edit_meeting", "SNAPSHOT-AFTER-MUTATE"):
        "The apparent earlier destructive helper is the dialog's Remove branch, which returns immediately; the edit branch checkpoints before its own replacement.",
    ("finder.py", "Finder._on_restore_all", "NEITHER"):
        "Restore All clears the persisted hidden-app set in order to reveal every app; it removes no user document.",
    ("nbaudio.py", "apply", "NEITHER"):
        "This removes only Notebook OS's marked legacy ALSA routing file after writing its replacement system route.",
    # ("nbnotify.py", "clear_all", "NEITHER") was here. The tray now spans two
    # spools -- the primary one inside the config directory and a fallback on
    # the temp filesystem, so the notice about a disk that will not take a
    # write can itself be written -- and clear_all walks the merged record list
    # (_records()) instead of one listdir + remove. The detector no longer
    # produces the finding, so the row is pruned rather than carried: a ledger
    # entry that matches nothing protects nothing.
    ("nbnotify.py", "prune", "NEITHER"):
        "Notification posting enforces the documented MAX_KEEP/MAX_AGE_S retention policy on its own spool records.",
    ("writer.py", "Writer._deserialize", "NEITHER"):
        "This load/undo reconstruction choke point replaces transient buffer indexes from an incoming saved document; callers own the open/undo protection.",
}


@dataclasses.dataclass(frozen=True)
class Method:
    module: str
    qualname: str
    name: str
    line: int
    end_line: int
    mutation_lines: tuple[int, ...]
    mutation_kinds: tuple[str, ...]
    snapshot_lines: tuple[int, ...]
    confirm_lines: tuple[int, ...]
    helper_calls: tuple[tuple[str, int], ...]
    escape_lines: tuple[int, ...]
    ignored_mutations: tuple[str, ...]
    inventoried: bool = True


@dataclasses.dataclass(frozen=True)
class Result:
    method: Method
    status: str
    detail: str


@dataclasses.dataclass(frozen=True)
class Persistence:
    attributes: frozenset[str]
    path_names: frozenset[str]


SAVE_CALLS = {"json.dump", "json.dumps", "nbapp.atomic_write_json",
              "nbapp.atomic_write_text", "atomic_write_json", "atomic_write_text"}


def self_attribute(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        else:
            node = node.value
    if isinstance(node, ast.Name) and node.id == "self" and parts:
        return ".".join(reversed(parts))
    return None


def loaded_self_attributes(node: ast.AST) -> set[str]:
    found = {path for part in ast.walk(node) if isinstance(part, ast.Attribute)
             and isinstance(part.ctx, ast.Load)
             for path in [self_attribute(part)] if path}
    return {path for path in found
            if not any(other.startswith(path + ".") for other in found)}


def _mentions_home(node: ast.AST, path_names: set[str]) -> bool:
    home_words = {"NB_HOME", "HOME", "DOCS_DIR", "DOCUMENTS", "PICS_DIR",
                  "PICTURES_DIR", "MUSIC_DIR", "VIDEOS_DIR"}
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and (part.id in home_words or part.id in path_names):
            return True
        if isinstance(part, ast.Constant) and isinstance(part.value, str) and any(
                word in part.value for word in ("Documents", "Pictures", "Music", "Videos")):
            return True
        if isinstance(part, ast.Call) and call_name(part) == "os.path.expanduser":
            return True
    return False


def persistence_for(tree: ast.Module) -> Persistence:
    """Find saved payload attributes and module names rooted below NB_HOME."""
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    by_name: dict[str, list[ast.AST]] = {}
    for fn in funcs:
        by_name.setdefault(fn.name, []).append(fn)
    path_names: set[str] = set()
    assignments: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None:
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value
    changed = True
    while changed:
        changed = False
        for name, value in assignments.items():
            if name not in path_names and _mentions_home(value, path_names):
                path_names.add(name)
                changed = True
    attrs: set[str] = set()
    followed: set[int] = set()
    def collect_payload(expr: ast.AST, owner: ast.AST) -> None:
        attrs.update(loaded_self_attributes(expr))
        for call in (n for n in ast.walk(expr) if isinstance(n, ast.Call)):
            targets = by_name.get(call_name(call).split(".")[-1], [])
            if len(targets) == 1 and id(targets[0]) not in followed:
                followed.add(id(targets[0]))
                attrs.update(loaded_self_attributes(targets[0]))
        if isinstance(expr, ast.Name):
            for part in descendants_without_nested(owner):
                if isinstance(part, (ast.Assign, ast.AnnAssign)):
                    targets = part.targets if isinstance(part, ast.Assign) else [part.target]
                    if any(isinstance(t, ast.Name) and t.id == expr.id for t in targets):
                        if part.value is not None:
                            attrs.update(loaded_self_attributes(part.value))
    for fn in funcs:
        for node in descendants_without_nested(fn):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            payloads: list[ast.AST] = []
            if name in SAVE_CALLS or name.endswith((".atomic_write_json", ".atomic_write_text")):
                index = 0 if name in {"json.dump", "json.dumps"} else 1
                if len(node.args) > index:
                    payloads.append(node.args[index])
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "write_to_png":
                payloads.append(node.func.value)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "save":
                if loaded_self_attributes(node.func.value):
                    payloads.append(node.func.value)
            for payload in payloads:
                collect_payload(payload, fn)
    return Persistence(frozenset(attrs), frozenset(path_names))



def dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def call_name(node: ast.Call) -> str:
    return dotted(node.func) or getattr(node.func, "id", "") or ""


def string_value(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def is_overwrite_call(node: ast.Call) -> bool:
    name = call_name(node)
    if name in {"Path.write_text", "Path.write_bytes"} or name.endswith((".write_text", ".write_bytes")):
        return True
    if name in {"open", "io.open"}:
        mode_node = node.args[1] if len(node.args) > 1 else next(
            (kw.value for kw in node.keywords if kw.arg == "mode"), None)
        mode = string_value(mode_node) if mode_node is not None else None
        return bool(mode and any(flag in mode for flag in "wa+"))
    return False


def mutation(node: ast.AST) -> str | None:
    if isinstance(node, ast.Delete):
        return "del"
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        empty = ((isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts)
                 or (isinstance(value, ast.Dict) and not value.keys))
        if empty:
            if any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Slice)
                   for t in targets):
                return "slice-empty"
        # Replacing a saved collection with a filtered comprehension is the
        # normal spelling of deleting one or many records (Calendar uses it
        # for both calendars and their events).
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp)):
            for target in targets:
                attr = self_attribute(target)
                if attr and attr in loaded_self_attributes(value):
                    return "filtered-reassign"
    if isinstance(node, ast.Call):
        name = call_name(node)
        if name in DELETE_CALLS:
            return name
        if isinstance(node.func, ast.Attribute) and node.func.attr == "clear":
            return ".clear()"
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr in COLLECTION_DELETE_CALLS:
            return ".%s()" % node.func.attr
        if is_overwrite_call(node):
            return "overwrite"
    return None


def mutation_persistence(node: ast.AST, persistence: Persistence) -> tuple[bool, str]:
    """Say whether this mutation reaches saved state, else why it is ignored."""
    kind = mutation(node)
    if kind is None:
        return False, "other"
    target: ast.AST | None = None
    if isinstance(node, ast.Delete) and node.targets:
        target = node.targets[0]
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target = targets[0] if targets else None
    elif isinstance(node, ast.Call):
        if (kind == ".clear()" or kind.startswith((".pop", ".remove"))) \
                and isinstance(node.func, ast.Attribute):
            target = node.func.value
        elif kind in DELETE_CALLS:
            target = node.args[0] if node.args else None
            if target is not None and _mentions_home(target, set(persistence.path_names)):
                return True, "user-filesystem"
            return False, "non-user-filesystem"
        elif kind == "overwrite":
            if call_name(node) in {"open", "io.open"}:
                target = node.args[0] if node.args else None
            elif isinstance(node.func, ast.Attribute):
                target = node.func.value
            if target is not None and _mentions_home(target, set(persistence.path_names)):
                return True, "user-filesystem"
            return False, "non-user-filesystem"
    attr = self_attribute(target) if target is not None else None
    if attr is not None:
        if any(attr == saved or attr.startswith(saved + ".") or saved.startswith(attr + ".")
               for saved in persistence.attributes):
            return True, "saved-attribute"
        return False, "transient-attribute"
    return False, "non-persistent-other"


def is_escape_test(node: ast.AST) -> bool:
    return any(isinstance(part, ast.Attribute) and part.attr == "KEY_Escape"
               for part in ast.walk(node))


def descendants_without_nested(fn: ast.AST):
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def position(node: ast.AST) -> int:
    return node.lineno * 10000 + getattr(node, "col_offset", 0)


def source_line(pos: int) -> int:
    return pos // 10000


def transactional_snapshot_lines(fn: ast.AST) -> list[int]:
    """Copied local state later consumed by an explicit save rollback path.

    Apps commonly use ``before = (dict(model), list(order))`` rather than a
    method whose name contains "snapshot". Count that idiom only when the
    copied local is either used inside a failed-save branch or passed as the
    named ``before`` argument to a rollback helper; a merely suggestive local
    name or shallow alias is deliberately insufficient.
    """
    out = []
    nodes = list(descendants_without_nested(fn))
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names:
            continue
        value = node.value
        copies = [part for part in ast.walk(value) if isinstance(part, ast.Call)
                  and (call_name(part) in ("dict", "list", "set", "tuple", "copy.deepcopy")
                       or call_name(part).endswith(".copy"))]
        if not copies:
            continue
        for name in names:
            passed_to_rollback = any(
                isinstance(part, ast.Call)
                and any(kw.arg == "before" and isinstance(kw.value, ast.Name)
                        and kw.value.id == name for kw in part.keywords)
                for part in nodes)
            used_in_failed_save = any(
                isinstance(part, ast.If)
                and isinstance(part.test, ast.UnaryOp)
                and isinstance(part.test.op, ast.Not)
                and any(isinstance(test_part, ast.Call)
                        and "save" in call_name(test_part).lower()
                        for test_part in ast.walk(part.test))
                and any(isinstance(body_part, ast.Name)
                        and isinstance(body_part.ctx, ast.Load)
                        and body_part.id == name
                        for stmt in part.body for body_part in ast.walk(stmt))
                for part in nodes)
            if passed_to_rollback or used_in_failed_save:
                out.append(position(node))
                break
    return out


def confirmation_dominates(fn: ast.AST, call: ast.Call,
                           target: ast.AST) -> bool:
    """Whether a confirmation call executes before every path to target.

    This intentionally recognizes the two product idioms: an unconditional
    call in an earlier statement, and ``if not confirm(): return``. A prompt
    hidden in an unrelated/debug branch must not certify a later deletion.
    """
    def contains(root, needle):
        return root is needle or any(part is needle for part in ast.walk(root))

    def path(body, needle, prefix=()):
        for index, stmt in enumerate(body):
            if not contains(stmt, needle):
                continue
            here = prefix + ((body, index, stmt),)
            if stmt is needle:
                return here
            for field in ("body", "orelse", "finalbody"):
                child = getattr(stmt, field, None)
                if child and any(contains(x, needle) for x in child):
                    return path(child, needle, here)
            for handler in getattr(stmt, "handlers", []):
                if any(contains(x, needle) for x in handler.body):
                    return path(handler.body, needle, here)
            return here
        return ()

    cp, tp = path(fn.body, call), path(fn.body, target)
    if not cp or not tp:
        return False
    common = 0
    while common < min(len(cp), len(tp)) and cp[common][0] is tp[common][0]:
        if cp[common][1] != tp[common][1]:
            break
        common += 1
    if common >= min(len(cp), len(tp)):
        return position(call) < position(target)
    cbody, ci, cstmt = cp[common]
    tbody, ti, _tstmt = tp[common]
    if cbody is not tbody or ci >= ti:
        return False
    # A call nested in an ordinary conditional is not guaranteed to run.
    if isinstance(cstmt, ast.If) and contains(cstmt.test, call):
        returns = any(isinstance(stmt, (ast.Return, ast.Raise))
                      for stmt in cstmt.body)
        negated = any(isinstance(part, ast.UnaryOp)
                      and isinstance(part.op, ast.Not)
                      and contains(part, call) for part in ast.walk(cstmt.test))
        return returns and negated
    return isinstance(cstmt, (ast.Expr, ast.Assign, ast.AnnAssign))


def escape_destructive_lines(fn: ast.AST, method_map: dict[str, list[ast.AST]]) -> tuple[int, ...]:
    hits: list[int] = []
    for node in descendants_without_nested(fn):
        if not isinstance(node, ast.If) or not is_escape_test(node.test):
            continue
        for part in node.body:
            for child in ast.walk(part):
                kind = mutation(child)
                if kind:
                    owner = dotted(child.func.value).lower() if (
                        kind == ".clear()" and isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)) else ""
                    if any(word in owner for word in
                           ("selection", "search", "typeahead", "buffer", "candidate", "cache")):
                        continue
                    hits.append(getattr(child, "lineno", node.lineno))
                elif isinstance(child, ast.Call):
                    name = call_name(child).split(".")[-1]
                    targets = method_map.get(name, [])
                    if len(targets) == 1 and any(mutation(x) for x in descendants_without_nested(targets[0])):
                        hits.append(child.lineno)
    return tuple(sorted(set(hits)))


def parse_module(path: Path) -> list[Method]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    persistence = persistence_for(tree)
    funcs = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    method_map: dict[str, list[ast.AST]] = {}
    for item in funcs:
        method_map.setdefault(item.name, []).append(item)
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    methods: list[Method] = []
    for fn in funcs:
        nodes = list(descendants_without_nested(fn))
        raw_muts = [(n, mutation(n)) for n in nodes if mutation(n)]
        muts = [(position(n), kind) for n, kind in raw_muts
                if mutation_persistence(n, persistence)[0]]
        ignored = [mutation_persistence(n, persistence)[1] for n, _kind in raw_muts
                   if not mutation_persistence(n, persistence)[0]]
        calls = [(call_name(n), position(n)) for n in nodes if isinstance(n, ast.Call)]
        snapshots = [pos for name, pos in calls
                     if (any(word in name.lower() for word in SNAPSHOT_WORDS)
                         or name.split(".")[-1] in SNAPSHOT_CALLS)
                     and not any(x in name.lower() for x in ("restore", "apply", "can_undo", "do_undo"))]
        snapshots.extend(transactional_snapshot_lines(fn))
        helpers = [(name.split(".")[-1], pos) for name, pos in calls
                   if name.startswith("self._")]
        confirm_nodes = [n for n in nodes if isinstance(n, ast.Call)
                         and any(word in call_name(n).lower()
                                 for word in CONFIRM_WORDS)]
        protected_targets = [n for n, _kind in raw_muts
                             if mutation_persistence(n, persistence)[0]]
        protected_targets += [n for n in nodes if isinstance(n, ast.Call)
                              and call_name(n).startswith("self._")
                              and not any(word in call_name(n).lower()
                                          for word in CONFIRM_WORDS)]
        confirms = []
        for call in confirm_nodes:
            later = [target for target in protected_targets
                     if position(target) > position(call)]
            if later and all(confirmation_dominates(fn, call, target)
                             for target in later):
                confirms.append(position(call))
        marked = (fn.name.startswith(DESTRUCTIVE_PREFIXES)
                  or fn.name.startswith(tuple("_on" + p for p in DESTRUCTIVE_PREFIXES))
                  or bool(muts) or bool(ignored))
        chain: list[str] = []
        cur: ast.AST | None = fn
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chain.append(cur.name)
            cur = parents.get(id(cur))
        qualname = ".".join(reversed(chain))
        methods.append(Method(path.name, qualname, fn.name, fn.lineno,
                              getattr(fn, "end_lineno", fn.lineno),
                              tuple(line for line, _ in sorted(muts)),
                              tuple(kind or "" for _, kind in sorted(muts)),
                              tuple(sorted(set(snapshots))), tuple(sorted(set(confirms))),
                              tuple(helpers), (), tuple(ignored), marked))
    # Esc is checked on every handler, including handlers not otherwise in the inventory.
    extra: list[Method] = []
    for fn in funcs:
        hits = escape_destructive_lines(fn, method_map)
        if not hits:
            continue
        existing = next((m for m in methods if m.line == fn.lineno), None)
        if existing:
            methods[methods.index(existing)] = dataclasses.replace(existing, escape_lines=hits)
        else:
            extra.append(Method(path.name, fn.name, fn.name, fn.lineno,
                                getattr(fn, "end_lineno", fn.lineno), (), (), (), (), (), hits, ()))
    return methods + extra


def classify(methods: list[Method]) -> list[Result]:
    by_name: dict[str, list[Method]] = {}
    for method in methods:
        if method.inventoried:
            by_name.setdefault(method.name, []).append(method)

    callers: dict[tuple[str, str, str], list[tuple[Method, int]]] = {}
    for caller in methods:
        owner = caller.qualname.rpartition(".")[0]
        for helper, call_pos in caller.helper_calls:
            callers.setdefault((caller.module, owner, helper), []).append((caller, call_pos))

    def protected_by_callers(method: Method) -> str | None:
        owner = method.qualname.rpartition(".")[0]
        sites = callers.get((method.module, owner, method.name), [])
        if not sites:
            return None
        for caller, call_pos in sites:
            if any(confirm < call_pos for confirm in caller.confirm_lines):
                continue
            # Equality means the helper call itself was recognized as a
            # snapshot-shaped name (for example ``_remember``), not that the
            # caller took a checkpoint before invoking it.
            if any(snapshot < call_pos for snapshot in caller.snapshot_lines):
                continue
            return None
        return f"all {len(sites)} caller(s) confirm or snapshot before calling the helper"

    def direct_status(method: Method) -> tuple[str, str] | None:
        mutations = list(method.mutation_lines)
        if method.confirm_lines:
            first_confirm = min(method.confirm_lines)
            if not mutations or first_confirm < min(mutations):
                return "CONFIRMED", f"confirmation/prompt at line {source_line(first_confirm)}"
            return ("NEITHER", "confirmation follows mutation at line %s" %
                    source_line(min(mutations)))
        if mutations and method.snapshot_lines:
            first_mutation, first_snapshot = min(mutations), min(method.snapshot_lines)
            if first_snapshot < first_mutation:
                return ("UNDOABLE",
                        f"snapshot line {source_line(first_snapshot)} precedes mutation line {source_line(first_mutation)}")
            return ("SNAPSHOT-AFTER-MUTATE",
                    f"mutation line {source_line(first_mutation)} precedes snapshot line {source_line(first_snapshot)}")
        if mutations:
            return "NEITHER", "mutation without confirmation or earlier snapshot"
        return None

    results: list[Result] = []
    for method in methods:
        if not method.inventoried:
            continue
        if method.escape_lines:
            results.append(Result(method, "ESC-DESTRUCTIVE",
                                  "Escape path reaches destructive action at line(s) " +
                                  ",".join(map(str, method.escape_lines))))
            continue
        own = direct_status(method)
        if own:
            if own[0] == "NEITHER":
                caller_detail = protected_by_callers(method)
                if caller_detail:
                    results.append(Result(method, "UNDOABLE", caller_detail))
                    continue
            results.append(Result(method, *own))
            continue
        helper_statuses: list[tuple[str, str]] = []
        for helper, call_pos in method.helper_calls:
            targets = by_name.get(helper, [])
            if len(targets) == 1:
                target = targets[0]
                target_status = direct_status(target)
                if target_status:
                    if any(confirm < call_pos for confirm in method.confirm_lines):
                        helper_statuses.append(("CONFIRMED",
                                                "wrapper confirmation precedes destructive helper"))
                    elif method.confirm_lines:
                        helper_statuses.append(("NEITHER",
                                                "wrapper confirmation follows destructive helper"))
                    elif method.snapshot_lines:
                        snap = min(method.snapshot_lines)
                        if snap < call_pos:
                            helper_statuses.append((
                                "UNDOABLE", f"wrapper snapshot line {source_line(snap)} precedes "
                                f"destructive helper call line {source_line(call_pos)}"))
                        else:
                            helper_statuses.append((
                                "SNAPSHOT-AFTER-MUTATE", f"destructive helper call line "
                                f"{source_line(call_pos)} precedes wrapper snapshot line "
                                f"{source_line(snap)}"))
                    else:
                        helper_statuses.append(target_status)
        if helper_statuses:
            # A wrapper is only as safe as its least-protected destructive
            # helper.  This keeps one-level following from laundering a mixed
            # method through one protected call.
            rank = {"CONFIRMED": 0, "UNDOABLE": 0, "SNAPSHOT-AFTER-MUTATE": 2,
                    "NEITHER": 3}
            status, detail = max(helper_statuses, key=lambda item: rank[item[0]])
            results.append(Result(method, status, "one-level helper: " + detail))
    return results


def scan(de: Path) -> tuple[list[Result], dict[str, int]]:
    all_methods: list[Method] = []
    for path in sorted(de.glob("*.py")):
        all_methods.extend(parse_module(path))
    ignored: dict[str, int] = {}
    for method in all_methods:
        for category in method.ignored_mutations:
            ignored[category] = ignored.get(category, 0) + 1
    return classify(all_methods), ignored


def run(de: Path, use_ledger: bool = True) -> tuple[int, str]:
    try:
        results, ignored = scan(de)
    except (OSError, SyntaxError) as exc:
        return 2, f"ERROR: cannot scan {de}: {exc}\n"
    counts = {status: sum(r.status == status for r in results) for status in
              ("CONFIRMED", "UNDOABLE", "SNAPSHOT-AFTER-MUTATE", "NEITHER", "UNCLASSIFIED", "ESC-DESTRUCTIVE")}
    modules = len({r.method.module for r in results})
    lines = [f"DESTRUCTIVE METHODS: {len(results)} across {modules} app modules",
             "CLASSIFIED: confirmed {CONFIRMED}; undoable {UNDOABLE}; snapshot-after-mutate "
             "{SNAPSHOT-AFTER-MUTATE}; neither {NEITHER}; unclassified {UNCLASSIFIED}".format(**counts),
             f"ESC-DESTRUCTIVE: {counts['ESC-DESTRUCTIVE']}",
             f"PERSISTENCE: kept {sum(len(r.method.mutation_lines) for r in results)} mutation references; "
             f"ignored {sum(ignored.values())} non-persistent mutations",
             "IGNORED: " + "; ".join(f"{name} {count}" for name, count in sorted(ignored.items())),
             f"COVERAGE: analysed {len(results) - counts['UNCLASSIFIED']}/{len(results)} destructive methods; "
             f"COULD NOT CLASSIFY: {counts['UNCLASSIFIED']}"]
    actual = {(r.method.module, r.method.qualname, r.status): r for r in results
              if r.status not in {"CONFIRMED", "UNDOABLE"}}
    lines.append(f"PERSISTENT FINDINGS: {len(actual)}")
    ledger = DEBT if use_ledger else {}
    new = set(actual) - set(ledger)
    stale = set(ledger) - set(actual)
    for key in sorted(new):
        r = actual[key]
        lines.append(f"FINDING {r.status} {key[0]}:{r.method.line} {key[1]} — {r.detail}")
    for key in sorted(stale):
        lines.append(f"STALE DEBT {key[0]} {key[1]} {key[2]}")
    lines.append(f"DEBT: {len(ledger)}")
    for key, reason in sorted(ledger.items()):
        lines.append(f"DEBT {key[0]} {key[1]} {key[2]} — {reason}")
    ok = not new and not stale
    lines.append("RESULT: PASS" if ok else
                 "RESULT: FAILED — destructive-action debt ratchet changed")
    return (0 if ok else 1), "\n".join(lines) + "\n"


def selfcheck() -> tuple[int, str]:
    # A small persisted fixture keeps this proof independent of concurrent app
    # schema refactors. Replace only its destructive method with explicit
    # ordering variants so the production debt ledger cannot make it pass.
    with tempfile.TemporaryDirectory(prefix="destructive-action-selfcheck-") as scratch:
        scratch_de = Path(scratch)
        target = scratch_de / "ordering_fixture.py"
        original = ("import json\n"
                    "class OrderingFixture:\n"
                    "    def _save(self):\n"
                    "        json.dumps(self.items)\n"
                    "    def _delete_layer(self):\n"
                    "        self.items.clear()\n"
                    "        self._snapshot()\n")
        target.write_text(original, encoding="utf-8")
        tree = ast.parse(original)
        fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "_delete_layer"), None)
        if fn is None or fn.end_lineno is None:
            return 1, "SELFCHECK FAIL: ordering fixture not found\n"
        source_lines = original.splitlines()
        indent = " " * fn.col_offset
        replacement = [indent + "def _delete_layer(self):",
                       indent + "    self.items.clear()",
                       indent + "    self._snapshot()"]
        source_lines[fn.lineno - 1:fn.end_lineno] = replacement
        target.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
        command = [sys.executable, str(Path(__file__).resolve()), "--de", str(scratch_de), "--no-ledger"]
        proc = subprocess.run(command, text=True, capture_output=True, check=False)
        after = "FINDING SNAPSHOT-AFTER-MUTATE ordering_fixture.py"
        if proc.returncode == 0 or after not in proc.stdout:
            return 1, "SELFCHECK FAIL: snapshot-after-mutate sabotage did not make gate red\n" + proc.stdout + proc.stderr
        source_lines = original.splitlines()
        replacement = [indent + "def _delete_layer(self):", indent + "    self.items.clear()"]
        source_lines[fn.lineno - 1:fn.end_lineno] = replacement
        target.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
        proc2 = subprocess.run(command, text=True, capture_output=True, check=False)
        neither = "FINDING NEITHER ordering_fixture.py"
        if proc2.returncode == 0 or neither not in proc2.stdout:
            return 1, "SELFCHECK FAIL: removed-snapshot sabotage did not make gate red\n" + proc2.stdout + proc2.stderr
        source_lines = original.splitlines()
        replacement = [indent + "def _delete_layer(self):",
                       indent + "    self._transient_probe.clear()"]
        source_lines[fn.lineno - 1:fn.end_lineno] = replacement
        target.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
        proc3 = subprocess.run(command, text=True, capture_output=True, check=False)
        if ("OrderingFixture._delete_layer" in proc3.stdout or
                "transient-attribute 1" not in proc3.stdout):
            return 1, ("SELFCHECK FAIL: non-saved attribute mutation was reported\n" +
                       proc3.stdout + proc3.stderr)
        caller_fixture = scratch_de / "caller_fixture.py"
        caller_fixture.write_text(
            "import json\n"
            "class CallerFixture:\n"
            "    def _save(self):\n"
            "        json.dumps(self.items)\n"
            "    def _erase_items(self):\n"
            "        self.items.clear()\n"
            "    def command_with_checkpoint(self):\n"
            "        self.undo.checkpoint('Erase Items')\n"
            "        self._erase_items()\n",
            encoding="utf-8")
        protected, _ignored = scan(scratch_de)
        helper_result = next((r for r in protected
                              if r.method.module == caller_fixture.name
                              and r.method.name == "_erase_items"), None)
        if helper_result is None or helper_result.status != "UNDOABLE":
            return 1, ("SELFCHECK FAIL: checkpointing caller did not protect helper\n" +
                       ("no helper result\n" if helper_result is None else
                        f"reported {helper_result.status}: {helper_result.detail}\n"))
        with caller_fixture.open("a", encoding="utf-8") as fixture:
            fixture.write(
                "    def command_without_checkpoint(self):\n"
                "        self._erase_items()\n")
        unprotected, _ignored = scan(scratch_de)
        helper_result = next((r for r in unprotected
                              if r.method.module == caller_fixture.name
                              and r.method.name == "_erase_items"), None)
        if helper_result is None or helper_result.status != "NEITHER":
            return 1, ("SELFCHECK FAIL: unprotected caller did not leave helper as a finding\n" +
                       ("no helper result\n" if helper_result is None else
                        f"reported {helper_result.status}: {helper_result.detail}\n"))
        after_confirm_fixture = scratch_de / "confirm_after_fixture.py"
        after_confirm_fixture.write_text(
            "import json\n"
            "class ConfirmAfterFixture:\n"
            "    def _save(self):\n"
            "        json.dumps(self.items)\n"
            "    def _erase_items(self):\n"
            "        self.items.clear()\n"
            "    def command(self):\n"
            "        self._erase_items()\n"
            "        self.confirm('Too late')\n",
            encoding="utf-8")
        after_confirm, _ignored = scan(scratch_de)
        after_result = next((r for r in after_confirm
                             if r.method.module == after_confirm_fixture.name
                             and r.method.name == "_erase_items"), None)
        if after_result is None or after_result.status != "NEITHER":
            return 1, ("SELFCHECK FAIL: confirmation after helper laundered deletion\n" +
                       ("no helper result\n" if after_result is None else
                        f"reported {after_result.status}: {after_result.detail}\n"))
        conditional_fixture = scratch_de / "conditional_confirm_fixture.py"
        conditional_fixture.write_text(
            "import json\n"
            "class ConditionalConfirmFixture:\n"
            "    def _save(self):\n"
            "        json.dumps(self.items)\n"
            "    def _delete_item(self):\n"
            "        if self.debug:\n"
            "            self.confirm('Debug only')\n"
            "        self.items.clear()\n",
            encoding="utf-8")
        conditional, _ignored = scan(scratch_de)
        conditional_result = next((r for r in conditional
                                   if r.method.module == conditional_fixture.name
                                   and r.method.name == "_delete_item"), None)
        if conditional_result is None or conditional_result.status != "NEITHER":
            return 1, ("SELFCHECK FAIL: conditional confirmation laundered deletion\n" +
                       ("no result\n" if conditional_result is None else
                        f"reported {conditional_result.status}: {conditional_result.detail}\n"))
        transaction_fixture = scratch_de / "transaction_fixture.py"
        transaction_fixture.write_text(
            "import json\n"
            "class TransactionFixture:\n"
            "    def _save(self):\n"
            "        json.dumps(self.items)\n"
            "    def _reset_items(self):\n"
            "        before = list(self.items)\n"
            "        self.items.clear()\n"
            "        if not self._save():\n"
            "            self.items[:] = before\n",
            encoding="utf-8")
        transactional, _ignored = scan(scratch_de)
        transaction_result = next((r for r in transactional
                                   if r.method.module == transaction_fixture.name
                                   and r.method.name == "_reset_items"), None)
        if transaction_result is None or transaction_result.status != "UNDOABLE":
            return 1, ("SELFCHECK FAIL: copied failed-save rollback was not protected\n" +
                       ("no result\n" if transaction_result is None else
                        f"reported {transaction_result.status}: {transaction_result.detail}\n"))
        transaction_fixture.write_text(
            "import json\n"
            "class TransactionFixture:\n"
            "    def _save(self):\n"
            "        json.dumps(self.items)\n"
            "    def _reset_items(self):\n"
            "        before = self.items\n"
            "        self.items.clear()\n"
            "        if not self._save():\n"
            "            self.items[:] = before\n",
            encoding="utf-8")
        shallow, _ignored = scan(scratch_de)
        shallow_result = next((r for r in shallow
                              if r.method.module == transaction_fixture.name
                              and r.method.name == "_reset_items"), None)
        if shallow_result is None or shallow_result.status != "NEITHER":
            return 1, ("SELFCHECK FAIL: shallow alias laundered failed-save mutation\n" +
                       ("no result\n" if shallow_result is None else
                        f"reported {shallow_result.status}: {shallow_result.detail}\n"))
        return 0, ("SELFCHECK PASS: isolated persisted ordering fixture\n"
                   "SELFCHECK PASS: mutation-before-snapshot reported SNAPSHOT-AFTER-MUTATE\n"
                   "SELFCHECK PASS: removed snapshot reported NEITHER\n"
                   "SELFCHECK PASS: persistence filter reported saved layers and ignored non-saved transient state\n"
                   "SELFCHECK PASS: checkpointing caller protected destructive helper; unprotected caller still reported NEITHER\n"
                   "SELFCHECK PASS: confirmation after destructive helper remains NEITHER\n"
                   "SELFCHECK PASS: conditional confirmation does not protect an unconditional mutation\n"
                   "SELFCHECK PASS: copied failed-save rollback is protected; shallow alias remains NEITHER\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--de", type=Path, default=DEFAULT_DE)
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--no-ledger", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    code, output = selfcheck() if args.selfcheck else run(args.de.resolve(), not args.no_ledger)
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
