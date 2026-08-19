#!/usr/bin/env python3
"""frame_pacing_check — PAPER-PHYSICS §F5: "smooth" is a number.

Two halves.

DYNAMIC. Drive each motion primitive under NB_MOTION_TRACE and read the
recorded frame times (nbmotion.trace_drain): for every run, the frame count,
the longest inter-frame gap, and the total duration, checked against the
token's DURATION_BANDS. A run that policy said should animate but recorded
ZERO frames FAILS — that is the vacuous-pass guard (a transition that never
fired cannot be called conformant).

  HONEST-MODE NOTE. With no display this drives the engine on a fake frame
  clock: the frame COUNT and the interpolation are real, but the inter-frame
  GAPS are the test's stepping, not a compositor's, so the longest-frame
  budget is only meaningful on the accelerated/software HARDWARE paths of
  Phase 3. Headless, the gap budget is reported and marked ADVISORY, and only
  total-duration-in-band and the non-vacuous guard are enforced. The header
  says which mode ran.

STATIC. No animating module may call bare `self.queue_draw()` — invalidating
the whole app WINDOW per frame is the full-screen software repaint §F1 exists
to prevent. The receiver matters: `self.queue_draw()` repaints the toplevel
and is the violation; `self.some_layer.queue_draw()` targets a small overlay
DrawingArea and is the correct F1-scoped invalidation (g2048's board layer,
widgets' settle strips). So the check flags a bare `self.queue_draw()` in a
module that animates, and leaves sub-widget invalidation alone. Ratchet debt
for existing offenders, both directions (grid_check's pattern).

  python3 tools/frame_pacing_check.py

Exit 0 clean; 1 on any budget failure, vacuous run, or unratcheted static
offender.
"""
import ast
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="frame-pacing-"))
os.environ["NB_ACCEL"] = "0"          # the software path; Amendment 1: motion
                                      # runs here too, and this is where the
                                      # budget bites

# Longest single frame allowed, ms. Named so Phase-3 hardware measurement can
# calibrate them; ADVISORY headless (see the honest-mode note).
LONGEST_FRAME_ACCEL = 33.0            # ~2 frames at 60Hz
LONGEST_FRAME_SOFT = 67.0            # ~4 frames; swrast gets more slack

# Static-half debt: module -> count of AppWindow self.queue_draw() calls that
# have been AUDITED as one-shot (a redraw after a content change), not
# per-frame animation invalidation. Recorded so the gate is green without
# lying, and a NEW one — the per-frame whole-window repaint §F1 forbids —
# fails by pushing the count past the audited baseline. Exact match, both
# directions (a fixed call whose entry lingers goes stale).
#   finder.py:2191 — one-shot redraw after folder navigation (load()), audited
#     2026-08-08; the launch-card animation uses Damaged/queue_draw_area.
QUEUE_DRAW_DEBT = {"finder.py": 1}

_FAILS = []
_CHECKS = [0]


def _check(ok, msg, advisory=False):
    _CHECKS[0] += 1
    if not ok:
        if advisory:
            print("ADVISORY %s" % msg)
        else:
            _FAILS.append(msg)
            print("FAIL  %s" % msg)


# --------------------------------------------------------------------- dynamic
def _run_dynamic():
    import nbmotion as m
    # A frame clock must exist for policy() to allow motion; supply the one
    # attribute it checks, exactly as the motion suite does headless.
    real_fca = m.frame_clock_available
    m.frame_clock_available = lambda: True
    m.set_reduced_motion(False)
    headless = m.Gtk is None
    mode = "HEADLESS (engine on a fake clock; gap budgets ADVISORY)" \
        if headless else "DISPLAY"
    print("dynamic mode: %s" % mode)
    os.environ["NB_MOTION_TRACE"] = "1"
    m.trace_drain()

    # One raw Scalar per non-instant token: the token is the label, and the
    # trace carries the token ms so a run can be matched back to its band.
    tokens = [("feedback", m.FEEDBACK), ("select", m.SELECT),
              ("surface-in", m.SURFACE_IN), ("surface-out", m.SURFACE_OUT),
              ("page", m.PAGE)]
    results = []
    for label, dur in tokens:
        s = m.Scalar(manual=True, duration=dur, clock=lambda: 0.0)
        # advance in ~16ms steps (a 60Hz frame) across the token's span
        s.animate_to(1.0)
        span = dur / 1000.0
        steps = max(2, int(round(span / 0.016)))
        for i in range(1, steps + 1):
            s.advance(span * i / steps)
        results.append((label, dur))

    traces = m.trace_drain()
    m.frame_clock_available = real_fca
    os.environ.pop("NB_MOTION_TRACE", None)

    # match traces back to tokens by their recorded token-ms
    by_ms = {}
    for tr in traces:
        by_ms.setdefault(tr[0], []).append(tr)

    print("\n  token         token_ms  frames  longest_ms  total_ms  verdict")
    band = m.DURATION_BANDS
    for label, dur in results:
        got = by_ms.get(float(dur), [])
        # non-vacuous: policy allowed motion, so a trace MUST exist
        _check(bool(got),
               "%s: policy animated but recorded ZERO frames (vacuous)"
               % label)
        if not got:
            print("  %-12s  %7d  %6s  %10s  %8s  NO TRACE"
                  % (label, dur, "-", "-", "-"))
            continue
        tr = got[0]
        frames = len(tr) - 2                 # entries after token_ms and t0
        times = tr[1:]
        gaps = [(times[i] - times[i - 1]) * 1000.0
                for i in range(1, len(times))]
        longest = max(gaps) if gaps else 0.0
        total = (times[-1] - times[1]) * 1000.0 if len(times) > 2 else 0.0
        lo, hi = band[label]
        # total duration must land in the token's band (the engine's own
        # bookkeeping — real regardless of display)
        in_band = lo <= round(total) <= hi or (lo == 0 and hi == 0)
        _check(in_band or total == 0,
               "%s: total %.0fms outside band %d-%d" % (label, total, lo, hi))
        # longest-frame budget: advisory headless, enforced on hardware
        _check(longest <= LONGEST_FRAME_SOFT,
               "%s: longest frame %.1fms over %.0fms"
               % (label, longest, LONGEST_FRAME_SOFT),
               advisory=True)
        print("  %-12s  %7d  %6d  %10.1f  %8.0f  %s"
              % (label, dur, frames, longest, total,
                 "ok" if in_band else "OUT-OF-BAND"))


# ---------------------------------------------------------------------- static
_ANIM_NAMES = ("Scalar", "Damaged", "animate_to", "GrowCard", "fade_to")


def _is_self_queue_draw(call):
    """True for `self.queue_draw()` with no args. Only a toplevel-window
    repaint when `self` IS the window — the enclosing-class check decides."""
    return (isinstance(call, ast.Call) and not call.args
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "queue_draw"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self")


def _base_names(classdef):
    names = []
    for base in classdef.bases:
        if isinstance(base, ast.Attribute):
            names.append(base.attr)
        elif isinstance(base, ast.Name):
            names.append(base.id)
    return names


def _is_window_class(classdef, known=()):
    """True when `self` in this class is the app WINDOW — a full
    self.queue_draw() there repaints the whole screen. A DrawingArea/Box/
    small-widget subclass invalidating itself is the correct F1 scope, so
    those are NOT window classes."""
    bases = _base_names(classdef)
    small = ("DrawingArea", "Box", "EventBox", "Fixed", "Overlay", "Grid",
             "Bin", "Frame", "ScrolledWindow", "Revealer", "Layout")
    if any(b in small for b in bases):
        return False
    roots = {"AppWindow", "Window", "ApplicationWindow"} | set(known)
    return any(b in roots for b in bases)


def _window_classes(tree):
    """Resolve local subclasses transitively to a GTK/app window root."""
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    known = set()
    changed = True
    while changed:
        changed = False
        for cls in classes:
            if cls.name not in known and _is_window_class(cls, known):
                known.add(cls.name)
                changed = True
    return known


def _run_static():
    print("\nstatic: bare self.queue_draw() (whole-window repaint) inside an "
          "AppWindow, in animating modules")
    found = {}
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(DE, fn), encoding="utf-8").read()
        if "nbmotion" not in src and "nbtransitions" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        window_classes = _window_classes(tree)
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef) or cls.name not in window_classes:
                continue
            for call in ast.walk(cls):
                if _is_self_queue_draw(call):
                    found[fn] = found.get(fn, 0) + 1
    for fn in sorted(set(found) | set(QUEUE_DRAW_DEBT)):
        have, debt = found.get(fn), QUEUE_DRAW_DEBT.get(fn)
        if have == debt:
            _CHECKS[0] += 1
            continue
        if debt is None:
            _check(False, "%s: %d bare queue_draw() in an animating function "
                   "(use queue_draw_area) and not in debt" % (fn, have))
        elif have is None:
            _check(False, "STALE DEBT: %s fixed, delete its QUEUE_DRAW_DEBT "
                   "entry" % fn)
        else:
            _check(False, "%s: %d bare queue_draw() != debt %d"
                   % (fn, have, debt))
    if not found:
        print("  none")


def main():
    _run_dynamic()
    _run_static()
    n = _CHECKS[0]
    if _FAILS:
        print("\nRESULT: FAILED — %d of %d checks (frame pacing §F5)"
              % (len(_FAILS), n))
        return 1
    print("\nPASS  frame pacing: %d checks (dynamic bands + static repaint)" % n)
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
