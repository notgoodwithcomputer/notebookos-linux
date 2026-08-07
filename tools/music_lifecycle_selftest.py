#!/usr/bin/env python3
"""Music must not keep playing (or keep timers running) after it is closed.

GStreamer bus messages are dispatched from the main loop, so a message the
pipeline posted a moment ago is still delivered AFTER the thing it refers to is
gone. An EOS posted as the last samples drained arrives after the user hit the
window's close box, or after a decode error already stopped playback. The old
handlers took that late EOS at face value and called _advance, which calls
_play_track, which puts the pipeline straight back to PLAYING and re-arms the
300ms progress timer — so the machine carried on making noise with no Music
window to stop it from, and a GLib timeout kept firing at destroyed widgets.

What this protects:

  * a queued EOS delivered after destroy does not advance          <- the bug
  * a queued EOS delivered after playback stopped does not advance
  * a queued error delivered after destroy does not touch the engine
  * _start_poll refuses to create a GLib source once closed
  * _on_destroy raises the closed flag BEFORE it tears the pipeline down,
    so a message dispatched during teardown already sees the flag

None of this needs a window: the handlers are pulled out of music.py by source
and run against a stub with the same attributes, so the test runs anywhere and
never opens a display.

Each behaviour is also re-run with its guard surgically removed (the "mutant"
below). Those runs must show the OLD broken behaviour — that is what proves the
checks would go red if someone deleted the guards from music.py.

Run:  python3 tools/music_lifecycle_selftest.py
"""
import ast
import os
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC = os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/music.py")

RESULTS = []
FAILED = []


def check(name, ok, note=""):
    RESULTS.append(bool(ok))
    if not ok:
        FAILED.append(name)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- %s" % (note,)))
    return bool(ok)


# ---------------- pulling the handlers out of music.py ----------------
SRC = open(MUSIC, encoding="utf-8").read()
TREE = ast.parse(SRC)
LINES = SRC.splitlines()


def music_class():
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == "Music":
            return node
    raise SystemExit("music.py has no class Music")


CLS = music_class()


def method(name):
    for node in CLS.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise SystemExit("Music has no method %s" % name)


def guard_refs(node):
    """Does this statement test the closed flag / the loaded track?"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in (
                "_closed", "_loaded_path"):
            return True
    return False


def compile_method(name, drop_guards=False):
    """The method as a callable, optionally with its lifecycle guards removed.

    drop_guards is the mutant: it deletes the top-level `if self._closed ...:
    return` from the body, reproducing the code as it was before the fix. The
    behaviour checks are run both ways, so a check that cannot tell the two
    apart is a check that proves nothing.
    """
    fn = method(name)
    if drop_guards:
        body = [st for st in fn.body
                if not (isinstance(st, ast.If) and guard_refs(st))]
        fn = ast.parse(ast.unparse(fn)).body[0]
        fn.body = [ast.parse(ast.unparse(st)).body[0] for st in body]
        src = ast.unparse(fn)
    else:
        src = textwrap.dedent(
            "\n".join(LINES[fn.lineno - 1:fn.end_lineno]))
    ns = {"GLib": GLibStub, "Gst": GstStub}
    exec(compile(src, MUSIC, "exec"), ns)
    return ns[name]


# ---------------- stubs standing in for GLib / Gst / the window ----------
class GLibStub(object):
    """Records every source this run would have created or removed."""
    added = []
    removed = []

    @classmethod
    def reset(cls):
        cls.added = []
        cls.removed = []

    @classmethod
    def timeout_add(cls, ms, fn):
        cls.added.append((ms, fn))
        return 4242

    @classmethod
    def source_remove(cls, sid):
        cls.removed.append(sid)
        return True


class _State(object):
    NULL = "NULL"


class GstStub(object):
    State = _State


class FakePlayer(object):
    def __init__(self, win):
        self._win = win
        self.states = []
        # what self._closed was AT THE MOMENT the pipeline was torn down
        self.closed_when_torn_down = None

    def set_state(self, state):
        self.states.append(state)
        self.closed_when_torn_down = self._win._closed


class FakeMusic(object):
    """Only the attributes the lifecycle methods touch."""

    def __init__(self, closed=False, loaded="/home/user/Music/a.mp3",
                 poll_id=0):
        self._closed = closed
        self._loaded_path = loaded
        self._poll_id = poll_id
        self._playing = True
        self._player = FakePlayer(self)
        self.advanced = []
        self.stopped = 0
        self.scan_stopped = 0
        self.saved = 0
        self.flashed = []

    # things the handlers call on their way to doing damage
    def _advance(self, auto, direction):
        self.advanced.append((auto, direction))
        # the real _advance ends in _play_track, which re-arms the poll
        self._start_poll_hook()

    def _start_poll_hook(self):
        GLibStub.timeout_add(300, None)

    def _on_poll(self):
        # the poll callback itself; only ever handed to GLib here, never run
        return False

    def _stop_playback(self):
        self.stopped += 1

    def _stop_length_scan(self):
        self.scan_stopped += 1

    def _save(self):
        self.saved += 1

    # _on_error reports the failure now instead of stopping in silence, so the
    # stub grew the two methods that path calls. Both RECORD rather than no-op:
    # a message shown after the window is gone would be the same lifecycle bug
    # this suite exists to catch, and that is now asserted below.
    def _track_label(self, path):
        return "a track"

    @staticmethod
    def _play_failure(msg):
        return "%s could not be played"

    def _flash(self, msg, restore_ms=4000):
        self.flashed.append(msg)


# ---------------- the checks ----------------
on_eos = compile_method("_on_eos")
on_eos_mutant = compile_method("_on_eos", drop_guards=True)
on_error = compile_method("_on_error")
on_error_mutant = compile_method("_on_error", drop_guards=True)
start_poll = compile_method("_start_poll")
start_poll_mutant = compile_method("_start_poll", drop_guards=True)
on_destroy = compile_method("_on_destroy")


def run_eos(fn, **kw):
    GLibStub.reset()
    win = FakeMusic(**kw)
    fn(win, None, None)
    return win


# 1. EOS delivered after the window was destroyed
w = run_eos(on_eos, closed=True, loaded=None)
check("late EOS after destroy does not advance", not w.advanced,
      "_advance ran with _closed set: playback restarts with no window")
check("late EOS after destroy arms no timer", not GLibStub.added,
      "a GLib timeout was created after destroy")

# 2. EOS delivered after playback was stopped (decode error / last track).
#    The window is still open, so only _loaded_path tells this apart.
w = run_eos(on_eos, closed=False, loaded=None)
check("late EOS after stop does not advance", not w.advanced,
      "a stopped player restarted itself from a queued EOS")

# 3. the guard must not break a REAL end-of-track
w = run_eos(on_eos, closed=False, loaded="/home/user/Music/a.mp3")
check("a real end-of-track still advances", w.advanced == [(True, 1)],
      "the guard swallowed normal auto-advance: got %r" % (w.advanced,))

# 4. the same three inputs through the unguarded handler: the checks above are
#    only worth something if this shows the old behaviour
w = run_eos(on_eos_mutant, closed=True, loaded=None)
check("MUTANT: unguarded EOS after destroy DOES advance", bool(w.advanced),
      "guards removed and nothing changed - checks 1-2 cannot fail")
w = run_eos(on_eos_mutant, closed=False, loaded=None)
check("MUTANT: unguarded EOS after stop DOES advance", bool(w.advanced),
      "guards removed and nothing changed - check 2 cannot fail")

# 5. a queued error after destroy must not re-enter the engine
GLibStub.reset()
w = FakeMusic(closed=True, loaded=None)
on_error(w, None, None)
check("late error after destroy does not touch the engine", w.stopped == 0,
      "_stop_playback ran on a destroyed window")
check("late error after destroy says nothing either", not w.flashed,
      "a message was shown on a window that is gone: %r" % (w.flashed,))
w2 = FakeMusic(closed=False)
on_error(w2, None, None)
check("a live error still stops playback", w2.stopped == 1,
      "the guard swallowed real error handling")
check("a live error DOES report itself", len(w2.flashed) == 1,
      "the failure stopped playback in silence again: %r" % (w2.flashed,))
w3 = FakeMusic(closed=True, loaded=None)
on_error_mutant(w3, None, None)
check("MUTANT: unguarded error after destroy DOES stop playback",
      w3.stopped == 1, "guards removed and nothing changed")

# 6. _start_poll must refuse to create a source once closed
GLibStub.reset()
w = FakeMusic(closed=True, loaded=None, poll_id=0)
start_poll(w)
check("_start_poll creates no GLib source after close",
      not GLibStub.added and w._poll_id == 0,
      "a 300ms timeout was armed after close: added=%r id=%r"
      % (GLibStub.added, w._poll_id))
GLibStub.reset()
w = FakeMusic(closed=False, poll_id=0)
start_poll(w)
check("_start_poll still arms the poll while open",
      len(GLibStub.added) == 1 and w._poll_id == 4242,
      "the guard broke normal progress polling")
GLibStub.reset()
w = FakeMusic(closed=True, loaded=None, poll_id=0)
start_poll_mutant(w)
check("MUTANT: unguarded _start_poll DOES arm a source after close",
      len(GLibStub.added) == 1, "guards removed and nothing changed")

# 7. destroy raises the flag BEFORE teardown, so anything dispatched during
#    teardown already sees a closed window. Checked twice: once by running it
#    (the pipeline records the flag as it saw it), once on the source order.
GLibStub.reset()
w = FakeMusic(closed=False, poll_id=99)
on_destroy(w)
check("destroy leaves the window closed", w._closed is True,
      "_closed was never set")
check("pipeline teardown saw _closed already True",
      w._player.closed_when_torn_down is True,
      "the pipeline was stopped while _closed was still False: a bus message "
      "dispatched during teardown would be treated as live")
check("destroy drops the loaded track and the poll source",
      w._loaded_path is None and w._poll_id == 0 and GLibStub.removed == [99],
      "loaded=%r poll=%r removed=%r"
      % (w._loaded_path, w._poll_id, GLibStub.removed))


def stmt_index(fn_node, needle):
    """Index of the first top-level statement whose source mentions needle."""
    for i, st in enumerate(fn_node.body):
        if needle in ast.unparse(st):
            return i
    return None


d = method("_on_destroy")
flag_at = None
for i, st in enumerate(d.body):
    if (isinstance(st, ast.Assign) and "self._closed" in ast.unparse(st.targets[0])):
        flag_at = i
        break
teardown = [x for x in (stmt_index(d, "source_remove"),
                        stmt_index(d, "_stop_length_scan"),
                        stmt_index(d, "set_state")) if x is not None]
check("_closed is assigned before every teardown statement in the source",
      flag_at is not None and teardown and all(flag_at < t for t in teardown),
      "flag at %r, teardown at %r" % (flag_at, teardown))

print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
sys.exit(1 if FAILED else 0)
