#!/usr/bin/env python3
"""jobs_selftest — the gate on de/nbjobs.py.

    python3 tools/jobs_selftest.py

Display-free and GTK-free, on the same terms as tools/motion_selftest.py and
tools/commands_selftest.py: nbjobs treats gi as optional, so this suite runs on
a machine with no X, no PyGObject and no main loop. It drives the REAL module —
a reimplementation of the delivery rules here would pass while the shipped code
was broken, which is the exact failure mode nbjobs was written to end.

Every rule below is one that some app in this OS got wrong by hand:

  1. **Prompt return.** start() hands the thread the work and comes back; a
     worker that blocks for a second does not hold the caller for a second.
  2. **Cancellation.** A token cancelled mid-work stops it at the next
     checkpoint, and the caller who ASKED for that gets on_cancel — exactly
     once, with no done and no error alongside it.
  3. **Generations.** Newest request wins: the superseded job is cancelled and
     delivers NOTHING, so a slow first answer cannot overwrite a fast second
     one. This is the stale-list bug.
  4. **Progress.** Reports are marshalled through the dispatcher (never called
     on the worker thread) and stop dead once the job has finished.
  5. **Duplicate policy.** REPLACE supersedes, REJECT refuses and leaves the
     job in flight untouched.
  6. **Close cleanup.** After close() nothing is delivered — including a
     callback ALREADY queued on the dispatcher, which is the case a check at
     queueing time misses and a person sees as a dead window updating itself.
  7. **One-shot delivery.** One result per job, whatever order the finish paths
     race in.
  8. **Exceptions as data.** Anything the work raises becomes a JobError; a
     callback that raises is recorded, not propagated into the idle handler.
  9. **Idle dispatch.** Nothing at all reaches a callback until the dispatcher
     is drained — the property that lets a UI thread own its own widgets.

The concurrency fixtures are real threads with real contention (a barrier, a
gate, an ordering latch), not sleeps: a delivery rule that only holds when the
timing is lucky is not a rule.

Exit status 0 on pass.
"""

import os
import sys
import threading
import time

DE = "/opt/notebook/de"
if not os.path.isdir(DE):
    # Run straight from a checkout as well: same module, no guest needed.
    DE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

import nbjobs                                                    # noqa: E402

FAILED = []
JOIN_TIMEOUT = 10.0


def check(cond, what):
    print("%-68s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


class Gate:
    """A worker that will not finish until the test lets it.

    The fixture that makes every ordering question below decidable: the test
    holds a job open, does the thing under test (close the owner, start a
    replacement, cancel), and only then releases the worker.
    """

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def work(self, job):
        self.entered.set()
        # Cancellable wait: a cancelled job leaves here at the checkpoint rather
        # than sitting on the gate until the test remembers to open it.
        self.release.wait(JOIN_TIMEOUT)
        job.checkpoint()
        return "gated"

    def open(self):
        self.release.set()


class Sink:
    """Somewhere for callbacks to land, with the thread they landed on."""

    def __init__(self):
        self.done = []
        self.errors = []
        self.cancels = 0
        self.progress = []
        self.threads = set()

    def _mark(self):
        self.threads.add(threading.current_thread().name)

    def on_done(self, value):
        self._mark()
        self.done.append(value)

    def on_error(self, err):
        self._mark()
        self.errors.append(err)

    def on_cancel(self):
        self._mark()
        self.cancels += 1

    def on_progress(self, fraction, phase):
        self._mark()
        self.progress.append((fraction, phase))

    @property
    def total(self):
        return len(self.done) + len(self.errors) + self.cancels


def owner_with_manual():
    disp = nbjobs.ManualDispatcher()
    return nbjobs.JobOwner(dispatch=disp, name="test"), disp


# ---- 1. prompt return -------------------------------------------------------
def t_prompt_return():
    owner, disp = owner_with_manual()
    gate = Gate()
    sink = Sink()
    t0 = time.monotonic()
    job = owner.start("slow", gate.work, on_done=sink.on_done)
    elapsed = time.monotonic() - t0
    check(job is not None, "start() returns a job")
    check(elapsed < 0.5,
          "start() returns while the work is still running (%.3fs)" % elapsed)
    check(gate.entered.wait(JOIN_TIMEOUT), "the work really is running")
    check(owner.is_running("slow") and sink.total == 0,
          "nothing has been delivered while the work is in flight")
    gate.open()
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.done == ["gated"], "the answer arrives once the work finishes")
    owner.close()


# ---- 2. cancellation --------------------------------------------------------
def t_cancellation():
    owner, disp = owner_with_manual()
    started = threading.Event()
    stopped_at = []

    def work(job):
        started.set()
        for i in range(1000):
            if job.wait(0.01):          # cancellable sleep
                stopped_at.append(i)
                job.checkpoint()
            job.checkpoint()
        return "ran to the end"

    sink = Sink()
    owner.start("c", work, on_done=sink.on_done, on_error=sink.on_error,
                on_cancel=sink.on_cancel)
    check(started.wait(JOIN_TIMEOUT), "the cancellable work started")
    check(owner.cancel("c"), "cancel() finds the job to cancel")
    check(owner.join(JOIN_TIMEOUT), "the worker leaves promptly when cancelled")
    check(stopped_at and stopped_at[0] < 900,
          "it stopped at a checkpoint rather than running to the end")
    disp.drain()
    check(sink.cancels == 1 and not sink.done and not sink.errors,
          "an explicit cancel delivers on_cancel and nothing else")
    check(owner.cancel("c") is False,
          "cancelling a finished job is a no-op, not an error")
    owner.close()


def t_cancel_before_first_checkpoint():
    """A job cancelled before its thread gets going must not run the work at
    all. Built from the real Job so the pre-work checkpoint is what is under
    test — going through start() could not hold the thread still long enough to
    ask the question."""
    owner, disp = owner_with_manual()
    ran = []
    sink = Sink()
    job = nbjobs.Job(owner, "c2", 1, lambda j: ran.append(1),
                     on_done=sink.on_done, on_cancel=sink.on_cancel)
    job.token.cancel()
    job._start()
    job.join(JOIN_TIMEOUT)
    disp.drain()
    check(ran == [], "work cancelled before it began is never run")
    check(sink.done == [], "a cancelled job never reports a value")
    check(sink.cancels == 1, "it reports the cancel exactly once")

    # And the same when the cancel lands while the work is in flight: the
    # RESULT it was about to return is thrown away, not delivered.
    owner2, disp2 = owner_with_manual()
    sink2 = Sink()
    gate = Gate()
    job2 = owner2.start("c3", gate.work, on_done=sink2.on_done,
                        on_cancel=sink2.on_cancel)
    check(gate.entered.wait(JOIN_TIMEOUT), "the gated work is in flight")
    job2.cancel()
    gate.open()
    owner2.join(JOIN_TIMEOUT)
    disp2.drain()
    check(sink2.done == [] and sink2.cancels == 1,
          "a cancel mid-work discards the value the work was returning")
    owner.close()
    owner2.close()


# ---- 3. generations ---------------------------------------------------------
def t_generations():
    owner, disp = owner_with_manual()
    sink = Sink()
    slow = Gate()
    first = owner.start("k", slow.work, on_done=sink.on_done,
                        on_cancel=sink.on_cancel, on_error=sink.on_error)
    check(slow.entered.wait(JOIN_TIMEOUT), "the first request is running")
    check(owner.generation("k") == 1, "the first request is generation 1")
    second = owner.start("k", lambda j: "second", on_done=sink.on_done,
                         on_cancel=sink.on_cancel)
    check(owner.generation("k") == 2,
          "a second request for the key takes the next generation")
    check(second.generation > first.generation,
          "generations increase monotonically per owner and key")
    check(first.cancelled, "the superseded job is cancelled")
    check(first.superseded and not second.superseded,
          "only the older job reports itself superseded")
    slow.open()
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.done == ["second"],
          "the newest request is the only one that reports")
    check(sink.cancels == 0,
          "being superseded is silent — not even on_cancel from the old job")
    owner.close()


def t_generation_stale_answer_loses():
    """The stale-list bug in its pure form: the SLOW answer finishes LAST."""
    owner, disp = owner_with_manual()
    sink = Sink()
    slow = Gate()
    owner.start("list", slow.work, on_done=sink.on_done)
    slow.entered.wait(JOIN_TIMEOUT)
    owner.start("list", lambda j: "fresh", on_done=sink.on_done)
    owner.join(JOIN_TIMEOUT)
    disp.drain()                          # the fresh answer is delivered
    slow.open()                           # only now does the old one return
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.done == ["fresh"],
          "a stale answer finishing later never overwrites the fresh one")
    owner.close()


def t_generation_is_per_key():
    owner, _disp = owner_with_manual()
    owner.start("a", lambda j: 1)
    owner.start("a", lambda j: 2)
    owner.start("b", lambda j: 3)
    owner.join(JOIN_TIMEOUT)
    check(owner.generation("a") == 2 and owner.generation("b") == 1,
          "generations are counted per key, not per owner")
    check(owner.generation("never-used") == 0,
          "a key that was never started is generation 0")
    owner.close()


# ---- 4. progress ------------------------------------------------------------
def t_progress():
    owner, disp = owner_with_manual()
    sink = Sink()
    worker_names = []

    def work(job):
        worker_names.append(threading.current_thread().name)
        job.progress(0.0, "Looking")
        job.progress(0.5, "Halfway")
        job.progress(1.0, "Finishing")
        return "ok"

    owner.start("p", work, on_done=sink.on_done, on_progress=sink.on_progress)
    owner.join(JOIN_TIMEOUT)
    check(sink.progress == [],
          "progress does not reach the caller from the worker thread")
    disp.drain()
    check(sink.progress == [(0.0, "Looking"), (0.5, "Halfway"),
                            (1.0, "Finishing")],
          "every progress report arrives, in order, through the dispatcher")
    check(sink.done == ["ok"], "the result arrives after the progress")
    check(worker_names and worker_names[0] not in sink.threads,
          "no callback ran on the worker thread")
    owner.close()


def t_progress_stops_after_finish():
    owner, disp = owner_with_manual()
    sink = Sink()
    late = []

    def work(job):
        late.append(job)
        return "done"

    owner.start("p", work, on_done=sink.on_done, on_progress=sink.on_progress)
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    late[0].progress(0.9, "too late")     # a straggler from a finished job
    disp.drain()
    check(sink.progress == [],
          "a progress report from a finished job is dropped")
    owner.close()


# ---- 5. duplicate-start policy ---------------------------------------------
def t_duplicate_policy():
    owner, disp = owner_with_manual()
    sink = Sink()
    gate = Gate()
    first = owner.start("d", gate.work, on_done=sink.on_done,
                        policy=nbjobs.REJECT)
    check(gate.entered.wait(JOIN_TIMEOUT), "the first job is in flight")
    rejected = owner.start("d", lambda j: "second", on_done=sink.on_done,
                           policy=nbjobs.REJECT)
    check(rejected is None, "REJECT refuses a duplicate start")
    check(owner.generation("d") == 1,
          "a rejected start does not burn a generation")
    check(not first.cancelled, "the job already running is left alone")
    gate.open()
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.done == ["gated"], "the first job still delivers its answer")
    # ...and once it is finished, the key is free again under either policy.
    owner.start("d", lambda j: "later", on_done=sink.on_done,
                policy=nbjobs.REJECT)
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.done == ["gated", "later"],
          "REJECT only refuses while a job is actually running")
    check(nbjobs.REPLACE != nbjobs.REJECT, "the two policies are distinct")
    owner.close()


def t_replace_is_the_default():
    owner, disp = owner_with_manual()
    gate = Gate()
    first = owner.start("d", gate.work)
    gate.entered.wait(JOIN_TIMEOUT)
    second = owner.start("d", lambda j: "b")
    check(second is not None and first.cancelled,
          "REPLACE is the default duplicate policy")
    gate.open()
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    owner.close()


# ---- 6. close cleanup -------------------------------------------------------
def t_close_stops_delivery():
    """The window-is-gone case, in both of its shapes."""
    owner, disp = owner_with_manual()
    sink = Sink()

    # (a) the callback is ALREADY on the idle queue when close() happens.
    owner.start("a", lambda j: "queued", on_done=sink.on_done)
    owner.join(JOIN_TIMEOUT)
    check(disp.pending() >= 1, "the answer is queued on the dispatcher")
    owner.close()
    disp.drain()
    check(sink.total == 0,
          "a callback already queued is dropped when the owner closes")

    # (b) the work is still running when close() happens.
    owner2, disp2 = owner_with_manual()
    sink2 = Sink()
    gate = Gate()
    job = owner2.start("b", gate.work, on_done=sink2.on_done,
                       on_cancel=sink2.on_cancel, on_error=sink2.on_error)
    gate.entered.wait(JOIN_TIMEOUT)
    owner2.close()
    check(job.cancelled, "close() cancels the work that is still running")
    gate.open()
    owner2.join(JOIN_TIMEOUT)
    disp2.drain()
    check(sink2.total == 0, "a job in flight when the owner closes says nothing")

    check(owner2.start("c", lambda j: 1) is None,
          "a closed owner starts nothing new")
    check(owner2.closed, "the owner reports itself closed")
    owner2.close()
    check(owner2.closed, "close() is idempotent")
    check(owner2.running_keys() == [], "a closed owner holds no jobs")


def t_close_then_reopen():
    """Two owners, the way a dialog closed and reopened makes two: the first
    one's answer has nowhere to go, and the second one is unaffected by it."""
    old, old_disp = owner_with_manual()
    old_sink = Sink()
    gate = Gate()
    old.start("printers", gate.work, on_done=old_sink.on_done)
    gate.entered.wait(JOIN_TIMEOUT)
    old.close()                                   # the dialog is closed

    new, new_disp = owner_with_manual()           # ...and opened again
    new_sink = Sink()
    new.start("printers", lambda j: "fresh list", on_done=new_sink.on_done)

    gate.open()                                   # the old discovery returns
    old.join(JOIN_TIMEOUT)
    new.join(JOIN_TIMEOUT)
    old_disp.drain()
    new_disp.drain()
    check(old_sink.total == 0, "the closed owner's answer is discarded")
    check(new_sink.done == ["fresh list"],
          "the reopened owner gets its own answer, uncontaminated")
    new.close()


# ---- 7. one-shot delivery ---------------------------------------------------
def t_one_shot():
    owner, disp = owner_with_manual()
    sink = Sink()

    # Work that finishes normally at the same moment it is cancelled: both
    # finish paths are live at once, and exactly one result may come out.
    barrier = threading.Barrier(2, timeout=JOIN_TIMEOUT)

    def work(job):
        barrier.wait()
        return "value"

    job = owner.start("one", work, on_done=sink.on_done,
                      on_cancel=sink.on_cancel, on_error=sink.on_error)
    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        pass
    job.cancel()
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.total == 1,
          "a job racing its own cancellation delivers exactly one result")
    check(job.state in nbjobs.FINAL_STATES, "it settles in a final state")

    # And a direct second attempt at delivery changes nothing.
    job._finish(nbjobs.DONE, "again", None)
    disp.drain()
    check(sink.total == 1, "a second delivery attempt is ignored")
    check(job.finished, "the job reports itself finished")
    owner.close()


def t_many_jobs_deliver_once_each():
    owner, disp = owner_with_manual()
    sink = Sink()
    n = 24
    ready = threading.Barrier(n, timeout=JOIN_TIMEOUT)

    def work(job):
        ready.wait()                 # all of them finish at the same instant
        return job.key

    for i in range(n):
        owner.start("k%02d" % i, work, on_done=sink.on_done)
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(len(sink.done) == n and len(set(sink.done)) == n,
          "%d simultaneous jobs deliver exactly one answer each" % n)
    check(owner.running_keys() == [],
          "every finished job is cleaned out of the owner")
    owner.close()


# ---- 8. exceptions as data --------------------------------------------------
def t_exception_is_data():
    owner, disp = owner_with_manual()
    sink = Sink()

    def boom(job):
        raise ValueError("the disk said no")

    owner.start("e", boom, on_done=sink.on_done, on_error=sink.on_error,
                on_cancel=sink.on_cancel)
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(len(sink.errors) == 1 and not sink.done and not sink.cancels,
          "a raising job delivers on_error and nothing else")
    err = sink.errors[0]
    check(isinstance(err, nbjobs.JobError),
          "the exception arrives as JobError data")
    check(not isinstance(err, BaseException),
          "a JobError cannot be re-raised by accident")
    check(err.kind == "ValueError" and "disk said no" in err.message,
          "the error keeps what was raised and what it said")
    check("boom" in err.detail,
          "the traceback is kept for the log, off the dialog")

    # A worker killed by something that is not an Exception is still data.
    def worse(job):
        raise KeyboardInterrupt()

    sink2 = Sink()
    owner.start("e2", worse, on_error=sink2.on_error, on_done=sink2.on_done)
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(len(sink2.errors) == 1 and sink2.errors[0].kind == "KeyboardInterrupt",
          "a BaseException on a worker is data too, not a lost thread")
    owner.close()


def t_callback_exception_does_not_escape():
    owner, disp = owner_with_manual()
    hit = []

    def bad(_value):
        hit.append(1)
        raise RuntimeError("a mistake in the app's handler")

    owner.start("cb", lambda j: 1, on_done=bad)
    owner.join(JOIN_TIMEOUT)
    raised = None
    try:
        disp.drain()                      # this stands in for the idle handler
    except Exception as exc:              # noqa: BLE001
        raised = exc
    check(raised is None,
          "a callback that raises does not escape into the main loop")
    check(hit == [1], "the callback did run")
    check(len(owner.callback_errors) == 1 and
          owner.callback_errors[0].kind == "RuntimeError",
          "the callback's mistake is recorded on the owner")

    # ...and the next job still works afterwards.
    sink = Sink()
    owner.start("cb2", lambda j: 2, on_done=sink.on_done)
    owner.join(JOIN_TIMEOUT)
    disp.drain()
    check(sink.done == [2], "the owner keeps working after a bad callback")
    owner.close()


# ---- 9. idle dispatch -------------------------------------------------------
def t_idle_dispatch():
    owner, disp = owner_with_manual()
    sink = Sink()
    owner.start("i", lambda j: "v", on_done=sink.on_done)
    owner.join(JOIN_TIMEOUT)
    check(sink.total == 0 and disp.pending() == 1,
          "nothing is delivered until the dispatcher runs")
    check(disp.run_pending() == 1, "draining the dispatcher runs it once")
    check(sink.done == ["v"], "and only then does the caller see the answer")
    check(disp.run_pending() == 0, "a drained dispatcher has nothing left")
    check(threading.current_thread().name in sink.threads,
          "the callback ran on the thread that drained, not on the worker")

    # The dispatcher is injectable, and the default is chosen by the machine.
    check(nbjobs.default_dispatcher() in
          (nbjobs.glib_dispatch, nbjobs.direct_dispatch),
          "the default dispatcher is GLib's where there is one")
    if nbjobs.GLib is None:
        check(nbjobs.default_dispatcher() is nbjobs.direct_dispatch,
              "with no GLib the default runs work directly")

    # direct_dispatch delivers on the worker thread, on purpose.
    direct = nbjobs.JobOwner(dispatch=nbjobs.direct_dispatch)
    seen = []
    direct.start("d", lambda j: "x", on_done=seen.append)
    direct.join(JOIN_TIMEOUT)
    check(seen == ["x"], "direct_dispatch needs no drain")
    direct.close()
    owner.close()


def t_no_heavy_dependency():
    """nbjobs must import on a machine with nothing on it: the DE selftests and
    the build host both run it that way."""
    import ast
    src = open(os.path.join(DE, "nbjobs.py"), encoding="utf-8").read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    allowed = {"threading", "traceback", "gi", "gi.repository"}
    check(imports <= allowed,
          "nbjobs imports nothing beyond %s (found %s)"
          % (sorted(allowed), sorted(imports - allowed)))
    check("gi" in src and "except Exception" in src,
          "gi is imported defensively, so a headless machine still gets nbjobs")


def main():
    for fn in (t_prompt_return,
               t_cancellation, t_cancel_before_first_checkpoint,
               t_generations, t_generation_stale_answer_loses,
               t_generation_is_per_key,
               t_progress, t_progress_stops_after_finish,
               t_duplicate_policy, t_replace_is_the_default,
               t_close_stops_delivery, t_close_then_reopen,
               t_one_shot, t_many_jobs_deliver_once_each,
               t_exception_is_data, t_callback_exception_does_not_escape,
               t_idle_dispatch, t_no_heavy_dependency):
        print("-- %s" % fn.__name__)
        fn()
    # Nothing above may have left a worker thread behind.
    leftover = [t for t in threading.enumerate()
                if t.name.startswith("nbjob-")]
    check(not leftover, "no worker thread outlives its owner (%s)" % leftover)

    print()
    if FAILED:
        print("jobs selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
        return 1
    print("jobs selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
