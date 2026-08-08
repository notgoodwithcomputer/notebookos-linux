#!/usr/bin/env python3
"""Display-free contracts for Packages app visibility and truthful copy."""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
SOURCE = os.path.join(DE, "packages.py")
sys.path.insert(0, DE)

import packages as pk  # noqa: E402

failures = []


def check(condition, message, detail=""):
    print(("ok   " if condition else "FAIL ") + message
          + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(message)


class Harness(object):
    _removed_apps_path = pk.Packages._removed_apps_path
    _load_removed_apps = pk.Packages._load_removed_apps
    _set_app_removed = pk.Packages._set_app_removed
    _on_uninstall = pk.Packages._on_uninstall
    _on_restore = pk.Packages._on_restore

    def __init__(self):
        self.sel = 0
        self._removed_apps = self._load_removed_apps()
        self.rebuilds = 0

    def _rebuild_detail(self):
        self.rebuilds += 1


def main():
    home = tempfile.mkdtemp(prefix="nb-packages-store-")
    old_home = os.environ.get("NB_HOME")
    old_packages = pk.PACKAGES
    os.environ["NB_HOME"] = home
    app_path = os.path.join(DE, "writer.py")
    sys_path = os.path.join(DE, "finder.py")
    app = ("Writer", "writer", "Application", "1 KB", 1024, "", 0, "",
           app_path)
    system = ("Finder", "finder", "System", "1 KB", 1024, "", 0, "",
              sys_path)
    pk.PACKAGES = [app, system]
    try:
        h = Harness()
        path = h._removed_apps_path()

        # Read-modify-write must preserve another app already in Finder's list,
        # and the bytes are the exact JSON-list shape Finder reads.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b'["Calendar"]')
        h._on_uninstall()
        with open(path, "rb") as fh:
            actual = fh.read()
        check(actual == b'["Calendar", "Writer"]',
              "Uninstall writes Finder's exact sorted JSON-list format",
              repr(actual))
        check(h._removed_apps == {"Calendar", "Writer"} and h.rebuilds == 1,
              "Uninstall updates state and the inspector immediately")

        h._on_restore()
        with open(path, "rb") as fh:
            actual = fh.read()
        check(actual == b'["Calendar"]',
              "Restore removes only the selected display name", repr(actual))

        # A system package reaches the same internal method in this headless
        # test. The kind guard must prevent any store or UI mutation.
        h.sel = 1
        before = actual
        rebuilds = h.rebuilds
        h._on_uninstall()
        with open(path, "rb") as fh:
            after = fh.read()
        check(after == before and h.rebuilds == rebuilds,
              "System modules expose no uninstall path")

        # Merely loading/browsing malformed data treats it as empty and leaves
        # the original evidence byte-identical. No action follows this load.
        damaged = b'{not a JSON list\n'
        with open(path, "wb") as fh:
            fh.write(damaged)
        browsed = Harness()
        browsed._load_removed_apps()
        with open(path, "rb") as fh:
            after_browse = fh.read()
        check(browsed._removed_apps == set() and after_browse == damaged,
              "Browsing a damaged store never overwrites it", repr(after_browse))

        with open(SOURCE, encoding="utf-8") as fh:
            source = fh.read()
        for promise in ("New packages install from a USB stick.",
                        "Package updates install from a USB stick."):
            check(promise not in source,
                  "promissory sentence is absent: %s" % promise)
        check('.sorthdr label { color: inherit; }' in source,
              "sort-header CSS explicitly carries colour to its label")
        check('color: #6E695E;' in source,
              "sort-header uses the design-token muted colour")
    finally:
        pk.PACKAGES = old_packages
        if old_home is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = old_home
        shutil.rmtree(home)

    print("\n%d failed" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
