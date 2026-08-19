#!/usr/bin/env python3
"""Headless ownership checks for Illustrator's deferred UI callbacks."""
import os
import sys
import json
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import illustrator  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Adjustment:
    def __init__(self, value=40, page=20, upper=500):
        self.value, self.page, self.upper = value, page, upper
        self.sets = []

    def get_value(self): return self.value
    def get_page_size(self): return self.page
    def get_upper(self): return self.upper
    def set_value(self, value): self.sets.append(value); self.value = value


class Mat:
    def __init__(self, ha, va): self.ha, self.va = ha, va
    def get_hadjustment(self): return self.ha
    def get_vadjustment(self): return self.va


class Canvas:
    def __init__(self): self.sizes, self.draws = [], 0
    def set_size_request(self, w, h): self.sizes.append((w, h))
    def queue_draw(self): self.draws += 1


app = illustrator.Illustrator.__new__(illustrator.Illustrator)
app._closed = False
app._recentre_src = app._fit_src = app._chip_restore_src = 0
app.zoom, app.cw, app.ch = 2, 16, 12
ha, va = Adjustment(), Adjustment(60)
app.mat, app.canvas = Mat(ha, va), Canvas()
app._sync_controls = lambda: None
app._refresh_status = lambda: None
queued, removed = [], []
real_idle = illustrator.GLib.idle_add
real_remove = illustrator.GLib.source_remove
illustrator.GLib.idle_add = lambda callback: queued.append(callback) or (100 + len(queued))
illustrator.GLib.source_remove = lambda sid: removed.append(sid)
try:
    app._set_zoom(3)
    app._set_zoom(4)
finally:
    illustrator.GLib.idle_add = real_idle
    illustrator.GLib.source_remove = real_remove
check(removed == [101] and app._recentre_src == 102,
      "rapid zoom replaces the stale recenter source")
check(queued[-1]() is False and app._recentre_src == 0,
      "the newest live recenter clears ownership")
check(len(ha.sets) == 1 and len(va.sets) == 1,
      "the newest live recenter updates each adjustment once")
before = (list(ha.sets), list(va.sets))
app._closed, app._recentre_src = True, 103
check(queued[0]() is False and app._recentre_src == 0
      and (ha.sets, va.sets) == before,
      "a closed dispatched recenter is inert")

app._closed, app._fitted, app._fit_src = False, False, 0
fit_calls, queued = [], []
app._zoom_fit = lambda: fit_calls.append("fit")
illustrator.GLib.idle_add = lambda callback: queued.append(callback) or 201
try:
    app._on_mat_allocate()
    app._on_mat_allocate()
finally:
    illustrator.GLib.idle_add = real_idle
check(app._fit_src == 201 and len(queued) == 1,
      "initial allocation owns exactly one fit idle")
check(queued[0]() is False and app._fit_src == 0 and fit_calls == ["fit"],
      "a live fit idle clears ownership and fits once")
app._closed, app._fit_src = True, 202
check(queued[0]() is False and app._fit_src == 0 and fit_calls == ["fit"],
      "a closed fit idle is inert")


class Label:
    def __init__(self): self.markups = []
    def set_markup(self, markup): self.markups.append(markup)


app._closed = False
app.save_lbl = Label()
app._flash_token = 0
app._chip_restore_src = 301
rendered, removed, timers = [], [], []
app._render_chip = lambda: rendered.append("render")
real_timeout = illustrator.GLib.timeout_add
illustrator.GLib.source_remove = lambda sid: removed.append(sid)
illustrator.GLib.timeout_add = lambda delay, callback, token: timers.append(
    (delay, callback, token)) or 302
try:
    app._flash_save("Saved elsewhere")
finally:
    illustrator.GLib.source_remove = real_remove
    illustrator.GLib.timeout_add = real_timeout
check(removed == [301] and app._chip_restore_src == 302,
      "a new chip flash replaces and owns its restore timer")
token = timers[0][2]
check(app._restore_chip(token) is False and app._chip_restore_src == 0
      and rendered == ["render"],
      "a current live chip restore clears ownership and renders once")
app._closed, app._chip_restore_src = True, 303
check(app._restore_chip(token) is False and app._chip_restore_src == 0
      and rendered == ["render"], "a closed chip restore is inert")

app._closed = False
app._chip_restore_src = 304
app._dirty = False
app._chip_state = "empty"
illustrator.GLib.source_remove = lambda sid: removed.append(sid)
try:
    app._mark_unsaved()
finally:
    illustrator.GLib.source_remove = real_remove
check(app._chip_restore_src == 0 and removed[-1] == 304,
      "a durable chip state cancels a transient restore")

app._closed = False
app._recentre_src, app._fit_src, app._chip_restore_src = 401, 402, 403
events = []
illustrator.GLib.source_remove = lambda sid: events.append((sid, app._closed))
try:
    first, second = app._on_destroy(), app._on_destroy()
finally:
    illustrator.GLib.source_remove = real_remove
check(first is False and second is False and app._closed,
      "destroy is idempotent and raises the closed gate")
check(events == [(401, True), (402, True), (403, True)],
      "destroy cancels every owned source behind the closed gate")
check((app._recentre_src, app._fit_src, app._chip_restore_src) == (0, 0, 0),
      "destroy clears all deferred ownership")

# Remembering a colour updates one preference field without erasing settings
# owned by a newer version or an extension.
prefs_dir = tempfile.mkdtemp(prefix="illustrator-prefs-")
prefs_path = os.path.join(prefs_dir, "illustrator.json")
with open(prefs_path, "w", encoding="utf-8") as fh:
    json.dump({"recent": ["#111111"], "palette_name": "custom"}, fh)
real_cfg = illustrator.CFG_FILE
illustrator.CFG_FILE = prefs_path
app._recent = ["#111111"]
app._sync_recent = lambda: None
try:
    app._remember("#ABCDEF")
    with open(prefs_path, encoding="utf-8") as fh:
        saved_prefs = json.load(fh)
finally:
    illustrator.CFG_FILE = real_cfg
check(saved_prefs.get("recent", [])[:2] == ["#ABCDEF", "#111111"],
      "remembering a colour persists the updated recent list")
check(saved_prefs.get("palette_name") == "custom",
      "remembering a colour preserves unrelated preference fields")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
