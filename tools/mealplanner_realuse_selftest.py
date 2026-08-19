#!/usr/bin/env python3
"""Meal Planner, driven the way somebody plans a week — one named check per
defect found by using it.

The app is the real one: a real Cookbook file next to a real plan file, the
real cell buttons, the real modal meal dialog (driven from inside its own
nested loop), the real menu callbacks, and real widget geometry measured after
a real layout pass (tools/appdrive.py hosts the window tree offscreen at the
1024x740 panel).

What each group is for:

  recipe link  a Cookbook recipe whose title ran past 80 characters was stored
               cut off. The title is the ONLY link a slot has back to its
               recipe, so the cut copy matched nothing: reopening the day
               showed "Nothing from the cookbook" with the shortened text in
               the free-text box, and pressing Save turned the meal into a
               note nobody had typed.
  dialog size  the chooser held every recipe title raw, so the longest title
               in Cookbook set the dialog's width — 1351px on a 1024px panel,
               with Save and Cancel off the side of the screen.
  typing       a meal longer than the store keeps was accepted in full and
               then kept cut mid-word, with no limit shown and nothing said;
               the trailing space it was cut on was trimmed by the next
               launch, so the plan on disk and the plan on screen disagreed.
  cell         a dish name was cut off after three lines in a cell with 110px
               of empty space under it.
  takeaway     the Takeaway tick was accepted beside a picked recipe and then
               silently dropped: no TAKEAWAY tag, and the tick gone on reopen.
  dates        no year anywhere — 19 weeks out the header read "28 December -
               3 January" — and the dialog heading never said which day.
  menus        Edit offered Cut / Copy / Paste / Select All, enabled, on a
               screen with no text field; firing them did nothing.
  clearing     the confirmation asked its own heading back ("Clear this week"
               / "Clear this week?") and named neither the count nor what
               survives.

Run:
    tools/guestrun.sh python3 tools/mealplanner_realuse_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOME = tempfile.mkdtemp(prefix="nb-mealreal-")
CFG = os.path.join(HOME, ".config", "notebook")
STORE = os.path.join(CFG, "mealplanner.json")
COOKBOOK = os.path.join(CFG, "cookbook.json")
os.makedirs(CFG)

# A real cook's recipe titles: one ordinary, one long enough to have been cut
# by the old store cap, one long enough to have set the dialog's width.
SHORT = "Grandma's stew"
LONG97 = ("Slow-roasted shoulder of lamb with rosemary potatoes, minted peas "
          "and a red wine gravy for Sunday")
LONG230 = ("Grandma's famous Sunday roast with all the trimmings: "
           "slow-roasted beef, Yorkshire puddings, roast potatoes, "
           "honey-glazed carrots and parsnips, buttered greens, cauliflower "
           "cheese and proper gravy, followed by sticky toffee pudding")
TYPED97 = ("Spaghetti bolognese with a slow-cooked ragu, parmesan, garlic "
           "bread and a green salad on the side")
LUNCH44 = "Leftover roast chicken sandwiches with salad"

with open(COOKBOOK, "w", encoding="utf-8") as fh:
    json.dump({"cats": [], "active_cat": None, "sel": 0,
               "recipes": [{"title": t}
                           for t in (SHORT, LONG97, LONG230)]}, fh)

import appdrive                                                   # noqa: E402
import gi                                                         # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib                               # noqa: E402

# The length a typed meal is kept to. Read from the app when it names one, so
# this suite fails by NAME rather than by AttributeError against a build that
# has no such limit -- which is exactly the build these checks were red on.
CAP = 80

FAILED, RAN = [], []


def check(name, cond):
    RAN.append(name)
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(*names):
    """Fail by NAME when a step this check depends on did not happen, so a
    missing control is a named failure instead of a traceback."""
    for n in names:
        check(n + "  [not reached: precondition failed]", False)


def walk(root):
    out, i = [root], 0
    while i < len(out):
        w = out[i]
        i += 1
        if isinstance(w, Gtk.Container):
            out.extend(w.get_children())
    return out


def of(root, kind):
    return [w for w in walk(root) if isinstance(w, kind)]


def dialog_button(dlg, label):
    for w in walk(dlg):
        if isinstance(w, Gtk.Button) and not isinstance(w, Gtk.CheckButton):
            child = w.get_child()
            if isinstance(child, Gtk.Label) and child.get_text() == label:
                return w
    return None


def dialog_labels(dlg):
    return [w.get_text() for w in of(dlg, Gtk.Label)
            if w.get_visible() and w.get_text()]


def open_dialog(action, handler, tries=60):
    """`action` blocks in the app's own dlg.run(); `handler(dlg)` runs from a
    timeout INSIDE that nested loop and must finish by answering the dialog.
    Returns False when no dialog ever appeared (so the caller can fail by
    name rather than by crash)."""
    before = {id(w) for w in Gtk.Window.list_toplevels()}
    state = {"n": 0, "seen": False}

    def look():
        state["n"] += 1
        found = [w for w in Gtk.Window.list_toplevels()
                 if isinstance(w, Gtk.Dialog) and w.get_visible()
                 and id(w) not in before]
        if not found:
            return state["n"] <= tries
        dlg = found[-1]
        state["seen"] = True

        def watchdog():
            if dlg.get_visible():
                dlg.response(Gtk.ResponseType.CANCEL)
            return False
        GLib.timeout_add(15000, watchdog)
        try:
            handler(dlg)
        except Exception:                                     # noqa: BLE001
            traceback.print_exc(file=sys.stdout)
            dlg.response(Gtk.ResponseType.CANCEL)
        return False

    GLib.timeout_add(120, look)
    action()
    appdrive.pump(0.1)
    return state["seen"]


def cells_of(app, day, meal):
    return app._cells.get((day, meal))


def dish_label(app, day, meal):
    cell = cells_of(app, day, meal)
    if cell is None:
        return None
    for w in walk(cell):
        if (isinstance(w, Gtk.Label)
                and w.get_style_context().has_class("mp-dish")):
            return w
    return None


def on_disk(day, meal):
    try:
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)["plan"][day][meal]
    except Exception:                                         # noqa: BLE001
        return None


d = appdrive.Drive("mealplanner", home=HOME)
app, mp = d.app, d.mod
WEEK = [mp._date_key(app.week + i) for i in range(7)]

# ------------------------------------------------------- the link to Cookbook
print("\n-- a recipe stays the recipe it came from --")
day = WEEK[5]
picked = {"row": None}


def pick_long(dlg):
    combo = of(dlg, Gtk.ComboBoxText)[0]
    model = combo.get_model()
    rows = [model[i][0] for i in range(len(model))]
    picked["row"] = rows.index(LONG97) if LONG97 in rows else None
    if picked["row"] is None:
        dlg.response(Gtk.ResponseType.CANCEL)
        return
    combo.set_active(picked["row"])
    dialog_button(dlg, mp._t("Save")).clicked()


if open_dialog(lambda: app._cells[(day, "dinner")].clicked(), pick_long) \
        and picked["row"]:
    slot = app._slot(day, "dinner")
    check("a recipe picked from Cookbook is stored under the title Cookbook"
          " has", bool(slot) and slot.get("title") == LONG97)
    check("...and that is what reaches the file",
          (on_disk(day, "dinner") or {}).get("title") == LONG97)

    seen = {}

    def reopen(dlg):
        combo = of(dlg, Gtk.ComboBoxText)[0]
        seen["row"] = combo.get_active()
        seen["entry"] = of(dlg, Gtk.Entry)[0].get_text()
        dialog_button(dlg, mp._t("Save")).clicked()

    if open_dialog(lambda: app._cells[(day, "dinner")].clicked(), reopen):
        check("reopening that meal shows the recipe picked, not its name"
              " typed out", seen.get("row") == picked["row"]
              and seen.get("entry") == "")
        after = app._slot(day, "dinner")
        check("...and a Save that changed nothing leaves it a recipe",
              bool(after) and after.get("kind") == mp.KIND_RECIPE
              and after.get("title") == LONG97)
    else:
        not_reached("reopening that meal shows the recipe picked, not its name"
                    " typed out",
                    "...and a Save that changed nothing leaves it a recipe")
else:
    not_reached("a recipe picked from Cookbook is stored under the title"
                " Cookbook has",
                "...and that is what reaches the file",
                "reopening that meal shows the recipe picked, not its name"
                " typed out",
                "...and a Save that changed nothing leaves it a recipe")

# A slot written by the older code — the title cut at the old cap — still
# names its recipe, so it opens on the recipe and re-saves as one.
app.plan.setdefault(WEEK[6], {})["dinner"] = {
    "kind": mp.KIND_RECIPE, "title": LONG97[:CAP].strip()}
app._refresh()
row, prefill = app.dialog_prefill(app._slot(WEEK[6], "dinner"), app.recipes)
check("a meal saved by the older, cutting version still finds its recipe",
      row == (app.recipes.index(LONG97) + 1) and prefill == "")

# ---------------------------------------------------------- the dialog's size
print("\n-- the dialog fits the panel, whatever Cookbook holds --")
size = {}


def measure(dlg):
    combo = of(dlg, Gtk.ComboBoxText)[0]
    combo.set_active(app.recipes.index(LONG230) + 1)
    appdrive.pump(0.2)
    alloc = dlg.get_allocation()
    size["width"] = alloc.width
    size["natural"] = dlg.get_preferred_width()[1]
    save = dialog_button(dlg, mp._t("Save"))
    button = save.get_allocation() if save is not None else None
    size["save_right"] = (button.x + button.width) if button else None
    dlg.response(Gtk.ResponseType.CANCEL)


if open_dialog(lambda: app._cells[(WEEK[0], "lunch")].clicked(), measure):
    panel = d.nbapp.screen_size()[0]
    check("the meal dialog stays well inside the panel with a 230-character"
          " recipe title (%s px of %s)" % (size.get("natural"), panel),
          size.get("natural") is not None and size["natural"] <= panel * 0.65)
    check("...so its Save button is on the screen (%s px)"
          % (size.get("save_right"),),
          size.get("save_right") is not None and size["save_right"] < panel)
else:
    not_reached("the meal dialog stays well inside the panel with a"
                " 230-character recipe title",
                "...so its Save button is on the screen")

# --------------------------------------------------------------- typing a meal
print("\n-- what is typed is what is kept --")
typed_day = WEEK[3]
typing = {}


def type_long(dlg):
    entry = of(dlg, Gtk.Entry)[0]
    typing["max"] = entry.get_max_length()
    entry.grab_focus()
    entry.set_text(TYPED97)
    typing["kept"] = entry.get_text()
    dialog_button(dlg, mp._t("Save")).clicked()


if open_dialog(lambda: app._cells[(typed_day, "dinner")].clicked(), type_long):
    check("the typed-meal box stops at the length a meal is kept (%s)"
          % (typing.get("max"),),
          typing.get("max") == getattr(mp, "MAX_TYPED_TITLE", CAP))
    # What the box ended up holding is what the slot ended up holding. (The
    # box can end on the space the limit fell on; a trailing space is not
    # something anybody typed to keep.)
    check("...so nothing typed into it is dropped behind the person's back",
          (typing.get("kept") or "").strip()
          == app._slot(typed_day, "dinner")["title"])
else:
    not_reached("the typed-meal box stops at the length a meal is kept",
                "...so nothing typed into it is dropped behind the person's"
                " back")

# ------------------------------------------------------------------- the cell
print("\n-- a dish name uses the room its cell has --")
app.plan[WEEK[4]] = {
    "lunch": {"kind": mp.KIND_NOTE, "title": LUNCH44},
    "dinner": {"kind": mp.KIND_TAKEOUT, "title": TYPED97[:60]},
}
app._refresh()
d.pump(0.4)
lunch = dish_label(app, WEEK[4], "lunch")
if lunch is not None:
    layout = lunch.get_layout()
    check("a 44-character dish is shown whole in a 160px cell (%d lines, cut"
          " short: %s)" % (layout.get_line_count(), layout.is_ellipsized()),
          not layout.is_ellipsized())
    check("...on more than the three lines it used to be held to (%d)"
          % lunch.get_lines(), lunch.get_lines() > 3)
else:
    not_reached("a 44-character dish is shown whole in a 160px cell",
                "...on more than the three lines it used to be held to")
takeaway_dish = dish_label(app, WEEK[4], "dinner")
if takeaway_dish is not None:
    check("a takeaway's name still leaves its TAKEAWAY tag a line",
          not takeaway_dish.get_layout().is_ellipsized()
          and any(w.get_text() == mp._t("TAKEAWAY")
                  for w in of(app._cells[(WEEK[4], "dinner")], Gtk.Label)))
else:
    not_reached("a takeaway's name still leaves its TAKEAWAY tag a line")

# ---------------------------------------------------------------- the takeaway
print("\n-- Takeaway is offered only where it can be honoured --")
tick_day = WEEK[2]
tick = {}


def ink(widget):
    """The colour this widget's label is actually printed in right now."""
    ctx = widget.get_style_context()
    colour = ctx.get_color(ctx.get_state())
    return (round(colour.red, 2), round(colour.green, 2),
            round(colour.blue, 2))


def drive_tick(dlg):
    combo = of(dlg, Gtk.ComboBoxText)[0]
    box = of(dlg, Gtk.CheckButton)[0]
    entry = of(dlg, Gtk.Entry)[0]
    box.set_active(True)
    tick["live_ink"] = ink(box)
    combo.set_active(app.recipes.index(SHORT) + 1)
    appdrive.pump(0.1)
    tick["off_ink"] = ink(box)
    tick["with_recipe"] = (box.get_sensitive(), box.get_active())
    combo.set_active(0)
    appdrive.pump(0.1)
    tick["back"] = (box.get_sensitive(), box.get_active())
    combo.set_active(app.recipes.index(SHORT) + 1)
    entry.set_text("Fish and chips from the corner")
    appdrive.pump(0.1)
    tick["typed"] = (box.get_sensitive(), box.get_active())
    dialog_button(dlg, mp._t("Save")).clicked()


if open_dialog(lambda: app._cells[(tick_day, "dinner")].clicked(), drive_tick):
    check("Takeaway is not offered while a recipe is the answer (%s)"
          % (tick.get("with_recipe"),), tick.get("with_recipe") == (False,
                                                                    False))
    check("...and comes back with the tick it was showing (%s)"
          % (tick.get("back"),), tick.get("back") == (True, True))
    # Papertone dims a disabled MENU item but not a check button's label, so
    # an unavailable tick that greyed only its box read as an ordinary
    # unticked one.
    check("...and is printed faintly while it is unavailable (%s vs %s)"
          % (tick.get("off_ink"), tick.get("live_ink")),
          tick.get("off_ink") is not None
          and tick.get("off_ink") != tick.get("live_ink"))
    saved = app._slot(tick_day, "dinner")
    check("...so a ticked takeaway that IS saved is filed as one",
          tick.get("typed") == (True, True)
          and bool(saved) and saved.get("kind") == mp.KIND_TAKEOUT)
else:
    not_reached("Takeaway is not offered while a recipe is the answer",
                "...and comes back with the tick it was showing",
                "...so a ticked takeaway that IS saved is filed as one")

# ------------------------------------------------------------------ the dates
print("\n-- the week and the day say which week and which day --")
for _ in range(60):
    if mp._date_key(app.week)[:4] != mp._date_key(app.week + 6)[:4]:
        break
    app._on_step(None, 7)
crossing = app.sub.get_text()
first, last = mp._date_key(app.week), mp._date_key(app.week + 6)
check("a week that crosses the new year names both years (%r)" % crossing,
      first[:4] in crossing and last[:4] in crossing)
app._on_step(None, 7)
check("a week in another year still names it (%r)" % app.sub.get_text(),
      mp._date_key(app.week)[:4] in app.sub.get_text())
app._on_step(None, 0)
check("this week is not cluttered with a year (%r)" % app.sub.get_text(),
      mp._date_key(app.week)[:4] not in app.sub.get_text())

heading_day = WEEK[3]
head = {}


def read_heading(dlg):
    head["labels"] = dialog_labels(dlg)
    dlg.response(Gtk.ResponseType.CANCEL)


if open_dialog(lambda: app._cells[(heading_day, "breakfast")].clicked(),
               read_heading):
    day_number = heading_day.split("-")[2].lstrip("0")
    month = mp._t(mp.MONTHS[int(heading_day.split("-")[1]) - 1])
    said = head.get("labels") or [""]
    check("the dialog that edits a meal names the day it is editing (%r)"
          % (said[0],), day_number in said[0] and month in said[0])
    tip = app._cells[(heading_day, "breakfast")].get_tooltip_text() or ""
    check("...and so does the cell it opened from (%r)" % tip,
          day_number in tip and month in tip)
else:
    not_reached("the dialog that edits a meal names the day it is editing",
                "...and so does the cell it opened from")

# ------------------------------------------------------------------ the menus
print("\n-- the Edit menu offers only what this screen can do --")
labels = [it[0] for it in app.menu_items("Edit") if it is not d.nbapp.SEP]
clipboard = [lab for lab in labels
             if lab.split("    ")[0] in ("Cut", "Copy", "Paste", "Select All")]
check("Edit offers no clipboard items on a screen with no text field (%r)"
      % (labels,), not clipboard)
check("...and still offers Undo, Redo and Clear This Week",
      len(labels) == 3 and labels[-1].startswith("Clear This Week"))

# --------------------------------------------------------------- clearing out
print("\n-- clearing the week says what goes --")
# Everything planned so far this week, left where it is: the confirmation has
# to count what is really there, and the meals typed above have to survive the
# Cancel for the relaunch check at the end.
app.week = mp._week_start(mp._today_key())
app._refresh()
planned = sum(len(app.plan.get(day) or {}) for day in WEEK)
confirm = {}


def read_confirm(dlg):
    confirm["labels"] = dialog_labels(dlg)
    dlg.response(Gtk.ResponseType.CANCEL)


if open_dialog(lambda: d.menu_action("Edit", "Clear This Week"), read_confirm):
    said = confirm.get("labels") or []
    detail = said[1] if len(said) > 1 else ""
    check("the confirmation states the consequence instead of repeating the"
          " heading (%r)" % (said,),
          len(said) > 1 and detail.rstrip("?") != said[0].rstrip("?"))
    check("...and counts the %d meals it is about to remove (%r)"
          % (planned, detail), str(planned) in detail)
    check("...and the plan is still there after Cancel",
          sum(len(app.plan.get(day) or {}) for day in WEEK) == planned)
else:
    not_reached("the confirmation states the consequence instead of repeating"
                " the heading",
                "...and counts the meals it is about to remove",
                "...and the plan is still there after Cancel")

# ------------------------------------------------ the plan survives a relaunch
print("\n-- the plan comes back the way it was left --")
before_close = dict(app._slot(typed_day, "dinner") or {})
d.close()
d2 = appdrive.Drive("mealplanner", home=HOME)
after = dict(d2.app._slot(typed_day, "dinner") or {})
check("a typed meal reads back exactly as it was saved (%r)"
      % (after.get("title"),),
      after == before_close and after == (on_disk(typed_day, "dinner") or {}))
d2.close()

shutil.rmtree(HOME, ignore_errors=True)
print("\n%d checks, %d passed, %d FAILED"
      % (len(RAN), len(RAN) - len(FAILED), len(FAILED)))
if FAILED:
    print("RESULT: FAILED")
    for f in FAILED:
        print("  " + f)
    sys.exit(1)
print("RESULT: ALL PASS")
