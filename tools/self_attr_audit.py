#!/usr/bin/env python3
"""
Static audit for the AttributeError class of bug — `self._foo(...)` where
`_foo` was never defined on the class or any of its in-tree bases.

This is the sibling of undefined_names_audit.py and covers the half that one is
blind to by construction: that checker collects *bare names* (imports, defs,
assignments) and flags Name nodes, so an attribute access is invisible to it.

It exists because exactly that bug was live in the shipped tree:
settings.py:_apply_saved_prefs called self._apply_pointer_speed() and
self._apply_natural_scroll(), both removed along with the inert Mouse & Touchpad
page. _apply_saved_prefs runs from __init__, so on any machine whose
settings.json still carried the legacy keys, Settings raised AttributeError and
would not open at all. py_compile is blind to it; undefined_names_audit reported
CLEAN; and construct-all missed it because construction only reaches the line
when the saved-prefs keys are present, which they are not on a fresh profile.

Method
------
For every class in the tree, collect the attribute names it DEFINES: methods,
class-level assignments, and every `self.X = ...` / `self.X += ...` /
`for self.X in` / `with ... as self.X` target anywhere in its body. Resolve base
classes to other classes in the same tree (same module, or imported from a
sibling module) and take the union up the chain. Then flag every `self._X`
that is not in that union.

Scope is deliberately limited to single-underscore private names. A public
`self.foo` may legitimately come from an out-of-tree base (Gtk.Window,
Gtk.Dialog, object), which this cannot see; the `_`-prefix convention means the
name is this codebase's own and must therefore be defined in this codebase.

setattr(self, ...) is modelled rather than surrendered to, because bailing on it
would have excluded fifteen classes including Finder, Illustrator and Writer --
the ones most worth checking. Every occurrence in this tree is the same idiom, a
GLib source-id being cleared, and it is read in three tiers:
  * a constant name -- setattr(self, "_course_scroll", v) -- is simply recorded;
  * a loop variable over a literal tuple of names expands to those names;
  * a helper parameter -- _cancel_source(self, attr) -- resolves through every
    in-tree call site, provided all of them pass a string constant.
What remains unknowable is separated by consequence. An unknown *name* makes the
defines-union incomplete, so reads of that class can no longer be trusted; but a
setattr whose *value* is a non-callable literal (0, None, "") can never make
self._foo() valid, so CALL findings stay sound. Only a setattr that could store
something callable disables the call check.

A class is SKIPPED entirely when it has a base this audit cannot resolve and
that base is not a known external root (Gtk.*, GObject.*, object, Exception,
...). An unresolvable in-tree-looking base means the union is incomplete in a way
that has no safe half. Skips and partial checks are reported in the summary so
the blind spot is visible rather than silent.

Run:
  python3 self_attr_audit.py             # audits the shipped de/ tree
  python3 self_attr_audit.py <dir>...    # audit specific dirs
Exit status is nonzero if any undefined attribute is found.
"""
import ast
import sys
import os
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIRS = [os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                             "rootfs-overlay", "opt", "notebook", "de")]

# Bases that are known to live outside this tree. Inheriting from one of these
# is fine: none of them contributes a single-underscore attribute that our own
# code would call, so the union stays sound for the names we check.
EXTERNAL_ROOTS = (
    "object", "type", "Exception", "BaseException", "ValueError", "OSError",
    "RuntimeError", "KeyError", "IOError", "dict", "list", "set", "tuple",
    "str", "int", "float", "Thread", "Enum", "IntEnum", "namedtuple",
    "HTMLParser", "SimpleHTTPRequestHandler", "BaseHTTPRequestHandler",
    "ArgumentParser", "Handler", "Formatter", "Protocol", "ABC",
    # ctypes: attributes come from a class-level _fields_ list, and those field
    # names are public, so the underscore filter already excludes them.
    "Structure", "Union", "BigEndianStructure", "LittleEndianStructure",
)
# Any dotted base whose first segment is one of these is an external library.
EXTERNAL_MODULES = (
    "Gtk", "Gdk", "GObject", "GLib", "Gio", "GdkPixbuf", "Pango", "PangoCairo",
    "cairo", "Gst", "ast", "html", "http", "threading", "argparse", "logging",
    "enum", "abc", "typing", "collections", "xml", "email", "unittest", "socketserver",
)


def base_name(node):
    """Render a base-class expression as a string, or None if too dynamic."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = base_name(node.value)
        return None if head is None else head + "." + node.attr
    return None


def is_external(name):
    if name is None:
        return False
    if name in EXTERNAL_ROOTS:
        return True
    return name.split(".")[0] in EXTERNAL_MODULES


class ClassInfo:
    __slots__ = ("module", "name", "bases", "defines", "uses", "lineno",
                 "opaque_names", "opaque_values", "setattr_params", "calls")

    def __init__(self, module, name, lineno):
        self.module = module
        self.name = name
        self.lineno = lineno
        self.bases = []        # list of rendered base strings (or None)
        self.defines = set()   # attribute names this class body establishes
        self.uses = []         # (attr, lineno, is_call)
        self.opaque_names = False   # a setattr target name we cannot pin down
        self.opaque_values = False  # a setattr value that might be callable
        self.setattr_params = {}    # method name -> param name used as target
        self.calls = []             # (method name, positional arg nodes)


def _record_target(node, defines):
    """Record `self.X` appearing as an assignment target."""
    for n in ast.walk(node):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "self"):
            defines.add(n.attr)


def _str_const(node):
    return (node.value if isinstance(node, ast.Constant)
            and isinstance(node.value, str) else None)


def _literal_strings(node):
    """The string constants of a literal tuple/list/set, or None if not one."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    out = []
    for elt in node.elts:
        s = _str_const(elt)
        if s is None:
            return None
        out.append(s)
    return out


def _loop_names(func, var):
    """Names `var` can take from `for var in ("a", "b")` loops inside func."""
    out = set()
    found = False
    for n in ast.walk(func):
        if (isinstance(n, (ast.For, ast.AsyncFor))
                and isinstance(n.target, ast.Name) and n.target.id == var):
            names = _literal_strings(n.iter)
            if names is None:
                return None
            out.update(names)
            found = True
    return out if found else None


def _non_callable_literal(node):
    """True when the value provably cannot be callable."""
    if isinstance(node, ast.Constant):
        return not callable(node.value)
    return isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict,
                             ast.JoinedStr, ast.Compare))


def _enclosing_func(classnode, call):
    """The method of `classnode` that lexically contains `call`."""
    for stmt in ast.walk(classnode):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(stmt):
                if n is call:
                    return stmt
    return None


def _model_setattr(classnode, info, call):
    """Fold one setattr(self, NAME, VALUE) into the class's known attributes.

    The value only ever matters when the name could not be pinned down: a name
    we resolved is in `defines` and is therefore never flagged, whatever it was
    assigned. So `opaque_values` is raised solely alongside `opaque_names`.
    """
    def give_up():
        info.opaque_names = True
        if not _non_callable_literal(call.args[2]):
            info.opaque_values = True

    const = _str_const(call.args[1])
    if const is not None:                       # setattr(self, "_x", ...)
        info.defines.add(const)
        return

    if not isinstance(call.args[1], ast.Name):  # a computed name
        return give_up()

    var = call.args[1].id
    func = _enclosing_func(classnode, call)
    if func is None:
        return give_up()

    names = _loop_names(func, var)              # for var in ("_a", "_b")
    if names is not None:
        info.defines |= names
        return

    params = [a.arg for a in
              list(func.args.posonlyargs) + list(func.args.args)]
    if var in params:                           # _cancel_source(self, attr)
        info.setattr_params[func.name] = var
        return

    give_up()


def scan_class(module, node):
    info = ClassInfo(module, node.name, node.lineno)
    for b in node.bases:
        info.bases.append(base_name(b))

    for stmt in node.body:
        # Methods and class-level names.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info.defines.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            # Walk the targets rather than matching bare Names: a class-level
            # `_A, _B = x, y` defines BOTH, and matching only ast.Name made
            # the audit manufacture "never defined" findings against correct
            # tuple-unpacked constants (finder._NAV_ON/_NAV_OFF).
            for t in stmt.targets:
                for leaf in ast.walk(t):
                    if isinstance(leaf, ast.Name):
                        info.defines.add(leaf.id)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            info.defines.add(stmt.target.id)
    # __slots__ declares attributes too.
    for stmt in node.body:
        if (isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__slots__"
                        for t in stmt.targets)):
            for elt in ast.walk(stmt.value):
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    info.defines.add(elt.value)

    # Everything `self.X` inside the body: assignments define, loads use.
    called = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                _record_target(t, info.defines)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            _record_target(n.target, info.defines)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            _record_target(n.target, info.defines)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    _record_target(item.optional_vars, info.defines)
        elif isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == "setattr"
                    and len(n.args) >= 3 and isinstance(n.args[0], ast.Name)
                    and n.args[0].id == "self"):
                _model_setattr(node, info, n)
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "self"):
                called.add(id(f))
                info.calls.append((f.attr, list(n.args)))

    for n in ast.walk(node):
        if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
                and isinstance(n.value, ast.Name) and n.value.id == "self"):
            info.uses.append((n.attr, n.lineno, id(n) in called))
    return info


def collect(paths):
    classes = {}   # (module, name) -> ClassInfo
    imports = {}   # module -> {local_name: source_module}
    methods = {}   # (module, class, method) -> FunctionDef
    for path in paths:
        module = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except SyntaxError as exc:
            print("SYNTAX ERROR %s: %s" % (path, exc))
            continue
        imports[module] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                for a in n.names:
                    imports[module][a.asname or a.name] = n.module
            elif isinstance(n, ast.Import):
                for a in n.names:
                    imports[module][a.asname or a.name] = a.name
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                info = scan_class(module, n)
                classes[(module, n.name)] = info
                for sub in ast.walk(n):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.setdefault((module, n.name, sub.name), sub)
    return classes, imports, methods


def resolve_setattr_params(classes, methods_of):
    """Tier three: _cancel_source(self, attr) -> the names its callers pass.

    Every in-tree call site of the helper is inspected. All-constant arguments
    fold into the class's defines; anything else leaves the name unknowable.
    """
    for info in classes.values():
        for method, param in info.setattr_params.items():
            func = methods_of.get((info.module, info.name, method))
            if func is None:
                info.opaque_names = True
                continue
            params = [a.arg for a in
                      list(func.args.posonlyargs) + list(func.args.args)]
            try:
                pos = params.index(param) - 1        # drop `self`
            except ValueError:
                info.opaque_names = info.opaque_values = True
                continue
            sites = 0
            for caller in classes.values():
                for name, args in caller.calls:
                    if name != method:
                        continue
                    sites += 1
                    const = _str_const(args[pos]) if pos < len(args) else None
                    if const is None:
                        # An unresolved argument means an unknown name reached
                        # setattr; the value there could be anything.
                        info.opaque_names = info.opaque_values = True
                    else:
                        info.defines.add(const)
            if not sites:
                info.opaque_names = info.opaque_values = True


def resolve(info, classes, imports, base):
    """Map a rendered base string to a ClassInfo in the tree, or None."""
    if base is None:
        return None
    if "." in base:
        mod, _, attr = base.rpartition(".")
        # `nbapp.NBApp` -> module nbapp, class NBApp (following `import nbapp`)
        src = imports.get(info.module, {}).get(mod, mod)
        return classes.get((src, attr))
    # Bare name: same module first, then whatever it was imported from.
    hit = classes.get((info.module, base))
    if hit is not None:
        return hit
    src = imports.get(info.module, {}).get(base)
    if src:
        return classes.get((src.split(".")[-1], base))
    return None


def chain(info, classes, imports, seen=None):
    """(defines up the MRO, opaque names?, opaque values?, unresolved base)."""
    if seen is None:
        seen = set()
    key = (info.module, info.name)
    if key in seen:
        return set(), False, False, None
    seen.add(key)
    defines = set(info.defines)
    op_names = info.opaque_names
    op_values = info.opaque_values
    unresolved = None
    for b in info.bases:
        if is_external(b):
            continue
        parent = resolve(info, classes, imports, b)
        if parent is None:
            unresolved = unresolved or (b or "<dynamic base>")
            continue
        pd, pn, pv, pu = chain(parent, classes, imports, seen)
        defines |= pd
        op_names = op_names or pn
        op_values = op_values or pv
        unresolved = unresolved or pu
    return defines, op_names, op_values, unresolved


def main(argv):
    verbose = "-v" in argv
    dirs = [a for a in argv[1:] if not a.startswith("-")] or DEFAULT_DIRS
    paths = []
    for d in dirs:
        paths.extend(sorted(glob.glob(os.path.join(d, "*.py"))))
    if not paths:
        print("no python files found in: %s" % ", ".join(dirs))
        return 2

    classes, imports, methods = collect(paths)
    resolve_setattr_params(classes, methods)
    findings = []
    skipped = []
    partial = []
    checked = 0

    for key in sorted(classes):
        info = classes[key]
        defines, op_names, op_values, unresolved = chain(info, classes, imports)
        if unresolved:
            skipped.append((info, "unresolved base %s" % unresolved))
            continue
        if op_values:
            skipped.append((info, "setattr(self, ...) may store a callable"))
            continue
        if op_names:
            # Calls stay sound -- no setattr here can install a method -- but a
            # read of a name only setattr knows would be a false positive.
            partial.append((info, "calls only: a setattr name is not static"))
        checked += 1
        seen = set()
        for attr, lineno, is_call in info.uses:
            if not attr.startswith("_") or attr.startswith("__"):
                continue
            if attr in defines or (attr, lineno) in seen:
                continue
            if op_names and not is_call:
                continue
            seen.add((attr, lineno))
            findings.append((info.module, lineno, info.name, attr, is_call))

    for module, lineno, cls, attr, is_call in sorted(findings):
        print("%s:%d  %s.self.%s%s  -- %s never defined on %s or its bases"
              % (module, lineno, cls, attr, "()" if is_call else "",
                 "method" if is_call else "attribute", cls))

    # Every class in the tree is currently checkable, so a skip is a REGRESSION,
    # not a fact of life -- and it fails the gate. Without this the audit
    # degrades to blind silently: renaming the setattr helper in illustrator
    # makes that class unsound, which would otherwise turn a would-be finding
    # into a quiet skip and still exit 0.
    for info, why in skipped:
        print("%s:%d  class %s is no longer checkable -- %s"
              % (info.module, info.lineno, info.name, why))

    print("")
    print("%d classes checked (%d for calls only), %d skipped, %d finding(s)"
          % (checked, len(partial), len(skipped), len(findings)))
    if verbose:
        for info, why in partial:
            print("  part %s.%s: %s" % (info.module, info.name, why))
    if findings or skipped:
        return 1
    print("CLEAN: no undefined self attributes, every class checked")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
