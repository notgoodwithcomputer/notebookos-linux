#!/usr/bin/env python3
"""Bottom-up census of user-visible state changes which may still hard-cut.

This is deliberately a report, not a gate.  It uses conservative AST heuristics,
prints its uncertainty, and always exits zero.  The inventory is the naming
authority; this script asks whether actual code sites have a plausible name.

Run from anywhere with:

    python3 tools/state_change_census.py

Use --top N to change the individual-site limit (default: 30).
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import traceback
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parent.parent
DE = (ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
INVENTORY = ROOT / "tools/motion_inventory.json"

# Infrastructure and non-surface processes.  They either implement motion/UI
# primitives, provide data/services, or draw no application surface themselves.
SKIPPED = {
    "desktopbg.py": "static desktop background renderer",
    "nbaudio.py": "audio service/helper, no application surface",
    "nbcommands.py": "command definitions, no application surface",
    "nbdiacritics.py": "input-method helper",
    "nbicons.py": "icon construction helper",
    "nbi18n.py": "translation infrastructure",
    "nbjobs.py": "background-job infrastructure",
    "nbkeyboard.py": "keyboard helper",
    "nbmediakeys.py": "media-key daemon",
    "nbmotion.py": "motion primitive implementation",
    "nbpinyin.py": "input-method helper",
    "nbstate.py": "persistence infrastructure",
    "nbsynth.py": "audio synthesis backend",
    "nbtransitions.py": "transition primitive implementation",
    "nbvideo.py": "video backend/helper",
    "xclipd.py": "clipboard daemon",
    "xflush.py": "display utility, no application surface",
    "xflushd.py": "display daemon",
    "xnudge.py": "window utility",
    "xrootbg.py": "root-window renderer",
    "xshape.py": "window-shape utility",
    "xtabletd.py": "tablet daemon",
}

VISIBLE_CALLS = {"show", "hide", "set_visible"}
STACK_CALLS = {"set_visible_child", "set_visible_child_name"}
VALUE_CALLS = {"set_text", "set_label", "set_markup"}
PACK_CALLS = {"add", "pack_start", "pack_end", "attach", "insert"}
BUILD_PREFIXES = ("_build", "build_", "_make", "make_", "_create", "create_",
                  "_setup", "setup_", "_init")
FREQUENT_WORDS = ("tick", "poll", "refresh", "update", "sync", "changed",
                  "toggle", "click", "press", "key", "playback", "runner")
RARE_WORDS = ("error", "fail", "exception", "dialog", "export", "import")


@dataclass
class Finding:
    module: str
    line: int
    function: str
    kind: str
    target: str
    score: int
    confidence: str
    inventory: tuple[str, ...]
    sight: str

    @property
    def unnamed(self) -> bool:
        return not self.inventory


_SOURCE_LINES: dict[int, list[str]] = {}


def source_of(src: str, node: ast.AST) -> str:
    try:
        if not src or not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
            return "?"
        lines = _SOURCE_LINES.setdefault(id(src), src.splitlines(keepends=True))
        first = node.lineno - 1
        last = node.end_lineno - 1
        if first == last:
            data = lines[first].encode("utf-8")
            return data[node.col_offset:node.end_col_offset].decode("utf-8", "replace")
        chunks = [lines[first].encode("utf-8")[node.col_offset:].decode("utf-8", "replace")]
        chunks.extend(lines[first + 1:last])
        chunks.append(lines[last].encode("utf-8")[:node.end_col_offset].decode("utf-8", "replace"))
        return "".join(chunks)
    except (IndexError, TypeError, AttributeError):
        # CPython's helper can fail on a malformed/transiently-edited span.
        return "?"


def receiver(src: str, call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return source_of(src, call.func.value)
    return "?"


def enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> Optional[ast.AST]:
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return cur
    return None


def enclosing_functions(node: ast.AST,
                        parents: dict[ast.AST, ast.AST]) -> Iterable[ast.AST]:
    """Yield nested callback and outer method scopes, nearest first."""
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield cur


def function_name(fn: Optional[ast.AST]) -> str:
    return getattr(fn, "name", "<lambda>")


def construction_function(name: str) -> bool:
    return name == "__init__" or any(name.startswith(p) for p in BUILD_PREFIXES)


def is_zero(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in (0, 0.0)


def policy_test(node: ast.AST) -> bool:
    """Recognise policy(...) == 0 and equivalent still/false tests."""
    if isinstance(node, ast.Compare):
        parts = [node.left, *node.comparators]
        return (any(is_zero(x) for x in parts) and
                any(isinstance(x, ast.Call) and
                    ((isinstance(x.func, ast.Attribute) and x.func.attr == "policy") or
                     (isinstance(x.func, ast.Name) and x.func.id == "policy"))
                    for x in parts))
    text = ast.dump(node, include_attributes=False)
    return "policy" in text and ("Constant(value=0)" in text or
                                  "UnaryOp(op=Not" in text)


def in_reduced_motion_branch(node: ast.AST,
                             parents: dict[ast.AST, ast.AST]) -> bool:
    """Skip only the instant branch, not the animated sibling branch."""
    child = node
    while child in parents:
        par = parents[child]
        if isinstance(par, ast.If) and policy_test(par.test):
            if child in par.body:
                # policy == 0 body; for policy truthiness, the body is motion-on.
                return "Compare" in ast.dump(par.test, include_attributes=False)
            if child in par.orelse:
                return "Compare" not in ast.dump(par.test, include_attributes=False)
        child = par
    return False


def has_motion_for_target(src: str, fn: ast.AST, target: str) -> bool:
    """Conservative same-function routing test for this receiver."""
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        callee = source_of(src, n.func)
        if not (callee.startswith("nbmotion.") or
                callee.startswith("nbtransitions.")):
            continue
        if any(target == source_of(src, a) or target in source_of(src, a)
               for a in n.args):
            return True
    return False


def motion_machinery(target: str, fn_scopes: Iterable[ast.AST],
                     motion_scopes: set[int]) -> bool:
    """Recognise animation-only drawing surfaces and their lifecycle calls.

    Animation callbacks are often nested functions: cleanup in ``_done`` is
    still machinery owned by the outer method that starts nbmotion.  Explicitly
    named animation/scrim surfaces are machinery even when their controller is
    a wrapper (for example a Scalar) rather than a direct function call.
    """
    low = target.lower()
    named_surface = any(word in low for word in
                        ("anim", "motion", "transition", "scrim"))
    return named_surface or any(id(scope) in motion_scopes for scope in fn_scopes)


def dynamic_value(call: ast.Call) -> bool:
    """Reject literal labelling; retain runtime-derived or state-message values."""
    if not call.args:
        return False
    arg = call.args[0]
    if isinstance(arg, ast.Constant):
        value = str(arg.value)
        # Status changes are real state changes even when each state is literal.
        return any(w in value.lower() for w in
                   ("saved", "saving", "selected", "remaining", "failed", "done"))
    # _t("literal") is static labelling; formatted translations are dynamic.
    if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and
            arg.func.id in {"_t", "str"} and len(arg.args) == 1 and
            isinstance(arg.args[0], ast.Constant)):
        return False
    return True


def inventory_for(kind: str, module: str, target: str, fn: str,
                  inventory_ids: set[str]) -> tuple[str, ...]:
    low = (target + " " + fn).lower()
    if kind == "stack-switch":
        return ("app.page-pane-switch",)
    if kind == "value-replace":
        return ("app.any-value-change",)
    if kind == "sensitivity-jump":
        return ("app.toolbar-state",)
    if kind == "container-rebuild":
        # Removal plus insertion is named, even though no generic primitive can
        # make a wholesale rebuild coherent automatically.
        return ("app.list-remove", "app.list-insert")
    if kind == "visibility-cut":
        if any(w in low for w in ("empty", "placeholder")):
            return ("app.empty-populated",)
        if any(w in low for w in ("row", "list", "item")):
            return ("app.list-insert", "app.list-remove")
        return ()
    if kind in {"redraw-cut", "opacity-jump"}:
        content_id = "content." + module.removesuffix(".py")
        if content_id in inventory_ids:
            return (content_id,)
    return ()


def rank(kind: str, fn: str, target: str, confidence: str) -> int:
    # 10-point base: surface size first, then likely encounter frequency and
    # perceptual salience.  Rare/error paths lose two; low-confidence sites one.
    base = {"container-rebuild": 9, "stack-switch": 8, "visibility-cut": 7,
            "opacity-jump": 7, "value-replace": 6, "sensitivity-jump": 5,
            "redraw-cut": 5}[kind]
    low = (fn + " " + target).lower()
    if any(w in low for w in FREQUENT_WORDS):
        base += 2
    if any(w in low for w in RARE_WORDS):
        base -= 2
    if confidence == "medium":
        base -= 1
    return max(1, min(10, base))


def sight(kind: str, target: str, fn: str) -> str:
    if kind == "container-rebuild":
        return f"the visible children of {target} are removed and repacked at once"
    if kind == "stack-switch":
        return f"{target} cuts directly to another page (also tracked by the Stack gate)"
    if kind == "visibility-cut":
        return f"{target} appears or disappears immediately"
    if kind == "opacity-jump":
        return f"{target} jumps to a new opacity"
    if kind == "value-replace":
        return f"the displayed value in {target} is replaced immediately"
    if kind == "sensitivity-jump":
        return f"{target} snaps between enabled and disabled"
    return f"{target} redraws changed content without an interpolated state"


def calls_in(fn: ast.AST) -> Iterable[ast.Call]:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            yield n


def census_file(path: Path, inventory_ids: set[str]) -> list[Finding]:
    src = path.read_text(encoding="utf-8", errors="replace")
    _SOURCE_LINES.clear()
    tree = ast.parse(src, str(path))
    parents: dict[ast.AST, ast.AST] = {}
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n):
            parents[child] = n
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    # Cache function-level facts once.  Some creative apps have multi-thousand
    # line classes; walking a function afresh for each setter is quadratic.
    fn_motion_targets: dict[int, set[str]] = {}
    fn_has_stack_primitive: dict[int, bool] = {}
    motion_scopes: set[int] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        owner = enclosing_function(n, parents)
        if owner is None:
            continue
        callee = source_of(src, n.func)
        fn_motion_targets.setdefault(id(owner), set())
        fn_has_stack_primitive.setdefault(id(owner), False)
        if callee.endswith("PageSwitcher") or callee.endswith("switch_page"):
            fn_has_stack_primitive[id(owner)] = True
        if callee.startswith("nbmotion.") or callee.startswith("nbtransitions."):
            motion_scopes.add(id(owner))
            fn_motion_targets[id(owner)].update(source_of(src, a) for a in n.args)

    # Detect the specific destructive loop shape, requiring later repacking of
    # the same syntactic receiver.  This catches finder._fill_sidebar without
    # treating every get_children() traversal as a rebuild.
    for fn in (n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if construction_function(fn.name):
            continue
        for loop in (n for n in ast.walk(fn) if isinstance(n, ast.For)):
            if not (isinstance(loop.iter, ast.Call) and
                    isinstance(loop.iter.func, ast.Attribute) and
                    loop.iter.func.attr == "get_children"):
                continue
            box = source_of(src, loop.iter.func.value)
            removes = [c for c in calls_in(loop) if c.func.attr == "remove" and
                       receiver(src, c) == box]
            packs = [c for c in calls_in(fn) if c.func.attr in PACK_CALLS and
                     receiver(src, c) == box and c.lineno > loop.lineno]
            if not removes or not packs:
                continue
            line = removes[0].lineno
            inv = inventory_for("container-rebuild", path.name, box, fn.name,
                                inventory_ids)
            findings.append(Finding(path.name, line, fn.name,
                                    "container-rebuild", box,
                                    rank("container-rebuild", fn.name, box, "high"),
                                    "high", inv,
                                    sight("container-rebuild", box, fn.name)))
            seen.add((line, "container-rebuild"))

    for call in (n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
        method = call.func.attr
        if method not in (VISIBLE_CALLS | STACK_CALLS | VALUE_CALLS |
                          {"set_opacity", "set_sensitive", "queue_draw"}):
            continue
        fn_node = enclosing_function(call, parents)
        fn = function_name(fn_node)
        if fn_node is None or construction_function(fn):
            continue
        if in_reduced_motion_branch(call, parents):
            continue
        target = receiver(src, call)
        if (method in VISIBLE_CALLS and
                motion_machinery(target, enclosing_functions(call, parents),
                                 motion_scopes)):
            continue
        if any(target == arg or target in arg
               for arg in fn_motion_targets.get(id(fn_node), ())):
            continue
        if method in VISIBLE_CALLS:
            kind, confidence = "visibility-cut", "high"
        elif method in STACK_CALLS:
            # Do not duplicate page_switch_consistency_check's module-level
            # adopter/debt verdict; expose concrete direct-call sites instead.
            if fn_has_stack_primitive.get(id(fn_node), False):
                continue
            kind, confidence = "stack-switch", "high"
        elif method in VALUE_CALLS:
            if not dynamic_value(call):
                continue
            # Clipboard/Pango/TextBuffer setters are not displayed Label values.
            low = target.lower()
            if any(w in low for w in ("clipboard", "layout", ".get_buffer()", "buf")):
                continue
            kind, confidence = "value-replace", "medium"
        elif method == "set_opacity":
            kind, confidence = "opacity-jump", "high"
        elif method == "set_sensitive":
            kind, confidence = "sensitivity-jump", "high"
        else:
            # queue_draw is intrinsically ambiguous.  Only retain callbacks and
            # refresh/update paths where application state is likely to change.
            if not any(w in fn.lower() for w in FREQUENT_WORDS):
                continue
            kind, confidence = "redraw-cut", "medium"
        key = (call.lineno, kind)
        if key in seen:
            continue
        inv = inventory_for(kind, path.name, target, fn, inventory_ids)
        findings.append(Finding(path.name, call.lineno, fn, kind, target,
                                rank(kind, fn, target, confidence), confidence,
                                inv, sight(kind, target, fn)))
        seen.add(key)
    # Precision pass.  A census is useful only if it is reviewable: collapse
    # mutually-exclusive branches which describe one transition, and retain
    # value changes only for names that strongly imply a displayed quantity or
    # status.  Local dialog labels are commonly construction-time form filling.
    value_hints = ("count", "total", "score", "time", "clock", "progress",
                   "percent", "stat", "balance", "remaining", "word", "save",
                   "mem", "cpu", "disk", "foot", "summary", "position", "pos",
                   "selected", "number", "num", "streak", "trace", "_tc")
    precise: dict[tuple[str, str, str], Finding] = {}
    for f in findings:
        low = f.target.lower()
        if f.kind == "visibility-cut" and not (
                low.startswith("self.") or low.startswith("getattr(self")):
            continue
        if f.kind == "value-replace" and not (
                low.startswith("self.") and any(h in low for h in value_hints)):
            continue
        if f.kind == "sensitivity-jump" and not low.startswith("self."):
            continue
        if f.kind == "redraw-cut" and not f.inventory:
            continue
        if f.score < 7 and f.kind not in {"container-rebuild", "stack-switch",
                                         "opacity-jump"}:
            continue
        key = (f.function, f.kind, f.target)
        old = precise.get(key)
        if old is None or (f.score, -f.line) > (old.score, -old.line):
            precise[key] = f
    return list(precise.values())


def print_report(findings: list[Finding], top: int, inventory: dict) -> None:
    findings.sort(key=lambda f: (-f.score, f.module, f.line, f.kind))
    unnamed = [f for f in findings if f.unnamed]
    print("STATE CHANGE CENSUS — report only (always exits 0)")
    print("Scope: user-facing de/*.py; construction/build methods and %d "
          "infrastructure modules skipped." % len(SKIPPED))
    print("Inventory: %d named transitions; %d candidate hard-cut sites; %d unnamed."
          % (len(inventory["entries"]), len(findings), len(unnamed)))
    print("Ranking: 1–10 = affected surface size + likely encounter frequency + "
          "salience; rare/error paths and uncertain heuristics are demoted.")
    print("Existing overlap: page_switch_consistency_check.py already enforces "
          "module-level Gtk.Stack adoption/debt. Here, stack sites are only "
          "concrete census evidence, not a second gate.\n")

    counts = Counter(f.module for f in findings)
    uncounts = Counter(f.module for f in unnamed)
    print("PER-MODULE (candidates / unnamed)")
    for module, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print("  %-20s %3d / %3d" % (module, count, uncounts[module]))

    print("\nTOP %d SITES" % min(top, len(findings)))
    for i, f in enumerate(findings[:top], 1):
        inv = ", ".join(f.inventory) if f.inventory else "UNNAMED — Article G"
        print("%2d. [%d/10 %s] %s:%d  %s()  %s" %
              (i, f.score, f.confidence, f.module, f.line, f.function, f.kind))
        print("    User sees: %s." % f.sight)
        print("    Inventory: %s" % inv)

    print("\nUNNAMED TRANSITIONS (%d)" % len(unnamed))
    for f in unnamed:
        print("  %s:%d  %-18s %s — %s" %
              (f.module, f.line, f.kind, f.target, f.function))

    print("\nKNOWN-TRUE VALIDATION")
    checks = [("finder.py", "_fill_sidebar", "container-rebuild"),
              ("academics.py", "_refresh_homework", "container-rebuild"),
              ("sysmon.py", "refresh", "value-replace")]
    for module, fn, kind in checks:
        matches = [f for f in findings if f.module == module and
                   f.function == fn and f.kind == kind]
        if matches:
            print("  FOUND %s.%s (%s) at line%s %s" %
                  (module[:-3], fn, kind, "s" if len(matches) > 1 else "",
                   ", ".join(str(f.line) for f in matches)))
        else:
            print("  MISSED %s.%s (%s) — detector regression" %
                  (module[:-3], fn, kind))

    medium = sum(f.confidence == "medium" for f in findings)
    # This is an explicit estimate, not pretend ground truth: high-confidence
    # structural shapes get 5%, ambiguous value/redraw shapes 25%.
    estimate = round((len(findings) - medium) * .05 + medium * .25)
    pct = round(100 * estimate / len(findings)) if findings else 0
    print("\nFALSE-POSITIVE ESTIMATE")
    print("  ~%d sites (%d%%): 5%% of high-confidence structural findings plus "
          "25%% of medium-confidence value/redraw findings." % (estimate, pct))
    print("  Basis: manual source review of the three validation classes above; "
          "medium findings remain labelled because AST cannot prove what is mapped.")
    print("  Main false-negative risk: state changes hidden behind project-specific "
          "wrapper methods or model mutation followed by indirect redraw.")

    print("\nSKIPPED MODULES")
    for module, why in sorted(SKIPPED.items()):
        print("  %-18s %s" % (module, why))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=30,
                    help="number of ranked individual sites to print (default 30)")
    args = ap.parse_args()
    try:
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        ids = {e["id"] for e in inventory["entries"]}
        findings: list[Finding] = []
        for path in sorted(DE.glob("*.py")):
            if path.name in SKIPPED:
                continue
            try:
                findings.extend(census_file(path, ids))
            except (OSError, SyntaxError) as exc:
                print("NOTE: skipped unreadable %s: %s" % (path.name, exc))
        print_report(findings, max(0, args.top), inventory)
    except Exception as exc:  # A report must not poison run_all_gates.
        print("STATE CHANGE CENSUS could not complete: %s" % exc)
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
