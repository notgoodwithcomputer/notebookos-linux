#!/usr/bin/env python3
"""Oversized closed map features remain geometrically closed when bounded."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "osm2nbmap.py")
spec = importlib.util.spec_from_file_location("osm2nbmap", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

points = [(float(i), float(i + 1)) for i in range(module.MAX_POINTS + 2)]
closed = module._bounded_points(points, True)
assert len(closed) == module.MAX_POINTS
assert closed[0] == points[0]
assert closed[-1] == points[0]
assert closed[-2] == points[module.MAX_POINTS - 2]

line = module._bounded_points(points, False)
assert len(line) == module.MAX_POINTS
assert line[-1] == points[module.MAX_POINTS - 1]
short = points[:3]
assert module._bounded_points(short, True) is short

print("OSM2NBMAP POINT LIMIT SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
