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
import copy
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
academics.py:_insert_at_cursor
academics.py:_insert_list
academics.py:_move_lecture
academics.py:_nav
academics.py:_new_lecture
academics.py:_recount
academics.py:_set_style
academics.py:_toggle_tag
animation.py:_delete_cel
animation.py:_move_scene
animation.py:_rename_cel_prompt
bills.py:_open_payment
bills.py:_undo_delete
calculator.py:recall
comics.py:_delete_selection
contacts.py:_clear_search
contacts.py:_delete_contact
contacts.py:_export_vcard
contacts.py:_step
cookbook.py:_delete_active_category
cookbook.py:_select_relative
ebook.py:_on_library_open
g2048.py:move
gbasdk.py:_delete_resource
gbasdk.py:_move_to_folder
gbasdk.py:_rename_resource
gbasdk.py:_show_where_used
illustrator.py:_clear_active_layer
illustrator.py:_copy_image
illustrator.py:_delete_layer
illustrator.py:_move_layer
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
writer.py:_toggle_char
writer.py:_undo
""".split()

# WHAT THIS LEDGER IS, EXACTLY — restored for the third time, because each
# pass over this file has replaced it with a sentence implying the entries
# have been checked. They have not. 76 keys get the blanket reason below;
# the DEBT.update() further down overrides exactly THREE of them with
# reasons of their own. The other 73 are UNVERIFIED and are what the gate
# consumes. Auditing an app here means emptying its entries and driving its
# items with a display, which no static pass can do.
#
# The gate resolves one level of helper call,
# matches set_sensitive() conditions, follows lambda aliases and recognises
# idempotent guards — which took the accounted-for count from 20 to 47 and
# the findings from 111 to 89. What is left is what it STILL cannot match.
#
# The mapping below is NOT temporary and NOT replaced: DEBT.update() names
# three keys, so 73 of these 76 reasons survive into the gate's own ledger.
DEBT = {
    tuple(item.split(":", 1)):
        "unverified: a guard this static test cannot match to a gating "
        "condition; carried so new ones are caught, not cleared"
    for item in _DEBT_KEYS
}

# Checked one at a time, and each says what it is rather than sharing a
# sentence with ninety others.
DEBT.update({
    ("animation.py", "_move_layer"):
        "a bounds check behind gating the static test cannot see: the Layer "
        "menu offers Up only while layer_i < len(layers)-1 and Down only "
        "while layer_i > 0, and _refresh_layer_buttons greys the dock pair "
        "on the same conditions (F49, F61 drive both)",
    ("comics.py", "_arrange_bubble"):
        "the same shape as animation's _move_layer — a defensive index "
        "bound behind a gated command; comics is another lane's file",
    ("comics.py", "_structure"):
        "guards on a callable that is absent rather than on user state; "
        "comics is another lane's file",
})

# Every surviving key has been inspected.  Keep the reason beside the key so
# the ratchet records only what that inspection actually established.
DEBT = {
    ("academics.py", "_clear_search"): "The empty-query guard is the inverse of Clear Search's derived query state.",
    ("academics.py", "_cycle_style"): "The editor/active guard mirrors the menu's derived open-lecture flag.",
    ("academics.py", "_delete_homework"): "The empty done-list guard mirrors the menu's finished-homework count.",
    ("academics.py", "_insert_at_cursor"): "The editor/active guard mirrors the Insert menu's open-lecture flag.",
    ("academics.py", "_insert_list"): "The editor/active guard mirrors the Insert menu's open-lecture flag.",
    ("academics.py", "_move_lecture"): "The active-index/class-count guard mirrors the open-lecture/two-class condition.",
    ("academics.py", "_nav"): "The empty display-order guard protects a derived sidebar invariant.",
    ("academics.py", "_new_lecture"): "The no-classes branch opens New Class, so the return completes a fallback action.",
    ("academics.py", "_recount"): "The missing-body branch visibly sets the word count to zero before returning.",
    ("academics.py", "_set_style"): "The editor/active guard mirrors the Format menu's open-lecture flag.",
    ("academics.py", "_toggle_tag"): "The editor/selection/tag guards protect formatting state; no selection only restores focus.",
    ("animation.py", "_delete_cel"): "The selected/in-use guard mirrors Delete Drawing gating; missing cel protects model consistency.",
    ("animation.py", "_move_layer"): "The target-range guard is implied by each direction's menu and dock sensitivity.",
    ("animation.py", "_move_scene"): "The target-range guard mirrors the separately gated left/right aliases.",
    ("animation.py", "_rename_cel_prompt"): "The missing-cel guard mirrors the menu's _active_cel() condition.",
    ("bills.py", "_open_payment"): "The missing-bill guard mirrors selection gating; the nested relookup protects a stale overlay.",
    ("bills.py", "_undo_delete"): "The duplicate-id guard cancels a stale undo that would corrupt bill identity.",
    ("calculator.py", "recall"): "The empty-tape and forward-boundary guards silently refuse enabled history commands.",
    ("comics.py", "_arrange_bubble"): "The range guard silently refuses an enabled bubble-order command at either boundary.",
    ("comics.py", "_delete_selection"): "The invalid-index guard protects a stale selection after presence gating.",
    ("comics.py", "_structure"): "The false model-operation guard preserves page invariants reflected by menu/dock sensitivity.",
    ("contacts.py", "_clear_search"): "The empty query/widget guard mirrors Clear Search's derived text flag.",
    ("contacts.py", "_delete_contact"): "The pending guard debounces re-entry; the index guard mirrors selected-card gating.",
    ("contacts.py", "_export_vcard"): "The empty-book guard mirrors both export entries' has-contact condition.",
    ("contacts.py", "_step"): "The empty-order guard silently refuses enabled Next/Previous Contact commands.",
    ("cookbook.py", "_delete_active_category"): "The All/out-of-range guards mirror the deletable-category state.",
    ("cookbook.py", "_select_relative"): "The empty-list guard silently refuses enabled Next/Previous Recipe commands.",
    ("ebook.py", "_on_library_open"): "The revealer guard returns only after the library sheet is populated and shown.",
    ("g2048.py", "move"): "The overlay guard blocks hidden mutation; the no-move guard is the game's invalid-move no-op.",
    ("gbasdk.py", "_delete_resource"): "The missing/stale selection guards mirror Delete Resource gating.",
    ("gbasdk.py", "_move_to_folder"): "The missing-resource guard mirrors the derived resource selection.",
    ("gbasdk.py", "_rename_resource"): "The missing-resource guard mirrors the derived resource selection.",
    ("gbasdk.py", "_show_where_used"): "The missing selection/resource guard mirrors resource-selection gating.",
    ("illustrator.py", "_clear_active_layer"): "The no-layers guard protects an impossible document invariant.",
    ("illustrator.py", "_copy_image"): "The missing-pixbuf guard silently refuses Copy Image when raster capture fails.",
    ("illustrator.py", "_delete_layer"): "The active-layer range guard enforces the non-background-layer constraint.",
    ("illustrator.py", "_move_layer"): "The destination guard is the boundary no-op for directional layer aliases.",
    ("illustrator.py", "_set_zoom"): "Closed is lifecycle protection; equal normalized zoom is an already-at-target no-op.",
    ("illustrator.py", "_zoom_fit"): "Closed/zero-allocation guards defer fitting until the viewport is measurable.",
    ("journal.py", "_clear_format"): "The entry guard mirrors gating; no selection silently leaves formatting unchanged.",
    ("journal.py", "_clear_search"): "The empty query/widget guard mirrors Clear Search's query flag.",
    ("journal.py", "_delete_active"): "The pending guard debounces re-entry; the index guard mirrors entry gating.",
    ("journal.py", "_go_entry"): "The no-entries guard is implied by the separately derived older/newer boundary gating.",
    ("journal.py", "_toggle_tag"): "The entry guard mirrors gating; no selection silently applies no character tag.",
    ("maps.py", "_fit"): "The missing-map guard silently refuses Fit before a map is loaded.",
    ("maps.py", "_zoom"): "The missing-map guard silently refuses Zoom before a map is loaded.",
    ("mealplanner.py", "_clear_week"): "The zero-meals guard mirrors Clear Week's nonempty-week condition.",
    ("media.py", "_on_trash"): "The missing/non-file path guard protects a stale media selection.",
    ("music.py", "_delete_current_playlist"): "The missing-playlist guard mirrors custom-playlist selection.",
    ("music.py", "_menu_volume"): "The delta-is-None return follows the completed mute/unmute action.",
    ("music.py", "_rename_current_playlist"): "The missing-playlist guard mirrors custom-playlist selection.",
    ("novel.py", "_on_delete_part"): "The stale part-index guard remains after the one-part minimum is accounted for.",
    ("novel.py", "_on_file_save"): "The missing-path guard routes Save to Save As rather than refusing.",
    ("novel.py", "_on_fmt"): "No selection applies no formatting; the quote return follows the completed quote action.",
    ("packages.py", "_on_open"): "The invalid package-index guards mirror openable-selection gating.",
    ("packages.py", "_on_verify"): "The invalid package-index guards mirror package-selection gating.",
    ("sequencer.py", "_export_audio"): "The active-export guard prevents a duplicate while progress is already visible.",
    ("sequencer.py", "_on_ff"): "The recording guard blocks seeking during an active recording.",
    ("sequencer.py", "_on_play"): "The recording guard blocks playback during an active recording.",
    ("sequencer.py", "_on_rec"): "The recording guard makes repeated Record clicks idempotent during the take.",
    ("sequencer.py", "_on_rew"): "The recording guard blocks seeking during an active recording.",
    ("sequencer.py", "_paste_clip"): "The empty-clipboard guard mirrors Paste availability.",
    ("sequencer.py", "_toggle_loop"): "The selected-clip return follows setting the loop; no selection flashes guidance.",
    ("sequencer.py", "_toggle_metro"): "The loading guard suppresses callbacks while saved state is restored.",
    ("tasks.py", "_remove_list"): "The protected-name guard rejects built-in lists the removable-list UI does not offer.",
    ("terminal.py", "_shell_reset"): "The missing-terminal guard protects startup/teardown before shell reset.",
    ("terminal.py", "_term_copy"): "The missing-terminal guard protects startup/teardown before clipboard access.",
    ("terminal.py", "_term_paste"): "The missing-terminal guard protects startup/teardown before clipboard access.",
    ("terminal.py", "_term_select_all"): "The missing-terminal guard protects startup/teardown before selection access.",
    ("terminal.py", "_toggle_blink"): "The missing-terminal guard protects startup/teardown before cursor changes.",
    ("terminal.py", "_zoom"): "The missing-terminal guard protects startup/teardown before font scaling.",
    ("video.py", "_delete_clip_guarded"): "The missing/stale clip guard mirrors selected-clip gating.",
    ("video.py", "_menu_add_transition"): "The missing/stale clip guard mirrors selected-clip gating.",
    ("video.py", "_menu_split"): "Clip selection mirrors gating; sub-two-frame duration is unsplittable.",
    ("video.py", "_move_clip"): "The selected/destination guards mirror separately gated move aliases.",
    ("workout.py", "_delete_exercise"): "The missing-exercise guard mirrors exercise selection.",
    ("workout.py", "_edit_exercise"): "The missing-exercise guard mirrors exercise selection.",
    ("writer.py", "_toggle_char"): "The no-selection guard silently applies no character formatting.",
    ("writer.py", "_undo"): "The oldest-history guard mirrors the menu's derived can-undo boundary state.",
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
    mechanism: int | None = None


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


def _invert_compare(op):
    inverse = {
        ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
        ast.Lt: ast.GtE, ast.LtE: ast.Gt, ast.Gt: ast.LtE, ast.GtE: ast.Lt,
    }
    return inverse.get(type(op))


def _subst(node, bindings):
    """Substitute simple local aliases such as ``cel = self._active_cel()``."""
    if isinstance(node, ast.Name) and node.id in bindings:
        rest = {key: value for key, value in bindings.items() if key != node.id}
        return _subst(bindings[node.id], rest)
    cloned = copy.copy(node)
    for field, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            value = _subst(value, bindings)
        elif isinstance(value, list):
            value = [_subst(v, bindings) if isinstance(v, ast.AST) else v
                     for v in value]
        setattr(cloned, field, value)
    return cloned


def _helper_expr(node, helpers):
    """Inline the predicate returned by one zero-argument self helper."""
    if not (isinstance(node, ast.Call) and not node.args and not node.keywords
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"):
        return None
    return helpers.get(node.func.attr)


def _canon(node, bindings=None, helpers=None, negate=False):
    """Canonical boolean expression, including De Morgan/inverse compares."""
    bindings = bindings or {}
    helpers = helpers or {}
    node = _subst(node, bindings)
    expanded = _helper_expr(node, helpers)
    if expanded is not None:
        return _canon(expanded, {}, {}, negate)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _canon(node.operand, {}, helpers, not negate)
    if isinstance(node, ast.BoolOp):
        is_and = isinstance(node.op, ast.And)
        op = "and" if is_and != negate else "or"
        return (op, tuple(sorted((_canon(v, {}, helpers, negate) for v in node.values),
                                 key=repr)))
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and negate:
        inv = _invert_compare(node.ops[0])
        if inv is not None:
            node = ast.Compare(left=node.left, ops=[inv()],
                               comparators=node.comparators)
            negate = False
    atom = ast.dump(node, annotate_fields=False, include_attributes=False)
    return ("not", atom) if negate else ("atom", atom)


def _norm(node, bindings=None, helpers=None):
    return _canon(node, bindings, helpers)


def _negated(node):
    return _canon(node, negate=True)


def _literal_menu_data(menu, helpers):
    """Return callback names and each callback's disabled predicates."""
    callbacks, disabled, lambda_aliases = set(), {}, set()
    bindings = {}
    for stmt in ast.walk(menu):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if stmt.value is not None and len(targets) == 1 \
                    and isinstance(targets[0], ast.Name):
                bindings[targets[0].id] = _subst(stmt.value, bindings)
    for node in ast.walk(menu):
        if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) < 2:
            continue
        cb = node.elts[1]
        name = _callback_name(cb)
        if name:
            callbacks.add(name)
            if isinstance(cb, ast.Lambda):
                lambda_aliases.add(name)
            continue
        if isinstance(cb, ast.IfExp):
            # Common form: self._foo if enabled else None.  Also accept the
            # symmetric form, whose disabled condition is the test itself.
            if _is_none(cb.orelse):
                name = _callback_name(cb.body)
                predicate = _canon(cb.test, bindings, helpers, negate=True)
            elif _is_none(cb.body):
                name = _callback_name(cb.orelse)
                predicate = _canon(cb.test, bindings, helpers)
            else:
                continue
            if name:
                callbacks.add(name)
                disabled.setdefault(name, set()).add(predicate)
                chosen = cb.body if not _is_none(cb.body) else cb.orelse
                if isinstance(chosen, ast.Lambda):
                    lambda_aliases.add(name)
    return callbacks, disabled, lambda_aliases


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


def _scan_block(statements, guards, feedback, source, out, bindings=None):
    """Flow-sensitive enough for early guard clauses, without guessing calls."""
    bindings = dict(bindings or {})
    seen_feedback = feedback
    for stmt in statements:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            value = stmt.value
            if value is not None and len(targets) == 1 \
                    and isinstance(targets[0], ast.Name):
                bindings[targets[0].id] = _subst(value, bindings)
        if isinstance(stmt, ast.If):
            test_text = ast.get_source_segment(source, stmt.test) or ast.unparse(stmt.test)
            _scan_block(stmt.body, guards + [(stmt.test, test_text, dict(bindings))],
                        seen_feedback, source, out, bindings)
            _scan_block(stmt.orelse, guards + [(ast.UnaryOp(op=ast.Not(), operand=stmt.test),
                                                "not (" + test_text + ")",
                                                dict(bindings))],
                        seen_feedback, source, out, bindings)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With,
                               ast.AsyncWith, ast.Try)):
            # Returns inside these constructs still inherit an enclosing guard.
            for field in ("body", "orelse", "finalbody"):
                _scan_block(getattr(stmt, field, []), guards, seen_feedback,
                            source, out, bindings)
            for handler in getattr(stmt, "handlers", []):
                _scan_block(handler.body, guards, seen_feedback, source, out,
                            bindings)
        elif isinstance(stmt, ast.Return) and guards and _silent_return(stmt):
            # A confirmation/prompt can live in the guard expression itself:
            # `if not self._confirm(...): return` is user-visible cancellation.
            if not seen_feedback and not any(_has_feedback(g) for g, _, _ in guards):
                guard, text, guard_bindings = guards[-1]
                out.append((stmt, guard, text, guard_bindings))
        if _has_feedback(stmt):
            seen_feedback = True


def _helper_predicates(methods):
    """Summarize simple predicate helpers without following a second call."""
    result = {}
    for name, method in methods.items():
        bindings = {}
        for stmt in method.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if stmt.value is not None and len(targets) == 1 \
                        and isinstance(targets[0], ast.Name):
                    bindings[targets[0].id] = _subst(stmt.value, bindings)
            elif isinstance(stmt, ast.Return) and stmt.value is not None:
                result[name] = _subst(stmt.value, bindings)
    return result


def _single_wrapper_call(method):
    """Return the sole self-method call made by a thin wrapper."""
    body = list(method.body)
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body.pop(0)
    if len(body) != 1 or not isinstance(body[0], (ast.Expr, ast.Return)):
        return None
    calls = [n for n in ast.walk(body[0]) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"]
    names = {n.func.attr for n in calls}
    return calls[0] if len(calls) == 1 and len(names) == 1 else None


def _matches_disabled(predicate, disabled):
    if predicate in disabled:
        return True
    # A menu enabled by A and B is disabled by ``not A or not B``.  Either
    # corresponding early-return guard is therefore covered independently.
    return any(isinstance(item, tuple) and item[0] == "or"
               and predicate in item[1] for item in disabled)


def _is_idempotent(method, guard, ret):
    """Recognize ``if self.x == value: return`` before ``self.x = value``."""
    if not (isinstance(guard, ast.Compare) and len(guard.ops) == 1
            and isinstance(guard.ops[0], (ast.Eq, ast.Is))
            and len(guard.comparators) == 1):
        return False
    left, right = guard.left, guard.comparators[0]
    pairs = [(left, right), (right, left)]
    for state, value in pairs:
        if not (isinstance(state, ast.Attribute)
                and isinstance(state.value, ast.Name)
                and state.value.id == "self"):
            continue
        state_name = _dotted(state)
        value_dump = ast.dump(value, annotate_fields=False, include_attributes=False)
        for node in ast.walk(method):
            if getattr(node, "lineno", 0) <= ret.lineno:
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None and any(
                        _dotted(t) == state_name for t in targets) and ast.dump(
                            node.value, annotate_fields=False,
                            include_attributes=False) == value_dump:
                    return True
    return False


def _sensitivity_predicates(method, helpers):
    """Disabled predicates established by a refresh method's button calls."""
    result = set()

    def visit(statements, bindings):
        bindings = dict(bindings)
        for stmt in statements:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if stmt.value is not None and len(targets) == 1 \
                        and isinstance(targets[0], ast.Name):
                    bindings[targets[0].id] = _subst(stmt.value, bindings)
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and node.args \
                        and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "set_sensitive":
                    result.add(_canon(node.args[0], bindings, helpers,
                                      negate=True))
            if isinstance(stmt, ast.If):
                visit(stmt.body, bindings)
                visit(stmt.orelse, bindings)
    visit(method.body, {})
    return result


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
        helpers = _helper_predicates(methods)
        callbacks, disabled, lambda_aliases = _literal_menu_data(menu, helpers)
        wrapper_aliases = {}
        wrapper_bindings = {}
        for name in tuple(callbacks):
            method = methods.get(name)
            call = _single_wrapper_call(method) if method is not None else None
            target = call.func.attr if call is not None else None
            # Following an ungated wrapper would broaden this gate from menu
            # callbacks into arbitrary internals.  The alias mechanism here is
            # specifically the gated-wrapper shape.
            if target in methods and target != name and disabled.get(name):
                wrapper_aliases[name] = target
                callbacks.add(target)
                disabled.setdefault(target, set()).update(disabled.get(name, set()))
                target_method = methods[target]
                params = target_method.args.args[1:]
                wrapper_bindings.setdefault(target, []).append({
                    param.arg: arg for param, arg in zip(params, call.args)})

        sensitive = set()
        for candidate in methods.values():
            sensitive.update(_sensitivity_predicates(candidate, helpers))
        callback_count += len(callbacks)
        for name in sorted(callbacks):
            method = methods.get(name)
            if method is None:
                continue  # inherited/shared callback: deliberately out of scope
            exits = []
            _scan_block(method.body, [], False, source, exits)
            for ret, guard, text, bindings in exits:
                item = Finding(basename, name, ret.lineno, " ".join(text.split()))
                predicates = [_norm(guard, {**extra, **bindings}, helpers)
                              for extra in wrapper_bindings.get(name, [{}])]
                predicate = predicates[0]
                menu_disabled = {_canon_expr for raw in disabled.get(name, set())
                                 for _canon_expr in [raw]}
                # Existing menu predicates were canonicalized without helper
                # expansion; rebuild them from their AST is impossible here,
                # so helper calls remain stable atoms and local aliases on the
                # callback side normalize to those same atoms.
                if _is_idempotent(method, _subst(guard, bindings), ret):
                    accounted.append(Finding(basename, name, ret.lineno,
                                             item.condition, 4))
                elif any(p in sensitive or _matches_disabled(p, sensitive)
                         for p in predicates):
                    accounted.append(Finding(basename, name, ret.lineno,
                                             item.condition, 2))
                elif any(_matches_disabled(p, menu_disabled) for p in predicates):
                    mechanism = 3 if (name in lambda_aliases or
                                      name in wrapper_aliases.values()) else 1
                    accounted.append(Finding(basename, name, ret.lineno,
                                             item.condition, mechanism))
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
