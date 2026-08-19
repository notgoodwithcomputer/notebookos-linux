#!/usr/bin/env python3
"""Headless ownership checks for Widgets' GLib sources."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import widgets  # noqa: E402


class Probe:
    _own_timeout_once = widgets.Widgets._own_timeout_once
    _on_destroy = widgets.Widgets._on_destroy


probe = Probe()
probe._destroyed = False
probe._owned_sources = []
probe._cancel_reload = lambda: None
probe._app_flag_monitor = None
probe._store_monitors = []
callbacks = {}
removed = []
real_add = widgets.GLib.timeout_add
real_remove = widgets.GLib.source_remove
widgets.GLib.source_remove = removed.append
try:
    def allocate(_delay, callback):
        callbacks[41] = callback
        return 41

    widgets.GLib.timeout_add = allocate
    fired = []
    probe._own_timeout_once(500, lambda: fired.append(True))
    assert probe._owned_sources == [41]
    assert callbacks[41]() is False and fired == [True]
    assert probe._owned_sources == [], "completed one-shot retained stale ID"

    # ID 41 may now belong to somebody else. Only the still-live owned source
    # 42 may be removed when Widgets is destroyed.
    probe._owned_sources = [42]
    probe._on_destroy()
    assert removed == [42], "destroy removed a reused unowned source ID"
finally:
    widgets.GLib.timeout_add = real_add
    widgets.GLib.source_remove = real_remove

print("WIDGETS LIFECYCLE SELFTEST: 4 checks, all pass")
