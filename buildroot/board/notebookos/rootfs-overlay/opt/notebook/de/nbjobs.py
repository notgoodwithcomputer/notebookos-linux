#!/usr/bin/env python3
"""nbjobs — the shared background job foundation for Notebook OS.

Half the apps in this OS run the same shape of work: something slow and
blocking (a subprocess, a disk walk, an encode) has to happen without the window
freezing, and its answer has to reach a widget afterwards. Each app grew its own
copy of that — `threading.Thread(target=..., daemon=True)` plus a `gen` counter
plus `GLib.idle_add` — and each copy got a different subset of it right. The
failure they share is not a crash: it is a callback that arrives after the
window it was going to update has been closed, or a stale answer that overwrites
a fresh one, so a person sees a list of things that are no longer there.

This module is that pattern, once, with the four rules that actually matter:

1. **Nothing reaches the caller except through the dispatcher.** Every
   completion, error, cancel and progress report is marshalled through one
   injectable callable (`glib_dispatch` on the guest, `ManualDispatcher` in a
   test). A worker thread therefore never touches a widget, and a test never
   needs a main loop to observe what a worker did.

2. **Newest request wins.** Generations are per (owner, key) and only ever go
   up. Starting a job for a key that already has one supersedes the old one:
   the old token is cancelled and the old job delivers NOTHING — not even
   `on_cancel` — because its callbacks were written to update the state the new
   request has already replaced. Explicitly cancelling a job with `cancel(key)`
   is the other case, and there `on_cancel` DOES run: someone asked to stop and
   is owed the answer.

3. **Exactly once, or not at all.** A job delivers one of done / error / cancel
   and never a second one. After `owner.close()` it delivers nothing, including
   work already queued on the dispatcher — the guard is re-checked at the moment
   the callback would run, not only when it was scheduled.

4. **An exception is data.** Whatever the work raises is caught and handed over
   as a `JobError`, so nothing is ever raised out of a GTK idle callback (where
   GLib swallows it and the app just quietly stops working). Callbacks are
   wrapped as well: a mistake in one app's `on_done` cannot take the main loop
   with it.

GTK is optional on purpose, on the same terms as nbmotion: this module imports
and works with no display, no gi and no main loop, which is what lets
tools/jobs_selftest.py drive the real code headlessly.

    import nbjobs

    class Panel:
        def __init__(self):
            self.jobs = nbjobs.JobOwner(name="panel")

        def refresh(self):
            self.jobs.start("scan", self._scan, on_done=self._show)

        def _scan(self, job):          # background thread
            job.progress(0.0, "Looking")
            job.checkpoint()           # raises out if cancelled
            return heavy()

        def _show(self, value):        # main loop, or not at all
            ...

        def on_close(self):
            self.jobs.close()          # nothing above ever fires again
"""

import threading
import traceback

# GTK is optional ON PURPOSE. Anything under tools/ runs this module with no
# display and no PyGObject; an import failure there would cost more than the
# idle dispatcher is worth. Everything below checks `GLib is None`.
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib
except Exception:                                                 # noqa: BLE001
    GLib = None


# ---- job states ------------------------------------------------------------
PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"
FINAL_STATES = (DONE, ERROR, CANCELLED)

# ---- duplicate-start policy ------------------------------------------------
# What start() does when the key it was given already has a job in flight.
REPLACE = "replace"     # cancel the old one, supersede it, start the new one
REJECT = "reject"       # leave the old one alone, return None


# ---- dispatchers -----------------------------------------------------------
# A dispatcher is one callable: dispatch(fn) arranges for fn() to be called,
# with no arguments, on the thread that owns the UI. That is the whole contract,
# which is why it can be swapped for a list in a test.
def glib_dispatch(fn):
    """Run fn() on the GTK main loop at idle. The real dispatcher on the guest."""
    if GLib is None:
        raise RuntimeError("no GLib on this machine")
    # idle_add keeps calling until the source returns False, so a bare fn that
    # returns None (the usual case) would run forever — the classic form of this
    # bug is a "one-shot" refresh that pegs a core.
    return GLib.idle_add(_glib_once, fn)


def _glib_once(fn):
    fn()
    return False


def direct_dispatch(fn):
    """Run fn() immediately, on the calling (worker) thread.

    The headless default. It is deliberately NOT a main-loop imitation: it is
    for code that has no UI to protect, and for tests that assert on ordering.
    """
    fn()


class ManualDispatcher:
    """A dispatcher that queues instead of running, drained by the test.

    Standing in for the GTK main loop this way is what makes the delivery rules
    checkable: a stale result can be produced, the owner closed, and only THEN
    the queue drained — which is exactly the race that put a closed window's
    callback on screen, and it cannot be provoked reliably any other way.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = []
        self.dispatched = 0

    def __call__(self, fn):
        with self._lock:
            self._queue.append(fn)
            self.dispatched += 1

    def pending(self):
        with self._lock:
            return len(self._queue)

    def run_pending(self):
        """Run everything queued so far and return how many ran. Work queued BY
        that work is left for the next drain, exactly as a real idle queue
        behaves within one main-loop iteration."""
        with self._lock:
            batch, self._queue = self._queue, []
        for fn in batch:
            fn()
        return len(batch)

    def drain(self, rounds=16):
        """run_pending() until nothing new appears. Returns the total run."""
        total = 0
        for _ in range(max(1, rounds)):
            n = self.run_pending()
            total += n
            if n == 0:
                break
        return total


def default_dispatcher():
    """The idle dispatcher for this machine: GLib's if there is one."""
    return glib_dispatch if GLib is not None else direct_dispatch


# ---- cancellation ----------------------------------------------------------
class Cancelled(Exception):
    """Raised INSIDE a worker by checkpoint(), caught by the runner.

    It never escapes the thread and is never handed to a callback: a cancelled
    job is a cancelled job, not a failed one.
    """


class CancelToken:
    """The one thing a worker and its owner share across the thread boundary.

    An Event and not a bool: `wait()` lets a worker sleep between checkpoints
    and still stop the instant it is cancelled, rather than a tenth of a second
    later.
    """

    __slots__ = ("_ev",)

    def __init__(self):
        self._ev = threading.Event()

    def cancel(self):
        self._ev.set()

    @property
    def cancelled(self):
        return self._ev.is_set()

    def checkpoint(self):
        """Leave the worker now if it has been cancelled."""
        if self._ev.is_set():
            raise Cancelled()

    def wait(self, seconds):
        """Sleep up to `seconds`. True if it was cancelled, False if it timed
        out — so `if token.wait(0.5): return` is a cancellable pause."""
        return self._ev.wait(seconds)


# ---- errors as data --------------------------------------------------------
class JobError:
    """What a worker raised, flattened into something safe to pass around.

    Deliberately not an exception: nothing that arrives on the UI thread should
    be re-raisable by accident. `message` is the exception's own words and is
    NOT fit to show a person as-is — apps map it to their own sentence, the way
    nbprint._prepare_problem does.
    """

    __slots__ = ("kind", "message", "detail")

    def __init__(self, kind, message, detail=""):
        self.kind = kind
        self.message = message
        self.detail = detail

    @classmethod
    def of(cls, exc):
        try:
            detail = "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))
        except Exception:                                         # noqa: BLE001
            detail = ""
        kind = type(exc).__name__
        return cls(kind, str(exc) or kind, detail)

    def __repr__(self):
        return "JobError(%s: %s)" % (self.kind, self.message)


# ---- a job -----------------------------------------------------------------
class Job:
    """One unit of background work, and the only object a worker sees.

    The work callable is passed the Job itself, so `job.checkpoint()` and
    `job.progress()` are to hand without a second context object to thread
    through.
    """

    def __init__(self, owner, key, generation, work,
                 on_done=None, on_error=None, on_cancel=None, on_progress=None):
        self.owner = owner
        self.key = key
        self.generation = generation
        self.token = CancelToken()
        self.state = PENDING
        self.value = None
        self.error = None
        self._work = work
        self._on_done = on_done
        self._on_error = on_error
        self._on_cancel = on_cancel
        self._on_progress = on_progress
        self._lock = threading.Lock()
        self._delivered = False
        self._thread = None

    # -- worker side --------------------------------------------------------
    @property
    def cancelled(self):
        return self.token.cancelled

    def cancel(self):
        self.token.cancel()

    def checkpoint(self):
        self.token.checkpoint()

    def wait(self, seconds):
        return self.token.wait(seconds)

    def progress(self, fraction=None, phase=None):
        """Report progress from the worker. Marshalled through the dispatcher
        like everything else, and dropped once the job has delivered its result,
        so a late tick can never re-light a finished progress bar."""
        if self._on_progress is None:
            return
        with self._lock:
            if self._delivered:
                return
        self.owner._dispatch(self, self._on_progress, fraction, phase)

    # -- state --------------------------------------------------------------
    @property
    def finished(self):
        with self._lock:
            return self._delivered

    @property
    def superseded(self):
        """True once a newer request for this key exists. Its callbacks were
        written for state that has already been replaced, so they are dropped."""
        return self.owner._generation_of(self.key) > self.generation

    # -- run ----------------------------------------------------------------
    def _start(self):
        self._thread = threading.Thread(
            target=self._run, name="nbjob-%s" % (self.key,), daemon=True)
        self.state = RUNNING
        self._thread.start()

    def _run(self):
        try:
            self.token.checkpoint()
            value = self._work(self)
        except Cancelled:
            self._finish(CANCELLED, None, None)
        except BaseException as exc:                              # noqa: BLE001
            # Everything, including KeyboardInterrupt and SystemExit: they mean
            # nothing on a worker thread and would otherwise be printed by the
            # threading module and lost.
            self._finish(ERROR, None, JobError.of(exc))
        else:
            self._finish(DONE, value, None)

    def _finish(self, state, value, error):
        with self._lock:
            if self._delivered:            # exactly once, whatever happens
                return
            self._delivered = True
            if self.token.cancelled:
                # A job that was cancelled reports cancelled, even if the work
                # happened to return or raise on its way out. The caller asked
                # for it to stop; a result it did not want is not an answer.
                state, value, error = CANCELLED, None, None
            self.state = state
            self.value = value
            self.error = error
        # Cleanup BEFORE delivery: by the time a callback runs, is_running(key)
        # is already False, so a handler may start the next job for the same key
        # without tripping the duplicate policy against its own predecessor.
        self.owner._retire(self)
        if state == DONE:
            cb, args = self._on_done, (value,)
        elif state == ERROR:
            cb, args = self._on_error, (error,)
        else:
            cb, args = self._on_cancel, ()
        if cb is not None:
            self.owner._dispatch(self, cb, *args)

    def join(self, timeout=None):
        """Wait for the worker thread. For deterministic teardown and tests —
        never call this from the UI thread."""
        t = self._thread
        if t is not None:
            t.join(timeout)
        return not (t is not None and t.is_alive())

    def __repr__(self):
        return "Job(%s#%d %s)" % (self.key, self.generation, self.state)


# ---- the owner -------------------------------------------------------------
class JobOwner:
    """The lifetime a set of jobs belongs to — a window, a panel, a dialog.

    One per thing that can be closed. `close()` is the whole point: after it,
    nothing this owner started can call back, so a window's handlers cannot
    touch widgets that have been destroyed.
    """

    def __init__(self, dispatch=None, name=""):
        self.name = name
        self._dispatch_fn = dispatch or default_dispatcher()
        self._lock = threading.RLock()
        self._jobs = {}          # key -> the job currently owning that key
        self._gens = {}          # key -> highest generation ever issued
        self._threads = []       # every job started, for join()
        self._closed = False
        self.callback_errors = []   # mistakes in callbacks, kept not raised

    # -- lifetime -----------------------------------------------------------
    @property
    def closed(self):
        return self._closed

    def close(self):
        """Cancel everything and stop delivering. Idempotent, and safe to call
        from a "destroy" handler."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.token.cancel()

    def join(self, timeout=None):
        """Wait for every worker thread this owner ever started. Test/teardown
        only. True if they are all done."""
        with self._lock:
            threads = list(self._threads)
        alive = False
        for t in threads:
            t.join(timeout)
            alive = alive or t.is_alive()
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
        return not alive

    # -- starting work ------------------------------------------------------
    def start(self, key, work, on_done=None, on_error=None, on_cancel=None,
              on_progress=None, policy=REPLACE):
        """Run work(job) on a daemon thread under `key`.

        Returns the Job, or None if the owner is closed or the REJECT policy
        turned the request away. Under REPLACE (the default) any job already
        running for this key is cancelled and superseded — it will deliver
        nothing at all.
        """
        with self._lock:
            if self._closed:
                return None
            current = self._jobs.get(key)
            if current is not None and not current.finished:
                if policy == REJECT:
                    return None
                current.token.cancel()
            gen = self._gens.get(key, 0) + 1
            self._gens[key] = gen
            job = Job(self, key, gen, work, on_done=on_done, on_error=on_error,
                      on_cancel=on_cancel, on_progress=on_progress)
            self._jobs[key] = job
        job._start()
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            if job._thread is not None:
                self._threads.append(job._thread)
        return job

    # -- stopping work ------------------------------------------------------
    def cancel(self, key):
        """Ask the job under `key` to stop. Its on_cancel still runs: this was
        asked for, unlike being superseded. True if there was one to cancel."""
        with self._lock:
            job = self._jobs.get(key)
        if job is None or job.finished:
            return False
        job.token.cancel()
        return True

    def cancel_all(self):
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.token.cancel()
        return len(jobs)

    # -- inspection ---------------------------------------------------------
    def is_running(self, key):
        with self._lock:
            job = self._jobs.get(key)
        return job is not None and not job.finished

    def job(self, key):
        with self._lock:
            return self._jobs.get(key)

    def running_keys(self):
        with self._lock:
            return sorted(k for k, j in self._jobs.items() if not j.finished)

    def generation(self, key):
        return self._generation_of(key)

    def _generation_of(self, key):
        with self._lock:
            return self._gens.get(key, 0)

    # -- internals ----------------------------------------------------------
    def _retire(self, job):
        """Drop a finished job, but only if it is still the one holding its key
        — a superseded job must not evict its own replacement."""
        with self._lock:
            if self._jobs.get(job.key) is job:
                del self._jobs[job.key]

    def _dispatch(self, job, fn, *args):
        """The single gate every callback passes through."""
        if self._closed or job.superseded:
            return                       # never even queue it
        owner = self

        def guarded():
            # Checked AGAIN here: the window may have closed, or a newer request
            # arrived, between queueing this and the main loop reaching it. That
            # gap is the whole bug this module exists to close.
            if owner._closed or job.superseded:
                return
            try:
                fn(*args)
            except Exception as exc:                              # noqa: BLE001
                # A callback that raises must not escape into the idle handler,
                # where GLib prints it and carries on with the source removed.
                owner.callback_errors.append(JobError.of(exc))
                traceback.print_exc()

        self._dispatch_fn(guarded)


# ---- selftest --------------------------------------------------------------
def _selftest():
    """A smoke check only — the real suite is tools/jobs_selftest.py."""
    disp = ManualDispatcher()
    owner = JobOwner(dispatch=disp, name="smoke")
    seen = []
    job = owner.start("k", lambda j: 41 + 1, on_done=seen.append)
    assert job is not None
    owner.join(5)
    disp.drain()
    assert seen == [42], seen
    owner.close()
    print("nbjobs selftest: OK")


if __name__ == "__main__":
    _selftest()
