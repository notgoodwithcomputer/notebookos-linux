#!/usr/bin/env python3
"""
Which app functions does no suite ever enter?

An AUDIT, not a gate — it is deliberately not in `run_all_gates`. Running every
suite under a profiler costs about as long as the whole gate run, and a
forty-minute gate is one people skip (see the note on
`language_course_selftest`). Run it when choosing what to work on next.

It answers the question that found the last four real defects, one level
sharper than "does this module have a suite at all":

    tools/guestrun.sh python3 tools/func_coverage.py bills
    tools/guestrun.sh python3 tools/func_coverage.py bills packages calculator
    tools/guestrun.sh python3 tools/func_coverage.py --all       # slow

For each module it finds every `tools/*_selftest.py` that imports it, runs them
all with a profiler attached, and lists the functions defined in the module that
were never entered. `sys.setprofile` fires only on call and return, so this
costs a few percent rather than the ten times of line tracing.

WHAT A MISS DOES AND DOES NOT MEAN. It is not a defect. It is a place where a
defect would be invisible, which is a different and more useful thing to know.
The first run said 25 of bills.py's 76 functions were never entered — the whole
of add, pay, delete and export — and an unguarded fixed-path PDF export was
sitting in there, the last one in the tree.

Known limits, stated so a reading of the output is not overconfident:
  * FUNCTION level, not line. A function entered once counts as covered even if
    only its first branch ever runs.
  * A suite that imports a module for one helper still counts as its suite; the
    per-module totals are about reach, not depth.
  * Suites that import DYNAMICALLY count for every module — `construct_all_host`
    builds all thirty apps through `importlib`, so it is included for each.
  * A decorated def reports the decorator's line as `co_firstlineno`, so the
    match allows a couple of lines of slack.
"""
import ast
import os
import re
import sys
import glob
import runpy
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")


def defined_functions(path):
    """{first line -> qualified name} for every def in a file."""
    out = {}
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_ClassDef(self, n):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        def _fn(self, n):
            out[n.lineno] = ".".join(self.stack + [n.name])
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

    V().visit(tree)
    return out


DYNAMIC = re.compile(r"importlib\.import_module|__import__\(")


def suites_importing(module):
    """Every tools/*.py that reaches `module` — by name OR dynamically.

    The dynamic half is not a nicety. `construct_all_host.py` builds every app
    through `importlib.import_module(name)`, so a name-only regex reported
    `Packages.__init__` as never entered when in fact every page of it is
    constructed on each run. That single omission overstated the gap for every
    app in the tree, which is worse than not measuring: it is the checker
    manufacturing work (blind-spot class 6).
    """
    found = []
    imp = re.compile(r"^\s*(?:import %s\b|from %s import)" % (module, module),
                     re.M)
    for path in sorted(glob.glob(os.path.join(HERE, "*_selftest.py"))
                       + glob.glob(os.path.join(HERE, "construct_all*.py"))):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if imp.search(text) or DYNAMIC.search(text):
            found.append(path)
    return sorted(set(found))


def run_under_profile(suite, target):
    """Run one suite; return the set of first-lines entered in `target`."""
    seen = set()

    def prof(frame, event, _arg):
        if event == "call" and frame.f_code.co_filename == target:
            seen.add(frame.f_code.co_firstlineno)
        return None

    argv, home = list(sys.argv), os.environ.get("NB_HOME")
    os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nb-cov-")
    sys.argv = [suite]
    sys.setprofile(prof)
    try:
        runpy.run_path(suite, run_name="__main__")
    except SystemExit:
        pass
    except Exception as exc:                                    # noqa: BLE001
        print("   (suite raised: %s: %s)" % (type(exc).__name__, exc))
    finally:
        sys.setprofile(None)
        sys.argv = argv
        if home is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = home
    return seen


def report(module):
    target = os.path.join(DE, module + ".py")
    if not os.path.exists(target):
        print("%-14s  no such module" % module)
        return None
    defined = defined_functions(target)
    suites = suites_importing(module)
    if not suites:
        print("\n== %s: %d functions, NO SUITE IMPORTS IT" % (module, len(defined)))
        return (module, len(defined), 0, sorted(defined.values()))

    seen = set()
    for s in suites:
        seen |= run_under_profile(s, target)

    hit = set()
    for ln in seen:
        for cand in (ln, ln + 1, ln + 2):     # decorated defs report the
            if cand in defined:               # decorator's line
                hit.add(cand)
                break
    miss = sorted((ln, defined[ln]) for ln in defined if ln not in hit)
    print("\n== %s: %d functions, %d entered, %d never  (%s)"
          % (module, len(defined), len(hit), len(miss),
             ", ".join(os.path.basename(s)[:-3] for s in suites)))
    for ln, name in miss:
        print("     %-52s :%d" % (name, ln))
    return (module, len(defined), len(hit), [n for _l, n in miss])


def main(argv):
    if "--all" in argv:
        mods = sorted(os.path.splitext(os.path.basename(p))[0]
                      for p in glob.glob(os.path.join(DE, "*.py")))
    else:
        mods = [a for a in argv if not a.startswith("-")]
    if not mods:
        print(__doc__)
        return 2

    rows = [r for r in (report(m) for m in mods) if r]
    if len(rows) > 1:
        print("\n%-16s %6s %6s %6s" % ("module", "defs", "hit", "never"))
        for mod, total, hit, miss in sorted(rows, key=lambda r: -len(r[3])):
            print("%-16s %6d %6d %6d" % (mod, total, hit, len(miss)))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, DE)
    sys.exit(main(sys.argv[1:]))
