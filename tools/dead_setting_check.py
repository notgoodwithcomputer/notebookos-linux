#!/usr/bin/env python3
"""
A control that persists a value nothing acts on.

gbaemu has a Fullscreen toggle. Flipping it writes `fullscreen` to the config
file, and the config file is read back at start-up — to set the toggle's own
state. Nothing else ever reads it. The switch moves, the setting is remembered
faithfully across reboots, and the game is fullscreen either way. Its sibling
`scale` is worse: loaded, range-checked to 1..6, and read by nothing at all.

This is the quietest way for a control to lie. It is not broken — it is
*consistent*, which is exactly what makes it convincing: you set it, you come
back, and your choice is still there. The absence of any effect is the only
evidence, and on a toggle whose effect is subtle nobody notices.

Two shapes are reported:

  ROUND TRIP   every read of the key feeds the very widget whose own handler
               writes it. The value's only consumer is the control that
               produces it.
  WRITE ONLY   the key is stored, and never read outside the loader that
               validates it on the way in.

A read that feeds a DIFFERENT widget is not a finding: `self._sidebar
.set_visible(cfg["show_sidebar"])` is what applying a setting looks like. The
distinction this makes is between a value that travels somewhere and a value
that goes in a circle.

Run:
  python3 dead_setting_check.py             # the shipped de/ tree
  python3 dead_setting_check.py --de DIR    # a scratch copy, for red-proofs
  python3 dead_setting_check.py -v          # show every key and its verdict
Exit status is nonzero if any setting is dead.
"""
import ast
import os
import sys
import glob
import re
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])

# Attribute names that hold a persisted settings mapping. Reported in the
# summary so a container this misses is visible rather than silently uncovered.
#
# `opts` is deliberately NOT here. nbprint builds a local `opts` dict of CUPS
# options and hands it straight to submit_pdf; its keys are written and never
# read back by design, which read as two dead settings until the local was told
# apart from a store.
CONTAINERS = ("_settings", "settings", "_cfg", "cfg", "_prefs", "prefs",
              "_config", "config")

# Calls that mean "push this value into a widget".
SETTERS = ("set_active", "set_value", "set_text", "set_state", "set_visible",
           "set_sensitive", "set_range", "set_label", "set_current_page",
           "set_show_all", "set_expanded", "set_index", "set_selected")

# Keys whose whole job is bookkeeping, not a user-facing control.
IGNORE_KEYS = {"version", "schema", "_version", "saved", "last", "geometry"}


def attr_chain(node):
    """`self._settings` -> "_settings"; anything else -> None."""
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def const_str(node):
    return (node.value if isinstance(node, ast.Constant)
            and isinstance(node.value, str) else None)


class Scan:
    def __init__(self, module, tree):
        self.module = module
        self.tree = tree
        self.reads = collections.defaultdict(list)   # key -> [consumer or None]
        self.writes = collections.defaultdict(list)  # key -> [enclosing func]
        self.containers = set()
        self.handlers = collections.defaultdict(set)  # func name -> {widgets}
        self.setter_targets = {}                      # id(node) -> widget name
        self.func_of = {}                             # id(node) -> func name
        self.accessors = {}                           # func name -> arg index

    # -- helpers ---------------------------------------------------------
    def _index(self):
        """Map every node to its enclosing function, and every value passed to
        a widget setter to that widget's attribute name."""
        for fn in ast.walk(self.tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    self.func_of.setdefault(id(n), fn.name)
        for call in ast.walk(self.tree):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr in SETTERS:
                widget = attr_chain(f.value)
                if widget:
                    for arg in call.args:
                        for n in ast.walk(arg):
                            self.setter_targets[id(n)] = widget
            # W.connect("signal", self._handler) -- which widget owns a handler
            if (isinstance(f, ast.Attribute) and f.attr == "connect"
                    and len(call.args) >= 2):
                widget = attr_chain(f.value)
                cb = call.args[1]
                if widget and isinstance(cb, ast.Attribute) \
                        and isinstance(cb.value, ast.Name) and cb.value.id == "self":
                    self.handlers[cb.attr].add(widget)

    def _find_accessors(self):
        """Typed accessors round the container: `_cfg_int(key, default)` wraps
        `self._settings.get(key, default)` in an int() and a try.

        Without this every setting read through one looks unread. settings.py
        reads blank_timeout, kbd_delay and kbd_rate that way and all three were
        reported dead on the first run -- three false findings out of eight,
        which is more than enough to make a gate stop being believed.
        """
        for fn in ast.walk(self.tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in
                      list(fn.args.posonlyargs) + list(fn.args.args)]
            for n in ast.walk(fn):
                key_node = None
                if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load):
                    if attr_chain(n.value) in CONTAINERS:
                        key_node = n.slice
                elif isinstance(n, ast.Call):
                    f = n.func
                    if (isinstance(f, ast.Attribute) and f.attr == "get"
                            and n.args and attr_chain(f.value) in CONTAINERS):
                        key_node = n.args[0]
                if (isinstance(key_node, ast.Name) and key_node.id in params
                        and key_node.id != "self"):
                    # The index into the CALL's arguments. Drop the receiver
                    # only when there is one: nbprefs' `cfg_int(settings, key,
                    # default)` is a plain function, and the unconditional -1
                    # pointed at `settings` instead of `key`, so three keys
                    # read through it looked unread.
                    off = 1 if params and params[0] == "self" else 0
                    self.accessors[fn.name] = params.index(key_node.id) - off
                    break

    def run(self):
        self._index()
        self._find_accessors()
        for n in ast.walk(self.tree):
            # C[key] = ...   (write)
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Subscript):
                        c = attr_chain(t.value)
                        k = const_str(t.slice)
                        if c in CONTAINERS and k:
                            self.containers.add(c)
                            self.writes[k].append(self.func_of.get(id(n), ""))
            # C[key]  (read)
            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load):
                c = attr_chain(n.value)
                k = const_str(n.slice)
                if c in CONTAINERS and k:
                    self.containers.add(c)
                    self.reads[k].append(self.setter_targets.get(id(n)))
            # C.get(key, ...)  (read)
            elif isinstance(n, ast.Call):
                f = n.func
                # C.update({"key": value}) / C.update(key=value) are writes
                # just as surely as C["key"] = value. Missing this shape let a
                # control persist and reload only itself while the scanner saw
                # no producer and silently omitted the key from verdicts.
                if isinstance(f, ast.Attribute) and f.attr == "update":
                    c = attr_chain(f.value)
                    if c in CONTAINERS:
                        keys = []
                        if n.args and isinstance(n.args[0], ast.Dict):
                            keys.extend(const_str(k) for k in n.args[0].keys)
                        keys.extend(kw.arg for kw in n.keywords if kw.arg)
                        for k in keys:
                            if k:
                                self.containers.add(c)
                                self.writes[k].append(
                                    self.func_of.get(id(n), ""))
                if (isinstance(f, ast.Attribute) and f.attr == "get"
                        and n.args):
                    c = attr_chain(f.value)
                    k = const_str(n.args[0])
                    if c in CONTAINERS and k:
                        self.containers.add(c)
                        self.reads[k].append(self.setter_targets.get(id(n)))
                    elif (k and isinstance(f.value, ast.Name)
                          and (self.func_of.get(id(n), "").startswith("_load")
                               or self.func_of.get(id(n), "").startswith("load"))):
                        # Some loaders parse JSON into a local `data` mapping
                        # and return one preference instead of assigning the
                        # whole mapping to self. Illustrator's _load_recent is
                        # this shape. It is still a real persisted read.
                        self.reads[k].append(self.setter_targets.get(id(n)))
                # self._cfg_int("blank_timeout", 0) -- a read through a typed
                # accessor, which is where most settings are actually read.
                # Either `self._cfg_int(...)` or a plain `cfg_int(...)` — the
                # accessor does not stop being one for being module-level.
                fname = (f.attr if isinstance(f, ast.Attribute)
                         else f.id if isinstance(f, ast.Name) else None)
                if fname in self.accessors:
                    pos = self.accessors[fname]
                    if 0 <= pos < len(n.args):
                        k = const_str(n.args[pos])
                        if k:
                            self.reads[k].append(
                                self.setter_targets.get(id(n)))
        return self


def foreign_config(module, text):
    """Another module's settings file, named outright in this one's source.

    `nbprefs.py` opens `settings.json` — it is the module that applies screen
    blanking and key repeat at start-up, and after ROADMAP #22 it is the ONLY
    Python that reads those keys. Scanning each file alone, this checker saw
    settings.py write them and nothing read them, and reported two live
    controls as dead (blind-spot class 6, third time in this tool).

    Matched on the literal filename rather than by pooling every read in the
    DE: two apps that happen to use the same key name must still be able to
    have one of them go dead.
    """
    out = set()
    for m in re.finditer(r'["\']([a-z][a-z0-9_]*)\.json["\']', text):
        other = m.group(1)
        if other != module:
            out.add(other)
    return out


def shell_readers():
    """Keys read by the session's shell scripts.

    A settings key does not have to have a Python reader to be alive.
    session.sh reads `tz_posix` and exports TZ from it before any app starts —
    that IS the consumer, and it is the only place it could be, because an
    environment variable has to be set by the parent. Scanning Python alone,
    this checker could not tell "nothing reads it" from "the reader is not a
    file I look at", and reported a live key as dead (blind-spot class 6).

    Looking here instead of exempting the key keeps the guard sharp in both
    directions: delete the session.sh reader and the key goes dead again,
    which is exactly the bug that made ROADMAP #23 worth fixing.
    """
    keys = set()
    for path in sorted(glob.glob(os.path.join(os.path.dirname(DE), "*.sh"))):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        # `.get("tz_posix")` / `["tz_posix"]` inside the inline python, and a
        # bare quoted key for anything reaching in with a different tool.
        #
        # DOTS AND BACKSLASHES BELONG IN THE PATTERN. Settings keys are dotted
        # ("sound.volume"), and a shell reader gets at them with sed, where the
        # dot must be escaped — `"sound\.volume"`. A pattern of [a-z0-9_] matches
        # neither form, so every dotted key read from a shell script looked
        # unread: this reported sound.volume, sound.capture and sound.muted as
        # write-only while session.sh had been restoring all three at boot since
        # 559f829a. The escape is stripped so both spellings land on one key.
        for m in re.finditer(r"""["']([a-z][a-z0-9_.\\]{2,})["']""", text):
            keys.add(m.group(1).replace("\\", ""))
    return keys


SHELL_READS = None


def verdicts(scan, external=()):
    """(key, kind, detail) for every dead setting in one module.

    `external` is every key another file reads out of THIS module's settings
    file — the session shell, or a helper module like nbprefs."""
    global SHELL_READS
    if SHELL_READS is None:
        SHELL_READS = shell_readers()
    external = set(external)
    out = []
    # Widgets whose own handler writes each key.
    for key in sorted(set(scan.writes) | set(scan.reads)):
        if key in IGNORE_KEYS:
            continue
        writes, reads = scan.writes.get(key, []), scan.reads.get(key, [])
        if not writes:
            continue                       # read-only default: not this bug
        producers = set()
        for func in writes:
            producers |= scan.handlers.get(func, set())

        if not reads and (key in SHELL_READS or key in external):
            continue                       # the consumer is in another file
        if not reads:
            out.append((key, "WRITE ONLY",
                        "written in %s; never read" % ", ".join(
                            sorted({w for w in writes if w}) or {"?"})))
            continue
        if not producers:
            continue                       # no widget writes it: not a control
        if key in external:
            continue      # read outside this file, so not a round trip either
        consumers = set(reads)
        if consumers and consumers <= producers:
            out.append((key, "ROUND TRIP",
                        "every read feeds %s, the control that writes it"
                        % ", ".join(sorted(producers))))
    return out


def main(argv):
    verbose = "-v" in argv
    paths = sorted(glob.glob(os.path.join(DE, "*.py")))
    if not paths:
        print("no python files under %s" % DE)
        return 2

    # Two passes: scan every module first, THEN judge. A key is only dead if
    # nothing anywhere reads it, and the reader may be in another file.
    scans = []
    external = collections.defaultdict(set)   # module -> keys read elsewhere
    for path in paths:
        module = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            print("SYNTAX ERROR %s: %s" % (module, exc))
            return 2
        scan = Scan(module, tree).run()
        if not scan.containers:
            continue
        scans.append(scan)
        for other in foreign_config(module, text):
            external[other] |= set(scan.reads)

    findings = []
    covered = collections.Counter()
    keys_seen = 0
    for scan in scans:
        module = scan.module
        covered[module] = len(set(scan.writes) | set(scan.reads))
        keys_seen += covered[module]
        for key, kind, detail in verdicts(scan, external.get(module, ())):
            findings.append((module, key, kind, detail))
        if verbose:
            print("%-14s %s: %d key(s)"
                  % (module, "/".join(sorted(scan.containers)), covered[module]))

    for module, key, kind, detail in findings:
        print("%-12s %-11s %-18s %s" % (module, kind, key, detail))

    print("\n%d settings key(s) across %d module(s), %d dead"
          % (keys_seen, len(covered), len(findings)))
    if findings:
        print("RESULT: %d control(s) persist a value nothing acts on"
              % len(findings))
        return 1
    print("RESULT: every persisted setting reaches something")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
