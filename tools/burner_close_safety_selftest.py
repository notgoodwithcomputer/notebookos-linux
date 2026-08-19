#!/usr/bin/env python3
"""Headless regression for closing Disc Burner during a write."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import burner  # noqa: E402


class Jobs:
    def __init__(self):
        self.cancelled = []

    def cancel(self, key):
        self.cancelled.append(key)


def bare(busy, confirmed):
    app = burner.DiscBurner.__new__(burner.DiscBurner)
    app.busy = busy
    app._jobs = Jobs()
    app.status = type("Status", (), {"set_text": lambda self, text: None})()
    # A control that can be disabled must also be able to carry the reason
    # it is disabled for; a fake without set_tooltip_text cannot stand in.
    control = type("Control", (), {
        "tooltip": None,
        "set_sensitive": lambda self, value: setattr(self, "sensitive", value),
        "set_tooltip_text": lambda self, text: setattr(self, "tooltip", text),
        "get_tooltip_text": lambda self: self.tooltip})
    app.stop_btn = control()
    app.add_btn = control()
    app.rescan_btn = control()
    app._clean_workdir = lambda: None
    app._confirm_stop_burn = lambda: confirmed
    app.destroyed = False
    app.destroy = lambda: setattr(app, "destroyed", True)
    return app


app = bare(True, False)
app.close()
assert app._jobs.cancelled == [] and app.destroyed is False
print("PASS cancelling close leaves an active burn and window intact")

app = bare(True, True)
app.close()
assert app._jobs.cancelled == ["burn"] and app.destroyed is False
burner.DiscBurner._finished(app, "cancelled", "")
assert app.destroyed is True
print("PASS confirmed close cancels and reaps the burn before destroying its window")

app = bare(False, False)
app.close()
assert app._jobs.cancelled == [] and app.destroyed is True
print("PASS idle close remains immediate")

app = bare(True, False)
assert app._on_delete() is True and app._jobs.cancelled == []
print("PASS window-manager close is vetoed when stop warning is declined")

app = bare(True, True)
assert app._on_delete() is True and app._jobs.cancelled == ["burn"]
assert app.destroyed is False
print("PASS confirmed window-manager close waits for burn cancellation")
print("RESULT: PASS")
