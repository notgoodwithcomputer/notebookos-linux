#!/usr/bin/env python3
"""Headless close boundary for an in-flight physical disc writer."""
import os
import sys
import tempfile

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="burner-close-"))

import burner  # noqa: E402


class Widget:
    """A stand-in for a Gtk.Button.

    It records the tooltip as well as the sensitivity: a control this app
    disables must say why, so a fake that cannot hold a reason cannot check
    the contract the real one is held to.
    """
    def __init__(self):
        self.sensitive = True
        self.tooltip = None
    def set_sensitive(self, value):
        self.sensitive = bool(value)
    def set_tooltip_text(self, text):
        self.tooltip = text
    def get_tooltip_text(self):
        return self.tooltip


class Jobs:
    def __init__(self):
        self.cancelled = []
    def cancel(self, name):
        self.cancelled.append(name)


class Probe:
    close = burner.DiscBurner.close
    _finished = burner.DiscBurner._finished

    def __init__(self):
        self.busy = True
        self._closing_after_stop = False
        self._jobs = Jobs()
        self.stop_btn = Widget()
        self.add_btn = Widget()
        self.rescan_btn = Widget()
        self.messages = []
        self.destroyed = 0
        self.cleaned = 0
    def _confirm_stop_burn(self):
        return True
    def _say(self, message):
        self.messages.append(message)
    def _clean_workdir(self):
        self.cleaned += 1
    def destroy(self):
        self.destroyed += 1


p = Probe()
p.close()
checks = [
    (p._jobs.cancelled == ["burn"], "confirmed close requests cancellation"),
    (p.destroyed == 0, "window stays alive until the worker is terminal"),
    (p._closing_after_stop, "close records its waiting state"),
    (not p.stop_btn.sensitive and not p.add_btn.sensitive,
     "burn controls are disabled while stopping"),
    (all(b.tooltip for b in (p.stop_btn, p.add_btn, p.rescan_btn)),
     "each control disabled while stopping says why"),
]
p._finished("stopped", "")
checks += [
    (p.cleaned == 1, "workdir cleanup waits for terminal delivery"),
    (p.destroyed == 1, "terminal delivery closes the window exactly once"),
]
failed = 0
for passed, label in checks:
    print(("PASS " if passed else "FAIL ") + label)
    failed += not passed
print("RESULT: %s" % ("PASS" if not failed else "FAILED"))
raise SystemExit(bool(failed))
