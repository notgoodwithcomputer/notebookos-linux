#!/usr/bin/env python3
"""Display-free first-run honesty contracts for NotebookOS apps.

The visual/control-driving audit needs a real GTK display and is reported as a
named SKIP when one is unavailable.  Pure policy checks still run everywhere,
always with a newly-created NB_HOME.
"""
import ast
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

APPS = (
    "music", "video", "media", "ebook", "maps", "language", "workout",
    "mealplanner", "cookbook", "bills", "contacts", "tasks", "journal",
    "novel", "writer", "screenplay", "sequencer", "illustrator", "calendar",
    "g2048", "gbaemu", "gbasdk", "finder", "packages", "sysmon",
    "calculator", "academics", "accounting",
)


class EmptyStateSelftest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nb-empty-state-")
        self._old_home = os.environ.get("NB_HOME")
        os.environ["NB_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = self._old_home
        self._tmp.cleanup()

    def test_00_every_scoped_app_has_parseable_ui_module(self):
        failures = []
        for app in APPS:
            path = DE / (app + ".py")
            if not path.is_file():
                failures.append("%s: missing de/%s.py" % (app, app))
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            except SyntaxError as exc:
                failures.append("%s: syntax error: %s" % (app, exc))
                continue
            classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
            if not classes:
                failures.append("%s: no UI class found" % app)
        self.assertFalse(failures, "SCOPED_UI_MODULES: " + "; ".join(failures))

    def test_gbasdk_empty_find_distinguishes_empty_project_from_no_match(self):
        sdk = importlib.import_module("gbasdk")
        label = sdk._find_empty_label(False, "")
        self.assertNotEqual(
            label, "No results",
            "GBASDK_EMPTY_FIND_LIE: a fresh project must not report a failed "
            "search as 'No results'",
        )
        self.assertEqual(
            sdk._find_empty_label(True, "sprite"), "No results",
            "GBASDK_NO_MATCH_TRUTH: a non-empty project may honestly report "
            "that a query has no results",
        )

    def test_gbasdk_empty_find_pass_mutant(self):
        sdk = importlib.import_module("gbasdk")
        honest = sdk._find_empty_label
        mutant = lambda _has_resources, _query: "No results"
        with self.assertRaises(AssertionError, msg=(
                "GBASDK_PASS_MUTANT_RED: reintroduced 'No results' lie was not caught")):
            self.assertNotEqual(mutant(False, ""), "No results")
        self.assertNotEqual(
            honest(False, ""), "No results",
            "GBASDK_PASS_MUTANT_GREEN: restored policy did not pass",
        )

    def test_runtime_control_drive_requires_real_display(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
        except Exception as exc:
            self.skipTest("GTK import unavailable: %s" % exc)
        ok, _argv = Gtk.init_check([])
        if not ok:
            self.skipTest(
                "real DISPLAY unavailable; cannot construct windows, enumerate "
                "visible controls, or drive active controls honestly"
            )
        self.assertTrue(ok, "GTK_DISPLAY_INIT: real display failed to initialise")


if __name__ == "__main__":
    unittest.main(verbosity=2)
