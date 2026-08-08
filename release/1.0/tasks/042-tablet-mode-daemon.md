# 042 — Tablet-mode watch daemon

## What shipped

`de/xtabletd.py` is the userspace half of convertible mode.  It discovers only
`/dev/input/event*` nodes whose sysfs `capabilities/sw` hexadecimal bitmap has
bit 1, reads the initial bitmap with a computed `EVIOCGSW` ioctl (so booting
already folded is not missed), and feeds complete x86-64 `input_event` records
to a transition-deduplicating core.  EV_SYN, EV_KEY, other switches, and repeat
states do not act.

The I/O loop uses `poll()` and a deliberately slow five-second rescan.  This is
stdlib-only, finds a platform driver loaded after login, handles vanished event
nodes, and sleeps rather than spinning when no source exists.  Core parsing and
state changes are separated from all real device, process, flag, and display
I/O, which is why the suite can be wholly headless.

Each real transition atomically replaces `/tmp/nb-tablet-mode` with `1` or `0`.
Entering starts `/usr/bin/matchbox-keyboard`, if executable, in its own process
group.  Leaving or daemon shutdown sends SIGTERM only to the child process group
this instance started; there is no name search or `pkill`.  A missing keyboard
prints one diagnostic once.  With a usable display, optional Gdk publishes the
CARDINAL root property `NB_TABLET_MODE`; neither Gdk nor X is part of the core
loop.  SIGTERM closes devices, terminates the owned keyboard, and removes the
flag.  A nonblocking `flock` on `/tmp/xtabletd.lock` makes a second instance exit
quietly.

## Honest limits

This daemon is dormant until the kernel half lands.  The shipped
`kbuild-desktop/.config` has all four possible platform sources unset:

- `CONFIG_INTEL_VBTN`
- `CONFIG_INTEL_HID_EVENT`
- `CONFIG_IDEAPAD_LAPTOP`
- `CONFIG_THINKPAD_ACPI`

Those options are queued with another session.  Until one exposes
EV_SW/SW_TABLET_MODE, the daemon keeps sleeping and rescanning and publishes no
invented state.  The OSK is independently dormant because
`BR2_PACKAGE_MATCHBOX_KEYBOARD` is also queued; its eventual installed path is
`/usr/bin/matchbox-keyboard`.  This task intentionally adds no UI or i18n text.

The campaign session should add exactly these two lines to `session.sh` (that
existing file was outside this new-files-only task):

```sh
# Keep watching even without a hinge source: its platform driver may load after X.
python3 /opt/notebook/de/xtabletd.py >/dev/null 2>&1 &
```

The comment records why the process must start even on the currently dormant
kernel; the command matches the quiet house-daemon launch style.

## Headless suite

Command:

```text
PYTHONDONTWRITEBYTECODE=1 env -u DISPLAY python3 tools/xtabletd_selftest.py
```

Final tail:

```text
test_noise_and_repeats_are_ignored (__main__.CoreTests.test_noise_and_repeats_are_ignored) ... ok
test_startup_ioctl_folded_fires_once (__main__.CoreTests.test_startup_ioctl_folded_fires_once) ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.031s

OK
```

The suite covers hexadecimal masks `0`, `2`, `3`, and `20\n`; exact discovery
against a temporary fake sysfs/dev tree; packed EV_SW transitions mixed with
EV_SYN and EV_KEY noise; the already-folded ioctl startup path; atomic flag
contents and cleanup; owned OSK startup/SIGTERM; and one-time missing-binary
diagnostics.  It requires no root account, display, or real input switch.

## Red-proofs

### 1. Repeat transitions were allowed through

I temporarily changed the deduplication guard to an always-false condition and
ran the whole suite.  Actual failing assertions:

```text
FAIL: test_noise_and_repeats_are_ignored (__main__.CoreTests.test_noise_and_repeats_are_ignored)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/xtabletd_selftest.py", line 60, in test_noise_and_repeats_are_ignored
    self.assertEqual(fired, [True, False])
AssertionError: Lists differ: [True, True, False, False] != [True, False]

FAIL: test_startup_ioctl_folded_fires_once (__main__.CoreTests.test_startup_ioctl_folded_fires_once)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/ben/Documents/notebookos-linux/tools/xtabletd_selftest.py", line 72, in test_startup_ioctl_folded_fires_once
    self.assertEqual(calls, [True])
AssertionError: Lists differ: [True, True] != [True]

----------------------------------------------------------------------
Ran 6 tests in 1.041s

FAILED (failures=2)
```

The guard was restored and the next run returned `Ran 6 tests in 0.010s` / `OK`.

### 2. The hexadecimal mask tested bit 5 instead of bit 1

I temporarily replaced `(1 << SW_TABLET_MODE)` with `0x20`.  Actual failing
output (the discovery failure and all three mask subtest failures):

```text
FAIL: test_fake_sysfs_discovery (__main__.CapabilityTests.test_fake_sysfs_discovery)
AssertionError: Lists differ: ['/tmp/tmpuu0_7gmd/dev/event1'] != ['/tmp/tmpuu0_7gmd/dev/event0', '/tmp/tmpuu0_7gmd/dev/event2']

FAIL: test_hex_masks (__main__.CapabilityTests.test_hex_masks) (mask='2')
AssertionError: False != True

FAIL: test_hex_masks (__main__.CapabilityTests.test_hex_masks) (mask='3')
AssertionError: False != True

FAIL: test_hex_masks (__main__.CapabilityTests.test_hex_masks) (mask='20\n')
AssertionError: True != False

----------------------------------------------------------------------
Ran 6 tests in 0.032s

FAILED (failures=4)
```

The bit expression was restored.  The final post-restoration run is the green
run recorded above.
