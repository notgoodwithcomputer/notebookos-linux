#!/usr/bin/env python3
"""Headless regression checks for the 037 calculator tape window and the
other windowed/indexed renders (recall scroll positions, table/trace/graph
state fields). Drives the REAL press/recall/evaluate code paths on a bare
object — no display needed — and the real tape_rows pairing the display
paints from."""
import os
import sys
import traceback

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot", "board", "notebookos", "rootfs-overlay", "opt", "notebook", "de")
# $CALCULATOR_MODULE_DIR wins over the repo path. Without it every red
# proof against this suite is VACUOUS: the mutated copy is ignored, the
# pristine module is measured, and a sabotage reports all-green.
DE = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, DE)
import calculator as C  # noqa: E402

failed = []
total = [0]


def check(name, condition, detail=""):
    total[0] += 1
    print(("PASS " if condition else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not condition:
        failed.append(name)


class Calc:
    """The real tape/eval logic bound onto a widget-free object."""
    press = C.Calculator.press
    recall = C.Calculator.recall
    _remember = C.Calculator._remember
    evaluate = C.Calculator.evaluate
    _fail = C.Calculator._fail
    _TAPE_MAX = C.Calculator._TAPE_MAX

    def __init__(self):
        self.expr = ""
        self.history = ""
        self.tape = []
        self.tape_results = []
        self.variables = {}
        self.ans = 0
        self.fix = None
        self.deg = True
        self.just_evaled = False
        self.second = False
        self.error = False
        self._err_why = None
        self._tape_i = None
        self._tape_draft = ""

    def _refresh(self):
        pass

    def _save_prefs(self):
        pass


def eq(calc, expression):
    calc.expr = expression
    calc.just_evaled = False
    calc.press(("=", "eq", None, "eq"))


def rows_of(calc):
    """The rows the display would paint; a raised exception IS the bug."""
    try:
        return C.tape_rows(calc.tape, calc.tape_results), None
    except Exception:
        return None, traceback.format_exc(limit=1).strip().splitlines()[-1]


WINDOW = 3

# ---- tape length 0 ----
rows, err = (C.tape_window([], [], count=WINDOW), None)
check("tape window empty", rows == [], repr(rows))

# ---- tape length 1 ----
calc = Calc()
eq(calc, "1+1")
rows, err = (C.tape_window(calc.tape, calc.tape_results, count=WINDOW), None)
check("tape window one entry", rows == [("1+1", "2")], err or repr(rows))

# ---- exactly one window of entries ----
calc = Calc()
for n in range(WINDOW):
    eq(calc, "%d+1" % n)
rows, err = (C.tape_window(calc.tape, calc.tape_results, count=WINDOW), None)
expected = [("%d+1" % n, str(n + 1)) for n in range(WINDOW)]
check("tape window exact fill", rows == expected,
      err or "%d rows" % len(rows or []))

# ---- one past the window: oldest drops, pairs stay aligned ----
eq(calc, "%d+1" % WINDOW)
rows, err = (C.tape_window(calc.tape, calc.tape_results, count=WINDOW), None)
expected = [("%d+1" % n, str(n + 1)) for n in range(1, WINDOW + 1)]
check("tape window overflow drops oldest", rows == expected,
      err or "%d rows, first %r" % (len(rows or []), (rows or [("", "")])[0]))
check("tape window overflow keeps lists aligned",
      len(calc.tape) == len(calc.tape_results) == WINDOW + 1,
      "tape=%d results=%d" % (len(calc.tape), len(calc.tape_results)))

# ---- simulated recall scroll: Up to an old sum, = again ----
calc = Calc()
eq(calc, "5+5")
check("recall walks back", calc.recall(-1) and calc.expr == "5+5", calc.expr)
calc.press(("=", "eq", None, "eq"))
rows, err = (C.tape_window(calc.tape, calc.tape_results, offset=99,
                           count=WINDOW), None)
check("tape window after recall re-eval", rows == [("5+5", "10")],
      err or "tape=%d results=%d rows=%r" % (len(calc.tape), len(calc.tape_results), rows))

# ---- a failed = after a good one must not shift the pairing ----
calc = Calc()
eq(calc, "2+2")
eq(calc, "1÷0")
rows, err = rows_of(calc)
check("tape window pairs skip failed entry", rows == [("2+2", "4")],
      err or repr(rows))
check("failed entry still recallable", calc.recall(-1) and calc.expr == "1÷0",
      calc.expr)

# ---- recall scroll position clamps at both ends ----
calc = Calc()
check("recall on empty tape refuses", calc.recall(-1) is False)
eq(calc, "3+4")
eq(calc, "6+4")
calc.expr = "9"          # a draft mid-typing
calc.just_evaled = False
calc.recall(-1)
calc.recall(-1)
calc.recall(-1)          # past the oldest: stays clamped at the oldest
check("recall clamps at oldest", calc.expr == "3+4", calc.expr)
calc.recall(1)
check("recall walks forward", calc.expr == "6+4", calc.expr)
calc.recall(1)           # past the newest: the draft comes back
check("recall past newest restores draft", calc.expr == "9", calc.expr)

# ---- clear and out-of-range scroll positions remain safe ----
calc.tape.clear()
calc.tape_results.clear()
rows = C.tape_window(calc.tape, calc.tape_results, offset=99, count=WINDOW)
check("tape window after clear", rows == [], repr(rows))

# ---- sanitized state guards the table/trace/graph indexed renders ----
if not hasattr(C, "sanitize_state"):
    for name in ("state ys padded to four", "state window rejects bad bounds",
                 "state fix rejects junk", "state table fields reject junk",
                 "state old-format tape pairs from the end",
                 "state oversized results clamp to tape"):
        check(name, False, "sanitize_state missing")
else:
    s = C.sanitize_state({"ys": ["X"], "y_enabled": [True]})
    check("state ys padded to four",
          len(s["ys"]) == 4 and len(s["y_enabled"]) == 4 and s["ys"][0] == "X",
          repr((s["ys"], s["y_enabled"])))
    s = C.sanitize_state({"window": {"xmin": 5, "xmax": -5, "xscl": 0}})
    w = s["window"]
    check("state window rejects bad bounds",
          w["xmin"] < w["xmax"] and w["ymin"] < w["ymax"]
          and w["xscl"] > 0 and w["yscl"] > 0, repr(w))
    s = C.sanitize_state({"fix": "abc"})
    s2 = C.sanitize_state({"fix": 99})
    s3 = C.sanitize_state({"fix": 3})
    check("state fix rejects junk",
          s["fix"] is None and s2["fix"] is None and s3["fix"] == 3,
          repr((s["fix"], s2["fix"], s3["fix"])))
    s = C.sanitize_state({"tbl_start": "junk", "tbl_step": None, "trace_x": float("nan")})
    check("state table fields reject junk",
          s["tbl_start"] == 0.0 and s["tbl_step"] == 1.0 and s["trace_x"] == 0.0,
          repr((s["tbl_start"], s["tbl_step"], s["trace_x"])))
    s = C.sanitize_state({"tape": ["a", "b"], "tape_results": ["4"]})
    check("state old-format tape pairs from the end",
          C.tape_rows(s["tape"], s["tape_results"]) == [("b", "4")],
          repr((s["tape"], s["tape_results"])))
    s = C.sanitize_state({"tape": ["a"], "tape_results": ["1", "2"]})
    rows, err = (None, None)
    try:
        rows = C.tape_rows(s["tape"], s["tape_results"])
    except Exception:
        err = traceback.format_exc(limit=1).strip().splitlines()[-1]
    check("state oversized results clamp to tape",
          err is None and rows is not None and len(rows) <= 1,
          err or repr(rows))

print("RESULT: %d checks, %s" % (total[0],
                                 "ALL PASS" if not failed else "%d FAILED" % len(failed)))
raise SystemExit(bool(failed))
