#!/usr/bin/env python3
"""The install wizard must actually ADVANCE, and its step rail must never
navigate on its own.

The rail is a column of Gtk.ToggleButtons so the current step is exposed to
assistive technology. But set_active emits "clicked", so restating the rail
inside _set_step also fired _on_rail_click for the step being UNLIT -- with
the PREVIOUS index, which is <= _max_reached and therefore passes every guard
in that handler and navigates straight back. Next went welcome -> target ->
welcome for ever, each bounce spawning another disk-scan thread, until the
recursion limit killed it. The installer could not leave its first screen.

Every other installer suite stayed green through this: lifecycle, target,
writes, unknown-disk and oem all drive _set_step() DIRECTLY and so never
emit the signal that caused it. This one presses the actual buttons.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook")
sys.path.insert(0, os.path.join(DE, "de"))
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", "/tmp/nbhome-instrail")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

# A ping-pong blows this long before it would blow the default 1000, and it
# fails as a RecursionError rather than hanging the suite.
sys.setrecursionlimit(200)

import gi                                            # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                        # noqa: E402,F401
import installer                                     # noqa: E402

checks = 0


def chk(name, ok, detail=""):
    global checks
    checks += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "  <- %s" % detail))
    if not ok:
        raise SystemExit(1)


app = installer.Installer()
names = [s[0] for s in app.STEPS]
chk("the wizard opens on the first step", app._step == 0, "step %d" % app._step)

# --- forward, by pressing the button a person presses -----------------------
for n in range(3):
    before = app._step
    app.next_btn.clicked()
    chk("Next advances past %r" % names[before], app._step > before,
        "went %d -> %d" % (before, app._step))

# --- the rail states the step; it does not choose it ------------------------
lit = [j for j, e in enumerate(app._rail_rows) if e[0].get_active()]
chk("exactly one rail row is lit", len(lit) == 1, "lit=%s" % lit)
chk("the lit row IS the current step", lit == [app._step],
    "lit=%s step=%d" % (lit, app._step))

# --- back, and forward again ------------------------------------------------
before = app._step
app.back_btn.clicked()
chk("Back goes back exactly one step", app._step == before - 1,
    "went %d -> %d" % (before, app._step))
before = app._step
app.next_btn.clicked()
chk("Next still advances after a Back", app._step == before + 1,
    "went %d -> %d" % (before, app._step))

# --- clicking a completed rail row is real navigation, and only backwards ----
target = app._step
app._rail_rows[0][0].clicked()
chk("clicking a completed rail row navigates to it", app._step == 0,
    "step %d" % app._step)
lit = [j for j, e in enumerate(app._rail_rows) if e[0].get_active()]
chk("the rail follows that navigation", lit == [0], "lit=%s" % lit)

ahead = len(names) - 1
app._rail_rows[ahead][0].clicked()
chk("a rail row beyond the furthest step is refused", app._step == 0,
    "step %d" % app._step)

print("\nINSTALLER RAIL NAVIGATION SELFTEST: %d checks, all pass" % checks)
print("RESULT: ALL PASS")
