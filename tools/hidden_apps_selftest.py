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
import ast
import json
import inspect
import tempfile
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay")
DE = os.path.join(OVERLAY, "opt/notebook/de")
sys.path.insert(0, DE)

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
APPDIR = os.path.join(OVERLAY, "root/Applications")

# An app reaches the image by one of two routes, so a hide has two valid
# shapes and the checks below have to know which one they are looking at:
#
#   BUNDLED      baked into the rootfs overlay. It ships, and is FILTERED out
#                of every launch surface. It must STAY on disk — hide, do not
#                delete — so that unhiding it is deleting one line.
#   NOT BUNDLED  built into the image from another repo at build time. The
#                honest hide is to not build it in at all, so there is nothing
#                on disk and nothing to filter.
#
# The one thing an app must never be is BUNDLED AND REACHABLE, and that is
# what the rest of this file measures.
bundled = sorted(n for n in HIDDEN if n in APPS)
unbundled = sorted(set(HIDDEN) - set(bundled))

print("hidden apps: %s" % (sorted(HIDDEN) or "none"))
print("  bundled, filtered out : %s" % (bundled or "none"))
print("  not bundled at all    : %s\n" % (unbundled or "none"))

# 1. Every hide carries its reason, so the debt is legible.
unexplained = sorted(k for k, v in HIDDEN.items()
                     if not isinstance(v, str) or len(v.strip()) < 20)
check("every hidden app says WHY, in a sentence", not unexplained,
      unexplained)

# 2. An app claiming the NOT-BUNDLED shape must really be absent from the
#    overlay. Otherwise it is in neither shape: nothing filters it (it is not
#    in APP_MODULES, so _hidden_modules cannot translate it) and it ships
#    anyway — the worst of both.
stray = sorted(n for n in unbundled
               if os.path.exists(os.path.join(APPDIR, n + ".app")))
check("every not-bundled hidden app is really absent from the overlay",
      not stray, "in the overlay yet not in APP_MODULES: %s" % stray)


def _scripts_reading_hidden_apps():
    """Build-time scripts that consult finder.HIDDEN_APPS, by name -> source.

    THIS FILE IS EXCLUDED ON PURPOSE. It reads HIDDEN_APPS and it names apps
    in its own comments, so counting itself would make check 3 satisfy itself
    — a gate that cannot go red for the failure it exists to catch. (The same
    trap the /proc fixture in finder_launch_selftest fell into: the two names
    it tested were the two that could not expose the bug.)"""
    me = os.path.basename(os.path.abspath(__file__))
    out = {}
    for d in (os.path.join(REPO, "tools"),
              os.path.join(REPO, "buildroot/board/notebookos")):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn == me or not fn.endswith((".sh", ".py")):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8",
                          errors="replace") as fh:
                    src = fh.read()
            except OSError:
                continue
            if "HIDDEN_APPS" in src:
                out[fn] = src
    return out


# 3. A not-bundled hide must be ACTED ON by something, or the entry is a
#    decision nobody carries out — it reads as withheld and ships anyway.
#    This is also what still catches a TYPO, which check 1 used to catch by
#    demanding every name be in APP_MODULES: a misspelled app is in
#    APP_MODULES under neither spelling AND no build script mentions it, so it
#    fails here instead of quietly passing itself off as "not bundled".
READERS = _scripts_reading_hidden_apps()
unenforced = sorted(n for n in unbundled
                    if not any(n in src for src in READERS.values()))
check("every not-bundled hidden app is acted on by a build script",
      not unenforced,
      "no script that reads HIDDEN_APPS mentions %s (readers: %s)"
      % (unenforced, sorted(READERS) or "none"))

# 4. A BUNDLED hide must still be on disk: hide, do not delete.
if os.path.isdir(APPDIR):
    on_disk = {n[:-4] for n in os.listdir(APPDIR) if n.endswith(".app")}
    shown = on_disk - set(HIDDEN)
    check("every app on disk that is not hidden is a known app",
          not (shown - APPS), sorted(shown - APPS))
    check("no bundled hidden app is missing from disk (hide, do not delete)",
          not (set(bundled) - on_disk), sorted(set(bundled) - on_disk))

# 5. A hidden app must not still reach the user through the desktop board.
try:
    import widgets
    tile_apps = set(getattr(widgets, "TILE_APP", {}).values())
    hidden_mods = {finder.APP_MODULES[a] for a in HIDDEN if a in finder.APP_MODULES}
    leaked = sorted(tile_apps & hidden_mods)
    check("no hidden app still has a desktop tile", not leaked, leaked)
except Exception as exc:                                        # noqa: BLE001
    check("the board could be checked for tiles of hidden apps", False, repr(exc))

# 6. A hidden app must not be the default handler for a file type — opening a
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

# 7. A hidden app must not be listed as software this computer has. Packages
#    is the machine's answer to "what is installed here", and a withheld app
#    sitting in that list — as an Application, with a size and a date — is the
#    same half-hide as an Applications row, reached by a different door. It
#    scans the de/ directory it lives beside, so on the host that is the
#    overlay: exactly the modules the image would carry.
try:
    import packages
    listed = {p[packages.NAME] for p in packages.PACKAGES}
    leaked = sorted(listed & set(HIDDEN))
    check("no hidden app is listed by Packages", not leaked, leaked)
    # ...and the listing is not empty for some unrelated reason, which would
    # make the check above pass without measuring anything.
    check("the Packages listing is non-empty (the check above is not vacuous)",
          len(listed) > 10, "only %d packages listed" % len(listed))
except Exception as exc:                                        # noqa: BLE001
    check("the Packages listing could be checked", False, repr(exc))

# 8. Unsigned discovery metadata cannot unhide an unstable bundled app. A
#    package that was not bundled at all may become visible after installation,
#    but an image hide changes only with image policy (or signed package metadata
#    in a future trust-aware registry), never with installed_apps.json alone.
try:
    if not bundled:
        check("the bundled hide boundary could be measured", False,
              "no bundled hidden app to measure it with")
    else:
        name = bundled[0]
        mod = finder.APP_MODULES[name]
        was_installed = name in finder.INSTALLED_APPS
        finder.INSTALLED_APPS.add(name)
        try:
            check("unsigned registry metadata cannot unhide a bundled app",
                  finder._is_hidden(name), name)
            check("...and its bundled module remains withheld from launch",
                  mod in finder._hidden_modules(), mod)
        finally:
            if not was_installed:
                finder.INSTALLED_APPS.discard(name)
    if unbundled:
        name = unbundled[0]
        was_installed = name in finder.INSTALLED_APPS
        finder.INSTALLED_APPS.add(name)
        try:
            check("an installed app absent from the image may become visible",
                  not finder._is_hidden(name), name)
        finally:
            if not was_installed:
                finder.INSTALLED_APPS.discard(name)
except Exception as exc:                                        # noqa: BLE001
    check("the installed-app hide boundary could be measured", False, repr(exc))

# A package registry cannot borrow a hidden built-in's display name while
# pointing at another module: that would exempt the name but launch the old,
# intentionally withheld built-in through APP_MODULES.
try:
    if bundled:
        name = bundled[0]
        original_mod = finder.APP_MODULES[name]
        original_de = finder.DE_DIR
        original_installed = set(finder.INSTALLED_APPS)
        fake_de = tempfile.mkdtemp(prefix="finder-collision-")
        with open(os.path.join(fake_de, "installed_apps.json"), "w") as fh:
            json.dump({name: {"module": "vendor_collision",
                              "kind": "Utility"}}, fh)
        finder.DE_DIR = fake_de
        try:
            finder._merge_installed_apps()
            check("an installed display-name collision cannot expose a hidden built-in",
                  finder.APP_MODULES[name] == original_mod
                  and name not in finder.INSTALLED_APPS,
                  (finder.APP_MODULES[name], finder.INSTALLED_APPS))
        finally:
            finder.DE_DIR = original_de
            finder.INSTALLED_APPS.clear()
            finder.INSTALLED_APPS.update(original_installed)
            import shutil
            shutil.rmtree(fake_de, ignore_errors=True)
except Exception as exc:                                        # noqa: BLE001
    check("installed display-name collisions could be checked", False, repr(exc))

# Dynamic package mappings can legitimately change module name on upgrade and
# must also disappear on uninstall without requiring a Finder restart.
try:
    original_de = finder.DE_DIR
    fake_de = tempfile.mkdtemp(prefix="finder-upgrade-")
    finder.DE_DIR = fake_de
    try:
        reg_path = os.path.join(fake_de, "installed_apps.json")
        with open(reg_path, "w") as fh:
            json.dump({"Radio": {"module": "radio_v1", "kind": "Utility"}}, fh)
        finder._merge_installed_apps()
        with open(reg_path, "w") as fh:
            json.dump({"Radio": {"module": "radio_v2", "kind": "Messaging"}}, fh)
        finder._merge_installed_apps()
        check("a signed package mapping can change module name without restart",
              finder.APP_MODULES.get("Radio") == "radio_v2"
              and finder.APP_KIND.get("Radio") == "Messaging"
              and "Radio" in finder.INSTALLED_APPS,
              (finder.APP_MODULES.get("Radio"), finder.INSTALLED_APPS))
        with open(reg_path, "w") as fh:
            json.dump({}, fh)
        finder._merge_installed_apps()
        check("uninstall removes a dynamic app mapping without restart",
              "Radio" not in finder.APP_MODULES
              and "Radio" not in finder.APP_KIND
              and "Radio" not in finder.INSTALLED_APPS)
    finally:
        finder.DE_DIR = original_de
        finder.APP_MODULES.pop("Radio", None)
        finder.APP_KIND.pop("Radio", None)
        finder.INSTALLED_APPS.discard("Radio")
        import shutil
        shutil.rmtree(fake_de, ignore_errors=True)
except Exception as exc:                                        # noqa: BLE001
    check("dynamic package upgrades could be checked", False, repr(exc))

def calls(method, name, first_arg=None, keywords=None):
    """A real call in executable syntax, never a comment/dead string."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = (node.func.attr if isinstance(node.func, ast.Attribute)
                  else node.func.id if isinstance(node.func, ast.Name) else "")
        if callee != name:
            continue
        if first_arg is not None:
            if not node.args or not isinstance(node.args[0], ast.Constant) \
                    or node.args[0].value != first_arg:
                continue
        values = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if any(k not in values or not isinstance(values[k], ast.Constant)
               or values[k].value != v for k, v in (keywords or {}).items()):
            continue
        return True
    return False


check("every Applications rebuild refreshes the cross-process app registry",
      calls(finder.Finder.load, "_merge_installed_apps"))
check("returning from Packages rebuilds the visible Applications catalogue",
      calls(finder.Finder._app_exited, "load", "Applications",
            {"record": False, "keep_filter": True}))

print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
