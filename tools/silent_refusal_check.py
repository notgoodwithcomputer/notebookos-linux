#!/usr/bin/env python3
"""Gate silent guard returns in app-level menu callbacks.

The analysis starts at literal callbacks returned by each app's ``menu_items``
method, then inspects only those methods.  A bare/None/False return below an
``if`` is a finding unless that path gives user feedback or the menu disables
the callback under the same condition.

DEBT is an exact two-way ratchet keyed by (file, method).  New findings and
stale entries both fail.  ``--selfcheck`` sabotages a copied real app and proves
that the ordinary analysis reports it.
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")

# (app file, callback method) -> why this deliberate static-analysis debt stays.
# This is an exact ratchet: remove an entry as soon as its matching finding goes.
_DEBT_KEYS = """
academics.py:_clear_search
academics.py:_cycle_style
academics.py:_delete_homework
academics.py:_delete_lecture
academics.py:_insert_at_cursor
academics.py:_insert_list
academics.py:_move_lecture
academics.py:_nav
academics.py:_new_lecture
academics.py:_recount
academics.py:_set_style
academics.py:_toggle_tag
animation.py:_add_take
animation.py:_choose_take_prompt
animation.py:_delete_cel
animation.py:_duplicate_scene
animation.py:_move_scene
animation.py:_new_scene
animation.py:_palette_add
animation.py:_recolor_cel
animation.py:_remove_take
animation.py:_rename_cel_prompt
bills.py:_open_payment
bills.py:_undo_delete
calculator.py:recall
comics.py:_delete_layer
comics.py:_delete_selection
comics.py:_new_layer
contacts.py:_clear_search
contacts.py:_delete_contact
contacts.py:_export_vcard
contacts.py:_step
cookbook.py:_confirm_delete_current
cookbook.py:_delete_active_category
cookbook.py:_duplicate_current
cookbook.py:_enter_cook
cookbook.py:_select_relative
ebook.py:_on_library_open
g2048.py:move
g2048.py:undo_new_game
gbasdk.py:_delete_resource
gbasdk.py:_move_to_folder
gbasdk.py:_rename_resource
gbasdk.py:_show_where_used
illustrator.py:_clear_active_layer
illustrator.py:_copy_image
illustrator.py:_delete_layer
illustrator.py:_move_layer
illustrator.py:_set_fill_shapes
illustrator.py:_set_zoom
illustrator.py:_zoom_fit
journal.py:_clear_format
journal.py:_clear_search
journal.py:_delete_active
journal.py:_go_entry
journal.py:_toggle_tag
maps.py:_fit
maps.py:_zoom
mealplanner.py:_clear_week
media.py:_on_trash
music.py:_delete_current_playlist
music.py:_menu_volume
music.py:_rename_current_playlist
novel.py:_on_delete_part
novel.py:_on_file_save
novel.py:_on_fmt
packages.py:_on_open
packages.py:_on_verify
sequencer.py:_copy_clip
sequencer.py:_cut_clip
sequencer.py:_delete_selected
sequencer.py:_duplicate_clip
sequencer.py:_export_audio
sequencer.py:_on_ff
sequencer.py:_on_play
sequencer.py:_on_rec
sequencer.py:_on_rew
sequencer.py:_paste_clip
sequencer.py:_toggle_loop
sequencer.py:_toggle_metro
tasks.py:_remove_list
terminal.py:_shell_reset
terminal.py:_term_copy
terminal.py:_term_paste
terminal.py:_term_select_all
terminal.py:_toggle_blink
terminal.py:_zoom
video.py:_delete_clip_guarded
video.py:_menu_add_transition
video.py:_menu_split
video.py:_move_clip
workout.py:_delete_exercise
workout.py:_edit_exercise
writer.py:_redo
writer.py:_toggle_char
writer.py:_undo
""".split()

DEBT = {
    tuple(item.split(":", 1)):
        "derived/aliased menu gating or an idempotent/invariant guard; "
        "the command is not a silent refusal"
    for item in _DEBT_KEYS
}

FEEDBACK_WORDS = (
    "flash", "overlay", "prompt", "dialog", "save_failure_reason",
    "status", "chip", "message", "alert", "toast", "notify", "warning",
    "confirm", "choose", "chooser", "picker", "pick_file", "select_file",
    "guard_document", "ok_to_discard", "confirm_discard", "_card",
)


@dataclass(frozen=True)
class Finding:
    file: str
    method: str
    line: int
    condition: str


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _callback_name(node):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "self":
        return node.attr
    # Literal menu lambdas commonly adapt a callback argument.  Follow only a
    # direct call to a self method; arbitrary lambda bodies are not callbacks
    # we can soundly name.
    if isinstance(node, ast.Lambda) and isinstance(node.body, ast.Call):
        return _callback_name(node.body.func)
    return None


def _norm(node):
    """Stable, cosmetic-parenthesis-free predicate text."""
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _negated(node):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _norm(node.operand)
    # Normalize simple inverse comparisons so `not x`, `x is None`, and the
    # enabled side's inverse compare reliably without AST identity tricks.
    inverse = {
        ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
        ast.Lt: ast.GtE, ast.LtE: ast.Gt, ast.Gt: ast.LtE, ast.GtE: ast.Lt,
    }
    if isinstance(node, ast.Compare) and len(node.ops) == 1 \
            and type(node.ops[0]) in inverse:
        copy = ast.Compare(left=node.left, ops=[inverse[type(node.ops[0])]()],
                           comparators=node.comparators)
        return _norm(copy)
    return _norm(ast.UnaryOp(op=ast.Not(), operand=node))


def _literal_menu_data(menu):
    """Return callback names and each callback's disabled predicates."""
    callbacks, disabled = set(), {}
    for node in ast.walk(menu):
        if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) < 2:
            continue
        cb = node.elts[1]
        name = _callback_name(cb)
        if name:
            callbacks.add(name)
            continue
        if isinstance(cb, ast.IfExp):
            # Common form: self._foo if enabled else None.  Also accept the
            # symmetric form, whose disabled condition is the test itself.
            if _is_none(cb.orelse):
                name = _callback_name(cb.body)
                predicate = _negated(cb.test)
            elif _is_none(cb.body):
                name = _callback_name(cb.orelse)
                predicate = _norm(cb.test)
            else:
                continue
            if name:
                callbacks.add(name)
                disabled.setdefault(name, set()).add(predicate)
    return callbacks, disabled


def _silent_return(node):
    return (node.value is None or
            isinstance(node.value, ast.Constant) and node.value.value in
            (None, False))


def _has_feedback(node):
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            name = _dotted(item.func).lower()
            if any(word in name for word in FEEDBACK_WORDS):
                return True
        # `self.status = ...` and `self.*_chip = ...` are also visible updates.
        if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            if any(any(word in _dotted(t).lower() for word in ("status", "chip"))
                   for t in targets):
                return True
    return False


def _scan_block(statements, guards, feedback, source, out):
    """Flow-sensitive enough for early guard clauses, without guessing calls."""
    seen_feedback = feedback
    for stmt in statements:
        if isinstance(stmt, ast.If):
            test_text = ast.get_source_segment(source, stmt.test) or ast.unparse(stmt.test)
            _scan_block(stmt.body, guards + [(stmt.test, test_text)],
                        seen_feedback, source, out)
            _scan_block(stmt.orelse, guards + [(ast.UnaryOp(op=ast.Not(), operand=stmt.test),
                                                "not (" + test_text + ")")],
                        seen_feedback, source, out)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With,
                               ast.AsyncWith, ast.Try)):
            # Returns inside these constructs still inherit an enclosing guard.
            for field in ("body", "orelse", "finalbody"):
                _scan_block(getattr(stmt, field, []), guards, seen_feedback,
                            source, out)
            for handler in getattr(stmt, "handlers", []):
                _scan_block(handler.body, guards, seen_feedback, source, out)
        elif isinstance(stmt, ast.Return) and guards and _silent_return(stmt):
            # A confirmation/prompt can live in the guard expression itself:
            # `if not self._confirm(...): return` is user-visible cancellation.
            if not seen_feedback and not any(_has_feedback(g) for g, _ in guards):
                guard, text = guards[-1]
                out.append((stmt, guard, text))
        if _has_feedback(stmt):
            seen_feedback = True


def analyze_file(path):
    source = open(path, encoding="utf-8").read()
    tree = ast.parse(source, filename=path)
    basename = os.path.basename(path)
    findings, accounted = [], []
    callback_count = 0
    has_menu = False
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        methods = {n.name: n for n in cls.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        menu = methods.get("menu_items")
        if menu is None:
            continue
        has_menu = True
        callbacks, disabled = _literal_menu_data(menu)
        callback_count += len(callbacks)
        for name in sorted(callbacks):
            method = methods.get(name)
            if method is None:
                continue  # inherited/shared callback: deliberately out of scope
            exits = []
            _scan_block(method.body, [], False, source, exits)
            for ret, guard, text in exits:
                item = Finding(basename, name, ret.lineno, " ".join(text.split()))
                if _norm(guard) in disabled.get(name, set()):
                    accounted.append(item)
                else:
                    findings.append(item)
    return callback_count, findings, accounted, has_menu


def app_files(module_dir=DE):
    return sorted(os.path.join(module_dir, name) for name in os.listdir(module_dir)
                  if name.endswith(".py") and name != "nbapp.py")


def run_gate(module_dir=DE, only=None, use_ledger=True):
    paths = app_files(module_dir)
    if only:
        paths = [p for p in paths if os.path.basename(p) == only + ".py"]
    callbacks = modules = silent = accounted_count = 0
    findings = []
    for path in paths:
        count, real, accounted, has_menu = analyze_file(path)
        if not has_menu:
            continue
        modules += 1
        callbacks += count
        silent += len(real) + len(accounted)
        accounted_count += len(accounted)
        findings.extend(real)

    actual_keys = {(f.file, f.method) for f in findings}
    ledger = set(DEBT) if use_ledger and module_dir == DE and not only else set()
    problems = 0
    for f in findings:
        if (f.file, f.method) not in ledger:
            problems += 1
            print(f"{f.file}:{f.method}:{f.line}: {f.condition}")
    for key in sorted(ledger - actual_keys):
        problems += 1
        print(f"LEDGER STALE  {key[0]}:{key[1]} — {DEBT[key]}")
    print(f"{callbacks} menu callbacks parsed across {modules} app modules; "
          f"{silent} silent returns; {accounted_count} accounted for by menu gating; "
          f"{len(findings)} findings before DEBT; "
          f"{len(actual_keys & ledger)} ledger keys matched")
    print("RESULT: " + ("PASS" if not problems else
                        f"FAILED: {problems} problem(s)"))
    return 1 if problems else 0


def selfcheck():
    """Insert one ungated silent guard in a copied real menu callback."""
    scratch = tempfile.mkdtemp(prefix="nb-silent-refusal-selfcheck-")
    try:
        module = "calculator"
        source = os.path.join(DE, module + ".py")
        target = os.path.join(scratch, module + ".py")
        shutil.copy2(source, target)
        text = open(target, encoding="utf-8").read()
        tree = ast.parse(text)
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_copy_result")
        insert = method.body[0].lineno
        lines = text.splitlines(keepends=True)
        indent = " " * (method.col_offset + 4)
        sabotage = (indent + "# silent_refusal_check selfcheck sabotage\n" +
                    indent + "if self is not None:\n" +
                    indent + "    return None\n")
        lines.insert(insert - 1, sabotage)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("".join(lines))
        print("SELFCHECK sabotage: calculator.py:_copy_result now returns None "
              "under ungated guard `self is not None`")
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            rc = run_gate(scratch, only=module, use_ledger=False)
        output = capture.getvalue()
        print(output, end="")
        # The ordinary gate must be red and name the exact sabotaged callback.
        caught = rc != 0 and "calculator.py:_copy_result:" in output
        print("SELFCHECK: " + ("PASS — sabotaged callback was reported" if caught
                                else "FAIL — sabotage did not make the gate red"))
        return 0 if caught else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv):
    if "--selfcheck" in argv:
        return selfcheck()
    module_dir = DE
    only = None
    if "--module-dir" in argv:
        module_dir = os.path.abspath(argv[argv.index("--module-dir") + 1])
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    return run_gate(module_dir, only)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
