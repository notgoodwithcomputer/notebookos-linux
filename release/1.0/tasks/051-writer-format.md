# Task 051 — Writer bold/italic/underline

Lane: `batch-0810`

## Defects fixed

### WRONG ANSWER — mixed selections toggled in the opposite direction

The toolbar state is taken from the leading edge of a selection. When that
edge was formatted but the whole selection was mixed, `_toggle_char()` instead
asked whether the entire range had the tag. Clicking an active B/I/U control
therefore filled the unformatted gap instead of turning the selection off.

RED (`python3 tools/writer_format_selftest.py`):

```text
FAIL mixed bold selection toggles OFF from active toolbar: toolbar=True runs=[[0, 6, 'bold']]
FAIL mixed italic selection toggles OFF from active toolbar: toolbar=True runs=[[0, 6, 'italic']]
FAIL mixed underline selection toggles OFF from active toolbar: toolbar=True runs=[[0, 6, 'underline']]
```

GREEN:

```text
PASS mixed bold selection toggles OFF from active toolbar
PASS mixed italic selection toggles OFF from active toolbar
PASS mixed underline selection toggles OFF from active toolbar
```

Root cause: command state and toolbar state used different definitions of
“active.” The command now toggles the effective state represented at the
selection's leading edge across the entire selection.

### BROKEN — paragraph-style B/I state was absent from the toolbar

A Heading 1 paragraph is visibly bold and a Quote is visibly italic, but the
toolbar inspected only explicit character tags. The corresponding control was
shown off and its first click produced no visible change.

RED:

```text
FAIL bold toolbar reflects Heading 1 paragraph style
FAIL italic toolbar reflects Quote paragraph style
```

GREEN:

```text
PASS bold toolbar reflects Heading 1 paragraph style
PASS italic toolbar reflects Quote paragraph style
PASS toolbar B/I/U state follows the cursor through formatted spans
```

Root cause: `_sync_toolbar()` ignored effective formatting inherited from the
active `style:*` tag. It now resolves explicit on/off character tags and the
paragraph-style baseline together.

### WRONG ANSWER — style-derived bold/italic could not be turned off

Removing a positive character tag cannot override Heading's bold or Quote's
italic. `GtkTextTag` properties resolve by priority, and priority follows tag
creation order. Writer had no higher-priority representation of “normal,” so
both selection toggles and collapsed-cursor pending formatting could only add
the already-visible property. There was consequently nothing correct to save,
reload, or print.

RED:

```text
FAIL collapsed bold toggles style-derived formatting OFF for typing: {'style:Heading 1', 'bold'}
FAIL collapsed italic toggles style-derived formatting OFF for typing: {'style:Quote', 'italic'}
FAIL bold OFF override is saved: [[0, 11, 'bold'], [0, 12, 'style:Heading 1']]
FAIL bold OFF override survives reload: {'style:Heading 1', 'bold'}
FAIL print path suppresses styled bold after override: {PANGO_ATTR_WEIGHT, PANGO_ATTR_SIZE}
FAIL italic OFF override is saved: [[0, 11, 'italic'], [0, 12, 'style:Quote']]
FAIL italic OFF override survives reload: {'italic', 'style:Quote'}
FAIL print path suppresses styled italic after override: {PANGO_ATTR_STYLE, PANGO_ATTR_SIZE}
```

GREEN:

```text
PASS collapsed bold toggles style-derived formatting OFF for typing
PASS collapsed italic toggles style-derived formatting OFF for typing
PASS bold OFF override is saved
PASS bold OFF override survives reload
PASS print path suppresses styled bold after override
PASS italic OFF override is saved
PASS italic OFF override survives reload
PASS print path suppresses styled italic after override
```

Root cause: Writer needed explicit, last-created `bold:off`, `italic:off`, and
`underline:off` tags. These now participate in pending typing, selection
commands, toolbar sync, `.writer` run serialization/deserialization, and Pango
print/PDF attribute derivation.

## M1 and mutant proof

The named failures above were added and executed before each production fix.
Full captures are in `.codex-scratch/051-red.txt` and
`.codex-scratch/051-red-pending.txt`; the final capture is
`.codex-scratch/051-green.txt`.

The suite accepts `WRITER_MODULE_DIR`. Its normal run copies `de/` beneath
`.codex-scratch/`, sabotages toolbar synchronization in that copy, runs itself
against the copy, and requires the named heading-toolbar assertion to go red:

```text
PASS MUTANT: sabotaged toolbar sync makes named checks red
21/21 checks passed
```

## Not-a-defect ledger

- Collapsed-cursor B/I/U already persisted across three separately inserted
  characters: `PASS pending {bold,italic,underline} survives caret motion while typing`.
- Ctrl+B, Ctrl+I, and Ctrl+U already dispatch to the same `_toggle_char`
  commands as the toolbar: all three named keyboard checks pass.
- Uniform selections already toggled correctly; the fault was specific to
  mixed selections whose leading-edge state and whole-range state differed.
- `.writer` serialization and reload already preserved positive B/I/U runs;
  the missing data representation was specifically a style override to OFF.
- No new user-visible strings were introduced, so no i18n fragments are needed.

## Files touched

- `buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/writer.py`
- `tools/writer_format_selftest.py` (new, additive)
- `release/1.0/tasks/051-writer-format.md` (this report)

## Suites and gates

- `python3 tools/writer_format_selftest.py` — **PASS, 21/21**, including
  scratch-copy PASS-MUTANT sabotage.
- `python3 tools/writer_sequencer_adversarial_selftest.py` — **PASS, 23/23**.
- `python3 -m py_compile .../writer.py tools/writer_format_selftest.py` — **PASS**.
- `python3 tools/voice_check.py --file writer.py --fail -v` — **PASS**, zero
  flagged strings.
- `python3 tools/jargon_sweep.py` — **PASS/CLEAN**; Writer's GTK token is an
  allowed technical occurrence.
- `python3 tools/self_attr_audit.py` — **PASS/CLEAN**, 132 classes checked,
  zero findings.
- `git diff --check` — **PASS**.

Display-blocked, for dispatcher re-run with a real display:

- `tools/writer_selftest.py` — blocked before assertions when `Gtk.Window`
  construction raised `RuntimeError: Gtk couldn't be initialized`.
- `tools/writer_plaintext_selftest.py` — blocked before assertions by the same
  `Gtk.Window` initialization error.

No X server was probed or started. No commits were made.

## Dispatcher verification (batch-0810, 2026-08-10)
Display rerun: writer_format_selftest 21/21; writer_selftest / writer_plaintext
/ writer_sequencer_adversarial all PASS with DISPLAY=:0. Personal belt-copy
red-proof (mixed-selection hunk reverted in a scratch copy via
WRITER_MODULE_DIR): 8 named FAILs — toggle semantics, OFF-override save/reload
×2 formats, print-path suppression. VERIFIED per M2.
