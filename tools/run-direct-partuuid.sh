#!/bin/bash
# Validation helper: direct-boot the kernel (QEMU -kernel, bypassing GRUB)
# against the PARTITIONED UEFI image, mounting root by PARTUUID — proving the
# kernel + rootfs + PARTUUID half of the real-hardware chain. Pairs with the
# OVMF->GRUB half proven by run-uefi.sh.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KERNEL="$ROOT/kbuild-desktop/arch/x86/boot/bzImage"
WORK="$ROOT/boot-work"; mkdir -p "$WORK"
PARTUUID="b8e5a5f2-1a2b-4c3d-9e8f-000000000042"
cp -f "$WORK/notebookos-uefi.img" "$WORK/direct-disk.img"
exec qemu-system-x86_64 -M pc -m 2048 -smp 1 -no-reboot -vga none -rtc base=localtime \
  -kernel "$KERNEL" \
  -drive "file=$WORK/direct-disk.img,if=virtio,format=raw" \
  -append "root=PARTUUID=$PARTUUID rw rootwait console=tty1 console=ttyS0 loglevel=5" \
  -device virtio-gpu-pci -device usb-ehci -device usb-tablet -device usb-kbd \
  -serial "file:$WORK/serial.log" \
  -serial "unix:$WORK/ttyS1.sock,server=on,wait=off" \
  -display none -qmp "unix:$WORK/qmp.sock,server,nowait"
