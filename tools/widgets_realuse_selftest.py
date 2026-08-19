#!/usr/bin/env python3
"""Real-use selftest for the desktop board (de/widgets.py).

Every check here is driven the way a person drives the board -- the real widget
tree hosted at a real panel size (tools/appdrive), the real store on disk, the
real click handlers -- and every one of them MEASURES what the board did:
what a label was allocated and whether Pango had to ellipsize it, how many
times tasks.json was written, where a row sat before and after.

It guards the seven consumer-visible defects found by driving the board:

  F1  the Tasks summary ("2 / 10 done", "Not saved") and the calendar card's
      day ("Mon 17") drew as a bare "..." -- both cards hand-rolled a copy of
      the header _card_shell had already been fixed for;
  F2  the Meals tile clipped ordinary dish names and its own "Nothing planned"
      on the smallest supported panel;
  F3  one click on a task row could re-enter itself through set_active and run
      away into ~1000 writes of tasks.json, and a failed save said so twice;
  F4  a task ticked here jumped to another row a fifth of a second later, when
      the board's own write came back through its store monitor;
  F5  the empty Tasks card clung to the top edge while every other empty card
      centred its prompt;
  F6  the calendar agenda sorted an all-day event last (and drew it at 00:00)
      while the Schedule tile beside it led with the same event;
  F7  a stored null title drew as the word "None", and a due date in 9999 read
      "in 2912214 days".

Run:  tools/guestrun.sh python3 tools/widgets_realuse_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("DISPLAY", ":0")

import gi                                                       # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                   # noqa: E402
import appdrive                                                 # noqa: E402

RESULTS = []
SHOTS = tempfile.mkdtemp(prefix="nb-widgets-realuse-")
TODAY = time.strftime("%Y-%m-%d")


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def guard(name, fn):
    """Run one check's measurement; a raise is that check FAILING by name, not
    the suite falling over."""
    try:
        ok, detail = fn()
    except Exception as exc:                                    # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    check(name, ok, detail)


# ---- a private home per scenario ------------------------------------------
HOMES = []


def home(tag, stores=None):
    path = tempfile.mkdtemp(prefix="nb-board-%s-" % tag)
    HOMES.append(path)
    cfg = os.path.join(path, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    for name, data in (stores or {}).items():
        with open(os.path.join(cfg, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return path


def store_path(path, name):
    return os.path.join(path, ".config", "notebook", name)


def drive(tag, path, size=(1024, 740)):
    d = appdrive.Drive("widgets", cls="Widgets", size=size, home=path)
    # Let the board finish arriving before it is measured: it reconciles the
    # desktop home half a second after start (_poll_home) and its store
    # monitors report the files this test just planted. Both rebuild the cards,
    # and a rebuild landing in the middle of a measurement would make this
    # suite flap rather than report.
    d.pump(0.9)
    d.shot(os.path.join(SHOTS, tag + ".png"))    # forces a real layout pass
    return d


# ---- measuring instruments -------------------------------------------------
def labels(d):
    out = []
    for w in d.walk():
        if isinstance(w, Gtk.Label) and w.get_visible():
            out.append(w)
    return out


def label_named(d, text):
    for w in labels(d):
        if w.get_text() == text:
            return w
    return None


def clipped(label):
    """True when Pango had to cut this label short in its real allocation."""
    lay = label.get_layout()
    return bool(lay is not None and lay.is_ellipsized())


def text_width(label):
    """What the label's own text measures, in the face it is drawn in."""
    lay = label.create_pango_layout(label.get_text())
    lay.set_font_description(
        label.get_style_context().get_font(Gtk.StateFlags.NORMAL))
    return lay.get_pixel_size()[0]


def task_rows(d):
    rows = []
    for w in d.walk():
        if (isinstance(w, Gtk.ToggleButton)
                and "taskrow" in w.get_style_context().list_classes()):
            names = [c for c in d.walk(w) if isinstance(c, Gtk.Label)]
            rows.append((names[0].get_text() if names else "?", w.get_active()))
    return rows


def row_button(d, text):
    for w in d.walk():
        if (isinstance(w, Gtk.ToggleButton)
                and "taskrow" in w.get_style_context().list_classes()):
            names = [c for c in d.walk(w) if isinstance(c, Gtk.Label)]
            if names and names[0].get_text() == text:
                return w
    raise LookupError("no task row %r on the board" % text)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


TEN_TASKS = [{"text": t, "done": d} for t, d in (
    ("Buy oat milk", False), ("Renew library card", False),
    ("Call the dentist", False), ("Email the landlord", False),
    ("Water the plants", True), ("Book train tickets", True),
    ("Pay the gas bill", False), ("Fix the shed door", False),
    ("Return the parcel", False), ("Sort the recycling", False))]


# ===========================================================================
# A -- a full board at the smallest supported panel: what the cards SAY
# ===========================================================================
full = home("full", {
    "tasks.json": TEN_TASKS,
    "calendar.json": [
        {"title": "Dentist", "date": TODAY, "start": 9.5, "end": 10.5},
        {"title": "Team sync", "date": TODAY, "start": 14.0, "end": 15.0},
        {"title": "Book fair", "date": TODAY, "all_day": True},
        {"title": "Standup", "date": TODAY, "start": 8.0, "end": 8.25}],
    "mealplanner.json": {"plan": {TODAY: {
        "breakfast": {"kind": "note", "title": "Porridge with berries"},
        "dinner": {"kind": "takeout", "title": "Thai green curry"}}}},
})
d = drive("A_full", full)
W = sys.modules["widgets"]

print("\n-- the header read-outs both cards hand-rolled (F1) --")


def a_progress():
    lbl = d.app._progress
    return (lbl.get_text() == "2 / 10 done" and not clipped(lbl)
            and lbl.get_allocation().width >= text_width(lbl),
            (lbl.get_text(), clipped(lbl), lbl.get_allocation().width,
             text_width(lbl)))


guard("the Tasks card's summary is readable, not a bare ellipsis", a_progress)


def a_calday():
    now = time.localtime()
    want = "%s %d" % (W.WD_ABBR[now.tm_wday], now.tm_mday)
    lbl = label_named(d, want)
    if lbl is None:
        return False, "no label reading %r on the board" % want
    return (not clipped(lbl) and lbl.get_allocation().width >= text_width(lbl),
            (want, clipped(lbl), lbl.get_allocation().width, text_width(lbl)))


guard("the calendar card's day is readable, not a bare ellipsis", a_calday)

print("\n-- the Meals tile at 1024x740 (F2) --")


def a_meals():
    bad = []
    for text in ("Nothing planned", "Porridge with berries",
                 "Thai green curry", "Breakfast", "Lunch", "Dinner"):
        lbl = label_named(d, text)
        if lbl is None:
            bad.append((text, "missing"))
        elif clipped(lbl):
            bad.append((text, "clipped to %dpx" % lbl.get_allocation().width))
    return not bad, bad


guard("the Meals tile says its meals and its own words in full", a_meals)

print("\n-- one day, described the same way by both cards (F6) --")


def a_allday_first():
    agenda = d.app._today_events()
    tile = d.app._read_schedule()[1]
    return (agenda and agenda[0]["title"] == "Book fair"
            and tile and tile[0][1] == "Book fair",
            ([e["title"] for e in agenda], [r[1] for r in tile]))


guard("the agenda leads with the all-day event, as the Schedule tile does",
      a_allday_first)


def a_allday_says_so():
    said = [w.get_text() for w in labels(d)]
    return "All Day" in said, said[:40]


guard("...and says it is an all-day event rather than leaving the time blank",
      a_allday_says_so)

d.close()

# the same day, in the shape the Calendar app itself writes: an all-day event
# is stored as 00:00-24:00, and that 0.0 must not be read back as a clock time.
d = drive("A_allday_app", home("allday_app", {"calendar.json": [
    {"title": "Village fete", "date": TODAY, "start": 0.0, "end": 24.0,
     "all_day": True},
    {"title": "Dentist", "date": TODAY, "start": 9.5, "end": 10.5}]}))


def a_allday_not_midnight():
    said = [w.get_text() for w in labels(d)]
    return ("All Day" in said and "00:00" not in said, said[:40])


guard("an all-day event is said to be all day, not held at 00:00",
      a_allday_not_midnight)
d.close()

# ===========================================================================
# B -- an empty board: where an empty card puts its prompt (F5)
# ===========================================================================
print("\n-- the empty Tasks card (F5) --")
d = drive("B_empty", home("empty"))


def b_centred():
    lbl = label_named(d, "No tasks")
    if lbl is None:
        return False, "the empty Tasks card said nothing"
    blk = lbl.get_parent()
    holder = d.app._tasklist.get_allocation()
    block = blk.get_allocation()
    off = abs((block.y + block.height / 2.0)
              - (holder.y + holder.height / 2.0))
    return off <= 4, ("prompt centre is %.0fpx off the card's" % off,
                      (block.y, block.height), (holder.y, holder.height))


guard("the empty Tasks card centres its prompt like every other card",
      b_centred)
d.close()

# ===========================================================================
# C -- ticking a task (F3, F4)
# ===========================================================================
print("\n-- one click is one toggle (F3) --")
race = home("race", {"tasks.json": [
    {"text": "Buy oat milk", "done": False},
    {"text": "Renew library card", "done": False},
    {"text": "Call the dentist", "done": False}]})
TASKS = store_path(race, "tasks.json")
d = drive("C_race", race)
W = sys.modules["widgets"]
import nbapp                                                    # noqa: E402
import nbnotify                                                 # noqa: E402
_real_write = nbapp.atomic_write_json
writes = []


def spy(path, data, *a, **k):
    if os.path.basename(path) == "tasks.json":
        writes.append([(t["text"], t["done"]) for t in data])
    return _real_write(path, data, *a, **k)


W.nbapp.atomic_write_json = spy
# the Tasks app inserts a task at the top a moment before the click lands --
# inside the 180ms the board coalesces store events over.
write_json(TASKS, [{"text": "New top", "done": True},
                   {"text": "Buy oat milk", "done": False},
                   {"text": "Renew library card", "done": False},
                   {"text": "Call the dentist", "done": False}])
row_button(d, "Renew library card").clicked()
d.pump(0.05)
after_race = read_json(TASKS)
W.nbapp.atomic_write_json = _real_write


def c_one_write():
    return len(writes) == 1, writes


guard("one click on a row whose store moved writes tasks.json once",
      c_one_write)


def c_right_task():
    want = [("New top", True), ("Buy oat milk", False),
            ("Renew library card", True), ("Call the dentist", False)]
    got = [(t["text"], t["done"]) for t in after_race]
    return got == want, got


guard("...and ticks the task that was clicked, and only it", c_right_task)
d.close()

gone = home("gone", {"tasks.json": [
    {"text": "Buy oat milk", "done": False},
    {"text": "Renew library card", "done": False}]})
GONE = store_path(gone, "tasks.json")
d = drive("C_gone", gone)
W = sys.modules["widgets"]
writes = []
W.nbapp.atomic_write_json = spy


def c_deleted():
    write_json(GONE, [{"text": "Renew library card", "done": False}])
    row_button(d, "Buy oat milk").clicked()
    d.pump(0.05)
    left = [(t["text"], t["done"]) for t in read_json(GONE)]
    return (writes == [] and left == [("Renew library card", False)],
            (len(writes), writes[:2], left))


guard("clicking a row deleted elsewhere writes nothing and flips nothing",
      c_deleted)
W.nbapp.atomic_write_json = _real_write
d.close()

print("\n-- a save that fails is reported once, and on the card (F3, F1) --")
ro = home("ro", {"tasks.json": [
    {"text": "Buy oat milk", "done": False},
    {"text": "Renew library card", "done": False}]})
d = drive("C_failsave", ro)
W = sys.modules["widgets"]
posts = []
_real_post = nbnotify.post


def _post(*a, **k):
    posts.append(a)


def _boom(*a, **k):
    writes.append(None)
    raise OSError(30, "Read-only file system")


writes = []
W.nbapp.atomic_write_json = _boom
sys.modules["nbnotify"].post = _post
row_button(d, "Buy oat milk").clicked()
d.pump(0.02)
# Read the card the instant the save failed: this is the one moment the
# warning is on screen, and it is the only sign the tick did not persist.
_failed = (d.app._progress.get_text(), clipped(d.app._progress),
           d.app._progress.get_allocation().width,
           text_width(d.app._progress))
_failed_rows = task_rows(d)
d.shot(os.path.join(SHOTS, "C_failsave_after.png"))
W.nbapp.atomic_write_json = _real_write
sys.modules["nbnotify"].post = _real_post


def c_once():
    return len(writes) == 1 and len(posts) == 1, (len(writes), len(posts))


guard("a failed save is attempted once and reported once", c_once)


def c_not_saved_visible():
    said, cut, alloc, want = _failed
    return said == "Not saved" and not cut and alloc >= want, _failed


guard("...and the card can be read saying so", c_not_saved_visible)


def c_not_painted():
    return _failed_rows == [("Buy oat milk", False),
                            ("Renew library card", False)], _failed_rows


guard("...and the row is not painted as done", c_not_painted)
d.close()

print("\n-- a ticked row stays where it was ticked (F4) --")
sort = home("sort", {"tasks.json": [
    {"text": "Buy milk", "done": False},
    {"text": "Buy bread", "done": False},
    {"text": "Renew library card", "done": False},
    {"text": "Water plants", "done": True}]})
SORT = store_path(sort, "tasks.json")
d = drive("C_sort", sort)
before = task_rows(d)
row_button(d, "Buy bread").clicked()
d.pump(0.05)
at_click = task_rows(d)
d.pump(0.8)                       # past the 180ms coalesced reload
d.shot(os.path.join(SHOTS, "C_sort_settled.png"))
settled = task_rows(d)


def c_stays():
    want = [("Buy milk", False), ("Buy bread", True),
            ("Renew library card", False), ("Water plants", True)]
    return at_click == want and settled == want, (before, at_click, settled)


guard("a task ticked here does not slide to another row a moment later",
      c_stays)


def c_elsewhere_still_lands():
    # The board must still notice a change made in the Tasks APP -- the fix
    # for F4 must not have made the desktop stop listening.
    write_json(SORT, [{"text": "Buy milk", "done": False},
                      {"text": "Buy bread", "done": True},
                      {"text": "Renew library card", "done": False},
                      {"text": "Water plants", "done": True},
                      {"text": "Book the ferry", "done": False}])
    d.pump(1.0)
    return ("Book the ferry" in [t for t, _ in task_rows(d)], task_rows(d))


guard("a task added in the Tasks app still appears on the board",
      c_elsewhere_still_lands)
d.close()

# ===========================================================================
# D -- a store nobody's app wrote (F7)
# ===========================================================================
print("\n-- hostile store values (F7) --")
bad = home("hostile", {
    "calendar.json": [{"title": None, "date": TODAY, "start": "9",
                       "end": "10"}],
    "academics.json": {"homework": [{"title": "Big", "due": "9999-12-31",
                                     "done": False}]},
})
d = drive("D_hostile", bad)


def d_no_none():
    said = [w.get_text() for w in labels(d)]
    titles = [e["title"] for e in d.app._load_events()]
    return "None" not in said and titles == [""], (titles, said[:40])


guard("a stored null event title is not drawn as the word None", d_no_none)


def d_far_date():
    rows = d.app._read_homework()[1]
    said = [w.get_text() for w in labels(d)]
    return (rows and rows[0][2] == "31 December 9999"
            and "31 December 9999" in said, rows)


guard("a due date a lifetime away is said as a date, not a day count",
      d_far_date)
d.close()

for path in HOMES:
    shutil.rmtree(path, ignore_errors=True)
print("\nshots: %s" % SHOTS)
print("%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
