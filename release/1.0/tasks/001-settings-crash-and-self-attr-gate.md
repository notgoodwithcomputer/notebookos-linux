# 001 — Settings crash on upgraded machines + the gate that missed it

**Lane:** A (apps) + C (harness) · **Streams:** S1 truth defects, S2 evidence
**Status:** CLOSED 2026-08-06

## Claim
`settings.py:_apply_saved_prefs` called `self._apply_pointer_speed()` and
`self._apply_natural_scroll()`. Both methods were removed together with the
Mouse & Touchpad page (ROADMAP #24 — the page was inert, `xinput` is not in the
image). `_apply_saved_prefs` runs from `__init__`, so on any machine whose
`settings.json` still carried `pointer_speed` or `natural_scroll` — that is, any
machine where the Mouse page had ever been touched before the upgrade — opening
Settings raised `AttributeError` and **Settings would not open at all.**

## Fix
The four lines are gone. The keys are now ignored deliberately, matching the
`background` key handled ten lines above for the same reason: the page that
could change the value back no longer exists, so honouring a saved value would
strand the user with a setting they can neither see nor undo.

## Why nothing caught it
* `py_compile` — an attribute is resolved at runtime; nothing to see.
* `undefined_names_audit.py` — reports **CLEAN** on the buggy file. It collects
  bare *names* and flags `ast.Name` nodes; `self.foo` is an `ast.Attribute` and
  is invisible to it by construction. This is the documented other half.
* `construct_all_host.py` — constructs each app on a **fresh profile**, where
  neither key is present, so the guarded branch never runs.
* `settings_selftest.py` — never drives `_apply_saved_prefs` with legacy keys.

## The gate: `tools/self_attr_audit.py`
Flags `self._foo` that is never defined on the class or any in-tree base.
Scoped to single-underscore names, so an out-of-tree base (`Gtk.Window`) cannot
produce a false positive. `setattr(self, ...)` is modelled in three tiers —
constant name, loop over a literal tuple, and helper parameter resolved through
its call sites — which took coverage from 97 classes / 17 skipped to **114
classes / 0 skipped**. A skipped class fails the gate, because with zero skips
today any skip is a coverage regression rather than a fact of life.

## Red-proof (run 2026-08-06)
Four mutations on a copy of the tree: the original `settings.py` call restored,
plus `_cancel_app_flag_monitor` (finder), `_cancel_source` (illustrator) and
`_save_autosave` (writer) renamed at the definition with all call sites left
pointing at the old name. The last three are classes the first version of this
gate **skipped**.

    finder:913  Finder.self._cancel_app_flag_monitor()  -- method never defined ...
    finder:1929 Finder.self._cancel_app_flag_monitor()  -- method never defined ...
    settings:775 Settings.self._apply_pointer_speed()   -- method never defined ...
    writer:2586 Writer.self._save_autosave()            -- method never defined ...
    writer:2852 Writer.self._save_autosave()            -- method never defined ...
    writer:3516 Writer.self._save_autosave()            -- method never defined ...
    illustrator:525 class Illustrator is no longer checkable -- setattr may store a callable

    113 classes checked (0 for calls only), 1 skipped, 6 finding(s)
    EXIT=1

The illustrator line is the interesting one: renaming the `setattr` helper makes
that class unsound to reason about, and an earlier draft turned that into a
silent skip that still exited 0. Coverage loss now fails.

Clean tree: `114 classes checked, 0 skipped, 0 finding(s)` — EXIT=0.
