#!/usr/bin/env python3
"""THE SURFACES A PERSON MEETS FIRST MUST ARM THE SHARED INPUT RULES.

`nbapp.track_input_modality()` installs one GDK dispatcher per process, and it
carries two rules the whole OS depends on:

  * focus rings appear for the KEYBOARD and never for the mouse
    (GtkWindow:focus-visible defaults True and GTK3 never lowers it), and
  * clicking a window GIVES IT THE KEYBOARD — matchbox does not move focus for
    a click on a DIALOG it did not activate, which is every window this OS
    opens.

It is armed from `nbapp.install_css()`. The Finder, the desktop board and the
panel do not build an `nbapp.AppWindow`; each has its OWN stylesheet installer,
so none of them ever reached that call — and they are exactly the surfaces a
person meets first. Measured on target: the Finder's search box could not be
typed into AT ALL, because the X input focus sat on GTK's 1x1 group-leader
window and no click could move it.

This is a source check on purpose: the defect is a call that is missing, and a
constructed window cannot tell you whether the dispatcher was armed by ITS
module or by something a suite imported earlier in the same process.
"""
import os
import re
import sys

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Every module that installs its OWN stylesheet instead of nbapp's, and the
# function that does it. Add a row here when another surface grows one.
OWN_CSS = {
    "finder.py": "install_css",
    "widgets.py": "_css",
    "shell.py": "install_css",
}

passed, failed = 0, []


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed.append(name)
        print("FAIL %s%s" % (name, ": " + detail if detail else ""))


src = {}
for mod in sorted(OWN_CSS):
    with open(os.path.join(DE, mod), encoding="utf-8") as fh:
        src[mod] = fh.read()

# nbapp itself must still arm it, or the rule has no home at all.
with open(os.path.join(DE, "nbapp.py"), encoding="utf-8") as fh:
    nbapp_src = fh.read()
check("nbapp.install_css arms the dispatcher",
      re.search(r"def install_css\([^)]*\):(?:.|\n)*?track_input_modality\(\)",
                nbapp_src) is not None)
check("...and the dispatcher gives a clicked window the keyboard",
      "get_accept_focus() and not top.is_active()" in nbapp_src)

for mod, fn in sorted(OWN_CSS.items()):
    body = src[mod].split("def %s(" % fn, 1)
    ok = len(body) == 2
    if ok:
        # the function body ends at the next top-level def/class
        rest = body[1]
        end = re.search(r"\n(?:def |class |if __name__)", rest)
        rest = rest[:end.start()] if end else rest
        ok = "track_input_modality()" in rest
    check("%s.%s arms the shared input rules" % (mod, fn), ok,
          "no track_input_modality() call in its own installer")

# ...and the one window that must NOT hold the keyboard. The desktop board is
# furniture: with accept-focus on it became matchbox's focused client for the
# whole session, and the Finder's search box could not be typed into at all.
with open(os.path.join(DE, "widgets.py"), encoding="utf-8") as fh:
    board_src = fh.read()
check("the desktop board declines the keyboard",
      "self.set_accept_focus(False)" in board_src
      and "self.set_accept_focus(True)" not in board_src)

print("\n%d checks, %d passed, %d FAILED"
      % (passed + len(failed), passed, len(failed)))
print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
raise SystemExit(1 if failed else 0)
