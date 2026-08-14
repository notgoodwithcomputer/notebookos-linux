# USB graphics tablets

A pen tablet is the input device this OS most obviously wants: it ships a pixel
editor, a comics tool and a zine printer, and until now every one of them was
driven by a mouse. This document is what the OS provides for a plugged-in
drawing tablet, what it deliberately does not, and how to check the chain on
real hardware.

Everything here is proved by `tools/graphics_tablet_selftest.py` — 36 checks,
6 of them mutants that must go red.

## What works

Plug in a USB tablet and draw. The pen moves the cursor, pressure varies the
brush width in Illustrator, and turning the pen over erases.

| you plug in | the driver that binds it |
|-------------|--------------------------|
| Wacom — Intuos, Intuos Pro, One, Bamboo, Cintiq, the pen displays | `wacom` |
| Huion, XP-Pen, UGEE, Gaomon, Parblo, Veikk | `hid-uclogic` |
| Genius | `hid-kye` |
| Waltop-OEM boards (many unbranded tablets) | `hid-waltop` |
| ViewSonic / Signotec pen displays | `hid-viewsonic` |

All five are `=m` and autoload from MODALIAS when the device appears, so none of
them is resident on a machine that never sees a tablet — the attack-surface rule
in `docs/SECURITY-MODEL.md` applies to input drivers like anything else.

`hid-uclogic` is the load-bearing one after Wacom. UC-Logic silicon sits inside
most of the budget market, and those tablets enumerate in a **fallback "mouse"
descriptor** until a driver switches them into their real digitiser mode: an
absolute pointer, a fraction of the true resolution, and no pressure axis at
all. Without the module nothing in userspace can even tell a pen was attached.

## The chain, link by link

Each link is separately checkable, and a break in any one of them looks
identical from the user's chair — "the tablet doesn't work".

1. **Kernel** — `CONFIG_HID_WACOM=m`, `CONFIG_HID_UCLOGIC=m`, `CONFIG_HID_KYE=m`,
   `CONFIG_HID_WALTOP=m`, `CONFIG_HID_VIEWSONIC=m` in `tools/desktop.config`
   (the tracked seed) and in `kbuild-desktop/.config` (the live build). The gate
   asserts the two agree: a seed and a build config that disagree is the F4
   two-sources-of-truth defect in the security doc.
2. **udev** — eudev's `input_id` builtin sees `BTN_TOOL_PEN` and tags the device
   `ID_INPUT_TABLET`. It does *not* also tag it `ID_INPUT_MOUSE`; the mouse
   branch is guarded by `!is_tablet`.
3. **X** — the tablet goes to the **libinput** driver, which speaks the XI2
   tablet-tool protocol: pressure, tilt, and the pen-versus-eraser identity.
   evdev would carry position and lose all three.
4. **GTK** — GDK turns those valuators into `Gdk.AxisUse.PRESSURE` on the motion
   event and reports the tool as `Gdk.InputSource.PEN` / `.ERASER`.
5. **Illustrator** — reads both. See below.

### Why the X layer needs saying out loud

`/etc/X11/xorg.conf.d/60-notebookos-input.conf` forces the **evdev** driver for
absolute pointers, because libinput emits no motion for them and the cursor
freezes the moment the GUI comes up. That rule is right and must stay.

A pen must not be caught by it. Today it isn't — a stylus is tagged
`ID_INPUT_TABLET` and not `ID_INPUT_MOUSE`, so `MatchIsPointer` never fires and
`40-libinput.conf`'s tablet catchall wins. But that is an invariant living in
someone else's source tree, so the OS now names tablets explicitly in its own
file and the gate replays xorg's "last InputClass naming a Driver wins" against
a modelled device to prove the outcome.

One consequence worth keeping: **a tablet with no kernel driver still degrades
to a working cursor.** `hid-generic` gives it absolute axes and mouse buttons
with no `BTN_TOOL_PEN`, so it is tagged a plain pointer and lands on evdev — a
usable pointing device instead of nothing at all.

## Pressure in Illustrator

Pressure drives **width**, not opacity, and that is not a shortcut. The pixel
engine writes exact byte values and never blends — the hard edge is the whole
point of it, and a translucent mark is not something it can express. Width is
also the honest analogue: pressing a pencil harder broadens the mark.

- The brush size you choose is the **ceiling**, reached at a firm press.
- The floor is 1 px, so a feathered stroke tapers to a hairline rather than
  vanishing and leaving a gap in the line.
- A stylus at rest still reports a little pressure and a hand at full lean
  rarely reaches 1.0, so the useful band is 0.04–0.85 (`PEN_FLOOR`/`PEN_CEIL`).
  Without that, a normal drawing hand only ever reaches two-thirds width.
- The **eraser end** erases whatever freehand tool is selected — that is what
  turning the pen over means. The shape tools keep their own behaviour, so
  flipping the pen never silently reinterprets "drag a rectangle".
- Lifting the pen drops pressure to ~0. That reading is *not* used for the
  final segment, or every stroke would end in a hairline the hand never drew.
- A **mouse is untouched by all of this**, and so is a touchscreen: the device
  source gates the behaviour, not the presence of a pressure axis. A fingertip
  reports pressure too, and scaling by it would make touch strokes wander
  between widths for no reason the hand can see.

Only Illustrator reads pressure. Comics and the other canvas tools use their own
paths and still draw at a fixed width.

## What is NOT supported

Stated plainly, because a half-supported device is worse than a documented one.

- **Express keys, the touch ring and the touch strip do nothing.** This is a
  tagging gap, not an Illustrator gap. eudev 3.2.14's `input_id` has no
  `ID_INPUT_TABLET_PAD` concept at all: a pad node with a ring exposes
  `ABS_WHEEL`, which falls inside the axis range the builtin scans for a
  joystick, so the pad is tagged `ID_INPUT_JOYSTICK` — and both catchall config
  files exclude joysticks, and `xf86-input-joystick` is not built. A pad with no
  ring gets no `ID_INPUT` tag at all, and xorg drops untagged devices outright.
  Fixing it means a udev rule that tags pads correctly, an InputClass with
  `MatchIsTabletPad`, and `Gtk.PadController` in the app — worth doing, but not
  worth guessing at without a tablet on the desk.
- **No tablet settings page.** Aspect-ratio mapping, area cropping, mapping to
  one monitor, button remapping and a pressure curve all have to be built.
  Illustrator's `PEN_FLOOR`/`PEN_CEIL` are constants, not preferences.
- **Bluetooth tablets do not work.** `CONFIG_BT` was removed entirely in 2026-08
  (F5 in the security doc). Wacom's wireless models are USB-only here.
- **`libinput` is built without `libwacom`** (`HAVE_LIBWACOM 0`). Pressure and
  tilt are unaffected — the tool falls back to copying the axes the tablet
  itself reports. What is lost is the per-stylus database (a tool may claim an
  axis its physical pen lacks) and pad LED/mode groups, which are moot while
  pads are unreachable anyway.
- **Pressure is per-segment, not interpolated.** Each motion sample paints at
  one width. GTK's event compression also thins the sample stream. Strokes look
  right; they are not a smoothed spline.
- Newer UC-Logic models occasionally need a quirk that only exists upstream. If
  a recent Huion or XP-Pen enumerates but has no pressure, that is the likely
  cause — check `hid-uclogic` bound it at all before suspecting anything here.

## Checking it on real hardware

Emulation cannot prove this layer: QEMU's `usb-tablet` is an absolute *pointer*,
not a pen digitiser, so it exercises the evdev path and never the tablet one.
The gate covers the config, the routing logic and the app; the rest needs the
device.

With a tablet plugged in:

```sh
# 1. the driver bound it
dmesg | grep -iE 'wacom|uclogic|hid-generic'
ls /sys/bus/hid/drivers/wacom/          # or .../hid-uclogic/

# 2. udev tagged it a tablet, NOT a mouse
udevadm info /dev/input/eventN | grep ID_INPUT

# 3. X gave it to libinput and it has a pressure valuator
grep -iE 'tablet|libinput' /var/log/Xorg.0.log
xinput list                             # the pen is its own device
xinput list-props <pen-id> | grep -i pressure
```

If step 1 shows `hid-generic`, the tablet has no driver in this build: it will
still move the cursor, and it will have no pressure. If step 2 shows
`ID_INPUT_MOUSE`, the device is in its fallback descriptor — for a UC-Logic
tablet that means `hid-uclogic` did not bind.

Then draw: pick the brush, set the size to something visible like 16, and vary
the pressure across one stroke. The mark should thicken and thin with the hand.

## Files

| path | what it holds |
|------|---------------|
| `tools/desktop.config` | the five `=m` drivers, and the legacy tablet drivers left off on purpose |
| `buildroot/board/notebookos/rootfs-overlay/etc/X11/xorg.conf.d/60-notebookos-input.conf` | tablets → libinput, absolute pointers → evdev |
| `de/illustrator.py` | `pen_size()`, `Illustrator._pen()`, and the size threaded through `_stroke_seg` → `_paint_ops` → `_stamp_on` |
| `tools/graphics_tablet_selftest.py` | the gate over all of it |
