#!/usr/bin/env python3
"""Headless contract checks for the Classes tile's miniature day schedule."""
import ast
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/widgets.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NAMES = {"_minutes", "_classes_for_day", "_classes_window",
         "_classes_block_geometry", "_classes_collision_lanes",
         "_classes_now_position"}
nodes = [node for node in TREE.body
         if isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom))
         and (not isinstance(node, ast.FunctionDef) or node.name in NAMES)]
# The pure functions need no GTK imports; keep only their own definitions.
space = {}
exec(compile(ast.Module(body=[n for n in nodes if isinstance(n, ast.FunctionDef)],
                        type_ignores=[]), str(SOURCE), "exec"), space)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


top, height = space["_classes_block_geometry"](540, 630, 480, 960, 320)
check((top, height) == (40.0, 60.0),
      "09:00-10:30 must occupy 40..100 in a 320px 08:00-16:00 axis")

events = [{"start": 540, "end": 600, "name": "A", "room": ""},
          {"start": 570, "end": 630, "name": "B", "room": ""},
          {"start": 630, "end": 690, "name": "C", "room": ""}]
laid = space["_classes_collision_lanes"](events)
check([(e["lane"], e["lane_count"]) for e in laid] == [(0, 2), (1, 2), (0, 1)],
      "overlaps must split into two lanes and a later run regain full width")

check(space["_classes_for_day"]([], 0) == [], "no classes must stay empty")
damaged = [{"label": "Safe", "meets": 7}, None,
           {"name": "Bad", "meets": [{"day": 0, "start": "broken"}]}]
check(space["_classes_for_day"](damaged, 0) == [],
      "damaged class records must yield the empty state without raising")

now_y = space["_classes_now_position"]
check(now_y(600, 480, 960, True, 320) == 80.0,
      "now inside a school-day window must render proportionally")
check(now_y(479, 480, 960, True, 320) is None,
      "now before the window must not render")
check(now_y(600, 480, 960, False, 320) is None,
      "now must not render when today has no classes")

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

print("widgets classes schedule selftest: ok")
