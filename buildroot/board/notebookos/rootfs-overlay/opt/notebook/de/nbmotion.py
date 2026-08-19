#!/usr/bin/env python3
"""nbmotion — the shared motion foundation for Notebook OS.

Article VI of docs/NOTEBOOKOS-INTERACTION-CONSTITUTION.md in one module: the
canonical duration and easing tokens, the policy that decides whether a
transition may move at all, and a retargetable scalar animation that is driven
by the GTK frame clock rather than by a timer.

Three decisions are load-bearing and are the reason this exists once instead of
once per app:

1. **The frame clock, not `GLib.timeout_add`.** A timeout is a wall-clock
   promise the main loop cannot keep under load: it fires late, the widget
   redraws between frames, and the animation costs more than it shows.
   `Gtk.Widget.add_tick_callback` is called once per frame the compositor is
   actually going to draw, so motion is frame-paced. One tick callback is shared
   by every animation on a widget (see `_Driver`) — a per-widget-per-property
   source is the design this module exists to prevent.

2. **`time.monotonic()` for the interpolation.** The frame clock's own
   timestamps are microseconds from an unspecified origin and are not
   comparable across widgets; a clock change (the installer sets the system
   clock) must never make a 160ms transition run for an hour. Frames come from
   the clock; the *value* comes from monotonic time.

3. **Instant is a first-class outcome, not a failure.** Under Reduced Motion,
   under software rendering, and on any widget that cannot give us a frame
   clock, a transition lands on EXACTLY the end state it would have animated to
   — synchronously, before the call returns, with the completion callback run.
   Callers therefore never need a second code path, and that equivalence is
   what tools/motion_selftest.py gates.

The module imports and works with no display, no GTK and no settings store; in
that state every animation degrades to instant. Nothing here draws, lays out or
restyles anything: it produces numbers and calls back.

    import nbmotion

    # one-shot, self-cleaning
    nbmotion.fade_to(card, 1.0, nbmotion.SURFACE_IN)

    # a value the caller owns and paints itself
    s = nbmotion.Scalar(view, 0.0, on_frame=lambda v: view.queue_draw())
    s.animate_to(1.0, nbmotion.PAGE, nbmotion.EASE_IN_OUT)
    s.animate_to(0.0)          # retargets from wherever it is now
"""
import json
import os
import time

# GTK is optional ON PURPOSE. tools/ runs this module headless, and an app that
# fails to import because a frame clock is unavailable would be a far worse
# outcome than one that snaps. Everything below checks `Gtk is None`.
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except Exception:                                                 # noqa: BLE001
    Gtk = None


# --------------------------------------------------------------------------
# Duration tokens (milliseconds) — Article VI §2
# --------------------------------------------------------------------------
# The band for each class is in the constitution; these are the single value
# each band resolves to, so two surfaces that arrive together arrive together.
# An app passes a TOKEN, never a number: a literal 250 in an app is how a house
# style becomes seventeen house styles.
INSTANT = 0            # the end state, now
FEEDBACK = 90          # hover / press acknowledgement          (band 70-100)
SELECT = 120           # selection / focus transition           (band 100-140)
SURFACE_IN = 160       # menu, inline card, disclosure arriving  (band 140-180)
SURFACE_OUT = 160      # ...and departing                        (band 140-180)
PAGE = 200             # page / document transition             (band 180-220)

#: The restrained crossfade Reduced Motion is allowed to keep (Article VI §4)
#: where an instant swap would be incomprehensible. Deliberately the shortest
#: token in the system: it is a legibility aid, not a transition.
REDUCED_FADE = FEEDBACK

DURATIONS = {
    "instant": INSTANT,
    "feedback": FEEDBACK,
    "select": SELECT,
    "surface-in": SURFACE_IN,
    "surface-out": SURFACE_OUT,
    "page": PAGE,
}

#: Documented band per token, inclusive, in ms — the gate in
#: tools/motion_selftest.py reads this rather than re-typing the constitution.
DURATION_BANDS = {
    "instant": (0, 0),
    "feedback": (70, 100),
    "select": (100, 140),
    "surface-in": (140, 180),
    "surface-out": (140, 180),
    "page": (180, 220),
}


# --------------------------------------------------------------------------
# Easing tokens — Article VI §2
# --------------------------------------------------------------------------
# Arrival has a slight SPRING: a small, bounded overshoot that settles back onto
# the target, so motion feels lively rather than mechanical. Every curve lands
# EXACTLY on its endpoints; the little excursion past the target in between is
# the life. A big bounce, an elastic wobble, 3D or liquid glass stay OUT (they do
# not fit the paper style) — but a restrained spring is the house feel, and
# animation belongs on every state change, not a whitelist of a few.
def _clamp01(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t


def linear(t):
    """No easing. For a value that is already a physical quantity (a playhead,
    an elapsed-time bar) where any curve would be a lie about the data."""
    return _clamp01(t)


def ease_out(t):
    """Arrival. Fast first, settling — the thing is already here and is coming
    to rest. The default for anything appearing, selecting or focusing."""
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in(t):
    """Departure. Slow first, accelerating away. Only for something leaving."""
    t = _clamp01(t)
    return t * t * t


def ease_in_out(t):
    """Movement of something already on screen: it is not arriving or leaving,
    it is going somewhere, so it starts and stops gently at both ends."""
    t = _clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def ease_out_back(t):
    """Arrival with a slight SPRING: fast in, a small overshoot past the target,
    then settles onto it. Lands EXACTLY at the endpoints (0 at 0, 1 at 1); the
    ~7% excursion past 1.0 in between is the life. The default for anything
    ARRIVING. Where the value is an opacity the overshoot is invisible (GTK
    clamps it, so a fade reads as a clean ease-out); where it is a position or a
    size it reads as the spring."""
    t = _clamp01(t)
    s = 1.20158
    u = t - 1.0
    return 1.0 + (s + 1.0) * u * u * u + s * u * u


LINEAR = linear
EASE_OUT = ease_out
EASE_IN = ease_in
EASE_IN_OUT = ease_in_out
EASE_OUT_BACK = ease_out_back

EASINGS = {
    "linear": linear,
    "ease-out": ease_out,
    "ease-in": ease_in,
    "ease-in-out": ease_in_out,
    "ease-out-back": ease_out_back,
}

#: Which curve each class of change takes, so a caller picks a MEANING.
#: ARRIVE springs, so it is for a GEOMETRIC arrival — a position or a scale that
#: can meaningfully overshoot its target and settle back. Do NOT use it for an
#: opacity or colour fade: opacity clamps at 1.0, so an overshoot is invisible at
#: best and a held-too-early frame at worst. Fades stay on EASE_OUT (the default
#: on fade_to/Track/animate), which is why those defaults are EASE_OUT, not ARRIVE.
ARRIVE = ease_out_back        # a slight spring: lively arrival, settles onto target
DEPART = ease_in
MOVE = ease_in_out


# --------------------------------------------------------------------------
# Policy — Article VI §4, as amended by PAPER-PHYSICS §0.5 Amendment 1
# --------------------------------------------------------------------------
# Resolution order is fixed: Reduced Motion, then the frame clock, then the
# token. NB_ACCEL is NO LONGER a motion input: the original gate reasoned
# that six dropped frames read as a struggling computer, and the amendment
# reverses the conclusion — the render path is the problem to solve (Article
# F damage-limiting, the GPU source restore), not a reason for most machines
# to live without the motion language. Reduced Motion is the one human
# off-switch. A "still" resolution yields 0, and a 0 lands on the same end
# state as an animation would.
_REDUCED = None                 # None = not read yet; cached per process


def _store_path():
    home = os.environ.get("NB_HOME") or os.path.expanduser("~")
    return os.path.join(home, ".config", "notebook", "settings.json")


def reduced_motion():
    """True when the user has turned Reduced Motion on in Settings.

    The same store, the same read-once-per-process shape and the same
    never-raise contract as `nbapp.a11y_prefs()`: this is on the import path of
    every app, so it is one open() of a small JSON file with no display
    connection and no GTK call. A missing, unreadable or malformed store means
    False — motion is the assumption that is never harmful, and a person who
    needs it off has said so."""
    global _REDUCED
    if _REDUCED is None:
        value = False
        try:
            with open(_store_path()) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                value = data.get("reduced_motion") is True
        except Exception:                                         # noqa: BLE001
            pass
        _REDUCED = value
    return _REDUCED


def set_reduced_motion(value):
    """Apply a new choice to THIS process, live (Settings calls it on toggle).

    Like the text-size and contrast switches it cannot reach other running apps
    — there is no session bus here — and the Settings page says so. It does not
    write the store; Settings owns that file."""
    global _REDUCED
    _REDUCED = bool(value)
    _apply_gtk_animations()
    return _REDUCED


def reload_prefs():
    """Drop the cached preference so the next read hits the store again."""
    global _REDUCED
    _REDUCED = None


def accelerated():
    """True when session.sh proved Mesa can actually drive this machine.

    NOT a motion input since PAPER-PHYSICS §0.5 Amendment 1 — policy() does
    not consult it. It remains for RENDER-PATH decisions that genuinely
    depend on the hardware: compositor gating, frame-budget selection, and
    the frame-pacing harness reporting which path it measured."""
    return os.environ.get("NB_ACCEL", "0").strip() == "1"


def frame_clock_available():
    """True when this build of GTK/PyGObject can give us a tick callback.

    Checked rather than assumed: `add_tick_callback` is GTK 3.8+, and if it is
    ever missing the honest outcome is instant motion, not an AttributeError
    inside a button handler."""
    return bool(Gtk is not None and hasattr(Gtk.Widget, "add_tick_callback"))


def policy(duration=SURFACE_IN, fade=False):
    """The effective duration in ms for a transition that WANTS `duration`.

    `fade=True` says the caller's change would be incomprehensible as an
    instant swap (a whole surface replacing another) and that a restrained
    crossfade is an acceptable substitute. It is the ONLY thing that survives
    Reduced Motion.

    NB_ACCEL is deliberately not consulted (PAPER-PHYSICS §0.5 Amendment 1):
    the same motion language runs on both render paths, and the software
    path's cost is answered by damage-limited drawing (Article F), never by
    switching the language off for the majority of machines.

    Returns 0 for "do it now". A caller never branches on this: `Scalar`
    already lands on the end state synchronously when it is 0."""
    try:
        duration = int(duration)
    except Exception:                                             # noqa: BLE001
        duration = 0
    if duration <= 0:
        return 0
    if reduced_motion():
        return REDUCED_FADE if fade else 0
    if not frame_clock_available():
        return 0
    return duration


def policy_state():
    """policy()'s two inputs plus the render-path fact, for a debug line or a
    test — one dict so a failure says WHICH input made the system still.
    `accelerated` stays in the dict although it no longer feeds policy():
    the frame-pacing harness reports which path it measured."""
    return {"reduced_motion": reduced_motion(),
            "accelerated": accelerated(),
            "frame_clock": frame_clock_available()}


def _apply_gtk_animations():
    """Keep GTK's own theme transitions in step with our policy.

    The Papertone theme's 90ms state feedback runs through GTK's animation
    machinery, not through this module. If Reduced Motion silenced only
    nbmotion the switch would be a half-truth on every button in the OS.
    Reduced Motion is the only input here — see Amendment 1."""
    if Gtk is None:
        return
    try:
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-enable-animations",
                                  not reduced_motion())
    except Exception:                                             # noqa: BLE001
        pass                     # a missing setting must never stop an app


# --------------------------------------------------------------------------
# The animation itself
# --------------------------------------------------------------------------
class _Completion:
    """A callback that fires exactly once, with True for completed and False
    for cancelled or retargeted.

    The once-ness is the whole point. A transition can end three ways — it
    finishes, it is retargeted mid-flight, or its widget goes away — and two of
    those can happen in the same frame. A completion that runs twice is how a
    dialog gets torn down while it is being torn down."""
    __slots__ = ("_cb", "_fired")

    def __init__(self, cb):
        self._cb = cb
        self._fired = False

    @property
    def fired(self):
        return self._fired

    def fire(self, completed):
        if self._fired:
            return False
        self._fired = True
        if self._cb is None:
            return True
        try:
            self._cb(bool(completed))
        except Exception:                                         # noqa: BLE001
            # A broken completion callback is an app bug; it must not leave the
            # engine mid-frame with a live tick callback and no way out.
            pass
        return True


class Track:
    """The pure interpolation: from, to, start time, duration, curve.

    Deliberately free of GTK, widgets and callbacks so the maths — including
    retargeting, which is the part that is easy to get wrong — can be tested
    with nothing but a number for a clock."""
    __slots__ = ("frm", "to", "t0", "dur", "easing")

    def __init__(self, value=0.0, easing=EASE_OUT):
        self.frm = float(value)
        self.to = float(value)
        self.t0 = 0.0
        self.dur = 0.0
        self.easing = easing

    def retarget(self, to, now, duration_ms, easing=None):
        """Aim at a new value FROM WHERE THE VALUE IS, not from where the last
        animation started (Article VI §3). This is why a second click on a
        disclosure arrow reverses the arrow instead of snapping it back to the
        top and replaying — and why repeated clicks cannot stack."""
        self.frm = self.value_at(now)
        self.to = float(to)
        self.t0 = float(now)
        self.dur = max(0.0, float(duration_ms) / 1000.0)
        if easing is not None:
            self.easing = easing
        return self

    def value_at(self, now):
        if self.dur <= 0.0:
            return self.to
        t = (float(now) - self.t0) / self.dur
        if t >= 1.0:
            return self.to               # EXACTLY the end state, not to-epsilon
        if t <= 0.0:
            return self.frm
        return self.frm + (self.to - self.frm) * self.easing(t)

    def done_at(self, now):
        return self.dur <= 0.0 or (float(now) - self.t0) >= self.dur


class Scalar:
    """One animated number attached to one widget.

    `on_frame(value)` is called for every frame the value changes, including
    the final one, and including the single synchronous call an instant
    transition makes. The caller decides what a value MEANS — an opacity, a
    cairo offset inside a fixed allocation, a progress fraction. It must not
    mean a width, a height, a margin or a padding: animating an allocation
    re-runs layout every frame and is the one thing the theme forbids outright.

    `manual=True` gives an animation with no widget and no frame clock, driven
    by `advance(now)`. That is how the headless tests exercise the retarget and
    cancellation paths, and it is also the escape hatch for a caller that is
    already inside its own tick callback.
    """

    def __init__(self, widget=None, value=0.0, on_frame=None,
                 duration=SURFACE_IN, easing=EASE_OUT, fade=False,
                 manual=False, clock=None):
        self.widget = widget
        self.on_frame = on_frame
        self.duration = duration
        self.fade = fade
        self.manual = bool(manual)
        self.clock = clock or time.monotonic
        self._track = Track(value, easing)
        self._value = float(value)
        self._done = None            # live _Completion while running
        self._running = False
        self._driver = None
        self._trace = None           # [token_ms, t0, t1, ...] under trace

    # ---- state ----
    @property
    def value(self):
        return self._value

    @property
    def target(self):
        return self._track.to

    @property
    def running(self):
        return self._running

    # ---- driving ----
    def animate_to(self, target, duration=None, easing=None, on_done=None,
                   fade=None):
        """Move to `target`. Interruptible, retargeting, one-shot completion.

        Returns self. If policy says still — Reduced Motion, software
        rendering, no frame clock, a zero token — the value IS `target` and
        `on_done(True)` has already run by the time this returns."""
        duration = self.duration if duration is None else duration
        fade = self.fade if fade is None else fade
        eff = policy(duration, fade)

        # Whatever was in flight ends here, as cancelled, before anything else
        # can observe the new target.
        self._finish_pending(False)

        if eff <= 0 or (not self.manual and not self._can_tick()):
            self._track.retarget(target, self.clock(), 0, easing)
            self._running = False
            self._detach()
            self._set(float(target))
            _Completion(on_done).fire(True)
            return self

        self._track.retarget(target, self.clock(), eff, easing)
        self._done = _Completion(on_done)
        self._running = True
        if _tracing():
            self._trace = [float(eff), self.clock()]
        if not self.manual:
            self._attach()
        return self

    def jump_to(self, value):
        """Land on `value` now. Anything in flight is cancelled, not
        completed: nobody asked for that target."""
        self._finish_pending(False)
        self._track.retarget(value, self.clock(), 0)
        self._running = False
        self._set(float(value))
        return self

    def settle(self):
        """Land on the CURRENT target now and report completion. What an
        unrealize does — a widget left frozen at 0.37 opacity is a worse
        outcome than one that simply arrived early."""
        if not self._running:
            return self
        self._running = False
        self._set(self._track.to)
        self._finish_pending(True)
        return self

    def cancel(self):
        """Stop where the value is. The completion callback runs once with
        False; a second cancel does nothing."""
        was = self._running
        self._running = False
        self._finish_pending(False)
        self._detach()
        return was

    def advance(self, now=None):
        """One frame. Returns True while the animation is still live.

        The single place a value moves, shared by the frame-clock driver and by
        `manual=True` callers, so there is one interpolation path to test."""
        if not self._running:
            return False
        now = self.clock() if now is None else now
        done = self._track.done_at(now)
        self._set(self._track.value_at(now))
        if not self._running:
            # _set contains a bad on_frame callback by stopping this scalar.
            # Report that post-callback state to manual drivers immediately.
            return False
        if self._trace is not None:
            self._trace.append(float(now))
            if done:
                TRACE.append(tuple(self._trace))
                self._trace = None
        if done:
            self._running = False
            self._finish_pending(True)
            return False
        return True

    # ---- internals ----
    def _set(self, value):
        value = float(value)
        changed = value != self._value
        self._value = value
        if changed and self.on_frame is not None:
            try:
                self.on_frame(value)
            except Exception:                                     # noqa: BLE001
                # A raising frame callback would otherwise take the tick
                # callback down with it and freeze every animation on the
                # widget; stop THIS one instead.
                self._running = False
                self._finish_pending(False)
                self._detach()

    def _finish_pending(self, completed):
        done, self._done = self._done, None
        if done is not None:
            done.fire(completed)

    def _can_tick(self):
        w = self.widget
        return bool(frame_clock_available() and w is not None
                    and hasattr(w, "add_tick_callback"))

    def _attach(self):
        if self._driver is None:
            self._driver = _driver_for(self.widget)
        if self._driver is not None:
            self._driver.add(self)
        else:                       # no driver, no frames: do not lie about it
            self.settle()

    def _detach(self):
        d, self._driver = self._driver, None
        if d is not None:
            d.remove(self)


class _Driver:
    """One tick callback per widget, shared by every animation on it.

    Article VI §3 bans the one-source-per-widget-per-property design; this is
    the object that makes the ban cheap to obey. It also owns the lifecycle:
    the destroy and unrealize handlers here are the reason no caller has to
    remember to cancel anything, and the reason a frame callback cannot outlive
    the widget it paints."""

    def __init__(self, widget):
        self.widget = widget
        self.anims = []
        self._tick = None
        self._in_tick = False
        self._handlers = []
        for signal, handler in (("destroy", self._on_destroy),
                                ("unrealize", self._on_unrealize)):
            try:
                self._handlers.append(widget.connect(signal, handler))
            except Exception:                                     # noqa: BLE001
                pass

    # ---- membership ----
    def add(self, scalar):
        if scalar not in self.anims:
            self.anims.append(scalar)
        self._ensure_tick()

    def remove(self, scalar):
        if scalar in self.anims:
            self.anims.remove(scalar)
        if not self.anims:
            self._stop_tick()
            self._disconnect_handlers()
            _drop_driver(self.widget, self)

    # ---- the frame clock ----
    def _ensure_tick(self):
        if self._tick is not None or not self.anims:
            return
        try:
            self._tick = self.widget.add_tick_callback(self._on_tick)
        except Exception:                                         # noqa: BLE001
            # No frame clock on this widget after all: land on the end state
            # rather than leave a caller waiting for frames that never come.
            self._tick = None
            self._release(settle=True)

    def _stop_tick(self):
        # Never from inside our own tick callback: the return value there is
        # what removes the source, and removing it twice is a GTK warning at
        # best. _on_tick clears _tick itself.
        if self._in_tick:
            return
        tick, self._tick = self._tick, None
        if tick is None:
            return
        try:
            self.widget.remove_tick_callback(tick)
        except Exception:                                         # noqa: BLE001
            pass                     # already gone with the widget: not a fault

    def _on_tick(self, _widget, _clock):
        # monotonic time, once per frame, shared by every animation on this
        # widget so two values started together stay together.
        now = time.monotonic()
        self._in_tick = True
        try:
            for scalar in list(self.anims):
                if not scalar.advance(now):
                    # advance() returning False means THIS animation ended —
                    # but its completion callback runs inside that call, and a
                    # completion is allowed to start the next animation on the
                    # same scalar (a crossfade's fade-in chains off its
                    # fade-out). Such a scalar has already re-added itself
                    # here, so dropping it unconditionally would strand a live
                    # animation with no frame clock and it would never finish.
                    if scalar.running:
                        continue
                    if scalar in self.anims:
                        self.anims.remove(scalar)
                    scalar._driver = None
        finally:
            self._in_tick = False
        if not self.anims:
            self._tick = None        # returning False removes it; do not re-remove
            self._disconnect_handlers()
            _drop_driver(self.widget, self)
            return False             # GLib.SOURCE_REMOVE
        return True                  # GLib.SOURCE_CONTINUE

    # ---- lifecycle ----
    def _on_destroy(self, _widget):
        # The widget is going away: cancel, do NOT settle. Calling a frame
        # callback that paints a dying widget is the crash this exists to
        # prevent. GTK drops the tick callback with the widget, so forget the
        # id first rather than removing it from under itself.
        self._tick = None
        self._release(settle=False)

    def _on_unrealize(self, _widget):
        # Unrealize is not death — a reparent unrealizes and realizes again —
        # but the frame clock goes with it, so finish now rather than freeze
        # halfway. End state, once, and the animation is over.
        self._stop_tick()
        self._release(settle=True)

    def _release(self, settle):
        anims, self.anims = self.anims, []
        for scalar in anims:
            scalar._driver = None
            if settle:
                scalar.settle()
            else:
                scalar._running = False
                scalar._finish_pending(False)
        _drop_driver(self.widget, self)
        self._disconnect_handlers()

    def _disconnect_handlers(self):
        for hid in self._handlers:
            try:
                self.widget.disconnect(hid)
            except Exception:                                     # noqa: BLE001
                pass
        self._handlers = []


# Keyed by id(): a Gtk.Widget is hashable, but the driver holds a reference to
# its widget for exactly as long as it is in this table, so the id cannot be
# reused while the entry is live. Entries are removed by the last animation
# finishing or by destroy/unrealize, never by a timer.
_DRIVERS = {}


def _driver_for(widget):
    if widget is None:
        return None
    d = _DRIVERS.get(id(widget))
    if d is None:
        d = _Driver(widget)
        _DRIVERS[id(widget)] = d
    return d


def _drop_driver(widget, driver=None):
    key = id(widget)
    if driver is None or _DRIVERS.get(key) is driver:
        _DRIVERS.pop(key, None)


def live_drivers():
    """How many widgets currently hold a tick callback. The cleanup gate in
    tools/motion_selftest.py asserts this returns to zero."""
    return len(_DRIVERS)


# --------------------------------------------------------------------------
# Small reusable helpers
# --------------------------------------------------------------------------
def animate(widget, on_frame, start, end, duration=SURFACE_IN, easing=EASE_OUT,
            fade=False, on_done=None):
    """One-shot: run `on_frame(value)` from `start` to `end` and forget it.

    Returns the Scalar so a caller that might need to interrupt can keep it;
    a caller that cannot be interrupted may throw it away, because the driver
    owns the cleanup."""
    s = Scalar(widget, start, on_frame=on_frame, duration=duration,
               easing=easing, fade=fade)
    return s.animate_to(end, duration, easing, on_done, fade)


def fade_to(widget, opacity, duration=SURFACE_IN, easing=EASE_OUT,
            on_done=None):
    """Animate a widget's opacity.

    Opacity is the one property that is always safe to animate here: it does
    not re-run layout, unlike width/height/margin/padding, which the Papertone
    theme forbids animating for exactly that reason. Under Reduced Motion this
    is the substitution the policy allows, so it passes fade=True."""
    if widget is None:
        return None
    state = getattr(widget, "_nbmotion_opacity", None)
    if state is None:
        try:
            start = float(widget.get_opacity())
        except Exception:                                         # noqa: BLE001
            start = 1.0

        def _apply(v, w=widget):
            try:
                w.set_opacity(v)
            except Exception:                                     # noqa: BLE001
                pass

        state = Scalar(widget, start, on_frame=_apply, fade=True)
        try:
            widget._nbmotion_opacity = state
        except Exception:                                         # noqa: BLE001
            pass                # a wrapper that refuses attributes: still works
    return state.animate_to(opacity, duration, easing, on_done, True)


def cancel_all(widget):
    """Stop every animation on a widget where it stands. For a caller that is
    about to replace the content underneath it."""
    d = _DRIVERS.get(id(widget))
    if d is None:
        return 0
    n = len(d.anims)
    for scalar in list(d.anims):
        scalar.cancel()
    return n


# --------------------------------------------------------------------------
# Article F — affordability (PAPER-PHYSICS §F1): damage-limited motion,
# static-layer caching, and the frame trace the pacing gate reads.
# --------------------------------------------------------------------------
# Amendment 1 put the full motion language on the software path; this section
# is the price. A fullscreen queue_draw() at 60 Hz on a CPU rasteriser is
# unaffordable, so an animation invalidates the SMALLEST rectangle that
# changed and everything that is not moving is painted from a cached surface.

TRACE = []          # (token_ms, t0, frame_t1, frame_t2, ...) per finished run


def _tracing():
    return bool(os.environ.get("NB_MOTION_TRACE"))


def trace_drain():
    """Return and clear the recorded frame traces (the pacing harness's
    read side). Empty unless NB_MOTION_TRACE was set when the animation
    STARTED — tracing is decided per run, not per frame."""
    out = TRACE[:]
    del TRACE[:]
    return out


class Damaged(Scalar):
    """A Scalar that invalidates only the moving layer's rectangle (§F1).

    `rect_for(value) -> (x, y, w, h)` describes where the moving layer sits
    at a value, in widget coordinates. Each frame invalidates the UNION of
    the previous and current rectangles — the paint that erases the old
    position and draws the new one — and never the whole widget. `pad`
    covers anti-aliased edges and the one-pixel border pair.

    The caller's own `on_frame` (model update) runs first, then the damage,
    so the draw handler that follows sees the new value. The draw handler
    pairs with `LayerCache` for everything that is NOT moving.
    """

    def __init__(self, widget=None, rect_for=None, pad=2, on_frame=None,
                 **kw):
        self._rect_for = rect_for
        self._pad = int(pad)
        self._prev_rect = None
        self._user_frame = on_frame
        super().__init__(widget=widget, on_frame=self._damage_frame, **kw)

    def _damage_frame(self, value):
        if self._user_frame is not None:
            self._user_frame(value)
        w, fn = self.widget, self._rect_for
        if w is None or fn is None:
            return
        r = fn(value)
        if r is None:
            return
        p = self._pad
        cur = (int(r[0]) - p, int(r[1]) - p,
               int(r[2]) + 2 * p, int(r[3]) + 2 * p)
        x, y, wd, ht = cur
        prev = self._prev_rect
        if prev is not None:
            x2, y2 = min(x, prev[0]), min(y, prev[1])
            wd = max(x + wd, prev[0] + prev[2]) - x2
            ht = max(y + ht, prev[1] + prev[3]) - y2
            x, y = x2, y2
        self._prev_rect = cur
        try:
            w.queue_draw_area(x, y, wd, ht)
        except Exception:                                         # noqa: BLE001
            pass         # a widget with no draw queue has nothing to repaint


class LayerCache:
    """Static content rendered once to an ImageSurface (§F1, bullet two).

    The draw-handler pattern:

        cache = nbmotion.LayerCache(draw_static)     # draw_static(cr, w, h)

        def on_draw(widget, cr):
            cache.paint(widget, cr)                  # cached, cheap
            draw_moving_layer(cr, anim.value)        # the only per-frame work

    Call `invalidate()` when the static content itself changes; an
    allocation change re-renders automatically (size mismatch). The cache
    imports cairo lazily so a host without gi/cairo can still import this
    module (the same contract as everything else here).
    """

    def __init__(self, draw_static):
        self._draw = draw_static
        self._surface = None
        self._size = (0, 0)
        self.renders = 0             # observable by tests; costs nothing

    def invalidate(self):
        self._surface = None

    def paint(self, widget, cr):
        try:
            aw = int(widget.get_allocated_width())
            ah = int(widget.get_allocated_height())
        except Exception:                                         # noqa: BLE001
            return False
        if aw <= 0 or ah <= 0:
            return False
        if self._surface is None or self._size != (aw, ah):
            try:
                import cairo
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, aw, ah)
                ctx = cairo.Context(surface)
            except Exception:                                     # noqa: BLE001
                return False
            try:
                self._draw(ctx, aw, ah)
            except Exception:                                     # noqa: BLE001
                pass       # a broken static painter must not kill the frame
            self._surface, self._size = surface, (aw, ah)
            self.renders += 1
        try:
            cr.set_source_surface(self._surface, 0, 0)
            cr.paint()
        except Exception:                                         # noqa: BLE001
            return False
        return True
