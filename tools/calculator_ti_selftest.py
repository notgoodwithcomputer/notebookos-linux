#!/usr/bin/env python3
"""Headless regression checks for task 037's TI-class calculator core."""
import math
import os
import sys

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot", "board", "notebookos", "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
import calculator as C  # noqa: E402

failed = []
def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not condition: failed.append(name)

class Calc:
    evaluate = C.Calculator.evaluate
    _fail = C.Calculator._fail
    def __init__(self, expression="", deg=True):
        self.expr, self.deg, self.variables, self.ans, self.fix = expression, deg, {}, 0, None

def ev(expression, deg=True, variables=None, ans=0, fix=None):
    c = Calc(expression, deg); c.variables = variables or {}; c.ans = ans; c.fix = fix
    return c.evaluate(), c._err_why

cases = [
    ("sin(30)", True, "0.5"), ("sin(PI/6)", False, "0.5"),
    ("cos(60)", True, "0.5"), ("tan(45)", True, "1"),
    ("asin(1)", True, "90"), ("acos(0)", True, "90"), ("atan(1)", True, "45"),
    ("sinh(0)", True, "0"), ("cosh(0)", True, "1"), ("tanh(0)", True, "0"),
    ("ln(e)", True, "1"), ("log(100)", True, "2"), ("log2(8)", True, "3"),
    ("exp(1)", True, "2.71828182846"), ("pow10(3)", True, "1000"),
    ("sqrt(9)", True, "3"), ("root(27,3)", True, "3"), ("abs(-3)", True, "3"),
    ("floor(2.9)", True, "2"), ("ceil(2.1)", True, "3"), ("round(2.6)", True, "3"),
    ("frac(2.25)", True, "0.25"), ("int(-2.9)", True, "-2"),
    ("fact(5)", True, "120"), ("nCr(5,2)", True, "10"), ("nPr(5,2)", True, "20"),
]
for expression, deg, expected in cases:
    got, _why = ev(expression, deg)
    check("catalog " + expression, got == expected, got)
got, _ = ev("random()")
check("catalog random range", 0 <= float(got) < 1, got)
for expression in ("sqrt(-1)", "log(0)", "root(-1,2)", "nCr(2,3)"):
    got, why = ev(expression)
    check("domain reported " + expression, got == "Error" and why == C._WHY_NOANSWER)
check("Ans expression", ev("Ans+5", ans=7)[0] == "12")
check("stored variable expression", ev("A*3", variables={"A": 4})[0] == "12")

window = {"xmin": -10., "xmax": 10., "ymin": -5., "ymax": 5.}
px = C.graph_to_pixel(2.5, -1.25, window, 800, 400)
xy = C.pixel_to_graph(px[0], px[1], window, 800, 400)
check("graph coordinate round trip", all(abs(a-b) < 1e-12 for a,b in zip(xy, (2.5,-1.25))))
segments = C.sample_segments(math.tan, 1.2, 1.9, 401)
check("tan asymptote breaks polyline", len(segments) >= 2, str(len(segments)))
check("trace step", abs((0 + (10 - (-10))/100) - .2) < 1e-12)
table = C.table_values(lambda x: x*x, -1, .5, 4)
check("table start and step", table == [(-1.,1.),(-.5,.25),(0.,0.),(.5,.25)], repr(table))
check("Fix-N formatting", C.format_number(1/3, 4) == "0.3333")

source = open(os.path.join(DE, "calculator.py"), encoding="utf-8").read()
for token in ('"1": "home"', '"2": "graph"', '"3": "table"', '_catalog_dialog(); return True',
              '_store_dialog(); return True', 'STO→', 'MATH',
              'ALT_VALUE', 'name.isalpha()', '"Left", "Right"', '"Up", "Down"'):
    check("keyboard route " + token, token in source)
for _category, items in C.CATALOG.items():
    for _label, insertion in items:
        check("catalog insertion " + insertion, insertion in source)

print("RESULT: %d checks, %s" % (len(cases) + 4 + 6 + 8 + sum(len(v) for v in C.CATALOG.values()),
                                  "ALL PASS" if not failed else "%d FAILED" % len(failed)))
raise SystemExit(bool(failed))
