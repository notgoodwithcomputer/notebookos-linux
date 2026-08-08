# Task 045 — `popup_at` adoption

## Call sites

- `sysmon.py`'s `_on_tree_button` keeps its `smmenu` styling and `show_all()`
  behavior, but now calls `nbapp.popup_at(menu, event=event)`. The direct
  `popup_at_pointer` call and deprecated `popup` fallback were removed because
  the shared helper owns pointer placement and the post-map workarea clamp.
- `music.py`'s `_on_add_clicked` keeps its outer exception guard, menu
  construction, and `show_all()` behavior, but now calls
  `nbapp.popup_at(menu, widget=button, anchor="widget-sw")`. The direct
  `popup_at_widget` call and deprecated `popup` fallback were removed because
  the shared helper owns that anchor and clamp.

Two additive, display-free checks pin the exact helper routing in
`tools/sysmon_popup_at_selftest.py` and `tools/music_popup_at_selftest.py`.

## Verification tails

### Compilation

```text
py_compile: PASS (sysmon.py, music.py)
```

### Full discovered selftest families

The discovered suites were:

```text
tools/music_failure_selftest.py
tools/music_lifecycle_selftest.py
tools/music_playlist_selftest.py
tools/music_popup_at_selftest.py
tools/music_tags_selftest.py
tools/music_transport_accessibility_selftest.py
tools/sysmon_popup_at_selftest.py
tools/sysmon_selftest.py
```

Display-free suite tails:

```text
tools/sysmon_popup_at_selftest.py
PASS sysmon context menu routes through nbapp.popup_at
RESULT: ALL PASS

tools/sysmon_selftest.py
PASS a finished program leaves the table
PASS a new program enters the table
PASS an empty tick empties the table
ALL PASS

tools/music_lifecycle_selftest.py
PASS destroy drops the loaded track and the poll source
PASS _closed is assigned before every teardown statement in the source
18/18 checks passed

tools/music_popup_at_selftest.py
PASS music row menu routes through nbapp.popup_at
RESULT: ALL PASS

tools/music_transport_accessibility_selftest.py
PASS transport controls retain action tooltips
PASS Papertone buttons suppress themed gradients and shadows
RESULT: ALL PASS
```

The three existing display-dependent Music suites were run but could not run
their GUI sections in this sandbox: `music_failure_selftest.py` and
`music_tags_selftest.py` stopped at `RuntimeError: Gtk couldn't be initialized`,
and `music_playlist_selftest.py` first exposed its documented `PYTHONPATH`
assumption; with that path supplied it likewise requires a GTK display. This
host has no display and no `xvfb-run`, so these are recorded as environment
skips rather than hidden. No display-free suite failed.

### Static and language gates

```text
tools/menu_conformance_check.py
812 checks
RESULT: PASS

tools/self_attr_audit.py
calculator:355  class Calculator is no longer checkable -- setattr(self, ...) may store a callable
114 classes checked (0 for calls only), 1 skipped, 0 finding(s)

tools/voice_check.py
9 flagged string(s) across 66 file(s)
   prose-in-ui              5
   second-person            3
   coaxing-prompt           1
RESULT: CLEAN

tools/jargon_sweep.py
=== writer.py ===
  writer.py:31  [graphics/X: GTK] (allow)
      'Gtk'
118 flagged strings
RESULT: CLEAN
```

The menu conformance label-subset rule still resolves through the new helper
call shape; it reported no new finding.

## Red proof

With the sysmon call site temporarily changed back to
`menu.popup_at_pointer(event)`, the new pin exited 1:

```text
FAIL sysmon context menu routes through nbapp.popup_at
```

After restoring `nbapp.popup_at(menu, event=event)`, the same check passed:

```text
PASS sysmon context menu routes through nbapp.popup_at
RESULT: ALL PASS
```

## Collision note

Adoption of `nbapp.popup_at` in `finder.py` and `widgets.py` remains deferred
to the owner of tasks 043/044, avoiding collisions with agents currently
working in those files.

## Widgets adoption

The board context-menu handler now routes `menu` and its click as
`nbapp.popup_at(menu, event=ev)`; the helper owns the legacy
`popup_at_pointer` call, so no local fallback remains.  The new display-free
`tools/widgets_popup_at_selftest.py` pins that exact call shape and rejects
legacy popup calls.

Verification tails:

```text
python3 -m py_compile .../de/widgets.py
(exit 0)

tools/widgets_accessibility_selftest.py
11 checks passed
RESULT: ALL PASS

tools/widgets_classes_schedule_selftest.py
widgets classes schedule selftest: ok

tools/widgets_popup_at_selftest.py
PASS widgets board context menu routes through nbapp.popup_at
RESULT: ALL PASS

tools/widgets_smoothness_selftest.py
11 checks passed
OK

tools/menu_conformance_check.py
812 checks
RESULT: PASS
```

The full headless family had no display-free failure.  With the DE directory
added to `PYTHONPATH`, `widgets_tasks_selftest.py` reached widget construction
and skipped because `Gtk couldn't be initialized`; the separate
`widgetsettings_selftest.py` stopped at the same display requirement.  The
orchestrator can exercise those GUI sections with a display.

For the red proof, the call site was temporarily restored to
`menu.popup_at_pointer(ev)`.  The new pin exited 1 with:

```text
FAIL widgets board context menu routes through nbapp.popup_at
```

Restoring the shared-helper call returned the green result shown above.

## Widgets adoption

`Board._on_board_press` in `de/widgets.py` now routes its right-click board
menu through the shared helper: `menu.popup_at_pointer(ev)` became
`nbapp.popup_at(menu, event=ev)`, with the adjacent comment updated to name
the helper. Nothing else in `widgets.py` was touched.

`tools/widgets_popup_at_selftest.py` mirrors the sysmon pin: a display-free
AST check that `_on_board_press` contains exactly one `nbapp.popup_at` call
whose first argument is `menu` and whose sole keyword is `event=ev`, and that
no legacy `popup`/`popup_at_pointer` call remains. Green run:

```text
PASS widgets board context menu routes through nbapp.popup_at
RESULT: ALL PASS
```

Red proof: with the call site temporarily reverted to
`menu.popup_at_pointer(ev)`, the pin printed the exact FAIL line and exited 1;
restoring `nbapp.popup_at(menu, event=ev)` returned it to ALL PASS:

```text
FAIL widgets board context menu routes through nbapp.popup_at
```

Verification actually run: `py_compile` clean on both `widgets.py` and the new
selftest. Widgets suite family all green — `widgets_accessibility_selftest`
(RESULT: ALL PASS), `widgets_classes_schedule_selftest` ("ok"),
`widgets_smoothness_selftest` ("OK"), `widgets_tasks_selftest` (RESULT: ALL
PASS, run with the DE dir on `PYTHONPATH`; it imports `widgets` directly), and
`widgetsettings_selftest` ("OK"). No skips were reported by any of them.
`menu_conformance_check` passed: 812 checks, RESULT: PASS.
