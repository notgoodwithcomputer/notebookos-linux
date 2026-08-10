# 031 — C2 durability matrix

Date: 2026-08-07. The effective `de/*.py` directory in this checkout is
`buildroot/board/notebookos/rootfs-overlay/opt/notebook/de`. The gate scans it
on every run; `project.json` is deliberately excluded because it is a picked
GBA/Sequencer/Video document/bundle member, not a file under
`$NB_HOME/.config/notebook`. Legacy input aliases `academic.json` and
`gbaide.json` are likewise not current writable stores.

## Evidence key

- **A — PASS, persistence layer:** `crash_mid_write()` at
  `tools/durability_matrix_selftest.py:79`; it produces an old, half-JSON,
  hour-old `.nbw-*.tmp`, then takes the shared close/save path and proves the
  store survived and `_reap_stale_tmp` removed the artifact. This complements
  the app-layer malformed launch/construct/destroy coverage in
  `tools/config_resilience_selftest.py`.
- **B — UNCOVERED (partial pass), persistence open+close simulation:**
  `corrupt_store()` at line 97 produces truncated JSON, non-UTF-8 garbage,
  zero bytes, and valid wrong-shape JSON. For the three nonempty cases it
  performs load fallback followed by close/save and proves exact original
  bytes exist in `.damaged-*` or `.bak`. This is exactly the historical
  “damaged store + open + close destroys it” regression. Zero bytes are not
  preserved by current `nbapp.preserve_damaged`; this real defect is filed,
  not hidden. App construction coverage is also referenced to
  `tools/config_resilience_selftest.py`; shared quarantine mechanics are
  referenced to `tools/document_safety_selftest.py:malformed_store_is_quarantined`.
- **C — PASS, persistence layer:** `disk_full()` at line 126 injects
  `OSError(ENOSPC)` into the real store write's `os.fsync`, checks
  `save_failure_reason()` says the disk is full, and checks the prior JSON is
  byte-logically intact. Reference: `tools/document_safety_selftest.py` also
  proves an atomic failure preserves an old document.
- **D — PASS, process/persistence layer:** `second_instance()` at line 145
  starts two independent Python writers, each repeatedly committing a large,
  distinct payload. The result must parse and equal one complete payload.
- **E — PASS, process/persistence layer:** `kill_nine()` at line 158 starts a
  real child writer and sends `SIGKILL` at seeded randomized points in 55
  iterations. It first observes an active `.nbw-*` artifact (preventing a
  vacuous kill-during-startup pass), then validates every post-kill file as an
  old or new complete payload. Stores rotate through all rows.
- **F — PASS, persistence call-sequence layer:** `power_cut()` at line 192
  wraps the real `os.fsync` and `os.replace` calls and requires
  `fsync(file) < rename < fsync(directory)`. This is the honest power-cut
  guarantee; a userspace suite cannot cut physical power.
- **G — UNCOVERED with reason:** these stores have a fixed location under
  `NB_HOME`; none can live on removable media. There is therefore no honest
  removable-yank event to produce for them. USB write-through belongs to the
  Finder/USB-writer suites and is not duplicated here.

`tools/data_stress_sweep.py` concerns stored-field UI dimensions, not a damage
mechanism, so no durability cell is credited to it.

## Full store × damage matrix

Every cell names its evidence key above; keys are deliberately repeated so a
new row cannot acquire a silent gap.

| Store | crash mid-write | corrupt store | disk full | second instance | kill -9 | power cut | removable yanked |
|---|---|---|---|---|---|---|---|
| academics.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| accounting.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| bills.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| calculator.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| calendar.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| calendars.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| contacts.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| cookbook.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| ebook.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| finder.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| g2048.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| gbasdk.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| illustrator.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| journal.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| language.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| locale.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| maps.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| mealplanner.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| music.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| novel.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| removed_apps.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| screenplay.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| sequencer.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| settings.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| shell.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| tasks-app.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| tasks.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| terminal.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| video.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| widgets.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| workout.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |
| writer.json | A PASS | B UNCOVERED/3 PASS | C PASS | D PASS | E PASS | F PASS | G UNCOVERED |

Totals: 32 stores, 224 cells; 160 cells proven here, 0 reference-only cells,
and 64 explicitly uncovered cells. Existing-suite references strengthen A, B,
and C but those cells are classified by the stronger result shown here.

## M1 RED-PROOF TRANSCRIPTS

Both sabotages used a copied `nbapp.py` under `/tmp`; the real source was never
mutated. `DURABILITY_DE` is a test-only import shim. The scratch kill writer
opened the destination with `"w"`, emitted 32-byte chunks, and slept between
chunks. The corrupt scratch copy redefined `preserve_damaged()` to return
`None`.

### Atomic-write sabotage is caught by kill -9

```text
$ REAL_DE=$PWD/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de
$ PYTHONPATH="$REAL_DE" DURABILITY_DE=/tmp/031-red-kill python3 tools/durability_matrix_selftest.py --only kill-9
...
FAILURES
- kill-9: Unterminated string starting at: line 1 column 37 (char 36)

1 checks
32 stores; 224 cells; explicit gaps: removable media, empty-file recovery
$ echo $?
1
```

The assertion failure is the required torn-write proof. Verified again against
the real module after that run:

```text
$ python3 tools/durability_matrix_selftest.py --only kill-9
...
56 checks
32 stores; 224 cells; explicit gaps: removable media, empty-file recovery
$ echo $?
0
```

### `preserve_damaged` bypass is caught by corrupt open+close

```text
$ PYTHONPATH="$REAL_DE" DURABILITY_DE=/tmp/031-red-corrupt python3 tools/durability_matrix_selftest.py --only corrupt-store
...
FAILURES
- academics.json/corrupt-store: academics.json: open+close lost damaged bytes b'{"unfinished":'
...
- writer.json/corrupt-store: writer.json: open+close lost damaged bytes b'{"unfinished":'

33 checks
32 stores; 224 cells; explicit gaps: removable media, empty-file recovery
$ echo $?
1
```

Verified against the real module immediately afterward:

```text
$ python3 tools/durability_matrix_selftest.py --only corrupt-store
...
193 checks
32 stores; 224 cells; explicit gaps: removable media, empty-file recovery
$ echo $?
0
```

The final standalone run after the scanner correction is the authoritative
green transcript:

```text
$ python3 tools/durability_matrix_selftest.py
...
504 checks
32 stores; 224 cells; explicit gaps: removable media, empty-file recovery
$ echo $?
0
```

The final run completes far below 300 seconds; no **TIMEOUTS** override is
needed.

## Defects filed

One append-only line was added to `release/1.0/HANDOFF.md`:

```text
2026-08-07 · 031 -> durability lane · zero-byte JSON stores are not preserved by preserve_damaged(), so damaged store + open + close can leave no recoverable copy of the original empty bytes
```

No durability implementation was changed.

## Campaign verification (M2) — 2026-08-07

Ran clean-process: 504 checks, 224 cells, 12s, exit 0; config_resilience /
document_safety / jobs unaffected (config_resilience requires the runner's
PYTHONPATH — that is documented in its header, not a defect). **The filed
zero-byte defect was fixed the same day** in nbapp.preserve_damaged (0 bytes
= a truncated write or disk-full create, never a healthy store; it now takes
the standard quarantine path) and the matrix's expectation was tightened to
assert it: 568 checks. Red-proof recorded at
redproofs/zero-byte-store-2026-08-07.txt — with the wave-through reverted,
every store's corrupt-store cell fails "open+close lost damaged bytes b''".
Suite auto-enrolls in run_all_gates (selftest pattern), 12s < 300s budget.
