#!/usr/bin/env python3
"""Every external command the desktop runs must exist in the image.

Found the hard way (2026-08-14): `nbtrust` and `nbpkg_install` both verify
signatures by running `openssl`, and **the image did not contain openssl** —
not the binary, not even the libraries. On the build host the command is on
PATH, so every suite passed; on the machine the code refused every signature it
was asked about. The app-trust lockout therefore refused *every* app, and the
signed-package install path could never have worked on real hardware. The
docs said "openssl is on-device". Nobody had asked the image.

This gate asks the image. It parses each de/*.py for subprocess calls, resolving
literal argv lists, local ``cmd``/``argv`` assignments and list concatenation,
then looks for each command in the target filesystem's bin directories.

A command wrapped in `shutil.which(...)` in the same module is reported as
GUARDED rather than missing: the code already asks before running it and can
degrade (installer.py does exactly this). Everything else must be present, or
the feature it belongs to is dead on the device and green everywhere else.

Exit status is the number of unguarded missing commands.
"""
import ast
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
TARGET = os.path.join(ROOT, "buildroot/output/target")
BIN_DIRS = ("usr/bin", "bin", "usr/sbin", "sbin", "usr/libexec")

RUNNERS = {"run", "Popen", "call", "check_call", "check_output"}

# Commands that are shell built-ins or provided by busybox applets under a name
# the scan cannot see, and commands the OS deliberately runs only on the build
# host. Keep this list short and justified.
ALLOW = {
    "sh",            # busybox applet, always present
    "python3",       # the interpreter running this very code
}

SHELL_BUILTINS = {
    ".", "[", "break", "case", "cd", "command", "continue", "do", "done",
    "echo", "elif", "else", "esac", "eval", "exec", "exit", "export", "fi",
    "for", "if", "local", "printf", "read", "return", "set", "shift",
    "test", "then", "trap", "true", "false", "umask", "unset", "until",
    "wait", "while",
}


def _key(node):
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return "self." + node.attr
    return None


def _values(node, env):
    """Possible string values of a small constant expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    key = _key(node)
    if key is not None:
        return set(env.get(key, ()))
    if isinstance(node, ast.IfExp):
        return _values(node.body, env) | _values(node.orelse, env)
    if isinstance(node, ast.BoolOp):
        out = set()
        for value in node.values:
            out.update(_values(value, env))
        return out
    if (isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "which"
            and node.args):
        return _values(node.args[0], env)
    return set()


def _commands(node, env):
    """Possible argv[0] values, including variables and list concatenation."""
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _values(node.elts[0], env)
    key = _key(node)
    if key is not None:
        return set(env.get(key, ()))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # argv_prefix + dynamic_args keeps argv_prefix's first element.
        # If the prefix itself is dynamic, the right-hand option list cannot
        # be mistaken for an executable (``base + ['sset']``).
        return _commands(node.left, env)
    if isinstance(node, ast.IfExp):
        return _commands(node.body, env) | _commands(node.orelse, env)
    return set()


def _scope_nodes(scope):
    """Nodes in one lexical scope, excluding nested functions/classes."""
    out = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue
            out.append(child)
            visit(child)

    visit(scope)
    return out


def commands_in(path):
    """{command: guarded} for statically resolvable subprocess argv[0]s."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    found = {}
    scopes = [tree] + [node for node in ast.walk(tree)
                       if isinstance(node, (ast.FunctionDef,
                                            ast.AsyncFunctionDef))]
    module_env = {}
    for node in _scope_nodes(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            vals = _values(node.value, module_env) or _commands(node.value, module_env)
            for target in targets:
                key = _key(target)
                if key is not None and vals:
                    module_env.setdefault(key, set()).update(vals)
    for scope in scopes:
        nodes = _scope_nodes(scope)
        env = {} if scope is tree else dict(module_env)
        # A few fixed-point passes resolve `tool = FFMPEG; cmd = [tool, ...]`
        # without pretending to evaluate arbitrary Python.
        for _pass in range(3):
            for node in nodes:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                    targets = node.targets if isinstance(node, ast.Assign) \
                        else [node.target]
                    vals = _values(value, env) or _commands(value, env)
                    if vals:
                        for target in targets:
                            key = _key(target)
                            if key is not None:
                                env.setdefault(key, set()).update(vals)
        guarded = set()
        for node in nodes:
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "which"
                    and node.args):
                guarded.update(_values(node.args[0], env))
        for node in nodes:
            if (not isinstance(node, ast.Call)
                    or getattr(node.func, "attr", None) not in RUNNERS
                    or not node.args):
                continue
            for name in _commands(node.args[0], env):
                if "/" in name or not name:
                    continue
                is_guarded = name in guarded
                found[name] = found.get(name, True) and is_guarded
    return found


def in_image(cmd):
    for d in BIN_DIRS:
        if os.path.exists(os.path.join(TARGET, d, cmd)):
            return True
    return False


def shell_commands_in(path):
    """Simple command positions in shipped appliance shell entrypoints."""
    source = open(path, encoding="utf-8").read()
    source = re.sub(r"\\\n", " ", source)
    functions = set(re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", source))
    guarded = set(re.findall(r"\bcommand\s+-v\s+([A-Za-z0-9_.+-]+)", source))
    guarded.update(re.findall(r"\[\s+-x\s+(/[^ ]+)\s+\]", source))
    found = {}
    heredoc = None
    multiline_quote = None
    for raw in source.splitlines():
        if heredoc is not None:
            if raw.strip() == heredoc:
                heredoc = None
            continue
        if multiline_quote is not None:
            if raw.count(multiline_quote) % 2:
                multiline_quote = None
            continue
        line = raw.split("#", 1)[0].strip()
        if (not line or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(\)", line)
                or re.match(r"^[^ ]+(?:\|[^ ]+)+\)$", line)):
            continue
        marker = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", line)
        if marker:
            heredoc = marker.group(1)
        for quote in ("'", '"'):
            if line.count(quote) % 2:
                multiline_quote = quote
                break
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            continue
        segments, segment = [], []
        for token in tokens:
            if token in {";", "&&", "||", "|"}:
                if segment:
                    segments.append(segment)
                segment = []
            else:
                segment.append(token)
        if segment:
            segments.append(segment)
        for words in segments:
            while words and (words[0] in {"if", "then", "elif", "else", "do", "!"}
                             or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
                words.pop(0)
            if not words:
                continue
            cmd = words[0]
            if (cmd in SHELL_BUILTINS or cmd in functions or cmd.startswith("$")
                    or cmd.startswith("(") or cmd.endswith(")")
                    or cmd in {"{", "}"}
                    or any(ch in cmd for ch in "*?[")
                    or cmd.isupper()
                    or not re.match(r"^(?:/[^ ]+|[A-Za-z_][A-Za-z0-9_.+-]*)$", cmd)):
                continue
            found[cmd] = found.get(cmd, True) and cmd in guarded
    return found


def shell_entrypoints():
    overlay = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay")
    for base, _dirs, files in os.walk(overlay):
        for name in files:
            path = os.path.join(base, name)
            if name.endswith(".sh") or "/etc/init.d/" in path:
                yield path


def main():
    if not os.path.isdir(TARGET):
        print("no built target tree at %s — build buildroot first" % TARGET)
        return 2
    missing, guarded_missing = {}, {}
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py"):
            continue
        for cmd, is_guarded in commands_in(os.path.join(DE, fn)).items():
            if cmd in ALLOW or in_image(cmd):
                continue
            (guarded_missing if is_guarded else missing).setdefault(cmd, []).append(fn)
    for path in shell_entrypoints():
        rel = os.path.relpath(path, ROOT)
        for cmd, is_guarded in shell_commands_in(path).items():
            if cmd.startswith("/"):
                present = os.path.exists(os.path.join(TARGET, cmd.lstrip("/")))
            else:
                present = cmd in ALLOW or in_image(cmd)
            if present:
                continue
            (guarded_missing if is_guarded else missing).setdefault(cmd, []).append(rel)

    for cmd, files in sorted(guarded_missing.items()):
        print("GUARDED  %-14s absent, but asked for with which(): %s"
              % (cmd, ", ".join(files)))
    for cmd, files in sorted(missing.items()):
        print("MISSING  %-14s runs unguarded in: %s" % (cmd, ", ".join(files)))
    print("\n%s: %d command(s) run unguarded and are not in the image"
          % ("FAIL" if missing else "OK", len(missing)))
    print("RESULT: " + ("FAIL" if missing else "PASS"))
    return len(missing)


if __name__ == "__main__":
    sys.exit(main())
