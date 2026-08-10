# 019 — A media key either does something or says why not

**Lane:** D (session) · **Streams:** S1 truth defects
**Status:** CLOSED

Third find from the untested-module sweep. `de/nbmediakeys.py` grabs the XF86
volume and brightness keys for the whole session — it is the only thing between
those keys and nothing at all — and had no suite.

## The file already contained the argument against itself

`_OSD.show_note` exists because a volume key that moves an unheard level, or
shows nothing whatever, *"reads as a dead key."* Six hundred lines above it, the
module header describes exactly that as the intended brightness behaviour:

> Brightness needs a backlight device (real hardware); with none present
> (QEMU, a desktop with no panel) the brightness keys simply no-op — no OSD,
> no error.

Same principle, applied to one branch and not its sibling. Volume was silent in
the same way whenever there was no mixer to move at all: `_amixer_state`
returned `None`, `_on_key` tested `if pct is not None`, and nothing happened.

Both now say so. The brightness note reuses the sentence the Displays page
already uses for the same situation — *"This screen cannot be adjusted from
here."* — rather than inventing a second way to say it; the volume note is
built on the same construction. The television case keeps its own, more
specific sentence, and the suite checks that the new note did not swallow it.

## The brightness key could black the screen out

`_brightness` clamped to `max(0, min(mx, new))`. Zero is a backlight that is
off. Every control for getting back is on the screen you can no longer read,
and **Settings has no brightness page at all** — these keys are the only way
to change it — so the state has no exit that does not involve pressing a key
you cannot see the effect of. Two presses from 16% reached it.

Floor is now `max(1, mx // 20)`: 5% of the device's own range, and at least one
raw step, so a panel whose `max_brightness` is 7 still lands somewhere visible
instead of being pinned at a floor it cannot leave. Full brightness is
unaffected — the suite walks it all the way back up to `mx`.

## Gate

`tools/mediakeys_selftest.py`, 16 checks.

**It drives a real backlight tree.** `_brightness` reads `max_brightness`,
reads `brightness`, computes and writes it back; a stubbed `_brightness` would
be a test of the stub. `/sys/class/backlight/*` is found by a glob, so the glob
points at a directory of real files and the value is read back off disk after
each press. Two panels: a 100-step one and a 7-step one, because the floor
arithmetic is where a small range breaks.

One trap worth recording: `MK.glob` **is** the test's own `glob` — the same
module object — so a replacement that calls `glob.glob()` calls itself. The
first run hit a `RecursionError` 992 frames deep. Hold the real function first.

**Red-proof, six mutations:**

| mutation | result |
|---|---|
| floor back to 0 (the shipped arithmetic) | 3 fail |
| brightness note removed (the shipped branch) | 4 fail |
| volume note removed (the shipped branch) | 3 fail |
| the new note replaces the television sentence | 1 fail |
| an over-long German translation | 1 fail — `de 100px` |
| a note missing from one catalog | 1 fail — `ja` |

The television one matters because both notes live on the volume path: without it the
suite would accept a fix that made the HDMI case less specific.

## Also

*"This screen cannot be adjusted from here."* was in
`tools/i18n_coverage_baseline.txt` as a known-untranslated string. Using it in a
second place made translating it worth doing: it and the new volume sentence are
now in all 17 catalogs, and the baseline is down to 119 entries.


## The OSD note can overflow, and now cannot silently

The popup is 300×92 and a note is drawn at `(H - lh) / 2`. Pango wraps it
horizontally, but nothing checks the height: the moment the wrapped text is
taller than the popup that y goes **negative**, the text is drawn above it and
clipped, with nothing on screen to say so. This is the small-screen overflow
class, vertically, and the sentence I added is longer than the one that was
there.

Measured across all 17: the worst case is Yiddish's television line at 63px of
92, and the new strings top out at German's 60px. Nothing clips today. Pinned
anyway, because the margin is 29px and the next translation is one edit away.
The check reads the catalogs directly — these are plain lookups, so the JSON
value is exactly what `_t` returns and all seventeen measure in one process,
unlike the date strings in 018.

## Two stale comments in session.sh, same file, same class

Neither is a code defect; both tell a future reader something false about
behaviour, which is how the next defect gets written.

**It said xflushd warps the pointer.** Twice — once to justify a guard ("two of
them fight over the cursor") and once to explain why it is gated on
acceleration ("perpetual pointer-warp … make the cursor vanish"). `xflushd.py`
states outright that it does **not** warp the pointer; its loop is
`flush(); sync(); sleep(0.5)`. The warp is `xflush.py`, a different file, run
once per window map. The guard is still right — two daemons flushing one
display is pointless — so only the reasoning was corrected.

**It said the dbus-launch timeout was inert.** The note explained that busybox
here is built with `CONFIG_TIMEOUT` off, that `timeout 10 dbus-launch` was
therefore dead, and gave instructions for arming it. Somebody followed them:
`board/notebookos/busybox-timeout.fragment` exists, `.config` points
`BR2_PACKAGE_BUSYBOX_CONFIG_FRAGMENT_FILES` at it, `CONFIG_TIMEOUT=y` is set,
and `./usr/bin/timeout` is in the shipped `rootfs.tar`. The bound is armed and
the note advertised its absence. Same shape as the `hwclock` finding: **an
item's stated cause is as stale as everything else in a record.**
