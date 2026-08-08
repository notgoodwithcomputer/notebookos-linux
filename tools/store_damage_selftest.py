#!/usr/bin/env python3
"""
Open each data app on a DAMAGED store, close it, and prove the user's data
survived.

THE BUG THIS EXISTS FOR (found and fixed in academics.py first): a loader that
is all-or-nothing. One malformed record -- an entry that is not a dict, a
section stored under the wrong type, an event whose date no longer parses --
made the loader give up and return its empty defaults, the app opened blank,
and the close-time save then wrote that blankness straight over the user's real
file. Opening an app and pressing Esc destroyed a term of work, a year of
recipes, an address book.

A malformed record must cost ITSELF and nothing more. A store that cannot be
parsed at all must be moved aside, never overwritten.

ONE CASE PER PROCESS: nbapp keeps a module-level _BACKED_UP set, so a store is
backed up once per file per PROCESS. Running every case in one process makes
cases 2..n look like they got no backup, which is a lie the first version of
the academics test told. The driver below re-invokes itself per case.

  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/store_damage_selftest.py [app] [case]
"""
import os, sys, json, shutil, copy

# ---------------------------------------------------------------- good stores
J_ENTRY = lambda d, t, txt: {
    "day": str(d), "wd": "MON", "month_label": "JULY", "date": "2026-07-%02d" % d,
    "meta": "", "title": t, "preview": txt[:40], "text": txt, "tags": []}

GOOD = {
 "journal": {"journal.json": {"entries": [
        J_ENTRY(20, "First light", "IRREPLACEABLE ONE"),
        J_ENTRY(21, "The hospital", "IRREPLACEABLE TWO"),
        J_ENTRY(22, "Rain again", "IRREPLACEABLE THREE")], "active": 1}},

 "accounting": {"accounting.json": {"opening": 100.0, "tx": [
        {"date": "2026-07-20", "desc": "Rent", "amt": -850.0},
        {"date": "2026-07-21", "desc": "Wages", "amt": 1200.0},
        {"date": "2026-07-22", "desc": "Groceries", "amt": -63.25}]}},

 "cookbook": {"cookbook.json": {
        "cats": ["Dinner", "Baking"], "active_cat": 0, "sel": 0, "recipes": [
        {"title": "Grandma's stew", "cat": "Dinner", "desc": "Sunday",
         "time": "3h", "makes": "6", "effort": "Easy",
         "ing": "beef\ncarrot", "steps": "brown\nsimmer", "photo": ""},
        {"title": "Soda bread", "cat": "Baking", "desc": "", "time": "1h",
         "makes": "1", "effort": "Easy", "ing": "flour", "steps": "bake",
         "photo": ""},
        {"title": "Lemon tart", "cat": "Baking", "desc": "", "time": "2h",
         "makes": "8", "effort": "Hard", "ing": "lemon", "steps": "chill",
         "photo": ""}]}},

 "contacts": {"contacts.json": {"people": [
        {"name": "Ada Peraza", "role": "Doctor", "phone": "555 0101",
         "email": "", "address": "", "bday": "", "notes": "cardiology"},
        {"name": "Ben Iyer", "role": "", "phone": "555 0102", "email": "",
         "address": "", "bday": "", "notes": ""},
        {"name": "Cy Mbeki", "role": "Plumber", "phone": "555 0103",
         "email": "", "address": "", "bday": "", "notes": "back door key"}]}},

 "tasks": {"tasks-app.json": {"tasks": [
        {"title": "Call the surgery", "project": "Garden", "due": "today",
         "date": "", "time": "", "prio": 2, "done": False},
        {"title": "Post the forms", "project": None, "due": "week",
         "date": "", "time": "", "prio": 0, "done": False},
        {"title": "Order compost", "project": "Garden", "due": "later",
         "date": "", "time": "", "prio": 1, "done": True}],
        "projects": [["Garden", "#4A5E73"]]},
        "tasks.json": [{"text": "Call the surgery", "done": False},
                       {"text": "Post the forms", "done": False},
                       {"text": "Order compost", "done": True}]},

 "workout": {"workout.json": {
        "exercises": [{"id": "e1", "name": "Push-ups", "sets": 3, "reps": 12},
                      {"id": "e2", "name": "Squats", "sets": 4, "reps": 10}],
        "log": {"2026-07-20": {"e1": [12, 12, 12]},
                "2026-07-21": {"e1": [12, 12], "e2": [10]},
                "2026-07-22": {"e2": [10, 10]}},
        "goals": {"2026-07-20": 36, "2026-07-21": 34},
        "show_widget": True}},

 "calendar": {"calendar.json": [
        {"id": "a1", "date": "2026-07-30", "start": 9.0, "end": 10.0,
         "title": "Dentist", "cal": "Personal"},
        {"id": "a2", "date": "2026-07-31", "start": 14.0, "end": 15.0,
         "title": "Aunt Vi's birthday", "cal": "Personal"},
        {"id": "a3", "date": "2026-08-03", "start": 8.0, "end": 9.0,
         "title": "MOT booking", "cal": "Work"}],
        "calendars.json": [{"name": "Personal", "color": "#4A5E73"},
                           {"name": "Work", "color": "#9A7B4F"}]},

 "mealplanner": {"mealplanner.json": {"plan": {
        "2026-07-27": {"breakfast": {"kind": "note", "title": "Porridge"},
                       "dinner": {"kind": "recipe",
                                  "title": "Grandma's stew"}},
        "2026-07-28": {"lunch": {"kind": "takeout", "title": "Chip shop"}},
        "2026-07-29": {"dinner": {"kind": "note", "title": "At Mum's"}}}}},

 "language": {"language.json": {
        "xp": 250, "streak": 7, "streak_day": "2026-07-28",
        "crowns": {"eo:0:0": 3, "eo:0:1": 1, "eo:1:0": 2},
        "seen": ["eo:saluton", "eo:bonan", "eo:dankon"]}},

 # academics: the gate's own docstring says it was BORN from an academics
 # defect, and for months it did not exercise academics (coverage gap found
 # by the app-improve session, 2026-08-09). Classes are referenced by integer
 # index from lectures/homework, so a class record lost mid-list would shift
 # every reference after it — the mutation worth having.
 "academics": {"academics.json": {
        "classes": [{"label": "Biology", "meets": [], "color": "#4A5E73"},
                    {"label": "History", "meets": [], "color": "#9A7B4F"},
                    {"label": "Mathematics", "meets": [], "color": "#6E8B6E"}],
        "lectures": [{"cls": 0, "num": 1, "title": "Cell structure",
                      "date": "2026-07-20", "meta": "", "notes": "mitochondria",
                      "ranges": []},
                     {"cls": 1, "num": 1, "title": "The Republic",
                      "date": "2026-07-21", "meta": "", "notes": "",
                      "ranges": []}],
        "homework": [{"title": "Lab report", "cls": 0, "due": "2026-07-25",
                      "done": False, "note": ""},
                     {"title": "Essay draft", "cls": 1, "due": "2026-07-26",
                      "done": False, "note": ""}]}},
}

BUILD = {
 "journal":    lambda m: m.Journal(),
 "accounting": lambda m: m.Accounting(),
 "cookbook":   lambda m: m.Cookbook(),
 "contacts":   lambda m: m.Contacts(),
 "tasks":      lambda m: m.Tasks(),
 "workout":    lambda m: m.Workout(),
 "calendar":   lambda m: m.Calendar(),
 "mealplanner": lambda m: m.MealPlanner(),
 "language":   lambda m: m.Language(),
 "academics":  lambda m: m.Academics(),
}


# ------------------------------------------------------------------- counting
# What each app's file(s) are worth to the user, in units that cannot be
# re-derived: entries with text, ledger lines, recipes, people, task titles,
# logged workout days, dated events.
def count(app, d):
    if app == "journal":
        e = d.get("journal.json", {})
        e = e.get("entries", []) if isinstance(e, dict) else []
        return sum(1 for x in e if isinstance(x, dict) and x.get("text"))
    if app == "accounting":
        a = d.get("accounting.json", {})
        return len(a.get("tx", [])) if isinstance(a, dict) else 0
    if app == "cookbook":
        c = d.get("cookbook.json", {})
        r = c.get("recipes", []) if isinstance(c, dict) else []
        return sum(1 for x in r if isinstance(x, dict) and x.get("title"))
    if app == "contacts":
        c = d.get("contacts.json", {})
        p = c.get("people", []) if isinstance(c, dict) else []
        return sum(1 for x in p if isinstance(x, dict) and x.get("name"))
    if app == "tasks":
        # Titles alone are not the measure: the flat file always carries those,
        # so a sidecar read as unusable looks harmless while it silently flattens
        # every task back to an undated Today with no list and no priority.
        # Count the rich fields too -- they exist ONLY in the sidecar.
        titles, rich = set(), 0
        m = d.get("tasks-app.json", {})
        for t in (m.get("tasks", []) if isinstance(m, dict) else []):
            if isinstance(t, dict) and t.get("title"):
                titles.add(t["title"])
                rich += bool(t.get("project")) + bool(t.get("prio"))
        for t in (d.get("tasks.json", []) or []):
            if isinstance(t, dict) and t.get("text"):
                titles.add(t["text"])
        return len(titles) + rich
    if app == "workout":
        w = d.get("workout.json", {})
        if not isinstance(w, dict):
            return 0
        ex = [x for x in (w.get("exercises") or []) if isinstance(x, dict)]
        log = w.get("log") if isinstance(w.get("log"), dict) else {}
        return len(ex) + len(log)
    if app == "calendar":
        ev = d.get("calendar.json", []) or []
        n = sum(1 for x in ev if isinstance(x, dict) and x.get("title"))
        cals = d.get("calendars.json", []) or []
        return n + sum(1 for x in cals if isinstance(x, dict) and x.get("name"))
    if app == "mealplanner":
        p = d.get("mealplanner.json") or {}
        p = p.get("plan") if isinstance(p, dict) else None
        if not isinstance(p, dict):
            return 0
        return sum(len(v) for v in p.values() if isinstance(v, dict))
    if app == "academics":
        a = d.get("academics.json", {})
        if not isinstance(a, dict):
            return 0
        n = 0
        for sect, field in (("classes", "label"), ("lectures", "title"),
                            ("homework", "title")):
            v = a.get(sect)
            n += sum(1 for x in v if isinstance(x, dict) and x.get(field)) \
                if isinstance(v, list) else 0
        return n
    if app == "language":
        # XP and the streak are counters somebody EARNED a day at a time and
        # nothing can hand back; crowns and seen-words are the shape of how far
        # through a course they are.
        g = d.get("language.json") or {}
        if not isinstance(g, dict):
            return 0
        n = 1 if g.get("xp") else 0
        n += 1 if g.get("streak") else 0
        for key in ("crowns", "seen"):
            v = g.get(key)
            n += len(v) if isinstance(v, (dict, list)) else 0
        return n
    return 0


# --------------------------------------------------------------------- damage
# Each mutation is a realistic drift/corruption of ONE part of the store. The
# number is how many user-visible units that damage is ALLOWED to cost.
def _mut(fn, cost=0):
    return (fn, cost)


def _first(d, key):
    return d[key]


CASES = {
 "journal": {
  "control":              _mut(lambda f: None),
  "entry not a dict":     _mut(lambda f: f["journal.json"]["entries"].insert(1, "junk")),
  "entries is an object": _mut(lambda f: f["journal.json"].__setitem__(
        "entries", {"a": f["journal.json"]["entries"][0],
                    "b": f["journal.json"]["entries"][1],
                    "c": f["journal.json"]["entries"][2]})),
  "top level is a list":  _mut(lambda f: f.__setitem__(
        "journal.json", f["journal.json"]["entries"])),
  "tags is not a list":   _mut(lambda f: f["journal.json"]["entries"][0]
                               .__setitem__("tags", "bold")),
  "active out of range":  _mut(lambda f: f["journal.json"].__setitem__("active", 99)),
  "wrapper key renamed":  _mut(lambda f: f["journal.json"].__setitem__(
        "days", f["journal.json"].pop("entries"))),
  "file is not json":     None,
 },
 "academics": {
  "control":              _mut(lambda f: None),
  # a class record that is not a dict IN THE MIDDLE of the list — the end
  # would shift no integer reference, the middle shifts every one after it
  "class not a dict mid": _mut(lambda f: f["academics.json"]["classes"]
                               .insert(1, "junk")),
  # a whole section stored as an object rather than a list — academics reads
  # a dict-section's VALUES rather than calling it empty (that recovery is the
  # point), so ALL records are present in the wrong shape and none should be
  # lost: the damage is the shape, not the data.
  "lectures is an object": _mut(lambda f: f["academics.json"].__setitem__(
        "lectures", {"a": f["academics.json"]["lectures"][0],
                     "b": f["academics.json"]["lectures"][1]})),
  "classes is an object": _mut(lambda f: f["academics.json"].__setitem__(
        "classes", {"a": f["academics.json"]["classes"][0],
                    "b": f["academics.json"]["classes"][1],
                    "c": f["academics.json"]["classes"][2]})),
  # a homework record with no title (the unit the count measures) — allowed
  # to cost that one record, nothing more
  "homework has no title": _mut(lambda f: f["academics.json"]["homework"][0]
                                .pop("title"), cost=1),
  "file is not json":     None,
 },
 "accounting": {
  "control":              _mut(lambda f: None),
  "entry not a dict":     _mut(lambda f: f["accounting.json"]["tx"].insert(1, "junk")),
  "tx is an object":      _mut(lambda f: f["accounting.json"].__setitem__(
        "tx", {"1": f["accounting.json"]["tx"][0],
               "2": f["accounting.json"]["tx"][1],
               "3": f["accounting.json"]["tx"][2]})),
  "amt is a string":      _mut(lambda f: f["accounting.json"]["tx"][1]
                               .__setitem__("amt", "1200.00")),
  "opening is junk":      _mut(lambda f: f["accounting.json"].__setitem__("opening", "x")),
  "file is not json":     None,
 },
 "cookbook": {
  "control":              _mut(lambda f: None),
  "recipe not a dict":    _mut(lambda f: f["cookbook.json"]["recipes"].insert(1, "junk")),
  "cats is a number":     _mut(lambda f: f["cookbook.json"].__setitem__("cats", 3)),
  "recipes is an object": _mut(lambda f: f["cookbook.json"].__setitem__(
        "recipes", {"a": f["cookbook.json"]["recipes"][0],
                    "b": f["cookbook.json"]["recipes"][1],
                    "c": f["cookbook.json"]["recipes"][2]})),
  "steps is a list":      _mut(lambda f: f["cookbook.json"]["recipes"][0]
                               .__setitem__("steps", ["brown", "simmer"])),
  "cat is a number":      _mut(lambda f: f["cookbook.json"]["recipes"][1]
                               .__setitem__("cat", 7)),
  # ROUND 5. Journal and Contacts have taken the first list of records out of a
  # store whose wrapper key drifted since round 3; Cookbook was the one left
  # that did not, so a renamed wrapper opened the library on "No recipes" and
  # the close-time save wrote that emptiness over every recipe in the file.
  "wrapper key renamed":  _mut(lambda f: f["cookbook.json"].__setitem__(
        "library", f["cookbook.json"].pop("recipes"))),
  "recipes keyed under a renamed wrapper": _mut(
        lambda f: f["cookbook.json"].__setitem__(
            "library", {r["title"]: r
                        for r in f["cookbook.json"].pop("recipes")})),
  "file is not json":     None,
 },
 "contacts": {
  "control":              _mut(lambda f: None),
  "person not a dict":    _mut(lambda f: f["contacts.json"]["people"].insert(1, "junk")),
  "people is an object":  _mut(lambda f: f["contacts.json"].__setitem__(
        "people", {"a": f["contacts.json"]["people"][0],
                   "b": f["contacts.json"]["people"][1],
                   "c": f["contacts.json"]["people"][2]})),
  "phone is a number":    _mut(lambda f: f["contacts.json"]["people"][0]
                               .__setitem__("phone", 5550101)),
  "wrapper key renamed":  _mut(lambda f: f["contacts.json"].__setitem__(
        "contacts", f["contacts.json"].pop("people"))),
  "file is not json":     None,
 },
 "tasks": {
  "control":              _mut(lambda f: None),
  "task not a dict":      _mut(lambda f: f["tasks-app.json"]["tasks"].insert(1, "junk")),
  "tasks is an object":   _mut(lambda f: f["tasks-app.json"].__setitem__(
        "tasks", {"a": f["tasks-app.json"]["tasks"][0],
                  "b": f["tasks-app.json"]["tasks"][1],
                  "c": f["tasks-app.json"]["tasks"][2]})),
  "sidecar is a list":    _mut(lambda f: f.__setitem__(
        "tasks-app.json", f["tasks-app.json"]["tasks"])),
  "due is a list":        _mut(lambda f: f["tasks-app.json"]["tasks"][0]
                               .__setitem__("due", ["today"])),
  "flat file is an object": _mut(lambda f: f.__setitem__(
        "tasks.json", {"a": f["tasks.json"][0]})),
  "file is not json":     None,
 },
 "workout": {
  "control":              _mut(lambda f: None),
  "exercise not a dict":  _mut(lambda f: f["workout.json"]["exercises"].insert(1, "junk")),
  # cost=2: these two mutations DESTROY records in the file itself (a number
  # holds no exercises; the list form carries only one of the three days), so 2
  # units are gone before the app ever opens. What is on trial is the other 3 —
  # a number here used to raise TypeError out of _load and stop the app opening
  # at all, and the wrapped log used to read as no history and be saved away.
  "exercises is a number": _mut(lambda f: f["workout.json"].__setitem__("exercises", 2),
                                cost=2),
  "log is a list":        _mut(lambda f: f["workout.json"].__setitem__(
        "log", [{"2026-07-20": {"e1": [12]}}]), cost=2),
  "one log day is junk":  _mut(lambda f: f["workout.json"]["log"]
                               .__setitem__("2026-07-19", "junk"), cost=0),
  # ROUND 5. A wrapper key AROUND the day map was taken at face value as one
  # bogus "day" whose values were day-maps rather than sets; every one was then
  # dropped as malformed and the next tap saved the empty log over a training
  # history. The list-of-one-day-objects wrapper was handled, this one was not.
  "log wrapped in a key": _mut(lambda f: f["workout.json"].__setitem__(
        "log", {"days": f["workout.json"]["log"]})),
  "sets not a list":      _mut(lambda f: f["workout.json"]["log"]["2026-07-20"]
                               .__setitem__("e1", 12)),
  "file is not json":     None,
 },
 "calendar": {
  "control":              _mut(lambda f: None),
  "event not a dict":     _mut(lambda f: f["calendar.json"].insert(1, "junk")),
  "store is an object":   _mut(lambda f: f.__setitem__(
        "calendar.json", {"events": list(f["calendar.json"])})),
  "date is unparseable":  _mut(lambda f: f["calendar.json"][1]
                               .__setitem__("date", "31 July")),
  "start is a string":    _mut(lambda f: f["calendar.json"][2]
                               .__setitem__("start", "8am")),
  "calendars is an object": _mut(lambda f: f.__setitem__(
        "calendars.json", {"a": f["calendars.json"][0],
                           "b": f["calendars.json"][1]})),
  # ROUND 5. _event_list has recognised a wrapped list in calendar.json since
  # round 3; the calendars file one directory entry along did not. Taking
  # .values() left a list-inside-a-list that matched nothing, the app fell back
  # to the single stock "Personal", and closing the window wrote that over every
  # named calendar the user had — leaving their events filed under colours and
  # a sidebar that no longer existed.
  "calendars wrapped in a key": _mut(lambda f: f.__setitem__(
        "calendars.json", {"calendars": list(f["calendars.json"])})),
  "calendars under a renamed key": _mut(lambda f: f.__setitem__(
        "calendars.json", {"lists": list(f["calendars.json"])})),
  "file is not json":     None,
 },
 "mealplanner": {
  "control":              _mut(lambda f: None),
  # EVERY edit rewrites this whole file, so a week this parser read nothing out
  # of is a week that the next slot you fill in replaces wholesale.
  "plan wrapped in a key": _mut(lambda f: f["mealplanner.json"].__setitem__(
        "plan", {"week": f["mealplanner.json"]["plan"]})),
  "wrapper key renamed":  _mut(lambda f: f["mealplanner.json"].__setitem__(
        "week", f["mealplanner.json"].pop("plan"))),
  "top level is the plan": _mut(lambda f: f.__setitem__(
        "mealplanner.json", f["mealplanner.json"]["plan"])),
  "one day is junk":      _mut(lambda f: f["mealplanner.json"]["plan"]
                               .__setitem__("2026-07-30", "junk")),
  "one slot is a string": _mut(lambda f: f["mealplanner.json"]["plan"]
                               ["2026-07-28"].__setitem__("lunch", "Chip shop"),
                               cost=1),
  "file is not json":     None,
 },
 "language": {
  "control":              _mut(lambda f: None),
  # Each of these used to break something DIFFERENT and silently: a non-number
  # xp/streak raised inside __init__ so the app would not open at all, a
  # non-object crowns raised the moment a course was opened, and a non-list
  # seen raised on finishing a lesson so the lesson never counted.
  "xp is a string":       _mut(lambda f: f["language.json"]
                               .__setitem__("xp", "250")),
  "streak is a list":     _mut(lambda f: f["language.json"]
                               .__setitem__("streak", [7]), cost=1),
  "crowns is a list":     _mut(lambda f: f["language.json"].__setitem__(
        "crowns", list(f["language.json"]["crowns"].values())), cost=3),
  "crowns wrapped in a key": _mut(lambda f: f["language.json"].__setitem__(
        "crowns", {"eo": dict(f["language.json"]["crowns"])})),
  "seen is an object":    _mut(lambda f: f["language.json"].__setitem__(
        "seen", {k: 1 for k in f["language.json"]["seen"]})),
  "file is not json":     None,
 },
}


def run_case(app, case):
    home = "/tmp/nbhome-dmg-%s" % app
    shutil.rmtree(home, ignore_errors=True)
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    os.environ["NB_HOME"] = home

    files = copy.deepcopy(GOOD[app])
    baseline = count(app, files)
    spec = CASES[app][case]
    primary = list(GOOD[app])[0]
    if spec is None:
        for name, obj in files.items():
            with open(os.path.join(cfg, name), "w") as fh:
                json.dump(obj, fh)
        with open(os.path.join(cfg, primary), "w") as fh:
            fh.write('{"this is not json')
        allowed = baseline            # judged by quarantine, not by count
    else:
        fn, allowed_cost = spec
        fn(files)
        allowed = allowed_cost
        for name, obj in files.items():
            with open(os.path.join(cfg, name), "w") as fh:
                json.dump(obj, fh)

    import gi; gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk
    mod = __import__(app)
    w = BUILD[app](mod)
    n = 0
    while Gtk.events_pending() and n < 300:
        Gtk.main_iteration_do(False); n += 1
    # Close the way a user does: Esc -> destroy.
    try:
        ev = Gdk.EventKey(); ev.keyval = Gdk.KEY_Escape; ev.state = 0
        w._on_key(w, ev)
    except Exception:
        pass
    if hasattr(w, "_on_destroy"):
        w._on_destroy()
    elif hasattr(w, "_save"):
        # Workout and Meal Planner have no destroy flush; ANY user action saves,
        # so stand in for one. Losing the store on the first tap is the same
        # defect as losing it on close.
        w._save()
    else:
        # Language flushes from a destroy lambda rather than a named handler.
        w._save_progress()

    after = {}
    for name in GOOD[app]:
        try:
            with open(os.path.join(cfg, name)) as fh:
                after[name] = json.load(fh)
        except Exception:
            after[name] = None
    got = count(app, after)
    extra = sorted(f for f in os.listdir(cfg) if f not in GOOD[app]
                   and not f.endswith(".bak"))
    if spec is None:
        ok = any(f.startswith(primary + ".damaged-") for f in extra)
        why = "an unreadable store must be moved aside, not overwritten"
    else:
        ok = got >= baseline - allowed
        why = "%d of %d units had to survive open+close" % (baseline - allowed,
                                                            baseline)
    print("%-4s %-12s %-24s kept %s/%s on disk  aside=%s%s"
          % ("PASS" if ok else "FAIL", app, case, got, baseline,
             extra or "NONE", "" if ok else "   <- " + why))
    return ok


# --------------------------------------------------------------------- coverage
# The gap the app-improve session found 2026-08-09: this OS-wide gate was born
# from an academics defect (see the module docstring) and for months exercised
# 9 of the 24 apps that persist a store — and "covered by that app's OWN suite"
# read identically to "covered" in every summary. The ratchet below names
# EVERY store-bearing app and refuses to let one be neither exercised here nor
# explicitly accounted for.
#
# It distinguishes debts that a keyword heuristic would flatten together, a
# distinction that itself cost a round of "no coverage" being misread as
# "vulnerable":
#   exercised            — a damage case runs HERE (the only real assurance)
#   suite:<name>         — a dedicated per-app suite drives a damaged store.
#                          UNVERIFIED until someone READS it — a suite that
#                          merely says "damage" is the vacuous pass one level
#                          up (app-improve's warning). Verification is tracked.
#   defended-untested    — MEASURED to salvage a damaged store, but no gate
#                          watches that defence (writer: writer.py:139 scar,
#                          _sane_page). A coverage debt on a defended app, NOT
#                          an open wound. Low urgency, still real.
#   unmeasured           — no test AND nobody has checked the defence. The one
#                          that could be a wound; must be measured.
#   small-store-judged   — a light store (a tape, a board, a map view) whose
#                          loss is a recorded judgement, not an accident.
# A store-bearing app in NONE of these fails the gate.
#
# KNOWN LIMITATION (app-improve, 2026-08-09): this ratchet measures only
# whether the data is DEFENDED — the damaged file survives. It says nothing
# about whether the app TELLS THE USER what it did, and those are different
# defects. Accounting defended perfectly and still said "A new ledger was
# started" with no mention that the original was kept — in a money app a true
# sentence that reads as "your figures are gone." The messaging axis wants its
# own gate; until then, an app can be VERIFIED here and still lie by omission.
COVERAGE = {
    # exercised here
    "journal": "exercised", "accounting": "exercised", "cookbook": "exercised",
    "contacts": "exercised", "tasks": "exercised", "workout": "exercised",
    "calendar": "exercised", "mealplanner": "exercised", "language": "exercised",
    "academics": "exercised",
    # covered by a dedicated suite — UNVERIFIED until read (app-improve's list)
    # VERIFIED means READ 2026-08-09 and confirmed it writes corrupt bytes to
    # the app's store, reopens the app on it, and asserts the original survives
    # byte-for-byte (not merely that the app doesn't crash). 4 of the 7
    # keyword-claimed suites held up; 3 did NOT — a higher false-cover rate
    # than app-improve's ~18%, and exactly the vacuous pass one level up they
    # warned of. The distinction that caught them: read/render-ROBUSTNESS (the
    # app survives corrupt INPUT) is not store-file PRESERVATION (the damaged
    # FILE is kept, not overwritten). Only the latter is the C2 contract.
    "bills": "suite:bills_selftest VERIFIED — malformed store survives "
             "repeated autosaves, handles the .bak-rotation subtlety",
    "gbasdk": "suite:gbasdk_damage_selftest VERIFIED — truncated/not-json/"
              "empty store, recovery + .bak/.damaged asserted",
    "music": "suite:music_adversarial_selftest VERIFIED — corrupt CFG_FILE "
             "survives open+close byte-for-byte, with a sabotage red-proof",
    "screenplay": "suite:screenplay_adversarial_selftest VERIFIED — two "
                  "corrupt DOC_FILE shapes survive byte-for-byte + a MUTANT "
                  "check that the guard's removal DOES rewrite",
    # keyword-claimed but VERIFICATION FAILED — the suite tests robustness of
    # the read, never that the store FILE is preserved. Real store, real gap.
    "gbaemu": "unmeasured: gbaemu_selftest tests ROM/cartridge handling, NOT "
              "its config store's damage preservation",
    "sequencer": "unmeasured: sequencer_selftest tests that a damaged SONG "
                 "renders without crashing, NOT store-file preservation",
    "settings": "unmeasured: settings_selftest tests resolve_default_app on "
                "corrupt VALUES, NOT that settings.json itself is preserved",
    # defended, measured, but no gate watches it (app-improve probed 11 shapes
    # against each 2026-08-09: 0 crashes, 0 losses, original kept as .bak).
    # These are the "no test" debt, NOT the "no defence" wound — a distinction
    # that got misread twice in an hour, so the status carries it explicitly.
    "writer": "defended-untested: writer.py _sane_page salvage, 11 shapes "
              "measured, 0 lost, .bak kept; needs a gate",
    # All 8 formerly-uncovered apps were measured by app-improve 2026-08-09
    # (8 damaged shapes each): every one DEFENDS — original kept as .bak or
    # quarantined on an unreadable store, 0 losses. So none is a wound; each
    # is a coverage debt (a defence with no gate over it). The C2 picture
    # across the OS: the ONLY genuine data-loss defects are journal/calendar/
    # contacts (the bug-fix session's), and everything else needs a GUARD, not
    # a fix. Burn-down = give each defended-untested app a damage case here.
    "video": "defended-untested: 11 shapes, 0 lost, .bak; "
             "shape {bin,clips,music,size,version}; needs a gate",
    "novel": "defended-untested: 10 shapes, 0 lost, .bak; "
             "shape {active,author,chapters,doc_path,parts,title}; needs a gate",
    "ebook": "defended-untested: quarantines properly (ebook.json.damaged-*); "
             "library metadata + reading position; needs a gate",
    "calculator": "defended-untested: several shapes not rewritten at all; "
                  "the tape; needs a gate",
    "g2048": "defended-untested: .bak every shape; board + best score",
    "terminal": "defended-untested: .damaged-* unreadable / .bak wrong-type; "
                "scrollback + geometry",
    # read path safe by measurement AND construction; the write-over-damaged
    # path needs a real map-pack fixture to reach (atomic by source comment)
    "maps": "read-safe-write-unmeasured: _load_cfg catches all, 0 crashes; "
            "_save_cfg needs a loaded pack to reach — atomic by construction, "
            "not yet measured",
    "finder": "small-store-judged: view mode + removed-apps list, prefs",
    "illustrator": "small-store-judged: recent-files list (drawings are "
                   "separate .nb document files, not this store)",
    "packages": "small-store-judged: the uninstalled-apps list",
    # not a persistent user store at all
    "installer": "no-user-store: writes transient install-time config during "
                 "an install, nothing that outlives the installer",
}


_DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "buildroot", "board", "notebookos", "rootfs-overlay",
                   "opt", "notebook", "de")


def store_bearing_apps():
    """Apps that persist a JSON store under the notebook config dir — the set
    the ratchet must fully account for. Computed, not hand-listed, so a new
    store-bearing app joins the obligation automatically."""
    import sys as _sys
    if _DE not in _sys.path:
        _sys.path.insert(0, _DE)
    try:
        import finder
        mods = sorted(set(finder.APP_MODULES.values()) | {"finder"})
    except Exception:                                             # noqa: BLE001
        mods = [f[:-3] for f in os.listdir(_DE) if f.endswith(".py")]
    bearing = []
    for m in mods:
        p = os.path.join(_DE, m + ".py")
        if not os.path.isfile(p):
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        if ".json" in src and ("CFG_DIR" in src or "config/notebook" in src
                               or "notebook" in src) \
                and ("atomic_write" in src or "json.dump" in src
                     or "json.load" in src):
            bearing.append(m)
    return bearing


def coverage_report():
    """Fail if any store-bearing app is unaccounted for; print the standing
    debt (defended-untested / unmeasured / suite-unverified) every run so it
    stays visible instead of decaying into a silent green."""
    bearing = store_bearing_apps()
    unaccounted = [a for a in bearing if a not in COVERAGE]
    exercised = {a for a, v in COVERAGE.items() if v == "exercised"}
    debts = [(a, COVERAGE[a]) for a in bearing
             if a in COVERAGE and a not in exercised]
    print("\nCOVERAGE: %d store-bearing apps, %d exercised here, %d on debt"
          % (len(bearing), len(exercised & set(bearing)), len(debts)))
    for a, status in sorted(debts):
        print("  debt  %-11s %s" % (a, status))
    for a in sorted(unaccounted):
        print("  FAIL  %-11s persists a store but is NOT accounted for in "
              "COVERAGE — add a case here or record its coverage" % a)
    return not unaccounted


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        raise SystemExit(0 if run_case(sys.argv[1], sys.argv[2]) else 1)
    if len(sys.argv) == 2 and sys.argv[1] == "--coverage":
        raise SystemExit(0 if coverage_report() else 1)
    import subprocess
    apps = [sys.argv[1]] if len(sys.argv) == 2 else list(CASES)
    bad = 0
    for app in apps:
        for case in CASES[app]:
            r = subprocess.run([sys.executable, os.path.abspath(__file__),
                                app, case], capture_output=True, text=True,
                               timeout=180, env=dict(os.environ))
            out = (r.stdout or "").strip().splitlines()
            line = out[-1] if out else "(crash) %s %s: %s" % (
                app, case, (r.stderr or "").strip().splitlines()[-1:])
            print(line)
            if "PASS" not in line:
                bad += 1
    # The coverage ratchet runs as part of the aggregate (not a separate flag
    # someone has to remember): a store-bearing app that nobody exercises or
    # accounts for fails the run, and the standing debt prints every time.
    covered_ok = coverage_report() if len(sys.argv) < 2 else True
    if not covered_ok:
        bad += 1
    print("\nRESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
    raise SystemExit(0 if not bad else 1)
