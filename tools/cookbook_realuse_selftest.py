#!/usr/bin/env python3
"""Cookbook, driven the way somebody uses it — one named check per defect.

Every check here was RED before the fix it guards and GREEN after; each one
names the behaviour a cook would notice, not the code that produces it. The app
is the real one: real recipes in a real store, real key events through the real
key ladder, real menu callbacks, real widget geometry measured after a real
layout pass (tools/appdrive.py hosts the actual window tree offscreen).

What each group is for:

  undo         Ctrl+Z used to jump the page to another recipe and another
               category chip, and to close the ingredient editor the caret was
               in — because the undo snapshot carried the selection and the
               filter as part of the document.
  columns      coming back from cook mode left Ingredients at its 320px
               cook-mode width and Method squashed to three words a line.
  kicker       every category name came out shortened ("SID…" for Sides).
  fields       a title longer than the column showed the tail, then the head
               with the right-hand side cut off and no ellipsis either way.
  save chip    "Saved 14:11" was stamped on every refresh — a time nothing was
               written at — and it painted over a real "Not saved".
  feedback     the "Ctrl+Z to undo" hint after a delete lasted 0.78 seconds;
               Delete Recipe… / Delete Category… promised a confirm they do not
               ask for, and deleting a category said nothing at all.
  categories   there was no way to file an existing recipe under a category.
  dialog       a blank or duplicate category name closed the dialog silently.
  search       there was no way to find a recipe except scrolling.

Run:
    tools/guestrun.sh python3 tools/cookbook_realuse_selftest.py
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

HOME = tempfile.mkdtemp(prefix="nb-cookreal-")
STORE = os.path.join(HOME, ".config", "notebook", "cookbook.json")
os.makedirs(os.path.dirname(STORE))
SEED = {
    "cats": ["Sides", "Soups & Stews"], "active_cat": 0, "sel": 0,
    "recipes": [
        {"title": "Grandma's Chicken Soup", "cat": None, "desc": "A Sunday soup.",
         "time": "2 hours", "makes": "Serves 6", "effort": "Easy",
         "ing": "Chicken thighs, bone-in - 1.2 kg\nCarrots - 3\nLentils - 200 g",
         "steps": "Brown the chicken.\nAdd water and simmer 90 minutes.",
         "photo": ""},
        {"title": "Toast", "cat": "Sides", "desc": "", "time": "5 min",
         "makes": "1", "effort": "Easy", "ing": "Bread - 2 slices",
         "steps": "Toast.", "photo": ""},
        {"title": "Beef Stew", "cat": "Soups & Stews", "desc": "Rich and slow.",
         "time": "3 hours", "makes": "Serves 4", "effort": "Medium",
         "ing": "Beef chuck - 1 kg", "steps": "Brown.\nStew.", "photo": ""},
    ]}
with open(STORE, "w", encoding="utf-8") as fh:
    json.dump(SEED, fh)
# The store was written an hour ago: nothing this session has saved anything.
os.utime(STORE, (time.time() - 3600, time.time() - 3600))

import appdrive                                                   # noqa: E402
import gi                                                         # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango                              # noqa: E402
import nbapp                                                      # noqa: E402

FAILED, RAN = [], []


def check(name, cond):
    RAN.append(name)
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(*names):
    for n in names:
        check(n + "  [not reached: precondition failed]", False)


LONG_TITLE = "Grandma's Chicken Soup, réchauffé au four"

d = appdrive.Drive("cookbook", home=HOME)
app = d.app


def sel_row(i):
    app._on_row_activated(None, app._find_row(i))
    d.pump(0.1)


def savestate():
    return app.savestate.get_text() or ""


def claims_saved(text):
    """True when the chip is telling the cook their work is on disk."""
    return "Saved" in text.replace("Not saved", "")


def baseline(label):
    """Where this label's first line of type actually sits, in the row."""
    _ox, oy = label.get_layout_offsets()
    return oy + label.get_layout().get_baseline() / Pango.SCALE


def edit_field(entry):
    """Start typing in a header field the way a person does — the line you read
    is a button that swaps the entry in. (Falls back to a plain focus grab so
    this suite fails by NAME rather than by crash if that control is gone.)"""
    stack = getattr(entry, "_field_stack", None)
    if stack is not None:
        stack.get_child_by_name("read").clicked()
    else:
        entry.grab_focus()
    d.pump(0.1)


def leave_field(entry):
    """Click away: the field goes back to being read."""
    if hasattr(entry, "_field_stack"):
        app._leave_field(entry)
    d.pump(0.2)


def dialog():
    return next((w for w in Gtk.Window.list_toplevels()
                 if isinstance(w, Gtk.Dialog) and w.get_visible()), None)


def dialog_text(dlg):
    return " | ".join(w.get_text() for w in d.walk(dlg)
                      if isinstance(w, Gtk.Label) and w.get_visible()
                      and w.get_text())


# ------------------------------------------------------------------ save chip
print("\n-- the save chip tells the truth about the file --")
check("opening the cookbook does not claim a save that never happened",
      savestate().strip() in ("● Saved", "Saved"))
sel_row(2)
check("choosing a recipe does not stamp a new save time",
      not any(c.isdigit() for c in savestate()))

# A write that really fails must be reported, and must SURVIVE navigation.
real_write = nbapp.atomic_write_json


def refuse_write(*_a, **_k):
    raise OSError(28, "No space left on device")


nbapp.atomic_write_json = refuse_write
edit_field(app.title_entry)
d.key("End")
d.type(" II")
d.pump(1.4)                                     # let the 900ms autosave fire
# The first failure flashes the reason (no room on the disk, read-only...);
# what matters here is that nothing on the chip reads as a completed save.
first = savestate()
check("a save that fails never reads as saved (%r)" % first,
      not claims_saved(first))
d.pump(5.6)                                     # the reason has had its moment
settled = check("...and the chip then stands at “Not saved” (%r)" % savestate(),
                "Not saved" in savestate())
sel_row(0)
d.pump(0.3)
if settled:
    check("...and clicking another recipe does not replace it with “Saved”"
          " (%r)" % savestate(), not claims_saved(savestate()))
else:
    not_reached("...and clicking another recipe does not replace it with "
                "“Saved”")
nbapp.atomic_write_json = real_write
sel_row(2)
edit_field(app.title_entry)
d.key("End")
d.key("BackSpace")
d.key("BackSpace")
d.key("BackSpace")
d.pump(1.4)
check("once a write lands, the chip names the time it landed",
      "Saved" in savestate() and any(c.isdigit() for c in savestate())
      and getattr(app, "_saved_at", None) is not None)

# ----------------------------------------------------------------------- undo
print("\n-- undo takes back the edit, not the page you are on --")
# The last thing typed was typed on a DIFFERENT recipe: that is the state the
# undo step carries, and jumping the page to it is the defect this pins.
sel_row(0)
edit_field(app.desc_entry)
d.key("End")
d.type(" Keep the bones.")
d.pump(0.9)
sel_row(2)
d.pump(0.7)
before_sel, before_cat = app.sel, app.active_cat
edit_field(app.title_entry)
d.key("End")
d.type(" Deluxe")
d.pump(0.9)
typed = check("the typing landed", app.recipes[2]["title"] == "Beef Stew Deluxe")
d.key("z", ctrl=True)
d.pump(0.3)
if typed:
    check("Ctrl+Z after typing puts the words back",
          app.recipes[2]["title"] == "Beef Stew")
    check("...and leaves the page on the recipe being edited",
          app.sel == before_sel and app.active_cat == before_cat)
else:
    not_reached("Ctrl+Z after typing puts the words back",
                "...and leaves the page on the recipe being edited")

# undo pressed with the caret in the ingredient editor
sel_row(0)
app.ing_edit_btn.clicked()
d.pump(0.1)
opened = check("the ingredients column opens for editing",
               app.ing_stack.get_visible_child_name() == "edit")
d.type("Bay leaf - 2\n")
d.pump(0.9)
d.key("z", ctrl=True)
d.pump(0.3)
if opened:
    check("Ctrl+Z in the ingredients editor takes back the line",
          "Bay leaf" not in app.recipes[0]["ing"])
    check("...and leaves the ingredients editor open where the caret was",
          app.ing_stack.get_visible_child_name() == "edit"
          and app.sel == 0)
else:
    not_reached("Ctrl+Z in the ingredients editor takes back the line",
                "...and leaves the ingredients editor open where the caret was")
app.ing_edit_btn.clicked()          # back to reading
d.pump(0.2)

# undo of a delete shows the recipe it brought back
sel_row(1)
d.pump(0.7)
d.menu_action("Cook", "Delete Recipe")
d.pump(0.1)
gone = check("Delete Recipe removes it at once, with no dialog",
             len(app.recipes) == 2 and dialog() is None)
print("   (status: %r)" % savestate())
held = savestate()
d.pump(1.6)                                     # past the 900ms autosave
check("the “Ctrl+Z to undo” hint is still readable a second and a half later",
      savestate() == held and "Ctrl" in savestate())
d.key("z", ctrl=True)
d.pump(0.4)
if gone:
    check("Ctrl+Z brings the deleted recipe back",
          [r["title"] for r in app.recipes][1] == "Toast")
    check("...and puts it back on the page",
          app.sel == 1 and app._cur() is not None
          and app._cur()["title"] == "Toast")
else:
    not_reached("Ctrl+Z brings the deleted recipe back",
                "...and puts it back on the page")

# --------------------------------------------------------------- cook columns
print("\n-- the recipe page comes back the width it went away --")
sel_row(0)
d.pump(0.3)
page_w = app.ing_box.get_allocation().width
d.click("Start cooking")
d.pump(0.5)
d.click("Done")
d.pump(1.5)
check("leaving cook mode gives Method its column back (Ingredients %dpx, was "
      "%dpx before cooking)" % (app.ing_box.get_allocation().width, page_w),
      app.ing_box.get_allocation().width == page_w)
d.click("Start cooking")
d.pump(0.4)
d.key("Escape")
d.pump(1.2)
check("...and the same when cook mode is left with Escape",
      app.ing_box.get_allocation().width == page_w)

# ---------------------------------------------------------------- the eyebrow
print("\n-- the category eyebrow --")
short = []
for idx, expect in ((1, "SIDES"), (2, "SOUPS & STEWS"), (0, "NO CATEGORY")):
    sel_row(idx)
    lay = app.kicker.get_layout()
    if app.kicker.get_text() != expect or lay.is_ellipsized():
        short.append((app.kicker.get_text(), lay.is_ellipsized()))
check("a category name that fits is shown in full (%r)" % (short,), not short)

# ------------------------------------------------------------ header fields
print("\n-- title, description and yield read on the page --")
sel_row(0)
edit_field(app.title_entry)
d.key("End")
d.type(", réchauffé au four")
d.pump(0.3)
leave_field(app.title_entry)
lbl = getattr(app.title_entry, "_read_label", None)
if lbl is None:
    not_reached("a long recipe title reads in full on the recipe page",
                "...and still does after the recipe is opened again")
else:
    check("a long recipe title reads in full on the recipe page",
          lbl.get_text() == LONG_TITLE and not lbl.get_layout().is_ellipsized())
    sel_row(2)
    sel_row(0)
    d.pump(0.2)
    check("...and still does after the recipe is opened again",
          lbl.get_text() == LONG_TITLE
          and not lbl.get_layout().is_ellipsized()
          and app.title_entry.get_property("scroll-offset") == 0)
makes = app.meta_entries["makes"]
edit_field(makes)
d.key("End")
d.type(" bowls of soup")
d.pump(0.3)
leave_field(makes)
mlbl = getattr(makes, "_read_label", None)
check("a yield too long for its cell is marked as shortened, and readable",
      mlbl is not None and mlbl.get_layout().is_ellipsized()
      and mlbl.get_tooltip_text() == "Serves 6 bowls of soup")

# ------------------------------------------------------------------ ledger
print("\n-- the ingredients ledger --")
row0 = app.ing_render.get_children()[0]
name_lbl, amount_lbl = row0.get_children()[0], row0.get_children()[1]
delta = baseline(name_lbl) - baseline(amount_lbl)
check("the amount sits on the ingredient's own line (%.1fpx off)" % delta,
      abs(delta) <= 1.0)

# -------------------------------------------------------------- categories
print("\n-- filing a recipe under a category --")
check("the recipe page has a control for its category",
      isinstance(getattr(app, "kicker_btn", None), Gtk.Button))
check("...and the Cook menu offers the same move",
      any(isinstance(i, tuple) and i[0].startswith("Move to Category")
          for i in app.menu_items("Cook")))
if not hasattr(app, "_file_current"):
    not_reached("filing the open recipe under a category writes it down",
                "...and Ctrl+Z takes it out again, without leaving the recipe")
else:
    app._file_current("Sides")
    d.pump(0.3)
    filed = check("filing the open recipe under a category writes it down",
                  app.recipes[0]["cat"] == "Sides"
                  and app.kicker.get_text() == "SIDES")
    d.key("z", ctrl=True)
    d.pump(0.3)
    if filed:
        check("...and Ctrl+Z takes it out again, without leaving the recipe",
              app.recipes[0]["cat"] is None and app.sel == 0)
    else:
        not_reached("...and Ctrl+Z takes it out again, without leaving the "
                    "recipe")

# ------------------------------------------------------------------ dialog
print("\n-- the New Category dialog --")
sel_row(0)
kept_sel, kept_cats = app.sel, list(app.cats)
app._new_category()
d.pump(0.2)
dlg = dialog()
if dlg is None:
    not_reached("a category name that already exists is refused, and said so",
                "a blank category name is refused, and said so",
                "adding a category keeps the recipe you were on")
else:
    entry = next(w for w in d.walk(dlg) if isinstance(w, Gtk.Entry))
    entry.set_text("Sides")
    entry.activate()
    d.pump(0.2)
    check("a category name that already exists is refused, and said so",
          dialog() is not None and app.cats == kept_cats
          and "already exists" in dialog_text(dlg))
    entry.set_text("   ")
    entry.activate()
    d.pump(0.2)
    check("a blank category name is refused, and said so",
          dialog() is not None and app.cats == kept_cats
          and "name" in dialog_text(dlg).lower())
    entry.set_text("Desserts")
    entry.activate()
    d.pump(0.3)
    check("adding a category keeps the recipe you were on",
          "Desserts" in app.cats and app.sel == kept_sel
          and app.stack.get_visible_child_name() == "editor")

# --------------------------------------------------------------- delete cat
print("\n-- deleting a category --")
labels = [i[0] for i in app.menu_items("Cook") if isinstance(i, tuple)]
check("the menu items that act at once carry no ellipsis (%r)"
      % ([l for l in labels if l.startswith("Delete")],),
      "Delete Recipe" in labels and "Delete Category" in labels)
app._on_chip(None, 2)                            # Soups & Stews
d.pump(0.2)
d.menu_action("Cook", "Delete Category")
d.pump(0.1)
check("deleting a category says which one, and that it can be undone (%r)"
      % savestate(),
      "Soups & Stews" in savestate() and "Ctrl" in savestate())
check("...and its recipes are kept",
      len(app.recipes) == 3 and app.recipes[2]["cat"] is None)
d.key("z", ctrl=True)
d.pump(0.4)
check("Ctrl+Z brings the category back",
      "Soups & Stews" in app.cats and app.recipes[2]["cat"] == "Soups & Stews")

# ------------------------------------------------------------- empty states
print("\n-- an empty category --")
if "Desserts" not in app.cats:          # the dialog above may have refused it
    app.cats.append("Desserts")
    app.rebuild_chips()
app._on_chip(None, app.cats.index("Desserts") + 1)      # real, and empty
d.pump(0.3)
said = [t for t in d.texts() if "Desserts" in t and "recipe" in t.lower()]
check("an empty category is named once, not twice (%r)" % (said,),
      len(said) == 1)

# ------------------------------------------------------------------ search
print("\n-- finding a recipe --")
app._on_chip(None, 0)
d.pump(0.2)
field = getattr(app, "search", None)
if field is None:
    not_reached("the recipe list has a search field",
                "searching narrows the list to what matches",
                "a search reaches the ingredients as well as the title",
                "Ctrl+F puts the caret in the search field",
                "Escape clears the search instead of closing the app")
else:
    check("the recipe list has a search field",
          isinstance(field, Gtk.SearchEntry) and field.get_visible())

    def rows():
        return [r._title_lbl.get_text() for r in app.listbox.get_children()
                if hasattr(r, "_title_lbl")]

    field.set_text("stew")
    d.pump(0.3)
    check("searching narrows the list to what matches (%r)" % (rows(),),
          rows() == ["Beef Stew"])
    field.set_text("lentils")
    d.pump(0.3)
    check("a search reaches the ingredients as well as the title (%r)"
          % (rows(),),
          rows() == [LONG_TITLE])
    edit_field(app.title_entry)
    d.key("f", ctrl=True)
    d.pump(0.1)
    check("Ctrl+F puts the caret in the search field",
          d.focus() is field)
    field.set_text("zzz")
    d.pump(0.3)
    said = [t for t in d.texts() if "match" in t.lower()]
    check("a search that finds nothing says so once, not twice (%r)" % (said,),
          len(said) == 1)
    # Cook mode hides the sidebar, so Escape there means "leave the stove
    # page" — the search box is not even on screen to be cleared.
    field.set_text("stew")
    d.pump(0.3)
    sel_row(2)
    d.click("Start cooking")
    d.pump(0.4)
    in_cook = check("Start cooking works with a search active",
                    app.stack.get_visible_child_name() == "cook")
    d.key("Escape")
    d.pump(0.4)
    if in_cook:
        check("Escape leaves cook mode before it touches the search",
              app.stack.get_visible_child_name() == "editor"
              and app.query == "stew")
    else:
        not_reached("Escape leaves cook mode before it touches the search")
    d.key("Escape")
    d.pump(0.3)
    check("Escape clears the search instead of closing the app",
          field.get_text() == "" and app.query == "" and len(rows()) == 3)

d.pump(1.2)
d.close()

print("\n%d checks, %d passed, %d FAILED"
      % (len(RAN), len(RAN) - len(FAILED), len(FAILED)))
if FAILED:
    print("RESULT: FAILED")
    for f in FAILED:
        print("  " + f)
    sys.exit(1)
print("RESULT: ALL PASS")
