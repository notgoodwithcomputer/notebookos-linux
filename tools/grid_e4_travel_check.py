#!/usr/bin/env python3
"""grid_e4_travel_check — §E4 check 5: no animation travel vector has both a
non-zero dx and dy.

PAPER-PHYSICS §E2: "A surface moves parallel to a rule that is already on
screen... Diagonal travel is forbidden: there is no diagonal in the layout for
it to follow." §E4 lists the gate for it as check 5, and grid_check's header
defers checks 3-5 because they "require the Article G motion inventory and land
with it". That inventory now exists and is bound, so this one can land.

Kept in its OWN file rather than folded into grid_check.py: grid_check owns the
STATIC layout constants (rails, ladders, lockstep) and is edited whenever a
sidebar converges, while this reads the MOTION inventory. Two ledgers with two
different burn-downs in one file is how a ratchet ends up with an edit conflict.

WHAT IS ACTUALLY CHECKED, and why the scope is narrow on purpose.

The rule is about a SURFACE TRAVELLING. This OS moves a surface exactly one
way: it paints a captured `cairo.ImageSurface` at an offset inside a draw
handler, and animates that offset (finder's navigation slide is the reference).
So the check reads the OFFSET ARGUMENTS of `set_source_surface` and `translate`
inside modules that carry an `nbmotion-inventory` marker, and fails when BOTH
the x and y argument are non-zero.

Three things are deliberately NOT travel, and treating them as such would make
this gate lie:
  * A GROW (`GrowCard`) interpolates a whole rect from an anchor to a target.
    Both axes change, but that is a SCALE about an origin — Article B's
    "a surface names where it came from" — not a slide across the layout.
  * A CONTENT VIEWPORT (maps panning cx/cy) moves the world under a fixed
    frame. The person is dragging a map; there is no surface travelling along
    a rule.
  * A cairo transform used to RENDER at device scale (HiDPI) is not motion.
Each exemption is listed below WITH ITS REASON, so the exemption list is itself
reviewable — an unexplained exemption is how a gate quietly stops applying.

    python3 tools/grid_e4_travel_check.py

Exit 0 clean; 1 on any diagonal travel vector.
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# NB_DE_DIR points the scan at a scratch COPY of de/, which is how this gate is
# red-proofed: sabotage the copy, never the tree. The inventory is still read
# from the real tools/ dir, because the red proof is about the SOURCE being
# diagonal, not about the ledger.
DE = os.environ.get("NB_DE_DIR") or os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de")
INVENTORY = os.path.join(HERE, "motion_inventory.json")

# Offsetting calls whose 2 leading numeric args are a translation vector.
TRAVEL_CALLS = {"set_source_surface": (1, 2), "translate": (0, 1)}

# (module, function) -> why this is not a travel vector. Reviewable on purpose.
EXEMPT = {
    ("nbtransitions.py", "_frame"):
        "GrowCard interpolates a whole rect from an anchor to its target: a "
        "SCALE about an origin (Article B), not a slide along the layout.",
    ("maps.py", "_draw"):
        "The map viewport moves the world under a fixed frame while the person "
        "pans. No surface travels along a rule.",
}

fails = []
checks = 0


def const(node):
    """The numeric value of `node`, or None if it is not a plain number."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))):
        return -node.operand.value
    return None


def is_zero(node):
    """Whether this argument is a hard zero — the only way an axis is proven
    not to move. A NAME or an expression could be anything at runtime, so it
    counts as moving; that is the safe direction for this gate to err in."""
    return const(node) == 0


def reads_state(node):
    """Whether this offset expression reads `self.<attr>` — i.e. is driven by
    the object's animation state rather than by a local coordinate."""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"):
            return True
    return False


def enclosing_functions(tree):
    """(node -> nearest enclosing FunctionDef name), so a finding can say which
    function it is in and be matched against the exemption list."""
    owner = {}

    def walk(node, fname):
        for child in ast.iter_child_nodes(node):
            nxt = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fname
            owner[child] = nxt
            walk(child, nxt)
    walk(tree, None)
    return owner


def forwarded_travel_helpers(tree):
    """Map a one-hop drawing helper to the parameters it forwards as x/y."""
    found = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in fn.args.args]
        if params and params[0] in ("self", "cls"):
            params = params[1:]
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)):
                continue
            slots = TRAVEL_CALLS.get(call.func.attr)
            if slots is None or len(call.args) <= max(slots):
                continue
            x, y = call.args[slots[0]], call.args[slots[1]]
            if (isinstance(x, ast.Name) and isinstance(y, ast.Name)
                    and x.id in params and y.id in params):
                found.setdefault(fn.name, []).append(
                    (params.index(x.id), params.index(y.id), call.func.attr))
    return found


def main():
    global checks
    try:
        inv = json.load(open(INVENTORY, encoding="utf-8"))
    except OSError as exc:
        print("FAIL  cannot read the motion inventory: %s" % exc)
        return 1

    # Only modules the inventory actually binds a transition to. A module with
    # no named motion has no travel vector to get wrong, and scanning it would
    # turn ordinary drawing code into gate noise.
    bound = set()
    for e in inv.get("entries", []):
        b = e.get("binding") or {}
        mod = (b.get("module") or "")
        if mod.endswith(".py"):
            bound.add(os.path.basename(mod))
    if not bound:
        print("FAIL  no bound modules in the inventory — nothing to check, "
              "which would make this gate vacuous")
        return 1

    print("scanning %d module(s) the inventory binds motion to" % len(bound))
    for name in sorted(bound):
        path = os.path.join(DE, name)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        except (OSError, SyntaxError) as exc:
            print("FAIL  %s unreadable: %s" % (name, exc))
            fails.append(name)
            continue
        owner = enclosing_functions(tree)
        helpers = forwarded_travel_helpers(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = (node.func.attr if isinstance(node.func, ast.Attribute)
                      else node.func.id if isinstance(node.func, ast.Name)
                      else "")
            slots = (TRAVEL_CALLS.get(callee) if isinstance(
                node.func, ast.Attribute) else None)
            if slots is None:
                forwarded = helpers.get(callee, [])
                for xi, yi, primitive in forwarded:
                    if len(node.args) <= max(xi, yi):
                        continue
                    checks += 1
                    x, y = node.args[xi], node.args[yi]
                    if (not is_zero(x) and not is_zero(y)
                            and (reads_state(x) or reads_state(y))):
                        fn = owner.get(node, None)
                        fails.append(
                            "%s:%d %s() via %s() in %s: travel vector moves "
                            "BOTH axes (dx=%s, dy=%s) — §E2 forbids diagonal "
                            "travel" % (name, node.lineno, primitive, callee,
                                        fn or "<module>", ast.unparse(x)[:28],
                                        ast.unparse(y)[:28]))
                continue
            xi, yi = slots
            if len(node.args) <= max(xi, yi):
                continue
            checks += 1
            fn = owner.get(node, None)
            why = EXEMPT.get((name, fn))
            if why:
                continue
            x, y = node.args[xi], node.args[yi]
            # An ANIMATION offset is driven by animation STATE — the progress
            # value the frame callback updates, reached as self.<something>.
            # A static composite (illustrator's _blit/_crop_surface painting a
            # layer at plain local x, y) is ordinary drawing and is NOT a
            # travel vector; flagging it would make this gate cry wolf on the
            # busiest file in the OS. So both axes must be non-zero AND at
            # least one must read animation state for this to be travel.
            if (not is_zero(x) and not is_zero(y)
                    and (reads_state(x) or reads_state(y))):
                fails.append(
                    "%s:%d %s() in %s: travel vector moves BOTH axes "
                    "(dx=%s, dy=%s) — §E2 forbids diagonal travel"
                    % (name, node.lineno, callee, fn or "<module>",
                       ast.unparse(x)[:28], ast.unparse(y)[:28]))

    for f in fails:
        print("FAIL  %s" % f)
    print("\n%s  §E4 check 5 (no diagonal travel): %d vector(s) examined, "
          "%d exemption(s) declared"
          % ("FAIL" if fails else "PASS", checks, len(EXEMPT)))
    if not fails:
        print("RESULT: PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
