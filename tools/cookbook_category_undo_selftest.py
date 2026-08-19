#!/usr/bin/env python3
"""Display-free contract for New Category's structural undo frame."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/cookbook.py"
tree = ast.parse(PATH.read_text(encoding="utf-8"), PATH)
outer = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
             and n.name == "_new_category")
commit = next(n for n in outer.body if isinstance(n, ast.FunctionDef)
              and n.name == "commit")

calls = [(n.lineno, ast.unparse(n)) for n in ast.walk(commit)
         if isinstance(n, ast.Call)]
checkpoint = next(line for line, text in calls
                  if ".undo.checkpoint(" in text and "New Category" in text)
append = next(line for line, text in calls if ".cats.append(" in text)
done = next(line for line, text in calls if ".undo.commit(" in text)
assert checkpoint < append < done, (checkpoint, append, done)

# A blank or duplicate name must be REFUSED before the history is touched, so
# it cannot create an undo step.
#
# This used to be written as "the `if` around .cats.append() also contains the
# checkpoint and the commit", which pinned one particular shape of the code
# rather than the rule. Add now answers a blank or duplicate name instead of
# closing the dialog with nothing to show for it (it says which it is and keeps
# the dialog open), so those cases are early returns and there is no enclosing
# `if` left to find. The rule below is the one that was always meant: both
# refusals happen, and both happen before the first history call.
guards = [n for n in commit.body if isinstance(n, ast.If)
          and any(isinstance(x, ast.Return) for x in ast.walk(n))]
assert len(guards) >= 2, [ast.unparse(g) for g in guards]
assert max(g.lineno for g in guards) < checkpoint
guard_src = " ".join(ast.unparse(g) for g in guards)
assert "not v" in guard_src, guard_src
assert "v in self.cats" in guard_src, guard_src

# ...and a refused name must leave the dialog open: destroy() only ever runs
# after the category has actually been added.
destroys = [line for line, text in calls if "dlg.destroy(" in text]
assert destroys and min(destroys) > checkpoint, (destroys, checkpoint)

print("PASS New Category checkpoints before append and commits afterward")
print("PASS blank and duplicate names cannot create a history frame")
print("PASS a refused name keeps the dialog open")
print("RESULT: PASS")
