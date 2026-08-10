# 015 — The time zone leaves the window

**Lane:** B (settings) · **Streams:** S1 truth defects
**Status:** CLOSED

ROADMAP #23 called this "the most deceptive control in the OS" and blamed the
missing zoneinfo. Half of that was already wrong: `_apply_tz` falls back to a
POSIX TZ string and calls `time.tzset()`, so the Date & Time page's own clock
was genuinely, correctly on the new zone. That is precisely what made it
deceptive — the one window that could confirm the change was the one window the
change reached.

`os.environ["TZ"]` is one process's memory. The panel clock, Calendar and
Journal were started by `session.sh` long before Settings opened, and they kept
the zone the machine booted in.

## Three decisions

**Persist the POSIX form, not the IANA name.** `session.sh` is what has to act
on this, and it has nothing to look a name up in — no zoneinfo ships.
`Europe/Paris` would name a file that is not there; `CET-1CEST,M3.5.0,M10.5.0/3`
carries the offset *and* the daylight-saving rule on its own. Both keys are
written: `tz` still drives the combo's own selection, `tz_posix` is the one that
leaves the process.

**Export it inline in `session.sh`, not from a backgrounded subshell.** The
keyboard block above it is backgrounded because it calls `setxkbmap`, which
acts on the server. A variable exported by a subshell is lost with the
subshell, and the entire point is that the apps started below inherit it.

**Say what it cannot do.** A running process cannot be handed a new
environment, so apps already on screen stay on the old zone. The page now says
so, in the sentence the Language setting already uses — "Apps opened from now
on… restart the computer to change all of them." Translated to all 17. Staying
silent about the limit is the same species of lie as the original bug: a
control that lets you believe more happened than did.

## Gate

`tools/settings_timezone_selftest.py`, 16 checks. It runs the **real shell
text**, lifted out of the shipped `session.sh` by line range, with `TZ` unset
first — an inherited TZ would have satisfied the assertion whether the block did
anything or not. It also asserts glibc actually honours each stored string
(a POSIX string the C library rejects falls back to UTC in silence, which is
this bug again), that a missing settings file leaves TZ **unset** rather than
exporting an empty string, and that a truncated one does not stop the session.

The scope note is read off the **Label on the page**, not grepped from the
source: a sentence that exists in the file but is never packed says nothing to
anybody.

**Red-proof, four mutations:**

| mutation | result |
|---|---|
| Date & Time handler stops saving `tz_posix` | 2 fail |
| Region & Language handler stops saving it | 1 fail |
| the `session.sh` export block deleted | 4 fail |
| the scope note deleted | 2 fail |

## What the red-proof caught in the test itself

The Region mutation **passed** on the first draft. `_on_region_tz` ends by
syncing the Date & Time combo, which fires `_on_tz`, which saves — so on a
window that had already built both pages, deleting the Region page's own save
changed nothing. The check was measuring the other handler.

It is not a corner case: somebody who opens Settings and goes straight to
Region & Language has no `_tz_combo` to sync, and the mutated handler would have
lost their choice entirely. The check now runs in a **second process** with its
own `NB_HOME` — necessary rather than tidy, because `NB_HOME` is read at import,
so re-pointing it in-process left the second window writing into the first
window's profile (measured: the isolated check read `(None, None)`).

## A second guard, from a gate that went red

`dead_setting_check` flagged `tz_posix` as WRITE ONLY — correct on its own
terms, since no Python reads it. Rather than exempt the key, the checker now
also scans the session's shell scripts. That keeps it sharp in both directions:
delete the `session.sh` reader and the key goes dead again. Red-proofed
separately (reader deleted → 1 dead; intact → 0).
