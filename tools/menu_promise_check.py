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
# Numbers only ever come down.
#
# WHERE THESE NUMBERS CAME FROM, 2026-08-17. The ledger used to hold 55
# violations across 24 apps. A full sweep now finds FOUR, in three apps, and
# almost none of the difference was an app being fixed: this gate was
# convicting apps of asking questions they never asked, and clearing others of
# questions they did ask. Four defects in the instrument, each found by driving
# the flagged item and LOOKING at what came up:
#
#   1. IDENTITY BY id(). The sweep held {id(child)} for the overlay's children
#      and for the toplevel list, and PyGObject frees a widget's Python wrapper
#      the moment nothing references it -- so the next get_children() built a
#      new wrapper that may or may not reuse the freed address. Sampling ONE
#      unchanged overlay child eight times returned two different id() values.
#      "A new child appeared" therefore meant "the allocator moved", which a
#      menu item that builds a lot of widgets makes likely. It convicted
#      academics' New Lecture, cookbook's Cut and journal's New Entry, and it
#      is the whole of the 52-to-56 wobble this file used to describe as
#      unexplained: address reuse is not repeatable. See _held_children.
#
#   2. A MODAL JUDGED AFTER IT CLOSED. Gtk.Dialog.run is stubbed to return
#      CANCEL so the sweep never waits for a person -- and a callback written
#      `if resp != OK: dlg.destroy(); return` then destroys its dialog before
#      action() returns, leaving an empty screen to inspect. academics' New
#      Class, Add an Assignment and Edit Class were each convicted of acting at
#      once while putting a titled form with a name field on the screen. The
#      verdict is now taken inside run(), while the dialog is provably up.
#
#   3. SURFACES THE WALK COULD NOT SEE. A popped-up Gtk.Menu is choices and
#      nothing else, and _offers_a_choice counted no MenuItem (cookbook's Move
#      to Category); a file chooser is a question, and the sweep stubs the
#      chooser away (usbwriter's Choose an Image…); a Revealer holding a form
#      or a sheet is a surface, and the sweep only looked for NEW widgets
#      (accounting's New Entry, ebook's Open Library). All three now count, and
#      the popup counts on the CALL rather than on the map, because whether a
#      headless X server obliges is not repeatable -- five probes of unchanged
#      cookbook gave 0,0,0,0,1 on exactly that.
#
#   4. A WINDOW THAT WAS NEVER SHOWN. A Gtk.ComboBox builds its dropdown as a
#      toplevel the moment its page is built, so seven Settings pages "asked"
#      by existing -- Region & Language produced three at once, Default Apps
#      four. A surface counts only if it is on screen (_on_screen).
#
# Two real defects were found underneath all that and FIXED rather than
# ledgered: tasks' New Task… and packages' Find… each promised a card and only
# moved the caret into a box already on screen. packages' was a registry
# violation too, and menu_conformance_check had it in debt; that row is now
# pruned.
#
# WHAT IS LEFT, 2026-08-17 (second pass): NOTHING. The ledger is empty, which
# is the only shape a ratchet can hold that no regression can hide inside. Of
# the four rows that stood here, one was a real app defect, three were the
# instrument convicting a NAME, and a second real defect was hiding under that
# same instrument bug in an app the ledger said was clean. The only thing that
# told them apart was driving the item and looking at the screen:
#
#   calculator.py  Variables… REAL, and fixed rather than ledgered. The dialog
#                  lists the stored variables under a single Close button and
#                  asks nothing -- the About-box distinction, applied to a
#                  label that promised a question. Now "Variables"; every
#                  catalog already carried the word, because it is the
#                  dialog's own title.
#   contacts.py    New Contact… REAL, and it had been HIDDEN by the fifth
#                  instrument defect below. _new_contact appends the person,
#                  selects it, enters edit mode and saves to disk before a
#                  field is typed. Now "New Contact", which is also what
#                  MENU-CONVENTIONS §2B prints for a single-store app.
#   settings.py    Backup INSTRUMENT, not the app. View > Backup navigates to
#                  a section, and the section's own "Where to copy it" box is
#                  named _bk_dest_card. Nothing is raised and nothing is
#                  asked, so the plain label was right all along.
#   video.py       Add Title Card, Add Credits INSTRUMENT, not the app. Both
#                  insert the clip at once and bank it as "Undo Add Title
#                  Card"; what the gate saw was the pixbuf cache
#                  _cardpv_cache coming into existence for the first title
#                  clip. Plain labels, correctly.
#
# That leaves a FIFTH defect in the instrument, alongside the four above:
#
#   5. A NAME IS NOT A SURFACE. `_card_tokens` convicted an item when an
#      attribute whose NAME contains "card" or "prompt" came to hold
#      something, and it was consulted only when nothing had appeared -- that
#      is, only when the app had raised nothing. It read settings' page
#      section box `_bk_dest_card`, video's pixbuf cache `_cardpv_cache` and
#      contacts' detail column `_card_col` as questions. Retired as evidence,
#      kept as housekeeping; the call site carries the measurement.
#
# music.py IS NO LONGER THE APP THIS GATE CANNOT SPEAK FOR. The probe never
# blocked: every music item returns in the same settle as everywhere else, and
# the whole item sweep takes 12.8s. What exceeded the 90-second budget was the
# PARENT, waiting on a capture pipe held open by the Finder that Open Music
# Folder had started and that outlived the probe. Timed both ways: the probe
# alone finishes in 12.8s while the surrounding capture ran 2m55 and ended the
# moment the leaked Finder was killed by hand. Fixed on both sides -- the probe
# detaches and then stops what it starts, and the parent captures to files, so
# the timeout can only ever mean what its message says.
#
# STILL OUT OF run_all_gates, and now for one honest reason: a full sweep takes
# about ten minutes. Repeatability is no longer the blocker -- the four
# instrument defects above were the four sources of the wobble, and the apps
# that used to differ run to run (gbaemu, gbasdk, screenplay, usbwriter,
# cookbook) now answer the same way on repeated probes.
#
# THAT BLIND SPOT IS CLOSED, 2026-08-17, and three more were found underneath
# it. The old note here said the probe replaced EVERY attribute starting with
# "_flash" -- ten swept apps keep a non-callable there -- so the app's next
# comparison raised, the callback was filed as "skipped", and the item was
# never judged. Measured before and after on the ten apps that keep one:
#
#     18 items in 3 apps were being dropped that way (cookbook 6, illustrator
#     5, sequencer 7); after the one-line fix, 0. Judged items on those ten
#     apps went 105 -> 119.
#
# Every one of the 18 was then driven by hand and every one keeps its label's
# promise, so the fix convicted nobody -- which is the point: it is the
# DIFFERENCE between "no violations" and "no violations that anything looked
# at" that this file exists to hold. Three further silent drops came out of
# the same measurement, each now counted and named in the output:
#
#   * A CALLBACK THAT RAISES is no longer folded into the handoff count. It is
#     named, and it FAILS the gate: there is no ledger for it, because zero is
#     the only allowance a regression cannot hide inside.
#   * A GREYED-OUT ITEM was never counted at all. 107 of them across the 32
#     apps -- and four of cookbook's were greyed by this sweep's OWN Undo,
#     which took back the recipe the later items needed. Undo and Redo are now
#     driven LAST (see _takes_it_back), which put those four back, and the
#     rest are listed per app so a state the sweep cannot reach reads as a gap
#     rather than as silence.
#   * A STATUS-ONLY REFUSAL still forms no verdict, and now says so by name.
#
# The audio exclusion was substring-matched ("play" is inside "Playlist" and
# "Display"), which had quietly swallowed calculator's Display Mode…, music's
# New Playlist, settings' Displays, screenplay's About Screenplay, gbasdk's
# Build & Play and sequencer's two recording toggles. It is exact labels now,
# and the handoff list is printed in full, because a count alone is what let
# an accident live inside it.
#
# WHAT THE WIDER SWEEP THEN FOUND: nothing it could reach, and two real
# defects where it still cannot -- sysmon's End Program and academics' Clear
# Finished Homework each raised a confirm under a label with no ellipsis, and
# both are greyed out in an empty-document sweep (no program selected, no
# finished homework). Both fixed in the apps, both proved by driving them.
# That is the standing limit of this gate, printed on every run: the items it
# cannot reach are the ones a defect survives in.
#
# How it got here, for whoever picks it up. Stubbing the audio pump and the
# file pickers took invoked items 104 -> 203 and blocked probes 16 -> 11.
# Then a stack dump -- not a guess -- showed calculator.py:_store_dialog
# sitting inside Gtk.Dialog.run(), a legacy modal loop that waits for a
# person. Stubbing run() to return CANCEL took items 203 -> 342 and blocked
# probes 11 -> 1.
# EMPTY, and it must stay that way. A ceiling above the measured number is
# slack a regression climbs back into without ever turning this gate red, and
# that is how the ledger reached 55 rows across 24 apps: apps got fixed, the
# numbers never came down, and by the time anyone looked 21 of the 24 rows
# were pure headroom. With no rows at all, the first violation anyone
# introduces is the one that fails the build.
DEBT = {}

HANDOFF = {
    "Open", "Open File", "Open Folder", "Save As", "Add Sound",
    "Place Image", "Import", "Import Audio", "Import Image", "Export",
    "Export Film", "Export Movie", "Export PDF", "Print", "New", "Quit",
    "Close", "Close Window",
}
# THE CONTROL ITSELF, NOT ANY LABEL CONTAINING THE WORD. This was a tuple of
# substrings tested with `word in label.lower()`, and "play" is inside
# "Playlist" and "Display", "record" inside "Record Payment" and "Recording" --
# so eight items in six apps were quietly filed as audio controls and never
# driven at all: calculator's Display Mode…, music's New Playlist (and its
# Undo), settings' Displays, screenplay's About Screenplay, gbasdk's Build &
# Play and sequencer's two recording TOGGLES. Every one of them was driven by
# hand before this list was narrowed, and every one behaves as its label says;
# the point is that nothing in this gate could have told you either way.
#
# Exact labels, so adding a control to the exclusion is a decision somebody
# makes and a reader can see. "Stop" is deliberately absent: composer and
# sequencer both offer it, both were already swept under the old rule (which
# only ever matched the phrase "stop playback"), and both stop cleanly.
AUDIO_CONTROLS = {"play", "pause", "play/pause", "record", "listen",
                  "stop playback", "preview audio", "test sound"}


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
    # SAVE, in every wording an app gives it. The docstring above already puts
    # Save outside this gate -- "unnamed documents ask for a path and named
    # documents save immediately... a generic sweep cannot assert one side" --
    # but HANDOFF listed only "Save As", so Save, Save Project and friends were
    # swept anyway, on an empty unnamed document, which is the asking side
    # every time. It read as green only while the file chooser was stubbed
    # away in silence; the moment opening a chooser counted as evidence, six
    # apps were convicted of the behaviour the docstring calls correct. One
    # rule for every wording, so a seventh app naming it "Save a Copy" is not
    # judged by a sweep that has already said it cannot judge this.
    if low == "save" or low.startswith("save "):
        return True
    # A tick or a bullet in front of a toggle is state, not name.
    return low.lstrip("\u2713\u2022* ").strip() in AUDIO_CONTROLS


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
        elif isinstance(widget, Gtk.SeparatorMenuItem):
            pass
        elif isinstance(widget, Gtk.MenuItem):
            # A POPPED-UP MENU is the purest form of asking, and it was the one
            # surface this walk could not see: a Gtk.MenuItem is neither a
            # Button nor any of the chooser widgets below, so cookbook's Move
            # to Category -- which offers every category, No category and New
            # Category in a menu anchored to the kicker -- came back as
            # "promises to ask but acts at once" against a menu of choices
            # sitting open on the screen.
            choosers += 1
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


def _on_screen(widget):
    """Is this surface actually UP, or merely constructed?

    A Gtk.ComboBox builds its dropdown as a toplevel Gtk.Window the moment the
    page holding it is built, and never shows it until someone clicks. Counting
    those made seven Settings pages "ask" by existing -- Region & Language
    produced three at once and Default Apps four, each an empty Window whose
    only content was a menu of options nobody had opened.

    Nothing is on screen until something shows it, so the widget's own visible
    flag is the question. It is set by show()/show_all() and does not wait on
    the X server, which keeps the answer the same on every run.
    """
    try:
        return bool(widget.get_visible())
    except Exception:                                             # noqa: BLE001
        return True


def _tree(app, Gtk):
    """Every widget under the app window, in a list that HOLDS them."""
    out, stack = [], [app]
    while stack:
        widget = stack.pop()
        out.append(widget)
        if isinstance(widget, Gtk.Container):
            try:
                stack.extend(widget.get_children())
            except Exception:                                     # noqa: BLE001
                pass
    return out


def _waiting(app, Gtk):
    """Closed Revealers: surfaces that EXIST and are being held back.

    Not every app asks by building something new. accounting's New Entry
    reveals an entry form that was already in the tree, and ebook's Open
    Library reveals a book-picking sheet that was already in the tree; both
    read as "promises to ask but acts at once" against a form and a sheet
    sitting open on the screen, because the sweep only ever looked for a NEW
    widget.

    A Gtk.Revealer AND NOTHING ELSE. The obvious wider rule -- anything that
    was not visible and is visible now -- was tried and is wrong, measured on
    three items already proved not to ask:

        journal   Show / Hide Entries   Box   ['He said "hello" - and left', ...]
        tasks     Look for New Events   Box   ['No events', 'Add one in the box below.']

    Rebuilding a list, swapping an empty state for a populated one and
    un-hiding a row all make widgets visible, and none of them asks anybody
    anything -- the same trap _card_tokens documents for card attributes. A
    Revealer is different in kind: an app puts one in the tree precisely so a
    surface can be brought up deliberately, and set_reveal_child(True) is that
    deliberate act with nothing else's meaning attached to it.
    """
    out = []
    for widget in _tree(app, Gtk):
        try:
            if isinstance(widget, Gtk.Revealer) and not widget.get_reveal_child():
                out.append(widget)
        except Exception:                                         # noqa: BLE001
            pass
    return out


def _came_up(waiting, Gtk):
    """Which of the held-back Revealers are now open."""
    out = []
    for widget in waiting:
        try:
            if widget.get_reveal_child():
                out.append(widget)
        except Exception:                                         # noqa: BLE001
            pass
    return out


def _is_new(widget, before):
    """Is this widget absent from the list captured before the action?

    Compared by OBJECT, and `before` is a list rather than a set of ids for a
    reason that took three apps' false verdicts to find. See _held_children.
    """
    return not any(widget is x for x in before)


def _new_overlay_widgets(app, before):
    overlay = getattr(app, "_overlay", None)
    if overlay is None:
        return []
    return [child for child in overlay.get_children() if _is_new(child, before)]


def _held_children(app):
    """The overlay's children AS OBJECTS, in a list that HOLDS them.

    This used to be ``{id(x) for x in overlay.get_children()}``, and a set of
    ints is not an identity at all. PyGObject builds a fresh Python wrapper
    each time get_children() hands a widget back and frees it as soon as the
    wrapper is dropped; an int keeps nothing alive, so the next call allocates
    a new wrapper that may or may not land on the address just freed.

    MEASURED, not reasoned: sampling ONE unchanged overlay child eight times
    returned two different id() values for the same widget

        no-ref ids: [...070272, ...070272, ...070272, ...070272,
                     ...070272, ...070336, ...070272, ...070336]

    and holding a reference collapsed it to one, with
    ``overlay.get_children()[0] is held[0]`` True.

    So "a new child appeared in the overlay" was really "the allocator moved",
    which a menu item that builds a lot of widgets makes likely. It convicted
    academics' New Lecture, cookbook's Cut and journal's New Entry of asking a
    question none of them asks — all three drive the SAME single overlay child
    that was there before — and it is the mechanism behind this gate's 52-55
    wobble on an unchanged tree, because address reuse is not repeatable.

    The list returned here is what keeps the wrappers alive, so identity holds
    for as long as the comparison needs it.
    """
    overlay = getattr(app, "_overlay", None)
    if overlay is None:
        return []
    try:
        return list(overlay.get_children())
    except Exception:
        return []


def _card_tokens(app):
    """Which card-ish attributes currently hold something.

    HOUSEKEEPING ONLY. This is no longer evidence that anything was asked —
    see the note at the call site in the sweep for the measurement that
    retired it — but it is still how the harness puts an app back the way it
    found it. When the sweep tears a card out of the overlay, the attribute
    the app parked it on has to go back to None as well, or the app spends the
    rest of the sweep believing a prompt it can no longer see is still up and
    every later item early-returns against it. Getting this wrong loses
    coverage; it cannot invent a verdict.

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


def _takes_it_back(action):
    """Is this callback an undo/redo, whatever its label says?

    By the CALLBACK, never the label. nbapp.undo_menu_items builds the pair
    from the history's own bound methods and nbcommands.dynamic_item passes
    them straight through, so `action.__self__` is the history object and
    `action.__name__` is "undo" or "redo" -- true in every one of the
    seventeen interface languages, where the label reads "Annuler Supprimer la
    recette" and matches nothing an English string test could look for.

    Duck-typed on the history rather than isinstance(nbapp.UndoHistory): a few
    apps hand their own adapter to the same builder, and an adapter that
    offers undo/redo/can_undo is the same thing for this purpose.
    """
    owner = getattr(action, "__self__", None)
    if owner is None or getattr(action, "__name__", "") not in ("undo", "redo"):
        return False
    return all(hasattr(owner, x) for x in ("undo", "redo", "can_undo"))


def _restore(app, Gtk, popped_menus, before_overlay, before_windows,
             before_cards):
    """Return the harness to its pre-action state.

    Shared cards are overlay children; legacy dialogs are toplevels.  Without
    this, the first asking action would hide every later asking action.  A
    popped menu holds a GRAB, so it is put down the way a person would rather
    than left for the toplevel sweep below to destroy underneath GTK.

    `popped` and NOT `menu`: this loop used to rebind the item sweep's OUTER
    loop variable, so from the first popped menu onward every later record in
    that app carried a Gtk.Menu where the menu's NAME belongs -- silently wrong
    in a finding, and fatal for the unjudged records, which take the same field
    and have to survive json.dumps (cookbook pops a menu, so cookbook was the
    app that proved it).

    A FUNCTION, and not an inline block, because there are now two ways out of
    an item: judged, and exempt-because-it-only-flashed. The exempt path used
    to `continue` straight past the whole of this, so anything a refusing item
    did put on screen stayed there for every later item to trip over.
    """
    for popped in popped_menus:
        try:
            popped.popdown()
        except Exception:                                         # noqa: BLE001
            pass
    overlay = getattr(app, "_overlay", None)
    if overlay is not None:
        for child in list(overlay.get_children()):
            if _is_new(child, before_overlay):
                try:
                    overlay.remove(child)
                except Exception:                                 # noqa: BLE001
                    pass
    for win in list(Gtk.Window.list_toplevels()):
        if _is_new(win, before_windows):
            try:
                win.destroy()
            except Exception:                                     # noqa: BLE001
                pass
    # Put back whatever this item made appear, so the first asking action does
    # not hide every later one.
    for attr in _card_tokens(app) - before_cards:
        try:
            setattr(app, attr, None)
        except Exception:                                         # noqa: BLE001
            pass


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
    # AND THE CALL ITSELF IS THE EVIDENCE. Stubbing the picker removed the one
    # surface some items raise: usbwriter's Choose an Image… hands straight to
    # the file chooser, so with the chooser stubbed out the item looked like it
    # acted at once. A picker that was opened is a question that was asked,
    # exactly as a modal that was run is, so it is recorded the same way.
    asked_by_picker = []

    def _picked(*args, **kwargs):
        asked_by_picker.append(True)
        return picker_path

    nbpicker.open_file = _picked
    nbpicker.save_file = _picked

    # AND A MENU THAT WAS POPPED IS A QUESTION TOO. cookbook's Move to
    # Category offers every category, No category and New Category in a
    # Gtk.Menu anchored to the kicker -- and whether that menu is MAPPED here
    # is not repeatable: five probes of an unchanged tree gave 0,0,0,0,1, the
    # odd one out being the run where GTK declined to pop it ("no trigger
    # event for menu popup") so nothing was on screen to find. Asking to pop a
    # menu is the app asking, whether or not a headless X server obliges, and
    # recording the call is what makes the answer the same every time.
    asked_by_menu = []
    popped_menus = []

    def _record_popup(real):
        def wrapper(menu, *args, **kwargs):
            try:
                asked_by_menu.append(_offers_a_choice(menu, Gtk))
            except Exception:                                     # noqa: BLE001
                asked_by_menu.append(True)
            popped_menus.append(menu)
            return real(menu, *args, **kwargs)
        return wrapper

    for _popup in ("popup_at_widget", "popup_at_pointer", "popup_at_rect",
                   "popup"):
        _real = getattr(Gtk.Menu, _popup, None)
        if _real is not None:
            setattr(Gtk.Menu, _popup, _record_popup(_real))

    # A MENU ITEM MAY LAUNCH ANOTHER APP, and one does: music.py's Open Music
    # Folder starts the Finder with subprocess.Popen. Left alone that child
    # inherits this probe's stdout and stderr and then OUTLIVES it, so the
    # parent's pipe never reaches EOF and the sweep reported
    #
    #     music.py: probe blocked or exceeded 90 seconds
    #
    # about a probe that had already finished. Timing every music item proves
    # it: each one returns in the same 1.23s settle as everywhere else, while
    # the surrounding capture took 2m55 and ended the moment the leaked Finder
    # was killed by hand. Nothing blocks the UI thread; the gate was measuring
    # a grandchild's lifetime.
    #
    # Popen still really runs, so no callback's control flow changes -- only
    # the handles it hands on, and the process group it lands in. What it
    # started is stopped again at the end of the probe, the same housekeeping
    # the sweep already does for windows it made appear: before this, every
    # run left a live Finder behind on the developer's display.
    spawned = []
    _real_popen = subprocess.Popen

    class _DetachedPopen(_real_popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("stdout", subprocess.DEVNULL)
            kwargs.setdefault("stderr", subprocess.DEVNULL)
            kwargs.setdefault("start_new_session", True)
            super().__init__(*args, **kwargs)
            spawned.append(self)

    subprocess.Popen = _DetachedPopen
    # A legacy Gtk.Dialog.run() spins its own modal loop until a person
    # dismisses it, so the probe hung there for ever. Diagnosed by stack, not
    # guessed: calculator.py:_store_dialog was sitting in Gtk.py run().
    #
    # The dialog is still CONSTRUCTED and still registers as a new toplevel,
    # so the verdict this gate exists to make — did the item ask? — is
    # unchanged. Only the waiting is removed.
    #
    # AND THE ANSWER HAS TO BE TAKEN WHILE THE DIALOG IS UP. The comment above
    # assumed the dialog would still be a toplevel when the sweep looked, and
    # for calculator it is. For an app that writes
    #
    #     resp = dlg.run()
    #     if resp != Gtk.ResponseType.OK:
    #         dlg.destroy()
    #         return
    #
    # it is not: forcing CANCEL sends the callback straight down its own
    # cleanup path, so the dialog is destroyed before action() returns and the
    # sweep sees an empty screen. That is what convicted academics' New Class,
    # Add an Assignment and Edit Class of acting at once -- three items that
    # each put a titled form with a name field on the screen and wait.
    #
    # So the verdict is recorded HERE, at the one moment the surface is
    # provably up and provably being waited on, by the same _offers_a_choice
    # used everywhere else.
    asked_modally = []

    def _no_wait_run(dialog, *args, **kwargs):
        try:
            asked_modally.append(_offers_a_choice(dialog, Gtk))
        except Exception:
            asked_modally.append(True)
        return Gtk.ResponseType.CANCEL

    Gtk.Dialog.run = _no_wait_run
    flashes = []
    # Refusals are deliberately neutral: they neither ask nor act.  Apps use
    # several names/signatures for their transient status method.
    #
    # ONLY WHAT IS CALLABLE. This loop used to replace EVERY attribute whose
    # name starts with "_flash", and thirteen apps park a non-callable there --
    # cookbook's `self._flash_until = 0.0`, music's `_flash_serial = 0`,
    # packages' `_flash_timer = None`, video's `_flash_id = 0`. Overwriting a
    # float with a function makes the app's next comparison raise
    # (`TypeError: '<' not supported between instances of 'float' and
    # 'function'`), the callback was counted as "skipped", and the item was
    # never judged at all: cookbook lost 6 of its 18 candidates, and the sweep
    # still printed a confident violation count over the hole. Stubbing the
    # METHOD is the whole intent; the counters and timestamps beside it are the
    # app's own state and must be left exactly as the app left them.
    for attr in dir(app):
        if not attr.startswith("_flash"):
            continue
        try:
            if not callable(getattr(app, attr)):
                continue
            setattr(app, attr, lambda *a, **k: flashes.append(str(a[0]) if a else ""))
        except Exception:                                         # noqa: BLE001
            pass
    findings = []
    errors = []
    settle_rounds = []
    unjudged = []
    neutral = []
    disabled = []
    handoffs = []
    candidates = judged = 0

    def judge(menu, label, action):
        """Invoke ONE enabled menu item and record what it did.

        A function rather than the two nested loops it used to be, so the
        sweep can choose WHEN an item runs. See the deferral below: Undo takes
        back the very state the later items need to be reachable at all.
        """
        nonlocal judged
        # Lists, not id sets: they HOLD the wrappers, which is what makes
        # "the same widget" mean the same widget. See _held_children.
        before_windows = list(Gtk.Window.list_toplevels())
        before_overlay = _held_children(app)
        before_cards = _card_tokens(app)
        del flashes[:]
        del asked_modally[:]
        del asked_by_picker[:]
        del asked_by_menu[:]
        del popped_menus[:]
        before_waiting = _waiting(app, Gtk)
        try:
            action()
        except Exception as exc:
            # A callback crash is owned by construct/selftests, not turned
            # into a made-up ellipsis verdict here -- but it is NOT
            # forgotten. An item that raises is an item this gate did not
            # judge, and a sweep that prints "0 violations" while dropping
            # items on the floor is telling you about the items it looked
            # at, not about the app. Every one is counted and NAMED in the
            # output, and the summary carries the judged/unjudged split, so
            # coverage can never quietly fall while the verdict stays
            # green.
            unjudged.append([menu,
                             label.lstrip().split("    ", 1)[0].strip(),
                             "%s: %s" % (type(exc).__name__, exc)])
            return
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
                            if _is_new(x, before_windows) and _on_screen(x)] +
                           [x for x in _new_overlay_widgets(app, before_overlay)
                            if _on_screen(x)])
                if showing:
                    first_seen = _round + 1
        if first_seen is not None:
            settle_rounds.append((name, label.strip()[:28], first_seen))
        appeared = ([x for x in Gtk.Window.list_toplevels()
                     if _is_new(x, before_windows) and _on_screen(x)] +
                    [x for x in _new_overlay_widgets(app, before_overlay)
                     if _on_screen(x)])
        asked = bool(appeared) and any(_offers_a_choice(x, Gtk)
                                       for x in appeared)
        # A modal that was raised and dismissed inside the callback asked
        # just as loudly as one still standing when the sweep looks, and a
        # file chooser that was opened asked too.
        if any(asked_modally) or any(asked_by_picker) or any(asked_by_menu):
            asked = True
        # A surface that was already built and merely waiting is the third
        # way an app asks; see _waiting.
        if not asked and any(_offers_a_choice(x, Gtk)
                             for x in _came_up(before_waiting, Gtk)):
            asked = True
        # A CARD ATTRIBUTE IS NOT A SURFACE, and it used to be counted as
        # one: `if not appeared and (_card_tokens(app) - before_cards)`
        # convicted an item because an attribute whose NAME contains
        # "card" or "prompt" came to hold something. Every other line
        # above watches a surface be raised — an overlay child, a shown
        # toplevel, a modal being run, a picker being opened, a menu being
        # popped, a Revealer being revealed. This one watched a name be
        # bound, and it was consulted only when nothing had appeared, i.e.
        # only in the cases where the app had raised nothing at all.
        #
        # MEASURED across all 32 swept apps and all 347 invoked items:
        # exactly five verdicts ever rested on it, and not one was a
        # question.
        #
        #   settings.py  Backup           _bk_dest_card   the "Where to
        #       copy it" section box of the Backup page, built the first
        #       time that page is navigated to. View > Backup is
        #       navigation; nothing is asked, and the label rightly
        #       carries no ellipsis.
        #   video.py     Add Title Card   _cardpv_cache   a lazily created
        #   video.py     Add Credits      _cardpv_cache   dict of rendered
        #       title-card pixbufs. The clip is inserted at once (undo
        #       banks it as "Undo Add Title Card"); the first title clip
        #       just makes the render cache exist.
        #   contacts.py  New Contact…     _card_col       the detail
        #       pane's card column, which goes from None to a Box the
        #       first time the book is non-empty. That one was hiding a
        #       real defect rather than inventing one: _new_contact
        #       appends, selects, edits and SAVES before anything is
        #       asked, so the ellipsis was promising a form that never
        #       comes. Fixed in contacts.py, not ledgered.
        #
        # So the name heuristic is gone as evidence. _card_tokens survives
        # for the housekeeping below, which is a different job with a
        # different failure mode: putting an app's card attribute back to
        # None after the sweep has torn the widget out keeps the app
        # coherent for the next item, and getting that wrong loses
        # coverage rather than inventing a verdict.
        #
        # A REFUSAL IS EXEMPT, AND IT SAYS SO OUT LOUD. An item that put a
        # transient message up and raised nothing neither asked nor acted
        # -- "No recipe to export" on an empty sweep is not the app failing
        # to keep its label's promise -- so no verdict is formed. That is
        # still an item this gate did not judge, and it used to vanish
        # between the "invoked" count and the violation count with nothing
        # to show it had ever been looked at. Named and counted, like every
        # other kind of skip.
        if flashes and not asked:
            neutral.append([menu,
                            label.lstrip().split("    ", 1)[0].strip(),
                            (flashes[0] or "")[:60]])
            _restore(app, Gtk, popped_menus, before_overlay, before_windows,
                     before_cards)
            return
        judged += 1
        promised = label.lstrip().split("    ", 1)[0].strip().endswith("…")
        if asked != promised:
            findings.append([menu, label.lstrip().split("    ", 1)[0].strip(),
                             "asks with no ellipsis" if asked else
                             "promises to ask but acts at once"])
        _restore(app, Gtk, popped_menus, before_overlay, before_windows,
                 before_cards)

    # UNDO IS SWEPT LAST, because Undo's whole job is to take back what the
    # sweep just built. cookbook proved it: File > New Recipe makes the one
    # recipe in the store, Edit > Undo New Recipe immediately takes it away,
    # and by the time the Cook menu is read `self._cur()` is None -- so Start
    # Cooking, Move to Category…, Duplicate Recipe and Delete Recipe are all
    # handed to this sweep with a callback of None and cannot be judged at
    # all. Four items, and they are the destructive four, which is exactly
    # where an ellipsis mistake costs the most.
    #
    # Deferred rather than dropped: Undo and Redo are still driven, still
    # judged, and still counted -- last, when there is nothing left after them
    # that needs the document to exist. Detected by the callback, never by the
    # label: nbapp.undo_menu_items hands the history's own bound `undo`/`redo`
    # through nbcommands.dynamic_item untouched, and the label is already
    # translated by then ("Annuler Supprimer la recette").
    deferred = []
    for menu in tuple(getattr(app, "menus", ())) + (getattr(app, "app_name", ""),):
        try:
            items = app.menu_items(menu)
        except Exception as exc:
            errors.append([menu, "menu_items failed: %s" % exc])
            continue
        for item in items:
            # BY VALUE, not by identity: nbcommands defines its own
            # `SEP = ("-", None)`, a different object with the same value, so
            # every separator in a registry-built menu slipped past an `is`
            # test and was counted as an item with no callback.
            if (not item or item == nbapp.SEP or not isinstance(item, tuple)
                    or len(item) < 2):
                continue
            label, action = item[0], item[1]
            label = str(label)
            # A GREYED-OUT ITEM IS NOT A JUDGED ITEM. There is no callback to
            # invoke, so this gate can say nothing about the promise its label
            # makes -- but it used to say nothing about the item either, which
            # is how four of cookbook's items could leave the sweep entirely
            # without moving a single number. Named, so that a state the sweep
            # cannot reach shows up as a gap instead of as silence.
            if action is None:
                # THE ELLIPSIS IS KEPT. bare_label() strips it, and a greyed
                # list that prints "Delete Class" for an item labelled "Delete
                # Class…" hides the one thing a reader of this list needs:
                # whether the item the sweep could not reach is making a
                # promise at all.
                disabled.append([menu,
                                 label.lstrip().split("    ", 1)[0].strip()])
                continue
            candidates += 1
            if skip_label(label):
                # NAMED, not just counted. Which labels this sweep hands off is
                # the whole of what it does not look at on purpose, and until
                # the list was printed nobody could see that "Display Mode…"
                # had joined it by accident. Collected here, aggregated into
                # one line by the parent.
                handoffs.append(bare_label(label))
                continue
            if _takes_it_back(action):
                deferred.append((menu, label, action))
                continue
            judge(menu, label, action)
    for menu, label, action in deferred:
        judge(menu, label, action)
    try:
        app.destroy()
    except Exception:
        pass
    for proc in spawned:
        try:
            proc.terminate()
        except Exception:
            pass
    if settle_rounds:
        worst = max(r for _n, _l, r in settle_rounds)
        late = [(l, r) for _n, l, r in settle_rounds if r > 3]
        print("SETTLE %s worst=%d late=%s" % (name, worst, late[:4]),
              file=sys.stderr)
    print(json.dumps({"findings": findings, "judged": judged,
                      "handoffs": handoffs, "unjudged": unjudged,
                      "neutral": neutral, "disabled": disabled,
                      "candidates": candidates, "errors": errors}))
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        return probe(sys.argv[2])
    modules = app_modules()
    carried = tracked()
    counts = {}
    pending = []
    bad = 0
    total_findings = total_judged = total_unjudged = 0
    total_neutral = total_disabled = 0
    handed_off = []
    for name in modules:
        home = tempfile.mkdtemp(prefix="nb-menu-promise-%s-" % name[:-3])
        env = dict(os.environ)
        env["NB_HOME"] = home
        # FILES, NOT PIPES. capture_output waits for the write end to close,
        # and any process the probe starts inherits it -- so one leaked child
        # made this timeout fire on a probe that had already exited, and the
        # message said "blocked" about something that was not. Written to
        # files, the timeout can only ever mean what it says. The probe stops
        # what it starts as well (see _DetachedPopen); this is the second
        # wall, so a stray descendant can never wedge the gate again.
        out_path = os.path.join(home, "probe.out")
        err_path = os.path.join(home, "probe.err")
        returncode = 0
        timed_out = False
        with open(out_path, "w", encoding="utf-8") as out_fh, \
                open(err_path, "w", encoding="utf-8") as err_fh:
            try:
                returncode = subprocess.run(
                    [sys.executable, SELF, "--probe", name], cwd=ROOT, env=env,
                    stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh,
                    timeout=PROBE_BUDGET).returncode
            except subprocess.TimeoutExpired:
                timed_out = True
        if timed_out:
            bad += 1
            print("%s: probe blocked or exceeded %d seconds" % (name, PROBE_BUDGET))
            continue
        stdout = open(out_path, encoding="utf-8", errors="replace").read()
        stderr = open(err_path, encoding="utf-8", errors="replace").read()
        sys.stderr.write(stderr)
        lines = [x for x in stdout.splitlines() if x.strip().startswith("{")]
        if returncode or not lines:
            bad += 1
            detail = (stderr or stdout).strip().splitlines()
            print("%s: probe failed%s" % (name, ": " + detail[-1] if detail else ""))
            continue
        data = json.loads(lines[-1])
        if data.get("error"):
            bad += 1
            print("%s: %s" % (name, data["error"]))
            continue
        errors = data.get("errors", [])
        candidates = int(data.get("candidates", 0))
        measured = (int(data.get("judged", 0)) + len(data.get("handoffs", [])) +
                    len(data.get("unjudged", [])) + len(data.get("neutral", [])))
        if errors or candidates == 0 or measured == 0:
            bad += 1
            reason = ("; ".join(str(x) for x in errors) if errors else
                      "zero menu actions were measured")
            print("%s: probe did no trustworthy work: %s" % (name, reason))
            continue
        findings = data["findings"]
        counts[name] = len(findings)
        total_findings += len(findings)
        total_judged += data["judged"]
        handed_off.extend(data["handoffs"])
        # A REFUSAL IS A COVERAGE HOLE TOO, and this is the one place it can be
        # seen. The probe forms no verdict for an item that only put a
        # transient message up, which is right -- but the count belongs in the
        # open, beside the judged number, so nobody reads "0 violations" as "0
        # violations across everything the menus offer".
        neutral = data.get("neutral", [])
        total_neutral += len(neutral)
        for _menu, label, said in neutral:
            print("%s: %s: not judged, status only: %s" % (name, label, said))
        # AND THE ITEMS THE SWEEP NEVER GOT TO OFFER. A greyed-out item has no
        # callback to invoke, so no verdict is possible -- but which items the
        # empty-document sweep cannot reach is the single most useful thing to
        # know about this gate's reach, and it used to be the one number
        # nothing anywhere printed. One line per app; the labels are what a
        # reader needs to judge whether the sweep is looking at the app or
        # past it.
        greyed = data.get("disabled", [])
        total_disabled += len(greyed)
        if greyed:
            print("%s: %d greyed out in the swept state, not judged: %s"
                  % (name, len(greyed),
                     ", ".join(sorted({label for _menu, label in greyed}))))
        # AN ITEM THIS GATE COULD NOT JUDGE IS A HOLE IN THE GATE, and it fails
        # the same way a violation does. There is no ledger for these on
        # purpose: the only number that cannot hide a regression is zero, and
        # the alternative -- an allowance per app -- is exactly the headroom
        # that let the violation ledger reach 55 rows nobody had looked at.
        unjudged = data.get("unjudged", [])
        total_unjudged += len(unjudged)
        if unjudged:
            bad += 1
            for _menu, label, why in unjudged:
                print("%s: %s: NOT JUDGED — the callback raised %s"
                      % (name, label, why))
        if carried is not None and name not in carried:
            if findings:
                pending.append((name, findings))
            continue
        allowed = DEBT.get(name, 0)
        if len(findings) > allowed:
            bad += 1
            for _menu, label, why in findings:
                print("%s: %s: %s" % (name, label, why))
    if not counts:
        # Zero measured violations is not evidence when every app probe failed.
        # Stop before comparing the debt ratchet to an empty sample.
        print("TALLY")
        print("%d apps selected; 0 apps probed" % len(modules))
        print("RESULT: NOT RUN — no app probe completed")
        return 2
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
    # THE HANDOFF LIST, IN FULL AND IN ONE LINE. A count alone is what let
    # "Display Mode…" sit in this bucket unnoticed; the distinct labels are
    # short enough to read and long enough to argue with.
    print("HANDED OFF (not judged): %d items, by label: %s"
          % (len(handed_off), ", ".join(sorted(set(handed_off)))))
    total_handoffs = len(handed_off)
    print("%d apps swept; %d items judged; %d skipped (%d headless handoffs, "
          "%d status only, %d not judged, %d greyed out); %d violations found"
          % (len(modules), total_judged,
             total_handoffs + total_neutral + total_unjudged + total_disabled,
             total_handoffs, total_neutral, total_unjudged, total_disabled,
             total_findings))
    print("RESULT: " + ("PASS" if not bad else "FAILED: %d app(s)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
