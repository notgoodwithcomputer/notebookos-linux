# 040 — Packages becomes real

## Decisions

- Kept Sources. It already performs a useful, checkable job: it reads mounted removable media from `/proc/mounts` and lists this computer plus the USB storage that is actually present. Its copy now describes that inventory only. It does not claim that Packages can install from it.
- Updates remains an honest boundary page. It says that Packages does not install updates and that Notebook OS is delivered as a complete system image. This matches the repository's full-image release builder and installer; it does not invent an in-place package-update mechanism.
- Uninstall means removal from Finder's Applications listing, not deletion. The selected app stays in the read-only image and Restore is always available, so no confirmation dialog is needed.
- System/shared modules have no Uninstall or Restore control. The absence is intentional; those files are not app-launcher visibility choices.
- The two existing Package-menu debt rows were not changed semantically. `Find…` remains at packages.py line 339 so the line-ledgered accelerator and ellipsis debt does not become new or stale Packages debt.

## Changes

- Added a Finder-compatible `$NB_HOME/.config/notebook/removed_apps.json` reader and atomic writer. Each Uninstall or Restore performs a fresh read-modify-write against the current list and writes the sorted JSON list Finder consumes.
- A malformed store loads as an empty display state and browsing performs no write. An explicit user action is the only path that may replace malformed contents; `nbapp.atomic_write_json` preserves damaged bytes by its shared policy.
- Application details show Shown or Removed, offer Uninstall or Restore immediately, and explain that the app stays on disk. System details state that system files remain in the read-only image.
- Removed both false USB-install promises and the related implied empty-state instruction. Sources now says what storage is attached; Updates states its real limitation.
- Raised sort-header text from the 2.92:1 faint token to the design-system muted token `#6E695E`, and added `.sorthdr label { color: inherit; }` for GTK's button-label child boundary. CSS remains ASCII and parses.
- Extended the real-window interaction suite to pin app and system affordances and to report a clean display SKIP when GTK3 cannot initialize. Added `tools/packages_removal_selftest.py` for the display-free persistence, damage, truth-copy, and CSS contracts.

## Other fake or dead surfaces reviewed

- The old module introduction said a USB package format and SDK were planned and described the installed set as non-removable. It was rewritten to describe only the implemented image, visibility, verification, Updates, and Sources behavior.
- The old Sources empty row instructed the user to plug in USB storage to install packages. It now reports only that no USB storage is attached.
- No other placeholder controls, planned actions, disabled uninstall controls, or fabricated package rows remain in this file. Verify still performs a real source read and parse; Open still launches the selected application; sorting, search, source scanning, and navigation remain live.

## Verification

Final command tails:

```text
python3 -m py_compile packages.py tools/packages*selftest*.py
(no output; exit 0)

packages_removal_selftest.py
ok   Uninstall writes Finder's exact sorted JSON-list format  b'["Calendar", "Writer"]'
ok   Uninstall updates state and the inspector immediately
ok   Restore removes only the selected display name  b'["Calendar"]'
ok   System modules expose no uninstall path
ok   Browsing a damaged store never overwrites it  b'{not a JSON list\n'
ok   promissory sentence is absent: New packages install from a USB stick.
ok   promissory sentence is absent: Package updates install from a USB stick.
ok   sort-header CSS explicitly carries colour to its label
ok   sort-header uses the design-token muted colour
0 failed

packages_selftest.py
all ok

packages_transition_selftest.py
24 checks, 0 failed

packages_interaction_selftest.py
SKIP: GTK3 display is unavailable; interaction section not run

self_attr_audit.py
120 classes checked (0 for calls only), 0 skipped, 0 finding(s)
CLEAN: no undefined self attributes, every class checked

voice_check.py --file packages.py --fail
0 flagged string(s) across 1 file(s)
RESULT: CLEAN

jargon_sweep.py packages.py
2 flagged strings (both existing GTK allow entries)
RESULT: CLEAN

css_parse_check.py packages.py
packages.py  1 css block(s)
clean

ascii_css_check.py
clean: no non-ASCII inside any bytes literal

design_tokens.py packages.py
TOTAL off-token: colour 0   font-size 0   radius 0
files with drift: 0

menu_conformance_check.py
FAIL STALE contacts.py:1316 [registry-accelerator] Print: shown '', registry 'Ctrl+P'
810 checks
RESULT: FAILED: 0 new, 1 stale
```

The menu gate's sole failure is concurrent and outside this task's allowed files: `contacts.py` changed while this work was in progress, leaving its Print debt row stale. Packages has no new or stale row; its two pre-existing `Find…` findings still resolve exactly to the ledgered packages.py line 339. Neither `contacts.py` nor the gate was edited here.

## Red proofs

1. Temporarily changed the atomic payload from the required list to `{"removed": sorted(current)}`. The suite failed with:

   ```text
   FAIL Uninstall writes Finder's exact sorted JSON-list format  b'{"removed": ["Calendar", "Writer"]}'
   FAIL Restore removes only the selected display name  b'{"removed": []}'
   2 failed
   ```

   The payload was reverted and the suite returned to `0 failed`.

2. Temporarily re-added the exact sentence `New packages install from a USB stick.` to packages.py. The suite failed with:

   ```text
   FAIL promissory sentence is absent: New packages install from a USB stick.
   1 failed
   ```

   The sentence was removed and the suite returned to `0 failed`.

## Translations

- Added 14 English source strings, all passed through `_t(...)`.
- Added 17 matching, sense-first fragments at `release/1.0/i18n-fragments/040-packages/<lang>.json`: de, el, eo, es, fr, hi, it, ja, ko, nl, pl, pt, ru, sr, tr, yi, zh.
- Validation: `fragments=17`, `keys_each=[14]`, `same_keys=True`.

## Finder handoff

Packages writes Finder's existing display-name list contract. An already-open Finder re-reads that store on Finder's own schedule; immediate live refresh inside Finder belongs to the separate Finder job and was not implemented here.
