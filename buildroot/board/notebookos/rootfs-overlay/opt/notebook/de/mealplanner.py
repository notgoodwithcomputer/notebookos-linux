#!/usr/bin/env python3
"""
Meal Planner — what you are eating this week, laid out as a week.

Cookbook's sister app: Cookbook is where a recipe lives, this is where it lands
on a day. Three meals down, seven days across, each slot either a recipe you
already have, a takeaway, or a line of your own ("leftovers", "at Mum's").
Takeaway is a first-class choice rather than an omission — a planner that can
only describe cooking is a planner most weeks disagree with.

Today's next meal shows on the desktop board.

A slot stores the recipe's TITLE, not its position in Cookbook. Cookbook
records carry no stable id, so an index would quietly repoint at a different
dish the moment a recipe was added, reordered or deleted — the plan would
change without anyone touching it. A title that no longer matches any recipe
still reads correctly; it simply stops linking.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango           # noqa: E402

import json                                                # noqa: E402
import os                                                  # noqa: E402
import time                                                # noqa: E402
import copy                                                # noqa: E402

import nbapp                                               # noqa: E402
import nbi18n
from nbi18n import _t                                      # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STORE = os.path.join(CFG_DIR, "mealplanner.json")
# Read-only. Cookbook owns this file; the planner never writes it.
COOKBOOK_FILE = os.path.join(CFG_DIR, "cookbook.json")
MAX_STORE_BYTES = 8 * 1024 * 1024


def _set_user_text(label, text, fallback=""):
    """Put a dish the USER named on a label, exactly as they named it.

    A meal slot holds a person's own words — "Takeaway", "Leftovers", or a
    recipe title out of Cookbook. nbi18n looks up every label, so on a French
    install the cell read "À emporter" while mealplanner.json went on saying
    "Takeaway", and the slot links back to Cookbook BY TITLE: the row the plan
    named was no longer the row the cookbook could find."""
    value = str(text or "")
    if value:
        nbi18n.set_verbatim(label, value)
        return
    empty = _t(fallback) if fallback else ""
    try:
        label.set_text(empty)
    except AttributeError:
        label.set_label(empty)


def _set_user_tooltip(widget, text):
    """Hover text carrying the user's own words. set_tooltip_text is patched
    by nbi18n; set_tooltip_markup is not and renders the same once escaped."""
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


def _combo_append_user(combo, text):
    """Add a row the user NAMED to a ComboBoxText verbatim. append_text() is
    patched by nbi18n; append(id, text) is not and fills the same column."""
    value = str(text or "")
    try:
        combo.append(None, value)
    except Exception:
        combo.append_text(value)


class MealStoreTooLarge(ValueError):
    pass


def _read_json_bounded(path, limit=MAX_STORE_BYTES):
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise MealStoreTooLarge("meal data store is too large")
    return json.loads(data)

MEALS = ("breakfast", "lunch", "dinner")
MEAL_NAMES = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"}


def meal_name(meal):
    """The meal's name in the interface language.

    The literals are spelled out inside _t() here on purpose: reached only as
    meal_name(meal) they are invisible to tools/i18n_coverage_check.py,
    which reads the source, so Breakfast / Lunch / Dinner were in NO catalog
    and showed English in all sixteen languages -- in the meal grid, the status
    line and the desktop Meals tile."""
    if meal == "breakfast":
        return _t("Breakfast")
    if meal == "lunch":
        return _t("Lunch")
    if meal == "dinner":
        return _t("Dinner")
    return _t(MEAL_NAMES.get(meal, meal))
# The hour each meal stops being "next". Used only to decide which meal the
# desktop tile should be pointing at.
MEAL_UNTIL = {"breakfast": 11, "lunch": 16, "dinner": 24}

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

KIND_RECIPE, KIND_TAKEOUT, KIND_NOTE = "recipe", "takeout", "note"

# A meal in your own words is a line, not an essay. The box that takes it stops
# at exactly this many characters, so what is typed is what is kept.
MAX_TYPED_TITLE = 80
# A recipe title is COOKBOOK'S, and Cookbook caps nothing. A slot links back to
# its recipe by that title and by nothing else (see the module docstring), so a
# cut copy links to nothing: the chooser could no longer preselect the recipe,
# and the next Save rewrote the meal as a note under a title that stopped
# mid-word. A picked recipe is therefore kept whole; this ceiling only stops a
# hand-edited or damaged store from putting an unbounded string through the
# grid, the status line and the desktop tile.
MAX_RECIPE_TITLE = 400
# The most lines of a dish name one cell will ever show. Seven holds a full
# 80-character typed meal at the width of a cell, and three rows of seven
# still stand in the 740px panel without the week having to scroll. It is
# also what stops a long title from taking the room its OWN height request
# made: each pass measured the cell, grew the label, and so measured a taller
# cell next pass, until one 400-character dish had squeezed the other two
# meals of the day into a strip.
MAX_DISH_LINES = 7


def _cap_title(title, kind):
    """The stored form of a slot title: trimmed, and cut only where a cut
    costs nothing.

    Cutting AFTER trimming and trimming again is what makes the result stable.
    A cut could land on a space, and the next read trimmed that space off, so
    the plan on disk and the plan on screen disagreed by one character from
    the first relaunch onwards."""
    limit = MAX_RECIPE_TITLE if kind == KIND_RECIPE else MAX_TYPED_TITLE
    return title.strip()[:limit].strip()


def _today_key():
    t = time.localtime()
    return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def _date_key(ordinal):
    """A day number back to "YYYY-MM-DD" — the inverse of nbapp.day_ordinal, by
    the same civil-date arithmetic (the stdlib `calendar` is shadowed by the
    Calendar app, so strptime and friends are off limits OS-wide)."""
    z = ordinal + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return "%04d-%02d-%02d" % (y + (1 if m <= 2 else 0), m, d)


def _day_label(day_key):
    """"Thursday 31 December": the weekday a week is read by, and the date
    that says WHICH Thursday. The dialog that edits a slot, and the tooltip
    that names one, both said only "Dinner on Thursday" -- true of one day in
    every week the app can show. Built from parts that are each already in the
    interface language, like the week span over the grid, rather than through
    a sentence no catalog has."""
    ordinal = nbapp.day_ordinal(day_key)
    weekday = _t(DAY_NAMES[(ordinal + 3) % 7])
    _y, month, dnum = _date_key(ordinal).split("-")
    return "%s %s %s" % (weekday, dnum.lstrip("0"), _t(MONTHS[int(month) - 1]))


def _week_start(day_key):
    """The Monday of the week `day_key` falls in."""
    o = nbapp.day_ordinal(day_key)
    if o is None:
        o = nbapp.day_ordinal(_today_key())
    return o - ((o + 3) % 7)          # 1970-01-01 was a Thursday


def _day_map(v, depth=0):
    """The {day: meals} map inside whatever the store holds, or None.

    A bare map, one under the "plan" key, and one under a renamed or extra
    wrapper key are all the same week. Refusing the wrapper used to read as "no
    meals planned", and because ANY subsequent edit rewrites this whole file,
    filling in one slot then replaced the entire plan with that one slot."""
    if not isinstance(v, dict) or depth > 2:
        return None
    if any(isinstance(k, str) and nbapp.day_ordinal(k) is not None for k in v):
        return v
    for inner in v.values():
        found = _day_map(inner, depth + 1)
        if found is not None:
            return found
    return None


def _holds_meals(path=None):
    """True when the store plainly contains slot-shaped records, whether or not
    read_plan managed to make a week out of them. An empty planner is a
    perfectly ordinary state, so the test is the SHAPE of what is in the file,
    never emptiness. Never raises."""
    path = STORE if path is None else path
    try:
        raw = _read_json_bounded(path)
    except MealStoreTooLarge:
        return True
    except (OSError, ValueError):
        return False
    stack, seen = [raw], 0
    while stack and seen < 400:
        seen += 1
        node = stack.pop()
        if isinstance(node, dict):
            title = node.get("title")
            if isinstance(title, str) and title.strip() and "kind" in node:
                return True
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def _quarantine(path):
    """Move a store this app could not make sense of aside, under the same
    <name>.damaged-<timestamp> name nbapp.preserve_damaged uses. True only when
    the original is safely out of the replacement path. Never raises."""
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.exists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
        return True
    except OSError:
        return not os.path.lexists(path)


def read_plan(path=None):
    """The plan as {day: {meal: {kind, title}}}, tolerating anything.

    Module-level and side-effect free so the desktop board can reuse exactly
    this parser rather than writing a second one that drifts from it."""
    path = STORE if path is None else path
    try:
        raw = _read_json_bounded(path)
    except (OSError, ValueError):
        return {}
    plan = raw.get("plan") if isinstance(raw, dict) else None
    if not isinstance(plan, dict) or not any(
            isinstance(k, str) and nbapp.day_ordinal(k) is not None
            for k in plan):
        plan = _day_map(raw)
    if not isinstance(plan, dict):
        return {}
    out = {}
    for day, meals in plan.items():
        if not isinstance(day, str):
            continue
        ordinal = nbapp.day_ordinal(day)
        if ordinal is None:
            continue
        if not isinstance(meals, dict):
            continue
        clean = {}
        for meal, slot in meals.items():
            if meal not in MEALS or not isinstance(slot, dict):
                continue
            title = slot.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            kind = slot.get("kind")
            if kind not in (KIND_RECIPE, KIND_TAKEOUT, KIND_NOTE):
                kind = KIND_NOTE
            clean[meal] = {"kind": kind, "title": _cap_title(title, kind)}
        if not clean:
            continue
        # File the day under the CANONICAL "YYYY-MM-DD" for the date it names,
        # never the spelling it happened to be written with. day_ordinal is
        # deliberately lenient -- it reads "2026-8-4" and rolls "2026-02-30"
        # forward to 2 March -- but the grid and the desktop tile only ever
        # look a day up by _date_key(), which is zero-padded and in range. A
        # key kept verbatim therefore passed this parser, was counted in the
        # status line, and was written back out by every later save, while
        # being invisible in the week and impossible to edit or clear: filling
        # that cell in made a SECOND entry for the same date, and the first one
        # stayed in the file for good.
        key = _date_key(ordinal)
        day_out = out.setdefault(key, {})
        for meal, slot in clean.items():
            # Two spellings of one date: the first in the file wins, so a
            # re-read is stable and nothing already shown changes underneath.
            day_out.setdefault(meal, slot)
    return out


def _read_store_raw(path=None):
    path = STORE if path is None else path
    try:
        raw = _read_json_bounded(path)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def read_recipe_titles(path=None):
    """Every recipe title in Cookbook, in its order. Never raises: no Cookbook,
    or a damaged one, simply means nothing to pick from."""
    path = COOKBOOK_FILE if path is None else path
    try:
        raw = _read_json_bounded(path)
    except (OSError, ValueError):
        return []
    recipes = raw.get("recipes") if isinstance(raw, dict) else None
    # Cookbook preserves both the current list shape and legacy/title-keyed
    # recipe maps. Its cross-app consumer must accept the same shapes or the
    # owning app can visibly contain recipes while this chooser says none exist.
    if isinstance(recipes, dict):
        recipes = list(recipes.values())
    elif not isinstance(recipes, list):
        return []
    out = []
    for r in recipes:
        if not isinstance(r, dict):
            continue
        title = r.get("title")
        if isinstance(title, str) and title.strip():
            out.append(title.strip())
    return out


def next_meal(plan, day=None, now_hour=None):
    """(meal key, slot) for the meal today is heading towards, or None.

    "Next" is the first meal whose hour has not passed; after dinner there is
    nothing left today, which is the honest answer rather than looping round to
    tomorrow's breakfast."""
    day = day or _today_key()
    hour = time.localtime().tm_hour if now_hour is None else now_hour
    today = plan.get(day) or {}
    for meal in MEALS:
        if hour < MEAL_UNTIL[meal] and today.get(meal):
            return meal, today[meal]
    return None


class MealPlanner(nbapp.AppWindow):
    app_name = "Meal Planner"
    menus = ("File", "Edit", "View")

    def __init__(self):
        super().__init__()
        self.plan = read_plan()
        self._store_raw = _read_store_raw()
        self.recipes = read_recipe_titles()
        # A store this parser read nothing out of, that nonetheless plainly
        # holds meals, must be kept rather than replaced: EVERY edit here
        # rewrites the whole file, so filling in one slot would otherwise leave
        # the week containing only that slot. Valid JSON of the wrong shape
        # parses perfectly, so nbapp's generic quarantine cannot see it.
        self._quarantine_pending = not self.plan and _holds_meals()
        self.week = _week_start(_today_key())
        self._cells = {}
        self._dish_lines = {}
        self._build()
        self._install_css()
        self._refresh()
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()
        self._closed = False
        self._shown_day = _today_key()
        self._day_rollover_id = GLib.timeout_add_seconds(
            30, self._check_day_rollover)
        self.connect("destroy", self._on_destroy)

    # -- store ---------------------------------------------------------------

    def _serialize_store(self):
        """Overlay editable slots onto the original forward-compatible store."""
        raw = copy.deepcopy(getattr(self, "_store_raw", {}))
        source = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
        out = {}
        known_source = {}
        for day, meals in source.items():
            ordinal = nbapp.day_ordinal(day) if isinstance(day, str) else None
            if ordinal is None or not isinstance(meals, dict):
                out[day] = copy.deepcopy(meals)
                continue
            key = _date_key(ordinal)
            bucket = out.setdefault(key, {})
            for meal, slot in meals.items():
                if meal in MEALS:
                    known_source.setdefault((key, meal), copy.deepcopy(slot))
                else:
                    bucket.setdefault(meal, copy.deepcopy(slot))

        for day in set(out) | set(self.plan):
            if nbapp.day_ordinal(day) is None:
                continue
            bucket = out.get(day)
            if not isinstance(bucket, dict):
                # A date key whose value was not a day of meals ("junk", a
                # list, a number) came through the first pass verbatim; it is
                # damage under a key this app OWNS, and the loader already
                # read that day as empty. Writing the live day over it is the
                # save the person asked for; leaving it in place crashed the
                # first save after opening such a store (str has no .pop).
                bucket = {}
                out[day] = bucket
            current = self.plan.get(day, {})
            for meal in MEALS:
                slot = current.get(meal)
                if not isinstance(slot, dict):
                    bucket.pop(meal, None)
                    continue
                merged = known_source.get((day, meal), {})
                merged = copy.deepcopy(merged) if isinstance(merged, dict) else {}
                merged.update({"kind": slot.get("kind", KIND_NOTE),
                               "title": slot.get("title", "")})
                bucket[meal] = merged
            if not bucket:
                out.pop(day, None)
        raw["plan"] = out
        return raw

    def _save(self):
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            if self._quarantine_pending:
                if not _quarantine(STORE):
                    raise OSError("could not preserve unrecognized meal plan")
                self._quarantine_pending = False
            data = self._serialize_store()
            nbapp.atomic_write_json(STORE, data, indent=1)
            self._store_raw = copy.deepcopy(data)
            self._save_error = ""
            return True
        except OSError as exc:
            # A read-only home must never stop the app working — but it must not
            # be silent either. See academics._save_to_disk. Held rather than
            # flashed because the status strip is rewritten on every refresh;
            # _refresh_status shows this until a save succeeds.
            self._save_error = nbapp.save_failure_reason(exc, STORE)
            return False

    @staticmethod
    def dialog_prefill(slot, recipes):
        """What the edit dialog opens showing for `slot`: (combo row, entry
        text). Row 0 is "Nothing from the cookbook"; recipes start at 1.

        THE BUG THIS EXISTS FOR: a slot filed as a RECIPE whose dish is no
        longer in Cookbook (renamed there, or deleted) matched neither branch —
        the combo could not preselect it and the free-text box was filled only
        for non-recipe slots. So the dialog for a day that plainly read
        "Grandma's stew" opened completely blank, and pressing Save wrote the
        empty result back, silently clearing the meal. Every slot with a title
        now shows that title somewhere it can be saved from."""
        if not slot:
            return 0, ""
        title = slot.get("title") or ""
        if slot.get("kind") == KIND_RECIPE and title:
            if title in recipes:
                return recipes.index(title) + 1, ""
            # A slot written before recipe titles were kept whole holds only
            # the first MAX_TYPED_TITLE characters of the recipe it names, so
            # it matches nothing above and would open as free text -- and
            # saving that turned the meal into a note. Only a title that IS
            # that long can be one of those cuts (the trim can have taken one
            # more character off the end), so nothing shorter is guessed at.
            if len(title) >= MAX_TYPED_TITLE - 1:
                for i, recipe in enumerate(recipes):
                    if len(recipe) > len(title) and recipe.startswith(title):
                        return i + 1, ""
        return 0, title

    def _slot(self, day, meal):
        return (self.plan.get(day) or {}).get(meal)

    def _undo_snapshot(self):
        return copy.deepcopy(self.plan)

    def _undo_restore(self, plan):
        before = copy.deepcopy(self.plan)
        self.plan = copy.deepcopy(plan)
        if not self._save():
            reason = self._save_error
            self.plan = before
            # Repair best-effort in case a writer failed after publishing the
            # rejected plan. A successful repair clears _save_error, so put
            # the original failure back for the status strip below.
            self._save()
            self._save_error = reason
            self._refresh()
            return False
        self._refresh()
        return True

    def _set_slot(self, day, meal, kind, title, restore_focus=False):
        title = _cap_title(title, kind)
        old = self._slot(day, meal)
        new = ({"kind": kind, "title": title} if title else None)
        if old == new:
            return
        before = self._undo_snapshot()
        self.undo.checkpoint("Clear Meal" if not title else "Edit Meal")
        if not title:
            entry = self.plan.get(day)
            if entry:
                entry.pop(meal, None)
                if not entry:
                    self.plan.pop(day, None)
        else:
            self.plan.setdefault(day, {})[meal] = {"kind": kind,
                                                   "title": title}
        if not self._save():
            # Do not show an edit that the next launch will silently discard.
            # Keep the save error set by _save so the restored view explains
            # why the requested change could not be applied.
            self.plan = before
            self._refresh()
            if restore_focus:
                cell = self._cells.get((day, meal))
                if cell is not None:
                    cell.grab_focus()
            return
        self._refresh()
        if restore_focus:
            cell = self._cells.get((day, meal))
            if cell is not None:
                cell.grab_focus()
        self.undo.commit()

    # -- ui ------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app.
        css = b"""
        .mp-main { background: #FCFBF8; }
        .mp-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .mp-title { font-size: 24px; font-weight: 700; color: #1A1916; }
        .mp-sub { font-size: 13px; color: #6E695E; }
        .mp-rule { background: #D7D2C5; }
        .mp-dayhead { font-size: 12px; font-weight: 700; color: #6E695E;
                      padding: 6px 0; }
        .mp-dayhead.today { color: #1A1916; }
        .mp-daydate { font-size: 11px; color: #6E695E; }
        .mp-mealname { font-size: 11px; letter-spacing: 0.12em;
                       font-weight: 700; color: #6E695E; padding: 0 8px; }
        .mp-slot { background: #FCFBF8; border: 1px solid #D7D2C5;
                   border-radius: 8px; padding: 8px 9px; }
        /* Every cell is a real button so the week can be tabbed through. This
           strips the button's own chrome -- its padding, border, fill and
           shadow -- so .mp-slot inside it still owns the whole look and the
           grid measures exactly as it did. Deliberately NO `:focus` rule and
           no `outline: none`: the focus ring is what makes the keyboard path
           visible, and it is the entire reason these are buttons. */
        .mp-slothit { padding: 0; margin: 0; border: none;
                      background: transparent; background-image: none;
                      box-shadow: none; min-width: 0; min-height: 0; }
        .mp-slothit:hover, .mp-slothit:active, .mp-slothit:checked {
                      background: transparent; background-image: none;
                      box-shadow: none; }
        /* Hover now belongs to the wrapper, not to .mp-slot: the pointer is
           over the BUTTON, and a cell whose fill only answered to its own
           :hover would light up on part of the cell and not the rest. */
        .mp-slothit:hover .mp-slot { background: #F1EEE6; }
        /* Today's column is where the eye goes first, so it carries the one
           accent on this screen. Nothing else here is red. */
        /* Today reads as a COLUMN, the way it does on the Academics timetable:
           the whole day is tinted, not just its heading. */
        .mp-slot.today { border-color: #C9C4B6; background: #F4F2EC; }
        .mp-slothit:hover .mp-slot.today { background: #EFEBE0; }
        .mp-daycol.today .mp-dayhead { box-shadow: inset 0 -2px 0 #C8341E; }
        .mp-dish { font-size: 15px; color: #1A1916; }
        /* "Add" is an invitation, not content: it stays quiet until the cell
           is hovered, so a mostly-empty week reads as a blank page to fill in
           rather than as a wall of the word "Add". */
        .mp-empty { font-size: 14px; color: #6E695E; }
        .mp-slothit:hover .mp-empty { color: #1A1916; }
        .mp-kind { font-size: 10px; letter-spacing: 0.08em; font-weight: 700;
                   color: #6E695E; }
        .mp-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #D7D2C5; background: #F8F7F2; }
        .mp-cta { background: #F8F7F2; border: 1px solid #C9C4B6;
                  border-radius: 8px; padding: 7px 16px; font-size: 14px;
                  color: #1A1916; box-shadow: none; }
        .mp-cta:hover { background: #EFEBE0; }
        .mp-quiet { background: transparent; border: 1px solid transparent;
                    border-radius: 8px; padding: 5px 10px; font-size: 13px;
                    color: #6E695E; box-shadow: none; }
        .mp-quiet:hover { background: #EFEBE0; border-color: #D7D2C5; }
        .mp-fieldlabel { font-size: 11px; letter-spacing: 0.1em;
                         font-weight: 700; color: #6E695E; }
        /* A tick that cannot be answered right now. Papertone dims a disabled
           MENU item's ink (gtk.css: menuitem:disabled) but leaves a check
           button's LABEL at full strength, so a Takeaway tick whose box alone
           went pale read as an ordinary unticked one. This is the theme's own
           unavailable tone, @inkoff, spelled out the way every other grey in
           this sheet is. */
        .mp-takeaway:disabled, .mp-takeaway:disabled label { color: #A9A395; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                       # noqa: BLE001
            pass          # styling is cosmetic; never block launch

    def _build(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("mp-main")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.set_margin_top(26)
        head.set_margin_start(28)
        head.set_margin_end(28)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=_t("Meals"), xalign=0)
        t.get_style_context().add_class("mp-title")
        titles.pack_start(t, False, False, 0)
        self.sub = Gtk.Label(xalign=0)
        self.sub.get_style_context().add_class("mp-sub")
        self.sub.set_ellipsize(Pango.EllipsizeMode.END)
        self.sub.set_max_width_chars(48)
        titles.pack_start(self.sub, False, False, 0)
        head.pack_start(titles, True, True, 0)

        # pack_end stacks right-to-left, so this list is reversed: read on
        # screen it is Back / This week / Forward, which is the only order in
        # which "Back" being left of "Forward" makes sense.
        for label, tip, delta in ((_t("Forward"), _t("The week after"), 7),
                                  (_t("This week"), _t("This week"), 0),
                                  (_t("Back"), _t("The week before"), -7)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("mp-quiet")
            b.set_valign(Gtk.Align.CENTER)
            b.set_tooltip_text(tip)
            b.connect("clicked", self._on_step, delta)
            head.pack_end(b, False, False, 0)
        main.pack_start(head, False, False, 0)

        rule = Gtk.Box()
        rule.get_style_context().add_class("mp-rule")
        rule.set_size_request(-1, 1)
        rule.set_margin_top(16)
        rule.set_margin_start(28)
        rule.set_margin_end(28)
        main.pack_start(rule, False, False, 0)

        self.grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        self.grid.set_margin_top(16)
        self.grid.set_margin_start(28)
        self.grid.set_margin_end(28)
        self.grid.set_margin_bottom(20)
        self.grid.set_column_homogeneous(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.pack_start(self.grid, True, True, 0)
        scroll.add(holder)
        main.pack_start(scroll, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        # An ellipsizing label still reports its WHOLE line as its natural
        # width, and this one names a dish: a recipe title out of Cookbook is
        # as long as Cookbook made it, and one of those asked for a 2384px
        # window. max_width_chars(1) pins the request; the strip is packed to
        # fill, so it still runs the width of the window and ends in an
        # ellipsis (the same fix widgets.py uses on the board's rows).
        self.status.set_max_width_chars(1)
        self.status.get_style_context().add_class("mp-status")
        self.status.set_vexpand(False)
        main.pack_start(self.status, False, False, 0)

        self.content.pack_start(main, True, True, 0)

    def _refresh(self):
        for ch in self.grid.get_children():
            self.grid.remove(ch)
        self._cells = {}
        # line budgets waiting to be applied belong to the cells being thrown
        # away here, not to the ones about to be built
        self._dish_lines = {}
        today = _today_key()

        # meal-name gutter down the left, so a column is read as a day
        for r, meal in enumerate(MEALS):
            lbl = Gtk.Label(label=meal_name(meal).upper(), xalign=1)
            lbl.get_style_context().add_class("mp-mealname")
            lbl.set_valign(Gtk.Align.CENTER)
            self.grid.attach(lbl, 0, r + 1, 1, 1)

        for c in range(7):
            day = _date_key(self.week + c)
            is_today = (day == today)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            cctx = col.get_style_context()
            cctx.add_class("mp-daycol")
            if is_today:
                cctx.add_class("today")
            name = Gtk.Label(label=_t(DAY_ABBR[c]), xalign=0.5)
            nctx = name.get_style_context()
            nctx.add_class("mp-dayhead")
            if is_today:
                nctx.add_class("today")
            col.pack_start(name, False, False, 0)
            dnum = Gtk.Label(label=day.split("-")[2].lstrip("0"), xalign=0.5)
            dnum.get_style_context().add_class("mp-daydate")
            col.pack_start(dnum, False, False, 0)
            self.grid.attach(col, c + 1, 0, 1, 1)

            for r, meal in enumerate(MEALS):
                self.grid.attach(self._slot_widget(day, meal, is_today),
                                 c + 1, r + 1, 1, 1)

        first = _date_key(self.week)
        last = _date_key(self.week + 6)
        fy, fm, fd = first.split("-")
        ly, lm, ld = last.split("-")
        # The year is named for any week that is not in the current one, and
        # on both ends of a week that crosses one: the last week of the year
        # read "28 December - 3 January", which is two different years and
        # said neither, and a week months out was indistinguishable from the
        # same dates this year. Composed from parts that are each already in
        # the interface language, the way the rest of this line is.
        this_year = _today_key()[:4]
        show_year = fy != this_year or ly != this_year
        head_year = " " + fy if show_year and fy != ly else ""
        tail_year = " " + ly if show_year else ""
        if fm == lm:
            span = "%s\u2013%s %s%s" % (fd.lstrip("0"), ld.lstrip("0"),
                                        _t(MONTHS[int(fm) - 1]), tail_year)
        else:
            span = "%s %s%s \u2013 %s %s%s" % (fd.lstrip("0"),
                                              _t(MONTHS[int(fm) - 1]),
                                              head_year,
                                              ld.lstrip("0"),
                                              _t(MONTHS[int(lm) - 1]),
                                              tail_year)
        self.sub.set_text(span)
        self._refresh_status()
        self.grid.show_all()

    def _check_day_rollover(self):
        """Follow midnight only when the user was viewing the current week."""
        if self._closed:
            return False
        day = _today_key()
        if day != self._shown_day:
            old_week = _week_start(self._shown_day)
            new_week = _week_start(day)
            if self.week == old_week:
                self.week = new_week
            self._shown_day = day
            self._refresh()
        return True

    def _on_destroy(self, *_):
        self._closed = True
        source_id = getattr(self, "_day_rollover_id", 0)
        self._day_rollover_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        return False

    def _slot_widget(self, day, meal, is_today):
        slot = self._slot(day, meal)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        ctx = box.get_style_context()
        ctx.add_class("mp-slot")
        if is_today:
            ctx.add_class("today")
        if slot:
            dish = Gtk.Label(xalign=0)
            _set_user_text(dish, slot["title"])
            dish.get_style_context().add_class("mp-dish")
            # A cell is tall now, so a long dish WRAPS instead of being cut off
            # at "Porridge an...". max_width_chars(1) keeps it from setting the
            # column's natural width (which would stretch one day wider than
            # the other six); wrap + valign START do the rest.
            dish.set_line_wrap(True)
            dish.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            dish.set_lines(3)
            dish.set_ellipsize(Pango.EllipsizeMode.END)
            dish.set_max_width_chars(1)
            dish.set_valign(Gtk.Align.START)
            _set_user_tooltip(dish, slot["title"])
            box.pack_start(dish, False, False, 0)
            k = None
            if slot["kind"] == KIND_TAKEOUT:
                k = Gtk.Label(label=_t("TAKEAWAY"), xalign=0)
                k.get_style_context().add_class("mp-kind")
                box.pack_start(k, False, False, 0)
            # A LINE BUDGET, not a fixed three. set_lines is what stops a long
            # dish from asking for its whole wrapped height and pushing the
            # week into a scroll, but three of them cut "Leftover roast
            # chicken sandwiches with salad" off in a cell with 110px of empty
            # space under it. The budget is measured from the room the CELL
            # was given -- not from the label, whose own allocation is only
            # ever the three lines it asked for -- so it follows the window's
            # height and leaves the TAKEAWAY tag its line.
            box.connect("size-allocate", self._fit_dish_lines, dish, k)
        else:
            empty = Gtk.Label(label=_t("Add"), xalign=0)
            empty.get_style_context().add_class("mp-empty")
            box.pack_start(empty, False, False, 0)

        box.set_valign(Gtk.Align.FILL)
        # A REAL BUTTON, not an EventBox. All 21 cells are actions — each one
        # opens the edit dialog — and an EventBox answers to the pointer
        # alone: it takes no focus, is not in the Tab ring, and reports
        # nothing to assistive technology. There is no other route to a slot,
        # so on the keyboard the entire week was unreachable. .mp-slothit
        # strips the button's chrome, so the cell looks and measures as before.
        hit = Gtk.Button()
        hit.set_relief(Gtk.ReliefStyle.NONE)
        hit.get_style_context().add_class("mp-slothit")
        hit.set_vexpand(True)          # the three meal rows share the window
        hit.add(box)
        hit.set_tooltip_text(
            _t("%s on %s") % (meal_name(meal), _day_label(day)))
        hit.connect("clicked",
                    lambda _w, d=day, m=meal: self._edit_slot(d, m))
        self._cells[(day, meal)] = hit
        return hit

    def _fit_dish_lines(self, box, alloc, dish, tag):
        """As many lines of the dish name as this cell has room to show.

        The cell's own padding and border come from the stylesheet, so they
        are read back from it rather than repeated here. The answer is applied
        from an idle and only when it CHANGES: a widget cannot ask for a new
        layout while it is being given its size (set the lines from here and
        the label keeps the three-line box it was already allocated and draws
        past the bottom of it), and a handler that writes on every allocation
        re-enters itself for ever."""
        ctx, state = box.get_style_context(), box.get_state_flags()
        pad, border = ctx.get_padding(state), ctx.get_border(state)
        room = (alloc.height - pad.top - pad.bottom
                - border.top - border.bottom)
        if tag is not None:
            room -= tag.get_preferred_height()[1] + box.get_spacing()
        line = max(1, dish.create_pango_layout("Ag").get_pixel_size()[1])
        lines = max(1, min(room // line, MAX_DISH_LINES))
        if lines != dish.get_lines() and self._dish_lines.get(dish) != lines:
            self._dish_lines[dish] = lines
            GLib.idle_add(self._apply_dish_lines, dish)

    def _apply_dish_lines(self, dish):
        lines = self._dish_lines.pop(dish, 0)
        if lines and dish.get_parent() is not None:
            dish.set_lines(lines)
        return False

    def _refresh_status(self):
        # A save that did not happen outranks anything else this strip says.
        if getattr(self, "_save_error", ""):
            self.status.set_text(self._save_error)
            return
        planned = sum(len(m) for m in self.plan.values())
        nxt = next_meal(self.plan)
        if nxt is None:
            today = self.plan.get(_today_key()) or {}
            if today:
                self.status.set_text(_t("No more meals today"))
            elif not planned:
                self.status.set_text(_t("No meals planned"))
            else:
                self.status.set_text(_t("Nothing planned for today"))
            return
        meal, slot = nxt
        self.status.set_text(_t("Next: %s for %s")
                             % (slot["title"], meal_name(meal)))

    # -- actions -------------------------------------------------------------

    def _on_step(self, _btn, delta):
        if delta == 0:
            self.week = _week_start(_today_key())
        else:
            self.week += delta
        self._refresh()

    def _edit_slot(self, day, meal):
        """Choose what is being eaten: something from Cookbook, a takeaway, or
        anything you like in your own words."""
        slot = self._slot(day, meal)
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        if slot:
            clr = dlg.add_button(_t("Clear"), Gtk.ResponseType.REJECT)
            clr.get_style_context().add_class("dlgcancel")
        ok = dlg.add_button(_t("Save"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(22)
        box.set_margin_bottom(14)
        box.set_margin_start(26)
        box.set_margin_end(26)

        heading = Gtk.Label(label=_t("%s on %s") % (meal_name(meal),
                                                    _day_label(day)),
                            xalign=0)
        heading.get_style_context().add_class("dlgtitle")
        box.pack_start(heading, False, False, 0)

        # From Cookbook
        lbl = Gtk.Label(label=_t("FROM THE COOKBOOK"), xalign=0)
        lbl.get_style_context().add_class("mp-fieldlabel")
        box.pack_start(lbl, False, False, 0)
        combo = Gtk.ComboBoxText()
        combo.append_text(_t("Nothing from the cookbook"))
        for title in self.recipes:
            _combo_append_user(combo, title)
        # A recipe title belongs to Cookbook, and Cookbook caps nothing:
        # appended raw, the longest title in the book set this chooser's
        # natural width and the whole dialog grew to match -- a 230-character
        # title made it 1351px wide on a 1024px panel, with Save and Cancel
        # off the side of the screen. Ellipsized and held to a column of
        # characters (composer._cap_combo_cells does the same for its
        # instrument lists), the dialog is the same size whatever Cookbook
        # holds; the popup still shows each title in full, and the full title
        # is what gets stored.
        for cell in combo.get_cells():
            cell.props.ellipsize = Pango.EllipsizeMode.END
            cell.props.max_width_chars = 64
        pick, prefill = self.dialog_prefill(slot, self.recipes)
        combo.set_active(pick)
        if not self.recipes:
            combo.set_sensitive(False)
            combo.set_tooltip_text(
                _t("No recipes in Cookbook"))
        box.pack_start(combo, False, False, 0)

        # Or in your own words
        lbl2 = Gtk.Label(label=_t("OR TYPE A MEAL"), xalign=0)
        lbl2.get_style_context().add_class("mp-fieldlabel")
        lbl2.set_margin_top(6)
        box.pack_start(lbl2, False, False, 0)
        entry = Gtk.Entry()
        entry.set_placeholder_text(_t("Example: leftovers"))
        entry.set_activates_default(True)
        # The box stops where the slot stops. Without this a longer meal was
        # typed in full, accepted, and then kept cut off mid-word, with
        # nothing on screen having said where the limit was. A slot that
        # already holds more than that -- a recipe title, which is kept whole
        # -- is only being SHOWN here, so the box opens wide enough for it
        # rather than cutting it on the way in.
        entry.set_max_length(max(MAX_TYPED_TITLE, len(prefill)))
        entry.set_text(prefill)
        box.pack_start(entry, False, False, 0)

        takeaway = Gtk.CheckButton(label=_t("Takeaway"))
        takeaway.get_style_context().add_class("mp-takeaway")
        was_takeout = bool(slot and slot["kind"] == KIND_TAKEOUT)
        takeaway.set_active(was_takeout)
        box.pack_start(takeaway, False, False, 0)

        # A takeaway is a meal nobody cooked from a recipe, so the two are
        # alternatives: with a recipe picked, the save path below stores
        # KIND_RECIPE and this tick had nowhere to go. It was accepted, then
        # silently dropped -- the cell showed no TAKEAWAY tag and the reopened
        # dialog showed the tick gone. Grey it out while a recipe is the
        # answer, and give back what it was showing the moment the meal is a
        # typed one again.
        wanted = {"tick": was_takeout}

        def _sync_takeaway(*_a):
            live = combo.get_active() <= 0 or bool(entry.get_text().strip())
            if live == takeaway.get_sensitive():
                return
            if live:
                takeaway.set_sensitive(True)
                takeaway.set_active(wanted["tick"])
            else:
                wanted["tick"] = takeaway.get_active()
                takeaway.set_active(False)
                takeaway.set_sensitive(False)

        combo.connect("changed", _sync_takeaway)
        entry.connect("changed", _sync_takeaway)
        _sync_takeaway()

        box.show_all()
        entry.grab_focus()
        resp = dlg.run()
        if resp == Gtk.ResponseType.REJECT:
            dlg.destroy()
            self._set_slot(day, meal, KIND_NOTE, "", restore_focus=True)
            return
        if resp != Gtk.ResponseType.OK:
            dlg.destroy()
            return
        # A recipe pick wins over the free-text box only when the box is empty,
        # so typing something after picking does not silently lose what you
        # typed. Read the combo's INDEX, never its text: nbi18n translates
        # widget text in place, so get_active_text() on the placeholder row
        # returns the translation and would be stored as a dish.
        idx = combo.get_active()
        typed = entry.get_text().strip()
        ticked = takeaway.get_active()
        dlg.destroy()
        if idx == pick and typed == prefill.strip() and ticked == was_takeout:
            # Nothing here was changed, so nothing is written. The dialog has
            # to show a slot the chooser cannot represent -- a recipe Cookbook
            # no longer has under that name -- as text in the box, and writing
            # that back would file a recipe as a note for a Save the person
            # made no edit in.
            return
        if typed:
            kind = KIND_TAKEOUT if ticked else KIND_NOTE
            self._set_slot(day, meal, kind, typed, restore_focus=True)
        elif idx > 0 and idx - 1 < len(self.recipes):
            self._set_slot(day, meal, KIND_RECIPE, self.recipes[idx - 1],
                           restore_focus=True)
        else:
            self._set_slot(day, meal, KIND_NOTE, "", restore_focus=True)

    def _clear_week(self):
        days = [_date_key(self.week + i) for i in range(7)]
        n = sum(len(self.plan.get(d) or {}) for d in days)
        if not n:
            return
        # The detail said "Clear this week?" under a heading reading "Clear
        # this week": a second copy of the question in place of the answer to
        # it. It now states what goes and what stays, and the catalog already
        # carried this sentence for this app (every language has it).
        if not self._confirm(_t("Clear this week"),
                             _t("Remove %d planned meal%s? "
                                "Recipes in Cookbook are kept.")
                             % (n, "" if n == 1 else "s"), _t("Clear")):
            return
        before = self._undo_snapshot()
        self.undo.checkpoint("Clear Week")
        for d in days:
            self.plan.pop(d, None)
        if not self._save():
            self.plan = before
            self._refresh()
            return
        self._refresh()
        self.undo.commit()

    def _confirm(self, heading, detail, ok_label):
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
        ok.get_style_context().add_class("destructive-action")
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(22)
        box.set_margin_bottom(14)
        box.set_margin_start(26)
        box.set_margin_end(26)
        h = Gtk.Label(label=heading, xalign=0)
        h.get_style_context().add_class("dlgtitle")
        box.pack_start(h, False, False, 0)
        d = Gtk.Label(label=detail, xalign=0)
        d.get_style_context().add_class("dlgsub")
        d.set_line_wrap(True)
        d.set_max_width_chars(44)
        box.pack_start(d, False, False, 0)
        box.show_all()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _reload_recipes(self):
        self.recipes = read_recipe_titles()
        self._refresh_status()

    def _on_key(self, w, ev):
        if hasattr(self, "undo") and nbapp.undo_keys(self.undo, ev):
            return True
        return super()._on_key(w, ev)

    def menu_items(self, name):
        if name == "Edit":
            days = [_date_key(self.week + i) for i in range(7)]
            planned = any(self.plan.get(d) for d in days)
            # Undo, Redo and Clear This Week only. There is no text field on
            # this screen -- the week is a grid of buttons, and the one entry
            # in the app lives in a modal dialog this menu cannot be opened
            # over -- so the inherited Cut / Copy / Paste / Select All stood
            # there enabled, on a screen where firing them did nothing.
            # Calendar and Workout, which are the same shape, return the undo
            # items alone.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + [("Clear This Week…",
                    self._clear_week if planned else None)]
        if name == "File":
            return [("Close    Esc", self.close)]
        if name == "View":
            return [
                ("This Week", (lambda: self._on_step(None, 0))),
                ("Week Before", (lambda: self._on_step(None, -7))),
                ("Week After", (lambda: self._on_step(None, 7))),
                nbapp.SEP,
                # Names the outcome — pick up anything saved in Cookbook since
                # this window opened — rather than the machine's word for how
                # it gets there ("Reload").
                ("Look for New Recipes", self._reload_recipes),
            ]
        return super().menu_items(name)


if __name__ == "__main__":
    nbapp.run(MealPlanner)
