# 035 — Article G motion inventory gate

## Honest baseline

Article G contains **45 entries: 3 implemented, 10 partial, 32 unimplemented**.
That is the UI campaign burn-down baseline. `pacing_required` is deliberately
false: null pacing records warn for implemented/partial entries until the later
campaign phase supplies per-entry results for both paths and flips the flag.

Source inspection established the following:

- `splash.py` really does advance `_fraction` in `Splash._tick_bar`, repainting
  each advance and finishing at 1.0; this implements `system.boot-session`.
  It does not lift the splash or settle the desktop, so that next row remains
  unimplemented.
- The current `shell.py` contains structured markers and animation behavior for
  `system.panel-menu-open` and `system.panel-menu-close`; both are implemented.
- `g2048.py`'s `move` changes the board model and `_refresh` directly replaces
  changed labels/styles. No tile-position animation call exists. Despite the
  specification calling it the reference, `content.2048` is honestly recorded
  as unimplemented.
- `nbtransitions.py` supplies `switch_page`, `reveal`, and `replace`. These can
  partially carry page/pane switching, About disclosure, list insertion/removal,
  tab content crossfade, value replacement, and empty/populated replacement.
  It does not by itself prove pervasive call-site adoption or origin-bound
  growth, so the ledger does not inflate those claims.
- Papertone's documented state-feedback section restricts transitions to
  color/border/shadow/opacity and uses 90ms. That partially carries inline edit,
  toolbar state, and toggle feedback, without claiming travelling geometry.

## Red-proofs

All edits below were temporary changes to the ledger and were reverted before
the next case. The baseline JSON digest after restoration was
`0ea3a36f7e1dc525d4409fcaf4949885a059c8d6bad5617bfc9412c3137742f7`.

### A — false implemented claim with bogus binding

Temporary diff:

```diff
-"status":"unimplemented","pacing":null
+"status":"implemented","binding":{"binding_kind":"module-behavior","module":"buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/splash.py","symbol_or_marker":"definitely_missing"},"pacing":null
```

Command and complete output:

```text
$ python3 tools/motion_inventory_check.py; rc=$?; echo "exit=$rc"
STATUS: implemented=4 partial=10 unimplemented=31 total=45
Entries missing an implementation binding:
  none
Implementation markers with no matching entry:
  none
WARN: null pacing result: system.boot-session
WARN: null pacing result: system.app-launch
WARN: null pacing result: system.panel-menu-open
WARN: null pacing result: system.panel-menu-close
WARN: null pacing result: app.page-pane-switch
WARN: null pacing result: app.about
WARN: null pacing result: app.tab-section-change
WARN: null pacing result: app.list-insert
WARN: null pacing result: app.list-remove
WARN: null pacing result: app.inline-edit-begin-end
WARN: null pacing result: app.toolbar-state
WARN: null pacing result: app.any-value-change
WARN: null pacing result: app.any-toggle
WARN: null pacing result: app.empty-populated
FAIL: entry binding symbol absent: system.app-launch (definitely_missing)
RESULT: FAILED — 1 failures; 106 checks
exit=1
```

The inverse diff restored `system.app-launch` to unimplemented; the restored
digest matches the baseline above.

### B — marker with no inventory entry

`--extra-file` exists specifically so this direction can be red-proofed without
modifying out-of-scope `de/*.py`. The scratch file contained only:

```python
# nbmotion-inventory: no.such-id
```

Command and complete output:

```text
$ python3 tools/motion_inventory_check.py --extra-file /tmp/claude-1000/-home-ben-Documents-notebookos-linux/d67f9712-06ad-45da-8334-54deb8646747/scratchpad/wp035_stray.py; rc=$?; echo "exit=$rc"
STATUS: implemented=3 partial=10 unimplemented=32 total=45
Entries missing an implementation binding:
  none
Implementation markers with no matching entry:
  no.such-id
WARN: null pacing result: system.boot-session
WARN: null pacing result: system.panel-menu-open
WARN: null pacing result: system.panel-menu-close
WARN: null pacing result: app.page-pane-switch
WARN: null pacing result: app.about
WARN: null pacing result: app.tab-section-change
WARN: null pacing result: app.list-insert
WARN: null pacing result: app.list-remove
WARN: null pacing result: app.inline-edit-begin-end
WARN: null pacing result: app.toolbar-state
WARN: null pacing result: app.any-value-change
WARN: null pacing result: app.any-toggle
WARN: null pacing result: app.empty-populated
FAIL: implementation marker with no inventory entry: no.such-id (/tmp/claude-1000/-home-ben-Documents-notebookos-linux/d67f9712-06ad-45da-8334-54deb8646747/scratchpad/wp035_stray.py)
RESULT: FAILED — 1 failures; 106 checks
exit=1
```

This case never changed the JSON; its digest therefore remained the baseline.

### C — implemented entry loses its binding

Temporary diff:

```diff
-"status":"implemented","binding":{"binding_kind":"module-behavior","module":"buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/splash.py","symbol_or_marker":"_tick_bar"},"pacing":null
+"status":"implemented","pacing":null
```

Command and complete output:

```text
$ python3 tools/motion_inventory_check.py; rc=$?; echo "exit=$rc"
STATUS: implemented=3 partial=10 unimplemented=32 total=45
Entries missing an implementation binding:
  system.boot-session
Implementation markers with no matching entry:
  none
WARN: null pacing result: system.boot-session
WARN: null pacing result: system.panel-menu-open
WARN: null pacing result: system.panel-menu-close
WARN: null pacing result: app.page-pane-switch
WARN: null pacing result: app.about
WARN: null pacing result: app.tab-section-change
WARN: null pacing result: app.list-insert
WARN: null pacing result: app.list-remove
WARN: null pacing result: app.inline-edit-begin-end
WARN: null pacing result: app.toolbar-state
WARN: null pacing result: app.any-value-change
WARN: null pacing result: app.any-toggle
WARN: null pacing result: app.empty-populated
FAIL: entry missing implementation binding: system.boot-session
RESULT: FAILED — 1 failures; 104 checks
exit=1
```

The inverse diff restored the `_tick_bar` binding; the digest again matches the
baseline.

## Restored-baseline verification

```text
$ sha256sum tools/motion_inventory.json
0ea3a36f7e1dc525d4409fcaf4949885a059c8d6bad5617bfc9412c3137742f7  tools/motion_inventory.json
$ python3 tools/motion_inventory_check.py; echo "exit=$?"
STATUS: implemented=3 partial=10 unimplemented=32 total=45
Entries missing an implementation binding:
  none
Implementation markers with no matching entry:
  none
WARN: null pacing result: system.boot-session
WARN: null pacing result: system.panel-menu-open
WARN: null pacing result: system.panel-menu-close
WARN: null pacing result: app.page-pane-switch
WARN: null pacing result: app.about
WARN: null pacing result: app.tab-section-change
WARN: null pacing result: app.list-insert
WARN: null pacing result: app.list-remove
WARN: null pacing result: app.inline-edit-begin-end
WARN: null pacing result: app.toolbar-state
WARN: null pacing result: app.any-value-change
WARN: null pacing result: app.any-toggle
WARN: null pacing result: app.empty-populated
PASS  motion inventory conformance: 105 checks
exit=0
```
