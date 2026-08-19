"""Navigation state: generation tokens, restore scopes and safe state reads.

Article III of the interaction constitution asks two things of every app that
restores itself:

1. **A delayed restore may never land on a newer state.** A scroll, selection
   or focus call posted with `GLib.idle_add` runs at least one main-loop turn
   later, and by then the reader may have opened another document, switched
   pane, or closed the window. Without a token the callback applies the OLD
   position to the NEW content, which reads as the app losing the place the
   person was just at.
2. **Restored state must be plausible.** A store written by an older build, a
   half-truncated file, or a playlist the user has since deleted must fall back
   to a sane default rather than raising inside `__init__`.

This module is deliberately GTK-free: it holds no widget, imports no `gi`, and
can be exercised head-lessly (`tools/navigation_state_selftest.py`). The apps
keep owning their widgets; what they stop re-deriving is the counter, the
comparison, the closed-window check and the coercion rules — patterns that were
open-coded in `ebook`, `installer`, `video`, `finder` and `nbtransitions`, each
slightly differently.

Nothing here starts, cancels or owns a timer. A generation only says whether a
callback that is ALREADY running is still speaking for the current state; the
caller returns early when it is not.
"""


class Generation:
    """A monotonic token for one owner (a document, a pane, a query).

    Bump it wherever the thing it guards changes; capture `token()` at the
    moment a delayed callback is posted; call `valid(token)` first thing inside
    that callback. `close()` (on `destroy`) invalidates every outstanding token
    at once, so a callback still queued on the main loop when the window goes
    away cannot touch a torn-down widget.
    """

    __slots__ = ("_n", "_closed", "name")

    def __init__(self, name=""):
        self._n = 0
        self._closed = False
        self.name = name

    def token(self):
        """The token a callback posted right now should carry."""
        return self._n

    def bump(self):
        """Advance to a new state; returns the new token.

        A closed generation still advances (the counter is never reused), but
        every token stays invalid — closing is one-way.
        """
        self._n += 1
        return self._n

    def reset(self, value=0):
        """Set the counter directly and reopen the generation.

        Only for an owner that re-uses one object across a full teardown and
        rebuild, and for fixtures that need a known starting count.
        """
        self._n = int(value)
        self._closed = False
        return self._n

    def valid(self, token):
        """True when `token` still speaks for the current state.

        `None` is never valid: it means the caller did not capture a token, and
        silently treating that as current is how an unguarded callback slips
        back in.
        """
        if self._closed or token is None:
            return False
        return token == self._n

    @property
    def closed(self):
        return self._closed

    def close(self):
        """The owner is gone. Every outstanding and future token is stale."""
        self._closed = True

    def guard(self, fn, token=None):
        """Wrap `fn` so it only runs while `token` is still current.

        `token` defaults to the token at wrap time, which is what a call site
        posting a callback for the state it is looking at wants. The wrapper
        always returns a bool, so it is safe to hand straight to
        `GLib.idle_add` / `GLib.timeout_add`: a stale (or already-run) callback
        returns False and the source is dropped rather than repeating.
        """
        if token is None:
            token = self._n

        def _run(*args):
            if not self.valid(token):
                return False
            return bool(fn(*args))

        _run.token = token
        _run.generation = self
        return _run


class RestoreScope:
    """A re-entrant "we are putting the UI back, not editing it" flag.

    Restoration walks the same setters a person's click walks, so without this
    every reopen would look like an edit: an autosave would fire, the document
    would go dirty, and an undo step would be pushed for something the user
    never did. Wrap the restore in `with scope:` and have the save/dirty/undo
    paths return early while `scope.active` is true.
    """

    __slots__ = ("_depth",)

    def __init__(self):
        self._depth = 0

    @property
    def active(self):
        return self._depth > 0

    @property
    def depth(self):
        return self._depth

    def __enter__(self):
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        # An exception inside a restore must still leave the scope, or every
        # later save in the session would be silently skipped.
        if self._depth > 0:
            self._depth -= 1
        return False


def choice(value, allowed, default=None):
    """Coerce a persisted choice (a pane id, a sort key) to a known one.

    Anything not in `allowed` — a missing key, a number, a pane that a later
    build removed — falls back to `default`, or to the first allowed value when
    no default is given, so a stale store can never leave an app with no pane
    selected.
    """
    seq = list(allowed)
    if value in seq:
        return value
    if default in seq:
        return default
    return seq[0] if seq else None


def identity_index(items, ident, key=None, default=-1):
    """Find `ident` in `items` by IDENTITY, returning its index or `default`.

    Restoring "row 4" is wrong the moment the content changed underneath: rows
    are added, removed, sorted and filtered between sessions, so a saved index
    points at a different thing than the one the user left selected. Save what
    the row IS (a path, a playlist name, an id) and look it up here.

    `key` extracts the identity from an item; without it the item is compared
    directly. A `None` identity never matches.
    """
    if ident is None:
        return default
    for i, item in enumerate(items):
        try:
            got = key(item) if key is not None else item
        except Exception:
            continue
        if got == ident:
            return i
    return default


def clamp_index(value, count, default=0):
    """A saved row/page index clamped into a list that may have changed size."""
    if count <= 0:
        return 0
    try:
        i = int(value)
    except (TypeError, ValueError):
        try:
            i = int(default)
        except (TypeError, ValueError):
            i = 0
    return max(0, min(i, count - 1))


def fraction(value, default=0.0):
    """A saved scroll/zoom fraction coerced into 0.0-1.0.

    Covers the shapes a damaged or hand-edited store actually holds: strings,
    None, NaN, infinities and out-of-range numbers.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        f = float("nan")
    if f != f or f in (float("inf"), float("-inf")):  # NaN / infinities
        try:
            f = float(default)
        except (TypeError, ValueError):
            f = 0.0
        if f != f or f in (float("inf"), float("-inf")):
            f = 0.0
    return max(0.0, min(1.0, f))
