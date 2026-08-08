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
from gi.repository import Gtk, Pango                      # noqa: E402

import json                                                # noqa: E402
import os                                                  # noqa: E402
import time                                                # noqa: E402
import copy                                                # noqa: E402

import nbapp                                               # noqa: E402
from nbi18n import _t                                      # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STORE = os.path.join(CFG_DIR, "mealplanner.json")
# Read-only. Cookbook owns this file; the planner never writes it.
COOKBOOK_FILE = os.path.join(CFG_DIR, "cookbook.json")

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


def _holds_meals(path=STORE):
    """True when the store plainly contains slot-shaped records, whether or not
    read_plan managed to make a week out of them. An empty planner is a
    perfectly ordinary state, so the test is the SHAPE of what is in the file,
    never emptiness. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
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
    <name>.damaged-<timestamp> name nbapp.preserve_damaged uses. Never raises."""
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.exists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
    except OSError:
        pass


def read_plan(path=STORE):
    """The plan as {day: {meal: {kind, title}}}, tolerating anything.

    Module-level and side-effect free so the desktop board can reuse exactly
    this parser rather than writing a second one that drifts from it."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
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
            clean[meal] = {"kind": kind if kind in
                           (KIND_RECIPE, KIND_TAKEOUT, KIND_NOTE) else KIND_NOTE,
                           "title": title.strip()[:80]}
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


def read_recipe_titles(path=COOKBOOK_FILE):
    """Every recipe title in Cookbook, in its order. Never raises: no Cookbook,
    or a damaged one, simply means nothing to pick from."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    recipes = raw.get("recipes") if isinstance(raw, dict) else None
    if not isinstance(recipes, list):
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
        self.recipes = read_recipe_titles()
        # A store this parser read nothing out of, that nonetheless plainly
        # holds meals, must be kept rather than replaced: EVERY edit here
        # rewrites the whole file, so filling in one slot would otherwise leave
        # the week containing only that slot. Valid JSON of the wrong shape
        # parses perfectly, so nbapp's generic quarantine cannot see it.
        self._quarantine_pending = not self.plan and _holds_meals()
        self.week = _week_start(_today_key())
        self._cells = {}
        self._build()
        self._install_css()
        self._refresh()
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()

    # -- store ---------------------------------------------------------------

    def _save(self):
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            if self._quarantine_pending:
                self._quarantine_pending = False
                _quarantine(STORE)
            nbapp.atomic_write_json(STORE, {"plan": self.plan}, indent=1)
            self._save_error = ""
        except OSError as exc:
            # A read-only home must never stop the app working — but it must not
            # be silent either. See academics._save_to_disk. Held rather than
            # flashed because the status strip is rewritten on every refresh;
            # _refresh_status shows this until a save succeeds.
            self._save_error = nbapp.save_failure_reason(exc, STORE)

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
        if slot.get("kind") == KIND_RECIPE and title in recipes:
            return recipes.index(title) + 1, ""
        return 0, title

    def _slot(self, day, meal):
        return (self.plan.get(day) or {}).get(meal)

    def _undo_snapshot(self):
        return copy.deepcopy(self.plan)

    def _undo_restore(self, plan):
        self.plan = copy.deepcopy(plan)
        self._save()
        self._refresh()

    def _set_slot(self, day, meal, kind, title):
        old = self._slot(day, meal)
        new = ({"kind": kind, "title": title[:80]} if title else None)
        if old == new:
            return
        self.undo.checkpoint("Clear Meal" if not title else "Edit Meal")
        if not title:
            entry = self.plan.get(day)
            if entry:
                entry.pop(meal, None)
                if not entry:
                    self.plan.pop(day, None)
        else:
            self.plan.setdefault(day, {})[meal] = {"kind": kind,
                                                   "title": title[:80]}
        self._save()
        self._refresh()
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
        .mp-daydate { font-size: 11px; color: #9A9484; }
        .mp-mealname { font-size: 11px; letter-spacing: 0.12em;
                       font-weight: 700; color: #9A9484; padding: 0 8px; }
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
        .mp-empty { font-size: 14px; color: #C9C4B6; }
        .mp-slothit:hover .mp-empty { color: #6E695E; }
        .mp-kind { font-size: 10px; letter-spacing: 0.08em; font-weight: 700;
                   color: #9A9484; }
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
                         font-weight: 700; color: #9A9484; }
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
        self.status.get_style_context().add_class("mp-status")
        self.status.set_vexpand(False)
        main.pack_start(self.status, False, False, 0)

        self.content.pack_start(main, True, True, 0)

    def _refresh(self):
        for ch in self.grid.get_children():
            self.grid.remove(ch)
        self._cells = {}
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
        if fm == lm:
            span = "%s\u2013%s %s" % (fd.lstrip("0"), ld.lstrip("0"),
                                      _t(MONTHS[int(fm) - 1]))
        else:
            span = "%s %s \u2013 %s %s" % (fd.lstrip("0"),
                                           _t(MONTHS[int(fm) - 1]),
                                           ld.lstrip("0"),
                                           _t(MONTHS[int(lm) - 1]))
        self.sub.set_text(span)
        self._refresh_status()
        self.grid.show_all()

    def _slot_widget(self, day, meal, is_today):
        slot = self._slot(day, meal)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        ctx = box.get_style_context()
        ctx.add_class("mp-slot")
        if is_today:
            ctx.add_class("today")
        if slot:
            dish = Gtk.Label(label=slot["title"], xalign=0)
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
            dish.set_tooltip_text(slot["title"])
            box.pack_start(dish, False, False, 0)
            if slot["kind"] == KIND_TAKEOUT:
                k = Gtk.Label(label=_t("TAKEAWAY"), xalign=0)
                k.get_style_context().add_class("mp-kind")
                box.pack_start(k, False, False, 0)
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
            _t("%s on %s") % (meal_name(meal),
                              _t(DAY_NAMES[(nbapp.day_ordinal(day) + 3) % 7])))
        hit.connect("clicked",
                    lambda _w, d=day, m=meal: self._edit_slot(d, m))
        self._cells[(day, meal)] = hit
        return hit

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

        wd = _t(DAY_NAMES[(nbapp.day_ordinal(day) + 3) % 7])
        heading = Gtk.Label(label=_t("%s on %s") % (meal_name(meal), wd),
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
            combo.append_text(title)
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
        entry.set_text(prefill)
        box.pack_start(entry, False, False, 0)

        takeaway = Gtk.CheckButton(label=_t("Takeaway"))
        takeaway.set_active(bool(slot and slot["kind"] == KIND_TAKEOUT))
        box.pack_start(takeaway, False, False, 0)

        box.show_all()
        entry.grab_focus()
        resp = dlg.run()
        if resp == Gtk.ResponseType.REJECT:
            dlg.destroy()
            self._set_slot(day, meal, KIND_NOTE, "")
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
        dlg.destroy()
        if typed:
            kind = KIND_TAKEOUT if takeaway.get_active() else KIND_NOTE
            self._set_slot(day, meal, kind, typed)
        elif idx > 0 and idx - 1 < len(self.recipes):
            self._set_slot(day, meal, KIND_RECIPE, self.recipes[idx - 1])
        else:
            self._set_slot(day, meal, KIND_NOTE, "")

    def _clear_week(self):
        days = [_date_key(self.week + i) for i in range(7)]
        n = sum(len(self.plan.get(d) or {}) for d in days)
        if not n:
            return
        self.undo.checkpoint("Clear Week")
        for d in days:
            self.plan.pop(d, None)
        self._save()
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

    def menu_items(self, name):
        if name == "Edit":
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items(name)
        if name == "File":
            return [("Close    Esc", self.close)]
        if name == "Edit":
            days = [_date_key(self.week + i) for i in range(7)]
            planned = any(self.plan.get(d) for d in days)
            return [("Clear This Week…", self._clear_week if planned else None)]
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
