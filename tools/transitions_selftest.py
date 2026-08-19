#!/usr/bin/env python3
"""transitions_selftest — the gate on de/nbtransitions.py.

    python3 tools/transitions_selftest.py

Display-free and static, on the same terms as tools/motion_selftest.py: no X
connection, no main loop, no real frame clock, no GLib source. The primitives
are built so that is possible — the policy decision is a pure function, the
container work is driven through fake widgets that implement the handful of
methods GTK does, and the one place this module schedules anything is a module
attribute the suite swaps for a fake scheduler.

What it proves, in the order a failure matters:

  1. **Policy.** All four (Reduced Motion x NB_ACCEL) combinations for stacks
     and revealers, including the rule that GTK's own transitions run only on
     an accelerated session and that Reduced Motion keeps a crossfade ONLY
     where the caller explicitly asked for one.
  2. **Instant equivalence.** A still page switch, reveal and content replace
     land on exactly the state an animated one lands on, synchronously, with
     the completion callback already run.
  3. **Generation.** A burst of switches leaves the container on the LAST
     request, and every callback belonging to an older one is dropped.
  4. **Lifecycle.** Nothing fires after destroy: no reveal completion, no
     highlight removal, no live tick callback, no stacked timer.
  5. **Leaks.** A crossfade replace always ends at opacity 1.0 and never
     leaves the old child parented.
  6. **Shape.** No per-frame GLib timer, no width/height/margin/padding
     animation, no spring, no duration numbers of its own — and Settings
     really does switch panes through the shared primitive.
"""
import ast
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


# ---------------------------------------------------------------- fakes -----
class FakeClock:
    """Stands in for the `time` module inside nbmotion, so an 80ms fade can be
    advanced frame by frame without waiting 80ms."""

    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeStyleContext:
    def __init__(self):
        self.classes = set()

    def add_class(self, name):
        self.classes.add(name)

    def remove_class(self, name):
        self.classes.discard(name)


class FakeWidget:
    """Everything the primitives ask of a Gtk.Widget: signals, a frame clock,
    an opacity, a style context and a child list."""

    def __init__(self, name=None):
        self.name = name
        self.handlers = {}
        self._hid = 0
        self.ticks = {}
        self._tid = 0
        self.opacity = 1.0
        self.ctx = FakeStyleContext()
        self.children = []
        self.parent = None
        self.destroyed = False
        self.shown = 0

    # signals
    def connect(self, signal, cb, *args):
        self._hid += 1
        self.handlers[self._hid] = (signal, cb, args)
        return self._hid

    def disconnect(self, hid):
        self.handlers.pop(hid, None)

    def emit(self, signal):
        for hid, (sig, cb, args) in list(self.handlers.items()):
            if sig == signal or (signal.startswith(sig + "::")):
                cb(self, *args)

    def destroy(self):
        self.destroyed = True
        self.emit("destroy")
        self.handlers.clear()
        self.ticks.clear()

    def in_destruction(self):
        # GTK's own guard: True from the moment disposal starts. The fake keeps
        # it True afterwards too, which is stricter, not looser.
        return self.destroyed

    # frame clock
    def add_tick_callback(self, cb, *_a):
        self._tid += 1
        self.ticks[self._tid] = cb
        return self._tid

    def remove_tick_callback(self, tid):
        self.ticks.pop(tid, None)

    def tick(self):
        for tid, cb in list(self.ticks.items()):
            if cb(self, None) is False:
                self.ticks.pop(tid, None)

    # widget bits
    def get_opacity(self):
        return self.opacity

    def set_opacity(self, v):
        self.opacity = float(v)

    def get_style_context(self):
        return self.ctx

    def get_children(self):
        return list(self.children)

    def add(self, child):
        self.children.append(child)
        child.parent = self

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)
            child.parent = None

    def get_parent(self):
        return self.parent

    def show_all(self):
        self.shown += 1


class FakeStack(FakeWidget):
    def __init__(self):
        FakeWidget.__init__(self)
        self.visible = None
        self.ttype = None
        self.tduration = None
        self.switches = []

    def set_transition_type(self, t):
        self.ttype = t

    def set_transition_duration(self, ms):
        self.tduration = ms

    def set_visible_child_name(self, name):
        self.visible = name
        self.switches.append(name)

    def get_visible_child_name(self):
        return self.visible


class FakeRevealer(FakeWidget):
    def __init__(self):
        FakeWidget.__init__(self)
        self.revealed = False
        self.child_revealed = False
        self.ttype = None
        self.tduration = None

    def set_transition_type(self, t):
        self.ttype = t

    def set_transition_duration(self, ms):
        self.tduration = ms

    def set_reveal_child(self, v):
        self.revealed = bool(v)
        if not self.tduration:
            self.finish()        # GTK arrives immediately with no transition

    def get_reveal_child(self):
        return self.revealed

    def get_child_revealed(self):
        return self.child_revealed

    def finish(self):
        """What GTK does at the end of the transition."""
        self.child_revealed = self.revealed
        self.emit("notify::child-revealed")


class FakeScheduler:
    """A stand-in for GLib's single-shot timeouts, so the hold on a highlight
    can be expired on demand with no main loop."""

    def __init__(self):
        self.sources = {}
        self._sid = 0
        self.added = 0

    def add(self, ms, fn):
        self._sid += 1
        self.added += 1
        self.sources[self._sid] = (ms, fn)
        return self._sid

    def remove(self, sid):
        self.sources.pop(sid, None)

    def fire_all(self):
        for sid, (_ms, fn) in list(self.sources.items()):
            if fn() is not True:
                self.sources.pop(sid, None)

    @property
    def live(self):
        return len(self.sources)


# --------------------------------------------------------------- policy -----
def set_policy(m, reduced, accel):
    os.environ["NB_ACCEL"] = "1" if accel else "0"
    m.set_reduced_motion(reduced)


def test_policy_matrix(m, t):
    """Article VI §4 as a truth table, as amended by PAPER-PHYSICS §0.5
    Amendment 1: the render path is not a motion input, so the software
    column must plan exactly what the accelerated column plans. Reduced
    Motion is the one off-switch, and it keeps a crossfade only where the
    caller explicitly asked for one — on either path."""
    # nbmotion needs a frame clock to allow anything; the fake gi shim in this
    # process provides one, but assert it rather than assume it.
    check("frame clock is available to the suite", m.frame_clock_available())

    set_policy(m, False, True)
    check("accel forward slides",
          t.stack_plan(t.FORWARD, t.PAGE) == (t.FORWARD, m.PAGE),
          str(t.stack_plan(t.FORWARD, t.PAGE)))
    check("accel back slides",
          t.stack_plan(t.BACK, t.PAGE) == (t.BACK, m.PAGE))
    check("accel crossfade crossfades",
          t.stack_plan(t.CROSSFADE, t.PAGE) == (t.CROSSFADE, m.PAGE))
    check("accel reveal slides down",
          t.revealer_plan(t.SLIDE_DOWN, t.SURFACE_IN)
          == (t.SLIDE_DOWN, m.SURFACE_IN))
    check("accel collapse slides up",
          t.revealer_plan(t.SLIDE_UP, t.SURFACE_OUT)
          == (t.SLIDE_UP, m.SURFACE_OUT))

    set_policy(m, False, False)
    check("software forward slides exactly like accel (Amendment 1)",
          t.stack_plan(t.FORWARD, t.PAGE) == (t.FORWARD, m.PAGE),
          str(t.stack_plan(t.FORWARD, t.PAGE)))
    check("software crossfade crossfades exactly like accel",
          t.stack_plan(t.CROSSFADE, t.PAGE) == (t.CROSSFADE, m.PAGE))
    check("software reveal slides exactly like accel",
          t.revealer_plan(t.SLIDE_DOWN, t.SURFACE_IN)
          == (t.SLIDE_DOWN, m.SURFACE_IN))

    set_policy(m, True, True)
    check("reduced motion never slides",
          t.stack_plan(t.FORWARD, t.PAGE) == (t.NONE, 0),
          str(t.stack_plan(t.FORWARD, t.PAGE)))
    check("reduced motion keeps an explicitly asked crossfade",
          t.stack_plan(t.CROSSFADE, t.PAGE) == (t.CROSSFADE, m.REDUCED_FADE))
    check("reduced motion downgrades an opted-in slide to a crossfade",
          t.stack_plan(t.FORWARD, t.PAGE, fade=True)
          == (t.CROSSFADE, m.REDUCED_FADE))
    check("reduced motion reveal is instant unless a fade is asked for",
          t.revealer_plan(t.SLIDE_DOWN) == (t.NONE, 0))
    check("reduced motion reveal keeps a requested crossfade",
          t.revealer_plan(t.CROSSFADE) == (t.CROSSFADE, m.REDUCED_FADE))

    set_policy(m, True, False)
    check("reduced motion on software keeps the asked crossfade too",
          t.stack_plan(t.CROSSFADE, t.PAGE) == (t.CROSSFADE, m.REDUCED_FADE))
    check("reduced motion on software never slides",
          t.stack_plan(t.FORWARD, t.PAGE) == (t.NONE, 0))
    check("explicit none is always still",
          t.stack_plan(t.NONE, t.PAGE) == (t.NONE, 0))

    set_policy(m, False, True)
    check("durations come from nbmotion tokens only",
          t.PAGE == m.PAGE and t.SURFACE_IN == m.SURFACE_IN
          and t.SELECT == m.SELECT)


# ------------------------------------------------------------- switching -----
def test_page_direction(t):
    """Direction is read off the SAME order the sidebar is built from, so the
    animation agrees with the list the user is looking at."""
    stack = FakeStack()
    p = t.PageSwitcher(stack, order=["a", "b", "c"])
    check("first switch has no history to slide from",
          p.direction_to("b") == t.CROSSFADE)
    p.switch("b")
    check("later in the order is forward", p.direction_to("c") == t.FORWARD)
    check("earlier in the order is back", p.direction_to("a") == t.BACK)
    check("the same page is not a movement",
          p.direction_to("b") == t.CROSSFADE)
    check("an unlisted page crossfades",
          p.direction_to("zz") == t.CROSSFADE)


def test_switch_equivalence(m, t):
    """The end state does not depend on the policy: only the getting there."""
    landed = {}
    for label, reduced, accel in (("accelerated", False, True),
                                  ("software", False, False),
                                  ("reduced", True, True)):
        set_policy(m, reduced, accel)
        stack = FakeStack()
        p = t.PageSwitcher(stack, order=["a", "b", "c"])
        p.switch("a")
        p.switch("c")
        landed[label] = (stack.visible, p.current(), p.target)
    check("instant equivalence: every policy lands on the same page",
          len(set(landed.values())) == 1, str(landed))
    check("and that page is the one asked for",
          landed["software"][0] == "c", str(landed))
    set_policy(m, False, False)
    stack = FakeStack()
    p = t.PageSwitcher(stack, order=["a", "b"])
    p.switch("b")
    check("software mode animates at the full token (Amendment 1)",
          stack.tduration == m.PAGE, str(stack.tduration))


def test_switch_generation(m, t):
    """Rapid clicks: the last one wins and nothing older lands on it."""
    set_policy(m, False, True)
    stack = FakeStack()
    p = t.PageSwitcher(stack, order=["a", "b", "c", "d"])
    tokens = [p.switch(n) for n in ("a", "b", "c", "d")]
    check("a burst leaves the stack on the last page asked for",
          stack.visible == "d", str(stack.switches))
    check("every generation is distinct", len(set(tokens)) == len(tokens))
    check("only the newest token is current",
          [p.is_current(tok) for tok in tokens] == [False, False, False, True])
    # The lazy-page-build pattern Settings uses: deferred work carries a token.
    built = []

    def deferred(name, tok):
        if p.is_current(tok):
            built.append(name)

    stale = p.switch("b")
    fresh = p.switch("c")
    deferred("b", stale)
    deferred("c", fresh)
    check("stale deferred work is dropped", built == ["c"], str(built))
    check("the target is set before the switch is made",
          p.target == "c" and p.current() == "c")


# -------------------------------------------------------------- revealer -----
def test_reveal(m, t):
    # Amendment 1: the software path animates identically — same duration,
    # same completion discipline, and it must clean its driver up the same
    # way (the old stanza asserted instant here).
    set_policy(m, False, False)
    r = FakeRevealer()
    fired = []
    ms = t.reveal(r, True, on_done=lambda ok: fired.append(ok))
    check("software reveal animates like accel (Amendment 1)",
          ms == m.SURFACE_IN)
    check("software reveal does not report before it finishes", fired == [])
    r.finish()
    check("software reveal lands revealed and reports once",
          r.revealed and r.child_revealed and fired == [True])

    set_policy(m, False, True)
    r = FakeRevealer()
    fired = []
    ms = t.reveal(r, True, on_done=lambda ok: fired.append(ok))
    check("accelerated reveal animates", ms == m.SURFACE_IN)
    check("accelerated reveal does not report before it finishes", fired == [])
    r.finish()
    check("accelerated reveal reports once it finishes", fired == [True])
    r.finish()
    check("a reveal completion fires exactly once", fired == [True])
    check("the completion handler disconnects itself",
          not any(sig.startswith("notify") for sig, _cb, _a
                  in r.handlers.values()), str(r.handlers))

    # Rapid toggle: the completion of the superseded reveal must not land.
    r = FakeRevealer()
    seen = []
    t.reveal(r, True, on_done=lambda ok: seen.append("open"))
    t.reveal(r, False, on_done=lambda ok: seen.append("close"))
    r.finish()
    check("a superseded reveal completion is dropped", seen == ["close"],
          str(seen))
    check("rapid toggle lands on the last state asked for", not r.revealed)

    # Destroy mid-reveal: nothing may call back into a dead widget.
    r = FakeRevealer()
    after = []
    t.reveal(r, True, on_done=lambda ok: after.append(ok))
    r.destroy()
    r.emit("notify::child-revealed")
    check("no reveal completion after destroy", after == [], str(after))


# --------------------------------------------------------------- replace -----
def test_replace_instant(m, t):
    # Amendment 1 removed "software = instant", so the synchronous path is
    # now reached by its one honest remaining route: no frame clock. The
    # contract this proves is unchanged — when no animation is possible the
    # swap is complete, destructive and reported before replace() returns,
    # which is what lets callers never branch on the policy.
    set_policy(m, False, False)
    real_clock = m.frame_clock_available
    m.frame_clock_available = lambda: False
    try:
        holder = FakeWidget("holder")
        old = FakeWidget("old")
        holder.add(old)
        new = FakeWidget("new")
        fired = []
        ms = t.replace(holder, new, on_done=lambda ok: fired.append(ok))
        check("clockless replace is instant", ms == 0)
        check("clockless replace swaps the child",
              holder.get_children() == [new], str(holder.get_children()))
        check("clockless replace destroys the old child", old.destroyed)
        check("clockless replace leaves opacity at exactly 1.0",
              holder.opacity == 1.0, str(holder.opacity))
        check("clockless replace reports completion synchronously",
              fired == [True])

        holder = FakeWidget()
        kept = FakeWidget("kept")
        holder.add(kept)
        t.replace(holder, FakeWidget("new"), destroy_old=False)
        check("destroy_old=False keeps the old child alive", not kept.destroyed)
        check("but still unparents it so it cannot affect layout",
              kept.get_parent() is None)
    finally:
        m.frame_clock_available = real_clock


def test_replace_animated(m, t, clock):
    """The crossfade path, driven frame by frame with no display and no wait."""
    set_policy(m, False, True)
    holder = FakeWidget("holder")
    old = FakeWidget("old")
    holder.add(old)
    new = FakeWidget("new")
    fired = []
    ms = t.replace(holder, new, on_done=lambda ok: fired.append(ok))
    check("accelerated replace runs for the whole token",
          ms == m.SURFACE_IN, str(ms))
    check("the swap does not happen before the midpoint",
          holder.get_children() == [old], str(holder.get_children()))
    clock.advance(m.SURFACE_IN / 2000.0)
    holder.tick()
    check("the swap happens at the midpoint",
          holder.get_children() == [new], str(holder.get_children()))
    check("the holder is invisible while the content changes",
          holder.opacity == 0.0, str(holder.opacity))
    check("replace does not report at the midpoint", fired == [])
    clock.advance(m.SURFACE_IN / 2000.0)
    holder.tick()
    check("the crossfade ends at exactly 1.0 opacity",
          holder.opacity == 1.0, str(holder.opacity))
    check("replace reports completion once it finishes", fired == [True])
    check("the crossfade leaves no live tick callback",
          not holder.ticks and m.live_drivers() == 0,
          "%s / %d" % (holder.ticks, m.live_drivers()))

    # A second replace mid-flight: the first must not land on the new content.
    holder = FakeWidget("holder")
    holder.add(FakeWidget("a"))
    seen = []
    t.replace(holder, FakeWidget("b"), on_done=lambda ok: seen.append("b"))
    final = FakeWidget("c")
    t.replace(holder, final, on_done=lambda ok: seen.append("c"))
    for _ in range(4):
        clock.advance(m.SURFACE_IN / 2000.0)
        holder.tick()
    names = [c.name for c in holder.get_children()]
    check("a superseded replace does not land its content",
          names == ["c"], str(names))
    check("a superseded replace does not report", seen == ["c"], str(seen))
    check("the final opacity is 1.0 after an interrupted replace",
          holder.opacity == 1.0, str(holder.opacity))

    # Destroy mid-crossfade: no callback, no leak, opacity is moot.
    holder = FakeWidget("holder")
    holder.add(FakeWidget("a"))
    after = []
    t.replace(holder, FakeWidget("b"), on_done=lambda ok: after.append(ok))
    holder.destroy()
    clock.advance(1.0)
    check("no replace callback after destroy", after == [], str(after))
    check("destroy mid-crossfade leaves no driver", m.live_drivers() == 0)


# ------------------------------------------------------------- highlight -----
def test_highlight(t, sched):
    w = FakeWidget()
    t.highlight(w, "justsaved")
    check("highlight adds the caller's class",
          "justsaved" in w.ctx.classes, str(w.ctx.classes))
    check("highlight schedules exactly one removal", sched.live == 1)
    check("highlight is pending", t.highlight_pending(w))
    sched.fire_all()
    check("the class comes off when the hold expires",
          "justsaved" not in w.ctx.classes, str(w.ctx.classes))
    check("the removal is single-shot", sched.live == 0)
    check("nothing is pending afterwards", not t.highlight_pending(w))

    # Hammering the same action must not stack timers.
    w = FakeWidget()
    for _ in range(5):
        t.highlight(w, "justsaved")
    check("repeated highlights never stack timers", sched.live == 1,
          str(sched.live))
    check("the class is still on after repeats", "justsaved" in w.ctx.classes)
    sched.fire_all()
    check("one expiry clears a repeated highlight",
          not w.ctx.classes and sched.live == 0, str(w.ctx.classes))

    # A different class replaces the first rather than layering over it.
    w = FakeWidget()
    t.highlight(w, "found")
    t.highlight(w, "changed")
    check("a new class replaces the old one",
          w.ctx.classes == {"changed"}, str(w.ctx.classes))
    check("and still owns only one timer", sched.live == 1)
    t.clear_highlight(w)
    check("clear_highlight removes the class now", not w.ctx.classes)
    check("clear_highlight cancels the pending removal", sched.live == 0)

    # Destroy: the source must die with the widget.
    w = FakeWidget()
    t.highlight(w, "justsaved")
    check("a highlight connects a destroy guard",
          any(sig == "destroy" for sig, _cb, _a in w.handlers.values()))
    w.destroy()
    check("destroy cancels the pending removal", sched.live == 0)
    check("nothing is pending after destroy", not t.highlight_pending(w))
    sched.fire_all()             # must be a no-op, not a call on a dead widget

    check("a highlight on nothing is harmless",
          t.highlight(None, "x") is False and t.highlight(w, "") is False)


def test_smooth_fraction(m, t, clock):
    """app.progress: the fill GLIDES between reported fractions -- linear,
    retargeting, landing exactly, instant-equivalent, and leaking no driver."""
    set_policy(m, False, True)           # accelerated: it should animate

    class FakeBar(FakeWidget):
        def __init__(self):
            FakeWidget.__init__(self)
            self._frac = 0.0

        def get_fraction(self):
            return self._frac

        def set_fraction(self, v):
            self._frac = float(v)

    # A glide from 0 to 0.5 over the FEEDBACK token, driven frame by frame.
    bar = FakeBar()
    fired = []
    ms = t.smooth_fraction(bar, 0.5, on_done=lambda ok: fired.append(ok))
    check("smooth_fraction runs for the feedback token", ms == m.FEEDBACK, str(ms))
    check("the fill does not jump to the target at once",
          bar.get_fraction() < 0.5, str(bar.get_fraction()))
    clock.advance(m.FEEDBACK / 2000.0)
    bar.tick()
    check("a progress fraction is LINEAR, not eased (~half-way at the midpoint)",
          0.20 < bar.get_fraction() < 0.30, str(bar.get_fraction()))
    check("smooth_fraction does not report mid-glide", fired == [])
    clock.advance(m.FEEDBACK / 1000.0)   # a full token more: comfortably past t=1
    bar.tick()
    check("the glide lands EXACTLY on the target",
          bar.get_fraction() == 0.5, str(bar.get_fraction()))
    check("smooth_fraction reports completion once", fired == [True])
    check("the glide leaves no live driver",
          not bar.ticks and m.live_drivers() == 0,
          "%s / %d" % (bar.ticks, m.live_drivers()))

    # RETARGET: a fresh report reuses the ONE scalar and heads to the newest
    # target from where the fill is, never restarting from zero.
    bar = FakeBar()
    t.smooth_fraction(bar, 0.5)
    sc1 = getattr(bar, "_nbt_frac", None)
    clock.advance(m.FEEDBACK / 2000.0)
    bar.tick()                           # ~0.25 on the way to 0.5
    t.smooth_fraction(bar, 0.9)          # retarget up to 0.9
    check("smooth_fraction keeps ONE scalar per bar",
          sc1 is not None and getattr(bar, "_nbt_frac", None) is sc1)
    for _ in range(3):
        clock.advance(m.FEEDBACK / 2000.0)
        bar.tick()
    check("a retargeted glide lands on the newest target",
          bar.get_fraction() == 0.9, str(bar.get_fraction()))
    check("retarget leaves no live driver", m.live_drivers() == 0)

    # A fraction outside [0, 1] is clamped, not handed to the bar.
    bar = FakeBar()
    t.smooth_fraction(bar, 1.4)
    for _ in range(3):
        clock.advance(m.FEEDBACK / 2000.0)
        bar.tick()
    check("smooth_fraction clamps above 1.0",
          bar.get_fraction() == 1.0, str(bar.get_fraction()))
    check("smooth_fraction on a None bar is safe", t.smooth_fraction(None, 0.5) == 0)

    # Non-finite worker/media values are indeterminate, never completion.
    for invalid in (float("nan"), float("inf"), float("-inf")):
        bar = FakeBar(); bar.set_fraction(0.4)
        rejected = []
        result = t.smooth_fraction(
            bar, invalid, on_done=lambda ok: rejected.append(ok))
        check("non-finite progress is rejected without painting completion",
              result == 0 and bar.get_fraction() == 0.4 and rejected == [False],
              "%r / %r / %r" % (invalid, bar.get_fraction(), rejected))

    # Destroy mid-glide: no callback fires, no driver leaks.
    bar = FakeBar()
    after = []
    t.smooth_fraction(bar, 0.9, on_done=lambda ok: after.append(ok))
    bar.destroy()
    clock.advance(1.0)
    check("no progress callback after destroy", after == [], str(after))
    check("destroy mid-glide leaves no driver", m.live_drivers() == 0)


# ---------------------------------------------------------------- static -----
def test_no_per_frame_timer():
    """Article VI §3: the frame clock, not a timer. The ONLY schedule in the
    module is the single-shot hold on a highlight, and it goes through the one
    indirection so there is one place to audit."""
    src = open(os.path.join(DE, "nbtransitions.py")).read()
    tree = ast.parse(src)
    banned = {"timeout_add", "timeout_add_seconds", "idle_add", "add_timeout"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in banned:
                owner = getattr(node.func.value, "id", "")
                hits.append("%s.%s" % (owner, node.func.attr))
    check("the module schedules through GLib exactly once",
          hits == ["GLib.timeout_add"], str(hits))
    fns = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    check("the schedule is behind one indirection",
          "_timeout_add" in fns and "_source_remove" in fns)
    check("every scheduled callback is single-shot",
          "return False             # single shot" in src)


def test_motion_character():
    """Animation belongs on EVERY state change, with a slight spring on arrival.

    The old ban this used to enforce -- colour/border only, no layout animation,
    nothing that overshoots -- was removed 2026-08-08: it was the campaign's
    invention, never the design intent. The only things out are 3D and liquid
    glass (they do not fit the paper style), and a transition may animate
    anything GTK can, layout included. What survives as a check is the CHARACTER:
    the arrival curve springs a LITTLE and lands EXACTLY, so 'lively' is a
    restrained spring, not a wild bounce (the tight bound is gated in
    tools/motion_selftest.py's easing checks)."""
    import nbmotion
    peak = max(nbmotion.ARRIVE(i / 100.0) for i in range(101))
    check("arrival springs, but only slightly (1.0 < peak <= 1.15)",
          1.0 < peak <= 1.15, "peak=%r" % peak)
    check("arrival lands exactly on its target",
          abs(nbmotion.ARRIVE(1.0) - 1.0) < 1e-9
          and abs(nbmotion.ARRIVE(0.0)) < 1e-9)


def test_settings_uses_the_primitive():
    """Settings must switch panes through the shared primitive — a second
    hand-rolled switch path is how the direction stops being coherent."""
    src = open(os.path.join(DE, "settings.py")).read()
    tree = ast.parse(src)
    check("settings imports nbtransitions",
          any(isinstance(n, ast.Import)
              and any(a.name == "nbtransitions" for a in n.names)
              for n in ast.walk(tree)))
    made = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "PageSwitcher"]
    check("settings builds one PageSwitcher", len(made) == 1, str(len(made)))
    order_arg = [k for n in made for k in n.keywords if k.arg == "order"]
    check("the switcher is given the sidebar order", len(order_arg) == 1)

    select = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_select":
            select = node
    check("settings still has _select", select is not None)
    if select is not None:
        attrs = [n.func.attr for n in ast.walk(select)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        check("_select switches through the primitive", "switch" in attrs,
              str(attrs))
        check("_select does not switch the stack by hand",
              "set_visible_child_name" not in attrs, str(attrs))
    direct = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "set_visible_child_name"]
    check("no hand-rolled pane switch is left in settings",
          len(direct) == 0, str(len(direct)))
    check("settings does not restyle its panes for the transition",
          "setpane" in src and src.count(".setpane {") == 1)


def test_grow_card(m, t, clock):
    """The anchored card (Article B): geometry is pure and exact, the origin
    discipline is enforced, and instant/animated both land on the target."""
    def run(host, seconds=0.30, steps=30):
        for _ in range(steps):
            clock.advance(seconds / steps)
            host.tick()
    # pure geometry
    a, b = (10.0, 20.0, 4.0, 3.0), (0.0, 0.0, 100.0, 80.0)
    check("interp_rect starts at the anchor", t.interp_rect(a, b, 0.0) == a)
    check("interp_rect ends at the target", t.interp_rect(a, b, 1.0) == b)
    mid = t.interp_rect(a, b, 0.5)
    check("interp_rect is the midpoint at 0.5",
          mid == (5.0, 10.0, 52.0, 41.5), str(mid))
    areas = [t.interp_rect(a, b, x)[2] * t.interp_rect(a, b, x)[3]
             for x in (0.0, 0.5, 1.0)]
    check("the card only grows", areas[0] < areas[1] < areas[2])

    # §B4 origin discipline: no anchor is a hard error, not a silent default
    raised = False
    try:
        t.GrowCard(FakeWidget()).grow(None, b)
    except ValueError:
        raised = True
    check("grow() refuses to present without an anchor", raised)

    # instant path (Reduced Motion): lands on the target, fires once
    set_policy(m, True, True)
    host = FakeWidget()
    fired = []
    card = t.GrowCard(host)
    card.grow(a, b, on_done=lambda ok: fired.append(ok))
    check("instant grow lands at the target and fires",
          card.rect() == b and fired == [True] and card.active)

    # animated path: damage is a bounded rectangle, never the whole host
    set_policy(m, False, True)
    host = FakeWidget()
    host.damage = []
    host.queue_draw_area = lambda x, y, w, h: host.damage.append((x, y, w, h))
    done = []
    card = t.GrowCard(host)
    card.grow(a, b, on_done=lambda ok: done.append(ok))
    run(host)
    check("animated grow completes at the target",
          done == [True] and card.rect() == b)
    biggest = max((w * h for (_x, _y, w, h) in host.damage), default=0)
    check("grow damage is bounded, never the whole host",
          0 < biggest <= 110 * 90, str(biggest))

    # retract collapses back toward the anchor and deactivates
    back = []
    card.retract(on_done=lambda ok: back.append(ok))
    run(host)
    check("retract returns to the anchor and deactivates",
          back == [True] and not card.active and card.rect() == a)

    # paint is inert while inactive (safe to leave connected)
    idle = t.GrowCard(FakeWidget())
    check("paint draws nothing when inactive", idle.paint(object()) is False)


def test_imports_without_gi():
    """The module must import and degrade to instant with no gi at all: tools/
    runs it headless and an app must never fail to import over a frame clock."""
    code = (
        "import sys, os\n"
        "sys.modules['gi'] = None\n"
        "sys.path.insert(0, %r)\n"
        "import nbtransitions as t\n"
        "assert t.Gtk is None and t.GLib is None\n"
        "assert t.stack_plan(t.FORWARD, t.PAGE) == (t.NONE, 0)\n"
        "assert t.revealer_plan(t.SLIDE_DOWN) == (t.NONE, 0)\n"
        "assert t.switch_page(None, 'x') == 0\n"
        "assert t.reveal(None, True) == 0\n"
        "assert t.replace(None, None) == 0\n"
        "assert t.highlight(None, 'x') is False\n"
        "print('ok')\n" % DE)
    env = dict(os.environ, NB_ACCEL="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env)
    check("imports and degrades to instant with no gi",
          r.returncode == 0 and "ok" in r.stdout, r.stderr.strip())


def test_byte_compiles():
    for name in ("nbtransitions.py", "settings.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile",
                            os.path.join(DE, name)],
                           capture_output=True, text=True)
        check("%s byte-compiles" % name, r.returncode == 0, r.stderr.strip())


def main():
    # An isolated NB_HOME: the machine running the suite must not decide the
    # answer, and nothing here may touch a real user's settings.
    home = tempfile.mkdtemp(prefix="nbtransitions-selftest-")
    os.environ["NB_HOME"] = home
    os.environ["NB_ACCEL"] = "0"

    import nbmotion as m
    m.reload_prefs()
    if m.Gtk is None:
        # No gi on this host: the policy tests below assert what an accelerated
        # session does, which needs a frame clock to exist. Supply the one
        # attribute nbmotion checks for, so the suite tests the module rather
        # than the host.
        class _W:
            def add_tick_callback(self, *_a):
                return 0

        class _Shim:
            Widget = _W
        m.Gtk = _Shim
    clock = FakeClock()
    m.time = clock               # every Scalar created after this uses it
    import nbtransitions as t

    sched = FakeScheduler()
    t._timeout_add = sched.add
    t._source_remove = sched.remove

    test_policy_matrix(m, t)
    test_page_direction(t)
    test_switch_equivalence(m, t)
    test_switch_generation(m, t)
    test_reveal(m, t)
    test_replace_instant(m, t)
    test_replace_animated(m, t, clock)
    test_highlight(t, sched)
    test_grow_card(m, t, clock)
    test_smooth_fraction(m, t, clock)
    test_no_per_frame_timer()
    test_motion_character()
    test_settings_uses_the_primitive()
    test_imports_without_gi()
    test_byte_compiles()

    check("the suite leaves no live driver", m.live_drivers() == 0)
    check("the suite leaves no live source", sched.live == 0)

    print("transitions_selftest: %d checks" % CHECKS[0])
    if FAILURES:
        print("FAIL (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        print("RESULT: FAILED")
        return 1
    print("PASS")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
