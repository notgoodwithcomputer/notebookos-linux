#!/usr/bin/env python3
"""Real-use selftest for Workout: the six defects a person meets by USING it.

Each check drives the app the way a person does — the real New Exercise sheet,
the real "Log a set" button, real Ctrl+Z through the app's key ladder, the real
menu-bar buttons — and then looks at what the app says and what is on disk.

  WO-1  a day counts only when EVERY exercise met its own goal; over-doing one
        exercise does not pay for skipping another (and a past day is judged
        against the per-exercise goal it was logged against, which has to
        survive a restart).
  WO-2  logging a set is its own named undo step, so Ctrl+Z takes back ONE set
        instead of silently discarding the day under another step's label.
  WO-3  the actions menu is not named after the app, so the bar carries one
        Workout button and About Workout is reachable.
  WO-4  that button loses its open look when its menu closes.
  WO-5  Save cannot be pressed until the exercise has a name.
  WO-6  the damaged-history notice gives way once the person's own work saves.

Run as:  tools/guestrun.sh python3 tools/workout_realuse_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, HERE)
sys.path.insert(0, DE)

import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk                              # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def scenario(name):
    """Wrap one drive; anything it raises FAILS the check by name rather than
    taking the suite down with a traceback."""
    def wrap(fn):
        def run():
            try:
                fn()
            except Exception as exc:                              # noqa: BLE001
                check(name, False, "[not reached: %r]" % (exc,))
        return run
    return wrap


def report():
    print("\n%d checks, %d passed, %d failed"
          % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
    print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
    sys.exit(0 if all(RESULTS) else 1)


# ---------------------------------------------------------------------------
# Model-level checks: these need no X server, so the rule they pin is measured
# on every host the suite runs on.
# ---------------------------------------------------------------------------
import workout as wo_model                                       # noqa: E402


def model(data):
    """The real model methods on an instance GTK never had to build."""
    app = wo_model.Workout.__new__(wo_model.Workout)
    app.data = data
    app.sel = 0
    return app


def past_day_rule():
    """A past day keeps the per-exercise goal it was run against."""
    real = wo_model.today_key
    wo_model.today_key = lambda when=None: "2026-05-04"
    try:
        skipped = model({
            "exercises": [{"id": "a", "name": "A", "sets": 3, "reps": 10},
                          {"id": "b", "name": "B", "sets": 3, "reps": 10}],
            # six sets of A yesterday, B never touched
            "log": {"2026-05-03": {"a": [10, 10, 10, 10, 10, 10]}},
            "goals": {"2026-05-03": 6},
            "goal_sets": {"2026-05-03": {"a": 3, "b": 3}}})
        both = model({
            "exercises": [{"id": "a", "name": "A", "sets": 3, "reps": 10},
                          {"id": "b", "name": "B", "sets": 3, "reps": 10}],
            "log": {"2026-05-03": {"a": [10, 10, 10], "b": [10, 10, 10]}},
            "goals": {"2026-05-03": 6},
            "goal_sets": {"2026-05-03": {"a": 3, "b": 3}}})
        legacy = model({
            "exercises": [{"id": "a", "name": "A", "sets": 3, "reps": 10}],
            # a store written before per-exercise goals were stamped: its
            # total is all that is left to judge it by, and a day already
            # banked must not be taken away by the newer rule.
            "log": {"2026-05-03": {"a": [10, 10, 10]}},
            "goals": {"2026-05-03": 3}})
        check("WO-1 a past day that skipped an exercise is not a completed day",
              skipped._day_complete("2026-05-03") is False
              and skipped._streak() == (0, 0),
              (skipped._day_complete("2026-05-03"), skipped._streak()))
        check("WO-1 a past day that met every exercise still counts",
              both._day_complete("2026-05-03") and both._streak() == (1, 1),
              (both._day_complete("2026-05-03"), both._streak()))
        check("WO-1 a day stamped before per-exercise goals keeps its streak",
              legacy._day_complete("2026-05-03") and legacy._streak() == (1, 1),
              (legacy._day_complete("2026-05-03"), legacy._streak()))
    finally:
        wo_model.today_key = real


def stamp_survives_a_restart():
    """The per-exercise goal has to come BACK off disk, not just go onto it.

    The rule that decides whether a past day counted lives in the store
    ("goal_sets"); a launch that does not read it back judges every past day on
    its total again, so the whole of WO-1 quietly returns on the next start
    while every other check here still passes (they all measure one session).
    """
    import tempfile
    import types
    day = "2026-05-03"
    payload = {
        "exercises": [{"id": "a", "name": "A", "sets": 3, "reps": 10},
                      {"id": "b", "name": "B", "sets": 3, "reps": 10}],
        # six sets of A that day, B never touched: the total was met, the goal
        # was not. A day where BOTH were done is loaded beside it, so a reader
        # that simply refuses everything cannot pass this.
        "log": {day: {"a": [10, 10, 10, 10, 10, 10]},
                "2026-05-02": {"a": [10, 10, 10], "b": [10, 10, 10]}},
        "goals": {day: 6, "2026-05-02": 6},
        "goal_sets": {day: {"a": 3, "b": 3}, "2026-05-02": {"a": 3, "b": 3}},
    }
    td = tempfile.mkdtemp(prefix="nb-wo-stamp-")
    path = os.path.join(td, "workout.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    real_store = wo_model.STORE
    wo_model.STORE = path
    try:
        loaded = wo_model.Workout._load(types.SimpleNamespace())
    finally:
        wo_model.STORE = real_store
        shutil.rmtree(td, ignore_errors=True)
    app = model(loaded)
    check("WO-1 the per-exercise goal survives a restart",
          loaded.get("goal_sets", {}).get(day) == {"a": 3, "b": 3}
          and app._day_complete(day) is False
          and app._day_complete("2026-05-02") is True,
          (loaded.get("goal_sets"), app._day_complete(day),
           app._day_complete("2026-05-02")))


def undo_steps_source():
    """Logging is wired into the history the way every other edit is."""
    import ast
    src = open(os.path.join(DE, "workout.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    methods = {n.name: ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)}
    ok = True
    detail = []
    for name, label in (("_on_log", "Log a Set"), ("_on_undo", "Remove Set")):
        body = methods.get(name, "")
        one = ("self.undo.checkpoint('%s')" % label in body
               and "self.undo.commit()" in body
               and body.find("self.undo.checkpoint(")
               < body.find("self._save_or_rollback(before)")
               < body.find("self.undo.commit()"))
        ok = ok and one
        detail.append((name, one))
    check("WO-2 logging and taking back a set are named undo steps in source",
          ok, detail)


past_day_rule()
stamp_survives_a_restart()
undo_steps_source()

if not Gtk.init_check()[0]:
    print("\n(no display: the drive-through checks below were not run)")
    report()


# ---------------------------------------------------------------------------
# Drive-through checks
# ---------------------------------------------------------------------------
import appdrive                                                  # noqa: E402
import cairo                                                     # noqa: E402

HOMES = tempfile.mkdtemp(prefix="nb-wo-realuse-")


def drive(tag, **kw):
    return appdrive.Drive("workout", home=os.path.join(HOMES, tag), **kw)


def store_of(d):
    return os.path.join(d.home, ".config", "notebook", "workout.json")


def read_store(d):
    with open(store_of(d), encoding="utf-8") as fh:
        return json.load(fh)


def walk(root):
    out = [root]
    i = 0
    while i < len(out):
        w = out[i]
        i += 1
        if isinstance(w, Gtk.Container):
            out.extend(w.get_children())
    return out


def dlg_button(dlg, label):
    for w in walk(dlg):
        if isinstance(w, Gtk.Button):
            try:
                if w.get_label() == label:
                    return w
            except Exception:                                     # noqa: BLE001
                pass
    return None


def with_dialog(action, filler, timeout_ms=120):
    """Fire `action` (which blocks inside the sheet's own dlg.run()) and drive
    the sheet from a timeout running inside that nested main loop."""
    seen = {"dlg": None}

    def _tick():
        dlg = None
        for w in Gtk.Window.list_toplevels():
            if isinstance(w, Gtk.Dialog) and w.get_visible():
                dlg = w
        if dlg is None:
            return True
        seen["dlg"] = dlg
        try:
            filler(dlg)
        except Exception as exc:                                  # noqa: BLE001
            print("   (sheet filler raised %r)" % (exc,))
            dlg.response(Gtk.ResponseType.CANCEL)
        return False

    source = GLib.timeout_add(timeout_ms, _tick)
    try:
        action()
    finally:
        # An action that never reached its sheet (a menu item that has moved,
        # say) must not leave this timeout armed: it would fire inside the NEXT
        # scenario's nested loop and fill a sheet that scenario is driving
        # itself, turning one failure into several unrelated ones.
        if seen["dlg"] is None:
            try:
                GLib.source_remove(source)
            except Exception:                                     # noqa: BLE001
                pass
    return seen["dlg"]


def paint(widget, settle=0.25):
    """The middle pixel of `widget` as it is actually drawn right now.

    Pixels, not the style context: Gtk.StyleContext.get_background_color for
    another state hands back the CURRENT state's colour, so it cannot answer
    "does the held button look different from the pressable one". The loop
    settles first — a style change that has not been processed yet redraws the
    OLD colours, which reads as a fix that did nothing.
    """
    end = time.monotonic() + settle
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)
    alloc = widget.get_allocation()
    w, h = max(alloc.width, 1), max(alloc.height, 1)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    widget.draw(cr)
    surf.flush()
    data = bytes(surf.get_data())
    row = surf.get_stride() * (h // 2)
    off = row + (w // 2) * 4
    return tuple(data[off:off + 4])


def name_entry(dlg):
    return [w for w in walk(dlg)
            if isinstance(w, Gtk.Entry) and not isinstance(w, Gtk.SpinButton)][0]


def add_exercise(d, name, sets=3, reps=10):
    """Add one through the REAL sheet: type the name, set the spins, Save."""
    def fill(dlg):
        ent = name_entry(dlg)
        ent.grab_focus()
        for ch in name:
            ent.insert_text(ch, -1)
            ent.set_position(-1)
        spins = [w for w in walk(dlg) if isinstance(w, Gtk.SpinButton)]
        spins[0].set_value(sets)
        spins[1].set_value(reps)
        dlg_button(dlg, "Save").clicked()
    with_dialog(lambda: d.menu_action("File", "New Exercise"), fill)
    d.pump(0.1)


def log_set(d, row=0):
    d.find(Gtk.Button, label="Log a set")[row].clicked()
    d.pump(0.05)


def ring_for_today(d):
    """The week strip's ring for today, read off the real widget tree."""
    today = d.mod.today_key()
    days = d.mod._week_days()
    idx = days.index(today)
    row = d.app.week_box.get_children()[idx]
    for w in walk(row):
        if isinstance(w, d.mod.Ring):
            return w
    raise LookupError("no ring in today's row")


@scenario("WO-1 one exercise skipped leaves the day unfinished")
def wo1_today():
    d = drive("wo1")
    try:
        add_exercise(d, "Push-ups", 3, 10)
        add_exercise(d, "Squats", 3, 10)
        for _ in range(6):
            log_set(d, 0)                     # six push-ups, no squats at all
        d.pump(0.1)
        today = d.mod.today_key()
        status = d.app.status.get_text()
        ring = ring_for_today(d)
        check("WO-1 one exercise skipped leaves the day unfinished",
              d.app._day_complete(today) is False
              and d.app._streak() == (0, 0)
              and "Today is done" not in status,
              (d.app._day_complete(today), d.app._streak(), status))
        check("WO-1 the week ring for that day is not full",
              ring.frac < 1.0 and ring.frac > 0.0, ring.frac)
        # .get all the way down: an absent stamp must fail THIS check by name
        # rather than raise and take the rest of the scenario with it.
        stamped = read_store(d).get("goal_sets", {}).get(today) or {}
        check("WO-1 the goal is stamped exercise by exercise",
              sorted(stamped.values()) == [3, 3],
              read_store(d).get("goal_sets"))
        # ...and the day DOES land once the second exercise is done too, so the
        # rule is not simply "never complete".
        for _ in range(3):
            log_set(d, 1)
        d.pump(0.1)
        status = d.app.status.get_text()
        check("WO-1 finishing every exercise banks the day",
              d.app._day_complete(today) and d.app._streak()[0] == 1
              and "Today is done" in status
              and ring_for_today(d).frac >= 1.0,
              (d.app._streak(), status))
    finally:
        d.close()


@scenario("WO-2 Ctrl+Z after logging takes back one set, not the day")
def wo2_undo():
    d = drive("wo2")
    try:
        add_exercise(d, "Push-ups", 5, 10)
        add_exercise(d, "Squats", 3, 15)
        add_exercise(d, "Burpees", 2, 5)
        d.app.sel = 2
        d.app._refresh()
        d.mod._confirm = lambda *a, **k: True      # the real delete, confirmed
        d.menu_action("Exercise", "Delete Exercise")
        d.pump(0.1)
        for _ in range(5):
            log_set(d, 0)
        log_set(d, 1)
        before = json.loads(json.dumps(d.app.data["log"]))
        label = d.menu("Edit")[0][0]
        check("WO-2 the Edit menu names the step Ctrl+Z will take back",
              label.startswith("Undo Log a Set"), label)
        d.key("z", ctrl=True)
        d.pump(0.2)
        today = d.mod.today_key()
        after = d.app.data["log"].get(today, {})
        eids = [ex["id"] for ex in d.app.data["exercises"]]
        on_disk = read_store(d)["log"].get(today, {})
        # The squat set was logged last, so that is the one set Ctrl+Z takes
        # back; the five push-up sets stay, on screen and on disk.
        check("WO-2 Ctrl+Z after logging takes back one set, not the day",
              len(after.get(eids[0], [])) == 5 and eids[1] not in after
              and len(on_disk.get(eids[0], [])) == 5
              and sum(len(v) for v in after.values())
              == sum(len(v) for v in before[today].values()) - 1,
              (before, after, on_disk))
        check("WO-2 the undone step is the set, not the earlier deletion",
              [ex["name"] for ex in d.app.data["exercises"]]
              == ["Push-ups", "Squats"],
              [ex["name"] for ex in d.app.data["exercises"]])
    finally:
        d.close()


@scenario("WO-3 the bar names the app once and About Workout is reachable")
def wo3_menus():
    d = drive("wo3")
    try:
        buttons = d.find(Gtk.Button, label="Workout")
        about = [it[0] for it in d.app.menu_items("Workout")
                 if isinstance(it, (tuple, list)) and str(it[0]).startswith("About")]
        actions = [it[0] for it in d.app.menu_items("Exercise")
                   if isinstance(it, (tuple, list))]
        check("WO-3 the bar names the app once and About Workout is reachable",
              len(buttons) == 1 and about == ["About Workout"]
              and d.app._menu_buttons.get("Workout") is buttons[0]
              and "Log a Set" in actions,
              (len(buttons), about, actions))
    finally:
        d.close()


@scenario("WO-4 the app-name button loses its open look when its menu closes")
def wo4_open_class():
    d = drive("wo4")
    try:
        btn = d.find(Gtk.Button, label="Workout")[0]
        btn.clicked()
        d.pump(0.1)
        opened = "open" in btn.get_style_context().list_classes()
        d.key("Escape")
        d.pump(0.1)
        after_esc = "open" in btn.get_style_context().list_classes()
        d.open_menu("File")
        d.close_menu()
        d.pump(0.1)
        after_file = "open" in btn.get_style_context().list_classes()
        check("WO-4 the app-name button loses its open look when its menu closes",
              opened and not after_esc and not after_file,
              (opened, after_esc, after_file))
    finally:
        d.close()


@scenario("WO-5 Save is out of reach until the exercise has a name")
def wo5_save_needs_name():
    d = drive("wo5")
    try:
        add_exercise(d, "Push-ups", 3, 10)
        seen = {}

        def look(dlg):
            ent = name_entry(dlg)
            save = dlg_button(dlg, "Save")
            seen["empty"] = save.get_sensitive()
            seen["off"] = paint(save)
            ent.grab_focus()
            ent.insert_text("Rows", -1)
            seen["typed"] = save.get_sensitive()
            seen["on"] = paint(save)
            ent.set_text("   ")
            seen["spaces"] = save.get_sensitive()
            dlg.response(Gtk.ResponseType.CANCEL)

        with_dialog(lambda: d.menu_action("File", "New Exercise"), look)
        d.pump(0.1)
        check("WO-5 Save is out of reach until the exercise has a name",
              seen.get("empty") is False and seen.get("typed") is True
              and seen.get("spaces") is False, seen)
        # ...and it LOOKS out of reach: the theme paints .suggested-action
        # solid ink in every state, so without the app's own disabled rule a
        # Save that cannot be pressed still came up as the filled black button.
        check("WO-5 the held Save is painted as held, not as pressable",
              seen.get("off") != seen.get("on"),
              (seen.get("off"), seen.get("on")))

        # An edit whose name is cleared must not be committable either: the
        # goal change beside it went down with the silently dropped name.
        edit = {}

        def clear_name(dlg):
            name_entry(dlg).set_text("")
            spins = [w for w in walk(dlg) if isinstance(w, Gtk.SpinButton)]
            spins[0].set_value(9)
            edit["save"] = dlg_button(dlg, "Save").get_sensitive()
            dlg.response(Gtk.ResponseType.CANCEL)

        with_dialog(lambda: d.menu_action("Exercise", "Edit Exercise"),
                    clear_name)
        d.pump(0.1)
        check("WO-5 an edit with the name cleared cannot be saved away",
              edit.get("save") is False
              and [(e["name"], e["sets"]) for e in d.app.data["exercises"]]
              == [("Push-ups", 3)],
              (edit, [(e["name"], e["sets"]) for e in d.app.data["exercises"]]))
    finally:
        d.close()


@scenario("WO-6 the damaged-history notice gives way once work is saved")
def wo6_notice():
    home = os.path.join(HOMES, "wo6")
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    blob = '{"exercises": [{"id": "a", "name": "Rows", "sets": 3, "re'
    with open(os.path.join(cfg, "workout.json"), "w", encoding="utf-8") as fh:
        fh.write(blob)
    d = appdrive.Drive("workout", home=home)
    try:
        at_launch = d.app.status.get_text()
        add_exercise(d, "Rows", 3, 8)
        log_set(d, 0)
        d.pump(0.1)
        after = d.app.status.get_text()
        aside = [f for f in os.listdir(cfg)
                 if f.startswith("workout.json.damaged-")]
        kept = any(open(os.path.join(cfg, f), encoding="utf-8").read() == blob
                   for f in aside)
        check("WO-6 the damaged-history notice gives way once work is saved",
              "could not be read" in at_launch
              and "could not be read" not in after
              and "1 of 3 sets today" in after, (at_launch, after))
        check("WO-6 ...and the unreadable bytes are still kept beside it",
              kept, aside)
    finally:
        d.close()


wo1_today()
wo2_undo()
wo3_menus()
wo4_open_class()
wo5_save_needs_name()
wo6_notice()
shutil.rmtree(HOMES, ignore_errors=True)
report()
