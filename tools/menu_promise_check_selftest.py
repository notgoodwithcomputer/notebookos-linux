#!/usr/bin/env python3
"""Headless regression for menu-promise's zero-probe verdict."""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import menu_promise_check as gate  # noqa: E402


class FailedProbe:
    returncode = 1
    stdout = ""
    stderr = "Gtk could not be initialized"


class JsonProbe:
    returncode = 0
    stderr = ""

    def __init__(self, payload):
        self.stdout = json.dumps(payload) + "\n"


def run_with(result):
    """Drive gate.main() with one stubbed probe.

    THE STUB HAS TO WRITE WHERE A REAL CHILD WRITES. This used to return the
    fake result object and nothing else, which was true while the parent read
    `.stdout` off it — and silently false the moment the parent started
    capturing to FILES (it does, so a leaked grandchild can never hold a pipe
    open and make the timeout lie). With nothing written, every case read back
    an empty file and came out as "probe failed", so the one assertion this
    file exists to make could not be reached and the whole selftest was red
    against a gate that was working. A child process writes to the handles it
    is given; so does this.
    """
    real_modules = gate.app_modules
    real_run = gate.subprocess.run

    def fake_run(*a, **kw):
        # ONLY THE PROBE IS FAKED. main() is not the only caller of
        # subprocess.run in this module: tracked() shells out to `git ls-files`
        # to learn which app modules are committed, and a blanket stub answered
        # that with the probe's own JSON. The basename set then held one entry
        # spelled `{"findings": ...}`, so every real app looked UNCOMMITTED,
        # took the work-in-progress branch, and a probe reporting a violation
        # came back "NOT YET COMMITTED … RESULT: PASS". A stub that answers
        # questions it was never asked is not a stub.
        if a and isinstance(a[0], (list, tuple)) and a[0] and a[0][0] == "git":
            return real_run(*a, **kw)
        out, err = kw.get("stdout"), kw.get("stderr")
        if out is not None and getattr(result, "stdout", ""):
            out.write(result.stdout)
        if err is not None and getattr(result, "stderr", ""):
            err.write(result.stderr)
        return result

    try:
        gate.app_modules = lambda: ["calendar.py"]
        gate.subprocess.run = fake_run
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = gate.main()
        return rc, output.getvalue()
    finally:
        gate.app_modules = real_modules
        gate.subprocess.run = real_run


result, text = run_with(FailedProbe())
assert result == 2, result
assert "RESULT: NOT RUN — no app probe completed" in text, text
assert "LEDGER STALE" not in text, text
assert "0 apps probed" in text, text

zero = {"findings": [], "judged": 0, "handoffs": [], "unjudged": [], "neutral": [], "disabled": [],
        "candidates": 0, "errors": []}
result, text = run_with(JsonProbe(zero))
assert result != 0, (result, text)
assert "zero menu actions were measured" in text, text

broken = {"findings": [], "judged": 0, "handoffs": [], "unjudged": [], "neutral": [], "disabled": [],
          "candidates": 0, "errors": [["File", "menu_items failed: boom"]]}
result, text = run_with(JsonProbe(broken))
assert result != 0, (result, text)
assert "menu_items failed: boom" in text, text

# BOTH SIDES OF THE RATCHET, PROVEN RATHER THAN ASSUMED. gate.DEBT is empty
# today, so an ordinary run never reaches the ledger comparison at all — and an
# empty ledger is exactly the state in which someone can later add a row at the
# wrong height, or let a fixed app keep its old ceiling, with nothing to say so.
# That is not hypothetical: the ledger reached 55 violations across 24 apps and
# 21 of those rows had already been fixed, each one headroom a regression could
# have climbed back into while this gate stayed green. Both directions are
# driven here so the ratchet cannot go slack in silence again.
clean = {"findings": [], "judged": 3, "handoffs": [], "unjudged": [], "neutral": [], "disabled": [],
         "candidates": 3, "errors": []}
real_debt = gate.DEBT
try:
    gate.DEBT = {"calendar.py": 1}
    result, text = run_with(JsonProbe(clean))
finally:
    gate.DEBT = real_debt
assert result != 0, (result, text)
assert "LEDGER STALE" in text and "ledger says 1" in text, text

dirty = {"findings": [["File", "New Thing…",
                       "promises to ask but acts at once"]],
         "judged": 3, "handoffs": [], "unjudged": [], "neutral": [], "disabled": [], "candidates": 3,
         "errors": []}
result, text = run_with(JsonProbe(dirty))
assert result != 0, (result, text)
assert "promises to ask but acts at once" in text, text
assert "3 items judged; 0 skipped" in text, text

# AN ITEM THE SWEEP COULD NOT JUDGE FAILS THE GATE AND IS NAMED. This is the
# hole the callable-only flash stub was opened to close: a callback that raises
# used to be folded into the same "skipped" number as a file chooser handed off
# on purpose, so thirteen apps' items disappeared out of the sweep while the
# summary line still printed a confident violation count. Silence is the defect
# — the count, the app, the label and the exception all have to reach the
# output, and the verdict has to be red while any item is unjudged.
unseen = {"findings": [], "judged": 3, "handoffs": ["Print"],
          "unjudged": [["Cook", "Delete Recipe",
                        "TypeError: '<' not supported between instances of "
                        "'float' and 'function'"]],
          "neutral": [["File", "Export to PDF", "No recipe to export"]],
          "disabled": [["Cook", "Start Cooking"]],
          "candidates": 6, "errors": []}
result, text = run_with(JsonProbe(unseen))
assert result != 0, (result, text)
assert "Delete Recipe: NOT JUDGED" in text, text
assert "TypeError" in text, text
assert "HANDED OFF (not judged): 1 items, by label: Print" in text, text
assert ("3 items judged; 4 skipped (1 headless handoffs, 1 status only, "
        "1 not judged, 1 greyed out)") in text, text
assert "1 greyed out in the swept state, not judged: Start Cooking" in text, text
assert "Export to PDF: not judged, status only: No recipe to export" in text, text
assert "RESULT: FAILED" in text, text
print("RESULT: ALL PASS")
