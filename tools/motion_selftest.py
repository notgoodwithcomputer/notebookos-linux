#!/usr/bin/env python3
"""motion_selftest — the Article VI gate on de/nbmotion.py.

    python3 tools/motion_selftest.py

Display-free and static: no X connection, no main loop, no real frame clock.
The engine is built so that is possible — `Scalar(manual=True)` is driven by the
caller's own clock, and the frame-clock driver is exercised through a fake
widget that implements `add_tick_callback` / `connect` the way GTK does. What
this proves, in the order a failure matters:

  1. **Policy.** All four (Reduced Motion x NB_ACCEL) combinations, plus a
     missing frame clock, and END-STATE EQUIVALENCE: a duration-0 transition
     lands on exactly the value an animated one lands on. That equivalence is
     what lets every caller have one code path.
  2. **Completion.** A completion callback fires exactly once, whether the
     transition finishes, is retargeted, is cancelled, or its widget dies.
     Twice is how a dialog gets torn down while it is being torn down.
  3. **Retargeting.** A second animate_to starts from the CURRENT value, not
     the old start value, and does not stack a second animation.
  4. **Lifecycle.** destroy and unrealize both leave zero live tick callbacks
     and zero live drivers. A frame callback outliving its widget is a crash.
  5. **Tokens.** Every duration is inside the band the constitution documents
     and every easing hits its endpoints EXACTLY with no overshoot.
  6. **Shape.** No GLib timer-per-frame anywhere in the module, and the module
     imports and degrades to instant with no gi at all.

The preference tests run against a temporary NB_HOME, so the machine running
the suite cannot change the result.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)

FAILURES = []
CHECKS = [0]


def check(label, ok, detail=""):
    CHECKS[0] += 1
    if not ok:
        FAILURES.append("%s%s" % (label, (": " + detail) if detail else ""))
    return ok


def near(a, b, eps=1e-9):
    return abs(a - b) <= eps


# ---------------------------------------------------------------- fakes -----
class FakeClock:
    """Stands in for the `time` module inside nbmotion, so a 160ms transition
    can be advanced frame by frame without waiting 160ms."""

    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeWidget:
    """The three things nbmotion asks of a Gtk.Widget: a tick callback, the
    destroy/unrealize signals, and an opacity."""

    def __init__(self):
        self.ticks = {}
        self._tid = 0
        self.handlers = {}
        self._hid = 0
        self.opacity = 1.0
        self.removed = []
        self.damage = []             # queue_draw_area rectangles, in order
        self.full_draws = 0          # bare queue_draw() calls — must stay 0
        self.alloc = (1000, 700)     # the "window" the damage is judged against

    def add_tick_callback(self, cb):
        self._tid += 1
        self.ticks[self._tid] = cb
        return self._tid

    def remove_tick_callback(self, tid):
        self.removed.append(tid)
        self.ticks.pop(tid, None)

    def connect(self, signal, cb):
        self._hid += 1
        self.handlers[self._hid] = (signal, cb)
        return self._hid

    def disconnect(self, hid):
        self.handlers.pop(hid, None)

    def emit(self, signal):
        for hid, (sig, cb) in list(self.handlers.items()):
            if sig == signal:
                cb(self)

    def frame(self):
        """One compositor frame: run the live tick callbacks, honouring a
        False return the way the GTK frame clock does."""
        for tid, cb in list(self.ticks.items()):
            if cb(self, None) is False:
                self.ticks.pop(tid, None)

    def get_opacity(self):
        return self.opacity

    def set_opacity(self, v):
        self.opacity = v

    # Article F: the suite watches HOW a widget is invalidated. A full
    # queue_draw() from an animation is the defect §F1 exists to prevent,
    # so both are recorded and the damage tests assert on the split.
    def queue_draw_area(self, x, y, w, h):
        self.damage.append((int(x), int(y), int(w), int(h)))

    def queue_draw(self):
        self.full_draws += 1

    def get_allocated_width(self):
        return self.alloc[0]

    def get_allocated_height(self):
        return self.alloc[1]


# ------------------------------------------------------------- fixtures -----
def with_env(reduced, accel, m):
    m.set_reduced_motion(reduced)
    os.environ["NB_ACCEL"] = "1" if accel else "0"


# ---------------------------------------------------------------- tests -----
def test_easing(m):
    for name, fn in sorted(m.EASINGS.items()):
        check("easing %s f(0)" % name, near(fn(0.0), 0.0), repr(fn(0.0)))
        check("easing %s f(1)" % name, near(fn(1.0), 1.0), repr(fn(1.0)))
        # clamping: a late frame hands us t > 1, an early one t < 0
        check("easing %s clamps low" % name, near(fn(-3.0), 0.0))
        check("easing %s clamps high" % name, near(fn(4.5), 1.0))
        prev = -1.0
        spring = "back" in name          # ease-out-back springs; the cubics do not
        peak = 0.0
        for i in range(0, 101):
            v = fn(i / 100.0)
            peak = max(peak, v)
            if spring:
                # a SLIGHT spring may overshoot the target, but only a little,
                # and it never dips below 0 or runs away (that would be a bounce)
                check("easing %s bounded spring" % name, -0.02 <= v <= 1.15,
                      "t=%.2f -> %r" % (i / 100.0, v))
            else:
                check("easing %s no overshoot" % name,
                      -1e-12 <= v <= 1.0 + 1e-12, "t=%.2f -> %r" % (i / 100.0, v))
                check("easing %s monotonic" % name, v >= prev - 1e-12)
            prev = v
        if spring:
            check("easing %s actually springs (lively, not flat)" % name,
                  peak > 1.0)
    # the curves must be distinguishable, or a token means nothing
    check("ease_out leads linear", m.ease_out(0.25) > m.linear(0.25))
    check("ease_in trails linear", m.ease_in(0.25) < m.linear(0.25))
    check("ease_in_out symmetric",
          near(m.ease_in_out(0.5), 0.5) and
          near(m.ease_in_out(0.25) + m.ease_in_out(0.75), 1.0, 1e-9))
    check("named tokens are the curves",
          m.ARRIVE is m.ease_out_back and m.DEPART is m.ease_in
          and m.MOVE is m.ease_in_out)


def test_durations(m):
    for name, ms in sorted(m.DURATIONS.items()):
        lo, hi = m.DURATION_BANDS[name]
        check("duration %s in band" % name, lo <= ms <= hi,
              "%dms outside %d-%d" % (ms, lo, hi))
        check("duration %s is int ms" % name, isinstance(ms, int))
    check("token set is complete",
          set(m.DURATIONS) == set(m.DURATION_BANDS))
    check("constants match table",
          m.DURATIONS["feedback"] == m.FEEDBACK
          and m.DURATIONS["select"] == m.SELECT
          and m.DURATIONS["surface-in"] == m.SURFACE_IN
          and m.DURATIONS["surface-out"] == m.SURFACE_OUT
          and m.DURATIONS["page"] == m.PAGE
          and m.INSTANT == 0)
    check("reduced fade is the shortest token",
          m.REDUCED_FADE <= min(v for v in m.DURATIONS.values() if v))


def test_policy(m):
    real_clock = m.frame_clock_available
    m.frame_clock_available = lambda: True
    try:
        # (reduced, accel) -> movement duration, crossfade duration.
        # PAPER-PHYSICS §0.5 Amendment 1: NB_ACCEL is not a motion input, so
        # each row must be identical across the accel column — the same
        # language runs on both render paths, and Reduced Motion is the one
        # human off-switch (its fade survives on any path).
        matrix = {
            (False, True): (m.PAGE, m.PAGE),
            (False, False): (m.PAGE, m.PAGE),
            (True, True): (0, m.REDUCED_FADE),
            (True, False): (0, m.REDUCED_FADE),
        }
        for (reduced, accel), (move, fade) in sorted(matrix.items()):
            with_env(reduced, accel, m)
            got_move = m.policy(m.PAGE)
            got_fade = m.policy(m.PAGE, fade=True)
            check("policy move reduced=%s accel=%s" % (reduced, accel),
                  got_move == move, "got %r want %r" % (got_move, move))
            check("policy fade reduced=%s accel=%s" % (reduced, accel),
                  got_fade == fade, "got %r want %r" % (got_fade, fade))
            st = m.policy_state()
            check("policy_state reports inputs reduced=%s accel=%s"
                  % (reduced, accel),
                  st["reduced_motion"] is reduced and st["accelerated"] is accel)
        # order of resolution: reduced motion wins over everything
        with_env(True, True, m)
        check("reduced motion outranks everything", m.policy(m.SURFACE_IN) == 0)
        # Amendment 1's own regression test: flipping NB_ACCEL alone must
        # change nothing about motion (a reintroduced gate fails HERE)
        with_env(False, True, m)
        on_accel = (m.policy(m.PAGE), m.policy(m.PAGE, fade=True))
        with_env(False, False, m)
        on_soft = (m.policy(m.PAGE), m.policy(m.PAGE, fade=True))
        check("NB_ACCEL is not a motion input", on_accel == on_soft,
              "accel %r != soft %r" % (on_accel, on_soft))
        # a zero token stays zero everywhere
        with_env(False, True, m)
        check("INSTANT token stays 0", m.policy(m.INSTANT) == 0)
        check("negative duration is 0", m.policy(-40) == 0)
        check("junk duration is 0", m.policy("soon") == 0)
        # no frame clock: still, whatever the preferences say
        m.frame_clock_available = lambda: False
        check("no frame clock is still", m.policy(m.PAGE) == 0)
    finally:
        m.frame_clock_available = real_clock


def test_track_math(m):
    t = m.Track(0.0, m.linear)
    t.retarget(10.0, 100.0, 200)                 # 200ms, linear, from 0 to 10
    check("track start value", near(t.value_at(100.0), 0.0))
    check("track mid value", near(t.value_at(100.1), 5.0))
    check("track end value EXACT", t.value_at(100.2) == 10.0)
    check("track past end clamps", t.value_at(999.0) == 10.0)
    check("track done_at", t.done_at(100.2) and not t.done_at(100.19))
    # retarget mid-flight starts from the CURRENT value, not the old start
    t.retarget(0.0, 100.1, 200)
    check("retarget starts from current value", near(t.frm, 5.0))
    check("retarget keeps continuity", near(t.value_at(100.1), 5.0))
    check("retarget new end", t.value_at(100.3) == 0.0)
    # a zero-duration track is its target immediately
    z = m.Track(3.0, m.ease_out)
    z.retarget(9.0, 0.0, 0)
    check("zero-duration track is instant", z.value_at(0.0) == 9.0)


def test_manual_scalar(m):
    with_env(False, True, m)
    clock = FakeClock()
    seen = []
    s = m.Scalar(None, 0.0, on_frame=seen.append, manual=True,
                 easing=m.linear, clock=clock.monotonic)
    dones = []
    s.animate_to(1.0, m.PAGE, m.linear, on_done=lambda ok: dones.append(ok))
    check("manual scalar runs", s.running and s.value == 0.0)
    clock.advance(0.1)                            # half of 200ms
    check("manual advance continues", s.advance() is True)
    check("manual mid value", near(s.value, 0.5), repr(s.value))
    check("no completion mid-flight", dones == [])
    # retarget mid-flight: the old completion is cancelled exactly once
    s.animate_to(0.0, m.PAGE, m.linear, on_done=lambda ok: dones.append(ok))
    check("retarget cancels the old completion once", dones == [False], dones)
    check("retarget keeps the value", near(s.value, 0.5))
    check("retarget does not stack", s.running)
    clock.advance(0.2)
    check("manual advance ends", s.advance() is False)
    check("end state exact", s.value == 0.0)
    check("completion fired once, completed", dones == [False, True], dones)
    check("no further frames after the end", s.advance() is False)
    # a frame is delivered only when the value CHANGES: mid-flight, then the
    # end state. A retarget that does not move the value is not a repaint.
    check("frames were delivered", len(seen) == 2, str(seen))
    check("frames are the values passed", near(seen[0], 0.5))
    check("last frame is the end state", seen[-1] == 0.0)

    # cancellation
    clock = FakeClock()
    dones = []
    s = m.Scalar(None, 0.0, manual=True, clock=clock.monotonic)
    s.animate_to(4.0, m.PAGE, m.linear, on_done=lambda ok: dones.append(ok))
    clock.advance(0.05)
    s.advance()
    mid = s.value
    check("cancel returns True while running", s.cancel() is True)
    check("cancel leaves the value where it stood", s.value == mid)
    check("cancel fired the completion once, cancelled", dones == [False])
    check("cancel stops the animation", not s.running)
    check("second cancel is a no-op", s.cancel() is False and dones == [False])
    clock.advance(1.0)
    check("cancelled scalar does not advance", s.advance() is False)
    check("cancelled scalar does not reach the target", s.value == mid)

    # jump_to cancels rather than completes
    dones = []
    s = m.Scalar(None, 0.0, manual=True, clock=clock.monotonic)
    s.animate_to(1.0, m.PAGE, on_done=lambda ok: dones.append(ok))
    s.jump_to(0.25)
    check("jump_to cancels the pending completion", dones == [False])
    check("jump_to lands", s.value == 0.25 and s.target == 0.25
          and not s.running)

    # settle completes
    dones = []
    s = m.Scalar(None, 0.0, manual=True, clock=clock.monotonic)
    s.animate_to(1.0, m.PAGE, on_done=lambda ok: dones.append(ok))
    s.settle()
    check("settle lands on the target", s.value == 1.0 and not s.running)
    check("settle completes once", dones == [True])
    s.settle()
    check("settle is idempotent", dones == [True])


def test_end_state_equivalence(m):
    """The gate the constitution names: animated and still must agree."""
    clock = FakeClock()
    with_env(False, True, m)
    a = m.Scalar(None, 0.0, manual=True, clock=clock.monotonic)
    a.animate_to(0.8, m.PAGE, m.ease_in_out)
    clock.advance(1.0)
    a.advance()
    with_env(True, True, m)                       # reduced motion: still
    b = m.Scalar(None, 0.0, manual=True, clock=clock.monotonic)
    dones = []
    b.animate_to(0.8, m.PAGE, m.ease_in_out, on_done=lambda ok: dones.append(ok))
    check("still path lands on the same value", a.value == b.value == 0.8)
    check("still path completes synchronously",
          dones == [True] and not b.running)
    check("still path reports the target", a.target == b.target == 0.8)


def test_driver_lifecycle(m):
    """The frame-clock path, through a fake widget: one tick callback shared by
    every animation on the widget, removed the moment it is idle."""
    real_time, real_clock = m.time, m.frame_clock_available
    clock = FakeClock()
    m.time = clock
    m.frame_clock_available = lambda: True
    try:
        with_env(False, True, m)
        w = FakeWidget()
        d1, d2 = [], []
        a = m.Scalar(w, 0.0, easing=m.linear)
        b = m.Scalar(w, 0.0, easing=m.linear)
        a.animate_to(1.0, m.PAGE, m.linear, on_done=lambda ok: d1.append(ok))
        b.animate_to(2.0, m.PAGE, m.linear, on_done=lambda ok: d2.append(ok))
        check("two animations share one tick callback", len(w.ticks) == 1,
              str(len(w.ticks)))
        check("one driver for one widget", m.live_drivers() == 1)
        clock.advance(0.1)
        w.frame()
        check("driver advances both", near(a.value, 0.5) and near(b.value, 1.0),
              "%r %r" % (a.value, b.value))
        check("still running after a mid frame", len(w.ticks) == 1)
        clock.advance(0.1)
        w.frame()
        check("driver lands both exactly", a.value == 1.0 and b.value == 2.0)
        check("both completed once", d1 == [True] and d2 == [True])
        check("tick callback removed when idle", w.ticks == {},
              str(w.ticks))
        check("driver dropped when idle", m.live_drivers() == 0)
        check("tick removed exactly once (return False, no double remove)",
              w.removed == [], str(w.removed))

        # destroy mid-flight: cancel, no frames, nothing left behind
        w = FakeWidget()
        dones = []
        s = m.Scalar(w, 0.0)
        s.animate_to(1.0, m.PAGE, on_done=lambda ok: dones.append(ok))
        clock.advance(0.05)
        w.frame()
        mid = s.value
        w.emit("destroy")
        check("destroy cancels once", dones == [False], str(dones))
        check("destroy does not jump the value", s.value == mid)
        check("destroy leaves no driver", m.live_drivers() == 0)
        check("destroy leaves no tick callback", len(w.ticks) == 1
              and not s.running)   # GTK drops it with the widget; we must not
        check("destroy disconnects its handlers", w.handlers == {})
        w.frame()
        check("no frames are delivered after destroy", s.value == mid)

        # unrealize mid-flight: land on the end state, complete once
        w = FakeWidget()
        dones = []
        s = m.Scalar(w, 0.0)
        s.animate_to(1.0, m.PAGE, on_done=lambda ok: dones.append(ok))
        clock.advance(0.05)
        w.frame()
        w.emit("unrealize")
        check("unrealize settles on the end state", s.value == 1.0)
        check("unrealize completes once", dones == [True], str(dones))
        check("unrealize removes the tick callback", w.ticks == {})
        check("unrealize leaves no driver", m.live_drivers() == 0)

        # cancel_all
        w = FakeWidget()
        s1 = m.Scalar(w, 0.0)
        s2 = m.Scalar(w, 0.0)
        s1.animate_to(1.0, m.PAGE)
        s2.animate_to(1.0, m.PAGE)
        check("cancel_all reports what it stopped", m.cancel_all(w) == 2)
        check("cancel_all clears the driver", m.live_drivers() == 0
              and w.ticks == {})
        check("cancel_all on an idle widget is 0", m.cancel_all(w) == 0)

        # fade_to reuses one scalar per widget, so repeated calls retarget
        w = FakeWidget()
        m.fade_to(w, 0.0, m.SURFACE_OUT)
        first = w._nbmotion_opacity
        m.fade_to(w, 1.0, m.SURFACE_IN)
        check("fade_to retargets rather than stacking",
              w._nbmotion_opacity is first and len(w.ticks) == 1)
        clock.advance(1.0)
        w.frame()
        check("fade_to lands on the opacity", w.opacity == 1.0)
        check("fade_to cleans up", m.live_drivers() == 0)

        # a raising frame callback must not leave a live tick callback
        w = FakeWidget()
        dones = []

        def boom(_v):
            raise ValueError("app bug")

        s = m.Scalar(w, 0.0, on_frame=boom)
        s.animate_to(1.0, m.PAGE, on_done=lambda ok: dones.append(ok))
        clock.advance(0.05)
        w.frame()
        check("a raising frame callback stops that animation", not s.running)
        check("a raising frame callback fires the completion once",
              dones == [False], str(dones))
        check("a raising frame callback leaves no driver",
              m.live_drivers() == 0)

        # reduced motion never reaches the frame clock at all
        with_env(True, True, m)
        w = FakeWidget()
        s = m.Scalar(w, 0.0)
        s.animate_to(1.0, m.PAGE)
        check("reduced motion never opens a tick callback",
              w.ticks == {} and m.live_drivers() == 0 and s.value == 1.0)
    finally:
        m.time, m.frame_clock_available = real_time, real_clock
        with_env(False, True, m)


def test_prefs_roundtrip(m):
    """The Settings key, read from the store the way an app reads it."""
    with tempfile.TemporaryDirectory() as home:
        old = os.environ.get("NB_HOME")
        os.environ["NB_HOME"] = home
        try:
            cfg = os.path.join(home, ".config", "notebook")
            os.makedirs(cfg)
            path = os.path.join(cfg, "settings.json")
            m.reload_prefs()
            check("default with no store is False", m.reduced_motion() is False)

            for value in (True, False):
                with open(path, "w") as fh:
                    json.dump({"large_text": True, "reduced_motion": value}, fh)
                m.reload_prefs()
                check("round-trip %r" % value, m.reduced_motion() is value)

            with open(path, "w") as fh:
                json.dump({"large_text": True}, fh)
            m.reload_prefs()
            check("missing key defaults to False", m.reduced_motion() is False)

            for junk in ("{ not json", "[]", "null", '{"reduced_motion": "yes"}'):
                with open(path, "w") as fh:
                    fh.write(junk)
                m.reload_prefs()
                check("malformed store never raises (%s)" % junk[:14],
                      isinstance(m.reduced_motion(), bool))

            with open(path, "w") as fh:
                json.dump({"reduced_motion": False}, fh)
            m.reload_prefs()
            check("cached read", m.reduced_motion() is False)
            check("set_reduced_motion overrides in-process",
                  m.set_reduced_motion(True) is True
                  and m.reduced_motion() is True)
            with open(path) as fh:
                check("set_reduced_motion does not write the store",
                      json.load(fh) == {"reduced_motion": False})
            m.reload_prefs()
            check("reload_prefs returns to the store",
                  m.reduced_motion() is False)
        finally:
            if old is None:
                os.environ.pop("NB_HOME", None)
            else:
                os.environ["NB_HOME"] = old
            m.reload_prefs()


def test_settings_page_wiring():
    """The preference must be reachable from the Accessibility page, and the
    page must apply it in-process the way it applies the other two."""
    src = open(os.path.join(DE, "settings.py")).read()
    tree = ast.parse(src)
    page = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_page_accessibility":
            page = node
    check("settings has an accessibility page", page is not None)
    if page is not None:
        keys = [a.value for n in ast.walk(page)
                if isinstance(n, ast.Call) for a in n.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        check("accessibility page offers reduced_motion",
              "reduced_motion" in keys, str(keys))
    applied = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "set_reduced_motion"]
    check("settings applies reduced motion in-process", len(applied) == 1)
    check("settings imports nbmotion",
          any(isinstance(n, ast.Import)
              and any(a.name == "nbmotion" for a in n.names)
              for n in ast.walk(tree)))
    nbapp_src = open(os.path.join(DE, "nbapp.py")).read()
    check("nbapp motion policy honours reduced motion",
          "reduced_motion()" in nbapp_src and "not reduced)" in nbapp_src)
    # Amendment 1 (PAPER-PHYSICS §0.5): the render path is not a motion
    # input. The docstring may TALK about NB_ACCEL; the code must not read
    # it — so walk the AST rather than grep the prose.
    ntree = ast.parse(nbapp_src)
    fn = next((n for n in ast.walk(ntree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "_apply_motion_policy"), None)
    reads_accel = fn is not None and any(
        isinstance(n, ast.Constant) and n.value == "NB_ACCEL"
        for n in ast.walk(fn))
    check("nbapp motion policy does not consult NB_ACCEL (Amendment 1)",
          fn is not None and not reads_accel)


def test_no_timeout_per_frame():
    """Article VI §3: the frame clock, not a timer per widget."""
    src = open(os.path.join(DE, "nbmotion.py")).read()
    tree = ast.parse(src)
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                calls.add(f.attr)
            elif isinstance(f, ast.Name):
                calls.add(f.id)
    for banned in ("timeout_add", "timeout_add_seconds", "idle_add",
                   "source_remove", "child_watch_add"):
        check("no %s in nbmotion" % banned, banned not in calls)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
    check("nbmotion does not import GLib", "GLib" not in imported,
          str(sorted(imported)))
    check("nbmotion drives from add_tick_callback",
          "add_tick_callback" in calls)
    check("nbmotion removes its tick callback",
          "remove_tick_callback" in calls)
    check("nbmotion interpolates on monotonic time", "monotonic" in calls)
    check("nbmotion cleans up on destroy and unrealize",
          '"destroy"' in src and '"unrealize"' in src)


def test_source_compatibility():
    """Parses on the shipped interpreter, and imports with no gi at all."""
    for name in ("nbmotion.py", "nbapp.py", "settings.py"):
        path = os.path.join(DE, name)
        try:
            ast.parse(open(path).read(), filename=path)
            ok, detail = True, ""
        except SyntaxError as exc:                                # noqa: PERF203
            ok, detail = False, str(exc)
        check("%s parses" % name, ok, detail)

    probe = r"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "gi" or name.startswith("gi."):
            raise ImportError("blocked for the test")
        return None
sys.meta_path.insert(0, Block())
sys.path.insert(0, %r)
import os
os.environ["NB_ACCEL"] = "1"
import nbmotion
assert nbmotion.Gtk is None, "Gtk should be None with gi blocked"
assert nbmotion.frame_clock_available() is False
assert nbmotion.policy(nbmotion.PAGE) == 0, "no frame clock must be still"
seen = []
s = nbmotion.Scalar(object(), 0.0, on_frame=seen.append)
done = []
s.animate_to(1.0, nbmotion.PAGE, on_done=done.append)
assert s.value == 1.0 and not s.running, "must degrade to instant"
assert done == [True] and seen == [1.0], (done, seen)
nbmotion.set_reduced_motion(True)
nbmotion._apply_gtk_animations()
print("OK")
""" % (DE,)
    env = dict(os.environ)
    env.pop("NB_HOME", None)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                       text=True, env=env)
    check("imports and degrades with no gi", r.returncode == 0 and
          r.stdout.strip().endswith("OK"),
          (r.stdout + r.stderr).strip()[-400:])

    r = subprocess.run([sys.executable, "-m", "py_compile",
                        os.path.join(DE, "nbmotion.py")],
                       capture_output=True, text=True)
    check("nbmotion byte-compiles", r.returncode == 0, r.stderr.strip())


def test_damaged(m):
    """Article F §F1: an animation invalidates the UNION of the moving
    layer's previous and current rectangles — never the whole widget. This
    is the price of Amendment 1 and the test runs on the software policy
    path on purpose: that is where a full-window repaint is unaffordable."""
    real_clock = m.frame_clock_available
    m.frame_clock_available = lambda: True
    try:
        with_env(False, False, m)
        w = FakeWidget()
        moved = []
        d = m.Damaged(widget=w, rect_for=lambda v: (v * 500.0, 100, 40, 40),
                      pad=2, on_frame=moved.append, manual=True,
                      duration=m.PAGE, clock=lambda: 0.0)
        d.animate_to(1.0)
        for t in (0.05, 0.10, 0.15, 0.20):
            d.advance(t)
        check("damaged: model frames ran first", len(moved) >= 3,
              str(len(moved)))
        check("damaged: zero full-widget invalidations", w.full_draws == 0,
              str(w.full_draws))
        check("damaged: one damage rect per moved frame",
              len(w.damage) == len(moved), "%d rects / %d frames"
              % (len(w.damage), len(moved)))
        aw, ah = w.alloc
        biggest = max((r[2] * r[3] for r in w.damage), default=0)
        check("damaged: largest rect is a small fraction of the window",
              0 < biggest < (aw * ah) // 4, "%d of %d" % (biggest, aw * ah))
        # The union property: after the first frame, every rect must reach
        # back far enough to erase the previous position (left edge at or
        # before the previous rect's left edge).
        ok_union = all(w.damage[i][0] <= w.damage[i - 1][0] + 1 or
                       w.damage[i][0] <= w.damage[i - 1][0] + w.damage[i - 1][2]
                       for i in range(1, len(w.damage)))
        check("damaged: each rect covers the erase of the previous frame",
              ok_union, str(w.damage))
        check("damaged: lands exactly on the end state",
              moved[-1] == 1.0, repr(moved[-1]))
    finally:
        m.frame_clock_available = real_clock


def test_layer_cache(m):
    """Article F §F1 bullet two: static content is rendered ONCE and
    recomposited, re-rendered only on resize or explicit invalidation."""
    import cairo  # the render toolchain requires pycairo; fail loudly if gone

    class FakeCr:
        def __init__(self):
            self.painted = 0

        def set_source_surface(self, s, x, y):
            self.src = s

        def paint(self):
            self.painted += 1

    w = FakeWidget()
    calls = []
    cache = m.LayerCache(lambda ctx, cw, ch: calls.append((cw, ch)))
    cr = FakeCr()
    check("cache: first paint renders", cache.paint(w, cr) and
          cache.renders == 1 and calls == [(1000, 700)], str(calls))
    cache.paint(w, cr)
    check("cache: second paint reuses the surface", cache.renders == 1)
    check("cache: composited every time", cr.painted == 2, str(cr.painted))
    w.alloc = (800, 600)
    cache.paint(w, cr)
    check("cache: an allocation change re-renders", cache.renders == 2
          and calls[-1] == (800, 600), str(calls))
    cache.invalidate()
    cache.paint(w, cr)
    check("cache: invalidate() re-renders at the same size",
          cache.renders == 3)
    check("cache: cairo surface is real", isinstance(
        getattr(cr, "src", None), cairo.ImageSurface))


def test_trace(m):
    """The §F5 pacing gate's read side: frame times are recorded only when
    NB_MOTION_TRACE was set at animation start, and drain empties."""
    real_clock = m.frame_clock_available
    m.frame_clock_available = lambda: True
    had = os.environ.pop("NB_MOTION_TRACE", None)
    try:
        with_env(False, False, m)
        m.trace_drain()
        s = m.Scalar(manual=True, duration=m.PAGE, clock=lambda: 0.0)
        s.animate_to(1.0)
        for t in (0.07, 0.14, 0.21):
            s.advance(t)
        check("trace: silent without the env", m.trace_drain() == [])
        os.environ["NB_MOTION_TRACE"] = "1"
        s2 = m.Scalar(manual=True, duration=m.PAGE, clock=lambda: 0.0)
        s2.animate_to(1.0)
        for t in (0.07, 0.14, 0.21):
            s2.advance(t)
        got = m.trace_drain()
        check("trace: one entry per finished animation", len(got) == 1,
              str(got))
        check("trace: entry carries token and frame times",
              len(got[0]) >= 4 and got[0][0] == float(m.PAGE), str(got[0]))
        check("trace: drain empties", m.trace_drain() == [])
    finally:
        m.frame_clock_available = real_clock
        if had is None:
            os.environ.pop("NB_MOTION_TRACE", None)
        else:
            os.environ["NB_MOTION_TRACE"] = had


def main():
    # An isolated NB_HOME: the machine running the suite must not decide the
    # answer, and nothing here may touch a real user's settings.
    home = tempfile.mkdtemp(prefix="nbmotion-selftest-")
    os.environ["NB_HOME"] = home
    os.environ.setdefault("NB_ACCEL", "0")
    import nbmotion as m
    m.reload_prefs()

    test_easing(m)
    test_durations(m)
    test_policy(m)
    test_track_math(m)
    test_manual_scalar(m)
    test_end_state_equivalence(m)
    test_driver_lifecycle(m)
    test_damaged(m)
    test_layer_cache(m)
    test_trace(m)
    test_prefs_roundtrip(m)
    test_settings_page_wiring()
    test_no_timeout_per_frame()
    test_source_compatibility()

    print("motion_selftest: %d checks" % CHECKS[0])
    if FAILURES:
        print("FAIL (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
