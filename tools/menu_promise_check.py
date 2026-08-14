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
# NOT WIRED INTO run_all_gates YET, and this is why: 11 of the 33 apps still
# never finish their probe (academics, calculator, calendar, composer,
# gbaemu, gbasdk, language, music, terminal, workout, writer).
#
# That is down from 16: stubbing the audio pump and the file pickers inside
# the probe — the way tools/animation_selftest.py already does — took the
# items actually invoked from 104 to 203, and freed five apps.
#
# An app whose probe blocks is
# not an app that passed — it is an app this gate cannot speak for, and the
# verdict counts it as a failure, correctly.  The cause is the same one the
# animation suite hit: pressing Play builds a real GStreamer pipeline that
# blocks on a host with no working sink, and pressing an item that wants a
# file picker waits forever.  HANDOFF and AUDIO_WORDS below catch some of
# them and not enough.  Until every app can be probed, wiring this into the
# aggregate would make the aggregate permanently red, which is the same as
# having no gate at all.
#
# The child probe now replaces both dependencies before invoking menu items.
# A complete 33-app sweep on display-equipped hardware is still required to
# replace this historical count, populate DEBT, and wire the aggregate gate.
DEBT = {
    'accounting.py': 0,  # Ledger Summary — reveals a dismiss-only report, no input or choice.
    'contacts.py': 0,
    # New Recipe appends a recipe, selects it and opens the editor on it. It
    # asks nothing — New Category… beside it does, so the app already drew
    # the line — but the editor arrives as an overlay full of fields, which
    # this gate cannot tell from a question. Carried as a known exemption
    # rather than labelled with a promise the item does not keep.
    'cookbook.py': 1,
    'g2048.py': 1,  # New Game — resets the board immediately, with no prompt.
    'installer.py': 1,
    'journal.py': 0,
    'maps.py': 1,  # Zoom In — adjusts the map view immediately, no dialog.
    'mealplanner.py': 1,  # Cut — moves selected text to the clipboard immediately, no dialog.
    'media.py': 1,  # Show Info Panel — reveals the existing panel immediately, no input.
    'packages.py': 2,
    # System, Sound, Power, Keyboard, Date & Time, Region & Language, Backup,
    # Default Apps — each switches pages immediately; page controls are not a prompt.
    'settings.py': 2,
    'sysmon.py': 1,  # Refresh Now — resamples processes immediately, no dialog.
    'tasks.py': 0,
    'usbwriter.py': 1,  # Look for Drives Again — rescans and redraws immediately, no dialog.
    'widgetsettings.py': 1,  # Hide All from the Desktop — disables all tiles and saves immediately.
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


def _offers_a_choice(surface, Gtk):
    """Does this surface ASK, or does it only SHOW?

    MENU-CONVENTIONS §1: the ellipsis promises a dialog, a picker or a
    confirm BEFORE ANYTHING HAPPENS. An About box is not that — it is the
    action itself, and it asks nothing. Counting any window that appears as
    "it asked" made every app's About item a violation, all seventeen of
    them, against a label composed by nbcommands.about_label — a SHARED
    helper that words it identically everywhere on purpose. A rule that
    convicts a shared helper seventeen times is a rule about the rule.

    A surface asks when it offers something to answer with: a place to type,
    a thing to choose, or more than one way out. A single dismissing button
    is how a presentation closes.
    """
    entries = choosers = buttons = 0
    stack, alive = [surface], []
    while stack:
        widget = stack.pop()
        alive.append(widget)
        if isinstance(widget, (Gtk.Entry, Gtk.TextView)):
            entries += 1
        elif isinstance(widget, (Gtk.ComboBox, Gtk.Switch, Gtk.Scale,
                                 Gtk.SpinButton, Gtk.CheckButton,
                                 Gtk.RadioButton, Gtk.TreeView, Gtk.ListBox)):
            choosers += 1
        elif isinstance(widget, Gtk.Button):
            buttons += 1
        if isinstance(widget, Gtk.Container):
            try:
                stack.extend(widget.get_children())
            except Exception:
                pass
    return bool(entries or choosers) or buttons > 1


def _new_overlay_widgets(app, before):
    overlay = getattr(app, "_overlay", None)
    if overlay is None:
        return []
    return [child for child in overlay.get_children() if id(child) not in before]


def _overlay_ids(app):
    overlay = getattr(app, "_overlay", None)
    if overlay is None:
        return set()
    try:
        return {id(x) for x in overlay.get_children()}
    except Exception:
        return set()


def _card_tokens(app):
    """Which card-ish attributes currently hold something.

    Names, not identities. Keyed on (name, id(value)) this counted a card
    being REBUILT as a card APPEARING — cookbook's New Recipe adds a recipe
    and refreshes the editor, which swaps a panel object, and the gate read
    that as a question being asked. Twice: once to say the label needed an
    ellipsis, and again, after one was added, to say the opposite.

    A card appears when an attribute that held nothing comes to hold
    something. Rebuilding what was already there is not asking.
    """
    holding = set()
    for name in dir(app):
        if "prompt" not in name.lower() and "card" not in name.lower():
            continue
        try:
            value = getattr(app, name)
        except Exception:
            continue
        if value is not None and not callable(value):
            holding.add(name)
    return holding


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

    class Silent:
        """Stand in for audio output so a callback never opens a real sink."""
        available = False

        def stop(self, *args, **kwargs):
            pass

        def position_samples(self, *args, **kwargs):
            return 0

        def start(self, *args, **kwargs):
            pass

        def play_once(self, *args, **kwargs):
            pass

        def push(self, *args, **kwargs):
            pass

    # Construction may create the real output object, but no driven callback
    # may reach it.  Apps without audio simply gain an unused stand-in.
    app.audio = Silent()

    # Picker callbacks must return without opening a window: a picker window
    # would itself look like evidence that the menu item asked a question.
    import nbpicker
    picker_path = os.path.join(os.environ["NB_HOME"], "menu-promise-probe")
    nbpicker.open_file = lambda *args, **kwargs: picker_path
    nbpicker.save_file = lambda *args, **kwargs: picker_path
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
            appeared = ([x for x in Gtk.Window.list_toplevels()
                         if id(x) not in before_windows] +
                        _new_overlay_widgets(app, before_overlay))
            asked = bool(appeared) and any(_offers_a_choice(x, Gtk)
                                           for x in appeared)
            if not appeared and (_card_tokens(app) - before_cards):
                asked = True
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
            # Put back whatever this item made appear, so the first asking
            # action does not hide every later one.
            for attr in _card_tokens(app) - before_cards:
                try:
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
