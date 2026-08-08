# 039 — Calendar event customization

Completed 2026-08-07.

## Built

- Added backward-compatible `location`, multiline `notes`, and `all_day` event fields. All-day records omit `start`/`end`, sort ahead of timed events, render as a band above the Day clock, and use a full-width month chip. Location appears in Day blocks and chip details; notes appear in details/editor tooltips.
- Added a compact editor layout for the new fields, an all-day time-control toggle, optional inclusive series end date, and calendar reassignment. The calendar picker pairs the inherited calendar colour swatch with its name; there is no arbitrary per-event colour.
- Kept Escape/Cancel non-mutating: widget values are only copied into the model after an affirmative response. Academics-derived class events continue through the existing read-only path.
- Added honest repeating edit/delete scopes: **This Occurrence Only**, **This and Following**, and **Whole Series**. A one-off edit/cancellation persists a detached record/tombstone with its original `pattern_date`; `_extend_series` treats that date as occupied and cannot duplicate or resurrect it. Delete-following removes the tail and writes an inclusive cutoff to the remaining series.
- Added inclusive `series_end` enforcement to initial expansion and top-up. Existing fortnight recurrence is exposed as “Every 2 weeks”. Monthly recurrence is labeled “Monthly (same date)”. Its rule is anchor-based clamping: 31 January → 28/29 February → 31 March, rather than drifting permanently to the 28th.
- Preserved old records with sane defaults. New fields round-trip, all-day clock omission round-trips, IDs/content keys retain the old `(title, date, start)` compatibility behavior, writes continue through `nbapp.atomic_write_json`, and an unreadable/foreign existing event store now raises a write guard so automatic close/save cannot overwrite it.

## Deliberately out

Reminders and alerts were not added. Notebook OS has no notification-delivery substrate for a closed Calendar; an alert that only works while this window remains open would misrepresent its reliability.

## Verification

`python3 -m py_compile` passed for `calendar.py` and every `tools/calendar_*selftest*.py` file.

Display-free calendar suites, real final output tails:

```text
calendar_accessibility_selftest.py       RESULT: ALL PASS
calendar_customization_selftest.py       PASS calendar customization selftest
calendar_event_accessibility_selftest.py RESULT: ALL PASS
calendar_lifecycle_selftest.py           21/21 checks passed
calendar_month_keyboard_selftest.py      RESULT: ALL PASS
calendar_rollover_lifecycle_selftest.py  8 checks, 0 failed
calendar_selftest.py                     18/18 checks passed
```

The existing `calendar_mirror_selftest.py` requires a GTK display. This runner has neither a display nor `xvfb-run`; its real result was `RuntimeError: Gtk couldn't be initialized`, and attempting the normal virtual-display fallback returned `/bin/bash: xvfb-run: command not found`. No pass is claimed for that environment-blocked suite.

Static gates, real final tails:

```text
menu_conformance_check.py: 812 checks / RESULT: PASS
ascii_css_check.py: clean: no non-ASCII inside any bytes literal
css_parse_check.py: calendar.py 1 css block(s) / clean
```

`calendar_customization_selftest.py` pins field round-trip, old-record migration, all-day partition/render model path, calendar reassignment persistence, fortnight year crossing, anchor-based monthly 31st math, inclusive end date, detached edit survival/no regeneration, delete-following truncation, and truncation survival after extension.

## Red proof

Two mutations were applied separately, executed, captured, and immediately reverted:

1. Changed the end-date comparison from `occurrence > end_date` to `occurrence >= end_date`. The suite failed with `AssertionError: end date is inclusive`.
2. Cleared a detached edit's persisted `pattern_date`. The suite failed with `AssertionError: detached occurrence survives expansion`.

After restoration the final tail was:

```text
PASS end date is inclusive
PASS detached occurrence survives expansion
PASS detached pattern date is not regenerated
PASS delete following truncates
PASS truncation survives expansion
PASS calendar customization selftest
```

## Internationalization

Added 11 new English keys, translated in 17 flat JSON fragments under `release/1.0/i18n-fragments/039-calendar/`: `de el eo es fr hi it ja ko nl pl pt ru sr tr yi zh`. All 17 files passed `python3 -m json.tool`. No `de/lang_*.json` file was changed.

## Follow-up — pre-existing _dow_fit AttributeError

When full weekday names fit, `_dow_fit` never assigned `_dow_short` but then read `self._dow_short` directly. GTK swallowed the resulting size-allocation-handler `AttributeError` to stderr, so weekday-header fitting silently stopped on its first wide-panel run. The final read now follows the surrounding file idiom, `getattr(self, "_dow_short", False)`, while retaining the rule and comment that label mutation must be deferred outside `size-allocate`.

An additive headless check drives `_dow_fit` on a bare `Calendar` with a label whose layout is not ellipsized, replaces `GLib.idle_add` with a recorder, and verifies that the call returns without an `AttributeError` and does not schedule `_dow_shorten`.

Before the calendar fix, the new check provided this red proof:

```text
Traceback (most recent call last):
  File "tools/calendar_customization_selftest.py", line 49, in <module>
    c._dow_fit(None, None)
  File "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calendar.py", line 1400, in _dow_fit
    if self._dow_short:
AttributeError: 'Calendar' object has no attribute '_dow_short'
```

After the fix, `python3 -m py_compile buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calendar.py` passed. Final calendar-suite tails were:

```text
calendar_accessibility_selftest.py       RESULT: ALL PASS
calendar_customization_selftest.py       PASS fitting weekday headers do not schedule shortening
calendar_customization_selftest.py       PASS calendar customization selftest
calendar_event_accessibility_selftest.py RESULT: ALL PASS
calendar_lifecycle_selftest.py           21/21 checks passed
calendar_month_keyboard_selftest.py      RESULT: ALL PASS
calendar_rollover_lifecycle_selftest.py  8 checks, 0 failed
calendar_selftest.py                     18/18 checks passed
```

`calendar_mirror_selftest.py` reached its display-dependent construction and could not run headlessly in this environment; its tail was `RuntimeError: Gtk couldn't be initialized. Use Gtk.init_check() if you want to handle this case.` No pass is claimed for that display-blocked suite.
