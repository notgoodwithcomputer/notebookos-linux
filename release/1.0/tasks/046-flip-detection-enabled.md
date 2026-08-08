# Task 046 — flip detection made possible (user directive)

User, verbatim: "Make flip detection possible. These ship on Yogas, and I
have one to test." This task turns the queued kernel/platform half of the
tablet-mode feature into a bootable test image.

## What changed

1. **kbuild-desktop/.config** — the four SW_TABLET_MODE sources, built in:
   `INTEL_VBTN`, `INTEL_HID_EVENT`, `IDEAPAD_LAPTOP`, `THINKPAD_ACPI`
   (all `=y`; every dependency was already satisfied — RFKILL, SERIO_I8042,
   BACKLIGHT_CLASS_DEVICE, ACPI_EC/BATTERY/VIDEO/WMI, I2C, DRM,
   PLATFORM_PROFILE — so olddefconfig added nothing else). Plus
   `INPUT_UINPUT=y` so a virtual switch device can exercise the whole chain
   in QEMU, where no ACPI switch hardware exists.
2. **buildroot/.config** — `BR2_PACKAGE_MATCHBOX_KEYBOARD=y`: the on-screen
   keyboard xtabletd raises in tablet mode. Survived olddefconfig.
3. **session.sh** — the two daemon lines (xclipd, xtabletd), placed just
   before the final `exec` of shell.py per the campaign session's condition
   (after the accel machinery, so the probe cannot race them).
4. **etc/X11/xorg.conf.d/60-notebookos-input.conf** — touchscreens now bind
   **libinput**, explicitly. QEMU exposes no touchscreen device (its
   usb-tablet is a pointer and keeps the evdev freeze-fix via the pointer
   rule), so the old touchscreen→evdev force protected nothing while costing
   real hardware XI2 touch and kinetic scrolling. The Yoga pass is the
   real-hardware verification this change was waiting for.
5. **tools/xtabletd_selftest.py** — new "uinput chain" section: with root and
   /dev/uinput it creates a virtual SW_TABLET_MODE device, starts the real
   daemon, flips the switch, and asserts the flag file follows; without
   access it prints one honest SKIP (this host: root-only /dev/uinput).
   Suite is 8 tests, green.
6. **tools/mkrelease.sh** (032's uncommitted hook, minimal repair) — the
   post-build hook's `command -v depmod` died under PATHs lacking /sbin
   (buildroot's hook env and unprivileged shells). depmod is now resolved
   with /sbin:/usr/sbin appended. modules_install itself had already been
   succeeding. Flagged to the 032 owner for their review.

## Build

`release/notebookos-1.4-fliptest.iso` (909 MB; grew from 1.3's 729 MB by the
032 GPU modules + firmware and the OSK). NOT a release candidate — the NA map
pack is not packaged (pending ISO-size decision). Overlay landing verified on
output/target after the images were removed first (the overlay-rebuild
gotcha): xclipd.py, xtabletd.py, /usr/bin/matchbox-keyboard, the session.sh
daemon lines, the libinput xorg rule, the new packages.py, and
lib/modules/7.2.0-rc3-00090-gc2950f0b750d all present. The stray
"error: you need to load the kernel first." in the build log is LITERAL
manifest text documenting the known Secure Boot first-boot message — benign.

## On the Yoga (the test the image exists for)

Boot the ISO live. Flip the hinge past flat: the on-screen keyboard should
appear and /tmp/nb-tablet-mode should read "1" (Terminal: `cat
/tmp/nb-tablet-mode`); fold back: keyboard leaves, flag reads "0". Touch on
the screen should scroll with a flick (libinput) rather than acting as a
bare click-pointer. If the switch does nothing, the useful diagnostic is
`grep -r . /sys/class/input/event*/device/name` — which platform device the
firmware exposes tells us which of the four drivers should have claimed it.

## Boot verification (QEMU, TCG, NB_GL=0)

Booted headless to the desktop at 1280x800; evidence in release/1.0/:

- `boot-verify-1.4-fliptest-desktop.png` — Finder on Applications (31 items),
  board tiles live, today circled in the calendar tile.
- `boot-verify-1.4-fliptest-launchcard.png` — the G1 launch card mid-motion
  ("Calculator" on the grown paper card): the motion inventory's first
  on-ISO appearance, per the campaign session's condition.
- `boot-verify-1.4-fliptest-calculator.png` — the task-037 TI calculator
  running on the image (Home/Graph/Table, STO->/MATH/2nd). It also EXPOSED a
  defect: at 1280 the maximized window shows unpainted BLACK flanks beside
  the centered keypad column (sibling apps paint their full allocation).
  Follow-up dispatched on calculator.py the same hour.
- `boot-verify-1.4-fliptest-clipboard-proof.png` — the user-shaped xclipd
  proof, driven over QMP: "123" typed and copied in Calculator, Calculator
  CLOSED (Esc), Journal opened, paste delivered "123" into today's entry
  ("Saved 00:25" visible). This exact sequence was the original bug.

Flip detection itself cannot fire in QEMU (no ACPI switch hardware); the
in-guest uinput chain section of tools/xtabletd_selftest.py plus the Yoga
are the remaining executions. QMP driving notes for successors: qmp.py
speaks 1920x1080 coordinates over a 1280x800 framebuffer (scale by
1.5/1.35), and key combos need raw QMP send-key with a qcode LIST
(scratchpad qmp_combo.py shape) — qmp.py's `key` op is one qcode per press.
