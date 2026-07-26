#!/usr/bin/env python3
"""
Static audit for the NameError class of bug — a name used but never imported or
defined, which py_compile does NOT catch and construct-all catches only if the
offending line runs during construction.

This exists because exactly that bug shipped once: shell.py called `_t(...)` for
the top-panel menu labels but never `from nbi18n import _t`, so Panel() crashed
on boot — invisible to py_compile and to construct-all (which covered only the
23 apps, not the panel). This checker would have flagged it immediately.

Method: for each module, collect every name DEFINED anywhere in it — imports,
assignments (at any scope), def/class names, function params, comprehension /
for / with-as / except-as targets, global/nonlocal decls — plus builtins. Then
flag every Name used in Load context that is not in that set. Collecting
definitions module-wide (not per-scope) deliberately OVER-approximates "defined",
which suppresses local-variable false positives at the cost of missing a name
that is defined in one function but used undefined in another — an acceptable
trade for catching the "never defined anywhere" class (like `_t`) with zero noise.

Run:
  python3 undefined_names_audit.py            # audits the shipped de/ tree
  python3 undefined_names_audit.py <dir>...    # audit specific dirs
Exit status is nonzero if any undefined name is found.
"""
import ast
import sys
import os
import glob
import builtins

BUILTINS = set(dir(builtins)) | {
    "self", "cls", "True", "False", "None", "__file__", "__name__", "__doc__",
    "__class__", "NotImplemented", "Ellipsis", "__import__",
}

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIRS = [os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                             "rootfs-overlay", "opt", "notebook", "de")]


def _targets(node, out):
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)


def defined_names(tree):
    d = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                d.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                d.add(a.asname or a.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            d.add(n.name)
            args = n.args
            for a in (list(args.args) + list(args.posonlyargs)
                      + list(args.kwonlyargs)):
                d.add(a.arg)
            if args.vararg:
                d.add(args.vararg.arg)
            if args.kwarg:
                d.add(args.kwarg.arg)
        elif isinstance(n, ast.Lambda):
            for a in list(n.args.args) + list(n.args.kwonlyargs):
                d.add(a.arg)
            if n.args.vararg:
                d.add(n.args.vararg.arg)
            if n.args.kwarg:
                d.add(n.args.kwarg.arg)
        elif isinstance(n, ast.ClassDef):
            d.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                _targets(t, d)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            _targets(n.target, d)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            _targets(n.target, d)
        elif isinstance(n, ast.comprehension):
            _targets(n.target, d)
        elif isinstance(n, ast.withitem):
            if n.optional_vars:
                _targets(n.optional_vars, d)
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                d.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            for x in n.names:
                d.add(x)
    return d


def audit_file(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    defined = defined_names(tree) | BUILTINS
    hits = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in defined and not n.id.startswith("__"):
                hits.setdefault(n.id, n.lineno)
    return sorted(hits.items(), key=lambda kv: kv[1])


def main():
    dirs = sys.argv[1:] or DEFAULT_DIRS
    files = []
    for d in dirs:
        files += sorted(glob.glob(os.path.join(d, "*.py")))
    bad = 0
    for f in files:
        for name, line in audit_file(f):
            bad += 1
            print("%s:%d  undefined name: %s" % (os.path.basename(f), line, name))
    n = len(files)
    if bad:
        print("\nFAIL: %d undefined-name use(s) across %d files" % (bad, n))
    else:
        print("CLEAN: no undefined names across %d files" % n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
