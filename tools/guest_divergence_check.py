#!/usr/bin/env python3
"""Find defects that only appear on the GUEST, never on this host.

    python3 tools/guest_divergence_check.py            # all checks
    python3 tools/guest_divergence_check.py --check fonts
    python3 tools/guest_divergence_check.py -v         # list what PASSED too

WHY THIS EXISTS
---------------
Every other check in this tree runs against the machine it is typed on: Debian,
Python 3.13, 225 font families, a full /usr/bin. The machine the user boots is a
busybox rootfs with Python 3.11, 22 font families, and only the binaries
buildroot was told to include. A whole class of defect therefore passes every
gate here and fails on the only machine that matters.

The one thing that makes this checkable without booting: the target's own
interpreter is an x86-64 ELF that RUNS ON THIS HOST when pointed at the target's
loader and PYTHONHOME. So `--guest`-tagged checks are not simulations — they are
the guest's real CPython 3.11, its real GTK, its real Pango, its real fonts,
answering for themselves.

CHECKS
------
  imports   every `import X` in the overlay resolves in the target's own
            stdlib / site-packages, and every gi.repository namespace has a
            typelib in the image.
  binaries  argv[0] of every subprocess/os.system call exists under the image's
            bin/sbin/usr/bin/usr/sbin/usr/libexec, and says whether a missing
            one is guarded (shutil.which / try) or surfaces to the user.
  fonts     every font-family named in CSS, in cairo select_font_face and in a
            Pango.FontDescription resolves under the GUEST font set.
  toyfont   THE SUBTLE ONE. cairo's toy text API (select_font_face + show_text)
            binds ONE FreeType face and does NO per-character fallback, so every
            character that face lacks is drawn as an empty box. The host's DejaVu
            + 225 families hide this; the guest's 22 do not. Cross-references
            every toy call site against all 17 shipped language catalogs.
  paths     absolute /etc /usr /opt paths that exist here by accident.
  pixbuf    gdk-pixbuf's loaders.cache agrees with the loaders actually on disk.
  compile   [--guest] every shipped .py byte-compiles under the target's 3.11.
  css       [--guest] every CSS block parses under the target's own GTK.
  encoding  [--guest] the guest boots with no LANG at all; confirm its default
            text encoding is still UTF-8, so a bare open() can read the 17
            language catalogs.

A CHECK THAT LIES IS WORSE THAN NO CHECK. Two traps are hard-coded around:
fontconfig silently ignores a relative FONTCONFIG_FILE and answers from the
host's 225 families, so this passes an absolute path; and a `guest` check that
cannot find the target interpreter reports SKIP, never a pass.
"""
import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay")
DE = os.path.join(OVERLAY, "opt/notebook/de")
TARGET = os.path.join(REPO, "buildroot/output/target")
GUEST_FC = os.path.join(REPO, "tools/guest-fonts.conf")
PY_LIB = os.path.join(TARGET, "usr/lib/python3.11")
BIN_DIRS = ["bin", "sbin", "usr/bin", "usr/sbin", "usr/libexec"]

# Modules built INTO the target interpreter, so they have no file to find.
BUILTIN = {
    "sys", "builtins", "_thread", "time", "errno", "itertools", "gc", "marshal",
    "posix", "_ast", "atexit", "faulthandler", "_codecs", "_collections",
    "_functools", "_imp", "_io", "_locale", "_operator", "_signal", "_sre",
    "_stat", "_string", "_symtable", "_tracemalloc", "_warnings", "_weakref",
    "_abc",
}
GENERIC_FAMILIES = {
    "sans-serif", "serif", "monospace", "sans", "mono", "cursive", "fantasy",
    "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "inherit",
    "initial", "unset",
}


class Report:
    """Findings, ranked. FAIL is a defect a user would meet; WARN is a
    degradation that is caught and handled; SKIP is a check that could not
    honestly run."""

    def __init__(self, verbose=False):
        self.rows = []
        self.verbose = verbose

    def add(self, level, check, what, detail=""):
        self.rows.append((level, check, what, detail))

    def fail(self, c, w, d=""):
        self.add("FAIL", c, w, d)

    def warn(self, c, w, d=""):
        self.add("WARN", c, w, d)

    def ok(self, c, w, d=""):
        self.add("OK", c, w, d)

    def skip(self, c, w, d=""):
        self.add("SKIP", c, w, d)

    def render(self):
        order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "OK": 3}
        shown = [r for r in self.rows
                 if self.verbose or r[0] in ("FAIL", "WARN", "SKIP")]
        shown.sort(key=lambda r: (order[r[0]], r[1]))
        for level, check, what, detail in shown:
            print("%-5s %-9s %s" % (level, check, what))
            for line in (detail.splitlines() if detail else []):
                print("               %s" % line)
        nf = sum(1 for r in self.rows if r[0] == "FAIL")
        nw = sum(1 for r in self.rows if r[0] == "WARN")
        ns = sum(1 for r in self.rows if r[0] == "SKIP")
        print("\nGUEST DIVERGENCE: %d fail, %d warn, %d skipped, %d checks"
              % (nf, nw, ns, len(self.rows)))
        return nf


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
def overlay_py():
    """Every Python file that ships, as (relative_name, absolute_path)."""
    out = []
    for dirpath, _dirs, files in os.walk(OVERLAY):
        for f in sorted(files):
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                out.append((os.path.relpath(p, OVERLAY), p))
    return sorted(out)


def parse(path):
    try:
        return ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return None


def guest_python():
    """(argv_prefix, env) for running the TARGET's own interpreter on this
    host, or (None, None) if the image has not been built. The target ELF asks
    for /lib64/ld-linux, which is the host's; invoking the target's loader
    explicitly and pinning LD_LIBRARY_PATH + PYTHONHOME keeps every library and
    every stdlib module coming from the image instead of from Debian."""
    ld = os.path.join(TARGET, "lib/ld-linux-x86-64.so.2")
    py = os.path.join(TARGET, "usr/bin/python3.11")
    if not (os.path.exists(ld) and os.path.exists(py)):
        return None, None
    env = {
        "HOME": "/tmp", "NB_HOME": "/tmp/nb-divergence",
        "LD_LIBRARY_PATH": "%s/lib:%s/usr/lib" % (TARGET, TARGET),
        "PYTHONHOME": os.path.join(TARGET, "usr"),
        "PYTHONPATH": DE,
        "GI_TYPELIB_PATH": os.path.join(TARGET, "usr/lib/girepository-1.0"),
        "XDG_DATA_DIRS": os.path.join(TARGET, "usr/share"),
        # absolute, always: fontconfig ignores a relative FONTCONFIG_FILE and
        # answers from the host's font set, which would make every font answer
        # below a lie in both directions.
        "FONTCONFIG_FILE": GUEST_FC,
        "DISPLAY": os.environ.get("DISPLAY", ":0"),
        "XAUTHORITY": os.environ.get(
            "XAUTHORITY", os.path.expanduser("~/.Xauthority")),
    }
    return [ld, py], env


def run_on_guest(script, rep, check):
    """Run `script` under the target interpreter; return stdout or None."""
    argv, env = guest_python()
    if argv is None:
        rep.skip(check, "target image not built (no output/target/usr/bin/python3.11)")
        return None
    p = subprocess.run(argv + ["-c", script], capture_output=True, text=True,
                       env=env, timeout=300)
    if p.returncode != 0:
        rep.skip(check, "guest interpreter run failed",
                 (p.stderr or "").strip()[:600])
        return None
    return p.stdout


# --------------------------------------------------------------------------
# 1. imports
# --------------------------------------------------------------------------
def guest_modules():
    """Every top-level module name importable on the guest."""
    mods = set(BUILTIN)
    site = os.path.join(PY_LIB, "site-packages")
    for base in (PY_LIB, site):
        if not os.path.isdir(base):
            continue
        for n in os.listdir(base):
            p = os.path.join(base, n)
            if n.endswith(".py"):
                mods.add(n[:-3])
            elif n.endswith(".so"):
                mods.add(n.split(".")[0])
            elif os.path.isdir(p) and os.path.exists(os.path.join(p, "__init__.py")):
                mods.add(n)
    dyn = os.path.join(PY_LIB, "lib-dynload")
    if os.path.isdir(dyn):
        for n in os.listdir(dyn):
            mods.add(n.split(".")[0])
    mods |= {f[:-3] for f in os.listdir(DE) if f.endswith(".py")}
    return mods


def check_imports(rep):
    if not os.path.isdir(PY_LIB):
        rep.skip("imports", "no %s — image not built" % PY_LIB)
        return
    mods = guest_modules()
    typelibs = set()
    tl = os.path.join(TARGET, "usr/lib/girepository-1.0")
    if os.path.isdir(tl):
        typelibs = {x.split("-")[0] for x in os.listdir(tl)}

    missing_mod, missing_ns = {}, {}
    for rel, path in overlay_py():
        tree = parse(path)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    top = a.name.split(".")[0]
                    if top not in mods:
                        missing_mod.setdefault(a.name, []).append("%s:%d" % (rel, n.lineno))
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                if n.module == "gi.repository":
                    for a in n.names:
                        if typelibs and a.name not in typelibs:
                            missing_ns.setdefault(a.name, []).append("%s:%d" % (rel, n.lineno))
                elif n.module.split(".")[0] not in mods:
                    missing_mod.setdefault(n.module, []).append("%s:%d" % (rel, n.lineno))
            elif isinstance(n, ast.Call):
                f = n.func
                if (isinstance(f, ast.Attribute) and f.attr == "require_version"
                        and n.args and isinstance(n.args[0], ast.Constant)):
                    ns = n.args[0].value
                    if typelibs and ns not in typelibs:
                        missing_ns.setdefault(ns, []).append("%s:%d" % (rel, n.lineno))

    for name, where in sorted(missing_mod.items()):
        rep.fail("imports", "module %r is not in the target image" % name,
                 "imported at " + ", ".join(where[:6]))
    for ns, where in sorted(missing_ns.items()):
        rep.fail("imports", "gi namespace %r has no typelib in the image" % ns,
                 "used at " + ", ".join(where[:6]))
    if not missing_mod and not missing_ns:
        rep.ok("imports", "every import and gi namespace resolves on the guest")


# --------------------------------------------------------------------------
# 2. binaries
# --------------------------------------------------------------------------
SUBPROC_FUNCS = {"run", "Popen", "call", "check_call", "check_output", "system",
                 "spawnv", "spawnvp", "spawnl", "spawnlp", "getoutput",
                 "getstatusoutput"}


def _str_const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}"
                       for v in node.values)
    return None


def check_binaries(rep):
    present = set()
    for d in BIN_DIRS:
        p = os.path.join(TARGET, d)
        if os.path.isdir(p):
            present |= set(os.listdir(p))
    if not present:
        rep.skip("binaries", "image not built — no bin dirs under output/target")
        return

    missing, whichd = {}, {}
    for rel, path in overlay_py():
        tree = parse(path)
        if tree is None:
            continue
        # Several modules carry their own `have("x")` / `_have("x")` probe
        # (settings.py runs `command -v`, nbprint/sequencer wrap shutil.which).
        # A command tested that way is guarded even though the call itself is
        # not inside a try — treat it as such, or the check cries wolf on the
        # one pattern the code uses correctly.
        probed = set()
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and n.args
                    and isinstance(n.func, ast.Name)
                    and n.func.id in ("have", "_have")):
                c = _str_const(n.args[0])
                if c:
                    probed.add(c)
        # map each node to its enclosing Try, so we can say whether a missing
        # binary degrades quietly or reaches the user as an error.
        guarded = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Try):
                for stmt in n.body:
                    for sub in ast.walk(stmt):
                        guarded.add(id(sub))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            # shutil.which("x") is the safe pattern; record separately.
            if fname == "which" and n.args:
                c = _str_const(n.args[0])
                if c and os.path.basename(c) not in present:
                    whichd.setdefault(c, []).append("%s:%d" % (rel, n.lineno))
                continue
            if fname not in SUBPROC_FUNCS or not n.args:
                continue
            a0 = n.args[0]
            if isinstance(a0, (ast.List, ast.Tuple)):
                argv = [_str_const(e) or "<expr>" for e in a0.elts]
            else:
                c = _str_const(a0)
                argv = c.split() if c else ["<expr>"]
            if not argv or argv[0] == "<expr>":
                continue
            cmd = argv[0]
            if cmd in ("sh", "bash", "/bin/sh", "/bin/bash"):
                for i, a in enumerate(argv):
                    if a == "-c" and i + 1 < len(argv):
                        inner = argv[i + 1].split()
                        if inner:
                            cmd = inner[0]
                        break
            if cmd == "<expr>" or os.path.basename(cmd) in present:
                continue
            how = ("guarded" if (id(n) in guarded or cmd in probed)
                   else "UNGUARDED")
            missing.setdefault(cmd, []).append((rel, n.lineno, how))

    for cmd, sites in sorted(missing.items()):
        hard = [s for s in sites if s[2] == "UNGUARDED"]
        detail = "\n".join("%s:%d (%s)" % s for s in sites[:8])
        if hard:
            rep.fail("binaries", "%r is invoked but is not in the image" % cmd, detail)
        else:
            rep.warn("binaries",
                     "%r is not in the image (every call is exception-guarded)" % cmd,
                     detail)
    for cmd, sites in sorted(whichd.items()):
        rep.warn("binaries",
                 "shutil.which(%r) can never succeed — not in the image" % cmd,
                 "the feature behind it is permanently off: " + ", ".join(sites[:6]))
    if not missing and not whichd:
        rep.ok("binaries", "every invoked binary is in the image")


# --------------------------------------------------------------------------
# 3 + 4. fonts, and the cairo toy-font API
# --------------------------------------------------------------------------
def guest_families():
    """Font families the GUEST ships, via fontconfig pointed at the image."""
    if not os.path.exists(GUEST_FC):
        return None
    env = dict(os.environ, FONTCONFIG_FILE=GUEST_FC)  # absolute, always
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True,
                             text=True, env=env, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    fams = set()
    for line in out.splitlines():
        for f in line.split(","):
            if f.strip():
                fams.add(f.strip())
    return fams or None


FAMILY_RE = re.compile(r'font-family\s*:\s*([^;}]*)[;}]')
TOY_FACE_RE = re.compile(r'select_font_face\(\s*["\']([^"\']+)["\']')
PANGO_RE = re.compile(r'Pango\.FontDescription(?:\.from_string)?\(\s*["\']([^"\']+)["\']')
_PANGO_STYLE = re.compile(
    r'\s+(Bold|Italic|Light|Regular|Medium|Condensed|Oblique|Semi-?Bold|'
    r'Thin|Black|Heavy)\b', re.I)


def check_fonts(rep):
    fams = guest_families()
    if fams is None:
        rep.skip("fonts", "fc-list unavailable, or tools/guest-fonts.conf missing")
        return
    low = {f.lower() for f in fams}
    unresolved = {}

    def note(name, where):
        name = name.strip().strip('"\'').strip()
        if not name or name.lower() in GENERIC_FAMILIES:
            return
        if name.lower() not in low:
            unresolved.setdefault(name, []).append(where)

    sources = list(overlay_py())
    for dirpath, _d, files in os.walk(OVERLAY):
        for f in files:
            if f.endswith(".css"):
                p = os.path.join(dirpath, f)
                sources.append((os.path.relpath(p, OVERLAY), p))
    for rel, path in sources:
        src = open(path, encoding="utf-8", errors="replace").read()
        for m in FAMILY_RE.finditer(src):
            ln = src[:m.start()].count("\n") + 1
            for part in m.group(1).split(","):
                note(part, "%s:%d" % (rel, ln))
        for m in TOY_FACE_RE.finditer(src):
            note(m.group(1), "%s:%d [cairo]" % (rel, src[:m.start()].count("\n") + 1))
        for m in PANGO_RE.finditer(src):
            name = _PANGO_STYLE.sub("", m.group(1))
            name = re.sub(r'\s+\d+(\.\d+)?$', "", name).strip()
            for part in name.split(","):
                note(part, "%s:%d [pango]" % (rel, src[:m.start()].count("\n") + 1))

    # A name absent from the image is only a defect if fontconfig does not
    # alias it onto something shipped. 99-notebookos.conf does exactly that for
    # Helvetica / Newsreader / Courier New, so ASK fontconfig rather than
    # assuming — and report what it actually answers.
    env = dict(os.environ, FONTCONFIG_FILE=GUEST_FC)
    for name, where in sorted(unresolved.items(), key=lambda kv: -len(kv[1])):
        try:
            out = subprocess.run(["fc-match", name], capture_output=True,
                                 text=True, env=env, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        got = out.split('"')[1] if '"' in out else "?"
        if got and got.lower() in low and got.lower() != "dejavu sans":
            rep.ok("fonts", "%r is not shipped but fontconfig aliases it to %r"
                   % (name, got), "%d references" % len(where))
        else:
            rep.fail("fonts",
                     "%r is not in the image and falls back to %r" % (name, got),
                     "%d references, e.g. %s" % (len(where), ", ".join(where[:4])))
    if not unresolved:
        rep.ok("fonts", "every named family is shipped")


# %-format specifiers that can only ever produce ASCII, whatever the argument.
_NUMERIC_SPECS = set("diouxXeEfFgGc%")
_SPEC_RE = re.compile(r'%[-#0 +]*[\d*]*(?:\.[\d*]+)?([a-zA-Z%])')
# Calls whose result is always ASCII regardless of what goes in.
_ASCII_CALLS = {"int", "len", "round", "abs", "hex", "oct", "id", "ord"}


def _ascii_safe(node, assigns, depth=0):
    """True when `node` can only ever evaluate to ASCII text.

    Numbers dressed as strings ("Page %d", str(int(x)), bar counters) are the
    bulk of cairo's toy-font call sites and are perfectly safe on the guest. The
    dangerous ones are the rest: a _t() UI string in the active language, or
    something the user typed. Distinguishing them is what keeps this check from
    crying wolf on every page number in the tree."""
    if depth > 6 or node is None:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and node.value.isascii()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        tmpl = node.left
        if not (isinstance(tmpl, ast.Constant) and isinstance(tmpl.value, str)
                and tmpl.value.isascii()):
            return False
        # "%d" is safe whatever the argument; "%s" is only as safe as the value.
        return all(s in _NUMERIC_SPECS for s in _SPEC_RE.findall(tmpl.value))
    if isinstance(node, ast.JoinedStr):
        return all(_ascii_safe(v, assigns, depth + 1) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _ascii_safe(node.value, assigns, depth + 1)
    if isinstance(node, ast.IfExp):
        return (_ascii_safe(node.body, assigns, depth + 1)
                and _ascii_safe(node.orelse, assigns, depth + 1))
    if isinstance(node, ast.BoolOp):
        return all(_ascii_safe(v, assigns, depth + 1) for v in node.values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _ASCII_CALLS:
            return True
        if node.func.id == "str" and node.args:
            return _ascii_safe(node.args[0], assigns, depth + 1) or isinstance(
                node.args[0], ast.Call) and isinstance(
                node.args[0].func, ast.Name) and node.args[0].func.id in _ASCII_CALLS
    if isinstance(node, ast.Name):
        vals = assigns.get(node.id)
        if vals:
            return all(_ascii_safe(v, assigns, depth + 1) for v in vals)
    return False


def _toy_sites():
    """Every cairo toy-font drawing site that can draw non-ASCII text, as
    (rel, line, family, text_src, literal_chars)."""
    sites = []
    for rel, path in overlay_py():
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = parse(path)
        if tree is None:
            continue
        lines = src.splitlines()
        # the most recent select_font_face family textually above each show_text
        faces = [(src[:m.start()].count("\n") + 1, m.group(1))
                 for m in TOY_FACE_RE.finditer(src)]
        # local assignments, per enclosing function, so a name like `label` that
        # is only ever set to "Muted" or "%d%%" is recognised as ASCII-only.
        assigns = {}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for st in ast.walk(fn):
                if isinstance(st, ast.Assign):
                    for tgt in st.targets:
                        if isinstance(tgt, ast.Name):
                            assigns.setdefault(tgt.id, []).append(st.value)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "show_text" and n.args):
                continue
            arg = n.args[0]
            if _ascii_safe(arg, assigns):
                continue
            fam = "?"
            for ln, f in faces:
                if ln <= n.lineno:
                    fam = f
            try:
                text_src = ast.unparse(arg)
            except Exception:
                text_src = lines[n.lineno - 1].strip() if n.lineno <= len(lines) else "?"
            lit = ""
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                lit = "".join(sorted({c for c in arg.value if not c.isascii()}))
            sites.append((rel, n.lineno, fam, text_src[:70], lit))
    return sites


def check_toyfont(rep):
    """cairo's toy API binds ONE face and does no fallback. Any script that face
    lacks is drawn as an empty box — the defect is invisible here because the
    host's fallback chain is 225 families deep and the guest's is 22."""
    fams = guest_families()
    if fams is None:
        rep.skip("toyfont", "fontconfig unavailable — cannot judge glyph coverage")
        return
    sites = _toy_sites()
    if not sites:
        rep.ok("toyfont", "no cairo toy-font site draws non-literal text")
        return
    catalogs = sorted(glob.glob(os.path.join(DE, "lang_*.json")))
    if not catalogs:
        rep.skip("toyfont", "no lang_*.json catalogs to test coverage against")
        return

    families = sorted({s[2] for s in sites})
    probe = {}
    for path in catalogs:
        code = os.path.basename(path)[5:-5]
        try:
            cat = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        chars = set()
        for v in cat.values():
            if isinstance(v, str):
                chars |= {c for c in v if not c.isascii()}
        if chars:
            probe[code] = "".join(sorted(chars))
    # Characters written straight into the source at a toy call site (the ©
    # in Maps' attribution, a bullet, a curly quote) are checked literally.
    probe["#literals"] = "".join(sorted({c for s in sites for c in s[4]})) or "A"

    script = (
        "import cairo, json\n"
        "fams = %r\nprobe = json.loads(%r)\n"
        "surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)\n"
        "cr = cairo.Context(surf)\nout = {}\n"
        "for fam in fams:\n"
        "    cr.select_font_face(fam, 0, 0); cr.set_font_size(16)\n"
        "    sf = cr.get_scaled_font()\n"
        "    for code, s in probe.items():\n"
        "        miss = ''.join(c for c in s\n"
        "                       if sf.text_to_glyphs(0, 0, c, True)[0][0][0] == 0)\n"
        "        if miss:\n"
        "            out.setdefault(fam, {})[code] = [miss, len(s)]\n"
        "print(json.dumps(out))\n" % (families, json.dumps(probe))
    )
    raw = run_on_guest(script, rep, "toyfont")
    if raw is None:
        # No guest interpreter: fall back to this host's cairo pointed at the
        # guest font tree. Same fontconfig answer, so the verdict still holds.
        env = dict(os.environ, FONTCONFIG_FILE=GUEST_FC)
        p = subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, env=env, timeout=120)
        if p.returncode != 0:
            rep.skip("toyfont", "cairo unavailable on host and guest")
            return
        raw = p.stdout
        rep.rows = [r for r in rep.rows
                    if not (r[0] == "SKIP" and r[1] == "toyfont")]
    try:
        broken = json.loads(raw.strip().splitlines()[-1])
    except (ValueError, IndexError):
        rep.skip("toyfont", "could not read glyph-coverage result")
        return

    # A face missing a stray symbol is a blemish; a face missing most of a
    # language's letters means that language is not rendered AT ALL. Rank them
    # apart, or the second drowns in the first.
    DEAD = 0.5
    by_file, symbols = {}, {}
    for rel, line, fam, text, lit in sites:
        langs = broken.get(fam) or {}
        dead = sorted(c for c, (miss, tot) in langs.items()
                      if c != "#literals" and tot and len(miss) / tot > DEAD)
        lit_missing = "".join(c for c in lit if c in
                              (langs.get("#literals") or ("", 0))[0])
        if dead or lit_missing:
            by_file.setdefault(rel, []).append((line, fam, text, dead, lit_missing))
        for c, (miss, _tot) in langs.items():
            if c != "#literals" and miss and c not in dead:
                symbols.setdefault(fam, set()).update(miss)

    for rel, entries in sorted(by_file.items()):
        dead = sorted({c for _l, _f, _t, d, _x in entries for c in d})
        lits = "".join(sorted({c for _l, _f, _t, _d, x in entries for c in x}))
        detail = "\n".join("line %d  face %r  draws %s" % (l, f, t)
                           for l, f, t, _d, _x in entries[:8])
        if dead:
            detail += ("\n-> the face carries almost none of these scripts, so "
                       "the text is drawn as empty boxes in: %s" % ", ".join(dead))
        if lits:
            detail += "\n-> literal character(s) with no glyph in that face: %s" % lits
        detail += ("\n-> cairo's toy API binds ONE face and does no per-character "
                   "fallback. Draw through PangoCairo (nbprint.PdfText) instead.")
        rep.fail("toyfont",
                 "%s draws translated/user text with cairo's toy font API" % rel,
                 detail)
    for fam, chars in sorted(symbols.items()):
        rep.warn("toyfont",
                 "face %r has no glyph for %s" % (fam, "".join(sorted(chars))),
                 "these appear in the shipped UI catalogs; anywhere they reach "
                 "a toy-font draw they show as empty boxes (Pango falls back "
                 "to DejaVu for them, cairo's toy API cannot)")
    if not by_file and not symbols:
        rep.ok("toyfont", "every toy-font face covers all shipped languages")


# --------------------------------------------------------------------------
# 5. paths
# --------------------------------------------------------------------------
PATH_RE = re.compile(r'["\'](/(?:etc|usr|opt|lib|srv)/[A-Za-z0-9_./+-]+)["\']')


def check_paths(rep):
    if not os.path.isdir(TARGET):
        rep.skip("paths", "image not built")
        return
    hits = {}
    for rel, path in overlay_py():
        src = open(path, encoding="utf-8", errors="replace").read()
        for m in PATH_RE.finditer(src):
            ln = src[:m.start()].count("\n") + 1
            hits.setdefault(m.group(1), []).append("%s:%d" % (rel, ln))
    bad = 0
    for p, where in sorted(hits.items()):
        if os.path.exists(os.path.join(TARGET, p.lstrip("/"))):
            continue
        # A path absent from the image is only interesting if it exists HERE —
        # that is precisely the accident this check is for.
        if not os.path.exists(p):
            continue
        bad += 1
        rep.warn("paths", "%s exists on this host but NOT in the image" % p,
                 ", ".join(where[:6]))
    if not bad:
        rep.ok("paths", "no code depends on a path that only this host has")


# --------------------------------------------------------------------------
# 6. gdk-pixbuf loaders
# --------------------------------------------------------------------------
def check_pixbuf(rep):
    base = os.path.join(TARGET, "usr/lib/gdk-pixbuf-2.0/2.10.0")
    cache = os.path.join(base, "loaders.cache")
    ldir = os.path.join(base, "loaders")
    if not (os.path.exists(cache) and os.path.isdir(ldir)):
        rep.skip("pixbuf", "no gdk-pixbuf loaders.cache in the image")
        return
    text = open(cache, encoding="utf-8", errors="replace").read()
    referenced = set(re.findall(r'"(/.*?/(libpixbufloader-[^/"]+\.so))"', text))
    ref_names = {n for _p, n in referenced}
    on_disk = {f for f in os.listdir(ldir) if f.startswith("libpixbufloader-")}
    for name in sorted(ref_names - on_disk):
        rep.fail("pixbuf",
                 "loaders.cache references %s, which is NOT in the image" % name,
                 "every load of that format fails with "
                 "'Unable to load image-loading module'; the cache is stale "
                 "(re-run gdk-pixbuf-query-loaders in post-build.sh)")
    for name in sorted(on_disk - ref_names):
        rep.warn("pixbuf",
                 "%s ships but is NOT registered in loaders.cache" % name,
                 "that format is unusable even though its loader is present; "
                 "the cache is stale")
    if not (ref_names - on_disk) and not (on_disk - ref_names):
        rep.ok("pixbuf", "loaders.cache matches the loaders on disk")


# --------------------------------------------------------------------------
# 7 + 8. guest-interpreter checks
# --------------------------------------------------------------------------
def check_compile(rep):
    """Byte-compile every shipped .py with the TARGET's own 3.11. This host is
    3.13, whose parser accepts syntax 3.11 rejects."""
    script = (
        "import os, py_compile, tempfile, sys\n"
        "bad = []\n"
        "for dp, _dn, fn in os.walk(%r):\n"
        "    for f in fn:\n"
        "        if not f.endswith('.py'): continue\n"
        "        p = os.path.join(dp, f)\n"
        "        try:\n"
        "            py_compile.compile(p, cfile=os.path.join(tempfile.gettempdir(),"
        " 'gd.pyc'), doraise=True)\n"
        "        except Exception as e:\n"
        "            bad.append('%%s: %%s' %% (p, e))\n"
        "print('VERSION', sys.version.split()[0])\n"
        "print('\\n'.join(bad))\n" % OVERLAY
    )
    out = run_on_guest(script, rep, "compile")
    if out is None:
        return
    lines = [l for l in out.splitlines() if l.strip()]
    ver = lines[0].split()[1] if lines and lines[0].startswith("VERSION") else "?"
    bad = lines[1:]
    if bad:
        rep.fail("compile", "%d file(s) do not compile under guest Python %s"
                 % (len(bad), ver), "\n".join(bad[:10]))
    else:
        rep.ok("compile", "every shipped .py compiles under guest Python %s" % ver)


def check_encoding(rep):
    """The guest's default TEXT ENCODING, with the environment it really boots
    with. /etc/profile sets PATH and nothing else — no LANG, no LC_ALL — while
    this host always has a UTF-8 locale exported. `open(path)` with no explicit
    encoding uses locale.getpreferredencoding(), so if the guest resolved that
    to ASCII, every `json.load(open(lang_ja.json))` in the tree would raise
    UnicodeDecodeError and 16 of the 17 languages would fail to load at all.
    CPython's PEP 538 C-locale coercion is what saves it — verify, never
    assume, because the failure mode is the whole product in one file."""
    catalogs = sorted(glob.glob(os.path.join(DE, "lang_*.json")))
    script = (
        "import locale, sys, json, glob\n"
        "print('ENC', locale.getpreferredencoding(False),"
        " sys.getfilesystemencoding(), locale.setlocale(locale.LC_CTYPE))\n"
        "bad = []\n"
        "for p in %r:\n"
        "    try: json.load(open(p))\n"
        "    except Exception as e: bad.append('%%s: %%r' %% (p, e))\n"
        "print('\\n'.join(bad))\n" % catalogs
    )
    argv, env = guest_python()
    if argv is None:
        rep.skip("encoding", "target image not built")
        return
    # env -i: reproduce the guest's own bare environment. LANG must NOT leak in
    # from this shell, or the check answers for the wrong machine.
    bare = {k: env[k] for k in ("LD_LIBRARY_PATH", "PYTHONHOME")}
    p = subprocess.run(argv + ["-c", script], capture_output=True, text=True,
                       env=bare, timeout=120)
    if p.returncode != 0:
        rep.skip("encoding", "guest interpreter run failed",
                 (p.stderr or "").strip()[:400])
        return
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    head = lines[0] if lines else ""
    bad = [l for l in lines[1:]]
    _tag, preferred, fsenc, ctype = (head.split() + ["?"] * 4)[:4]
    if "utf-8" not in preferred.lower():
        rep.fail("encoding",
                 "guest default text encoding is %r, not UTF-8" % preferred,
                 "every bare open() of a UTF-8 file (all 17 lang_*.json) will "
                 "raise UnicodeDecodeError; /etc/profile exports no LANG")
    elif bad:
        rep.fail("encoding", "%d language catalog(s) fail to load on the guest"
                 % len(bad), "\n".join(bad[:6]))
    else:
        rep.ok("encoding",
               "guest reads UTF-8 with no LANG set (preferred=%s fs=%s ctype=%s); "
               "all %d catalogs load" % (preferred, fsenc, ctype, len(catalogs)))


def check_css(rep):
    """Parse every CSS block with the GUEST's own GTK. A property the guest's
    older GTK does not know is dropped silently — the widget just looks wrong."""
    script = (
        "import ast, os, re, sys\n"
        "import gi; gi.require_version('Gtk','3.0')\n"
        "from gi.repository import Gtk, GLib\n"
        "OVL = %r\n"
        "def defuse(v):\n"
        "    v = re.sub(r'%%\\((\\w+)\\)([sdifgx])', lambda m: '0' if m.group(2)"
        " in 'difgx' else '#000000', v)\n"
        "    v = re.sub(r'%%[0-9.]*[difgx]', '0', v)\n"
        "    v = re.sub(r'%%[0-9.]*s', '#000000', v)\n"
        "    return re.sub(r'\\{[a-zA-Z_]\\w*\\}', '#000000', v)\n"
        "KEYS = ('color:','background','font-','padding','margin','border',\n"
        "        'min-width','min-height','box-shadow','opacity')\n"
        "# a real rule body: a brace holding at least one `prop: value;`.\n"
        "# Prose full of braces (an ffmpeg filter graph in a docstring) is not.\n"
        "DECL = re.compile(r'\\{[^{}]*[a-z-]+\\s*:[^{};]+;')\n"
        "blocks = []\n"
        "for dp,_dn,fn in os.walk(OVL):\n"
        "    for f in fn:\n"
        "        p = os.path.join(dp,f); rel = os.path.relpath(p,OVL)\n"
        "        if f.endswith('.css'):\n"
        "            blocks.append((rel,1,defuse(open(p,encoding='utf-8',"
        "errors='replace').read()))); continue\n"
        "        if not f.endswith('.py'): continue\n"
        "        try: tree = ast.parse(open(p,encoding='utf-8',"
        "errors='replace').read())\n"
        "        except Exception: continue\n"
        "        for n in ast.walk(tree):\n"
        "            if isinstance(n,ast.Constant) and isinstance(n.value,(str,bytes)):\n"
        "                v = n.value if isinstance(n.value,str) else "
        "n.value.decode('utf-8','replace')\n"
        "                if (len(v)>60 and any(k in v for k in KEYS)\n"
        "                        and DECL.search(defuse(v))):\n"
        "                    blocks.append((rel,n.lineno,defuse(v)))\n"
        "seen = set()\n"
        "for name,ln,text in blocks:\n"
        "    prov = Gtk.CssProvider(); msgs = []\n"
        "    prov.connect('parsing-error', lambda p,sec,err,m=msgs:"
        " m.append((sec.get_start_line(), err.message)))\n"
        "    try: prov.load_from_data(text.encode('utf-8'))\n"
        "    except GLib.Error: pass\n"
        "    for l,m in msgs:\n"
        "        k = (name,ln,l,m)\n"
        "        if k in seen: continue\n"
        "        seen.add(k)\n"
        "        print('%%s py~%%d cssline %%d: %%s' %% (name,ln,l+1,m))\n"
        "print('GTKVER %%d.%%d.%%d BLOCKS %%d' %% (Gtk.get_major_version(),"
        " Gtk.get_minor_version(), Gtk.get_micro_version(), len(blocks)))\n"
        % OVERLAY
    )
    out = run_on_guest(script, rep, "css")
    if out is None:
        return
    lines = [l for l in out.splitlines() if l.strip()]
    tail = lines[-1] if lines else ""
    errs = [l for l in lines[:-1] if not l.startswith("GTKVER")]
    if errs:
        rep.fail("css", "%d CSS parse error(s) under the guest's GTK (%s)"
                 % (len(errs), tail), "\n".join(errs[:12]))
    else:
        rep.ok("css", "every CSS block parses under the guest's GTK (%s)" % tail)


# --------------------------------------------------------------------------
CHECKS = {
    "imports": check_imports,
    "binaries": check_binaries,
    "fonts": check_fonts,
    "toyfont": check_toyfont,
    "paths": check_paths,
    "pixbuf": check_pixbuf,
    "compile": check_compile,
    "css": check_css,
    "encoding": check_encoding,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="append", choices=sorted(CHECKS),
                    help="run only this check (repeatable)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also list the checks that passed")
    args = ap.parse_args()
    rep = Report(verbose=args.verbose)
    for name in (args.check or sorted(CHECKS)):
        try:
            CHECKS[name](rep)
        except Exception as e:            # a broken check must never be silence
            rep.skip(name, "check itself raised %s: %s" % (type(e).__name__, e))
    return 1 if rep.render() else 0


if __name__ == "__main__":
    sys.exit(main())
