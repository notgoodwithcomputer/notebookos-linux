# Notebook OS — booting on real hardware (UEFI)

This is a **dd-able UEFI USB image** for validating Notebook OS on physical
machines — primarily to confirm the panel dropdown menus render reliably (the
one behaviour that was flaky under QEMU/TCG software emulation).

- **Image:** `boot-work/notebookos-uefi.img` (~1.6 GB)
- **Rebuild it:** `tools/mkimage-uefi.sh` (after `make -C buildroot` and the
  kernel build). Layout: GPT with an EFI System Partition (GRUB + kernel) and
  the ext4 rootfs; GRUB boots the kernel with `root=PARTUUID=…` so it finds the
  rootfs regardless of the device name (sdX / nvme0n1 / mmcblk0).
- **Graphics:** the kernel uses **`simpledrm`** — a KMS device built from the
  UEFI firmware's framebuffer. This works on **any GPU vendor with no
  per-vendor driver or firmware**, which is exactly why it's a clean, universal
  test. Rendering is software (kms_swrast) at the firmware's native resolution;
  the menu-repaint test doesn't need GPU acceleration. (Accelerated GL for
  Intel/AMD is a heavier follow-up build — i915/amdgpu/nouveau modules + Mesa
  radeonsi/iris + LLVM + linux-firmware — not needed to validate the menus.)

---

## 0. Fastest check first (no USB stick, no real hardware)

The menu flakiness is almost certainly a TCG *timing* artifact. Run the **exact
same image under KVM** (real CPU timing, same virtio-gpu/swrast) on any Linux
box with `/dev/kvm`:

```
NB_KVM=1 tools/run-desktop.sh
```

If the menus paint reliably there, that alone confirms the hypothesis. Only
bother with the USB image if you want a true bare-metal run.

---

## 1. Flash the image to a USB stick

**This erases the target USB stick.** Identify the device carefully
(`lsblk`), then:

```
sudo dd if=boot-work/notebookos-uefi.img of=/dev/sdX bs=4M oflag=direct status=progress
sync
```

Replace `/dev/sdX` with the USB stick (e.g. `/dev/sdb`), **not** a partition
(`/dev/sdb1`) and **not** your system disk.

## 2. UEFI firmware settings

- **Disable Secure Boot.** The kernel/GRUB here are unsigned; Secure Boot will
  refuse to boot them.
- Legacy/CSM can stay off — this is a pure UEFI image.

## 3. Boot it

Power on with the stick inserted and pick it from the firmware boot menu
(usually F12 / F10 / Esc, machine-dependent). GRUB shows a 1-second menu with
three entries, then boots the first straight into the papertone GTK desktop:

1. **Notebook OS** — the normal desktop (this is what you want first).
2. **Notebook OS (verbose + serial console)** — same, plus a full boot log on
   `ttyS0` for debugging.
3. **Notebook OS (with compositor — menu-repaint test)** — identical desktop
   but starts the `xcompmgr` software compositor (`nb.compositor=1`). Only use
   this to A/B the panel-menu repaint question (see §4); the default entry
   deliberately runs *without* a compositor.

Expected: the top menu bar (snail logo, Finder / File / Edit / View / Go,
clock), and a Finder window on Applications, with a Devices/Places sidebar
(Home · Desktop · Documents · Applications · Music · Pictures · Videos · Trash).
The Finder toolbar has working **Back / Forward / Up**, a live **Search** box,
and a **Trash** action. Resolution is whatever the firmware set (often the
panel's native res).

---

## 4. What to test — the repaint question

The one open question is whether GTK repaints reliably on this no-compositor
stack. Under the QEMU/TCG emulator it was flaky (things map but stay blank
until an input/expose nudge); the hypothesis is that this is an emulation
artifact and real hardware paints normally. Check three things:

**a. Panel dropdown menus**
1. Click the **snail logo** (top-left) → the About / Sleep / Restart / Shut
   Down menu should drop down and paint.
2. Click **"Finder"** (bold app name) → the app switcher should drop down.

**b. Launching apps from the Finder**
Double-click any `.app` in the Finder (e.g. Calculator, Tasks, Media Viewer).
The Finder hides and the app should fill the screen, fully painted. Try
several. (Apps run fine — the only question is whether they paint on launch.)

**c. Interact**
Calculator: press keys, confirm it computes. Writer/Tasks: type. 2048/Tetris:
arrow keys. This confirms input + live redraw.

**If (a)/(b) paint reliably, the whole design is validated and the emulator
flakiness was the expected TCG artifact — which is the most likely outcome.**

The repaint issue was diagnosed exhaustively under emulation (see below); the
short version: it is a **GTK3 frame-clock timing failure**, and it is expected
to disappear on real hardware. What was proven under QEMU/TCG, so you know what
was ruled out:

- Instrumentation shows GTK `realize`/`map` fire but the frame clock never
  schedules the first paint. **strace** confirms the X server *does* deliver
  events to the client (92 socket reads) — so it is NOT an event-delivery
  problem — yet there are zero ~16 ms frame-scheduling waits. The refresh rate
  is a sane 60 Hz. The conclusion: `GdkFrameClockIdle`'s next-frame *timing
  math* never fires, almost certainly because of TCG's non-real-time monotonic
  clock. Real hardware has a real monotonic clock, so the math should work.
- A **compositor does NOT fix it**: `xcompmgr` and a full **picom** build (v10.2,
  packaged in-tree) were both tried; with picom the display is composited
  (`is_composited()` → true) but a draw-count probe still shows zero paints. So
  the "with compositor" GRUB entry is kept only as a lever, not a known fix.
- Also ruled out: `GDK_SYNCHRONIZE`, disabling the X Present extension,
  `process_updates`, manual frame-clock phase requests, window nudges
  (resize/raise/move/hide-show), obscuring, and injected pointer clicks.

So: **the real-hardware boot is the definitive test.** If the desktop and menus
paint there, the design is done. If they are *also* blank on real hardware, the
issue is deeper than TCG timing and we would pursue GPU-accelerated Mesa (this
build uses software rendering by design) — capture the serial log in that case.

### What's in this build (all logic-verified headlessly on the guest)

The desktop grew well past the original 21 apps. Everything below is validated
by headless self-tests (`tools/*_selftest.py`) that drive the real code and
check state directly — independent of the paint question.

**Three new system apps** (the core "accessible Linux" tools):

- **Terminal** — a real VTE terminal running **bash** (`/usr/bin/bash`;
  `/bin/sh` stays busybox for system scripts). papertone-styled.
- **Settings / Control Center** — Display resolution (xrandr), Sound volume
  (amixer, degrades gracefully with no audio device), Date & Time, Keyboard
  layout, Power (shut down / restart / screen-off), and a live **About** page
  from `/proc` (kernel, memory, storage, uptime).
- **System Monitor** — live CPU + memory gauges and a sortable process table
  with **End Process**, all read from `/proc`.

All three are on the Finder's Applications list, the panel app-switcher, and the
snail-logo system menu.

**The Finder is a full file manager** (validated across five self-test suites):

- **Back / Forward** history, live **Search**, **column sorting** (Name / Size /
  Date, folders grouped first), and a **Hidden files** toggle.
- **File operations**: New Folder, Copy / Cut / Paste (with " copy" collision
  handling and recursive directory copy).
- **Whole-filesystem browsing** — the "Local Disk" device opens `/`; the
  **Devices** sidebar lists real mounted volumes from `/proc/mounts`.
- **Trash** with **Put Back** (restores an item to where it came from) and
  **Empty Trash**.
- **USB auto-mount** — inserting a USB stick mounts it to `/media/<dev>` (udev
  rule + `automount.sh`) and it appears under Devices; removal unmounts it.

**All 21 original apps are now genuinely interactive** (not mockups) — every
dead control and empty shell surfaced by a structured audit has been wired and
logic-verified. Highlights of the final completion pass: **Messages** went from
a static mockup to a working messenger (composer + live LoRa byte-budget +
in-memory conversations); **Contacts** got New-Contact / editable fields / a
working Message button; **Screenplay**'s Element bar now applies real script
paragraph layout; **Academic Notes** cycles paragraph styles; **E-book**'s
font-size buttons resize the reading pane; **Music** has working playlists and
"Open Music Folder"; plus earlier fixes to Tasks (a stdlib `calendar` shadow)
and the Writer/Journal format bars. Each fix is covered by a headless self-test
under `tools/*_selftest.py`. (The shared File/Edit/View menu *bars* are still
decorative — apps are driven by their toolbars.) Screen blanking is disabled.

### Input (cursor)

A new `/etc/X11/xorg.conf.d/60-notebookos-input.conf` forces the **evdev** driver
for the pointer. This matters on real hardware / KVM: the QEMU `usb-tablet` is an
*absolute* pointer, and libinput emits no motion for such devices, which froze
the cursor once X took over. evdev drives it correctly; the keyboard stays on
libinput.

---

## Debugging (if something goes wrong)

- **GRUB menu:** hold the boot so you see it (or raise the timeout by rebuilding
  with `NB_GRUB_TIMEOUT=5 tools/mkimage-uefi.sh`). The 2nd entry, **"Notebook OS
  (verbose + serial console)"**, adds `console=ttyS0,115200 loglevel=7` — attach
  a serial cable / use the machine's serial port to capture the full boot log.
- **Black screen / no display:** at the GRUB menu press `e`, and to the `linux`
  line add `drm.debug=0x1e` (or try removing `simpledrm` assumptions). If
  simpledrm doesn't bind, the firmware GOP framebuffer may be unusual — capture
  the serial log and we can adjust.
- **Won't boot at all:** confirm Secure Boot is off and the firmware is in UEFI
  (not legacy-only) mode; some firmwares need the stick added as a manual boot
  entry pointing at `\EFI\BOOT\BOOTX64.EFI`.
- **Root not found:** the rootfs is found by `PARTUUID=b8e5a5f2-1a2b-4c3d-9e8f-
  000000000042`; if you re-partitioned the stick this breaks — re-flash the
  whole image with `dd`.

## What was validated in emulation (so you know the chain is sound)

Under QEMU + OVMF (UEFI firmware), end to end: firmware → USB → GRUB → kernel →
`root=PARTUUID` on the GPT partition → Buildroot userspace → GTK desktop. The
new kernel (simpledrm + NVMe added) boots regression-free. The only thing that
*didn't* reproduce in emulation is GRUB's countdown timer (it stalls under
TCG); real firmware timers work normally.
