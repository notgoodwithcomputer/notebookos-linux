# Task 037 — Calculator TI-class expansion

Completed 2026-08-07.

## Built

1. Home now has a persistent scrolling expression/result tape, persistent `Ans`, operator-after-result continuation (`Ans+5`), and reversible Up/Down expression recall.
2. `STO→` stores the displayed result in A-Z, variables are accepted by the safe evaluator, a non-empty variable listing is available, and variables persist atomically with calculator state. `Ctrl+S` reaches store.
3. `MATH`/`Ctrl+M` opens a keyboard-navigable categorized catalog. The evaluator covers direct, inverse, and hyperbolic trig; ln/log/log2; exponential functions; roots; numeric decomposition/rounding; factorial/combinations/permutations; and random. The 2nd layer remains wired to inverse trig. Domain, zero-division, overflow, and syntax failures retain the existing honest `_fail` messages.
4. Home, Graph, and Table are distinct paper-language stack views, reachable with `Ctrl+1/2/3`. Graph supplies Y1-Y4 enable/edit rows, Window values, four zoom actions, Cairo axes/ticks/grid/curves, pole-safe segmented sampling, and arrow-key trace. Table supplies start/step controls and scrollable values for enabled functions.
5. DEG/RAD is shared by home evaluation, graph, trace, and table. Float/Fix-0 through Fix-9 is shared by result, trace, and table formatting. Esc returns Graph/Table to Home and does not erase data.

No numbered scope item was cut. The layout uses a compact top segmented view control rather than a wide rail so the existing six-column keypad and graph retain usable width at 1024x722.

State writes use `nbapp.atomic_write_json`. A malformed existing state file marks the store unreadable and is not overwritten.

## Verification

All edited Python files compiled:

```text
python3 -m py_compile ...
exit 0
```

Calculator selftest family, run through `tools/guestrun.sh`:

```text
calculator_accessibility_selftest.py:
PASS focus is visibly indicated
RESULT: ALL PASS

calculator_keyboard_selftest.py:
SKIP GTK interaction checks: no display connection
RESULT: ALL PASS

calculator_selftest.py:
SKIP GTK display/history checks: no display connection
all headless checks passed

calculator_ti_selftest.py:
PASS catalog insertion random()
RESULT: 70 checks, ALL PASS
```

The current sandbox exposes no usable GTK display connection, so the two existing suites' widget-interaction sections now skip explicitly after their headless checks instead of aborting in `Gtk.Window`. The source/static accessibility coverage remains active, and the new suite is entirely headless.

Required static gates:

```text
tools/menu_conformance_check.py
812 checks
RESULT: PASS

tools/css_parse_check.py
calculator.py          1 css block(s)
clean

tools/ascii_css_check.py
clean: no non-ASCII inside any bytes literal
```

All 17 fragment files parse as JSON:

```text
RESULT: 17 valid JSON fragments
```

## Red-proof

Temporarily forced DEG evaluation to use radians and disabled the pole split, then ran `calculator_ti_selftest.py`. Captured failures included:

```text
FAIL catalog sin(30)  -0.988031624093
FAIL catalog atan(1)  0.785398163397
FAIL tan asymptote breaks polyline  1
RESULT: 70 checks, 7 FAILED
```

Both sabotages were reverted. The final rerun ended `RESULT: 70 checks, ALL PASS`.

## Localization

Added 35 new-string entries per language under `release/1.0/i18n-fragments/037-calculator/` for all requested languages: de, el, eo, es, fr, hi, it, ja, ko, nl, pl, pt, ru, sr, tr, yi, and zh. `%d` is preserved in `Fix %d`. No `de/lang_*.json` file was changed.

## Follow-up (same day)

Two display-gate defects were repaired.

1. The tape renderer computed a row count independently from its offset and
   then indexed `self.tape[offset + i]`. Once the offset moved into a short
   tail, `offset + count` exceeded the tape length and the third keyboard
   evaluation raised `IndexError`. The real `_refresh()` path now calls the
   pure `tape_window()` helper. It first pair-aligns expression/result rows,
   clamps the requested count, clamps the offset to the last valid offset, and
   ends its slice at `len(rows)`. Empty, one-row, exact-window, window+1,
   recalled, failed-result, and cleared states are covered headlessly.
2. `_table_setting()` used `setattr(self, attr, value)`, where `attr` came from
   a computed callback argument. Although its two values were numeric table
   settings, the static attribute auditor must conservatively treat a computed
   `setattr` as possibly installing a callable. It is now two explicit plain
   assignments to `self.tbl_start` and `self.tbl_step`; behavior is unchanged.

The rest of the task-037 indexed/windowed paths were audited too. Two adjacent
loaded-state hazards were found and fixed: legacy/malformed tape and result
lists can differ in length, so sanitization now aligns them from the newest end
and `tape_rows()` pairs only through the shorter list; loaded `ys` and
`y_enabled` arrays can differ or be short, so both are normalized to exactly
four entries before graph, table, and trace indexing. Oversized tape state is
trimmed to the same 30-entry retention window. Recall already clamps at the
oldest row and restores the draft past the newest. Trace switching already
indexes only a nonempty enabled-function list, with a missing current curve
reset to its first member. Table rows enumerate the normalized function list.
Catalog insertion already requires a selected tree iterator and a nonempty
value. No further unclamped-window defect was present in those paths.

The required red proof was run by temporarily restoring the defective
independent `offset + count` indexing inside the new real helper, then running
the new headless suite. Raw failure tail, before restoring the fix:

```text
$ python3 tools/calculator_tape_selftest.py
PASS tape window empty  []
PASS tape window one entry  [('1+1', '2')]
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/calculator_tape_selftest.py", line 89, in <module>
    rows, err = (C.tape_window(calc.tape, calc.tape_results, count=WINDOW), None)
  File "/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calculator.py", line 133, in tape_window
    return [rows[offset + i] for i in range(count)]
            ~~~~^^^^^^^^^^^^
IndexError: list index out of range
```

The identical suite after restoring the bounded slice ended green:

```text
PASS recall past newest restores draft  9
PASS tape window after clear  []
PASS state ys padded to four  (['X', '', '', ''], [True, False, False, False])
PASS state window rejects bad bounds  {'xmin': -10.0, 'xmax': 10.0, 'ymin': -10.0, 'ymax': 10.0, 'xscl': 1.0, 'yscl': 1.0}
PASS state fix rejects junk  (None, None, 3)
PASS state table fields reject junk  (0.0, 1.0, 0.0)
PASS state old-format tape pairs from the end  (['a', 'b'], [None, '4'])
PASS state oversized results clamp to tape  [('a', '2')]
RESULT: 20 checks, ALL PASS
```

Compilation and the attribute gate:

```text
$ python3 -m py_compile buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calculator.py tools/calculator_*selftest*.py && echo 'PY_COMPILE PASS'
PY_COMPILE PASS

$ python3 tools/self_attr_audit.py
115 classes checked (0 for calls only), 0 skipped, 0 finding(s)
CLEAN: no undefined self attributes, every class checked
```

The complete headless calculator suite family produced these real tails (the
display-only sections skipped as expected in this sandbox):

```text
$ for suite in tools/calculator_*selftest*.py; do python3 "$suite"; done
calculator_accessibility_selftest.py:
PASS focus is visibly indicated
RESULT: ALL PASS
EXIT 0
calculator_keyboard_selftest.py:
SKIP GTK interaction checks: no display connection
RESULT: ALL PASS
EXIT 0
calculator_selftest.py:
SKIP GTK display/history checks: no display connection
all headless checks passed
EXIT 0
calculator_tape_selftest.py:
PASS state oversized results clamp to tape  [('a', '2')]
RESULT: 20 checks, ALL PASS
EXIT 0
calculator_ti_selftest.py:
PASS catalog insertion random()
RESULT: 70 checks, ALL PASS
EXIT 0
```

The remaining required gates:

```text
$ python3 tools/menu_conformance_check.py
812 checks
RESULT: PASS

$ python3 tools/voice_check.py
9 flagged string(s) across 66 file(s)
   prose-in-ui              5
   second-person            3
   coaxing-prompt           1
RESULT: CLEAN
```

No user-facing string was added or changed in this follow-up, so
`tools/jargon_sweep.py` and new localization fragments were not required.

The keyboard contract now claims letters and visibly inserts uppercase A-Z,
because STO variables made them vocabulary; Esc, Tab, and unowned keys retain
the original fall-through intent. F5 now pins genuine unowned-key fall-through.
The revised consumption/insertion assertion is the static red-proof pending the
orchestrator's live display run; `calculator.py` stayed byte-identical at SHA-256 `a270ecea08958f8ac012af244fe24e8d492e82b75f5fdd9700ee48524ec43f90`.
