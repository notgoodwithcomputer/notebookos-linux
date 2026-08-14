#!/usr/bin/env python3
"""
Selftest for finder.HIDDEN_APPS — the ship-time list of apps withheld from the
Applications folder because they are not finished.

Hiding an unfinished app is the honest alternative to shipping a control that
does nothing. But a hide is easy to get wrong in ways nobody notices: a name
that does not match any real app hides nothing, an app hidden without a reason
is undocumented debt, and an app that is hidden but still has a desktop tile or
a file association is only half hidden — the user meets it anyway, by a route
nobody checked.

Run as:
  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de python3 hidden_apps_selftest.py
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                             # noqa: E402,F401

import finder                                             # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


HIDDEN = getattr(finder, "HIDDEN_APPS", {})
APPS = set(finder.APP_MODULES)

print("hidden apps: %s\n" % (sorted(HIDDEN) or "none"))

# 1. A hide that names nothing hides nothing.
unknown = sorted(set(HIDDEN) - APPS)
check("every hidden app is a real app", not unknown,
      "not in APP_MODULES: %s" % unknown)

# 2. Every hide carries its reason, so the debt is legible.
unexplained = sorted(k for k, v in HIDDEN.items()
                     if not isinstance(v, str) or len(v.strip()) < 20)
check("every hidden app says WHY, in a sentence", not unexplained,
      unexplained)

# 3. The .app stub should be gone from the shipped image too, or the hide is
#    relying on one filter in one code path.
APPDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "buildroot/board/notebookos/rootfs-overlay/root/Applications")
if os.path.isdir(APPDIR):
    on_disk = {n[:-4] for n in os.listdir(APPDIR) if n.endswith(".app")}
    shown = on_disk - set(HIDDEN)
    check("every app on disk that is not hidden is a known app",
          not (shown - APPS), sorted(shown - APPS))
    check("no hidden app is missing from disk (hide, do not delete)",
          not (set(HIDDEN) - on_disk), sorted(set(HIDDEN) - on_disk))

# 4. A hidden app must not still reach the user through the desktop board.
try:
    import widgets
    tile_apps = set(getattr(widgets, "TILE_APP", {}).values())
    hidden_mods = {finder.APP_MODULES[a] for a in HIDDEN if a in finder.APP_MODULES}
    leaked = sorted(tile_apps & hidden_mods)
    check("no hidden app still has a desktop tile", not leaked, leaked)
except Exception as exc:                                        # noqa: BLE001
    check("the board could be checked for tiles of hidden apps", False, repr(exc))

# 5. A hidden app must not be the default handler for a file type — opening a
#    document would launch something we have declared unfit to be seen.
try:
    hidden_mods = {finder.APP_MODULES[a] for a in HIDDEN if a in finder.APP_MODULES}
    # Measure the RESOLVER, not the map: FILE_APPS may keep a hidden app's
    # association on record (unhiding restores it with no edit), but
    # _default_app_for must WITHHOLD it — the user's double-click is the
    # route that matters. _default_app_for reads no instance state, so it is
    # driven unbound here.
    leaks = {}
    for ext, mod in getattr(finder, "FILE_APPS", {}).items():
        if mod in hidden_mods:
            resolved = finder.Finder._default_app_for(None, ext)
            if resolved in hidden_mods:
                leaks[ext] = resolved
    check("no file type opens with a hidden app", not leaks, leaks)
except Exception as exc:                                        # noqa: BLE001
    check("file associations could be checked", False, repr(exc))

print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
