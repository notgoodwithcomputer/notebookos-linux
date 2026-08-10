# 029 — Menu conformance gate

`python3 tools/menu_conformance_check.py` is the AST-only release gate for the
menu article of the Interaction Constitution.  It parses every Python module in
the desktop app directory, selects `AppWindow` subclasses, and never imports or
executes application code.  The accepted baseline runs 800 individual checks.

## Contract coverage

- §1, ellipses: registry-recognised literal and `nbcommands` labels must have
  the registry's ellipsis state. Dialog/picker/confirmation behaviour reached
  only through runtime indirection is not statically knowable; the gate does
  not claim to decide whether an arbitrary callback eventually asks a question.
- §2, the two File models: registry-resolved commands are checked in canonical
  grouping/order, and Save/Save As are treated as the document command pair.
  The gate cannot infer whether a product conceptually owns documents or one
  store; that classification is product meaning rather than syntax.
- §3, accelerators: labels use exactly four spaces; accelerators are unique per
  app; every resolved registry command is compared in both directions (shown
  but not declared, and declared but absent from the shown label). Runtime GTK
  key-event wiring cannot be proven without executing the app.
- §4, menu titles/order: literal `menus` declarations require File, Edit, View
  in that relative order, app-specific menus after those, and Help last.
  Registry order/group metadata and Undo/Redo-before-clipboard are checked when
  the relevant entries resolve. Runtime replacement of `self.menus` is not
  checked because its value depends on state.
- §5, disabled rather than absent: `(label, None)` is retained and analysed as
  a visible disabled item. Whether a branch removes an item at runtime is not
  statically decidable.
- §6, wording: resolved action labels are checked for Title Case and canonical
  registry wording. Whether a phrase names an outcome rather than a mechanism
  is semantic and remains a review responsibility.
- Separators: resolved entries reject adjacent separators and registry group
  transitions are analysed from source order. Runtime-composed list edges
  cannot be certified where list contents do not resolve.
- Context menus: labels of `Gtk.MenuItem`, `Gtk.CheckMenuItem`, and
  `Gtk.RadioMenuItem` appended/prepended to a hand-built `Gtk.Menu` (including
  `_t()`-wrapped literals) must occur in the same app's resolved menu bar.

## Exact debt inventory

Every row below is an exact `(file, line, rule, detail)` ledger row. A source
finding without its row is NEW and fails; a row whose source finding disappears
is STALE and also fails.

- `accounting.py:1900` — Print omits registry `Ctrl+P`.
- `bills.py:1973` — Print omits registry `Ctrl+P`.
- `contacts.py:1316` — Print omits registry `Ctrl+P`.
- `cookbook.py:1823` — Print omits registry `Ctrl+P`.
- `ebook.py:2163` — Open omits registry `Ctrl+O`.
- `journal.py:1366` — Print omits registry `Ctrl+P`.
- `maps.py:913` — Zoom In omits registry `Ctrl+Plus`.
- `maps.py:914` — Zoom Out omits registry `Ctrl+Minus`.
- `packages.py:339` — Find omits registry `Ctrl+F`.
- `packages.py:339` — Find has an ellipsis although the registry command is an
  inline, immediate action and declares none.
- `screenplay.py:1418` — Print omits registry `Ctrl+P`.
- `sequencer.py:5318` — Zoom In shows `+`, not registry `Ctrl+Plus`.
- `sequencer.py:5319` — Zoom Out shows `−`, not registry `Ctrl+Minus`.
- `terminal.py:354` — Close omits registry `Esc`.
- `terminal.py:367` — Close omits registry `Esc`.
- `terminal.py:373` — Copy shows `Ctrl+Shift+C`, not registry `Ctrl+C`.
- `terminal.py:374` — Paste shows `Ctrl+Shift+V`, not registry `Ctrl+V`.
- `terminal.py:375` — Select All shows `Ctrl+Shift+A`, not registry `Ctrl+A`.
- `music.py:1969` — context action `Add to playlist` has no menu-bar action.
- `sequencer.py:5371` — menu item `No microphone or input found` is sentence
  case rather than Title Case.

## Red proofs

All fixtures were created textually under the real app directory, then deleted
textually. After each deletion, `git diff -- <fixture>` printed nothing and
`test ! -e <fixture>` printed `byte-identical=absent`.

### Duplicate accelerator

Exact edit: create `menu_gate_redproof.py`, an `AppWindow` subclass whose
`menu_items()` returns `[("Alpha    Ctrl+K", None), ("Beta    Ctrl+K", None)]`.

```text
FAIL NEW   menu_gate_redproof.py:8 [duplicate-accelerator] Ctrl+K also at line 8
805 checks
RESULT: FAILED: 1 new, 0 stale
exit=1
```

### Context-only action

Exact edit: create `menu_gate_redproof.py` with menu-bar label `Present`, then
construct `Gtk.Menu()`, construct `Gtk.MenuItem(label="Context Only")`, and
append that item.

```text
FAIL NEW   menu_gate_redproof.py:13 [context-subset] 'Context Only'
804 checks
RESULT: FAILED: 1 new, 0 stale
exit=1
```

### Missing debt row

Exact edit: delete only the `music.py:1969` tuple from `DEBT`, leaving the real
context-only action untouched, then restore that exact tuple textually.

```text
FAIL NEW   music.py:1969 [context-subset] 'Add to playlist'
800 checks
RESULT: FAILED: 1 new, 0 stale
exit=1
```

This is the real-violation half of the exact ledger mismatch: deleting debt
while source still reproduces makes the finding NEW. The reverse operation
(fixing source while retaining debt) is reported as `FAIL STALE` by the same
set comparison.

## Runtime

Measured on the campaign host with `/usr/bin/time`: approximately 1.5 seconds
wall clock for 800 checks. Expected release-machine runtime is under 3 seconds;
the work is linear AST parsing over the desktop Python sources with no imports,
subprocesses, display startup, or I/O beyond reading source files.
