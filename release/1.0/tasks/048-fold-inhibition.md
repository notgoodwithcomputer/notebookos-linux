# Task 048 — folded-input inhibition

Completed xtabletd v2 for lane `test-batch`.

## What shipped

Tablet mode now starts `python3 /opt/notebook/de/osk.py` in its own process
group and polls it for about two seconds. If the native OSK is absent or exits
during confirmation, xtabletd emits one fallback line and tries executable
`/usr/bin/matchbox-keyboard`. If neither survives confirmation, tablet state is
still published but no physical device is inhibited. This follows the task's
stated assumption that `osk.py` shows itself at startup and exits on SIGTERM;
`osk.py` itself was not changed.

The XInput2 adapter uses `libXi.so.6` and `libX11.so.6` through ctypes. It asks
`XIQueryDevice(XIAllDevices)` for the inventory, considers slave pointer and
slave keyboard devices only, resolves `Device Node`, reads the event device's
`phys` and `id/bustype`, and changes `Device Enabled` with `XIChangeProperty`.
The in-memory disabled-ID map is mirrored as human-readable device names in
`/tmp/nb-tablet-inhibited`.

## Matcher and why

- Internal evidence: `phys` must contain `isa0060` or `serio` for an i8042
  keyboard, or `i2c` for an internal touchpad/pointing device. This is the
  hardware-location signal that distinguishes folded-back controls from loose
  peripherals.
- External veto: a USB or Bluetooth physical path/name, or Linux bustype
  `0003`/`0005`, is never selected. Bustype keeps generic-name Bluetooth and
  USB devices external even when their `phys` text is ambiguous.
- Class allowlist: the independent XInput class/name signal must also identify
  a slave keyboard or a touchpad/trackpad/trackpoint/pointing stick. Ordinary
  mice and master devices are ineligible.
- Touchscreens are affirmatively rejected through XInput's touch class as well
  as touchscreen name markers. The front-side touch surface must remain usable.
- No `Device Node` means no match. Without the node, xtabletd cannot establish
  the Linux physical path, so disabling would violate the conservative
  internal-only rule.

Headless inventories pin internal AT/i8042 keyboard and i2c touchpad matches;
USB and Bluetooth keyboards and mice, a touchscreen, and a device without
`Device Node` are pinned as non-matches.

## Order law

- Enter: `start-osk -> confirm -> inhibit`, exactly in that order. Failed
  confirmation ends the sequence without inhibition.
- Leave: `enable -> stop-osk`, exactly in that order. Input restoration begins
  before OSK retirement or the laptop-mode flag write.

## Complete fail-open inventory

- Startup healing enumerates every device selected by the current matcher,
  sends an enable for each regardless of this process's in-memory ownership,
  and removes stale `/tmp/nb-tablet-inhibited` in a `finally` block.
- SIGTERM and SIGINT synchronously call `enable_all` before stopping the watcher.
- The registered `atexit` hook calls `enable_all`.
- `run_watch_loop` calls `enable_all` in `finally`, including an exception raised
  inside the live watch loop; the headless test drives an injected exception.
- Any exception during enumeration, disabling, or inhibition-file recording
  calls `enable_all` for the complete currently tracked set before propagating.
- Leaving tablet mode and normal `close()` each call `enable_all` before OSK
  shutdown.
- A device is placed in the tracked set before its disable request. A failed
  enable stays tracked so later cleanup paths retry it, while cleanup continues
  across the rest of the set if one device operation raises.
- An absent, unlaunchable, or early-exiting OSK prevents inhibition entirely.
- X connection loss is intentionally not given a pretend recovery path: it
  means the X session ended, and X discards the session's device state when the
  server/session restarts. This rationale is documented next to X teardown.

## Red-proofs run and reverted

1. USB safety: I temporarily removed the USB veto and explicitly allowed USB
   physical paths as internal. `MatcherTests.test_usb_keyboard_never_matches`
   failed with `AssertionError: True is not false` (exit 1). The sabotage was
   reverted. A preliminary, weaker attempt that only removed the explicit USB
   veto stayed green because the positive internal-path requirement still
   rejected USB; it was not counted as a red-proof.
2. SIGTERM restoration: I temporarily removed `actions.enable_all()` from
   `fail_open_stop`. `SafetyTests.test_sigterm_reenables_entire_disabled_set`
   failed because `[] != [(7, True), (9, True)]` (exit 1). The sabotage was
   reverted.

The final full suite passed after both reversions.

## Verification tails

`python3 -m py_compile buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/xtabletd.py tools/xtabletd_selftest.py`

```text
(no output)
EXIT: 0
```

`python3 tools/xtabletd_selftest.py`

```text
SKIP: /dev/uinput not accessible -- chain section needs root (runs on the guest)
test_real_kernel_event_chain (__main__.UinputChainTests.test_real_kernel_event_chain) ... ok

----------------------------------------------------------------------
Ran 21 tests in 2.669s

OK
EXIT: 0
```

The `/dev/uinput` live-device portion was unavailable in this sandbox as
expected; its existing headless gate test passed. All new matcher, ordering,
and failure-path tests use injected fakes and ran without X or real sysfs.

`python3 tools/self_attr_audit.py`

```text
132 classes checked (0 for calls only), 0 skipped, 0 finding(s)
CLEAN: no undefined self attributes, every class checked
EXIT: 0
```

`python3 tools/voice_check.py`

```text
7 flagged string(s) across 69 file(s)
   prose-in-ui              5
   second-person            1
   coaxing-prompt           1
RESULT: CLEAN
EXIT: 0
```

`python3 tools/jargon_sweep.py`

```text
=== xclipd.py ===
  xclipd.py:141  [graphics/X: GTK] (allow)
      'Gtk'

121 flagged strings
RESULT: CLEAN
EXIT: 0
```
