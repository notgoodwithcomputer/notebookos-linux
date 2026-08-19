#!/usr/bin/env python3
"""Headless ownership checks for GBA Emulator's deferred ROM launch."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import gbaemu  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


app = gbaemu.GbaEmu.__new__(gbaemu.GbaEmu)
app._closed = False
app._launch_source = 11
played = []
app._play = lambda path: played.append(path)
check(app._launch_pending("game.gba") is False and app._launch_source == 0,
      "a live launch sink clears ownership")
check(played == ["game.gba"], "a live launch sink starts its ROM exactly once")
app._closed, app._launch_source = True, 12
check(app._launch_pending("late.gba") is False and app._launch_source == 0,
      "a closed dispatched launch sink clears ownership")
check(played == ["game.gba"], "a closed launch sink starts no game")


class Session:
    def __init__(self, owner, events): self.owner, self.events = owner, events
    def stop(self): self.events.append(("stop", self.owner._closed))
    def _finish(self): self.events.append(("finish", self.owner._closed))


app = gbaemu.GbaEmu.__new__(gbaemu.GbaEmu)
app._closed = False
app._scan_source = 20
app._launch_source = 21
events = []
app._session = Session(app, events)
# No _save_settings any more: gbaemu's whole config was `fullscreen` and
# `scale`, neither of which could act on anything, so the file, its loader and
# its saver are gone. The stub is kept so a REGRESSION that reintroduces a
# save on the teardown path shows up as an unexpected event below.
app._save_settings = lambda: events.append(("save", app._closed))
real_remove = gbaemu.GLib.source_remove
gbaemu.GLib.source_remove = lambda sid: events.append(
    ("remove-%d" % sid, app._closed))
try:
    first, second = app._on_destroy(), app._on_destroy()
finally:
    gbaemu.GLib.source_remove = real_remove
check(first is False and second is False and app._closed,
      "destroy is idempotent and raises the closed gate")
check(events == [("remove-20", True), ("remove-21", True),
                 ("stop", True), ("finish", True)],
      "destroy cancels scanning and launch, then tears down the session")
check(not [e for e in events if e[0] == "save"],
      "destroy writes no settings file — there is none to write")
check(app._scan_source == 0 and app._launch_source == 0 and
      app._session is None,
      "destroy clears scan, launch, and session ownership")

app._session = object()
app._launch_time = 0
app._flash = lambda *_a: events.append(("flash", app._closed))
app.present = lambda: events.append(("present", app._closed))
before = list(events)
app._on_game_end()
check(app._session is None and events == before,
      "a game-end delivery after close releases session without touching UI")

# The control: live, that same delivery DOES surface UI — otherwise the check
# above passes on a method that simply never says anything.
app._closed = False
app._session = object()
app._launch_time = gbaemu.time.monotonic()
app._on_game_end()
check(events[len(before):] == [("flash", False), ("present", False)],
      "a live game-end still reports the quick exit and raises the launcher")

# _play carries the same gate, so no other caller outlives the window either.
app._closed = True
app._session = None
touched = []
app._vbam_path = lambda: touched.append("vbam_path")
check(app._play("game.gba") is None and touched == [],
      "playing on a closed window returns before it looks for the core")

# What GLib is actually handed: an owned callback whose id is kept, not the
# fire-and-forget lambda this replaced.
import inspect  # noqa: E402
src = inspect.getsource(gbaemu.GbaEmu.__init__)
check("self._launch_source = GLib.idle_add(self._launch_pending, rompath)"
      in src and "idle_add(lambda" not in src,
      "the constructor stores the launch source id and schedules no lambda")
check(0 < src.index("self._closed = False") < src.index("GLib.idle_add"),
      "the closed gate is initialised before anything can be scheduled")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
