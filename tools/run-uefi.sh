#!/bin/bash
# Boot the assembled UEFI disk image (boot-work/notebookos-uefi.img) under
# QEMU + OVMF, to validate the real-hardware boot chain end to end:
#   UEFI firmware -> GRUB (/EFI/BOOT/BOOTX64.EFI) -> kernel -> root=PARTUUID
#   -> Buildroot userspace -> GTK desktop.
# The image is attached as a USB mass-storage device, exactly as a real USB
# stick would appear, so the USB-storage + rootwait + PARTUUID path is tested.
#
#   run-uefi.sh              interactive (GTK window)
#   run-uefi.sh --headless   QMP socket for screenshots
#   DISPLAY_MODE=simpledrm run-uefi.sh   use std VGA (exercises simpledrm,
#                                        the driverless real-HW display path)
#   DISPLAY_MODE=virtio (default)        virtio-gpu KMS
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMG="$ROOT/boot-work/notebookos-uefi.img"
WORK="$ROOT/boot-work"; mkdir -p "$WORK"
[ -f "$IMG" ] || { echo "missing image: $IMG (run tools/mkimage-uefi.sh)"; exit 2; }

OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS_SRC=/usr/share/OVMF/OVMF_VARS_4M.fd
cp -f "$OVMF_VARS_SRC" "$WORK/OVMF_VARS.fd"      # writable per-run copy

# writable image snapshot so repeat boots start clean
cp -f "$IMG" "$WORK/uefi-disk.img"

# Hardware virtualization. -machine accel=kvm:tcg TRIES KVM and falls back to
# software TCG automatically if the KVM module isn't usable — unlike -enable-kvm,
# which hard-fails and won't boot. (The colon fallback list is only valid on
# -machine accel=..., not on -accel.) KVM is dramatically faster (real CPU + real
# clock, which also fixes the repaint stall); TCG works everywhere but is slow.
# Force software with NB_KVM=0.
NB_KVM="${NB_KVM:-1}"
if [ "$NB_KVM" = "1" ]; then
  MACHACCEL="kvm:tcg"
  echo "run-uefi: trying KVM, falling back to TCG if unavailable"
else
  MACHACCEL="tcg"
  echo "run-uefi: software TCG (forced) — slow"
fi
ACCEL=(-smp 1)

# GPU: virtio-gpu-gl (virgl) offloads rendering to the host GPU. Default ON when
# a host render node exists — this removes the slow softpipe software renderer.
# NB_GL=0 forces software virtio-gpu. simpledrm mode is the driverless real-HW
# framebuffer path (software, no GL) and is only for that specific test.
DISPLAY_MODE="${DISPLAY_MODE:-virtio}"
NB_GL="${NB_GL:-$([ -e /dev/dri/renderD128 ] && echo 1 || echo 0)}"
if [ "$DISPLAY_MODE" = "simpledrm" ]; then
  GPU=(-vga std); NB_GL=0
  HEADLESS_DISPLAY=(-display none); WINDOW_DISPLAY=(-display gtk)
elif [ "$NB_GL" = "1" ]; then
  GPU=(-vga none -device virtio-gpu-gl-pci)
  HEADLESS_DISPLAY=(-display egl-headless); WINDOW_DISPLAY=(-display "gtk,gl=on")
  echo "run-uefi: GPU acceleration ON (virtio-gpu-gl / virgl -> host GPU)"
else
  GPU=(-vga none -device virtio-gpu-pci)
  HEADLESS_DISPLAY=(-display none); WINDOW_DISPLAY=(-display gtk)
fi

COMMON=(
  -M "q35,accel=$MACHACCEL" -m 2048 -no-reboot
  "${ACCEL[@]}"
  -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
  -drive "if=pflash,format=raw,unit=1,file=$WORK/OVMF_VARS.fd"
  -drive "if=none,id=usbstick,format=raw,file=$WORK/uefi-disk.img"
  -device qemu-xhci -device usb-storage,drive=usbstick,bootindex=0
  -device usb-tablet -device usb-kbd
  -rtc base=localtime
  "${GPU[@]}"
  -serial "file:$WORK/serial.log"
  -serial "unix:$WORK/ttyS1.sock,server=on,wait=off"
)

if [ "${1:-}" = "--headless" ]; then
  exec qemu-system-x86_64 "${COMMON[@]}" \
    "${HEADLESS_DISPLAY[@]}" -qmp "unix:$WORK/qmp.sock,server,nowait"
else
  exec qemu-system-x86_64 "${COMMON[@]}" "${WINDOW_DISPLAY[@]}"
fi
