#!/usr/bin/env python3
"""
Language — a delayed lesson callback must not outlive the lesson it belongs to.

THE BUG THIS EXISTS FOR
-----------------------
A lesson runs on delays. A finished matching round grades itself 250ms after
the last pair joins; a wrong tile keeps its red for 400ms; a correct answer
moves on after 750ms; the last heart brings up the out-of-hearts page 900ms
later. Every one of those was a bare GLib.timeout_add that nobody owned:
nothing recorded the source and nothing cancelled it.

So the delay fired into whatever came next.

  * Press the lesson's close button inside the 900ms out-of-hearts pause and
    _out_of_hearts still ran -- it set _lesson to None again, saved, and packed
    its "Out of hearts" ending into the lesson holder, replacing the course
    path the quit had just returned the reader to. A page they had left came
    back over the page they had asked for, a second later, with nothing
    touched.
  * Close the window inside the 750ms advance and _advance repainted a whole
    exercise into a window being torn down.
  * Start Practice straight off the ending screen and a delay owed to the run
    that just finished could advance an exercise in the run that just started.

Destroy only saved progress; _quit_lesson only cleared _lesson.

WHAT IS ENFORCED HERE
---------------------
The owned-source contract, both halves of it:

  1. every lesson timer is RECORDED, so it can be removed;
  2. every lesson timer also carries the generation it was scheduled in, so a
     callback GLib has already dispatched -- one that source_remove is too late
     to stop -- finds itself stale and does nothing.

Half of that is not enough. source_remove alone loses the race with an
already-queued callback, and a generation check alone leaks a live source per
answer. Both are checked by delivering stale closures BY HAND after their
source has been removed, which is exactly the delivery a real main loop can do
and no timing-based test can reliably reproduce.

PURE. No GTK window, no main loop, no display, no clock: language.GLib is
swapped for a recorder that hands back the closure instead of running it, and
the real methods are bound onto a plain stub. Nothing here sleeps.

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/language_lesson_timer_selftest.py
"""
import ast
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="nbhome-langtimer-")
os.environ["NB_HOME"] = HOME
os.environ.setdefault("GDK_BACKEND", "x11")

DE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, os.path.abspath(DE))

import language                                          # noqa: E402

L = language.Language
FAIL = []


def check(cond, what):
    if cond:
        print("  ok   %s" % what)
    else:
        print("  FAIL %s" % what)
        FAIL.append(what)


# ----------------------------------------------------------------------
# the fake main loop
# ----------------------------------------------------------------------
class FakeGLib:
    """Records timeouts instead of running them. `deliver` plays a closure the
    way a main loop would -- including one whose source has been removed, which
    is the case the whole fix is about."""

    def __init__(self):
        self.pending = {}        # id -> (ms, closure)
        self.removed = []        # ids passed to source_remove, in order
        self._next = 100

    def timeout_add(self, ms, fn, *a, **kw):
        sid = self._next
        self._next += 1
        self.pending[sid] = (ms, fn)
        return sid

    def source_remove(self, sid):
        self.removed.append(sid)
        self.pending.pop(sid, None)
        return True

    def closure(self, sid):
        return self.pending[sid][1]

    def delay(self, sid):
        return self.pending[sid][0]


class Stub:
    """The lesson-timer surface of the window and nothing else."""

    _lesson_later = L._lesson_later
    _cancel_lesson_callbacks = L._cancel_lesson_callbacks
    _on_destroy = L._on_destroy
    _quit_lesson = L._quit_lesson
    _grade = L._grade
    _advance = L._advance

    def __init__(self):
        self._closed = False
        self._lesson_gen = 0
        self._lesson_sources = set()
        self._lesson = {"i": 0, "ex": [], "answered": 0}
        self._graded = False
        self.saves = 0
        self.saw_closed_at_save = None
        self.backs = 0
        self.rendered = 0
        self.hearts_page = 0

    # collaborators the real methods reach for
    def _save_progress(self):
        self.saves += 1
        self.saw_closed_at_save = self._closed

    def _back_to_course(self):
        self.backs += 1

    def _render_exercise(self):
        self.rendered += 1

    def _out_of_hearts(self):
        self.hearts_page += 1
        self._lesson = None


def fresh():
    return Stub()


print("Language: owned lesson timers")

real_glib = language.GLib
G = FakeGLib()
language.GLib = G
try:
    # ------------------------------------------------------------------
    # 1. a scheduled source is recorded and keeps its delay
    # ------------------------------------------------------------------
    print("\n1. sources are recorded")
    s = fresh()
    hits = []
    a = s._lesson_later(250, lambda: hits.append("match"))
    b = s._lesson_later(400, lambda: hits.append("tile"))
    c = s._lesson_later(750, lambda: hits.append("advance"))
    d = s._lesson_later(900, lambda: hits.append("hearts"))
    check(s._lesson_sources == {a, b, c, d},
          "all four pending sources are held for cancellation")
    check([G.delay(x) for x in (a, b, c, d)] == [250, 400, 750, 900],
          "timings unchanged: 250 / 400 / 750 / 900")
    check(hits == [], "scheduling alone runs nothing")

    # ------------------------------------------------------------------
    # 2. a fresh callback runs once and unregisters itself
    # ------------------------------------------------------------------
    print("\n2. a live callback still fires")
    s = fresh()
    hits = []
    sid = s._lesson_later(750, lambda: hits.append(1))
    ret = G.closure(sid)()
    check(hits == [1], "an open owner at the current generation runs the work")
    check(ret is False, "returns False: a one-shot does not re-arm")
    check(sid not in s._lesson_sources,
          "a fired source drops its id (the set cannot grow without bound)")
    check(G.removed == [],
          "a source that fired is NOT handed to source_remove")

    # ------------------------------------------------------------------
    # 3. invalidation removes every pending source, exactly once
    # ------------------------------------------------------------------
    print("\n3. invalidation cancels everything pending")
    s = fresh()
    G.removed = []
    ids = {s._lesson_later(250, lambda: None),
           s._lesson_later(400, lambda: None),
           s._lesson_later(900, lambda: None)}
    gen_before = s._lesson_gen
    s._cancel_lesson_callbacks()
    check(sorted(G.removed) == sorted(ids), "every id was removed")
    check(len(G.removed) == len(set(G.removed)), "each id removed exactly once")
    check(s._lesson_sources == set(), "the pending set is cleared")
    check(s._lesson_gen == gen_before + 1, "the generation moved on")
    s._cancel_lesson_callbacks()
    check(len(G.removed) == len(ids),
          "a second invalidation removes nothing twice")

    # ------------------------------------------------------------------
    # 4. a closure already queued by the main loop is inert
    # ------------------------------------------------------------------
    print("\n4. a STALE closure delivered after removal does nothing")
    s = fresh()
    hits = []
    sid = s._lesson_later(750, lambda: hits.append("advance"))
    queued = G.closure(sid)          # the main loop already has this in hand
    s._cancel_lesson_callbacks()
    ret = queued()                   # ... and delivers it anyway
    check(hits == [], "the stale advance did not run")
    check(ret is False, "the stale callback still unregisters cleanly")

    s = fresh()
    hits = []
    queued = G.closure(s._lesson_later(400, lambda: hits.append("tile")))
    s._closed = True                 # window torn down, not just lesson ended
    check(queued() is False and hits == [],
          "a closed owner refuses the callback with no generation change")

    # ------------------------------------------------------------------
    # 5. quitting a lesson cannot be undone by the out-of-hearts delay
    # ------------------------------------------------------------------
    print("\n5. quit beats the 900ms out-of-hearts page")
    s = fresh()
    queued = G.closure(s._lesson_later(900, s._out_of_hearts))
    s._quit_lesson()
    check(s.backs == 1, "quit returned to the course path")
    check(queued() is False, "the out-of-hearts closure was delivered")
    check(s.hearts_page == 0,
          "the ending did NOT replace the course page after the quit")

    # a new lesson started in the meantime is not touched either
    print("\n6. a new lesson is not advanced by the old one's delay")
    s = fresh()
    queued = G.closure(s._lesson_later(750, s._advance))
    s._cancel_lesson_callbacks()          # what _run does before it starts one
    s._lesson = {"i": 0, "ex": [], "answered": 0}
    s._graded = True                      # the NEW lesson has a graded answer
    check(queued() is False and s.rendered == 0 and s._lesson["i"] == 0,
          "the stale advance did not skip the new lesson's first exercise")

    # ------------------------------------------------------------------
    # 7. destroy: closed first, then cancel, then one save
    # ------------------------------------------------------------------
    print("\n7. destroy is ordered and idempotent")
    order = []

    class OrderedGLib(FakeGLib):
        def source_remove(self, sid):
            order.append(("remove", stub._closed))
            return FakeGLib.source_remove(self, sid)

    language.GLib = OG = OrderedGLib()
    stub = fresh()
    stub._lesson_later(750, lambda: None)
    stub._lesson_later(900, lambda: None)
    stub._on_destroy()
    check(stub._closed is True, "the window is marked closed")
    check(order and all(closed for _n, closed in order),
          "_closed was set BEFORE any source was removed")
    check(len(OG.removed) == 2 and len(set(OG.removed)) == 2,
          "each pending source was removed exactly once")
    check(stub.saves == 1, "progress saved exactly once")
    check(stub.saw_closed_at_save is True, "_closed was set before the save")
    check(stub._lesson_sources == set(), "nothing is left pending")

    stub._on_destroy()
    stub._on_destroy(None)
    check(stub.saves == 1, "a duplicate destroy does not save again")
    check(len(OG.removed) == 2, "a duplicate destroy removes nothing again")

    # a destroy on a window whose lesson state never existed must not raise
    bare = Stub.__new__(Stub)
    bare.saves = 0
    bare._save_progress = lambda: None
    try:
        bare._on_destroy()
        check(True, "destroy survives a window with no lesson state")
    except Exception as e:
        check(False, "destroy survives a window with no lesson state (%s)" % e)

    # ------------------------------------------------------------------
    # 8. the grading path rejects a closed owner on its own
    # ------------------------------------------------------------------
    print("\n8. _grade / _advance gate on the closed window")
    s = fresh()
    s._closed = True
    s._graded = False
    s._grade(True, {"kind": "type", "term": "x", "answer": "x"})
    check(s._graded is False and s._lesson["answered"] == 0,
          "_grade scores nothing once the window is closed")
    s._graded = True
    check(s._advance() is False and s.rendered == 0,
          "_advance repaints nothing once the window is closed")

    s = fresh()
    s._graded = True
    s._advance()
    check(s.rendered == 1 and s._lesson["i"] == 1,
          "an OPEN window still advances normally")
finally:
    language.GLib = real_glib

# ----------------------------------------------------------------------
# 9. static: no lesson delay may go back to being unowned
# ----------------------------------------------------------------------
print("\n9. every lesson delay goes through the owned helper")
src = open(os.path.join(DE, "language.py"), encoding="utf-8").read()
tree = ast.parse(src)
funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
for name in ("_match_tap", "_grade", "_hold_for_continue"):
    body = funcs[name]
    bare = [n for n in ast.walk(body)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("timeout_add", "timeout_add_seconds",
                                "idle_add")
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "GLib"]
    check(not bare, "%s schedules no unowned GLib source" % name)
owned = [n for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and n.func.attr == "_lesson_later"]
check(len(owned) == 5, "all five lesson delays use _lesson_later (found %d)"
      % len(owned))

print("\n%s" % ("FAILED: %d" % len(FAIL) if FAIL else "all checks passed"))
sys.exit(1 if FAIL else 0)
