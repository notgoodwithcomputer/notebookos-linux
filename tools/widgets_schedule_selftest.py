#!/usr/bin/env python3
"""Headless contract checks for the Schedule tile's miniature day chart.

The tile is the Calendar day view reduced to one board card: today's
calendar.json events merged with today's line of the Academics weekly
pattern, an all-day band above the clock, and each block's spine in its
event's own colour. These are the pure pieces, exercised without GTK.
"""
import ast
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/widgets.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NAMES = {"_minutes", "_schedule_rgb", "_schedule_classes_for_day",
         "_schedule_events_for_day", "_schedule_window",
         "_schedule_block_geometry", "_schedule_collision_lanes",
         "_schedule_now_position"}
CONSTS = {"_RGB_CLASS", "_RGB_EVENT", "_SCHEDULE_DEFAULT_WINDOW"}
nodes = [node for node in TREE.body
         if (isinstance(node, ast.FunctionDef) and node.name in NAMES)
         or (isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id in CONSTS
                     for t in node.targets))]
# The pure functions need no GTK imports; keep only their own definitions.
space = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"),
     space)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


check(len([n for n in nodes if isinstance(n, ast.FunctionDef)]) == len(NAMES),
      "every pure schedule helper must still exist under its contract name")

top, height = space["_schedule_block_geometry"](540, 630, 480, 960, 320)
check((top, height) == (40.0, 60.0),
      "09:00-10:30 must occupy 40..100 in a 320px 08:00-16:00 axis")

events = [{"start": 540, "end": 600, "name": "A", "where": ""},
          {"start": 570, "end": 630, "name": "B", "where": ""},
          {"start": 630, "end": 690, "name": "C", "where": ""}]
laid = space["_schedule_collision_lanes"](events)
check([(e["lane"], e["lane_count"]) for e in laid] == [(0, 2), (1, 2), (0, 1)],
      "overlaps must split into two lanes and a later run regain full width")

check(space["_schedule_classes_for_day"]([], 0) == [],
      "no classes must stay empty")
damaged = [{"label": "Safe", "meets": 7}, None,
           {"name": "Bad", "meets": [{"day": 0, "start": "broken"}]}]
check(space["_schedule_classes_for_day"](damaged, 0) == [],
      "damaged class records must yield the empty state without raising")

# A class carries its Academics colour onto the chart; one that never chose a
# colour paints in the reserved Classes-calendar tone the Calendar app uses.
cls = [{"name": "Chem", "color": "#8A5340", "room": "B4",
        "meets": [{"day": 2, "start": "14:00", "end": "15:30"},
                  {"day": 3, "start": "09:00"}]},
       {"label": "Alg", "meets": [{"day": 2, "start": "09:00"}]}]
wed = space["_schedule_classes_for_day"](cls, 2)
check([(e["start"], e["end"], e["name"], e["where"]) for e in wed]
      == [(540, 600, "Alg", ""), (840, 930, "Chem", "B4")],
      "a weekday expands to its own meetings only, sorted and defaulted")
check(wed[1]["rgb"] == (0x8A / 255.0, 0x53 / 255.0, 0x40 / 255.0),
      "a class colour must reach its spine")
check(wed[0]["rgb"] == space["_RGB_CLASS"],
      "a colourless class must fall back to the Classes-calendar tone")

# The Calendar side of the merge: timed and all-day split, each event painted
# in its calendar's colour, and the Calendar app's own defaults for a record
# with a missing or backwards time (see calendar._norm_event).
evs = [{"ymd": (2026, 8, 9), "start_min": 570, "end_min": 630,
        "title": "Dentist", "cal": "Personal", "where": "High St",
        "all_day": False},
       {"ymd": (2026, 8, 9), "start_min": None, "end_min": None,
        "title": "Fair", "cal": "Personal", "where": "", "all_day": True},
       {"ymd": (2026, 8, 9), "start_min": None, "end_min": None,
        "title": "Timeless", "cal": "Nope", "where": "", "all_day": False},
       {"ymd": (2026, 8, 9), "start_min": 600, "end_min": 540,
        "title": "Backwards", "cal": "Personal", "where": "",
        "all_day": False},
       {"ymd": (2026, 8, 8), "start_min": 60, "end_min": 90,
        "title": "Yesterday", "cal": "Personal", "where": "",
        "all_day": False},
       "garbage", None]
timed, allday = space["_schedule_events_for_day"](
    evs, (2026, 8, 9), {"Personal": "#4A5E73"})
check([e["name"] for e in timed] == ["Dentist", "Timeless", "Backwards"],
      "only today's timed records reach the clock, damaged ones tolerated")
check([a["name"] for a in allday] == ["Fair"],
      "an all-day record goes to the band, not the clock")
check(timed[0]["rgb"] == (0x4A / 255.0, 0x5E / 255.0, 0x73 / 255.0),
      "an event paints in its calendar's colour")
check(timed[1]["rgb"] == space["_RGB_EVENT"],
      "an unknown calendar keeps the neutral event tone")
check((timed[1]["start"], timed[1]["end"]) == (540, 600),
      "a timeless record lands at 09:00 for an hour, as the Calendar draws it")
check((timed[2]["start"], timed[2]["end"]) == (600, 660),
      "an end at or before its start means an hour, as the Calendar draws it")
check(space["_schedule_events_for_day"](7, (2026, 8, 9), {}) == ([], []),
      "a non-list store must yield the empty day without raising")

check(space["_schedule_rgb"]("#4A5E73", None)
      == (0x4A / 255.0, 0x5E / 255.0, 0x73 / 255.0),
      "a well-formed colour parses")
for bad in ("#zzzzzz", "red", "", None, 7, "#4A5E7"):
    check(space["_schedule_rgb"](bad, (1.0, 0.0, 0.0)) == (1.0, 0.0, 0.0),
          "%r must keep the fallback colour" % (bad,))

check(space["_schedule_window"]([]) is None,
      "an empty day has no window; the tile shows its empty state instead")
check(space["_schedule_window"]([{"start": 540, "end": 630}]) == (480, 720),
      "the window pads a whole hour of context either side")
check(isinstance(space["_SCHEDULE_DEFAULT_WINDOW"], tuple)
      and space["_SCHEDULE_DEFAULT_WINDOW"][0]
      < space["_SCHEDULE_DEFAULT_WINDOW"][1],
      "the all-day-only fallback window must be a forward span")

now_y = space["_schedule_now_position"]
check(now_y(600, 480, 960, True, 320) == 80.0,
      "now inside a populated window must render proportionally")
check(now_y(479, 480, 960, True, 320) is None,
      "now before the window must not render")
check(now_y(600, 480, 960, False, 320) is None,
      "now must not render over a day with nothing timed")

# Pin the tolerant JSON-store primitive itself against a genuinely damaged file.
widgets_cls = next(node for node in TREE.body
                   if isinstance(node, ast.ClassDef) and node.name == "Widgets")
read_store = next(node for node in widgets_cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_read_store")
store_space = {"json": json}
exec(compile(ast.Module(body=[read_store], type_ignores=[]), str(SOURCE), "exec"),
     store_space)
with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp) / "academics.json"
    bad.write_text("{not json", encoding="utf-8")
    check(store_space["_read_store"](str(bad)) == {},
          "damaged academics.json must become the empty state")

print("widgets schedule selftest: ok")
print("RESULT: PASS")
