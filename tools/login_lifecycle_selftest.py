#!/usr/bin/env python3
"""login_lifecycle_selftest -- one Enter is one attempt at the sign-in screen.

    python3 tools/login_lifecycle_selftest.py        (no display needed)

WHAT THIS PROTECTS
de/login.py counts failed attempts and, from the third one, holds the field
insensitive for a few seconds so guessing is not free. That count only means
anything if it counts what a person actually did.

It did not. The password field carried set_activates_default(True) AND had its
"activate" signal connected to _try. GtkEntry's own class handler for that
signal is what implements activates-default, and it runs AFTER connected
handlers -- so one press of Enter ran _try, then activated the default widget,
which is the Sign In button, connected to the same _try. Measured on GTK 3:
one synthesised Return gave ['entry-activate', 'button-clicked'].

So every Enter counted twice. The pause meant for the third wrong password
arrived on the second, its length (min(5, tries)) ran ahead of the truth, and a
CORRECT password paid for a second SHA-512 crypt after the screen had already
hidden itself and quit the main loop.

The second half is the same defect from the other side: _try did not ask
whether a pause was already running, so a submission arriving inside that
window was counted anyway and armed a SECOND timer on top of the first. Two
timers, and the field came back at whichever fired first.

Both are checked here without a display: the wiring by reading the source (the
double path is a fact about the two lines, not about anything runnable
headless), the counting by driving the real _try with stub widgets.

AND THE TIMERS MUST NOT OUTLIVE THE WINDOW (part 3). `destroy` was connected
straight to Gtk.main_quit, so both of this screen's timers -- the 30-second
clock and the failure pause -- were still armed over a window whose widgets had
been finalised, and their callbacks went on setting text on a destroyed label
and handing focus to a destroyed entry. That is checked here the same way: the
real _on_destroy, _tick_clock, _re_enable and _try are driven against stub
widgets, and the stubs used for the closed cases are ones that FAIL if they are
touched at all.
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
LOGIN = os.path.join(DE, "login.py")
sys.path.insert(0, DE)

fails = []


def check(ok, what):
    print("%s  %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        fails.append(what)


# -- 1. the wiring: the field must have exactly ONE way to submit -------------

with open(LOGIN, encoding="utf-8") as fh:
    tree = ast.parse(fh.read(), LOGIN)

activates_default = []          # entry.set_activates_default(True)
activate_connect = []           # entry.connect("activate", ...)
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not isinstance(node.func,
                                                        ast.Attribute):
        continue
    target = node.func.value
    if not (isinstance(target, ast.Attribute) and target.attr == "entry"):
        continue
    name = node.func.attr
    if name == "set_activates_default":
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and arg.value:
            activates_default.append(node.lineno)
    elif name == "connect" and node.args:
        sig = node.args[0]
        if isinstance(sig, ast.Constant) and sig.value == "activate":
            activate_connect.append(node.lineno)

check(len(activate_connect) == 1,
      "the password field connects 'activate' exactly once (found %d)"
      % len(activate_connect))
check(not (activates_default and activate_connect),
      "Enter has ONE path to _try: no set_activates_default(True) on a field "
      "whose 'activate' is already connected (lines %s / %s)"
      % (activates_default or "-", activate_connect or "-"))

# The tear-down below is only reached if something calls it, and the tests that
# drive _on_destroy directly would go on passing over a window that never
# connected it. That is a fact about one line, so it is read off the source.
destroy_targets = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not isinstance(node.func,
                                                        ast.Attribute):
        continue
    if node.func.attr != "connect" or len(node.args) < 2:
        continue
    sig = node.args[0]
    if not (isinstance(sig, ast.Constant) and sig.value == "destroy"):
        continue
    handler = node.args[1]
    destroy_targets.append(handler.attr if isinstance(handler, ast.Attribute)
                           else getattr(handler, "id", "?"))

check(destroy_targets == ["_on_destroy"],
      "'destroy' goes to _on_destroy, not straight to Gtk.main_quit (found %s)"
      % (destroy_targets or "nothing"))


# -- 2. the counting: one call to _try is one attempt -------------------------

import login                                                    # noqa: E402


class Style:
    def add_class(self, *_a):
        pass

    def remove_class(self, *_a):
        pass


class Widget:
    """Enough of a Gtk widget for _try, and it remembers the one thing that
    matters here: whether the field is accepting input."""

    def __init__(self):
        self.text = ""
        self.sensitive = True
        self._style = Style()

    def get_text(self):
        return self.text

    def set_text(self, s):
        self.text = s

    def get_style_context(self):
        return self._style

    def set_sensitive(self, on):
        self.sensitive = on

    def grab_focus(self):
        pass

    def show(self):
        pass

    def set_no_show_all(self, *_a):
        pass


timers = []


def fake_timeout_add_seconds(secs, cb, *a):
    timers.append((secs, cb))
    return len(timers)          # a truthy source id, like GLib's


login.GLib.timeout_add_seconds = fake_timeout_add_seconds
login.verify = lambda user, password: False      # every attempt is wrong

win = login.Login.__new__(login.Login)           # no window, no display
win.lock = False
win.user = "tester"
win._tries = 0
win._wait_id = 0
win._clock_id = 0
win._closed = False
win.ok = False
win._kb_groups = []
win._kb_btns = []
win._kb_active = 0
win.entry = Widget()
win.error = Widget()
win._show = Widget()
win._recall = Widget()
win._go = Widget()

for _ in range(2):
    win.entry.set_text("wrong")
    win._try()
check(win._tries == 2 and not timers,
      "two wrong passwords: counted 2 (got %d), no pause yet (%d armed)"
      % (win._tries, len(timers)))
check(win.entry.sensitive,
      "the field is still accepting input after two failures")

win.entry.set_text("wrong")
win._try()
check(win._tries == 3 and len(timers) == 1,
      "the third failure arms exactly one pause (tries=%d, timers=%d)"
      % (win._tries, len(timers)))
check(not win.entry.sensitive and not win._go.sensitive,
      "the field and the button are held during the pause")

# THE REGRESSION. A submission that lands inside the pause -- the second half
# of a doubled Enter, a click already queued when the field went insensitive --
# is not an attempt. Before the fix this reached the counter and armed timer #2.
win.entry.set_text("wrong")
win._try()
check(win._tries == 3,
      "a submit during the pause is not counted (tries=%d, want 3)"
      % win._tries)
check(len(timers) == 1,
      "a submit during the pause does not arm a second timer (timers=%d)"
      % len(timers))

# When the pause ends, the screen accepts attempts again -- the guard must not
# be able to wedge the field shut, which would be the brick this file's header
# in login.py exists to prevent.
secs, cb = timers[0]
check(secs == 3, "the first pause is min(5, tries) = 3s (got %s)" % secs)
check(cb() is False, "_re_enable does not repeat")
check(win.entry.sensitive and win._go.sensitive and not win._wait_id,
      "the field comes back after the pause")
win.entry.set_text("wrong")
win._try()
check(win._tries == 4 and len(timers) == 2,
      "attempts count again after the pause (tries=%d, timers=%d)"
      % (win._tries, len(timers)))

# A successful sign-in quits the main loop; anything still queued behind it
# must not run crypt again over a screen that is already gone.
win2 = login.Login.__new__(login.Login)
win2._closed = False
win2._clock_id = 0
win2.ok = True
win2._wait_id = 0
win2._closed = False
win2.user = "tester"
win2.entry = Widget()
win2._tries = 0
login.verify = lambda u, p: (_ for _ in ()).throw(
    AssertionError("verify() called after sign-in"))
try:
    win2._try()
    check(True, "a submit after a successful sign-in re-runs nothing")
except AssertionError as exc:
    check(False, str(exc))

# Timer sinks release their own IDs before returning and never touch a widget
# once destruction has raised the owner gate.
clock = login.Login.__new__(login.Login)
clock._closed = False
clock._clock_id = 21
clock.clock = Widget()
clock.date = Widget()
check(clock._tick_clock() is True and clock.clock.text and clock.date.text,
      "a live clock tick updates both labels and keeps repeating")
before = (clock.clock.text, clock.date.text)
clock._closed = True
clock._clock_id = 22
check(clock._tick_clock() is False and clock._clock_id == 0
      and (clock.clock.text, clock.date.text) == before,
      "a closed clock tick unregisters without touching labels")

pause = login.Login.__new__(login.Login)
pause._closed = True
pause._wait_id = 23
pause.entry, pause._go = Widget(), Widget()
pause.entry.set_sensitive(False)
pause._go.set_sensitive(False)
check(pause._re_enable() is False and pause._wait_id == 0
      and not pause.entry.sensitive and not pause._go.sensitive,
      "a closed backoff sink unregisters without re-enabling widgets")

# Destroy marks closed before cancellation, owns both IDs to zero, and quits
# only once even if GTK delivers destroy more than once.
owner = login.Login.__new__(login.Login)
owner._closed = False
owner._clock_id, owner._wait_id = 31, 32
events = []
real_remove = login.GLib.source_remove
real_quit = login.Gtk.main_quit
login.GLib.source_remove = lambda sid: events.append(("remove-%d" % sid,
                                                       owner._closed))
login.Gtk.main_quit = lambda: events.append(("quit", owner._closed))
try:
    first, second = owner._on_destroy(), owner._on_destroy()
finally:
    login.GLib.source_remove = real_remove
    login.Gtk.main_quit = real_quit
check(first is False and second is False and owner._closed,
      "destroy is idempotent and raises the Login closed gate")
check(events == [("remove-31", True), ("remove-32", True), ("quit", True)],
      "destroy cancels both timers then quits exactly once behind the gate")
check(owner._clock_id == 0 and owner._wait_id == 0,
      "destroy clears all Login timer ownership")

closed_try = login.Login.__new__(login.Login)
closed_try._closed = True
closed_try._wait_id = 0
closed_try.ok = False
closed_try.user = "tester"
closed_try.entry = Widget()
login.verify = lambda *_a: (_ for _ in ()).throw(
    AssertionError("verify called after Login destruction"))
try:
    closed_try._try()
    check(True, "a queued submit after destruction verifies nothing")
except AssertionError as exc:
    check(False, str(exc))


# -- 3. the tear-down: no timer may outlive the window ------------------------

class Trap:
    """A widget that must not be touched at all.

    A closed window's callbacks are exactly the ones whose widgets are gone, so
    "did it leave them alone?" cannot be asked of a stub that politely answers.
    Every attribute access here is the defect."""

    def __init__(self, why):
        object.__setattr__(self, "_why", why)

    def __getattr__(self, name):
        raise AssertionError("%s touched a widget (.%s)"
                             % (object.__getattribute__(self, "_why"), name))


def fresh(closed, **kw):
    """A Login with no window behind it, closed or live."""
    w = login.Login.__new__(login.Login)
    w.lock = False
    w.user = "tester"
    w._tries = 0
    w._wait_id = 0
    w._clock_id = 0
    w._closed = closed
    w.ok = False
    for k, v in kw.items():
        setattr(w, k, v)
    return w


# The clock, live: it still says the time, and it still repeats.
live = fresh(False, clock=Widget(), date=Widget(), _clock_id=11)
check(live._tick_clock() is True, "a live clock tick repeats")
check(live.clock.text and live.date.text,
      "a live clock tick still sets the time and the date (%r / %r)"
      % (live.clock.text, live.date.text))
check(live._clock_id == 11, "a live clock tick keeps its source id")

# The clock, closed: this is the callback that ran every 30 seconds over a
# finalised GtkLabel. It must touch nothing and stop.
gone = fresh(True, clock=Trap("the clock tick after destroy"),
             date=Trap("the clock tick after destroy"), _clock_id=11)
try:
    check(gone._tick_clock() is False, "a clock tick after destroy stops")
except AssertionError as exc:
    check(False, str(exc))
check(gone._clock_id == 0,
      "a clock tick after destroy releases its source id (got %r)"
      % gone._clock_id)

# The pause, live: unchanged -- the field comes back and the source is done.
live = fresh(False, entry=Widget(), _go=Widget(), _wait_id=12)
live.entry.set_sensitive(False)
live._go.set_sensitive(False)
check(live._re_enable() is False, "a live _re_enable does not repeat")
check(live.entry.sensitive and live._go.sensitive,
      "a live _re_enable brings the field and the button back")
check(live._wait_id == 0, "a live _re_enable releases its source id")

# The pause, closed: up to five seconds of it can be in flight when the screen
# goes. It must release the id -- the source is finished either way, and an id
# left set is one _on_destroy would try to remove twice -- and touch nothing.
gone = fresh(True, entry=Trap("the pause after destroy"),
             _go=Trap("the pause after destroy"), _wait_id=12)
try:
    check(gone._re_enable() is False, "a _re_enable after destroy does nothing")
except AssertionError as exc:
    check(False, str(exc))
check(gone._wait_id == 0,
      "a _re_enable after destroy releases its source id (got %r)"
      % gone._wait_id)

# A submit that lands after the screen is gone: same case as the pause and the
# successful sign-in above, one step further along.
gone = fresh(True, entry=Trap("a submit after destroy"),
             error=Trap("a submit after destroy"),
             _show=Trap("a submit after destroy"),
             _recall=Trap("a submit after destroy"),
             _go=Trap("a submit after destroy"))
login.verify = lambda u, p: (_ for _ in ()).throw(
    AssertionError("verify() called after destroy"))
try:
    gone._try()
    check(True, "a submit after destroy runs nothing")
except AssertionError as exc:
    check(False, str(exc))
check(gone._tries == 0, "a submit after destroy is not counted (tries=%d)"
      % gone._tries)

# THE TEAR-DOWN ITSELF. Both timers cancelled, both ids cleared, the loop
# quit exactly once -- and "closed" set BEFORE any of it, because a callback
# GLib has already dispatched is behind us in the queue and reads that flag.
real_source_remove = login.GLib.source_remove
real_main_quit = login.Gtk.main_quit

removed = []                    # (source id, was _closed already set?)
quits = []                      # was _closed already set?
dying = fresh(False, _clock_id=21, _wait_id=22)


def fake_source_remove(sid):
    removed.append((sid, dying._closed))
    return True


def fake_main_quit(*_a):
    quits.append(dying._closed)


try:
    login.GLib.source_remove = fake_source_remove
    login.Gtk.main_quit = fake_main_quit

    check(dying._on_destroy() is False, "_on_destroy returns False")
    check(dying._closed is True, "_on_destroy marks the window closed")
    check([sid for sid, _ in removed] == [21, 22],
          "_on_destroy cancels the clock and the pause (removed %s)"
          % ([sid for sid, _ in removed] or "nothing"))
    check(all(was for _, was in removed) and quits == [True],
          "'closed' is set BEFORE anything is cancelled or quit "
          "(cancels %s, quits %s)"
          % ([was for _, was in removed], quits))
    check(dying._clock_id == 0 and dying._wait_id == 0,
          "_on_destroy clears both source ids (clock=%r, wait=%r)"
          % (dying._clock_id, dying._wait_id))
    check(len(quits) == 1, "_on_destroy quits the main loop once (got %d)"
          % len(quits))

    # Emitted twice -- hide-then-destroy, a WM delete on the way out. A second
    # main_quit would pop a loop this screen does not own, and a second
    # source_remove would be handed ids that are already gone.
    dying._on_destroy()
    check(len(quits) == 1,
          "a second destroy does not quit again (quits=%d)" % len(quits))
    check(len(removed) == 2,
          "a second destroy does not re-remove dead sources (removes=%d)"
          % len(removed))

    # Cancelling is best-effort: a source that already fired and returned False
    # is gone, and GLib raises rather than shrugging. The loop must still quit.
    def angry_source_remove(sid):
        raise ValueError("source %d is already gone" % sid)

    login.GLib.source_remove = angry_source_remove
    stubborn = fresh(False, _clock_id=31, _wait_id=32)
    try:
        ok = stubborn._on_destroy() is False
    except Exception as exc:                                   # noqa: BLE001
        ok = False
        check(False, "_on_destroy survives a source that is already gone "
                     "(raised %s)" % exc)
    else:
        check(ok, "_on_destroy survives a source that is already gone")
    check(len(quits) == 2 and stubborn._clock_id == 0 and
          stubborn._wait_id == 0,
          "a failed cancel still quits and still clears the ids "
          "(quits=%d, clock=%r, wait=%r)"
          % (len(quits), stubborn._clock_id, stubborn._wait_id))
finally:
    login.GLib.source_remove = real_source_remove
    login.Gtk.main_quit = real_main_quit

check(login.GLib.source_remove is real_source_remove and
      login.Gtk.main_quit is real_main_quit,
      "the real GLib.source_remove and Gtk.main_quit are put back")

print()
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("all checks passed")
