# Task 044 — Classes tile day schedule

## Result and visual grammar

The Classes tile now renders today's meetings as a miniature day schedule while preserving the existing `Classes` card header and the existing click destination (`Academics`, `schedule`). Missing, malformed, or empty Academics data still produces `No classes / Add classes in Academics`.

Calendar/Academics grammar mapped into the tile:

- Mirrored: a narrow left time gutter, whole-hour hairlines, duration-proportional event rectangles, a quiet tinted fill with a solid colour spine, labels anchored at the block top, room on a second line when height permits, Pango ellipsis, side-by-side collision lanes, and a red now rule with a dot at the gutter edge.
- Simplified for tile scale: there is no scrolling, interaction, date heading, all-day band, or per-class colour picker. The board's papertone/ink/grey hairline palette is used, with the existing muted green as the event spine and the board's signage red only for now.
- Window decision: the window is data-derived from today's classes, rounded to whole hours, with one hour before the earliest start and one hour after the latest end, clamped to midnight. This shows useful context without compressing a fixed 08:00–20:00 Calendar day into a short tile. An absent/invalid end receives Academics' own one-hour fallback.
- Collision decision: each connected overlap run is greedily assigned the fewest side-by-side lanes. Once a run ends, a later class regains full width. This preserves every meeting and is more legible than stacking blocks over one another.
- Now decision: the line exists only when today has at least one valid class and current local time lies within the displayed window.

## Verification

Python compile:

```text
$ python3 -m py_compile buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/widgets.py tools/widgets_classes_schedule_selftest.py
(no output; exit 0)
```

Selftest discovery:

```text
$ ls tools/widgets*selftest*.py
tools/widgets_accessibility_selftest.py
tools/widgets_classes_schedule_selftest.py
tools/widgets_smoothness_selftest.py
tools/widgets_tasks_selftest.py
tools/widgetsettings_selftest.py
```

Full family command (with the overlay DE on `PYTHONPATH`) ran in lexical order. Accessibility, Classes schedule, and smoothness passed; the two legacy window-instantiating tests cannot initialize the configured `:0` display in this execution container. Real output tail at the first environmental stop:

```text
$ DE_PATH=buildroot/board/notebookos/rootfs-overlay/opt/notebook/de; for test_file in tools/widgets*selftest*.py; do PYTHONPATH="$DE_PATH${PYTHONPATH:+:$PYTHONPATH}" python3 "$test_file" || exit; done
PASS task rows have a visible focus treatment
RESULT: ALL PASS
widgets classes schedule selftest: ok
PASS cancelling twice is harmless
PASS store monitors queue a rebuild, never run one
PASS the monitor wiring was found at all
OK
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/widgets_tasks_selftest.py", line 49, in <module>
    col = widgets.Widgets()
  File "/usr/lib/python3/dist-packages/gi/overrides/Gtk.py", line 505, in __init__
    raise RuntimeError(
RuntimeError: Gtk couldn't be initialized. Use Gtk.init_check() if you want to handle this case.
```

`widgetsettings_selftest.py` was also invoked separately so it was not hidden behind the loop's stop:

```text
$ PYTHONPATH="$DE_PATH${PYTHONPATH:+:$PYTHONPATH}" python3 tools/widgetsettings_selftest.py
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/widgetsettings_selftest.py", line 77, in main
    w = widgetsettings.WidgetSettings()
  File "/usr/lib/python3/dist-packages/gi/overrides/Gtk.py", line 505, in __init__
    raise RuntimeError(
RuntimeError: Gtk couldn't be initialized. Use Gtk.init_check() if you want to handle this case.
```

The new headless contract gate after both mutations were reverted:

```text
$ python3 tools/widgets_classes_schedule_selftest.py
widgets classes schedule selftest: ok
```

Text and copy gates:

```text
$ python3 tools/toyfont_check.py
pending (parallel sweep)  nbprint.py       1 call
pending (parallel sweep)  sequencer.py     4 calls
pending (parallel sweep)  settings.py      8 calls
pending (parallel sweep)  writer.py        11 calls

clean: 62 files draw text only through Pango, 4 pending, 0 BROKEN

$ python3 tools/voice_check.py
9 flagged string(s) across 66 file(s)
   prose-in-ui              5
   second-person            3
   coaxing-prompt           1
RESULT: CLEAN

$ python3 tools/jargon_sweep.py
=== widgets.py ===
  widgets.py:59  [graphics/X: GTK] (allow)
      'Gtk'
  widgets.py:2852  [graphics/X: widget] (pending)
      'Widget Settings…'
118 flagged strings
RESULT: CLEAN
```

CSS was not changed, so the CSS-only gates were not applicable.

## Red proofs

1. Duration proportion was inverted by changing the block bottom calculation from `pixels / time-span` to `time-span / pixels`. The focused test failed as intended:

```text
$ python3 tools/widgets_classes_schedule_selftest.py
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/widgets_classes_schedule_selftest.py", line 30, in <module>
    check((top, height) == (40.0, 60.0),
  File "/home/ben/Documents/notebookos-linux/tools/widgets_classes_schedule_selftest.py", line 26, in check
    raise AssertionError(message)
AssertionError: 09:00-10:30 must occupy 40..100 in a 320px 08:00-16:00 axis
```

2. The tolerant store exception handler was mutated from `return {}` to `raise`. A genuinely damaged temporary `academics.json` then failed as intended:

```text
$ python3 tools/widgets_classes_schedule_selftest.py
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/widgets_classes_schedule_selftest.py", line 65, in <module>
    check(store_space["_read_store"](str(bad)) == {},
  File "/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/widgets.py", line 1874, in _read_store
    data = json.load(fh)
json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
```

Both mutations were reverted. The focused suite and `py_compile` then passed green.

## Strings and i18n

New strings: none. Existing translated keys (`Classes`, `Today`, `No classes`, `Add classes in Academics`, and `Open Academics`) are reused. i18n fragment path: none.
