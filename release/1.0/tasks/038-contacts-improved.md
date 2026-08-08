# Task 038 — Contacts improvement pass

Completed 2026-08-07 in lane `test-batch`.

## Delivered

- Delete is immediate. The deleted record and its original position are retained as a deep copy; Edit > Undo Delete Contact and Ctrl+Z restore it, save the restored book atomically, select it, and refresh both panes. The status strip says that the deletion can be undone. The Card menu no longer uses an ellipsis or opens the old confirmation dialog.
- The persisted model now carries labeled `phones` and `emails` arrays, `organization`, `address`, `notes`, `bday`, and `favorite`. Old scalar `phone` and `email` records migrate on read. Normalization begins with a copy of the source dict, so unknown/future fields survive. A malformed or unreadable store raises a session-long write gate: close, edit, and add paths cannot replace its bytes.
- The detail form edits labeled values using `mobile: value; home: value; work: value`; the read card renders each value separately. Every phone and email has a keyboard-focusable Copy button using the GTK clipboard and a function-worded status confirmation.
- Favorites persist. The star button is keyboard-focusable and favorites sort before other contacts inside each letter group without disrupting A-Z grouping.
- Search includes organization, address, notes, birthday, all phone and email values, and a digits-only phone projection, so `2125550199` matches `+1 (212) 555-0199`.
- File now offers OS-picker-based Import vCard…, Export Contact vCard…, and Export All vCards…. The vCard 3.0 codec handles CRLF, folded lines, escaped backslashes/commas/semicolons/newlines, Unicode FN/N, repeated typed TEL/EMAIL values, ORG, ADR, NOTE, and BDAY, while ignoring unknown properties. CELL maps to the schema's mobile label. Exact-name imports merge: blank scalar fields fill, existing scalar conflicts win, and distinct labeled phone/email values are retained.
- PDF output includes organization plus every labeled phone and email. Imported BDAY values use the existing birthday parser and therefore participate in the upcoming-birthday bar.
- The existing fixed-card fitting remains in place; no new minimum width or height was introduced.

## Deliberate exclusions

- Arbitrary custom phone/email label names are normalized to the appropriate fallback because the requested schema explicitly limits functional labels to mobile, home, and work.
- Favorites are not exported as a private vCard extension. Favorites are local application state and no interoperable vCard 3.0 property represents them.
- The legacy `_confirm` helper remains in the source but has no deletion caller. Removing unrelated, now-dead dialog styling was avoided to keep this pass scoped; deletion itself never invokes it.

## Verification

`python3 -m py_compile` passed for `contacts.py` and every `tools/contacts_*selftest*.py` file.

Headless suite tails:

```text
PASS dedupe retains list conflict
PASS digit search ignores formatting
PASS favorites sort first inside letter
PASS undo restores byte-identical record
RESULT: ALL PASS
```

```text
ok   destroy removes each owned source exactly once after closing the gate
ok   destroy clears both source IDs
ok   close commits the edit, drops blank new card, and saves exactly once

8 checks, 0 failed
```

```text
PASS normalize-keeps-good-color-'rgb(10,20,30)'
PASS load-keeps-every-card
PASS load-every-avatar-renders
RESULT: ALL PASS
```

The display-dependent `tools/contacts_selftest.py` reached `PASS locate-window-class`, then skipped in practical terms because this sandbox has no GTK display; GTK raised `RuntimeError: Gtk couldn't be initialized`. It was not reported as a pass. The orchestrator must rerun it with a display.

Gate results:

```text
css_parse_check.py: clean
ascii_css_check.py: clean: no non-ASCII inside any bytes literal
self_attr_audit.py: CLEAN: no undefined self attributes, every class checked
jargon_sweep.py: RESULT: CLEAN
```

`menu_conformance_check.py` has no new Contacts finding. Its only Contacts line is the now-stale ledger entry for the former literal Print item; Print now comes from `nbcommands.item("file.print", ...)`. The repository-wide run also reported new failures in concurrently edited `packages.py`, outside this task's authorized file set. `voice_check.py` reported no Contacts string; its sole unaccounted finding was likewise in concurrent `packages.py`.

## Red-proof

The vCard unescaper was deliberately changed to stop converting `\\n` to a newline. The focused suite failed for real:

```text
FAIL folded line unfolds
PASS escaped comma unescapes
PASS unknown property skipped
RESULT: SOME FAILED
```

After restoration, undo was deliberately changed to drop `organization` from its restored deep copy. The focused suite exited 1:

```text
PASS digit search ignores formatting
PASS favorites sort first inside letter
FAIL undo restores byte-identical record
RESULT: SOME FAILED
```

Both mutations were reverted, and the final focused run returned `RESULT: ALL PASS`.

## Localization

There are 24 new source strings in each of 17 flat fragments, 408 translated entries total. Languages: de, el, eo, es, fr, hi, it, ja, ko, nl, pl, pt, ru, sr, tr, yi, zh. Fragments are under `release/1.0/i18n-fragments/038-contacts/` and preserve the `%d` placeholder in `Imported %d contacts`.

## Follow-up
Removed the callerless `Contacts._confirm` modal confirmation builder from `contacts.py`.
Caller check: `grep -n "_confirm(" .../contacts.py` and `rg -n "contacts\\._confirm|self\\._confirm|['\\\"]_confirm['\\\"]|getattr...|setattr...|__dict__..." contacts.py tools/contacts_*selftest*.py` both returned no matches after removal; the pre-removal grep returned only the definition at line 1320.
Verification tails: `py_compile` PASS; contacts selftests PASS (`interop` ALL PASS, `lifecycle` 8 checks/0 failed, `record` ALL PASS; GTK-backed `contacts_selftest.py` display section skipped after `locate-window-class` PASS because no DISPLAY/xvfb was available); `self_attr_audit.py` CLEAN (120 classes, 0 findings).
