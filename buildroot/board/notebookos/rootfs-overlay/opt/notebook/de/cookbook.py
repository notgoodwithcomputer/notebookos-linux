#!/usr/bin/env python3
"""
Cookbook — recipe manager for Notebook OS (native GTK).

Two-pane layout: a sidebar of category chips and a recipe list, and a main
recipe page with a photo-caption band, a category kicker, a title field, a
description field, a Time / Makes / Effort field strip, and Ingredients +
Method columns. Ships empty (no recipes, no categories) per the no-seed rule;
a technical empty state names the New Recipe action.

The whole library persists to $NB_HOME/.config/notebook/cookbook.json on every
edit — this file is the sole source of truth. The File menu adds a recipe (New
Recipe) and renders the current recipe to a PDF under $NB_HOME/Documents
(Export to PDF); there is no file open / save / save-as.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import re
import time
import copy
import cairo

import nbapp
import nbicons
import nbprint
import nbtransitions
import nbi18n
from nbi18n import _t  # noqa: E402


def _set_user_tooltip(widget, text):
    """Hover text carrying the cook's own words.

    set_tooltip_text is one of the setters nbi18n patches, so a category
    called "Mains" hovered as "Secteur". set_tooltip_markup is not patched and
    renders identically once escaped, so it is the verbatim route."""
    value = str(text or "")
    if value:
        widget.set_tooltip_markup(GLib.markup_escape_text(value))
        # set_tooltip_text is also where nbapp fills in a missing ACCESSIBLE
        # NAME (an icon-only button has none), and the markup form is not that
        # setter — so the name is filled in here instead. Skipping this step
        # would have traded a translated tooltip for an anonymous control.
        try:
            acc = widget.get_accessible()
            if acc is not None and not (acc.get_name() or "").strip():
                acc.set_name(value)
        except Exception:                                         # noqa: BLE001
            pass
    else:
        widget.set_tooltip_text(None)


def _set_recipe_text(label, text, fallback=""):
    """Keep recipe-authored text out of the interface translation hook."""
    value = str(text or "")
    if value:
        nbi18n.set_verbatim(label, value)
        return
    empty = _t(fallback) if fallback else ""
    try:
        label.set_text(empty)              # a Gtk.Label
    except AttributeError:
        label.set_label(empty)             # a Gtk.Button / ToggleButton

# The whole library (categories, recipes, the active category filter and the
# current selection) is written to $NB_HOME/.config/notebook/cookbook.json on
# every edit so nothing is lost across a close or reboot — this file is the
# sole source of truth. The File menu's Export to PDF renders the current
# recipe to a PDF under $NB_HOME/Documents; there is no file open / save.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
COOKBOOK_FILE = os.path.join(CFG_DIR, "cookbook.json")
DOCUMENTS = os.path.join(HOME, "Documents")
MAX_COOKBOOK_BYTES = 8 * 1024 * 1024


class CookbookStoreTooLarge(ValueError):
    pass


def _read_cookbook_json(path=None, limit=MAX_COOKBOOK_BYTES):
    """Read the text-only recipe library without trusting its file size."""
    if path is None:
        path = COOKBOOK_FILE
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise CookbookStoreTooLarge("cookbook store is too large")
    return json.loads(data)


def _is_record(v):
    """True when this object is plainly one recipe: a mapping carrying a
    non-empty title, ingredient list or method."""
    return isinstance(v, dict) and any(
        isinstance(v.get(k), str) and v.get(k)
        for k in ("title", "ing", "steps"))


def _holds_records(data, _depth=0):
    """True when a parsed store plainly contains recipe-shaped records, whether
    or not this app's loader managed to read them.

    The one thing a cookbook can never do is reopen empty and then autosave that
    emptiness over the only copy of somebody's recipes. An empty library is a
    perfectly legitimate state (a new user, or one who deleted their last
    recipe), so the test is the SHAPE of what is in the file, never emptiness.

    The search walks the whole document (to a bounded depth), because the shapes
    that reach this guard are exactly the ones nobody planned for. Looking only
    for a list-of-records one level down missed a file that IS a map of recipes
    keyed by title, and one whose recipes sit under a nested wrapper: this
    returned False for both, the guard stayed silent, and the close-time save
    wrote an empty library straight over the user's only copy."""
    if _is_record(data):
        return True
    if _depth >= 4:                      # a store is shallow; don't recurse a
        return False                     # pathological document forever
    if isinstance(data, dict):
        children = data.values()
    elif isinstance(data, (list, tuple)):
        children = data
    else:
        return False
    return any(_holds_records(c, _depth + 1) for c in children)


def _not_a_cookbook(data):
    """True when the recipes slot holds something that is not a collection at
    all — a string, a number. A cookbook with nothing in it yet is a list or an
    object; a SCALAR there means this file is some other app's, or a repair
    gone wrong, and it must be kept rather than replaced."""
    rec = data.get("recipes") if isinstance(data, dict) else data
    return rec is not None and not isinstance(rec, (list, dict))


def _quarantine(path):
    """Move a store this app could not make sense of aside, under the same
    <name>.damaged-<timestamp> name nbapp.preserve_damaged uses. Never raises.

    nbapp quarantines any store that fails to PARSE, and does it for us on every
    write. It deliberately cannot cover the case here: valid JSON of the wrong
    shape parses perfectly, and only this app knows the shape is not a
    cookbook."""
    if not os.path.exists(path):
        return True
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.exists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------- quantities
# Cooking for six from a recipe written for four means doing ten sums in your
# head at the stove. The amount column is free text ("500g", "1 large",
# "1 1/2 tbsp", "to taste"), so scaling reads the number off the FRONT of it and
# leaves everything else exactly as written — an amount with no number ("to
# taste", "a pinch") is passed through untouched rather than guessed at.
_QTY_RE = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?)(\s*)(.*)$")
_NUM_RE = re.compile(r"\d+")


def _read_qty(text):
    """The leading quantity of an amount as a float, or None."""
    try:
        if " " in text and "/" in text:              # "1 1/2"
            whole, frac = text.split(None, 1)
            num, den = frac.split("/")
            return int(whole) + int(num) / float(den)
        if "/" in text:                              # "3/4"
            num, den = text.split("/")
            return int(num) / float(den)
        return float(text.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def _fmt_qty(value):
    """A scaled quantity back as text: whole numbers stay whole, anything else
    keeps at most two decimals with no trailing zeros."""
    if abs(value - round(value)) < 0.005:
        return "%d" % round(value)
    return ("%.2f" % value).rstrip("0").rstrip(".")


def scale_amount(amount, factor):
    """`amount` with its leading quantity multiplied by `factor`. Text with no
    leading number ("to taste") comes back unchanged."""
    if factor == 1:
        return amount
    m = _QTY_RE.match(amount or "")
    if not m:
        return amount
    value = _read_qty(m.group(1))
    if value is None:
        return amount
    rest = m.group(3)
    # A range ("2-3 sprigs") has to scale at both ends, or doubling it produces
    # the nonsense "4-3 sprigs".
    rng = re.match(r"^-\s*(\d+(?:[.,]\d+)?)(.*)$", rest)
    if rng:
        top = _read_qty(rng.group(1))
        if top is not None:
            rest = "-" + _fmt_qty(top * factor) + rng.group(2)
    return _fmt_qty(value * factor) + m.group(2) + rest


def base_servings(makes):
    """The number a recipe's free-text yield is written for ("Serves 4" -> 4,
    "Makes 12" -> 12), or None when it names no number to scale from."""
    m = _NUM_RE.search(makes or "")
    if not m:
        return None
    n = int(m.group(0))
    return n if 1 <= n <= 999 else None


def restate_servings(makes, n):
    """The yield line with its number swapped for `n`, so "Serves 4" becomes
    "Serves 6" and "Makes 12 buns" becomes "Makes 18 buns"."""
    if not makes:
        return "%d" % n
    return _NUM_RE.sub(str(n), makes, count=1)


class Cookbook(nbapp.AppWindow):
    app_name = "Cookbook"
    menus = ("File", "Edit", "View", "Cook")
    # How much of a category name the header eyebrow shows. A 45-character
    # name measured 518px there, which kept the whole app at 1100px minimum.
    KICKER_MAX = 28

    def __init__(self):
        super().__init__()
        self._install_css()

        # data model
        self.cats = []          # list of category name strings
        self.recipes = []       # list of dicts: {title, cat, desc, time,
                                #                 makes, effort, ing, steps}
        self.active_cat = 0     # 0 == "All"; else index into cats + 1
        self.query = ""         # what the search box is narrowing the list to
        self.sel = -1           # index into self.recipes
        self._save_timer = None
        self._closed = False
        self._recovery_dirty = False
        # What the save chip is allowed to claim. It used to stamp the clock at
        # the moment of any refresh, so merely opening the app or clicking a
        # recipe reported a save that never happened (and painted over a real
        # "Not saved"). Only a write that reached the file sets these.
        self._saved_at = None       # HH:MM of the last successful write
        self._save_failed = False   # the last write attempt did not land
        self._flash_until = 0.0     # a transient message owns the chip until
        self._flash_timer = None    # this moment (monotonic seconds)
        self.meta_entries = {}  # field -> Gtk.Entry for the stat strip
        self._fields = []       # header entries shown as text until edited
        # Cook mode: which step is showing, what the recipe is written for, and
        # what it is being cooked for now (scaling is display-only — see
        # scale_amount — so none of this is ever written to the recipe).
        self._cook_i = 0
        self._cook_base = None
        self._cook_n = None

        # Restore the cookbook library (categories + recipes) from its autosave.
        # On first run there is no file, so the model stays empty and the app
        # opens on its "No recipes" empty state (ships-empty rule).
        self._load_state()
        # Undo, on the shared history every other editor uses. Cookbook was the
        # only text-editing app in the OS without it: selecting a whole method
        # and typing over it, or deleting a recipe, was final. Built AFTER
        # _load_state, because the first snapshot has to be the library the user
        # actually has rather than an empty one.
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_hexpand(True)
        row.set_vexpand(True)
        self.content.pack_start(row, True, True, 0)

        self._side = self._build_sidebar()
        row.pack_start(self._side, False, False, 0)
        row.pack_start(self._build_main(), True, True, 0)

        self.rebuild_chips()
        self.rebuild_list()
        self._refresh_editor()

        # Flush the final (possibly still-debounced) edit when the window
        # closes so no typed input is lost.
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.set_size_request(344, -1)
        side.get_style_context().add_class("sidebar")

        # category chips
        chipwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chipwrap.get_style_context().add_class("chipwrap")
        self.chipbox = Gtk.FlowBox()
        self.chipbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chipbox.set_max_children_per_line(30)
        self.chipbox.set_column_spacing(7)
        self.chipbox.set_row_spacing(7)
        self.chipbox.set_homogeneous(False)
        self.chipbox.get_style_context().add_class("chipflow")
        chipwrap.pack_start(self.chipbox, False, False, 0)
        # A cookbook is a library, and the only way to reach a recipe in it was
        # to scroll the list: no search anywhere, and no Find in any menu, while
        # Contacts, Music, Journal and Academics all have one. It reads every
        # word of the recipe, because "what can I do with the lentils" is the
        # question a cook actually asks the book.
        self.search = Gtk.SearchEntry()
        nbicons.style_search_entry(self.search)
        self.search.set_placeholder_text(_t("Search recipes"))
        self.search.get_style_context().add_class("cbsearch")
        self.search.connect("search-changed", self._on_search)
        chipwrap.pack_start(self.search, False, False, 0)
        side.pack_start(chipwrap, False, False, 0)

        # recipe list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("recipelist")
        self.listbox.connect("row-activated", self._on_row_activated)
        scroll.add(self.listbox)
        side.pack_start(scroll, True, True, 0)

        # new recipe button
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        foot.get_style_context().add_class("sidefoot")
        newbtn = Gtk.Button(label=_t("+ New Recipe"))
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("newrecipe")
        newbtn.connect("clicked", lambda *_: self.new_recipe())
        foot.pack_start(newbtn, False, False, 0)
        side.pack_start(foot, False, False, 0)
        return side

    def rebuild_chips(self):
        for c in self.chipbox.get_children():
            self.chipbox.remove(c)
        self._chip_buttons = {}
        labels = ["All"] + list(self.cats)
        for i, name in enumerate(labels):
            btn = Gtk.ToggleButton()
            if i == 0:
                btn.set_label(name)          # "All" is the app's own word
            else:
                _set_recipe_text(btn, name)  # a category the cook named
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_active(i == self.active_cat)
            ctx = btn.get_style_context()
            ctx.add_class("chip")
            if i == self.active_cat:
                ctx.add_class("active")
            # A chip's width is its label's width, and a category name is free
            # text the cook typed. The row of chips is packed across the top of
            # the app, so one long name became the app's minimum width: the
            # measured overflows were 1150px with Settings > Large text on and
            # 1025px in Greek, both off a 1024px panel. Gtk.Button(label=...)
            # builds the Gtk.Label itself, so the cap has to be applied to the
            # child after the fact — there is no ellipsize property on the
            # button. The full name still shows in the tooltip.
            self._cap_chip(btn, name)
            btn.connect("clicked", self._on_chip, i)
            self.chipbox.add(btn)
            self._chip_buttons[i] = btn
        # add-category chip
        add = Gtk.Button(label=_t("+ Category"))
        add.set_relief(Gtk.ReliefStyle.NONE)
        add.get_style_context().add_class("chipadd")
        # Same treatment: this label is translated prose, and nobody has
        # measured how long "+ Category" is in every one of 17 languages.
        self._cap_chip(add)
        add.connect("clicked", lambda *_: self._new_category())
        self.chipbox.add(add)
        self.chipbox.show_all()

    @staticmethod
    def _cap_chip(btn, full=None):
        """Bound a category chip's width. Gtk.Button builds its own internal
        Gtk.Label, so ellipsizing means reaching for get_child(); guard it, a
        button whose child was replaced would otherwise raise here."""
        try:
            lbl = btn.get_child()
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(18)
        except Exception:
            return
        if full and len(full) > 18:
            _set_user_tooltip(btn, full)

    def _on_search(self, entry):
        q = entry.get_text().strip()
        if q == self.query:
            return
        self.query = q
        self._after_search()

    def _after_search(self):
        """The list has to follow the box; the recipe on the page does not —
        narrowing the shelf must not close a column the cook is typing in."""
        self.rebuild_list()
        if self._cur() is None or self.stack.get_visible_child_name() == "empty":
            self._refresh_editor()

    def _can_search(self):
        """True while the search box is on screen and there is a shelf to
        narrow — cook mode hides the whole sidebar."""
        if not self.recipes:
            return False
        try:
            return self.stack.get_visible_child_name() != "cook"
        except Exception:
            return True

    def _focus_search(self):
        """Ctrl+F / View ▸ Search Recipes — put the caret in the search box."""
        self.search.grab_focus()

    def _clear_search(self):
        """Drop the search and show the whole shelf again. True when there was
        one to drop, so Esc can fall through to closing the app when there was
        not (Esc leaves; it never destroys anything)."""
        if not self.query and not self.search.get_text():
            return False
        self.search.set_text("")
        self.query = ""
        self._after_search()
        return True

    def _matches(self, r, q):
        """True when recipe `r` answers the search `q` (already lower-cased)."""
        for key in ("title", "cat", "desc", "time", "makes", "effort",
                    "ing", "steps"):
            v = r.get(key)
            if isinstance(v, str) and q in v.lower():
                return True
        return False

    def _on_chip(self, button, idx):
        restore_focus = bool(button is not None and button.has_focus())
        self.active_cat = idx
        self.sel = -1
        self.rebuild_chips()
        self.rebuild_list()
        self._refresh_editor()
        replacement = self._chip_buttons.get(idx)
        if restore_focus and replacement is not None:
            replacement.grab_focus()

    def rebuild_list(self):
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        cat_filter = self.cats[self.active_cat - 1] if self.active_cat > 0 else None
        visible = [(i, self.recipes[i]) for i in self._visible_indices()]
        if not visible:
            # ONE empty state on screen at a time. This row used to carry
            # "No recipes" + "Add one with New Recipe, below." while the main
            # pane showed "No recipes" + "Add one with New Recipe, below the
            # list." — the same two sentences, side by side, differing by two
            # words. The old comment here set out to avoid exactly that and
            # the wording drifted back into it; a doubled empty state reads as
            # a rendering fault, so the main pane (centred, and the surface a
            # first-run user looks at) now owns the message alone.
            #
            # The one thing this list knows that the main pane does not is
            # that recipes DO exist and a category filter is hiding them —
            # "No recipes" was also simply untrue in that case. So the row
            # survives only there, and it names the category.
            #
            # A search that found nothing follows the same division: the list
            # states it (the search box is right above it), and the main pane —
            # which may well be showing a recipe — never repeats the sentence.
            if self.query:
                self.listbox.add(self._empty_row(_t("No matches")))
            elif self.recipes:
                self.listbox.add(self._empty_row(
                    _t("No recipes in “%s”") % cat_filter
                    if cat_filter else _t("No recipes")))
        else:
            for idx, r in visible:
                self.listbox.add(self._recipe_row(idx, r))
        self.listbox.show_all()

    def _empty_row(self, text):
        """A quiet, unselectable note in place of the recipe rows."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("emptylistbox")
        lbl = Gtk.Label(label=text)
        lbl.get_style_context().add_class("emptylist")
        # Translated prose in a fixed-width sidebar: wrap it, or the
        # sentence's own length sets the column's minimum.
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(22)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        box.pack_start(lbl, False, False, 0)
        row.add(box)
        return row

    def _recipe_row(self, idx, r):
        row = Gtk.ListBoxRow()
        row._idx = idx
        ctx = row.get_style_context()
        ctx.add_class("reciperow")
        selected = idx == self.sel
        if selected:
            ctx.add_class("selected")

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        # small bookmark icon (red when selected, muted otherwise)
        try:
            icon = nbicons.image(
                "bookmark", 16, "#C8341E" if selected else "#B3AD9E")
        except Exception:
            # icon rendering must never block a row from building
            icon = Gtk.Image()
        icon.get_style_context().add_class("ricon")
        icon.set_valign(Gtk.Align.START)
        outer.pack_start(icon, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title = Gtk.Label(label="", xalign=0)
        _set_recipe_text(title, r["title"], "Untitled recipe")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.get_style_context().add_class("rtitle")
        meta = Gtk.Label(label="", xalign=0)
        _set_recipe_text(meta, self._row_meta(r))
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.get_style_context().add_class("rmeta")
        # keep handles so field edits can update this row's text in place
        # (see _update_row_titles) instead of rebuilding the whole sidebar.
        row._title_lbl = title
        row._meta_lbl = meta
        box.pack_start(title, False, False, 0)
        box.pack_start(meta, False, False, 0)
        outer.pack_start(box, True, True, 0)
        row.add(outer)
        return row

    def _row_meta(self, r):
        """Compose the 'Category · time · yield' line, degrading gracefully to
        an ingredient count when the descriptive fields are empty."""
        # _t() on the FALLBACK only, never on the cook's own category name:
        # running user text through the catalog would silently "translate" a
        # category that happens to collide with a key.
        bits = [(r.get("cat") or _t("No category"))]
        for key in ("time", "makes"):
            v = (r.get(key) or "").strip()
            if v:
                bits.append(v)
        if len(bits) == 1:
            n_ing = len([x for x in (r.get("ing") or "").split("\n") if x.strip()])
            bits.append("%d ingredient%s" % (n_ing, "" if n_ing == 1 else "s"))
        return "  ·  ".join(bits)

    def _on_row_activated(self, _lb, row):
        if hasattr(row, "_idx"):
            self.sel = row._idx
            self.rebuild_list()
            self._refresh_editor()

    # --------------------------------------------------------------------- main
    def _build_main(self):
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.get_style_context().add_class("mainpane")

        # empty state
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        self.empty_title = Gtk.Label(label=_t("No recipes"))
        self.empty_title.get_style_context().add_class("emptytitle")
        # Not "Click + New Recipe to add one": "Click" is jargon for a button
        # you can also reach by keyboard, and quoting the button's label with
        # its own "+" glyph in it reads as a plus sign in the sentence.
        self.empty_hint = Gtk.Label(
            label=_t("Add one with New Recipe, below the list."))
        self.empty_hint.get_style_context().add_class("emptyhint")
        # Both of these are pure translated prose, re-set at runtime from
        # _refresh_editor with several different sentences — exactly the input
        # whose width nobody can predict. Wrap them so the sentence can never
        # set the main pane's minimum width.
        for lbl in (self.empty_title, self.empty_hint):
            lbl.set_line_wrap(True)
            lbl.set_max_width_chars(40)
            lbl.set_justify(Gtk.Justification.CENTER)
        empty.pack_start(self.empty_title, False, False, 0)
        empty.pack_start(self.empty_hint, False, False, 0)
        self.stack.add_named(empty, "empty")

        # editor / reader
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # NO PHOTO BAND. There is no image subsystem on this machine, so the
        # "photo" was only ever a line of text sitting in a 148px grey band at
        # the top of every recipe -- it looked like a picture that had failed to
        # load, and it pushed the method further down the page for nothing. The
        # stored "photo" field is still READ so an existing cookbook loads
        # unchanged; it is simply no longer shown or asked for.

        # header block
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("edhead")

        topline = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.kicker = Gtk.Label(label="", xalign=0)
        self.kicker.get_style_context().add_class("edkicker")
        # The same free text as a category chip, upper-cased and letter-spaced,
        # so it is WIDER than the chip it came from: a 45-character category
        # name measured 518px here and, with the 344px sidebar beside it, kept
        # the app at 1100px minimum even after the chips were capped. Ellipsize
        # it; the category is also shown in full on its chip and in the
        # Category picker.
        # Ellipsize is applied per name in _set_kicker, never up front: see
        # the note there about letter-spacing and Pango's fit test.
        self.kicker.set_max_width_chars(self.KICKER_MAX)
        # The eyebrow is the recipe's category CONTROL, not a caption: there
        # was no way anywhere in the app to file an existing recipe, or to move
        # one — a category could only ever be chosen by making a recipe while
        # its chip was active, and the code comment here already promised a
        # picker that did not exist.
        self.kicker_btn = Gtk.Button()
        self.kicker_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.kicker_btn.get_style_context().add_class("kickerbtn")
        self.kicker_btn.set_tooltip_text(_t("Choose the category"))
        self.kicker_btn.get_accessible().set_name(_t("Category"))
        self.kicker_btn.add(self.kicker)
        self.kicker_btn.connect("clicked", self._on_kicker_clicked)
        self.savestate = Gtk.Label(label=_t("Saved"), xalign=1)
        self.savestate.get_style_context().add_class("savestate")
        topline.pack_start(self.kicker_btn, False, False, 0)
        topline.pack_end(self.savestate, False, False, 0)
        head.pack_start(topline, False, False, 0)

        self.title_entry = Gtk.Entry()
        self.title_entry.set_placeholder_text(_t("Untitled recipe"))
        self.title_entry.set_has_frame(False)
        self.title_entry.get_style_context().add_class("edtitle")
        self.title_entry.connect("changed", self._on_title_changed)
        head.pack_start(
            self._reader_field(self.title_entry, "edtitle", _t("Title"),
                               wrap=True, lines=2, chars=30),
            False, False, 0)

        self.desc_entry = Gtk.Entry()
        self.desc_entry.set_placeholder_text(_t("Description"))
        self.desc_entry.set_has_frame(False)
        self.desc_entry.get_style_context().add_class("eddesc")
        self.desc_entry.connect("changed", self._on_desc_changed)
        head.pack_start(
            self._reader_field(self.desc_entry, "eddesc", _t("Description"),
                               wrap=True, lines=2, chars=46),
            False, False, 0)

        # The stat strip, with the way into cook mode beside it — the point at
        # which someone stops reading a recipe and starts making it.
        metarow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        metarow.pack_start(self._build_metabar(), False, False, 0)
        cookbtn = Gtk.Button(label=_t("Start cooking"))
        cookbtn.set_relief(Gtk.ReliefStyle.NONE)
        cookbtn.get_style_context().add_class("startcook")
        cookbtn.set_valign(Gtk.Align.END)
        # The one hard-minimum label left in the edit header. Beside the
        # 344px sidebar, the 144px page margin and the 364px stat strip,
        # this button gets ~150px on a 1024 panel and its Polish label
        # already uses 151 of it (the sweep measured 5px to spare). A longer
        # translation must shorten THIS label, never push the method column
        # off the panel: below its natural width it ellipsizes, exactly like
        # the kicker above, and the tooltip carries the full sentence. At any
        # width that fits the natural label, nothing changes.
        cookbtn.get_child().set_ellipsize(Pango.EllipsizeMode.END)
        cookbtn.set_tooltip_text(
            _t("Show the method one step at a time, in large type"))
        cookbtn.connect("clicked", self._enter_cook)
        metarow.pack_end(cookbtn, False, False, 0)
        head.pack_start(metarow, False, False, 0)
        editor.pack_start(head, False, False, 0)

        # ingredients + method columns
        cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=44)
        cols.get_style_context().add_class("edcols")
        cols.set_vexpand(True)
        # The ingredients column keeps its 320px measure while there is room and
        # gives width back on a small panel, so Method never collapses to three
        # words a line (see _fit_columns). The handler hangs off the Stack, not
        # off `cols`: a hidden Stack page is never allocated, but it still
        # counts towards the window's minimum width.
        self._ing_w = 0          # the width the last allocation asked for
        self._ing_w_set = None   # the width actually written to the widget
        self._ing_w_idle = None  # pending idle that will write it

        ing_box, self.ing_view, self.ing_render, self.ing_stack, \
            self.ing_edit_btn = self._panel(
                "INGREDIENTS", "One ingredient per line (name - amount)",
                320, "ing")
        self.ing_box = ing_box
        self.ing_view.get_buffer().connect("changed", self._on_ing_changed)
        cols.pack_start(ing_box, False, False, 0)

        steps_box, self.steps_view, self.steps_render, self.steps_stack, \
            self.steps_edit_btn = self._panel(
                "METHOD", "Write each step on its own line", -1, "steps")
        self.steps_view.get_buffer().connect("changed", self._on_steps_changed)
        cols.pack_start(steps_box, True, True, 0)

        editor.pack_start(cols, True, True, 0)
        self.stack.add_named(editor, "editor")

        cook = self._build_cook()
        self.stack.add_named(cook, "cook")

        self.stack.connect("size-allocate", self._fit_columns)
        # GtkStack ignores set_visible_child_name for not-yet-visible children;
        # mark every child visible up front so switching works before show_all.
        empty.show_all()
        editor.show_all()
        cook.show_all()
        self._main_pager = nbtransitions.PageSwitcher(
            self.stack, order=["empty", "editor", "cook"],
            duration=nbtransitions.PAGE)
        return self.stack

    def _switch_main(self, name):
        """Navigate primary states, but never animate a same-state refresh."""
        current = self.stack.get_visible_child_name()
        if current == name:
            if self._main_pager.target is None:
                self._main_pager.switch(name, direction=nbtransitions.NONE)
            return
        direction = (nbtransitions.NONE
                     if self._main_pager.target is None else None)
        self._main_pager.switch(name, direction=direction)

    # ------------------------------------------------------------- cook mode
    def _build_cook(self):
        """The page you actually cook from: one step at a time in large type,
        the (scalable) ingredients beside it, and buttons big enough to hit with
        the back of a floury hand. The recipe page is for writing a recipe down;
        this is for standing at the stove with it."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.get_style_context().add_class("cookpage")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        head.get_style_context().add_class("cookhead")
        self.cook_title = Gtk.Label(label="", xalign=0)
        self.cook_title.get_style_context().add_class("cooktitle")
        self.cook_title.set_ellipsize(Pango.EllipsizeMode.END)
        head.pack_start(self.cook_title, True, True, 0)

        # Servings stepper — presentational only. It never writes to the
        # recipe, so halving a cake to try it can't quietly rewrite the book.
        self.cook_scaler = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   spacing=0)
        self.cook_scaler.get_style_context().add_class("scaler")
        self.cook_scaler.set_valign(Gtk.Align.CENTER)
        less = Gtk.Button(label="–")
        less.set_relief(Gtk.ReliefStyle.NONE)
        less.get_style_context().add_class("scalebtn")
        less.set_tooltip_text(_t("Cook less"))
        less.get_accessible().set_name(_t("Cook less"))
        less.connect("clicked", lambda *_: self._cook_resize(-1))
        self.cook_serves = Gtk.Label(label="")
        self.cook_serves.get_style_context().add_class("scaleval")
        more = Gtk.Button(label="+")
        more.set_relief(Gtk.ReliefStyle.NONE)
        more.get_style_context().add_class("scalebtn")
        more.set_tooltip_text(_t("Cook more"))
        more.get_accessible().set_name(_t("Cook more"))
        more.connect("clicked", lambda *_: self._cook_resize(1))
        self.cook_scaler.pack_start(less, False, False, 0)
        self.cook_scaler.pack_start(self.cook_serves, False, False, 0)
        self.cook_scaler.pack_start(more, False, False, 0)
        # Show the CHILDREN now and mark only the container no-show-all: a
        # container flagged no-show-all is skipped by show_all() entirely, so
        # its children would never be shown and a later .show() on it would
        # reveal an empty box (which is exactly what it did).
        for child in (less, self.cook_serves, more):
            child.show()
        self.cook_scaler.set_no_show_all(True)
        head.pack_start(self.cook_scaler, False, False, 0)

        done = Gtk.Button(label=_t("Done"))
        done.set_relief(Gtk.ReliefStyle.NONE)
        done.get_style_context().add_class("cookdone")
        done.set_valign(Gtk.Align.CENTER)
        done.connect("clicked", lambda *_: self._exit_cook())
        head.pack_start(done, False, False, 0)
        page.pack_start(head, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)

        ingwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ingwrap.get_style_context().add_class("cookings")
        ingwrap.set_size_request(300, -1)
        ihd = Gtk.Label(label=_t("INGREDIENTS"), xalign=0)
        ihd.get_style_context().add_class("cookinghd")
        ingwrap.pack_start(ihd, False, False, 0)
        iscroll = Gtk.ScrolledWindow()
        iscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        iscroll.set_vexpand(True)
        self.cook_ing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        iscroll.add(self.cook_ing)
        ingwrap.pack_start(iscroll, True, True, 0)
        body.pack_start(ingwrap, False, False, 0)

        stepcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stepcol.get_style_context().add_class("cooksteps")
        stepcol.set_hexpand(True)
        self.cook_pos = Gtk.Label(label="", xalign=0)
        self.cook_pos.get_style_context().add_class("cookpos")
        stepcol.pack_start(self.cook_pos, False, False, 0)
        stepscroll = Gtk.ScrolledWindow()
        stepscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        stepscroll.set_vexpand(True)
        self.cook_step = Gtk.Label(label="", xalign=0)
        self.cook_step.set_line_wrap(True)
        self.cook_step.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # The same 46-character reading measure the recipe page's steps use —
        # at 26px that is a comfortable line, and pinning it (halign START)
        # stops a step running the full width of a big screen.
        self.cook_step.set_max_width_chars(46)
        self.cook_step.set_halign(Gtk.Align.START)
        self.cook_step.set_valign(Gtk.Align.START)
        self.cook_step.get_style_context().add_class("cooksteptext")
        stepbody = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        stepbody.pack_start(self.cook_step, False, False, 0)
        # What is coming, in small muted type: worth knowing while something is
        # already on the heat, and it stops the page reading as one line of
        # text stranded in a lot of paper.
        self.cook_peek = Gtk.Label(label="", xalign=0)
        self.cook_peek.set_line_wrap(True)
        self.cook_peek.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.cook_peek.set_max_width_chars(52)
        self.cook_peek.set_halign(Gtk.Align.START)
        self.cook_peek.get_style_context().add_class("cookpeek")
        stepbody.pack_start(self.cook_peek, False, False, 0)
        stepscroll.add(stepbody)
        stepcol.pack_start(stepscroll, True, True, 0)

        navrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        navrow.set_halign(Gtk.Align.END)
        navrow.set_vexpand(False)
        self.cook_back = Gtk.Button(label=_t("Back"))
        self.cook_back.set_relief(Gtk.ReliefStyle.NONE)
        self.cook_back.get_style_context().add_class("cooknav")
        self.cook_back.connect("clicked", lambda *_: self._cook_move(-1))
        self.cook_next = Gtk.Button(label=_t("Next step"))
        self.cook_next.set_relief(Gtk.ReliefStyle.NONE)
        self.cook_next.get_style_context().add_class("cooknav")
        self.cook_next.get_style_context().add_class("cooknext")
        self.cook_next.connect("clicked", lambda *_: self._cook_move(1))
        navrow.pack_start(self.cook_back, False, False, 0)
        navrow.pack_start(self.cook_next, False, False, 0)
        stepcol.pack_start(navrow, False, False, 0)
        body.pack_start(stepcol, True, True, 0)

        page.pack_start(body, True, True, 0)
        return page

    def _cook_steps(self, r):
        return [ln.strip() for ln in (r.get("steps") or "").split("\n")
                if ln.strip()]

    def _enter_cook(self, *_):
        """Open the current recipe in cook mode, at its first step."""
        r = self._cur()
        if r is None:
            return
        self._cook_i = 0
        self._cook_base = base_servings(r.get("makes", ""))
        self._cook_n = self._cook_base
        self._refresh_cook()
        # The recipe list is for choosing what to make; while you are making it
        # the page is worth more than the list, and a 26px step needs the width.
        try:
            self._side.hide()
        except Exception:
            pass
        self._switch_main("cook")

    def _exit_cook(self, *_):
        """Back to the recipe page. Nothing was changed, so there is nothing to
        save or discard."""
        try:
            self._side.show()
        except Exception:
            pass
        if self.stack.get_visible_child_name() == "cook":
            self._switch_main("editor")

    def _cook_move(self, delta):
        r = self._cur()
        if r is None:
            return
        steps = self._cook_steps(r)
        if not steps:
            return
        self._cook_i = max(0, min(self._cook_i + delta, len(steps) - 1))
        self._refresh_cook()

    def _cook_resize(self, delta):
        """Cook for more or fewer people. Only the shown amounts change."""
        if not self._cook_base:
            return
        self._cook_n = max(1, min((self._cook_n or 1) + delta, 99))
        self._refresh_cook()

    def _refresh_cook(self):
        r = self._cur()
        if r is None:
            return
        _set_recipe_text(self.cook_title, r.get("title"), "Untitled recipe")

        factor = 1.0
        if self._cook_base and self._cook_n:
            factor = self._cook_n / float(self._cook_base)
        if self._cook_base:
            self.cook_serves.set_text(
                restate_servings(r.get("makes", ""), self._cook_n))
            self.cook_scaler.show()
        else:
            self.cook_scaler.hide()

        for c in self.cook_ing.get_children():
            self.cook_ing.remove(c)
        lines = [ln for ln in (r.get("ing") or "").split("\n") if ln.strip()]
        if not lines:
            lbl = Gtk.Label(label=_t("No ingredients"), xalign=0)
            lbl.get_style_context().add_class("cookingempty")
            self.cook_ing.pack_start(lbl, False, False, 0)
        for ln in lines:
            name, amount = self._split_ing(ln)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.get_style_context().add_class("cookingrow")
            nm = Gtk.Label(xalign=0)
            _set_recipe_text(nm, name)
            nm.set_line_wrap(True)
            nm.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            nm.set_valign(Gtk.Align.START)
            nm.get_style_context().add_class("cookingname")
            row.pack_start(nm, True, True, 0)
            if amount:
                al = Gtk.Label(xalign=1)
                _set_recipe_text(al, scale_amount(amount, factor))
                al.set_valign(Gtk.Align.START)
                al.set_halign(Gtk.Align.END)
                al.set_margin_top(3)   # optically level with the serif name
                al.get_style_context().add_class("cookingamt")
                row.pack_end(al, False, False, 0)
            self.cook_ing.pack_start(row, False, False, 0)
        self.cook_ing.show_all()

        steps = self._cook_steps(r)
        if not steps:
            self.cook_pos.set_text(_t("No method"))
            self.cook_step.set_text(
                _t("Add steps on the recipe page."))
            self.cook_peek.set_text("")
            self.cook_back.set_sensitive(False)
            self.cook_next.set_sensitive(False)
            return
        self._cook_i = max(0, min(self._cook_i, len(steps) - 1))
        # Translate in sentence case (the catalog's key) and upper-case after,
        # so this eyebrow reuses the same string the rest of the OS carries
        # instead of needing a shouted duplicate in four languages.
        self.cook_pos.set_text(
            (_t("Step %d of %d") % (self._cook_i + 1, len(steps))).upper())
        _set_recipe_text(self.cook_step, steps[self._cook_i])
        if self._cook_i + 1 < len(steps):
            self.cook_peek.set_text(
                _t("Next: %s") % steps[self._cook_i + 1])
        else:
            self.cook_peek.set_text(_t("Last step"))
        self.cook_back.set_sensitive(self._cook_i > 0)
        self.cook_next.set_sensitive(self._cook_i < len(steps) - 1)

    def _on_key(self, w, ev):
        """In cook mode the arrows (and Space) walk the steps and Esc closes it,
        so a recipe can be followed without hunting for a small button. Anything
        else, and any other page, falls through to the base handler."""
        # Undo first, and on every page: the shortcut has to work while the
        # caret is in an ingredient field, which is where the loss happens.
        if nbapp.undo_keys(self.undo, ev):
            return True
        # The search box is in the sidebar, which cook mode hides, so cook mode
        # keeps Esc (leave the stove page) and Ctrl+F falls through there.
        if self._can_search():
            # Esc drops an active search before it reaches the base handler
            # (which would close the whole app) — the way out a narrowed list
            # needs. Esc leaves; it never destroys anything.
            if ev.keyval == Gdk.KEY_Escape and self._clear_search():
                return True
            if (ev.state & Gdk.ModifierType.CONTROL_MASK
                    and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F)):
                self._focus_search()
                return True
        try:
            if self.stack.get_visible_child_name() == "cook":
                kv = ev.keyval
                if kv == Gdk.KEY_Escape:
                    self._exit_cook()
                    return True
                if kv in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_space,
                          Gdk.KEY_Page_Down):
                    self._cook_move(1)
                    return True
                if kv in (Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_BackSpace,
                          Gdk.KEY_Page_Up):
                    self._cook_move(-1)
                    return True
        except Exception:
            pass
        return super()._on_key(w, ev)

    def _fit_columns(self, _stack, alloc):
        """Share the recipe page between Ingredients and Method.

        Ingredients is a narrow ledger and Method is prose, so the design fixes
        Ingredients at 320px — but on a 1024-wide screen that left Method about
        170px, roughly three words a line. Give Ingredients its 320px whenever
        the page is wide enough and let it shrink (never below 200px) when it
        is not. Setting the same width twice is a no-op, so this settles on the
        first allocation instead of looping."""
        inner = alloc.width - 144 - 44      # .edcols padding + the column gap
        w = max(200, min(320, int(inner * 0.42)))
        if w != self._ing_w:
            self._ing_w = w
            self._apply_ing_width()

    def _apply_ing_width(self):
        """Ask for the Ingredients width from an idle, not from inside the
        allocation pass.

        A size request made WHILE the stack is being allocated is stored on the
        widget and then never allocated: coming back from cook mode (where the
        sidebar is hidden and the page is 1024 wide) computed the right 206px,
        wrote it, and left Ingredients sitting at its 320px cook-mode width with
        Method squashed to three or four words a line until something else
        forced a relayout. Landing the request one idle later puts it in its own
        layout pass, which does run. The last value actually written is kept so
        a re-entrant allocation cannot start a request/allocate loop."""
        if self._ing_w == self._ing_w_set or self._ing_w_idle is not None:
            return

        def landing():
            self._ing_w_idle = None
            if self._ing_w != self._ing_w_set:
                self._ing_w_set = self._ing_w
                self.ing_box.set_size_request(self._ing_w, -1)
            return False

        self._ing_w_idle = GLib.idle_add(landing)

    def _set_kicker(self, r):
        """The category eyebrow for recipe `r`, upper-cased.

        Shortened ONLY when the name really is longer than the eyebrow's cap.
        Pango charges the trailing letter-spacing of the last letter against the
        line when it tests whether the text fits, but leaves it out of the
        natural width the label asks for — so a kicker allocated exactly what it
        asked for was judged too narrow and EVERY category came out cut:
        "SID…" for Sides, "NO CATEGO…" with the rest of the line empty. The cap
        is what bounds the header's width, and it still does."""
        cat = r.get("cat") if r else None
        text = (cat if cat else _t("No category")).upper()
        self.kicker.set_ellipsize(Pango.EllipsizeMode.END
                                  if len(text) > self.KICKER_MAX
                                  else Pango.EllipsizeMode.NONE)
        if cat:
            _set_recipe_text(self.kicker, text)   # the cook's category
        else:
            self.kicker.set_text(text)            # our own "No category"

    def _on_kicker_clicked(self, button):
        """Offer every category, No category, and a way to make a new one."""
        if self._cur() is None:
            return
        try:
            menu = Gtk.Menu()
            current = (self._cur() or {}).get("cat")
            none_item = Gtk.MenuItem(
                label=("✓  " if not current else "     ") + _t("No category"))
            none_item.connect("activate", lambda *_: self._file_current(None))
            menu.append(none_item)
            for name in self.cats:
                # The cook's own category name, never run through the catalog.
                shown = ("✓  " if name == current else "     ") + name
                item = Gtk.MenuItem(label=shown)
                nbi18n.set_verbatim(item.get_child(), shown)
                item.connect("activate", lambda _mi, n=name:
                             self._file_current(n))
                menu.append(item)
            menu.append(Gtk.SeparatorMenuItem())
            new_item = Gtk.MenuItem(label=_t("New Category…"))
            new_item.connect("activate", lambda *_: self._new_category(True))
            menu.append(new_item)
            menu.show_all()
            nbapp.popup_at(menu, widget=button, anchor="widget-sw")
        except Exception:
            # The category control must never crash the recipe page.
            pass

    def _file_current(self, cat):
        """File the current recipe under `cat` (None = No category)."""
        r = self._cur()
        if r is None or r.get("cat") == cat:
            return
        self.undo.checkpoint("Move to Category")
        r["cat"] = cat
        # Follow the recipe: leaving it selected under a filter that no longer
        # shows it would put an empty list beside a recipe page.
        if self.active_cat > 0 and self.sel not in self._cat_indices():
            self.active_cat = (self.cats.index(cat) + 1) if cat in self.cats else 0
            self.rebuild_chips()
        self._set_kicker(r)
        self.rebuild_list()
        self._touch()
        self.undo.commit()

    def _build_metabar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("metabar")
        bar.set_halign(Gtk.Align.START)
        specs = [("TIME", "time"), ("MAKES", "makes"), ("EFFORT", "effort")]
        for i, (cap, field) in enumerate(specs):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            ctx = cell.get_style_context()
            ctx.add_class("metacell")
            if i > 0:
                ctx.add_class("metadiv")
            localized_cap = _t(cap.title())
            cl = Gtk.Label(label=localized_cap.upper(), xalign=0)
            cl.get_style_context().add_class("metacap")
            ent = Gtk.Entry()
            ent.set_has_frame(False)
            ent.set_placeholder_text("—")
            ent.set_width_chars(10)
            ent.get_style_context().add_class("metaval")
            cl.set_mnemonic_widget(ent)
            ent.get_accessible().set_name(localized_cap)
            ent.connect("changed", self._on_meta_changed, field)
            cell.pack_start(cl, False, False, 0)
            cell.pack_start(self._reader_field(ent, "metaval", localized_cap,
                                               chars=10), False, False, 0)
            self.meta_entries[field] = ent
            bar.pack_start(cell, False, False, 0)
        return bar

    # ------------------------------------------------- read/edit text fields
    def _reader_field(self, entry, css, name, wrap=False, lines=1, chars=0):
        """Wrap `entry` so its value READS when nobody is editing it.

        A Gtk.Entry can neither wrap nor ellipsize — it scrolls — so a recipe
        title longer than the column showed whichever end the caret had last
        left in view: the tail right after typing ("ndma’s Chicken Soup"),
        and after the recipe was reselected the head with the right-hand side
        simply cut off, no ellipsis either time to say anything was missing.
        The sidebar row a few hundred pixels away showed the same title in full,
        which made the recipe page the one place a recipe’s own name could
        not be read. Same for the description and the Time / Makes / Effort
        cells.

        So the field shows a label that wraps (or ellipsizes, and then carries
        the whole value in its tooltip) and swaps in the real entry when it is
        clicked or focused — which is exactly how the Ingredients and Method
        columns beside it already work, rather than a new idea."""
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        # Size to the child on screen, not to the larger of the two, or the
        # read label’s wrapped height would pad the entry as well.
        stack.set_hhomogeneous(False)
        stack.set_vhomogeneous(False)
        lbl = Gtk.Label(label="", xalign=0)
        lbl.get_style_context().add_class(css)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        if wrap:
            lbl.set_line_wrap(True)
            # WORD_CHAR so one long word cannot set a minimum width the whole
            # window has to grow to (the same rule the method rows follow).
            lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_lines(lines)
        if chars:
            lbl.set_max_width_chars(chars)
            if not wrap:
                lbl.set_width_chars(chars)   # so the cell never changes width
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("readfield")
        btn.add(lbl)
        btn.get_accessible().set_name(name)
        btn.connect("clicked", lambda *_: self._edit_field(entry))
        stack.add_named(btn, "read")
        stack.add_named(entry, "edit")
        entry._read_label = lbl
        entry._read_chars = chars
        entry._field_stack = stack
        entry.connect("changed", lambda *_: self._sync_field(entry))
        entry.connect("activate", lambda *_: self._leave_field(entry))
        entry.connect("focus-in-event", self._on_field_focus_in)
        entry.connect("focus-out-event", self._on_field_focus_out)
        self._fields.append(entry)
        self._sync_field(entry)
        return stack

    def _sync_field(self, entry):
        """Put what the entry holds on its read label."""
        lbl = getattr(entry, "_read_label", None)
        if lbl is None:
            return
        text = entry.get_text()
        ctx = lbl.get_style_context()
        if text:
            ctx.remove_class("placeholder")
            # The cook’s own words: never run them through the catalog.
            nbi18n.set_verbatim(lbl, text)
        else:
            ctx.add_class("placeholder")
            lbl.set_text(entry.get_placeholder_text() or "")
        chars = getattr(entry, "_read_chars", 0)
        # A value the label had to shorten still has to be readable somehow.
        _set_user_tooltip(lbl, text if (chars and len(text) > chars) else "")

    def _edit_field(self, entry):
        """Swap the real entry in and put the caret at the end of the text."""
        if self._cur() is None:
            return
        stack = getattr(entry, "_field_stack", None)
        if stack is not None:
            stack.set_visible_child_name("edit")
        entry.grab_focus()
        entry.set_position(-1)

    def _leave_field(self, entry):
        """Back to reading. The entry keeps its scroll offset for as long as it
        exists, so it is wound back to the start of the text as well."""
        self._sync_field(entry)
        entry.set_position(0)
        stack = getattr(entry, "_field_stack", None)
        if stack is not None:
            stack.set_visible_child_name("read")

    def _on_field_focus_in(self, entry, _ev):
        # Reached by Tab as well as by clicking the label.
        stack = getattr(entry, "_field_stack", None)
        if stack is not None:
            stack.set_visible_child_name("edit")
        return False

    def _on_field_focus_out(self, entry, _ev):
        self._leave_field(entry)
        return False

    def _read_fields(self):
        """Show every header field as text again (a recipe was just opened)."""
        for entry in self._fields:
            self._sync_field(entry)
            entry.set_position(0)
            stack = getattr(entry, "_field_stack", None)
            if stack is not None:
                stack.set_visible_child_name("read")

    def _panel(self, heading, placeholder, width, kind):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("edpanel")
        if width > 0:
            box.set_size_request(width, -1)

        # ruled heading row: the section title on the left, a small "Edit"
        # affordance on the right that flips the panel to its editable text.
        hdrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hdrow.get_style_context().add_class("edpanelhd")
        hd = Gtk.Label(label=heading, xalign=0)
        hd.get_style_context().add_class("edpanelhdtext")
        hd.set_valign(Gtk.Align.CENTER)
        hdrow.pack_start(hd, True, True, 0)
        edit_btn = Gtk.Button(label=_t("Edit"))
        edit_btn.set_relief(Gtk.ReliefStyle.NONE)
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.get_style_context().add_class("paneledit")
        edit_btn.connect("clicked", self._toggle_panel_edit, kind)
        hdrow.pack_end(edit_btn, False, False, 0)
        box.pack_start(hdrow, False, False, 0)

        # two children — the structured read view, and the raw text editor —
        # swapped instantly (NONE transition; crossfade stalls under swrast).
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.NONE)

        view_scroll = Gtk.ScrolledWindow()
        view_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        view_scroll.set_vexpand(True)
        render_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        render_box.get_style_context().add_class("renderlist")
        view_scroll.add(render_box)
        stack.add_named(view_scroll, "view")

        edit_scroll = Gtk.ScrolledWindow()
        # EXTERNAL, not NEVER, horizontally: NEVER passes the TextView's own
        # width request up as a hard minimum (a text view asks for the width of
        # its longest UNWRAPPED line), which alone made the window 130px wider
        # than a 1024-wide screen. The view wraps at whatever width it gets, so
        # there is never anything to scroll sideways.
        edit_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        edit_scroll.set_vexpand(True)
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_pixels_below_lines(10)
        view.set_pixels_inside_wrap(8)
        view.get_style_context().add_class("edbox")
        view._placeholder = placeholder
        self._attach_placeholder(view)
        edit_scroll.add(view)
        stack.add_named(edit_scroll, "edit")

        box.pack_start(stack, True, True, 0)
        return box, view, render_box, stack, edit_btn

    # -------------------------------------------------------------- placeholder
    def _attach_placeholder(self, view):
        buf = view.get_buffer()
        view._ph_active = False

        def show_ph():
            if buf.get_char_count() == 0 and not view.has_focus():
                view._ph_active = True
                buf.set_text(view._placeholder)
                view.get_style_context().add_class("placeholder")

        def hide_ph():
            if view._ph_active:
                view._ph_active = False
                buf.set_text("")
                view.get_style_context().remove_class("placeholder")

        def on_focus_in(*_):
            hide_ph()
            return False

        def on_focus_out(*_):
            show_ph()
            return False

        view.connect("focus-in-event", on_focus_in)
        view.connect("focus-out-event", on_focus_out)
        view._show_ph = show_ph
        view._hide_ph = hide_ph
        show_ph()

    def _view_text(self, view):
        if getattr(view, "_ph_active", False):
            return ""
        buf = view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _set_view(self, view, text):
        buf = view.get_buffer()
        view._ph_active = False
        view.get_style_context().remove_class("placeholder")
        buf.set_text(text or "")
        if not text:
            view._show_ph()

    # -------------------------------------------------- structured rendering
    def _render_placeholder(self, box, text, kind):
        # The empty read view doubles as an affordance: activating the hint drops
        # straight into that column's text editor, so a novice never has to hunt
        # for the small "Edit" control just to add a first line.
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("renderphbtn")
        evt.set_tooltip_text(
            _t("Add ingredients") if kind == "ing" else _t("Add instructions"))
        lbl = Gtk.Label(label=text, xalign=0)
        # Wrap it: on one line this hint is the widest thing in an empty
        # recipe, and it alone set the window's minimum width (1018px of the
        # 1024 budget, with nothing left for a longer translation).
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.get_style_context().add_class("renderph")
        evt.add(lbl)
        evt.connect("clicked", lambda *_: self._enter_panel_edit(kind))
        box.pack_start(evt, False, False, 0)

    def _enter_panel_edit(self, kind):
        """Flip a column into its text editor (backs the clickable empty-state
        hint). No-op when it is already editing or no recipe is open."""
        if self._cur() is None:
            return True
        stack = self.ing_stack if kind == "ing" else self.steps_stack
        btn = self.ing_edit_btn if kind == "ing" else self.steps_edit_btn
        if stack.get_visible_child_name() != "edit":
            self._toggle_panel_edit(btn, kind)
        return True

    def _render_ingredients(self, text):
        """Lay the raw 'name — amount' lines out as a two-column ledger:
        ingredient name flush left, amount right-aligned in muted grey."""
        box = self.ing_render
        for c in box.get_children():
            box.remove(c)
        lines = [ln for ln in (text or "").split("\n") if ln.strip()]
        if not lines:
            self._render_placeholder(box, self.ing_view._placeholder, "ing")
        else:
            for ln in lines:
                box.pack_start(self._ingredient_row(ln), False, False, 0)
        box.show_all()

    def _split_ing(self, line):
        """Split an ingredient line into (name, amount). The amount may follow
        the name after ' - ' (a plain, keyboard-typable hyphen padded with
        spaces) or an em/en dash; a line with no separator is all name and gets
        no amount column. The spaces around the hyphen keep hyphenated names
        (e.g. self-raising flour) intact."""
        for sep in ("—", " – ", " - "):
            if sep in line:
                name, _, amount = line.partition(sep)
                return name.strip(), amount.strip()
        return line.strip(), ""

    def _ingredient_row(self, line):
        name, amount = self._split_ing(line)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("ingrow")
        nm = Gtk.Label(xalign=0)
        _set_recipe_text(nm, name)
        # An ingredient you cannot read is useless, and on a 1024-wide screen
        # this column is only ~200px: ellipsizing turned "Preserved lemons,
        # quartered" into "Preserved lemons, quart...". Wrap onto a second line
        # instead — the row simply grows a little taller.
        nm.set_line_wrap(True)
        nm.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # BASELINE, not START: see the amount below.
        nm.set_valign(Gtk.Align.BASELINE)
        nm.get_style_context().add_class("ingname")
        row.pack_start(nm, True, True, 0)
        if amount:
            al = Gtk.Label(xalign=1)
            _set_recipe_text(al, amount)
            al.set_line_wrap(True)                     # a wordy amount wraps
            al.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            al.set_max_width_chars(16)                 # but never widens the
            al.set_halign(Gtk.Align.END)               # column (halign, since
            al.get_style_context().add_class("ingamt")  # does not cap a FILL
            # 12px sans beside 16px serif: aligned at the TOP the amount sits
            # six pixels above the name it belongs to, and the ledger reads as
            # two lists rather than one. Both labels share the row's baseline
            # instead — measured level whatever the two type sizes are, so it
            # holds with Settings ▸ Large text as a fixed nudge would not. (A
            # wrapped name keeps its FIRST line on that baseline, which is the
            # line the amount belongs beside.)
            al.set_valign(Gtk.Align.BASELINE)
            row.pack_end(al, False, False, 0)          # child)
        return row

    def _render_steps(self, text):
        """Render each method line as a numbered row: a thin-outline circle
        holding the step index, then the step's serif prose."""
        box = self.steps_render
        for c in box.get_children():
            box.remove(c)
        lines = [ln for ln in (text or "").split("\n") if ln.strip()]
        if not lines:
            self._render_placeholder(box, self.steps_view._placeholder, "steps")
        else:
            for i, ln in enumerate(lines):
                box.pack_start(self._step_row(i + 1, ln.strip()),
                               False, False, 0)
        box.show_all()

    def _step_row(self, n, text):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.get_style_context().add_class("steprow")
        num = Gtk.Label(label=str(n))
        num.set_valign(Gtk.Align.START)
        num.set_halign(Gtk.Align.START)
        num.get_style_context().add_class("stepnum")
        row.pack_start(num, False, False, 0)
        body = Gtk.Label(xalign=0)
        _set_recipe_text(body, text)
        body.set_line_wrap(True)
        # WORD_CHAR, not WORD: a single very long word (a pasted link, say)
        # cannot then set a minimum width the window has to grow to.
        body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_xalign(0)
        body.set_valign(Gtk.Align.START)
        body.set_max_width_chars(46)
        # max_width_chars only caps what the label ASKS for; left to fill its
        # column the step still wrapped at the full window width, so on a wide
        # screen a method line ran right across the page. Pinning it to the
        # start keeps the 46-character reading measure the design specifies.
        body.set_halign(Gtk.Align.START)
        body.get_style_context().add_class("steptext")
        row.pack_start(body, True, True, 0)
        return row

    def _toggle_panel_edit(self, btn, kind):
        """Flip a column between its structured read view and the raw text
        editor. The buffer's changed handler writes through on every keystroke,
        so leaving edit mode just needs a re-render — persistence is unchanged."""
        r = self._cur()
        if r is None:
            return
        view = self.ing_view if kind == "ing" else self.steps_view
        stack = self.ing_stack if kind == "ing" else self.steps_stack
        if stack.get_visible_child_name() != "edit":
            self._loading = True
            self._set_view(view, r.get(kind, ""))
            self._loading = False
            stack.set_visible_child_name("edit")
            btn.set_label(_t("Done"))
            view.grab_focus()
        else:
            stack.set_visible_child_name("view")
            btn.set_label(_t("Edit"))
            if kind == "ing":
                self._render_ingredients(r.get("ing", ""))
            else:
                self._render_steps(r.get("steps", ""))

    # ------------------------------------------------------------------ actions
    def new_recipe(self):
        self.undo.checkpoint("New Recipe")
        cat = self.cats[self.active_cat - 1] if self.active_cat > 0 else None
        # Build through the canonical dict shape so every render/edit/persist
        # path finds the full key set (including "photo").
        self.recipes.append(self._make_recipe(cat=cat))
        self.sel = len(self.recipes) - 1
        self.rebuild_list()
        self._refresh_editor()
        self._touch()
        self.undo.commit()

    def _make_recipe(self, **fields):
        """Build a recipe dict with the full default key set (the same shape
        new_recipe() produces), overlaid with the supplied fields. Guarantees
        every render/edit path finds the keys it expects."""
        rec = {"title": "", "cat": None, "desc": "", "time": "", "makes": "",
               "effort": "", "ing": "", "steps": "", "photo": ""}
        rec.update(fields)
        return rec

    def _cur(self):
        if 0 <= self.sel < len(self.recipes):
            return self.recipes[self.sel]
        return None

    def _refresh_editor(self):
        # This always lands on the recipe page or an empty state, so bring the
        # recipe list back if cook mode had hidden it (View ▸ Next Recipe and
        # the category filters are still reachable from in there).
        try:
            getattr(self, "_side").show()
        except Exception:
            pass
        r = self._cur()
        if r is None:
            # Distinguish three empty states so the main pane never contradicts
            # the sidebar. In particular: when the active category filter is
            # empty but recipes exist elsewhere, the sidebar list shows nothing
            # to choose, so don't prompt "Select a recipe from the list" —
            # name the empty category instead (state-consistency fix).
            # EVERY string set here goes through _t(). These labels are built
            # once with _t() and then re-set by hand the first time the list is
            # filtered; nbapp translates the widget TREE at construction, so a
            # bare set_text() after that snapped all five of these back to
            # English on, say, a Spanish install and left them there.
            if not self.recipes:
                self.empty_title.set_text(_t("No recipes"))
                self.empty_hint.set_text(
                    _t("Add one with New Recipe, below the list."))
            elif self.query and not self._visible_indices():
                # The list has already said "No matches" beside the search box;
                # this pane offers the way out of it instead of repeating it.
                self.empty_title.set_text(
                    _t("Clear the search to see all recipes."))
                self.empty_hint.set_text("")
            elif not self._visible_indices():
                # The LIST owns the sentence that names the empty category (it
                # is the only surface that knows a filter is hiding recipes).
                # Naming it here as well put the identical sentence on screen
                # twice, a few hundred pixels apart, which reads as a rendering
                # fault — the very thing the note in rebuild_list sets out to
                # avoid. So the list states the absence and this pane, which is
                # the one with room for it, offers the way out of it.
                self.empty_title.set_text(_t("Add one with New Recipe."))
                self.empty_hint.set_text("")
            else:
                self.empty_title.set_text(_t("No recipe selected"))
                self.empty_hint.set_text(_t("Select a recipe from the list"))
            # An empty label still takes a line of height, which would push the
            # centred block off centre.
            self.empty_hint.set_visible(bool(self.empty_hint.get_text()))
            self._switch_main("empty")
            return
        self._loading = True
        self._set_kicker(r)
        self.title_entry.set_text(r.get("title", ""))
        self.desc_entry.set_text(r.get("desc", ""))
        for field, ent in self.meta_entries.items():
            ent.set_text(r.get(field, ""))
        self._render_ingredients(r.get("ing", ""))
        self._render_steps(r.get("steps", ""))
        # switching recipes always lands on the read view with a fresh "Edit"
        # affordance, even if the previous recipe was left mid-edit.
        self.ing_stack.set_visible_child_name("view")
        self.steps_stack.set_visible_child_name("view")
        self.ing_edit_btn.set_label(_t("Edit"))
        self.steps_edit_btn.set_label(_t("Edit"))
        self._read_fields()
        self._show_save_state()
        self._switch_main("editor")
        self._loading = False

    def _on_title_changed(self, entry):
        r = self._cur()
        if r is None or getattr(self, "_loading", False):
            return
        r["title"] = entry.get_text()
        self._set_kicker(r)
        self._update_row_titles()
        self._touch()

    def _on_desc_changed(self, entry):
        r = self._cur()
        if r is None or getattr(self, "_loading", False):
            return
        r["desc"] = entry.get_text()
        self._touch()

    def _on_meta_changed(self, entry, field):
        r = self._cur()
        if r is None or getattr(self, "_loading", False):
            return
        r[field] = entry.get_text()
        self._update_row_titles()
        self._touch()

    def _on_ing_changed(self, buf):
        r = self._cur()
        if r is None or getattr(self, "_loading", False) or \
                getattr(self.ing_view, "_ph_active", False):
            return
        r["ing"] = self._view_text(self.ing_view)
        self._update_row_titles()
        self._touch()

    def _on_steps_changed(self, buf):
        r = self._cur()
        if r is None or getattr(self, "_loading", False) or \
                getattr(self.steps_view, "_ph_active", False):
            return
        r["steps"] = self._view_text(self.steps_view)
        self._touch()

    def _update_row_titles(self):
        """Refresh the selected recipe's sidebar row in place — a field edit
        (title / time / makes / ingredients) changes only that one row's
        title and meta text, so mutate its existing Gtk.Labels rather than
        tearing down and rebuilding the whole list (each row of which re-runs
        an nbicons bookmark encode). Structural changes — new/delete/select/
        category switch — still go through rebuild_list()."""
        r = self._cur()
        if r is None:
            return
        row = self._find_row(self.sel)
        if row is None:
            # The edited recipe isn't in the current filtered view (shouldn't
            # happen on an edit path, since only a visible row can be selected)
            # — fall back to a full rebuild to stay correct.
            self.rebuild_list()
            return
        title_lbl = getattr(row, "_title_lbl", None)
        if title_lbl is not None:
            _set_recipe_text(title_lbl, r["title"], "Untitled recipe")
        meta_lbl = getattr(row, "_meta_lbl", None)
        if meta_lbl is not None:
            _set_recipe_text(meta_lbl, self._row_meta(r))

    def _find_row(self, idx):
        """Locate the current sidebar ListBoxRow for recipe index `idx`."""
        for row in self.listbox.get_children():
            if getattr(row, "_idx", None) == idx:
                return row
        return None

    def _undo_snapshot(self):
        """The whole library as a state. _serialize already produces exactly
        this — categories, recipes, the active filter and the selection — and
        reusing it means undo can never capture a different subset of the model
        than the autosave writes.

        The selection and the filter ride along as VOLATILE keys ("_"-prefixed,
        which UndoHistory keeps but ignores when it decides whether two states
        differ — Writer's caret is the same idea). Choosing a recipe or a chip
        is navigation, not an edit anyone would ask to take back; carried as
        ordinary keys they made Ctrl+Z on a mistyped title jump the page to
        whatever recipe and chip the previous snapshot happened to hold."""
        state = self._serialize()
        state["_sel"] = state.pop("sel")
        state["_active_cat"] = state.pop("active_cat")
        return state

    def _undo_restore(self, state):
        """Put a snapshot back on screen. Rebuilds the chips as well as the
        list: undoing a New Category has to take the chip away too, and undoing
        a Delete Category has to bring it back."""
        before = self._undo_snapshot()
        self._apply_undo_state(state)
        if self._save_timer:
            try:
                GLib.source_remove(self._save_timer)
            except Exception:
                pass
            self._save_timer = None
        if not self._save_state():
            self._apply_undo_state(before)
            # Repair best-effort if a writer failed after publishing. The
            # persistent status message from the first failure remains visible.
            self._save_state()
            return False
        # The message a delete left on the chip has been answered by this undo.
        self._clear_flash()
        self._show_save_state()
        return True

    def _apply_undo_state(self, state):
        """Apply one complete cookbook snapshot without scheduling a save.

        WHERE THE PAGE LANDS. The snapshot's selection and filter are put back
        only when the restore is STRUCTURAL — the library gained or lost a
        recipe or a category (an undo of New Recipe, Duplicate, Delete Recipe,
        New/Delete Category), which is exactly when what was on screen may no
        longer exist. An undo of typing changes text and nothing else, so it
        leaves you on the recipe you are editing, in the column you were
        typing in."""
        known = {"cats", "active_cat", "sel", "recipes", "_sel", "_active_cat"}
        self._extra = {k: copy.deepcopy(v) for k, v in state.items()
                       if k not in known}
        here_sel, here_cat = self.sel, self.active_cat
        n_recipes, n_cats = len(self.recipes), len(self.cats)
        editing = [kind for kind, stack in (("ing", self.ing_stack),
                                            ("steps", self.steps_stack))
                   if stack.get_visible_child_name() == "edit"]
        self.cats = list(state.get("cats", []))
        self.recipes = [self._make_recipe(**r) for r in state.get("recipes", [])]
        snap_sel = state.get("_sel", state.get("sel", -1))
        snap_cat = state.get("_active_cat", state.get("active_cat", 0))
        structural = (len(self.recipes) != n_recipes or len(self.cats) != n_cats)
        cat = snap_cat if structural else here_cat
        if not self._valid_cat(cat):
            cat = snap_cat if self._valid_cat(snap_cat) else 0
        self.active_cat = cat
        sel = snap_sel if structural else here_sel
        if isinstance(sel, int) and 0 <= sel < len(self.recipes):
            self.sel = sel
        elif sel == -1:
            self.sel = -1          # nothing was selected: don't invent one
        else:
            # The recipe that was on screen is gone. Fall back INSIDE the active
            # filter — recipe 0 blindly put a recipe from another category on
            # the page while the chip beside it said otherwise.
            vis = self._visible_indices()
            self.sel = vis[0] if vis else -1
        if self.sel >= 0 and self.sel not in self._cat_indices():
            # ...and never show a recipe the category filter hides.
            self.active_cat = self._filter_for(self.sel)
        self.rebuild_chips()
        self.rebuild_list()
        self._refresh_editor()
        # _refresh_editor drops both columns back to their read view, which is
        # right when the recipe changed and wrong when it did not: undo pressed
        # with the caret in the ingredient editor closed the editor under it.
        if self.sel == here_sel and self._cur() is not None:
            for kind in editing:
                self._enter_panel_edit(kind)

    def _valid_cat(self, cat):
        return (isinstance(cat, int) and not isinstance(cat, bool)
                and 0 <= cat <= len(self.cats))

    def _filter_for(self, idx):
        """The chip index that shows recipe `idx` (0 = All)."""
        cat = self.recipes[idx].get("cat") if 0 <= idx < len(self.recipes) else None
        return (self.cats.index(cat) + 1) if cat in self.cats else 0

    def _touch(self):
        self.undo.touch()
        self._recovery_dirty = True
        # Typing again answers whatever the last message said, so the chip goes
        # back to reporting the document.
        self._clear_flash()
        self._show_save_state()
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(900, self._mark_saved)

    def _mark_saved(self):
        # The debounce has fired: perform the REAL disk write, and only claim
        # "Saved" once the bytes have actually reached the file.
        if self._closed:
            self._save_timer = None
            return False
        self._save_state()
        self._save_timer = None
        self._show_save_state()
        return False

    def _dot(self, color, text):
        self.savestate.set_markup('<span foreground="%s">● </span>%s'
                                  % (color, GLib.markup_escape_text(text)))

    def _show_save_state(self):
        """Say what has actually happened to the file.

        This label used to be stamped with time.strftime() on every refresh, so
        opening the app claimed a save at the moment you opened it and clicking
        any recipe re-stamped it — a time nothing was written at. Worse, that
        stamp also ran on the navigation refresh, so a red "Not saved" from a
        write that really had failed was replaced by a green "Saved <now>" as
        soon as you clicked another recipe, while the file still held the old
        text. The chip now renders from what the writer recorded, and a
        transient message (a delete, an export) keeps the chip until it has been
        on screen long enough to read."""
        if time.monotonic() < self._flash_until:
            return
        if self._recovery_dirty and self._save_failed:
            self._dot("#C8341E", _t("Not saved"))
        elif self._recovery_dirty:
            self._dot("#C8341E", _t("Editing…"))
        elif self._saved_at:
            self._dot("#7FA98C", _t("Saved %s") % self._saved_at)
        else:
            # Nothing has been written this session: the library on screen is
            # the one in the file, and no clock reading would be true.
            self._dot("#7FA98C", _t("Saved"))

    # ------------------------------------------------------------- persistence
    def _serialize(self):
        """Build the on-disk dict for the whole cookbook (categories, recipes,
        the active filter and the selection) written to the autosave file."""
        # Unknown top-level fields may belong to a newer Cookbook build. Carry
        # them through an edit instead of silently downgrading the store and
        # erasing metadata this version does not understand.
        data = dict(getattr(self, "_extra", None) or {})
        data.update({
            "cats": list(self.cats),
            "active_cat": self.active_cat,
            "sel": self.sel,
            "recipes": [self._serialize_recipe(r) for r in self.recipes],
        })
        return data

    @staticmethod
    def _serialize_recipe(r):
        known = {"title", "cat", "desc", "time", "makes", "effort",
                 "ing", "steps", "photo"}
        out = {k: copy.deepcopy(v) for k, v in r.items() if k not in known}
        out.update({"title": r.get("title", ""), "cat": r.get("cat"),
                    "desc": r.get("desc", ""), "time": r.get("time", ""),
                    "makes": r.get("makes", ""),
                    "effort": r.get("effort", ""), "ing": r.get("ing", ""),
                    "steps": r.get("steps", ""), "photo": r.get("photo", "")})
        return out

    @staticmethod
    def _as_list(v):
        """Whatever a store section is, as a list of records.

        A section stored as an object (keyed by title or id) still holds the
        user's recipes in its values, and one stored as a scalar holds nothing.
        Both used to be fatal: iterating a number raised straight out of
        _load_state's except, the library opened empty, and the close-time
        _save_state wrote that emptiness over every recipe in the file."""
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return list(v.values())
        return []

    @staticmethod
    def _as_text(v):
        """One loaded field as the string the editor expects. Ingredients and
        method are multi-line text; a file that stored them as a list of lines
        is still the user's recipe, so join it rather than silently dropping
        the only copy of how the dish is made."""
        if isinstance(v, str):
            return v
        if isinstance(v, (list, tuple)):
            return "\n".join(Cookbook._as_text(x) for x in v)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        return ""

    def _apply_data(self, data):
        """Replace the in-memory model from a parsed autosave dict. Every loaded
        recipe is normalised through the add-path dict shape so all render/edit
        paths find their keys. Callers are responsible for rebuilding the UI.

        Never raises and never gives up wholesale: one unreadable recipe costs
        itself, not the cookbook."""
        self._extra = {k: v for k, v in data.items()
                       if k not in ("cats", "active_cat", "sel", "recipes")}
        cats = []
        for c in self._as_list(data.get("cats")):
            c = self._as_text(c).strip()
            if c and c not in cats:
                cats.append(c)
        recipes = []
        for r in self._as_list(data.get("recipes")):
            if not isinstance(r, dict):
                continue
            fields = {}
            for k in ("title", "desc", "time", "makes", "effort",
                      "ing", "steps", "photo"):
                if k in r:
                    fields[k] = self._as_text(r.get(k))
            cat = r.get("cat")
            cat = self._as_text(cat).strip() if not isinstance(cat, str) \
                else cat.strip()
            fields["cat"] = cat or None
            recipe = self._make_recipe(**fields)
            known = {"title", "cat", "desc", "time", "makes", "effort",
                     "ing", "steps", "photo"}
            recipe.update({k: copy.deepcopy(v) for k, v in r.items()
                           if k not in known})
            recipes.append(recipe)
            # A recipe filed under a category the list has lost keeps its
            # category and gets the chip back, rather than being quietly
            # unfiled: the user typed that name, and re-pointing beats dropping.
            if fields["cat"] and fields["cat"] not in cats:
                cats.append(fields["cat"])
        self.cats = cats
        self.recipes = recipes
        active = data.get("active_cat", 0)
        self.active_cat = active if (isinstance(active, int)
                                     and not isinstance(active, bool)
                                     and 0 <= active <= len(cats)) else 0
        sel = data.get("sel", -1)
        self.sel = sel if (isinstance(sel, int) and 0 <= sel < len(recipes)) \
            else (0 if recipes else -1)

    def _load_state(self):
        """Restore the session-recovery cookbook from disk. On a missing file
        (first run) or any read/parse error, leave the model empty so the app
        opens on its 'No recipes' empty state (ships-empty rule).

        A file that parses but is not shaped like this app's store is NOT
        written off: _save_state rewrites the whole library on close, so
        anything this loader shrugs off is destroyed a moment later. A bare
        list is read as the recipe list it plainly is, and _apply_data takes
        the rest one record at a time."""
        try:
            data = _read_cookbook_json()
        except CookbookStoreTooLarge:
            # Valid JSON that exceeds the launch budget would not be recognized
            # as damaged by the shared writer. Mark it for the same protective
            # move used by other valid-but-unrecognized cookbook shapes.
            self._quarantine_pending = True
            return
        except Exception:
            # First run (no file) or unreadable data -> empty library. An
            # unparseable file is quarantined by nbapp.atomic_write_json before
            # the next save replaces it, so its bytes are never lost.
            return
        if isinstance(data, list):
            data = {"recipes": data}
        elif not isinstance(data, dict):
            return
        elif not self._as_list(data.get("recipes")):
            # The wrapper key is gone or was written under another name. The
            # recipes are still in the file; take the first list of records
            # rather than opening on "No recipes" and writing that empty
            # library straight over them on close. Journal and Contacts have
            # read their stores this way for two rounds; Cookbook was the one
            # store left where a renamed wrapper cost every recipe in it.
            for v in data.values():
                recs = self._as_list(v)
                if recs and any(isinstance(x, dict) and x.get("title")
                                for x in recs):
                    data = dict(data, recipes=recs)
                    break
        try:
            self._apply_data(data)
        except Exception:
            # Belt and braces: a surprise in one record must not leave the
            # library half-loaded and then saved back over the real file.
            self.cats, self.recipes = [], []
            self.active_cat, self.sel = 0, -1
        # LAST RESORT. If this file plainly holds records and we adopted none of
        # them, the next _save_state would write an empty library over it and
        # the user's only copy would be gone. Valid JSON of the wrong shape
        # parses perfectly, so nbapp's generic quarantine cannot see it — only
        # this app knows the shape is not a cookbook. Move it aside on the way
        # past instead (see _save_state), the way accounting.py does.
        if not self.recipes and (_holds_records(data)
                                 or _not_a_cookbook(data)):
            self._quarantine_pending = True

    def _save_state(self):
        """Persist the session-recovery cookbook. Never raises — a failed write
        must not crash the app. Returns True only once the bytes have reached
        the file."""
        try:
            # A store that PARSED but was not a cookbook is moved aside here,
            # immediately before the write that would otherwise replace it — the
            # same moment nbapp picks for the files it can detect, so there is
            # never a window in which the library has no file at all.
            if getattr(self, "_quarantine_pending", False):
                if not _quarantine(COOKBOOK_FILE):
                    raise OSError("could not preserve the unrecognized cookbook")
                self._quarantine_pending = False
            nbapp.atomic_write_json(COOKBOOK_FILE, self._serialize())
            self._save_warned = False
            self._recovery_dirty = False
            self._last_store_error = None
            # The one moment a save time is true: the bytes are in the file.
            self._saved_at = time.strftime("%H:%M")
            self._save_failed = False
            return True
        except Exception as exc:
            self._last_store_error = exc
            self._save_failed = True
            # See academics._save_to_disk. Most callers here ignore the return
            # value, so without this a full disk or a read-only filesystem is
            # indistinguishable from the app eating the library: the file keeps
            # the last write that worked and every recipe written after it
            # vanishes on close. Warn once per run of failures.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash_status(
                        nbapp.save_failure_reason(exc, COOKBOOK_FILE))
                except Exception:
                    pass
            return False

    # ------------------------------------------------- PDF export (File menu)
    # cookbook.json stays the sole source of truth (autosaved on every edit).
    # The File menu offers a one-way render of the CURRENT recipe — its
    # category eyebrow, title, description, the time/makes/effort meta and the
    # Ingredients + Method sections — to a paginated PDF under $NB_HOME/
    # Documents. There is no file open / save.
    FLASH_SECONDS = 5.0

    def _flash_status(self, text, ok=False):
        """Surface a transient status line in the recipe page's save indicator,
        matching the save chip's dot styling — a muted green dot for success,
        signage-red for a problem (crash-safe).

        It HOLDS the chip for a few seconds. This label is the only feedback a
        no-confirm delete gives, and the autosave that the same delete started
        used to overwrite it with "Saved 14:12" after 0.78 seconds — long enough
        to see something appear, not long enough to read that Ctrl+Z brings the
        recipe back."""
        color = "#7FA98C" if ok else "#C8341E"
        try:
            self._dot(color, text)
            self._flash_until = time.monotonic() + self.FLASH_SECONDS
            if self._flash_timer:
                GLib.source_remove(self._flash_timer)
            self._flash_timer = GLib.timeout_add(
                int(self.FLASH_SECONDS * 1000), self._flash_expired)
        except Exception:
            pass

    def _flash_expired(self):
        self._flash_timer = None
        self._flash_until = 0.0
        self._show_save_state()
        return False

    def _clear_flash(self):
        """Give the chip back to the document (the user has moved on)."""
        self._flash_until = 0.0
        if self._flash_timer:
            try:
                GLib.source_remove(self._flash_timer)
            except Exception:
                pass
            self._flash_timer = None

    def _pdf_name(self, r):
        """A neutral PDF filename derived from the recipe title."""
        raw = r.get("title", "") or "recipe"
        words = "".join(c if c.isalnum() else " " for c in raw).split()
        base = "-".join(words).lower()[:70] if words else "recipe"
        return base + ".pdf"

    def _export_pdf(self, *_a):
        """Render the current recipe to a PDF under $NB_HOME/Documents. Reports
        a neutral status line in the save indicator; never crashes on a bad
        path/write. cookbook.json remains the sole source of truth."""
        r = self._cur()
        if r is None:
            self._flash_status("No recipe to export")
            return
        name = self._pdf_name(r)
        # The name comes from the recipe title, so re-exporting after an edit —
        # the usual reason to export twice — lands on the earlier PDF. Ask, using the same three strings as
        # Novel's Save As -- one wording for "you are about to overwrite",
        # already carried by all seventeen catalogs.
        if os.path.exists(os.path.join(DOCUMENTS, name)):
            self._confirm(
                _t("Replace file?"),
                _t("“%s” already exists in Documents. Replace it?")
                % name,
                _t("Replace"), lambda: self._write_export_pdf(name, r))
            return
        self._write_export_pdf(name, r)

    def _write_export_pdf(self, name, r):
        """Render recipe `r` to Documents/`name`. Split from _export_pdf so the
        replace-an-existing-file question can be answered before anything is
        written.

        Rendered BESIDE the destination and moved into place only once it is
        complete. Writing straight onto the path meant a render that failed
        part-way had already truncated whatever was there — and the common
        reason to export twice is that the recipe changed, so the file being
        destroyed was the user's previous good PDF, immediately after they
        answered "Replace".

        Through nbapp.atomic_write_via rather than a private copy of the
        dance: Writer, Accounting and Contacts had the same defect, and five
        apps each hand-rolling temp+replace is how one of them ends up subtly
        different from the rest."""
        try:
            os.makedirs(DOCUMENTS, exist_ok=True)
            nbapp.atomic_write_via(os.path.join(DOCUMENTS, name),
                                   lambda draft: self._render_pdf(draft, r))
        except Exception:
            self._flash_status("Export failed")
            return
        # Success: the autosave state is unchanged, so only update the label.
        # Name the destination folder so the file is findable in the Finder.
        self._flash_status("Exported to Documents", ok=True)

    def _print_recipe(self, *_a):
        """Print the current recipe via the shared themed Print dialog. Reuses
        the exact same renderer as Export to PDF, so the printed page matches
        the exported file byte-for-byte. The no-printer case is handled inside
        nbprint (it reports 'no printer' in the dialog)."""
        r = self._cur()
        if r is None:
            self._flash_status("No recipe to print")
            return

        def make_pdf(path):
            # Bind the recipe at dialog time so a later selection change can't
            # print a different recipe than the one the user was looking at.
            self._render_pdf(path, r)

        try:
            nbprint.print_document(self, make_pdf, job_name="Recipe")
        except Exception:
            # A dialog/spooler failure must never crash the app.
            self._flash_status("Print failed")

    def _render_pdf(self, path, r):
        """Draw recipe `r` onto a cairo PDF at `path`, paginating when the
        cursor overflows the page. Serif body + ink palette to match the
        recipe page."""
        PW, PH = 612.0, 792.0            # US Letter, points
        ML, MR, MT, MB = 64.0, 64.0, 72.0, 64.0
        text_w = PW - ML - MR
        surf = cairo.PDFSurface(path, PW, PH)
        cr = cairo.Context(surf)

        # Laid out with nbprint.PdfText (PangoCairo). The private wrap()/emit()
        # this replaces used cairo's TOY font API, which binds one FreeType face
        # and does no per-character fallback: a recipe titled or written in
        # Japanese, Chinese, Korean, Hindi or Yiddish printed as a page of empty
        # .notdef boxes, and the line wrapping was measured against those boxes
        # too. Pango picks a face per glyph, so the same recipe prints in its own
        # script. (journal.py and Academics' reports moved for the same reason.)
        pt = nbprint.PdfText(surf, cr, ML, MT, PH - MB, text_w)
        emit = pt.emit

        def ink(hexc):
            rr, gg, bb = nbicons._hex(hexc)
            cr.set_source_rgb(rr, gg, bb)

        # Header: category eyebrow, title, description, meta, then a hairline.
        emit((r.get("cat") or _t("No category")).upper(), 9.5, False,
             "#6E695E", gap_after=6)
        emit(r.get("title", "") or _t("Untitled recipe"), 26, True, "#1A1916",
             gap_after=3)
        desc = (r.get("desc") or "").strip()
        if desc:
            emit(desc, 12, False, "#6E695E", italic=True, gap_after=4)
        meta_bits = []
        for cap, key in (("Time", "time"), ("Makes", "makes"),
                         ("Effort", "effort")):
            v = (r.get(key) or "").strip()
            if v:
                meta_bits.append("%s: %s" % (cap, v))
        if meta_bits:
            emit("      ".join(meta_bits), 10, False, "#9A9484", gap_after=6)
        if pt.y + 1 <= PH - MB:
            ink("#D7D2C5")
            cr.set_line_width(1.0)
            cr.move_to(ML, pt.y)
            cr.line_to(PW - MR, pt.y)
            cr.stroke()
        pt.y += 18

        # Ingredients: one row per non-empty line; normalise the em-dash
        # 'name — amount' separator the editor uses.
        emit("INGREDIENTS", 11, True, "#6E695E", gap_after=6)
        ing = [ln.strip() for ln in (r.get("ing") or "").split("\n")
               if ln.strip()]
        if ing:
            for ln in ing:
                nm, amount = self._split_ing(ln)
                line = "%s — %s" % (nm, amount) if amount else nm
                emit(line, 11, False, "#2A2620", gap_after=2)
        else:
            emit("No ingredients", 11, False, "#9A9484", italic=True)

        # Method: each non-empty line becomes a numbered step.
        emit("METHOD", 11, True, "#6E695E", gap_before=16, gap_after=6)
        steps = [ln.strip() for ln in (r.get("steps") or "").split("\n")
                 if ln.strip()]
        if steps:
            for i, ln in enumerate(steps):
                emit("%d.  %s" % (i + 1, ln), 11, False, "#2A2620",
                     gap_after=4)
        else:
            emit("No method", 11, False, "#9A9484", italic=True)

        surf.finish()

    def _on_destroy(self, *_):
        # Final flush on window close so the last (possibly still-debounced)
        # edit is written before we exit.
        if self._closed:
            return False
        self._closed = True
        for attr in ("_save_timer", "_flash_timer", "_ing_w_idle"):
            source = getattr(self, attr, None)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._save_state()
        return False

    def _on_delete(self, *_):
        """Veto close while this window holds the only updated cookbook -- and
        say why, with a way out (nbapp.close_unsaved_card). A bare veto was a
        window that could not be closed on a full disk."""
        if not getattr(self, "_recovery_dirty", False):
            return False
        if self._save_state():
            return False
        return not nbapp.close_unsaved_card(
            self, getattr(self, "_last_store_error", None), COOKBOOK_FILE)

    def _dialog_escape(self, dlg):
        """Wire Esc to dismiss a decorationless modal dialog. These dialogs are
        separate windows with their own key focus, so the app-window Esc handler
        never sees their events — without this, Esc would be dead in the dialog
        and a novice's instinct to press it to back out would do nothing."""
        dlg.connect(
            "key-press-event",
            lambda _w, e: (dlg.destroy() or True)
            if e.keyval == Gdk.KEY_Escape else False)

    def _new_category(self, assign=False):
        """Make a category. With `assign`, the recipe on screen is filed under
        it — that is what asking for a new category from the recipe's own
        category control means."""
        dlg = Gtk.Dialog(title="New Category", transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("catdlg")
        area = dlg.get_content_area()
        area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.get_style_context().add_class("catdlgbox")
        hd = Gtk.Label(label=_t("New Category"), xalign=0)
        hd.get_style_context().add_class("catdlgtitle")
        entry = Gtk.Entry()
        entry.set_placeholder_text(_t("Category name"))
        entry.get_style_context().add_class("catdlgentry")
        # Add used to close the dialog whatever was typed: a blank name or one
        # that already exists left the chips exactly as they were, with no
        # message anywhere — the same gesture as a successful add, and nothing
        # to tell them apart. The dialog now stays open and says which it is.
        err = Gtk.Label(label="", xalign=0)
        err.get_style_context().add_class("catdlgerr")
        err.set_line_wrap(True)
        err.set_max_width_chars(34)
        err.set_no_show_all(True)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("dlgcancel")
        ok = Gtk.Button(label=_t("Add"))
        ok.get_style_context().add_class("dlgprimary")
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        box.pack_start(hd, False, False, 0)
        box.pack_start(entry, False, False, 0)
        box.pack_start(err, False, False, 0)
        box.pack_start(btns, False, False, 0)
        area.add(box)

        def refuse(message):
            err.set_text(message)
            err.show()
            entry.get_style_context().add_class("catdlgentryerr")
            entry.grab_focus()

        def commit(*_):
            v = entry.get_text().strip()
            if not v:
                refuse(_t("Enter a category name."))
                return
            if v in self.cats:
                refuse(_t("A category with that name already exists."))
                return
            self.undo.checkpoint("New Category")
            self.cats.append(v)
            r = self._cur() if assign else None
            if r is not None:
                # Asked for from the recipe's own category control: file it.
                r["cat"] = v
                self.active_cat = len(self.cats)
            elif self._cur() is None:
                # Nothing open, so show what was just made.
                self.active_cat = len(self.cats)
                self.sel = -1
            # ...and otherwise leave the page alone. Adding a category used to
            # switch the filter to the new (empty) one and deselect the recipe,
            # so making a category while writing a recipe threw the recipe off
            # the screen.
            self.rebuild_chips()
            self.rebuild_list()
            self._refresh_editor()
            self._touch()
            self.undo.commit()
            dlg.destroy()

        cancel.connect("clicked", lambda *_: dlg.destroy())
        ok.connect("clicked", commit)
        entry.connect("activate", commit)
        self._dialog_escape(dlg)
        dlg.show_all()
        entry.grab_focus()

    def _delete_active_category(self):
        """Remove the currently-filtered category (a specific chip must be
        active — 'All' is not a category). Recipes are not deleted."""
        if self.active_cat <= 0:
            return
        ci = self.active_cat - 1
        if not (0 <= ci < len(self.cats)):
            return
        name = self.cats[ci]
        self._remove_category(ci)
        # The same sentence a deleted recipe gets. Deleting a category used to
        # say nothing at all: the chip vanished, its recipes quietly became
        # "No category", and the only clue that any of it could be taken back
        # was knowing Ctrl+Z exists.
        self._flash_status(_t("Deleted “%s” — Ctrl+Z to undo") % name)

    def _remove_category(self, ci):
        """Delete category index `ci`, reassigning its recipes to No category
        (cat=None) so nothing is lost, then fix up the active filter."""
        if not (0 <= ci < len(self.cats)):
            return
        self.undo.checkpoint("Delete Category")
        name = self.cats[ci]
        for r in self.recipes:
            if r.get("cat") == name:
                r["cat"] = None
        del self.cats[ci]
        # Keep the active filter pointing at a valid chip: clear to All if the
        # deleted category was active, else shift down if it sat after it.
        if self.active_cat == ci + 1:
            self.active_cat = 0
        elif self.active_cat > ci + 1:
            self.active_cat -= 1
        self.rebuild_chips()
        self.rebuild_list()
        self._refresh_editor()
        self._touch()
        self.undo.commit()

    def _confirm(self, title, message, ok_label, on_yes):
        """Modal confirmation for a destructive action (mirrors the New Category
        dialog). Runs `on_yes` only when the primary button is pressed."""
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("catdlg")
        area = dlg.get_content_area()
        area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.get_style_context().add_class("catdlgbox")
        hd = Gtk.Label(label=title, xalign=0)
        hd.get_style_context().add_class("catdlgtitle")
        msg = Gtk.Label(label=message, xalign=0)
        msg.set_line_wrap(True)
        msg.set_line_wrap_mode(Pango.WrapMode.WORD)
        msg.set_max_width_chars(40)
        msg.get_style_context().add_class("catdlgmsg")
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("dlgcancel")
        ok = Gtk.Button(label=ok_label)
        ok.get_style_context().add_class("dlgok")
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        box.pack_start(hd, False, False, 0)
        box.pack_start(msg, False, False, 0)
        box.pack_start(btns, False, False, 0)
        area.add(box)
        cancel.connect("clicked", lambda *_: dlg.destroy())
        ok.connect("clicked", lambda *_: (dlg.destroy(), on_yes()))
        self._dialog_escape(dlg)
        dlg.show_all()
        # focus the safe default so a stray Space/Return cancels, not deletes
        cancel.grab_focus()

    def menu_items(self, name):
        if name == "Edit":
            # Visible, not just bound to a key nobody can discover —
            # the same two lines Journal, Novel and Screenplay show.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit")
        if name == "File":
            # cookbook.json is the sole source of truth (autosaved on every
            # edit). File offers only the in-memory New Recipe action plus a
            # one-way render of the current recipe to a PDF under
            # $NB_HOME/Documents — no file open / save / save-as. Export asks
            # nothing and writes the file straight away, so it takes NO
            # ellipsis; Print opens the printer dialog, so it does. Both need a
            # recipe to render and grey out without one, where they used to look
            # live and only flash "No recipe to export".
            has = self._cur() is not None
            return [("New Recipe", self.new_recipe),
                    nbapp.SEP,
                    ("Export to PDF", self._export_pdf if has else None),
                    ("Print…", self._print_recipe if has else None),
                    nbapp.SEP,
                    ("Close    Esc", self.close)]
        if name == "View":
            # the search, the category filters (mirroring the sidebar chips)
            # and list navigation
            items = [("Search Recipes    Ctrl+F",
                      self._focus_search if self._can_search() else None),
                     # Live whenever there is something to clear, which is
                     # the same pair of conditions _clear_search itself tests.
                     # `self.query` is the box STRIPPED, so a box holding only
                     # spaces greyed this item out beside text a person can see
                     # and Clear would remove -- and the callback's own guard
                     # then had to refuse in silence for a case the menu said
                     # could not happen.
                     ("Clear Search",
                      (lambda: self._clear_search())
                      if self.query or self.search.get_text() else None),
                     nbapp.SEP]
            items += [(("•  " if self.active_cat == 0 else "     ") + "All Recipes",
                       lambda: self._on_chip(None, 0))]
            for i, c in enumerate(self.cats):
                items.append(
                    (("•  " if self.active_cat == i + 1 else "     ") + c,
                     lambda idx=i + 1: self._on_chip(None, idx)))
            items += [nbapp.SEP,
                      ("Next Recipe", lambda: self._select_relative(1)),
                      ("Previous Recipe", lambda: self._select_relative(-1))]
            return items
        if name == "Cook":
            has = self._cur() is not None
            has_cat = self.active_cat > 0
            # Title Case, like every other menu item in the OS — this one was
            # the odd sentence-case label in the bar.
            return [("Start Cooking", (lambda: self._enter_cook())
                     if has else None),
                    ("Move to Category…",
                     (lambda: self._on_kicker_clicked(self.kicker_btn))
                     if has else None),
                    nbapp.SEP,
                    ("New Recipe", lambda: self.new_recipe()),
                    ("New Category…", lambda: self._new_category()),
                    # No ellipsis on either Delete: both act at once and are
                    # taken back with Ctrl+Z, and rule 1 of the menu
                    # conventions makes "…" a promise that something will be
                    # asked first. The confirm they once had was removed on
                    # purpose (see _confirm_delete_current) — the labels were
                    # left behind still promising it.
                    ("Delete Category",
                     (lambda: self._delete_active_category())
                     if has_cat else None),
                    nbapp.SEP,
                    ("Duplicate Recipe",
                     (lambda: self._duplicate_current()) if has else None),
                    ("Delete Recipe",
                     (lambda: self._confirm_delete_current()) if has else None)]
        return super().menu_items(name)

    def _cat_indices(self):
        """Recipe indices the active CATEGORY shows, search set aside."""
        cat_filter = self.cats[self.active_cat - 1] if self.active_cat > 0 else None
        return [i for i, r in enumerate(self.recipes)
                if cat_filter is None or r.get("cat") == cat_filter]

    def _visible_indices(self):
        """Recipe indices currently listed: the active category, narrowed by
        whatever is in the search box."""
        q = (self.query or "").strip().lower()
        idx = self._cat_indices()
        if not q:
            return idx
        return [i for i in idx if self._matches(self.recipes[i], q)]

    def _select_relative(self, delta):
        vis = self._visible_indices()
        if not vis:
            return
        if self.sel in vis:
            pos = min(max(vis.index(self.sel) + delta, 0), len(vis) - 1)
        else:
            pos = 0 if delta >= 0 else len(vis) - 1
        self.sel = vis[pos]
        self.rebuild_list()
        self._refresh_editor()

    def _duplicate_current(self):
        r = self._cur()
        if r is None:
            return
        self.undo.checkpoint("Duplicate Recipe")
        copy = dict(r)
        copy["title"] = (r.get("title") or _t("Untitled recipe")) + _t(" (copy)")
        self.recipes.insert(self.sel + 1, copy)
        self.sel += 1
        self.rebuild_list()
        self._refresh_editor()
        self._touch()
        self.undo.commit()

    def _confirm_delete_current(self):
        """Delete the current recipe. No confirm: it is undoable now.

        This used to open a dialog whose own sentence read "This cannot be
        undone", which is exactly what changed. Friction belongs to
        commitment, never to mechanism -- so the delete happens at once and
        Ctrl+Z brings it back, which is both faster for the common case and
        safer for the mistaken one. The name is kept so the menu and every
        other call site are unchanged."""
        if self._cur() is None:
            return
        title = self._cur().get("title") or _t("Untitled recipe")
        self._delete_current()
        self._flash_status(_t("Deleted “%s” — Ctrl+Z to undo") % title)

    def _delete_current(self):
        if not (0 <= self.sel < len(self.recipes)):
            return
        self.undo.checkpoint("Delete Recipe")
        # Remember the deleted recipe's position within the *filtered* view so
        # its neighbour is picked from the same category, not by global index.
        vis_before = self._visible_indices()
        pos = vis_before.index(self.sel) if self.sel in vis_before else 0
        del self.recipes[self.sel]
        # Pick the next selection from the currently-filtered set so the active
        # category filter is honoured. The old code used a global index, which
        # could land on a recipe hidden by the filter — or blank the sidebar
        # while recipes still existed in other categories.
        vis = self._visible_indices()
        if vis:
            self.sel = vis[min(pos, len(vis) - 1)]
        elif self.recipes:
            # Deleted the last recipe in this category: fall back to "All" so
            # the remaining recipes stay visible instead of a misleading empty
            # sidebar, then select the first of them.
            self.active_cat = 0
            self.sel = 0
            self.rebuild_chips()
        else:
            self.sel = -1
        self.rebuild_list()
        self._refresh_editor()
        self._touch()
        self.undo.commit()

    # ---------------------------------------------------------------------- css
    def _install_css(self):
        css = b"""
        .sidebar { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        .chipwrap { padding: 20px 20px 16px; border-bottom: 1px solid #D7D2C5; }
        .chipflow { background: transparent; }
        .chip { min-height: 28px; padding: 0 13px; border-radius: 8px;
                font-size: 13px; font-weight: 500; border: 1px solid #D7D2C5;
                background: #FCFBF8; color: #3A362E; box-shadow: none; }
        .chip:hover { background: #EFEBE0; }
        .chip.active { background: #EAE3D2; color: #B12D19; font-weight: 600;
                       border: 1px solid #C9C4B6; }
        .chip.active:hover { background: #EAE3D2; }
        .chipadd { min-height: 28px; padding: 0 12px; border-radius: 8px;
                   font-size: 13px; color: #8A857A; background: transparent;
                   border: 1px dashed #B3AD9E; box-shadow: none; }
        .chipadd:hover { background: #EAE3D2; }
        /* The theme's `* { color: ink }` lands straight on a button's label
           node, so a colour set on the button alone never reaches its text:
           every button whose label is not ink has to name the label as well. */
        .chip label { color: #3A362E; }
        .chip.active label { color: #B12D19; font-weight: 600; }
        .chipadd label { color: #6E695E; }

        .cbsearch { margin-top: 14px; font-size: 13px; color: #1A1916;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; box-shadow: none; min-height: 30px; }
        .cbsearch:focus { border: 1px solid #8A857A; }

        .recipelist { background: #F1EEE6; padding: 12px; }
        .recipelist row { padding: 0; }
        .reciperow { padding: 12px 12px; border-radius: 6px; margin-bottom: 2px;
                     border-left: 3px solid transparent; }
        .reciperow:hover { background: #F0EADC; }
        .reciperow.selected { background: #EAE3D2; border-left: 3px solid #C8341E; }
        .ricon { margin-top: 2px; }
        .rtitle { font-family: "Newsreader","Liberation Serif",serif;
                  font-size: 16px; color: #1A1916; }
        .rmeta { font-size: 11px; letter-spacing: 0.3px; color: #6E695E; }
        .emptylistbox { padding: 34px 12px; }
        /* No tracking here. 11px + ~0.18em is this OS's UPPERCASE eyebrow
           style ("CLASSES", "TRANSITIONS"); applied to a sentence-case line
           it renders as "N o   r e c i p e s" -- spaced-out text reads as a
           rendering fault. Music's "No playlists" sidebar note, the same
           pattern one app over, is untracked too. */
        .emptylist { font-size: 11px; color: #6E695E; font-weight: 600; }

        .sidefoot { border-top: 1px solid #D7D2C5; padding: 14px 18px; }
        .newrecipe { min-height: 40px; border: 1px solid #C9C4B6; border-radius: 8px;
                     background: #FCFBF8; font-size: 14px; font-weight: 600;
                     color: #1A1916; box-shadow: none; }
        .newrecipe:hover { background: #EFEBE0; }

        .mainpane { background: #FCFBF8; }
        .emptytitle { font-size: 15px; color: #6E695E; }
        .emptyhint { font-size: 13px; color: #6E695E; }

        .edhead { padding: 20px 72px 6px; }
        .edkicker { font-family: "Nimbus Sans","Helvetica",sans-serif;
                    font-size: 11px; letter-spacing: 2px; color: #9A9484;
                    font-weight: 600; }
        .kickerbtn { padding: 0; margin: 0; min-height: 0; border: none;
                     background: transparent; background-image: none;
                     box-shadow: none; }
        .kickerbtn:hover label { color: #1A1916; }
        .savestate { font-family: "Nimbus Sans","Helvetica",sans-serif;
                     font-size: 12px; color: #6E695E; }
        .edtitle { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 40px; color: #1A1916; background: transparent;
                   border: none; padding: 0; margin-top: 8px; }
        .eddesc { font-family: "Newsreader","Liberation Serif",serif;
                  font-style: italic; font-size: 17px; color: #6E695E;
                  background: transparent; border: none; padding: 0;
                  margin-top: 6px; }

        /* The read side of a header field: the label carries all the type, so
           the button around it must add nothing at all. */
        .readfield { padding: 0; margin: 0; min-height: 0; border: none;
                     background: transparent; background-image: none;
                     box-shadow: none; }
        /* muted-3, not muted-2. These stand in for the recipe's own
           title / description / meta value on the read side, inside a flat
           .readfield BUTTON, so a person reads them and clicks them -- and
           muted-2 measures 2.92:1 on paper, under the 3:1 floor
           (button_contrast_check). One step down the same ink ramp is 3.55:1
           and still clearly quieter than the real value (ink for the title and
           the meta cells, muted for the description), so the placeholder still
           reads AS a placeholder. Both tones map to _HC_QUIET in nbapp's
           high-contrast table, so the boost survives the change. */
        .edtitle.placeholder, .eddesc.placeholder,
        .metaval.placeholder { color: #8A857A; }

        .metabar { margin-top: 20px; border: 1px solid #D7D2C5; border-radius: 8px;
                   background: #FCFBF8; }
        .metacell { padding: 9px 20px 10px; }
        .metadiv { border-left: 1px solid #D7D2C5; }
        .metacap { font-family: "Nimbus Sans","Helvetica",sans-serif;
                   font-size: 10px; letter-spacing: 1.5px; color: #6E695E;
                   font-weight: 600; }
        .metaval { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 16px; color: #1A1916; background: transparent;
                   border: none; padding: 0; min-height: 0; }

        .edcols { padding: 26px 72px 40px; }
        .edpanel { background: transparent; border: none; }
        .edpanelhd { padding: 0 0 8px; border-bottom: 1px solid #C9C4B6; }
        .edpanelhdtext { font-family: "Nimbus Sans","Helvetica",sans-serif;
                         font-size: 11px; letter-spacing: 2px; color: #6E695E;
                         font-weight: 600; }
        .paneledit { min-height: 0; padding: 0 2px; background: transparent;
                     border: none; box-shadow: none;
                     font-family: "Nimbus Sans","Helvetica",sans-serif;
                     font-size: 11px; letter-spacing: 1px; color: #9A9484; }
        .paneledit label { color: #6E695E; letter-spacing: 1px; }
        .paneledit:hover, .paneledit:hover label { color: #1A1916; }
        .edbox { font-family: "Newsreader","Liberation Serif",serif;
                 font-size: 16px; color: #1A1916; background: transparent;
                 padding: 16px 0 0; caret-color: #C8341E; }
        .edbox text { background: transparent; }
        .edbox text selection { background-color: #EAE3D2; color: #1A1916; }
        .edbox.placeholder text { color: #8A857A; }   /* muted-3: see above */

        .renderlist { padding: 16px 0 0; background: transparent; }
        /* Same placeholder tier, and the same defect shape -- a LABEL
           inside a flat button (.renderphbtn). It is only reachable once a
           recipe is open, so a construct-time gate never sees it. */
        .renderph { font-family: "Newsreader","Liberation Serif",serif;
                    font-size: 16px; color: #6E695E; }
        .renderphbtn { padding: 0; border: none; background: transparent;
                       background-image: none; box-shadow: none; }
        .ingrow { padding: 6px 0; }
        .ingname { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 16px; color: #1A1916; }
        .ingamt { font-family: "Nimbus Sans","Helvetica",sans-serif;
                  font-size: 12px; letter-spacing: 0.5px; color: #6E695E; }
        .steprow { padding: 3px 0 15px; }
        .stepnum { min-width: 26px; min-height: 26px;
                   border: 1px solid #C9C4B6; border-radius: 100px;
                   color: #6E695E;
                   font-family: "Nimbus Sans","Helvetica",sans-serif;
                   font-size: 12px; }
        .steptext { font-family: "Newsreader","Liberation Serif",serif;
                    font-size: 16px; color: #2A2620; }

        /* Cook mode: one step at a time, in the largest type in the OS, with
           targets you can hit without looking. Same papertone surfaces, no new
           colours -- the only accent is the one primary button. */
        .cookpage { background: #FCFBF8; }
        .cookhead { padding: 20px 36px 18px; border-bottom: 1px solid #D7D2C5;
                    background: #FCFBF8; }
        .cooktitle { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 30px; color: #1A1916; }
        .scaler { border: 1px solid #C9C4B6; border-radius: 8px;
                  background: #FCFBF8; }
        .scalebtn { min-width: 38px; min-height: 38px; padding: 0;
                    background: transparent; border: none; box-shadow: none;
                    font-size: 20px; color: #3A362E; }
        .scalebtn label { color: #3A362E; }
        .scalebtn:hover { background: #EFEBE0; }
        .scaleval { font-family: "Nimbus Sans","Helvetica",sans-serif;
                    font-size: 14px; font-weight: 600; color: #1A1916;
                    padding: 0 14px; }
        .cookdone { min-height: 40px; padding: 0 20px; border-radius: 8px;
                    border: 1px solid #C9C4B6; background: #FCFBF8;
                    box-shadow: none; font-size: 14px; color: #3A362E; }
        .cookdone label { color: #3A362E; }
        .cookdone:hover { background: #EFEBE0; }
        .cookings { background: #F8F7F2; border-right: 1px solid #D7D2C5;
                    padding: 24px 26px 24px 36px; }
        .cookinghd { font-family: "Nimbus Sans","Helvetica",sans-serif;
                     font-size: 11px; letter-spacing: 2px; color: #6E695E;
                     font-weight: 600; padding-bottom: 12px;
                     border-bottom: 1px solid #C9C4B6; }
        .cookingrow { padding: 9px 0; border-bottom: 1px solid #D7D2C5; }
        .cookingname { font-family: "Newsreader","Liberation Serif",serif;
                       font-size: 17px; color: #1A1916; }
        .cookingamt { font-family: "Nimbus Sans","Helvetica",sans-serif;
                      font-size: 14px; font-weight: 600; color: #3A362E; }
        .cookingempty { font-family: "Newsreader","Liberation Serif",serif;
                        font-size: 16px; color: #6E695E; padding-top: 14px; }
        .cooksteps { padding: 24px 36px 26px; }
        .cookpos { font-family: "Nimbus Sans","Helvetica",sans-serif;
                   font-size: 11px; letter-spacing: 2px; color: #9A9484;
                   font-weight: 600; padding-bottom: 18px; }
        .cooksteptext { font-family: "Newsreader","Liberation Serif",serif;
                        font-size: 24px; color: #1A1916; }
        .cookpeek { font-family: "Nimbus Sans","Helvetica",sans-serif;
                    font-size: 13px; color: #9A9484; padding-top: 26px; }
        .cooknav { min-height: 48px; padding: 0 26px; border-radius: 8px;
                   border: 1px solid #C9C4B6; background: #FCFBF8;
                   box-shadow: none; font-size: 15px; color: #1A1916;
                   margin-top: 18px; }
        .cooknav label { color: #1A1916; }
        .cooknav:hover { background: #EFEBE0; }
        .cooknav:disabled, .cooknav:disabled label { color: #B3AD9E; }
        .cooknext { background: #1A1916; border: 1px solid #1A1916; }
        .cooknext, .cooknext label { color: #FCFBF8; }
        .cooknext:hover { background: #2A2620; }
        .cooknext:disabled { background: #EAE3D2; border-color: #D7D2C5; }
        .cooknext:disabled, .cooknext:disabled label { color: #B3AD9E; }
        .startcook { min-height: 40px; padding: 0 20px; border-radius: 8px;
                     border: 1px solid #C9C4B6; background: #FCFBF8;
                     box-shadow: none; font-size: 14px; font-weight: 600;
                     color: #1A1916; margin-top: 20px; }
        .startcook label { color: #1A1916; font-weight: 600; }
        .startcook:hover { background: #EFEBE0; }

        .catdlg, .catdlgbox { background: #F8F7F2; }
        .catdlgbox { padding: 26px 28px; border: 1px solid #C9C4B6; }
        .catdlgtitle { font-size: 16px; font-weight: 700; color: #1A1916; }
        .catdlgmsg { font-size: 14px; color: #3A362E; }
        .catdlgentry { min-height: 40px; border: 1px solid #C9C4B6; border-radius: 8px;
                       background: #FCFBF8; font-size: 15px; color: #1A1916; }
        .catdlgentryerr { border: 1px solid #C8341E; }
        .catdlgerr { font-size: 13px; color: #C8341E; }
        .dlgcancel { min-height: 38px; padding: 0 18px; border: 1px solid #C9C4B6;
                     color: #3A362E; border-radius: 8px; font-size: 14px;
                     background: #FCFBF8; box-shadow: none; }
        .dlgcancel:hover { background: #EFEBE0; }
        .dlgok { min-height: 38px; padding: 0 18px; background: #C8341E;
                 color: #FCFBF8; border-radius: 8px; font-size: 14px;
                 font-weight: 600; box-shadow: none; border: 1px solid #C8341E; }
        .dlgok:hover { background: #B12D19; border-color: #B12D19; }
        .dlgok label { color: #FCFBF8; font-weight: 600; }
        /* non-destructive primary (Add / Save): dark ink, never signage-red;
           red is reserved for active/alert (the destructive .dlgok). */
        .dlgprimary { min-height: 38px; padding: 0 18px; background: #1A1916;
                      color: #FCFBF8; border-radius: 8px; font-size: 14px;
                      font-weight: 600; box-shadow: none;
                      border: 1px solid #1A1916; }
        .dlgprimary:hover { background: #2A2620; border-color: #2A2620; }
        .dlgprimary label { color: #FCFBF8; font-weight: 600; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # Styling is cosmetic: a CSS parse error or a missing default
            # screen must not stop the app window from constructing.
            pass


if __name__ == "__main__":
    nbapp.run(Cookbook)
