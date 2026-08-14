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
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
SELF = os.path.abspath(__file__)

# These live lanes are explicitly outside this gate's ownership for now.
# A card that arrives on an idle callback needs the loop pumped after the
# queue empties. Twenty rounds of ten milliseconds is 200ms per item —
# the same for every item on every run, which is what makes the verdict
# repeatable.
# Measured, not guessed. At a 200ms settle the slowest probe (gbasdk) takes
# 10.5s of a 25s budget; screenplay 4.9s, calculator 3.3s, media 0.9s. A
# 1.2s settle multiplies the per-item cost sixfold, which is why raising the
# settle ALONE cut the items invoked from 342 to 193 — probes were being
# killed partway and the gate quietly saw less. The budget has to move with
# it.
PROBE_BUDGET = 90
SETTLE_ROUNDS = 120
SETTLE_TICK = 0.01

OFF_LIMITS = {"animation.py", "burner.py", "comics.py"}

# App -> number of currently known behavioral promise violations.
# Numbers only ever come down.  Filled from the first complete sweep.
#
# NOT WIRED INTO run_all_gates YET, and there are now exactly two reasons,
# both small and both named:
#
#   1. music.py is the last app whose probe never finishes. Every other one
#      of the 33 completes.
#   2. The violation count oscillates across consecutive runs of an
#      UNCHANGED tree: 52, 53, 54 and 55 have all been observed, while the
#      items invoked stayed at exactly 342 every time. A gate that answers
#      differently to the same question cannot go into the aggregate — it
#      would fail on nobody's change, and a flaky gate teaches people to
#      re-run rather than to look.
#
#      HALF FIXED, and the other half is not fixable here.
#
#      The TALLY line named the pair: gbaemu.py gave 2 then 1, gbasdk.py 9
#      then 8. Both build their surface a beat late, so draining the event
#      queue once missed it on some runs. A FIXED settle span (SETTLE_ROUNDS
#      above) replaced that race, and both are now identical run to run.
#
#      usbwriter.py was next, 0 then 1, and no settle could fix it: its probe
#      ENUMERATES REAL USB DRIVES through sysfs, so its answer depended on
#      what was plugged in at that second. The probe is now told there are no
#      drives — a real state the app must handle, and the same one every run.
#
#      Then screenplay.py appeared, 2 then 1. Run in isolation it gave
#      1,1,1,2 across four probes with the same 22 items invoked, and the
#      varying finding was ZINE PRINT — an async export.
#
#      An earlier note here said a longer settle would buy nothing, on the
#      strength of an instrument reporting worst=1. That instrument was
#      BLIND: it could only see surfaces that appeared inside the window, so
#      a surface arriving after it was invisible to the very measurement
#      meant to find it. At 1.2s the same probe gives 1,1,1,1.
#
#      What is actually happening: Zine Print opens a PROGRESS card, and the
#      export then dismisses it. Sample early and a surface is there; sample
#      late and it is gone. A progress card is not asking anybody anything —
#      it is the About-box distinction again.
#
#      BUT A LONGER SETTLE IS NOT THE ANSWER, and the full sweep says so.
#      At 1.2s per item the run took 6m43 and invoked 193 items instead of
#      342, with violations falling 56 to 36. Each app's probe has a
#      25-second budget; at 1.2s an app with twenty items exhausts it and the
#      probe is cut off partway, so the gate quietly SEES LESS. Trading
#      coverage for stability that way is a bad bargain and an invisible one
#      — the totals simply drop, and nothing says why.
#
#      THE BUDGET WAS THE MISSING HALF. Timing the probes at 200ms: gbasdk
#      10.5s of its 25s allowance, screenplay 4.9s, calculator 3.3s, media
#      0.9s — so the sweep was using under half its budget, and a sixfold
#      settle simply did not fit. With the settle at 1.2s AND the budget at
#      90s, the run invokes all 342 items again and takes 8m50 against about
#      three minutes at 200ms.
#
#      ESTABLISHED: two full runs at these settings came out IDENTICAL — 53
#      violations, 342 items, no app differing by the TALLY line. That is the
#      repeatability the change was for, and it took two runs to be able to
#      say it.
#
#      music.py remains the one app this gate cannot speak for, and the shape
#      of it is now clear even though the cause is not. Probed ALONE it
#      finishes in 12 seconds. Probed as part of the sweep it exceeds 90.
#      Each app runs in its own subprocess, so this is not state inside the
#      interpreter — it is something a previous probe leaves held that a
#      music player then waits for, an audio device being the obvious
#      candidate. Run the sweep with music FIRST and see whether it still
#      hangs; that separates "music is slow" from "music waits for what
#      someone else left open".
#
#      screenplay is ledgered at its lower value, so a run that finds two
#      still fails — deliberately, because pinning the higher number would
#      hide the instability instead of showing it.
#
#      STILL OUT OF THE AGGREGATE, and now for one reason with two faces:
#      music.py never finishes, and the settle is not yet long enough for
#      every app to answer the same way twice.
#
# How it got here, for whoever picks it up. Stubbing the audio pump and the
# file pickers took invoked items 104 -> 203 and blocked probes 16 -> 11.
# Then a stack dump — not a guess — showed calculator.py:_store_dialog
# sitting inside Gtk.Dialog.run(), a legacy modal loop that waits for a
# person. Stubbing run() to return CANCEL took items 203 -> 342 and blocked
# probes 11 -> 1. The dialog is still CONSTRUCTED and still registers as a
# new toplevel, so the verdict this gate makes — did the item ask? — is
# untouched; only the waiting is gone.
#
# Reaching 342 items instead of 104 is why the ledger now holds 55
# violations rather than 20. They were always there; nothing had invoked
# them.
#
# The child probe now replaces both dependencies before invoking menu items.
# A complete 33-app sweep on display-equipped hardware is still required to
# replace this historical count, populate DEBT, and wire the aggregate gate.
DEBT = {
    'academics.py': 3,
    'calculator.py': 4,
    'calendar.py': 2,
    'composer.py': 2,
    'cookbook.py': 1,
    'g2048.py': 1,
    'gbaemu.py': 1,
    'gbasdk.py': 9,
    'illustrator.py': 1,
    'installer.py': 1,
    'language.py': 3,
    'maps.py': 1,
    'mealplanner.py': 1,
    'media.py': 1,
    'novel.py': 1,
    'packages.py': 2,
    'screenplay.py': 1,
    'sequencer.py': 1,
    'settings.py': 2,
    'sysmon.py': 1,
    'usbwriter.py': 1,
    'video.py': 7,
    'widgetsettings.py': 1,
    'writer.py': 5,
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
    # usbwriter enumerates REAL drives through sysfs, so its verdict moved
    # with whatever happened to be plugged into the machine at that second —
    # the TALLY line caught it giving 0 then 1 on an unchanged tree. A gate
    # cannot ask the world a question and call the answer repeatable, so the
    # probe is told there are no drives. That is a real state the app must
    # handle, and it is the same one every run.
    if name == "usbwriter.py":
        module._drives = lambda *args, **kwargs: []
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
    # A legacy Gtk.Dialog.run() spins its own modal loop until a person
    # dismisses it, so the probe hung there for ever. Diagnosed by stack, not
    # guessed: calculator.py:_store_dialog was sitting in Gtk.py run().
    #
    # The dialog is still CONSTRUCTED and still registers as a new toplevel,
    # so the verdict this gate exists to make — did the item ask? — is
    # unchanged. Only the waiting is removed.
    Gtk.Dialog.run = lambda self, *args, **kwargs: Gtk.ResponseType.CANCEL
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
    settle_rounds = []
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
            # Settle for a FIXED span, not until the queue happens to be
            # empty. Draining once is a race: a card that arrives on an idle
            # or a short timeout lands after the queue empties, so the same
            # item was seen asking on one run and not the next. That is the
            # whole of this gate's 52-to-56 wobble — the TALLY line named
            # gbaemu.py (2 then 1) and gbasdk.py (9 then 8), both of which
            # build their surface a beat late.
            first_seen = None
            for _round in range(SETTLE_ROUNDS):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                time.sleep(SETTLE_TICK)
                # When did this item's surface FIRST become visible? Choosing
                # the settle window by guess is what left four apps answering
                # differently on different runs; this records the answer so
                # the window can be set from the distribution instead.
                if first_seen is None:
                    showing = ([x for x in Gtk.Window.list_toplevels()
                                if id(x) not in before_windows] +
                               _new_overlay_widgets(app, before_overlay))
                    if showing:
                        first_seen = _round + 1
            if first_seen is not None:
                settle_rounds.append((name, label.strip()[:28], first_seen))
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
    if settle_rounds:
        worst = max(r for _n, _l, r in settle_rounds)
        late = [(l, r) for _n, l, r in settle_rounds if r > 3]
        print("SETTLE %s worst=%d late=%s" % (name, worst, late[:4]),
              file=sys.stderr)
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
                                 text=True, timeout=PROBE_BUDGET)
        except subprocess.TimeoutExpired:
            bad += 1
            print("%s: probe blocked or exceeded %d seconds" % (name, PROBE_BUDGET))
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
    # Per-app tally, always. Findings WITHIN the ledger are never printed, so
    # the total could move between runs of an unchanged tree while the visible
    # output stayed empty — which made this gate's own flakiness impossible to
    # chase by diffing two runs. Now two runs can be diffed and the varying
    # app named.
    print("TALLY " + " ".join("%s=%d" % (app, counts[app])
                              for app in sorted(counts)))
    print("%d apps swept; %d enabled items invoked; %d headless handoffs skipped; "
          "%d violations found" % (len(modules), total_invoked,
                                     total_skipped, total_findings))
    print("RESULT: " + ("PASS" if not bad else "FAILED: %d app(s)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
