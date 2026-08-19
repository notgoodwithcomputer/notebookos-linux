#!/usr/bin/env python3
"""Headless regression for failed Settings preference switches."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import settings  # noqa: E402


class Switch:
    """What _on_pref_switch is allowed to do to the switch that fired it: put
    its active flag back, with the handler blocked while it does (GTK has
    already flipped the flag by the time state-set runs, so refusing the
    state alone left the knob painted ON over an unsaved preference)."""

    def __init__(self):
        self.active = True          # GTK flips it BEFORE state-set is emitted
        self.log = []

    def set_active(self, value):
        assert self.log and self.log[-1] == "block", \
            "set_active outside a handler block would re-enter the handler"
        self.active = bool(value)
        self.log.append(("set_active", bool(value)))

    def handler_block_by_func(self, _func):
        self.log.append("block")

    def handler_unblock_by_func(self, _func):
        self.log.append("unblock")


def bare(initial, save_ok):
    app = settings.Settings.__new__(settings.Settings)
    app._settings = dict(initial)
    app._save_settings = lambda: save_ok
    return app


calls = []
app = bare({"large_text": False}, False)
sw = Switch()
handled = app._on_pref_switch(sw, True, "large_text", calls.append)
assert handled is True
assert app._settings == {"large_text": False}, app._settings
assert calls == [], calls
assert sw.active is False and sw.log[-1] == "unblock", sw.log
print("PASS failed switch save restores value and blocks its live callback")

app = bare({}, False)
sw = Switch()
app._on_pref_switch(sw, True, "reduced_motion", calls.append)
assert "reduced_motion" not in app._settings, app._settings
assert sw.active is False, sw.log
print("PASS failed new preference does not create an in-memory-only key")

app = bare({"large_text": False}, True)
sw = Switch()
handled = app._on_pref_switch(sw, True, "large_text", calls.append)
assert sw.active is True and sw.log == [], sw.log
assert handled is False
assert app._settings["large_text"] is True
assert calls == [True], calls
print("PASS durable switch save applies the live callback normally")
print("RESULT: PASS")
