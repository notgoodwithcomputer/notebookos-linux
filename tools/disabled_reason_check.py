#!/usr/bin/env python3
"""Gate the OS-wide "disabled, never absent" explanation rule.

Every launchable app is constructed in its own subprocess and private NB_HOME.
The live widget tree is moved into a Gtk.OffscreenWindow, then every Button,
MenuItem, ToolButton, Switch and ComboBox is inspected.  An insensitive control
must have a non-empty tooltip which is not merely its own name.

DEBT is an exact two-way ratchet: a finding absent from the ledger fails, and a
ledger entry absent from the findings fails as stale.  Entries include a short
reason so accepted debt can never become an unexplained number.

Run through tools/guestrun.sh (run_all_gates does this) for the guest fonts,
theme and data paths.  ``--selfcheck`` copies a real app to a scratch directory,
sabotages its first button, and proves that the normal gate reports the defect.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
SELF = os.path.abspath(__file__)

# (app file, GTK type, control name, defect) -> why it is deliberately carried.
# Keep this exact: fixed entries are failures until removed, just like additions.
DEBT = {
    # comics.py had four entries here that were never real: its layer
    # buttons are icon-only, and the gate was reading their tooltip back
    # as their own name. Removed with the fix, not with a fix to comics.
}

CONTROL_TYPES = ("Button", "MenuItem", "ToolButton", "Switch", "ComboBox")


def app_modules(module_dir=DE):
    """Use the desktop's launch table, not a second hand-maintained app list."""
    sys.path.insert(0, module_dir)
    if module_dir != DE:
        sys.path.append(DE)
    import finder
    return sorted(set(finder.APP_MODULES.values()))


def _window_class(module, Gtk, app_base=None):
    candidates = []
    for _name, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ == module.__name__ and issubclass(cls, Gtk.Window):
            candidates.append(cls)
    # Several modules also define helper Gtk.Windows.  The desktop launches the
    # nbapp.AppWindow subclass; choosing alphabetically can construct a dialog
    # instead and make a green result describe the wrong tree.
    candidates.sort(key=lambda c: (not (app_base and issubclass(c, app_base)),
                                   "dialog" in c.__name__.lower()))
    return candidates[0] if candidates else None


def _text(value):
    return " ".join(str(value or "").split())


def _control_name(widget, Gtk):
    """The control's AUTHORED label, and a name to report it by.

    These must be separate. GTK derives a widget's accessible name from its
    tooltip when nothing else names it, so an icon-only button whose reason
    lives in its tooltip reports that reason as its own name — and comparing
    the two then compares the tooltip with itself. The gate called that
    "tooltip repeats control name" and flagged the CORRECT fix, on the very
    control the fix was written for.

    Only an authored label can be repeated. Where there is none, a tooltip
    cannot be repeating it.
    """
    label = ""
    if isinstance(widget, Gtk.ToolButton):
        label = _text(widget.get_label())
    elif isinstance(widget, Gtk.Button):
        label = _text(widget.get_label())
        if not label:
            child = widget.get_child()
            if isinstance(child, Gtk.Label):
                label = _text(child.get_text())
    elif isinstance(widget, Gtk.MenuItem):
        label = _text(widget.get_label())
    try:
        accessible = _text(widget.get_accessible().get_name())
    except Exception:
        accessible = ""
    return label, (label or accessible or "<unnamed>")


def _walk(root, Gtk):
    """Every widget under root, once.

    `alive` is not bookkeeping — it is the fix. A PyGObject wrapper handed
    back by get_children() is temporary, and once it is collected its id is
    reused by a live widget, so an id-keyed visited set silently prunes whole
    branches. Holding a reference keeps each id unique for the walk. Without
    it this gate saw 99 controls across 34 apps; the animation app alone has
    more than fifty buttons.
    """
    seen, alive, stack = set(), [], [root]
    while stack:
        widget = stack.pop()
        if id(widget) in seen:
            continue
        seen.add(id(widget))
        alive.append(widget)
        yield widget
        if isinstance(widget, Gtk.Container):
            try:
                stack.extend(widget.get_children())
            except Exception:
                pass


def probe(name, module_dir):
    """Child-side runtime sweep. Emit one JSON record and nothing else."""
    sys.path.insert(0, module_dir)
    if module_dir != DE:
        sys.path.append(DE)
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-disabled-"))
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import nbapp
    nbapp.claim_single_instance = lambda *a, **k: None
    nbapp.screen_size = lambda: (1024, 740)

    module = importlib.import_module(name)
    cls = _window_class(module, Gtk, nbapp.AppWindow)
    if cls is None:
        print(json.dumps({"error": "no Gtk.Window app class"}))
        return 0
    app = cls()
    root = app.get_child()
    off = Gtk.OffscreenWindow()
    if root is not None:
        app.remove(root)
        off.set_size_request(1024, 740)
        off.add(root)
        off.show_all()
        for _ in range(60):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    gtk_types = tuple(getattr(Gtk, x) for x in CONTROL_TYPES)
    findings = []
    inspected = disabled = 0
    for widget in _walk(root, Gtk) if root is not None else ():
        if not isinstance(widget, gtk_types):
            continue
        inspected += 1
        if widget.get_sensitive():
            continue
        disabled += 1
        authored, shown = _control_name(widget, Gtk)
        reason = _text(widget.get_tooltip_text())
        if not reason:
            defect = "no tooltip"
        elif authored and reason.casefold() == authored.casefold():
            defect = "tooltip repeats control name"
        else:
            continue
        findings.append([name + ".py", type(widget).__name__, shown, defect])

    off.destroy()
    try:
        app.destroy()
    except Exception:
        pass
    print(json.dumps({"findings": findings, "inspected": inspected,
                      "disabled": disabled}))
    return 0


def _run_probe(name, module_dir):
    home = tempfile.mkdtemp(prefix="nb-disabled-%s-" % name)
    env = dict(os.environ)
    env["NB_HOME"] = home
    try:
        return subprocess.run(
            [sys.executable, SELF, "--probe", name, module_dir], cwd=ROOT,
            env=env, capture_output=True, text=True, timeout=30)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def run_gate(module_dir=DE, only=None):
    modules = [x for x in app_modules(module_dir) if not only or x == only]
    actual = set()
    bad = inspected = disabled = apps = environment_blocked = 0
    for name in modules:
        try:
            run = _run_probe(name, module_dir)
        except subprocess.TimeoutExpired:
            bad += 1
            print("%s.py: probe blocked or exceeded 30 seconds" % name)
            continue
        lines = [x for x in run.stdout.splitlines() if x.strip().startswith("{")]
        if run.returncode or not lines:
            bad += 1
            detail = (run.stderr or run.stdout).strip().splitlines()
            if detail and "Gtk couldn't be initialized" in detail[-1]:
                environment_blocked += 1
            print("%s.py: probe failed%s" %
                  (name, ": " + detail[-1] if detail else ""))
            continue
        data = json.loads(lines[-1])
        if data.get("error"):
            bad += 1
            print("%s.py: %s" % (name, data["error"]))
            continue
        apps += 1
        inspected += data["inspected"]
        disabled += data["disabled"]
        for finding in data["findings"]:
            actual.add(tuple(finding))

    if apps == 0 and modules and environment_blocked == len(modules):
        print("0 controls inspected across 0 apps")
        print("RESULT: NOT RUN — GTK display is unavailable")
        return 2

    ledger = set(DEBT) if module_dir == DE and only is None else set()
    for key in sorted(actual - ledger):
        bad += 1
        print("%s: %s %r: %s" % (key[0], key[1], key[2], key[3]))
    for key in sorted(ledger - actual):
        bad += 1
        print("LEDGER STALE  %s: %s %r (%s) — remove this debt entry" %
              (key[0], key[1], key[2], DEBT[key]))
    print("%d controls inspected across %d apps; %d disabled; %d violations found"
          % (inspected, apps, disabled, len(actual)))
    print("RESULT: " + ("PASS" if not bad else "FAILED: %d problem(s)" % bad))
    return 1 if bad else 0


def selfcheck():
    """Sabotage a copied real module and require the ordinary gate to catch it."""
    scratch = tempfile.mkdtemp(prefix="nb-disabled-selfcheck-")
    try:
        module = "calculator"
        source = os.path.join(DE, module + ".py")
        target = os.path.join(scratch, module + ".py")
        shutil.copy2(source, target)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(r'''

# disabled_reason_check --selfcheck sabotage: alter the copied app only.
_disabled_reason_original_init = Calculator.__init__
def _disabled_reason_sabotaged_init(self, *args, **kwargs):
    _disabled_reason_original_init(self, *args, **kwargs)
    stack = [self]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button):
            widget.set_sensitive(False)
            widget.set_tooltip_text(None)
            return
        if isinstance(widget, Gtk.Container):
            stack.extend(widget.get_children())
Calculator.__init__ = _disabled_reason_sabotaged_init
''')
        run = subprocess.run(
            [sys.executable, SELF, "--module-dir", scratch, "--only", module],
            cwd=ROOT, capture_output=True, text=True, timeout=45,
            env=dict(os.environ, NB_HOME=os.path.join(scratch, "home")))
        output = (run.stdout or "") + (run.stderr or "")
        print(output.rstrip())
        caught = (run.returncode != 0 and "no tooltip" in output and
                  "calculator.py" in output)
        print("SELFCHECK: " + ("PASS — sabotaged control was reported" if caught
                                else "FAIL — sabotage did not make the gate red"))
        return 0 if caught else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv):
    if len(argv) == 4 and argv[1] == "--probe":
        return probe(argv[2], argv[3])
    if "--selfcheck" in argv:
        return selfcheck()
    module_dir = DE
    only = None
    if "--module-dir" in argv:
        module_dir = os.path.abspath(argv[argv.index("--module-dir") + 1])
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    return run_gate(module_dir, only)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
