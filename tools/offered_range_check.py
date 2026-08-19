#!/usr/bin/env python3
"""Static gate for: a control must not offer values its own apply refuses.

The checker inventories numeric GTK controls and Notebook OS prompt rows, then
only compares a range when it can connect the control to a clamp by widget
identity, signal flow, or prompt state key.  Unconnected controls remain
visible in the coverage summary instead of being guessed at.

Run:
  python3 tools/offered_range_check.py
  python3 tools/offered_range_check.py --de /path/to/de
  python3 tools/offered_range_check.py --selfcheck
"""

# COVERAGE, STATED PLAINLY. This finds 47 ranged controls across 15 apps and
# can connect only 2 of them to the clamp that will squeeze their value. The
# other 45 are reported as unconnected rather than as passing, because a gate
# that cannot see the apply has not checked anything about it.
#
# Those 45 were read by hand once, on 2026-08-14, and none was wrong: they are
# mostly bounds that are correct by construction (0..255 for a colour channel,
# 1..31 for a day, 0..59 for minutes, 1..127 for MIDI velocity), and video.py
# and sequencer.py already derive theirs from the material. That is a
# reading, not a measurement, and it goes stale the moment someone edits one.
#
# The law it enforces — a control must not offer values its own apply refuses
# — was met three times in de/animation.py: a Strength slider running 0..2
# against a .7..1.8 clamp, loudness thresholds running 0..2 against an RMS
# that never exceeds 1, and a Scene Length field that accepted anything and
# then refused it. The two connected controls are those first two, so the
# gate does hold the ground where the defect actually appeared.
from __future__ import annotations

import argparse
import ast
import collections
import dataclasses
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = (ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# (module filename, control identity) -> a specific, hand-verified reason.
# Keep this small: entries are accepted mismatches, not suppressions.  The
# two-way check below rejects both new findings and entries which go stale.
DEBT: dict[tuple[str, str], str] = {}

# Every control the scanner cannot yet connect is still part of the gate's
# coverage contract.  Normalize local line suffixes below so harmless source
# movement does not churn this list, while a newly offered control, a removed
# one, or another same-named local occurrence changes the Counter and fails.
EXPECTED_INVENTORY = (
    ('animation.py', 'prompt:count@*'), ('animation.py', 'prompt:count@*'),
    ('animation.py', 'prompt:length@*'), ('animation.py', 'prompt:loud@*'),
    ('animation.py', 'prompt:quiet@*'), ('animation.py', 'prompt:strength@*'),
    ('animation.py', 'widget@*'), ('animation.py', 'widget@*'),
    ('bills.py', 'd@*'), ('bills.py', 'lead@*'), ('bills.py', 'y@*'),
    ('comics.py', 'scale@*'), ('comics.py', 'self.op_scale'),
    ('composer.py', 'self.tempo'), ('composer.py', 'self.velocity'),
    ('gbasdk.py', 'adj@*'), ('gbasdk.py', 'line:@*'),
    ('gbasdk.py', 'self._obj_hurt_frames'), ('gbasdk.py', 'self._snd_tempo'),
    ('illustrator.py', 'sc@*'), ('illustrator.py', 'self.op_scale'),
    ('installer.py', 'self._sp_swap'), ('media.py', 'self._v_seek'),
    ('music.py', 'self.vol'), ('music.py', 'track@*'),
    ('nbprint.py', 'copies@*'), ('sequencer.py', 'gain@*'),
    ('sequencer.py', 'gain@*'), ('sequencer.py', 's@*'),
    ('sequencer.py', 'self._hadj'), ('sequencer.py', 'self.bpm_scale'),
    ('sequencer.py', 'self.cgain'), ('sequencer.py', 'self.master_fader'),
    ('sequencer.py', 'self.master_scale'), ('settings.py', 'adj@*'),
    ('settings.py', 'cadj@*'), ('settings.py', 'self._hspin'),
    ('settings.py', 'self._kdelay'), ('settings.py', 'self._krate'),
    ('settings.py', 'self._mspin'), ('video.py', 'self._mus_vol'),
    ('video.py', 'self._prop_dur'), ('video.py', 'self._prop_trim'),
    ('video.py', 'self._prop_vol'), ('workout.py', 'r_adj@*'),
    ('workout.py', 's_adj@*'), ('writer.py', 'sp@*'),
)
MIN_CONNECTED = 2

RANGE_CTORS = {
    "SpinButton.new_with_range": (0, 1),
    "Scale.new_with_range": (1, 2),
}
GETTERS = {"get_value", "get_value_as_int"}


@dataclasses.dataclass
class Control:
    module: str
    name: str
    label: str
    lo: float | None
    hi: float | None
    line: int
    handler: str | None = None
    state_key: str | None = None


@dataclasses.dataclass(frozen=True)
class Finding:
    module: str
    control: str
    label: str
    offered_lo: float
    offered_hi: float
    clamp_lo: float
    clamp_hi: float
    line: int


def dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def assigned_name(node: ast.AST, parents: dict[int, ast.AST]) -> str | None:
    cur = node
    while id(cur) in parents:
        parent = parents[id(cur)]
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
            return dotted(target)
        if isinstance(parent, ast.Call):
            break
        cur = parent
    return None


def number(node: ast.AST, constants: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = number(node.operand, constants)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = number(node.left, constants), number(node.right, constants)
        if left is None or right is None:
            return None
        try:
            return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right,
                    ast.Mult: lambda: left * right, ast.Div: lambda: left / right,
                    ast.FloorDiv: lambda: left // right}[type(node.op)]()
        except (KeyError, ZeroDivisionError):
            return None
    return None


def literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def state_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Subscript):
        return literal_string(node.slice)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "get" and node.args:
        return literal_string(node.args[0])
    return None


def contains_getter(node: ast.AST, widget: str | None, sender: str | None) -> bool:
    for part in ast.walk(node):
        if not isinstance(part, ast.Call) or not isinstance(part.func, ast.Attribute):
            continue
        if part.func.attr in GETTERS:
            owner = dotted(part.func.value)
            if owner == widget or (sender and owner == sender):
                return True
    return False


def contains_state(node: ast.AST, key: str | None) -> bool:
    return bool(key and any(state_key(part) == key for part in ast.walk(node)))


def transparent_value(node: ast.AST) -> ast.AST:
    """Discard conversions which preserve units, but not arithmetic/scaling."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in {"int", "float", "round"} and node.args:
        node = node.args[0]
    return node


def direct_source(node: ast.AST, control: Control, sender: str | None,
                  aliases: set[str]) -> bool:
    node = transparent_value(node)
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    if control.state_key and state_key(node) == control.state_key:
        return True
    return contains_getter(node, control.name, sender) and isinstance(node, ast.Call)


def clamp_from_expr(node: ast.AST, constants: dict[str, float]) -> tuple[float, float, ast.AST] | None:
    """Recognize min(hi, max(lo, value)) and the reversed spelling."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) \
            or node.func.id not in {"min", "max"} or len(node.args) != 2:
        return None
    outer = node.func.id
    for bound_node, inner in ((node.args[0], node.args[1]), (node.args[1], node.args[0])):
        if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Name) \
                or len(inner.args) != 2 or inner.func.id == outer:
            continue
        outer_bound = number(bound_node, constants)
        for inner_bound_node, value in ((inner.args[0], inner.args[1]),
                                        (inner.args[1], inner.args[0])):
            inner_bound = number(inner_bound_node, constants)
            if outer_bound is None or inner_bound is None:
                continue
            lo, hi = ((inner_bound, outer_bound) if outer == "min"
                      else (outer_bound, inner_bound))
            if lo <= hi:
                return lo, hi, value
    return None


def guard_bound(node: ast.If, constants: dict[str, float], control: Control,
                sender: str | None, aliases: set[str]) -> tuple[str, float] | None:
    """Recognize `if value > hi: value = hi` / return and lower twins."""
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 \
            or len(test.comparators) != 1:
        return None
    left, right, op = test.left, test.comparators[0], test.ops[0]
    bound = number(right, constants)
    value = left
    if bound is None:
        bound = number(left, constants)
        value = right
        op = {ast.Gt: ast.Lt, ast.GtE: ast.LtE, ast.Lt: ast.Gt,
              ast.LtE: ast.GtE}.get(type(op), type(op))()
    if bound is None or not direct_source(value, control, sender, aliases):
        return None
    # A return/raise refuses the value.  An assignment must visibly replace
    # the compared local with the boundary.
    refuses = any(isinstance(part, (ast.Return, ast.Raise)) for part in node.body)
    compared_name = transparent_value(value).id if isinstance(transparent_value(value), ast.Name) else None
    assigns_bound = False
    for part in node.body:
        if isinstance(part, ast.Assign) and compared_name \
                and any(isinstance(t, ast.Name) and t.id == compared_name for t in part.targets) \
                and number(part.value, constants) == bound:
            assigns_bound = True
    if not (refuses or assigns_bound):
        return None
    if isinstance(op, (ast.Gt, ast.GtE)):
        return "hi", bound
    if isinstance(op, (ast.Lt, ast.LtE)):
        return "lo", bound
    return None


def function_map(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def constants_in(tree: ast.Module) -> dict[str, float]:
    out: dict[str, float] = {}
    changed = True
    while changed:
        changed = False
        for stmt in tree.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                value_node = stmt.value
                value = number(value_node, out) if value_node else None
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None and target.id not in out:
                        out[target.id] = value
                        changed = True
    return out


def prompt_controls(call: ast.Call, module: str, constants: dict[str, float]) -> list[Control]:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "_overlay_prompt" \
            or len(call.args) < 2 or not isinstance(call.args[1], (ast.List, ast.Tuple)):
        return []
    callback = call.args[3] if len(call.args) > 3 else None
    handler = callback.attr if isinstance(callback, ast.Attribute) else (
        callback.id if isinstance(callback, ast.Name) else None)
    out = []
    for row in call.args[1].elts:
        if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) < 4:
            continue
        spec = row.elts[3]
        if not isinstance(spec, (ast.Tuple, ast.List)) or len(spec.elts) < 3 \
                or literal_string(spec.elts[0]) not in {"int", "float"}:
            continue
        key, label = literal_string(row.elts[0]), literal_string(row.elts[1])
        if key:
            out.append(Control(module, f"prompt:{key}@{row.lineno}", label or key,
                               number(spec.elts[1], constants), number(spec.elts[2], constants),
                               row.lineno, handler, key))
    return out


def scan_module(path: Path) -> tuple[list[Control], list[Finding], int]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    constants = constants_in(tree)
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    funcs = function_map(tree)
    controls: list[Control] = []
    handlers: dict[str, str] = {}

    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        controls.extend(prompt_controls(call, path.name, constants))
        fname = dotted(call.func)
        short = ".".join((fname or "").split(".")[-2:])
        if short in RANGE_CTORS:
            li, hi = RANGE_CTORS[short]
            if len(call.args) > hi:
                owner = assigned_name(call, parents) or f"line:{call.lineno}"
                if owner and not owner.startswith("self."):
                    owner = f"{owner}@{call.lineno}"
                controls.append(Control(path.name, owner, owner,
                                        number(call.args[li], constants),
                                        number(call.args[hi], constants), call.lineno))
        elif fname == "Gtk.Adjustment":
            kws = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            if "lower" in kws and "upper" in kws:
                owner = assigned_name(call, parents) or f"line:{call.lineno}"
                if owner and not owner.startswith("self."):
                    owner = f"{owner}@{call.lineno}"
                controls.append(Control(path.name, owner, owner,
                                        number(kws["lower"], constants),
                                        number(kws["upper"], constants), call.lineno))
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "set_range" \
                and len(call.args) >= 2:
            owner = dotted(call.func.value) or f"line:{call.lineno}"
            existing = next((c for c in controls if c.name == owner), None) \
                if owner.startswith("self.") else None
            if existing:
                # A later set_range is the operative declaration for this widget.
                existing.lo, existing.hi = (number(call.args[0], constants),
                                            number(call.args[1], constants))
            else:
                controls.append(Control(path.name, owner, owner,
                                        number(call.args[0], constants),
                                        number(call.args[1], constants), call.lineno))
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "connect" \
                and len(call.args) >= 2:
            owner = dotted(call.func.value)
            cb = call.args[1]
            handler = cb.attr if isinstance(cb, ast.Attribute) else (
                cb.id if isinstance(cb, ast.Name) else None)
            if owner and handler:
                handlers[owner] = handler

    # Calls are walked in source-independent AST order; consolidate duplicate
    # declarations by identity while retaining anonymous line controls.
    unique: dict[str, Control] = {}
    for control in sorted(controls, key=lambda c: c.line):
        unique[control.name] = control
    controls = list(unique.values())
    findings: list[Finding] = []
    connected = 0

    for control in controls:
        handler_name = control.handler or handlers.get(control.name)
        # Local widgets use a line suffix to keep same-named controls in
        # different functions distinct.  Their connect receiver has no suffix.
        if not handler_name and "@" in control.name:
            handler_name = handlers.get(control.name.rsplit("@", 1)[0])
        fn = funcs.get(handler_name or "")
        if not fn:
            continue
        sender = None
        positional = list(fn.args.posonlyargs) + list(fn.args.args)
        if positional:
            names = [arg.arg for arg in positional]
            if names and names[0] == "self":
                names = names[1:]
            sender = names[0] if names else None
        aliases: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if direct_source(value, control, sender, aliases):
                    aliases.update(t.id for t in targets if isinstance(t, ast.Name))
        clamps: list[tuple[float, float]] = []
        guard_lows: list[float] = []
        guard_highs: list[float] = []
        for node in ast.walk(fn):
            parsed = clamp_from_expr(node, constants)
            if not parsed:
                continue
            lo, hi, value = parsed
            if direct_source(value, control, sender, aliases):
                clamps.append((lo, hi))
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.If):
                guarded = guard_bound(node, constants, control, sender, aliases)
                if guarded:
                    (guard_lows if guarded[0] == "lo" else guard_highs).append(guarded[1])
        if guard_lows or guard_highs:
            fallback_lo = control.lo if control.lo is not None else float("-inf")
            fallback_hi = control.hi if control.hi is not None else float("inf")
            clamps.append((max(guard_lows, default=fallback_lo),
                           min(guard_highs, default=fallback_hi)))
        if clamps:
            connected += 1
            if control.lo is not None and control.hi is not None:
                # Multiple clamps on the same direct value mean the tightest
                # interval is what apply actually permits.
                clamp_lo = max(x[0] for x in clamps)
                clamp_hi = min(x[1] for x in clamps)
                if control.lo < clamp_lo or control.hi > clamp_hi:
                    findings.append(Finding(path.name, control.name, control.label,
                                            control.lo, control.hi, clamp_lo, clamp_hi,
                                            control.line))
    return controls, findings, connected


def fmt(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def inventory_key(control: Control) -> tuple[str, str]:
    import re
    name = re.sub(r"^line:\d+@\d+$", "line:@*", control.name)
    return control.module, re.sub(r"@\d+$", "@*", name)


def run(de: Path) -> tuple[int, str]:
    controls: list[Control] = []
    findings: list[Finding] = []
    connected = 0
    modules = 0
    for path in sorted(de.glob("*.py")):
        try:
            found_controls, found_findings, found_connected = scan_module(path)
        except (OSError, SyntaxError) as exc:
            return 2, f"ERROR: cannot scan {path}: {exc}\n"
        if found_controls:
            modules += 1
            controls.extend(found_controls)
            findings.extend(found_findings)
            connected += found_connected

    lines = [f"RANGED CONTROLS: {len(controls)} across {modules} app modules",
             f"CONNECTED TO APPLY/CLAMP: {connected}",
             f"COULD NOT CONNECT: {len(controls) - connected}"]
    actual_inventory = collections.Counter(inventory_key(c) for c in controls)
    expected_inventory = collections.Counter(EXPECTED_INVENTORY)
    for key, count in sorted((actual_inventory - expected_inventory).items()):
        lines.append(f"NEW UNCONNECTED/UNRATCHETED CONTROL: {key[0]} {key[1]} x{count}")
    for key, count in sorted((expected_inventory - actual_inventory).items()):
        lines.append(f"STALE CONTROL INVENTORY: {key[0]} {key[1]} x{count}")
    if connected < MIN_CONNECTED:
        lines.append(f"CONNECTED COVERAGE SHRANK: {connected} < {MIN_CONNECTED}")
    for finding in sorted(findings, key=lambda f: (f.module, f.line, f.control)):
        lines.append(f"FINDING {finding.module}:{finding.line} {finding.label} "
                     f"offers {fmt(finding.offered_lo)}..{fmt(finding.offered_hi)}; "
                     f"apply clamps to {fmt(finding.clamp_lo)}..{fmt(finding.clamp_hi)}")

    actual = {(f.module, f.control) for f in findings}
    new = actual - set(DEBT)
    stale = set(DEBT) - actual
    for key in sorted(new):
        lines.append(f"NEW DEBT (unledgered): {key[0]} {key[1]}")
    for key in sorted(stale):
        lines.append(f"STALE DEBT (remove ledger entry): {key[0]} {key[1]}")
    for key, reason in sorted(DEBT.items()):
        lines.append(f"DEBT {key[0]} {key[1]}: {reason}")
    if not DEBT:
        lines.append("DEBT LEDGER: empty")
    ok = (not new and not stale and actual_inventory == expected_inventory
          and connected >= MIN_CONNECTED)
    lines.append("RESULT: PASS" if ok else
                 "RESULT: FAILED — offered-range debt ratchet changed")
    return (0 if ok else 1), "\n".join(lines) + "\n"


def selfcheck() -> tuple[int, str]:
    source = DEFAULT_DE / "animation.py"
    old = "('strength', 'Strength', 1.1,\n                               ('float', .7, 1.8))"
    widened = "('strength', 'Strength', 1.1,\n                               ('float', 0., 2.0))"
    with tempfile.TemporaryDirectory(prefix="offered-range-selfcheck-") as scratch:
        scratch_de = Path(scratch)
        target = scratch_de / source.name
        shutil.copy2(source, target)
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return 1, "SELFCHECK FAIL: real animation.py strength control fixture not found exactly once\n"
        target.write_text(text.replace(old, widened), encoding="utf-8")
        command = [sys.executable, str(Path(__file__).resolve()), "--de", str(scratch_de)]
        proc = subprocess.run(command, text=True, capture_output=True, check=False)
        expected = "Strength offers 0..2; apply clamps to 0.7..1.8"
        if proc.returncode == 0 or expected not in proc.stdout:
            return 1, ("SELFCHECK FAIL: widened real control did not make the gate red\n"
                       + proc.stdout + proc.stderr)
        return 0, ("SELFCHECK PASS: widened animation.py Strength from 0.7..1.8 to 0..2\n"
                   "SELFCHECK PASS: reported Strength offers 0..2; apply clamps to 0.7..1.8\n"
                   "SELFCHECK PASS: gate exited nonzero\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--de", type=Path, default=DEFAULT_DE)
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args()
    code, output = selfcheck() if args.selfcheck else run(args.de.resolve())
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
