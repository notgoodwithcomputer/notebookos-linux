# 016 — A preference survives a restart

**Lane:** B (settings) · **Streams:** S1 truth defects
**Status:** CLOSED

ROADMAP #22, and its premise held up in full — including the detail that made
it the worst of the seven. `_apply_saved_prefs` ran inside the Settings app's
`__init__`, so screen blanking, key repeat and render scale took effect only
while somebody had **Settings open**. Nothing starts Settings at boot.

Blanking was not merely forgotten. `session.sh` runs `xset s off s noblank
-dpms` on every boot — written for walk-up demos and hardware bring-up — so a
saved five-minute blank was *actively undone*, every restart, while the page
went on displaying "5 minutes". That display reads as confirmation.

## Three decisions

**A module of its own, with no Gtk import.** This runs on the boot path. Making
it an argv branch of settings.py would have pulled Gtk in to issue three `xset`
commands, costing most of a second on every start-up. `de/nbprefs.py` imports
`os`, `sys`, `json`, `subprocess`.

**After the default, not instead of it.** `xset s off …` stays exactly where it
was and still governs a machine whose owner has never chosen. nbprefs runs
below it and touches **only keys that are present**, so a fresh install behaves
as before and a saved choice wins. Ordering is the whole fix: run the applier
first and the blanket default underneath erases it again, which is the shipped
bug with extra steps. The suite asserts the order, not just the presence.

**One implementation.** `_apply_blank`, `_apply_repeat` and `_x_output` in
settings.py now delegate. Two copies of "what does 5 minutes mean" — one for
the page, one for boot — is the same shape as the defect being fixed, and would
have drifted the first time either was touched.

The other four of the seven were already deliberate and are documented as such:
`background` and the two Mouse & Touchpad keys are ignored by design because
their pages were removed, and accessibility is read by `nbapp` at import, which
is what makes it reach every app rather than only Settings.

## Gate

`tools/session_prefs_selftest.py`, 15 checks. It reads **`xset q`** — what the
server will actually do — rather than capturing nbprefs' subprocess calls,
which would only prove intent. It restores the server's prior state on the way
out. It also runs `nbprefs.py` as a *command*, because that is how session.sh
invokes it and an ImportError there is invisible to any in-process check.

Comments are stripped from session.sh before matching: the paragraph explaining
this fix names every string the check looks for, so an unfixed session.sh with
a good comment would have passed. That is blind-spot class 7, and this repo has
now produced four instances of it.

**Red-proof, four mutations:**

| mutation | result |
|---|---|
| session.sh never calls nbprefs | 2 fail |
| called BEFORE the appliance default instead of after | 1 fail |
| settings.py keeps its own copy of the xset calls | 1 fail |
| an unset key applied at its default anyway | 1 fail |

## Three fixes to `dead_setting_check`, all earned

The new module made the checker report two live controls as dead — a false
alarm at this gate's maximum severity, where the repair is to break a correct
design until the tool goes quiet.

1. **Cross-file readers.** The checker judges each module alone, and after this
   change `nbprefs.py` is the only Python that reads `blank_timeout`. A module
   that names another's settings file outright (`"settings.json"`) now counts
   as a reader of its keys — matched on the literal filename rather than by
   pooling every read in the DE, so two apps using the same key name can still
   each go dead on their own.
2. **The accessor offset assumed a method.** `params.index(key) - 1` silently
   dropped a receiver that was not there, so `cfg_int(settings, key, default)`
   resolved to argument 0 — `settings` — instead of the key.
3. **Accessor calls had to be attributes.** Only `self._cfg_int(...)` was
   matched; a plain `cfg_int(...)` was not. An accessor does not stop being one
   for being module-level.

Red-proofed as a unit: nbprefs stops reading the keys → 2 dead; nbprefs reads
`journal.json` instead → 2 dead; intact → 0.
