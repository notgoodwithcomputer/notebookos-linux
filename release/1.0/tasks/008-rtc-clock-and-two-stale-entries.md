# 008 — The clock the OS could not keep, and two entries that were not true

**Lane:** A (settings) + C (ROADMAP) · **Streams:** S1 truth defects
**Status:** CLOSED 2026-08-06

## A. ROADMAP #20 was closed by a file deletion nobody recorded

`gbaide.py` was deleted in commit 3a75345d and folded into `gbasdk.py`. Both
halves of the entry are answered in the merged app: the Edit menu is populated
with undo/redo, and the room clear carries **both** a confirm and an
`undo.checkpoint`.

Worth recording how this surfaced. Task 006's audit probed
`has("gbaide.py", ...)`, which returns `None` for a missing file — and `None`
read as **"feature absent"** rather than **"cannot check"**. So the audit
confidently reported a defect in a file that had not existed for days. That is
the same blind-spot shape these gates keep turning up, this time in the audit
script itself, and the ROADMAP's reconciliation note now says so.

## B. ROADMAP #15: the stated cause was wrong, the symptom was real

The entry reads *"Set Clock is lost on every restart — nothing writes the RTC.
On a machine that cannot use NTP this is the whole feature"*, and both it and
the campaign plan explain it as **"no `hwclock` in the tree"**.

**There is one.** Busybox provides it — `CONFIG_HWCLOCK=y` in the built
config — and the image has it at `/sbin/hwclock -> ../bin/busybox`. An item
written off as unfixable was a two-line fix.

The symptom was exactly right, and the mechanism is worth stating because it is
not obvious:

* `_apply_datetime` ran `date -s`, which moves the RUNNING clock only.
* On x86 the kernel reads the CMOS at boot through `read_persistent_clock64`
  (`arch/x86/kernel/rtc.c`, still present in the fork) — **independent of
  `CONFIG_RTC_HCTOSYS`, which is off here**. So the boot clock comes from
  hardware the app never wrote.
* There is no networking anywhere in this OS, so no NTP corrects it afterwards.
  This button is the only clock the machine has.

Every restart therefore threw the setting away, and Calendar, Journal, Tasks
and Bill Tracker all date their records from that clock.

**Fix.** `hwclock -w` after a successful `date -s`, with three distinct
outcomes rather than one:

    date -s failed        "The clock could not be set."
    hwclock failed        "Clock set, but this computer cannot remember it.
                           It will need setting again after a restart."
    both succeeded        "Clock set."

The middle one matters most. A machine with no RTC (some boards, a VM without
one) genuinely cannot keep the time, and *"the clock is set but will not
survive a restart"* is a different fact from *"the clock is not set"* — one
sentence covering both would be false in one of the two cases. The page had no
status channel at all, so it gained one, matching the Device-name row's idiom.

**Gate: `tools/settings_rtc_selftest.py`.** `run` is stubbed rather than really
calling `date -s`, which would move the developer's system clock and leave it
moved. What is asserted is the pair of commands issued, their order, and — the
part that matters — that the three outcomes say three different things.

**Red-proof:** the `hwclock` call removed → **8 of 10 fail**, with the two
order/flag assertions reporting `[not reached: one of the two commands was
never issued]`.

Three strings, added to all 17 catalogs (3099 → 3102).

## An assertion I wrote and then deleted

The suite first checked that `_apply_datetime` survives being called before its
page exists — the shape of the task 001 crash. It failed on `self._cal`, and the
obvious next move was to add defensive initialisers.

That would have been wrong. Settings builds pages **lazily**, and
`_apply_datetime` is reachable only from the Set Clock button, which the page
itself creates. The state cannot occur, so the "fix" would have been dead code
guarding an unreachable path, and the assertion would have locked it in place.
The check is gone and the file records why, so it does not get re-added.

`self._dt_status = None` in `__init__` is kept — `_set_status` already treats
None as "nowhere to report", and one line recording which page owns the widget
costs nothing. That is a different judgement from adding four initialisers and
a guard clause to satisfy a test of an impossible state.
