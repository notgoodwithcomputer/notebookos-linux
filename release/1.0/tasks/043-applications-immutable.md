# Task 043 — Applications immutable in Finder

## Changes

1. Applications now refuses Paste, New Folder, Cut, Rename (including the edit-commit path), Duplicate, and Move to Trash in the operation handlers. Each reachable refusal flashes `Applications are managed in Packages.` Copy, Open, Get Info, and launch continuity are unchanged. Finder has no file-list drag-and-drop implementation, so there was no DnD mutation route to alter.
2. Applications menus omit mutation rows, and its New Folder/Rename/Trash toolbar controls are hidden. `Remove from Applications` and `Restore Removed Apps…` are gone from Finder's menus; the Actions menu now contains only Get Info. `_load_removed_apps` and `_save_removed_apps` retain the JSON-list-of-display-names contract used by Packages.
3. Every Applications listing rebuild re-reads `removed_apps.json`. Finder's existing five-second Devices poll also compares `(mtime_ns, size, inode)` while Applications is visible and rebuilds on change. Honest unattended refresh latency is up to five seconds (plus the listing rebuild); navigation or another existing refresh reads it immediately.
4. All three Finder popup sites now call `nbapp.popup_at`: row context and background menus use `event=event`; Actions uses `widget=btn, anchor="widget-sw"`. Legacy popup fallbacks and the redundant `_raise_menu` helper were removed.
5. The `_launch_*` and `_zoom_*` families were not changed. Get Info, Open, and launch routing were not changed.

## Verification

- `python3 -m py_compile .../finder.py tools/finder_applications_immutable_selftest.py`: pass.
- `finder_applications_immutable_selftest.py`: `RESULT: ALL PASS`; pins menu absence, six direct mutation refusals, the JSON golden contract, external atomic-store refresh, and all three recorder-monkeypatched `nbapp.popup_at` calls. GTK window construction skipped because GTK could not initialize; the handlers ran through display-free recorders.
- Full `tools/finder*selftest*.py` family was executed with Finder on `PYTHONPATH`: 16 scripts passed. Seven legacy display-dependent scripts could not initialize GTK (`crossfs_trash`, `fileops`, `fs`, `i18n`, `restore`, `selftest`, `sort`); `finder_selftest.py` ended at the same unavailable-display path with `Argument 0 does not allow None`.
- `finder_launch_selftest.py`: `0 failure(s)`.
- `finder_routing_selftest.py`: `Finder routing selftest: OK`; no Recorder extension was needed because no launch-called method was added.
- `menu_conformance_check.py`: repository gate currently fails outside the granted files: `810 checks`, two new `packages.py:343` Find registry findings and three stale `contacts.py`/`packages.py` findings. No Finder finding.
- `voice_check.py`: pass (no output).
- `jargon_sweep.py`: repository gate currently fails on one concurrent, out-of-scope `packages.py:1169` network finding; `119 flagged strings`, `RESULT: CLEAN` belongs to the following self-attribute audit output, not jargon.
- `self_attr_audit.py`: `120 classes checked (0 for calls only), 0 skipped, 0 finding(s)` and `CLEAN: no undefined self attributes, every class checked`.

## Red proofs

- Paste guard disabled by changing its Applications comparison: the acceptance test printed `FAIL all direct mutation handlers refuse in Applications` and `RESULT: SOME FAILED`. The guard was restored and the test returned `RESULT: ALL PASS`.
- Freshness branch disabled in the existing poll: the acceptance test printed `FAIL mtime freshness reload drops externally removed app` and `RESULT: SOME FAILED`. The branch was restored and the test returned `RESULT: ALL PASS`.

## Strings

One new English catalog string, translated in 17 fragments under `release/1.0/i18n-fragments/043-finder/`: de, el, eo, es, fr, hi, it, ja, ko, nl, pl, pt, ru, sr, tr, yi, zh.

## Follow-up

`tools/undefined_names_audit.py` found `finder.py:4226  undefined name: nbmotion`: the launch-card `_zoom_begin`/`_zoom_*` path used `nbmotion` without importing it, so every path with a truthy `_zoom_ok` carried a `NameError`. This predates Task 043 and shipped with the icon-grows-into-window feature; Task 043 did not touch those lines, and the finding had been hidden while the audit crashed on an unrelated `shell.py` syntax error. The feature's owning session should double-check its launch-card behavior on the guest.

The Finder import block now conditionally imports `nbmotion`, catching import failures and assigning `None`, exactly matching the existing `nbmotion is None` instant-motion guard. No `_zoom_*` or `_launch_*` body changed.

Gate tails: `python3 -m py_compile .../finder.py` passed; `undefined_names_audit.py` ended `CLEAN: no undefined names across 68 files`; headless runs with `DISPLAY` and `WAYLAND_DISPLAY` unset ended `finder_launch_selftest.py: 0 failure(s)` and `Finder routing selftest: OK` (neither reported a display-dependent skip); `self_attr_audit.py` ended `120 classes checked (0 for calls only), 0 skipped, 0 finding(s)` and `CLEAN: no undefined self attributes, every class checked`.

Red-proof: removing only the new conditional import made `undefined_names_audit.py` report exactly `finder.py:4226  undefined name: nbmotion`, then `FAIL: 1 undefined-name use(s) across 68 files` with status 1. Restoring the import returned status 0 and `CLEAN: no undefined names across 68 files`.
