#!/usr/bin/env python3
"""nbtransitions — the four transitions Notebook OS is allowed to have.

de/nbmotion.py owns *time*: the duration tokens, the easing curves, the policy
that decides whether anything may move, and a retargetable scalar driven by the
frame clock. This module owns *containers*: it turns those tokens into the
handful of container-level changes the interaction constitution actually
sanctions, so that no app has to reason about `Gtk.StackTransitionType`,
Reduced Motion and NB_ACCEL on its own and get a different answer each time.

    page switch      a Gtk.Stack changing page, with a DIRECTION
    inline reveal    a Gtk.Revealer opening or closing a disclosure
    content replace  one widget crossfading into another inside a holder
    result highlight a CSS class held briefly on the thing that just changed

That is the complete list. Anything not on it is not a transition, it is
decoration, and the answer is no.

Four rules run through every function here:

1. **GTK does the moving, we do the deciding.** A page switch is
   `Gtk.Stack`'s own animation and a reveal is `Gtk.Revealer`'s own: one
   internal frame-clock animation inside the toolkit, in C, with no Python
   running per frame. What this module contributes is the *policy* — which
   transition type, how long, or none at all. The ban on animating
   width/height/margin/padding applies to hand-rolled animation of somebody
   else's layout properties; a Revealer animating its own allocation is the
   widget doing the single job it exists for.

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
    from gi.repository import Gtk, GLib
except Exception:                                                 # noqa: BLE001
    Gtk = None
    GLib = None


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
