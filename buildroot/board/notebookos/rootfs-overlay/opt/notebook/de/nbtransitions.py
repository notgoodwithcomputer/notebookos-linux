#!/usr/bin/env python3
"""nbtransitions — the shared container transitions every app reaches for.

de/nbmotion.py owns *time*: the duration tokens, the easing curves, the policy
that decides whether anything may move, and a retargetable scalar driven by the
frame clock. This module owns *containers*: it turns those tokens into the
common container-level changes, so that no app has to reason about
`Gtk.StackTransitionType`, Reduced Motion and NB_ACCEL on its own and get a
different answer each time.

    page switch      a Gtk.Stack changing page, with a DIRECTION
    inline reveal    a Gtk.Revealer opening or closing a disclosure
    content replace  one widget crossfading into another inside a holder
    result highlight a CSS class held briefly on the thing that just changed

These are the shared primitives, not a whitelist of the only motion allowed. The
design rule is the opposite of restrictive: animate every state change, keeping
the lively slight-spring character. The only things out of bounds are 3D and
liquid glass, because they do not fit the paper style. An app that needs a
transition these four do not cover writes its own on nbmotion's Scalar/Track and
the same policy — this module just spares it the boilerplate for the common ones.

Four rules run through every function here:

1. **GTK does the moving, we do the deciding.** A page switch is
   `Gtk.Stack`'s own animation and a reveal is `Gtk.Revealer`'s own: one
   internal frame-clock animation inside the toolkit, in C, with no Python
   running per frame. What this module contributes is the *policy* — which
   transition type, how long, or none at all. Layout may animate — a Revealer
   sliding its own allocation open is exactly the widget doing the job it exists
   for — but prefer letting GTK drive it over hand-rolling a per-frame width or
   margin tween in Python, which is what invites jank on the software path.

2. **Instant is equivalent, not degraded.** When policy says still — Reduced
   Motion, software rendering, no frame clock — every function here lands on
   EXACTLY the end state it would have animated to, synchronously, with the
   completion callback already run before the call returns. Callers get one
   code path. `tools/transitions_selftest.py` gates that equivalence.

3. **The newest request wins.** Every primitive that can be interrupted keeps
   a generation counter on its container. A rapid sequence of clicks lands on
   the LAST one asked for, and every callback belonging to an older request is
   dropped rather than allowed to land on the new state. This is the same
   guard as a stale worker result: a transition is just a slow write to the UI.

4. **Nothing runs after destroy.** No primitive here leaves a timer, a signal
   handler or a completion callback that can fire against a dead widget.

    import nbtransitions as nbt

    pager = nbt.PageSwitcher(stack, order=[n for n, _ in SECTIONS])
    pager.switch("Sound")            # direction inferred from the order

    nbt.reveal(revealer, True)       # inline disclosure
    nbt.replace(holder, new_view)    # crossfade content in place
    nbt.highlight(row, "justsaved")  # brief, self-clearing
"""
import nbmotion

# GTK is optional for the same reason it is optional in nbmotion: tools/ runs
# this module with no display and no gi at all, and everything below has an
# honest instant answer in that state.
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib, Gdk
except Exception:                                                 # noqa: BLE001
    Gtk = None
    GLib = None
    Gdk = None


# --------------------------------------------------------------------------
# Direction vocabulary
# --------------------------------------------------------------------------
# A caller names a MEANING, never a Gtk.StackTransitionType. "forward" is
# deeper into a hierarchy or later in an ordered list; "back" is the way you
# came. The mapping to a slide direction lives in one table below so that
# reversing the house convention is a one-line change instead of a sweep.
NONE = "none"
FORWARD = "forward"
BACK = "back"
CROSSFADE = "crossfade"
SLIDE_DOWN = "slide-down"
SLIDE_UP = "slide-up"
SLIDE_RIGHT = "slide-right"     # a horizontal disclosure; see _REVEALER_TYPES
SLIDE_LEFT = "slide-left"

#: Durations come from nbmotion; this module defines NO numbers of its own.
PAGE = nbmotion.PAGE
SURFACE_IN = nbmotion.SURFACE_IN
SURFACE_OUT = nbmotion.SURFACE_OUT
SELECT = nbmotion.SELECT

#: How long a result highlight is held before the class comes off again. It is
#: a fixed hold, not an animation: the class going on and off is a state, and
#: any fade on it belongs to the theme's own CSS transition (which nbmotion
#: already silences under Reduced Motion via gtk-enable-animations).
HIGHLIGHT_HOLD = 1200

# new page arrives from the right when moving forward, from the left going back
_STACK_TYPES = {
    NONE: "NONE",
    CROSSFADE: "CROSSFADE",
    FORWARD: "SLIDE_LEFT",
    BACK: "SLIDE_RIGHT",
}

_REVEALER_TYPES = {
    NONE: "NONE",
    CROSSFADE: "CROSSFADE",
    SLIDE_DOWN: "SLIDE_DOWN",
    SLIDE_UP: "SLIDE_UP",
    # A disclosure on a HORIZONTAL run — a pill opening along a breadcrumb, a
    # control appearing in a toolbar. Named by where the content opens TOWARDS,
    # which is why these are their own words rather than FORWARD/BACK: those two
    # mean the opposite thing for a Stack (FORWARD sends the outgoing page
    # LEFT), and one vocabulary that points two ways is how a direction gets
    # inverted by somebody reading it in good faith.
    SLIDE_RIGHT: "SLIDE_RIGHT",
    SLIDE_LEFT: "SLIDE_LEFT",
}


# --------------------------------------------------------------------------
# The policy decision, as pure data
# --------------------------------------------------------------------------
def _plan(kinds, direction, duration, fade):
    """`(kind, milliseconds)` for a container transition that WANTS
    `direction` for `duration`.

    Deliberately pure — no GTK, no widget — because this is the part that has
    to be true, and a truth table is testable with no display. The resolution
    order is nbmotion's and is not re-implemented here:

      * `nbmotion.policy()` already returns 0 unless NB_ACCEL=1 and a frame
        clock exists, which is exactly the rule that GTK's own stack/revealer
        transitions may only run on an accelerated session.
      * Under Reduced Motion it returns 0 as well, EXCEPT when the caller
        explicitly asked for a fade — a crossfade is the one substitution the
        constitution keeps, and `fade` is how a caller says the change would be
        incomprehensible as an instant swap.

    So a 0 here means "do it now" and is never an error path.
    """
    kind = direction or CROSSFADE
    if kind not in kinds:
        kind = CROSSFADE
    if kind == NONE:
        return (NONE, 0)
    # Asking for a crossfade BY NAME is itself the explicit request that
    # Reduced Motion honours; `fade=True` says the same thing about a slide.
    wants_fade = bool(fade) or kind == CROSSFADE
    ms = nbmotion.policy(duration, wants_fade)
    if ms <= 0:
        return (NONE, 0)
    if nbmotion.reduced_motion():
        # Movement is off; the only thing that survives is the restrained
        # crossfade, at the short duration nbmotion.policy already chose.
        return (CROSSFADE, ms)
    return (kind, ms)


def stack_plan(direction=CROSSFADE, duration=PAGE, fade=False):
    """The effective `(kind, ms)` for a Gtk.Stack page switch."""
    return _plan(_STACK_TYPES, direction, duration, fade)


def revealer_plan(direction=SLIDE_DOWN, duration=SURFACE_IN, fade=False):
    """The effective `(kind, ms)` for a Gtk.Revealer reveal or collapse."""
    return _plan(_REVEALER_TYPES, direction, duration, fade)


def _enum(enum_name, kind, table):
    """Resolve a kind to the GTK enum member, tolerating a build that lacks
    one: an unknown transition type must degrade to NONE, never raise inside a
    button handler."""
    if Gtk is None:
        return None
    enum = getattr(Gtk, enum_name, None)
    if enum is None:
        return None
    return getattr(enum, table.get(kind, "NONE"), getattr(enum, "NONE", None))


# --------------------------------------------------------------------------
# Small shared plumbing
# --------------------------------------------------------------------------
def _fire(callback, completed):
    """Run a completion callback once, swallowing its exceptions. A broken
    callback is an app bug; it must not leave a container mid-transition."""
    if callback is None:
        return False
    try:
        callback(bool(completed))
    except Exception:                                             # noqa: BLE001
        pass
    return True


def _bump(obj, attr):
    """Advance a generation counter stored on the widget itself.

    On the widget rather than in a module table because the counter must die
    with the widget: a dict keyed by id() would outlive it and could hand a
    recycled id a stale generation."""
    try:
        gen = int(getattr(obj, attr, 0)) + 1
        setattr(obj, attr, gen)
        return gen
    except Exception:                                             # noqa: BLE001
        return 0                 # a wrapper that refuses attributes: no guard,
                                 # but also no crash, and the swap still happens


def _current(obj, attr, gen):
    """True when `gen` is still the newest request on this widget."""
    try:
        return int(getattr(obj, attr, 0)) == int(gen)
    except Exception:                                             # noqa: BLE001
        return True


def _alive(widget):
    """False while the widget is being destroyed.

    A destroy cancels every animation on the widget, and a cancellation is
    delivered to the completion callback — so without this check the last act
    of a dying container would be to run somebody's "the content has been
    replaced" callback against a widget that is halfway through disposal."""
    try:
        return not widget.in_destruction()
    except Exception:                                             # noqa: BLE001
        return True              # a build or a wrapper without it: assume live


# --------------------------------------------------------------------------
# 1. Page switching
# --------------------------------------------------------------------------
def switch_page(stack, name, direction=CROSSFADE, duration=PAGE, fade=False):
    """Show `name` in `stack`, with policy applied. Returns the effective ms.

    The transition type and duration are set on every call, because the
    direction changes per switch: a stack whose type was set once at
    construction slides the same way whether you are going forward or back,
    which reads as the UI losing its place."""
    if stack is None:
        return 0
    kind, ms = stack_plan(direction, duration, fade)
    try:
        ttype = _enum("StackTransitionType", kind, _STACK_TYPES)
        if ttype is not None:
            stack.set_transition_duration(ms)
            stack.set_transition_type(ttype)
    except Exception:                                             # noqa: BLE001
        pass                     # styling the switch must never block the switch
    try:
        stack.set_visible_child_name(name)
    except Exception:                                             # noqa: BLE001
        return 0
    return ms


class PageSwitcher:
    """Directional page switching for one Gtk.Stack.

    Holds the two things a bare `set_visible_child_name` cannot know:

    * **Where the pages are relative to each other.** Given `order` (the same
      list the sidebar or tab strip is built from), moving to a later page is
      forward and to an earlier one is back, so the animation agrees with the
      user's mental model of the list. Without this every switch has to pick a
      direction by hand, and they disagree within a week.

    * **Which switch is the current one.** `switch()` returns a generation
      token; anything deferred — building a page lazily, restoring a scroll
      position, a completion callback — passes the token back to `is_current()`
      and does nothing if it has been superseded. Rapid clicks therefore land
      on the LAST page clicked with no stale work landing on it afterwards.
    """

    def __init__(self, stack, order=None, duration=PAGE, fade=False):
        self.stack = stack
        self.order = list(order or [])
        self.duration = duration
        self.fade = fade
        self.generation = 0
        self.target = None
        self.last_kind = NONE
        self.last_ms = 0

    # ---- direction ----
    def index_of(self, name):
        try:
            return self.order.index(name)
        except ValueError:
            return -1

    def direction_to(self, name, frm=None):
        """forward / back, or a crossfade when the two pages have no order
        between them (an unlisted page, or the first switch of all — there is
        nothing to have come *from*, so a slide would invent a history)."""
        if frm is None:
            frm = self.target
        if frm is None or frm == name:
            return CROSSFADE
        a, b = self.index_of(frm), self.index_of(name)
        if a < 0 or b < 0:
            return CROSSFADE
        return FORWARD if b > a else BACK

    # ---- switching ----
    def switch(self, name, direction=None, duration=None, fade=None):
        """Switch to `name`. Returns the generation token for this switch.

        The token is the whole point of the return value: hold it across any
        deferred work and check `is_current()` before touching the UI."""
        # nbmotion-inventory: app.page-pane-switch
        # This is the directional pane switch, consistent OS-wide: `direction`
        # is inferred from `order` so a move to a later page slides forward and
        # an earlier one back, on ONE shared primitive, so the direction cannot
        # drift between apps. Adopted by academics/cookbook/language/packages/
        # sequencer/settings/video; the apps that still hand-roll a Stack switch
        # are ratcheted in tools/page_switch_consistency_check.py.
        if direction is None:
            direction = self.direction_to(name)
        duration = self.duration if duration is None else duration
        fade = self.fade if fade is None else fade
        gen = _bump(self, "generation")
        self.generation = gen
        # The target is recorded BEFORE the switch: the stack's own
        # notify::visible-child-name handler runs inside set_visible_child_name,
        # and a lazy page builder reading self.target there must see the page it
        # is being asked to build, not the previous one.
        self.target = name
        kind, ms = stack_plan(direction, duration, fade)
        self.last_kind, self.last_ms = kind, ms
        switch_page(self.stack, name, direction, duration, fade)
        return gen

    def is_current(self, generation):
        """False once a newer switch has been requested — the guard a deferred
        callback checks before it writes anything."""
        return int(generation) == int(self.generation)

    def current(self):
        """The page the stack is actually showing, asked of the stack rather
        than remembered, so a switch made behind our back is still the truth."""
        try:
            return self.stack.get_visible_child_name()
        except Exception:                                         # noqa: BLE001
            return self.target


# --------------------------------------------------------------------------
# 2. Inline reveal / collapse
# --------------------------------------------------------------------------
def reveal(revealer, revealed=True, direction=None, duration=SURFACE_IN,
           fade=False, on_done=None):
    """Open or close a Gtk.Revealer under policy. Returns the effective ms.

    `direction` defaults to sliding the disclosure open downward and closed
    upward, which is what an inline card under a row does. Under policy-still
    conditions the child is revealed or hidden with no animation at all and
    `on_done(True)` has already run when this returns.

    Repeated calls do not stack: GTK retargets its own animation from the
    current position, and the generation counter here means the completion
    callback of an interrupted reveal is dropped rather than firing against a
    state nobody asked for.
    """
    if revealer is None:
        return 0
    revealed = bool(revealed)
    if direction is None:
        direction = SLIDE_DOWN if revealed else SLIDE_UP
    kind, ms = revealer_plan(direction, duration, fade)
    gen = _bump(revealer, "_nbt_reveal_gen")
    try:
        ttype = _enum("RevealerTransitionType", kind, _REVEALER_TYPES)
        if ttype is not None:
            revealer.set_transition_duration(ms)
            revealer.set_transition_type(ttype)
    except Exception:                                             # noqa: BLE001
        pass
    try:
        revealer.set_reveal_child(revealed)
    except Exception:                                             # noqa: BLE001
        return 0
    if ms <= 0:
        _fire(on_done, True)
        return 0
    if on_done is not None:
        _on_child_revealed(revealer, revealed, gen, on_done)
    return ms


def _on_child_revealed(revealer, revealed, gen, on_done):
    """Fire `on_done` once, when GTK reports the reveal has finished.

    `child-revealed` is the property that flips at the END of the animation
    (`reveal-child` flips at the start), so this is the only honest completion
    signal a Revealer has. The handler disconnects itself, is dropped if a
    newer reveal has been requested, and is torn down on destroy so nothing
    calls back into a dead widget."""
    state = {"handler": None, "destroy": None}

    def _disconnect():
        for key in ("handler", "destroy"):
            hid, state[key] = state[key], None
            if hid is not None:
                try:
                    revealer.disconnect(hid)
                except Exception:                                 # noqa: BLE001
                    pass

    def _notify(*_args):
        if not _alive(revealer):
            _disconnect()
            return
        if not _current(revealer, "_nbt_reveal_gen", gen):
            _disconnect()        # superseded: this completion is not ours
            return
        try:
            done = bool(revealer.get_child_revealed()) == revealed
        except Exception:                                         # noqa: BLE001
            done = True
        if not done:
            return
        _disconnect()
        _fire(on_done, True)

    def _destroyed(*_args):
        state["handler"] = None  # GTK drops handlers with the widget
        state["destroy"] = None

    try:
        state["handler"] = revealer.connect("notify::child-revealed", _notify)
        state["destroy"] = revealer.connect("destroy", _destroyed)
    except Exception:                                             # noqa: BLE001
        _disconnect()
        _fire(on_done, True)     # no signal to wait for: report the end state


# --------------------------------------------------------------------------
# 3. Content replacement
# --------------------------------------------------------------------------
def replace(container, new_child, duration=SURFACE_IN, on_done=None,
            destroy_old=True, pack=None):
    """Put `new_child` in `container` in place of what is there, crossfading.

    Opacity is the only property touched, so no layout runs per frame; the
    swap itself happens at the midpoint, while the holder is invisible, so
    nothing is ever seen half-replaced.

    Two leaks this is written to prevent, both of which have shipped in other
    codebases as "the panel is slightly grey now":

    * **An opacity leak.** Every path — completed, cancelled, superseded,
      instant — ends with the container at EXACTLY 1.0. A container abandoned
      at 0.63 by an interrupted fade looks like a rendering fault and there is
      nothing on screen to tell the user otherwise.
    * **A layout leak.** The old child is removed from the container, not just
      hidden: a hidden-but-parented widget still counts in a Gtk.Box's size
      request, so the holder keeps the taller of the two forever.

    `destroy_old=False` keeps the removed children alive for a caller that is
    going to put one back; the default destroys them, which is what
    "replacing" means and is the only leak-free default.
    """
    if container is None:
        return 0
    gen = _bump(container, "_nbt_swap_gen")
    half = int(duration) // 2 if duration else 0
    ms = nbmotion.policy(half, True)

    if ms <= 0:
        _swap(container, new_child, destroy_old, pack)
        _set_opacity(container, 1.0)
        _fire(on_done, True)
        return 0

    def _midpoint(completed):
        if not _current(container, "_nbt_swap_gen", gen) or not _alive(container):
            return               # a newer replace owns this container now,
                                 # or there is no container left to own
        _swap(container, new_child, destroy_old, pack)
        if not completed:
            # Cancelled on the way out (a destroy, a cancel_all): the content
            # is correct, so restore full opacity rather than animate into it.
            _set_opacity(container, 1.0)
            _fire(on_done, False)
            return
        _set_opacity(container, 0.0)
        nbmotion.fade_to(container, 1.0, half, nbmotion.EASE_OUT, _arrived)

    def _arrived(completed):
        if not _current(container, "_nbt_swap_gen", gen) or not _alive(container):
            return
        _set_opacity(container, 1.0)
        _fire(on_done, bool(completed))

    nbmotion.fade_to(container, 0.0, half, nbmotion.EASE_IN, _midpoint)
    return half * 2


def _swap(container, new_child, destroy_old, pack=None):
    """Remove every current child and install `new_child`.

    `pack` exists because `Gtk.Container.add` on a Gtk.Box packs with
    expand=False, which silently makes a replaced page stop filling its holder;
    a caller with a Box passes its own `pack_start` call instead."""
    try:
        children = list(container.get_children())
    except Exception:                                             # noqa: BLE001
        children = []
    for child in children:
        if child is new_child:
            continue
        try:
            container.remove(child)
        except Exception:                                         # noqa: BLE001
            continue
        if destroy_old:
            try:
                child.destroy()
            except Exception:                                     # noqa: BLE001
                pass
    if new_child is None:
        return
    try:
        if new_child.get_parent() is None:
            if pack is not None:
                pack(container, new_child)
            else:
                container.add(new_child)
        new_child.show_all()
    except Exception:                                             # noqa: BLE001
        pass


def _set_opacity(widget, value):
    try:
        widget.set_opacity(float(value))
    except Exception:                                             # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# 4. Result highlight
# --------------------------------------------------------------------------
# Indirection so the headless suite can drive the hold without a main loop —
# and so there is exactly ONE place in this module that schedules anything.
def _timeout_add(ms, fn):
    if GLib is None:
        return 0
    return GLib.timeout_add(ms, fn)


def _source_remove(sid):
    if GLib is None or not sid:
        return
    try:
        GLib.source_remove(sid)
    except Exception:                                             # noqa: BLE001
        pass


def highlight(widget, css_class, hold=HIGHLIGHT_HOLD):
    """Hold `css_class` on `widget` briefly, then take it off again.

    The way the UI says "*this* is the row that changed" after a save, a
    search, an import or an undo. Not an animation — a state that expires — so
    it is one single-shot source, never a per-frame timer, and it runs under
    Reduced Motion and software rendering exactly as it does anywhere else.
    (Whether the class fades in is the theme's business: nbmotion already turns
    GTK's CSS transitions off with the rest of the motion.)

    Calling it again on the same widget REPLACES the pending removal instead of
    adding a second one, so a user hammering Save gets one highlight held from
    the last press rather than a queue of timers that strip the class from
    under each other. The pending source is cancelled on destroy, so the
    removal never touches a dead widget.
    """
    if widget is None or not css_class:
        return False
    ctx = _style_context(widget)
    if ctx is None:
        return False
    state = getattr(widget, "_nbt_highlight", None)
    if state is not None:
        _source_remove(state.get("source"))
        state["source"] = 0
        old = state.get("css_class")
        if old and old != css_class:
            _remove_class(widget, old)
    else:
        state = {"source": 0, "css_class": None, "destroy": None}
        try:
            widget._nbt_highlight = state
        except Exception:                                         # noqa: BLE001
            state = None
    _add_class(widget, css_class)
    if state is None:
        return True              # a widget that refuses attributes: no timer to
                                 # own, so hold the class rather than risk one
    state["css_class"] = css_class

    def _expire(*_args):
        state["source"] = 0
        _clear(widget, state)
        return False             # single shot; never rescheduled

    hold = max(1, int(hold))
    state["source"] = _timeout_add(hold, _expire)
    if state.get("destroy") is None:
        try:
            state["destroy"] = widget.connect("destroy", _on_destroy_highlight)
        except Exception:                                         # noqa: BLE001
            state["destroy"] = None
    return True


def _on_destroy_highlight(widget):
    """Drop the pending removal with the widget. The class is going away with
    the style context anyway; what must not survive is the source."""
    state = getattr(widget, "_nbt_highlight", None)
    if not state:
        return
    _source_remove(state.get("source"))
    state["source"] = 0
    state["destroy"] = None      # GTK drops the handler with the widget
    state["css_class"] = None


def clear_highlight(widget):
    """Take the class off now and cancel the pending removal. For a caller
    whose result stopped being the result — a new search, a cleared selection."""
    state = getattr(widget, "_nbt_highlight", None)
    if not state:
        return False
    _source_remove(state.get("source"))
    state["source"] = 0
    return _clear(widget, state)


def _clear(widget, state):
    css_class, state["css_class"] = state.get("css_class"), None
    hid, state["destroy"] = state.get("destroy"), None
    if hid is not None:
        try:
            widget.disconnect(hid)
        except Exception:                                         # noqa: BLE001
            pass
    if not css_class:
        return False
    _remove_class(widget, css_class)
    return True


def highlight_pending(widget):
    """True while a highlight is still held. The cleanup gate in
    tools/transitions_selftest.py asserts this returns to False."""
    state = getattr(widget, "_nbt_highlight", None)
    return bool(state and state.get("source"))


def _style_context(widget):
    try:
        return widget.get_style_context()
    except Exception:                                             # noqa: BLE001
        return None


def _add_class(widget, css_class):
    ctx = _style_context(widget)
    if ctx is None:
        return
    try:
        ctx.add_class(css_class)
    except Exception:                                             # noqa: BLE001
        pass


def _remove_class(widget, css_class):
    ctx = _style_context(widget)
    if ctx is None:
        return
    try:
        ctx.remove_class(css_class)
    except Exception:                                             # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# 4. Continuous value (app.progress: continuous, never stepped)
# --------------------------------------------------------------------------
def smooth_fraction(bar, fraction, duration=None, on_done=None):
    """Glide a progress bar to `fraction` instead of STEPPING to it.

    A bar that jumps 0.2 -> 0.4 reads as stepped; gliding the fill between the
    fractions the work actually reports is "continuous, never stepped"
    (app.progress). The fraction is a physical quantity -- how much work is done
    -- so the glide is LINEAR: a spring or ease would lie about the rate. It
    RETARGETS from wherever the fill is on screen, so a rapid sequence of reports
    heads to the newest one and the bar never jumps back or stalls. One
    nbmotion.Scalar is kept on the bar (`_nbt_frac`) and reused across reports.

    Under policy-still (Reduced Motion, software, no frame clock) the bar is set
    to `fraction` at once and `on_done(True)` has already run when this returns:
    the instant path is EQUIVALENT, which is nbmotion.Scalar's own contract.

    `bar` is anything with get_fraction()/set_fraction() (a Gtk.ProgressBar). A
    None bar, or one with no engine to drive it, sets the value directly and
    reports the end state, so a caller keeps one code path.
    """
    if bar is None:
        return 0
    try:
        target = max(0.0, min(1.0, float(fraction)))
    except Exception:                                             # noqa: BLE001
        return 0
    dur = nbmotion.FEEDBACK if duration is None else duration
    sc = getattr(bar, "_nbt_frac", None)
    if sc is None:
        try:
            start = max(0.0, min(1.0, float(bar.get_fraction())))
        except Exception:                                         # noqa: BLE001
            start = target

        def _frame(v):
            try:
                bar.set_fraction(max(0.0, min(1.0, float(v))))
            except Exception:                                     # noqa: BLE001
                pass          # a destroyed bar raises here; nothing to paint

        try:
            sc = nbmotion.Scalar(widget=bar, value=start, on_frame=_frame,
                                 easing=nbmotion.LINEAR)
        except Exception:                                         # noqa: BLE001
            sc = None
        if sc is None:
            try:
                bar.set_fraction(target)
            except Exception:                                     # noqa: BLE001
                pass
            _fire(on_done, True)
            return 0
        bar._nbt_frac = sc

    def _land(ok):
        # Nothing to land or report to once the bar is gone (same convention as
        # replace's _arrived): a destroy drops the completion silently.
        if not _alive(bar):
            return
        # Land EXACTLY on the target when a glide COMPLETES: a fill left at 0.999
        # by a frame-quantised animation never reads as "done". A superseded or
        # cancelled glide (ok False) leaves the fill for whoever retargeted it and
        # only reports.
        if ok:
            try:
                bar.set_fraction(target)
            except Exception:                                     # noqa: BLE001
                pass
        _fire(on_done, bool(ok))

    sc.animate_to(target, dur, nbmotion.LINEAR, on_done=_land)
    return nbmotion.policy(dur, False)


# --------------------------------------------------------------------------
# The anchored card (PAPER-PHYSICS Article B: nothing appears from nowhere)
# --------------------------------------------------------------------------
# A confirm card grows from the control that raised it; an About card drops
# from its menu title; a picker grows from its menu item. All three are one
# motion — a surface travelling between its ANCHOR's rectangle and its final
# rectangle — and all three are the same shape as the app-launch card the
# Finder already draws. This is that pattern, extracted so every app gets it
# from one tested place instead of hand-rolling a fifth copy.
#
# It is PAINT, never allocation (F2): the card is drawn over the host by a
# draw-after hook, growing from anchor to target and back, while the real
# surface it introduces is built at full size beneath and revealed when the
# growth lands. Damage follows the card's rectangle (F1). The origin
# discipline of §B4 is enforced in code: grow() with no anchor raises, so a
# call site that forgets where its surface came from cannot compile away the
# question.


def interp_rect(a, b, t):
    """A rectangle t of the way from a to b, each (x, y, w, h). Pure."""
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def widget_rect(widget, relative_to):
    """`widget`'s allocation as (x, y, w, h) in `relative_to`'s coordinates,
    or None if either is unrealized. The anchor-resolution both the confirm
    and About call sites need, in one place."""
    if Gtk is None or widget is None or relative_to is None:
        return None
    try:
        at = widget.translate_coordinates(relative_to, 0, 0)
        if at is None:
            return None
        alloc = widget.get_allocation()
        return (float(at[0]), float(at[1]),
                float(max(1, alloc.width)), float(max(1, alloc.height)))
    except Exception:                                             # noqa: BLE001
        return None


class GrowCard:
    """A card that grows from an anchor rectangle to a target and retracts.

    `host` is the widget the card is painted over — it must emit `draw` and
    accept `queue_draw_area`; the caller connects `host`'s draw-after signal
    to `paint`. `on_paint(cr, rect, t)` draws the card's content for the
    current rectangle and progress; a caller that only wants the default
    paper-and-hairline card omits it.

    The lifecycle mirrors nbmotion's own guarantees: instant when policy says
    still (the card lands at the target and `on_done(True)` runs before grow()
    returns), one animation retargets the next, nothing fires after the host
    dies.
    """

    def __init__(self, host, on_paint=None):
        self.host = host
        self.on_paint = on_paint
        self.active = False
        self._t = 0.0
        self._anchor = self._target = (0.0, 0.0, 1.0, 1.0)
        self._scalar = None

    def _damage(self, t):
        # the union of anchor and target always covers the current rect, so a
        # single fixed damage rectangle per grow suffices and never repaints
        # the whole host
        a, b = self._anchor, self._target
        x = min(a[0], b[0]) - 2
        y = min(a[1], b[1]) - 2
        w = max(a[0] + a[2], b[0] + b[2]) - x + 4
        h = max(a[1] + a[3], b[1] + b[3]) - y + 4
        return (x, y, w, h)

    def _frame(self, t):
        self._t = t
        if self.host is None:
            return
        try:
            x, y, w, h = self._damage(t)
            self.host.queue_draw_area(int(x), int(y), int(w), int(h))
        except Exception:                                         # noqa: BLE001
            pass

    def rect(self):
        return interp_rect(self._anchor, self._target, self._t)

    def grow(self, anchor, target, on_done=None):
        """Grow a card from `anchor` to `target`, each (x, y, w, h) in host
        coordinates. `anchor` is REQUIRED — §B4: a surface must name where it
        came from."""
        if anchor is None:
            raise ValueError("GrowCard.grow needs an anchor (Article B): a "
                             "surface may not appear from nowhere")
        self._anchor = tuple(float(v) for v in anchor)
        self._target = tuple(float(v) for v in target)
        self.active = True
        if nbmotion is None:
            self._t = 1.0
            self._fire(on_done, True)
            return self
        # SURFACE_IN, not PAGE: a card is "a menu, card, tooltip, sheet or app
        # arriving" in §D1's mapping, and retract() below already departs on
        # SURFACE_OUT — growing at PAGE made the arrival 40ms slower than its
        # own departure, which §B3 ("departure retraces arrival") forbids.
        # ARRIVE, not EASE_OUT: this is the one GEOMETRIC arrival in the shared
        # layer (it travels a rectangle), so Amendment 3's lively slight spring
        # applies. The fades elsewhere keep EASE_OUT deliberately — opacity has
        # nowhere to overshoot to.
        # BOUND ON THE SPRING, measured: ARRIVE peaks at 1.053, so the rect
        # briefly exceeds its target by 5.3% of the anchor->target delta. Against
        # present_card's CENTRED target that stays inside the host until the card
        # reaches ~90% of a host dimension (at 1024x722: 921x649 is inside,
        # 942x664 clips). Every card that uses this today — Get Info, Confirm,
        # About — is far below that, so the overshoot is not clamped and the
        # spring is honest. A near-FULLSCREEN card would have its edge cut for
        # the ~40ms of overshoot; give that one a clamp rather than flattening
        # the spring for everybody.
        if self._scalar is None:
            self._scalar = nbmotion.Scalar(
                widget=self.host, value=0.0, on_frame=self._frame,
                duration=nbmotion.SURFACE_IN, easing=nbmotion.ARRIVE)
        self._t = 0.0
        self._scalar.jump_to(0.0)
        self._scalar.animate_to(1.0, duration=nbmotion.SURFACE_IN,
                                easing=nbmotion.ARRIVE, on_done=on_done)
        return self

    def retract(self, on_done=None):
        """Collapse back into the anchor (§B3: departure retraces arrival)."""
        if not self.active:
            self._fire(on_done, True)
            return self
        if nbmotion is None or self._scalar is None:
            self._t = 0.0
            self.active = False
            self._fire(on_done, True)
            return self

        def _done(ok):
            self.active = False
            self._frame(0.0)
            self._fire(on_done, ok)

        self._scalar.animate_to(0.0, duration=nbmotion.SURFACE_OUT,
                                easing=nbmotion.EASE_IN, on_done=_done)
        return self

    def clear(self):
        if self._scalar is not None:
            self._scalar.cancel()
        self.active = False

    def paint(self, cr):
        """Call from the host's draw-after handler. Draws nothing when the
        card is not active, so it is safe to leave connected."""
        if not self.active:
            return False
        rect = self.rect()
        if self.on_paint is not None:
            self.on_paint(cr, rect, self._t)
            return False
        x, y, w, h = rect
        try:
            cr.set_source_rgb(0.988, 0.984, 0.973)      # paper
            cr.rectangle(x, y, w, h)
            cr.fill()
            cr.set_source_rgb(0.788, 0.769, 0.714)      # hairline
            cr.set_line_width(1)
            cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
            cr.stroke()
        except Exception:                                         # noqa: BLE001
            pass
        return False

    @staticmethod
    def _fire(on_done, completed):
        if on_done is not None:
            try:
                on_done(bool(completed))
            except Exception:                                     # noqa: BLE001
                pass


def _call(fn):
    """Run a no-argument card callback once, swallowing its exceptions (an
    app's on_shown/on_close bug must not strand a half-presented overlay)."""
    if fn is None:
        return
    try:
        fn()
    except Exception:                                             # noqa: BLE001
        pass


# GrowCard is the PAINT of an anchored card; present_card is the whole
# PRESENTATION. It builds the scrim + grow layer + real content card on a host's
# Gtk.Overlay, grows the paper frame from `anchor` to the card's centred target,
# reveals the real content on landing, and retracts to the anchor on close.
# Extracted verbatim from the Finder's Get-Info/confirm presenter so every app
# gets the same anchored card from ONE tested place instead of a fifth hand-
# rolled copy (PAPER-PHYSICS Article B). GrowCard.grow raises without an anchor,
# so a caller that means to grow from a control cannot forget where it came
# from. present_card allows ONE sanctioned exception: an explicit anchor=None
# centre-grows, for a surface with genuinely no on-screen origin -- the Finder's
# grid view, where a selected icon resolves no row rectangle. That is a decision
# the caller makes by passing None, not a silent default.
def present_card(overlay, box, anchor, on_close=None, on_shown=None,
                 css_class="nbcard", size_from=None):
    """Present `box` as a card that GROWS FROM `anchor` on `overlay` and
    retracts to it on close. Returns `(card_win, close)`:

      * card_win  the EventBox holding `box`; emits `destroy` when removed, so a
                  caller filling it asynchronously can watch that.
      * close     call it (or wire it to Esc / a button) to retract and remove.

    `anchor` is (x, y, w, h) in `overlay` coordinates, or None to centre-grow
    when the surface has no on-screen origin (grid view). With nbtransitions'
    motion still (Reduced Motion, software, no frame clock) the card lands at
    its target and `on_shown()` runs before this returns -- the instant path is
    EQUIVALENT, not skipped. `size_from` overrides where the host size is read
    (default: the overlay's own allocation).

    `on_shown` fires the moment the real content is revealed -- on landing for
    the animated path, immediately for the instant one -- so a destructive
    confirm can focus its SAFE default only once the card exists."""
    if Gtk is None or overlay is None:
        _call(on_shown)                      # headless: no surface to build
        return (None, (lambda *_a: _call(on_close)))
    host = size_from if size_from is not None else overlay
    try:
        alloc = host.get_allocation()
        W = alloc.width if alloc.width > 1 else 1024
        H = alloc.height if alloc.height > 1 else 722
    except Exception:                                             # noqa: BLE001
        W, H = 1024, 722
    layer = Gtk.Fixed()
    scrim = Gtk.EventBox()
    if Gdk is not None:
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    scrim.set_size_request(W, H)
    layer.put(scrim, 0, 0)
    grow_da = Gtk.DrawingArea()
    grow_da.set_size_request(W, H)
    layer.put(grow_da, 0, 0)
    card_win = Gtk.EventBox()
    if css_class:
        card_win.get_style_context().add_class(css_class)
    card_win.add(box)
    card_win.set_no_show_all(True)
    layer.put(card_win, 0, 0)
    overlay.add_overlay(layer)
    scrim.show()
    grow_da.show()
    _min, nat = card_win.get_preferred_size()
    cw = nat.width if nat.width > 1 else 340
    ch = nat.height if nat.height > 1 else 220
    tx, ty = max((W - cw) // 2, 0), max((H - ch) // 2, 0)
    # §E4 check 4: an arriving surface's rest edge may not be off the grid.
    # A CENTRED card lands on the 4u grid only when its natural size happens to
    # be a multiple of 8 — measured at 1024x722, more than half of plausible
    # card widths put BOTH edges 1-3px off it. §E3.8: "A surface that stops
    # 11 px from a hairline is the single most visible way to prove the grid is
    # decorative." Snapping the REST position costs at most 3px of centring —
    # invisible — and buys an edge that lands where the grid says.
    # The unit is read from nbapp rather than copied: design_tokens ->
    # nbapp is a LOCKSTEP pair grid_check enforces, and a third copy here is
    # exactly the drift that check exists to catch. Imported lazily because
    # nbapp imports this module; any failure falls back to the centred value,
    # since a card that is 2px off the grid is still infinitely better than a
    # card that does not appear.
    try:
        import nbapp as _nbapp
        _u = int(getattr(_nbapp, "GRID_UNIT", 4)) or 4
        tx, ty = (tx // _u) * _u, (ty // _u) * _u
    except Exception:                                             # noqa: BLE001
        pass
    target = (float(tx), float(ty), float(cw), float(ch))
    layer.put(card_win, tx, ty)

    state = {"grow": None, "closing": False}

    def remove(*_a):
        if layer.get_parent() is not None:
            overlay.remove(layer)
        card_win.destroy()                   # fires the handle's "destroy"
        _call(on_close)

    def close(*_a):
        if state["closing"]:
            return True
        state["closing"] = True
        g = state["grow"]
        if g is not None and getattr(g, "active", False):
            g.retract(on_done=lambda _ok: remove())
        else:
            remove()
        return True

    scrim.connect("button-press-event", close)
    if Gdk is not None:
        card_win.add_events(Gdk.EventMask.KEY_PRESS_MASK)

    def reveal(*_a):
        card_win.show()
        _call(on_shown)

    if anchor is not None:
        g = GrowCard(grow_da)
        grow_da.connect_after("draw", lambda _w, cr: g.paint(cr))
        state["grow"] = g
        g.grow(anchor, target, on_done=lambda _ok: reveal())
    else:
        reveal()
    return (card_win, close)
