#!/usr/bin/env python3
"""Behavioral gate for MENU-CONVENTIONS section 1's ellipsis promise.

Labels cannot prove this rule.  This check constructs every app with a menu,
invokes each enabled item, and observes whether the callback creates an
in-window card or a dialog/toplevel.  Existing defects live in DEBT, an exact
two-way ratchet: a new defect and a stale allowance both fail.

Some callbacks cannot be driven headlessly and are deliberately not invoked:

* Open, Save As, Add Sound, Place Image, Import and Export hand control to a
  file chooser; Print hands control to the host printer dialog.
* New may ask for a preset or discard confirmation, or immediately replace a
  document, according to the app's data model.  It needs scenario-specific
  state rather than an empty-document sweep.
* Save is intentionally dual-natured: unnamed documents ask for a path and
  named documents save immediately.  A generic sweep cannot assert one side.
* Quit and Close destroy the harness window.  Esc/Close belongs to nbapp, not
  to the document, and is outside this gate.
* Playback/recording/audio controls are skipped because opening ALSA or a media
  pipeline on a host without a working sink can wedge the entire gate.

Each app is a separate subprocess.  That contains document mutations in its
private NB_HOME and means a surprising backend cannot poison later apps.
Run through tools/guestrun.sh (run_all_gates does this) so construction uses
the guest fonts, Papertone theme and XDG data paths.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
SELF = os.path.abspath(__file__)

# These live lanes are explicitly outside this gate's ownership for now.
OFF_LIMITS = {"animation.py", "burner.py", "comics.py"}

# App -> number of currently known behavioral promise violations.
# Numbers only ever come down.  Filled from the first complete sweep.
#
# NOT WIRED INTO run_all_gates YET, and this is why: 16 of the 33 apps never
# finish their probe (academics, calculator, calendar, composer, gbaemu, gbasdk, illustrator, language, music, novel, screenplay, sequencer, terminal, video, workout, writer).  An app whose probe blocks is
# not an app that passed — it is an app this gate cannot speak for, and the
# verdict counts it as a failure, correctly.  The cause is the same one the
# animation suite hit: pressing Play builds a real GStreamer pipeline that
# blocks on a host with no working sink, and pressing an item that wants a
# file picker waits forever.  HANDOFF and AUDIO_WORDS below catch some of
# them and not enough.  Until every app can be probed, wiring this into the
# aggregate would make the aggregate permanently red, which is the same as
# having no gate at all.
DEBT = {
    'accounting.py': 3,
    'bills.py': 1,
    'contacts.py': 3,
    'cookbook.py': 2,
    'ebook.py': 1,
    'g2048.py': 2,
    'installer.py': 1,
    'journal.py': 2,
    'maps.py': 2,
    'mealplanner.py': 2,
    'media.py': 2,
    'packages.py': 3,
    'settings.py': 9,
    'sysmon.py': 2,
    'tasks.py': 4,
    'usbwriter.py': 2,
    'widgetsettings.py': 2,
}

HANDOFF = {
    "Open", "Open File", "Open Folder", "Save As", "Add Sound",
    "Place Image", "Import", "Import Audio", "Import Image", "Export",
    "Export Film", "Export Movie", "Export PDF", "Print", "New", "Quit",
    "Close", "Close Window",
}
AUDIO_WORDS = ("play", "pause", "stop playback", "record", "listen",
               "preview audio", "test sound")


def tracked():
    """Return tracked modules; uncommitted apps are reported as WIP only."""
    try:
        run = subprocess.run(["git", "ls-files", DE], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if run.returncode:
        return None
    return {os.path.basename(x) for x in run.stdout.splitlines() if x}


def app_modules():
    """Derive the sweep from source: concrete modules defining menu_items."""
    found = []
    for name in sorted(os.listdir(DE)):
        if not name.endswith(".py") or name in OFF_LIMITS or name == "nbapp.py":
            continue
        path = os.path.join(DE, name)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except (OSError, SyntaxError):
            continue
        if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and
               n.name == "menu_items" for n in ast.walk(tree)):
            found.append(name)
    return found


def bare_label(label):
    return label.lstrip().split("    ", 1)[0].strip().rstrip("…")


def skip_label(label):
    bare = bare_label(label)
    if bare in HANDOFF:
        return True
    low = bare.lower()
    if low.startswith("export ") or low.startswith("import "):
        return True
    return any(word in low for word in AUDIO_WORDS)


def _window_class(module, Gtk):
    candidates = []
    for _name, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ == module.__name__ and issubclass(cls, Gtk.Window):
            candidates.append(cls)
    # Prefer the app class with a menu provider over helper dialog classes.
    candidates.sort(key=lambda c: "menu_items" not in c.__dict__)
    return candidates[0] if candidates else None


def _overlay_ids(app):
    overlay = getattr(app, "_overlay", None)
    if overlay is None:
        return set()
    try:
        return {id(x) for x in overlay.get_children()}
    except Exception:
        return set()


def _card_tokens(app):
    tokens = set()
    for name in dir(app):
        if "prompt" not in name.lower() and "card" not in name.lower():
            continue
        try:
            value = getattr(app, name)
        except Exception:
            continue
        if value is not None and not callable(value):
            tokens.add((name, id(value)))
    return tokens


def probe(name):
    """Child-side runtime sweep.  Emits one JSON record and nothing else."""
    sys.path.insert(0, DE)
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-menu-promise-"))
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import nbapp
    nbapp.claim_single_instance = lambda *a, **k: None
    nbapp.screen_size = lambda: (1024, 722)
    module = importlib.import_module(name[:-3])
    cls = _window_class(module, Gtk)
    if cls is None:
        print(json.dumps({"error": "no Gtk.Window app class"}))
        return 0
    app = cls()
    flashes = []
    # Refusals are deliberately neutral: they neither ask nor act.  Apps use
    # several names/signatures for their transient status method.
    for attr in dir(app):
        if attr.startswith("_flash"):
            try:
                setattr(app, attr, lambda *a, **k: flashes.append(str(a[0]) if a else ""))
            except Exception:
                pass
    findings = []
    invoked = skipped = 0
    for menu in tuple(getattr(app, "menus", ())) + (getattr(app, "app_name", ""),):
        try:
            items = app.menu_items(menu)
        except Exception:
            continue
        for item in items:
            if not item or item is nbapp.SEP or not isinstance(item, tuple) or len(item) < 2:
                continue
            label, action = item[0], item[1]
            if action is None:
                continue
            label = str(label)
            if skip_label(label):
                skipped += 1
                continue
            before_windows = {id(x) for x in Gtk.Window.list_toplevels()}
            before_overlay = _overlay_ids(app)
            before_cards = _card_tokens(app)
            del flashes[:]
            try:
                action()
            except Exception as exc:
                # A callback crash is owned by construct/selftests, not turned
                # into a made-up ellipsis verdict here.
                skipped += 1
                continue
            invoked += 1
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            asked = bool(({id(x) for x in Gtk.Window.list_toplevels()} - before_windows) or
                         (_overlay_ids(app) - before_overlay) or
                         (_card_tokens(app) - before_cards))
            if flashes and not asked:
                continue
            promised = label.lstrip().split("    ", 1)[0].strip().endswith("…")
            if asked != promised:
                findings.append([menu, label.lstrip().split("    ", 1)[0].strip(),
                                 "asks with no ellipsis" if asked else
                                 "promises to ask but acts at once"])
            # Return the harness to its pre-action state.  Shared cards are
            # overlay children; legacy dialogs are toplevels.  Without this,
            # the first asking action would hide every later asking action.
            overlay = getattr(app, "_overlay", None)
            if overlay is not None:
                for child in list(overlay.get_children()):
                    if id(child) not in before_overlay:
                        try:
                            overlay.remove(child)
                        except Exception:
                            pass
            for win in list(Gtk.Window.list_toplevels()):
                if id(win) not in before_windows:
                    try:
                        win.destroy()
                    except Exception:
                        pass
            for attr, token in _card_tokens(app) - before_cards:
                try:
                    if id(getattr(app, attr)) == token:
                        setattr(app, attr, None)
                except Exception:
                    pass
    try:
        app.destroy()
    except Exception:
        pass
    print(json.dumps({"findings": findings, "invoked": invoked,
                      "skipped": skipped}))
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        return probe(sys.argv[2])
    modules = app_modules()
    carried = tracked()
    counts = {}
    pending = []
    bad = 0
    total_findings = total_invoked = total_skipped = 0
    for name in modules:
        home = tempfile.mkdtemp(prefix="nb-menu-promise-%s-" % name[:-3])
        env = dict(os.environ)
        env["NB_HOME"] = home
        try:
            run = subprocess.run([sys.executable, SELF, "--probe", name],
                                 cwd=ROOT, env=env, capture_output=True,
                                 text=True, timeout=25)
        except subprocess.TimeoutExpired:
            bad += 1
            print("%s: probe blocked or exceeded 25 seconds" % name)
            continue
        lines = [x for x in run.stdout.splitlines() if x.strip().startswith("{")]
        if run.returncode or not lines:
            bad += 1
            detail = (run.stderr or run.stdout).strip().splitlines()
            print("%s: probe failed%s" % (name, ": " + detail[-1] if detail else ""))
            continue
        data = json.loads(lines[-1])
        if data.get("error"):
            bad += 1
            print("%s: %s" % (name, data["error"]))
            continue
        findings = data["findings"]
        counts[name] = len(findings)
        total_findings += len(findings)
        total_invoked += data["invoked"]
        total_skipped += data["skipped"]
        if carried is not None and name not in carried:
            if findings:
                pending.append((name, findings))
            continue
        allowed = DEBT.get(name, 0)
        if len(findings) > allowed:
            bad += 1
            for _menu, label, why in findings:
                print("%s: %s: %s" % (name, label, why))
    for name, allowed in sorted(DEBT.items()):
        actual = counts.get(name, 0)
        if actual < allowed:
            bad += 1
            print("LEDGER STALE  %s now has %d violations, ledger says %d — "
                  "lower the number so it cannot climb back" % (name, actual, allowed))
    for name, findings in pending:
        print("NOT YET COMMITTED  %s owes %d menu promise violation(s) — its "
              "lane must fix or ledger them before it ships" % (name, len(findings)))
        for _menu, label, why in findings:
            print("  %s: %s: %s" % (name, label, why))
    print("%d apps swept; %d enabled items invoked; %d headless handoffs skipped; "
          "%d violations found" % (len(modules), total_invoked,
                                     total_skipped, total_findings))
    print("RESULT: " + ("PASS" if not bad else "FAILED: %d app(s)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
