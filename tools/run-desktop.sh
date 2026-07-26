#!/bin/bash
# Boot Notebook OS in QEMU: our no-internet desktop kernel + the Buildroot
# rootfs, with virtio-gpu (DRM/KMS) for the display and PS/2 + USB tablet for
# input. Boots straight into the native GTK desktop.
#
#   run-desktop.sh              interactive (GTK window on the host X)
#   run-desktop.sh --headless   no window; QMP socket for screenshots
#
# Set NB_KVM=1 to use hardware virtualization (needs /dev/kvm). This runs the
# SAME virtio-gpu/swrast image but at real CPU speed instead of TCG emulation
# — the quickest way to check whether the panel-menu repaint flakiness is a
# TCG-timing artifact (hypothesis: under KVM the menu paints reliably).
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KERNEL="$ROOT/kbuild-desktop/arch/x86/boot/bzImage"
ROOTFS="$ROOT/buildroot/output/images/rootfs.ext4"
WORK="$ROOT/boot-work"; mkdir -p "$WORK"
[ -f "$KERNEL" ] || { echo "missing desktop kernel: $KERNEL"; exit 2; }
[ -f "$ROOTFS" ] || { echo "missing rootfs: $ROOTFS"; exit 2; }

# writable snapshot so repeat boots start clean
cp -f "$ROOTFS" "$WORK/rootfs.ext4"

# accel=kvm:tcg tries KVM (fast, real clock) and falls back to software TCG if
# /dev/kvm isn't usable — instead of hard-failing the way -enable-kvm does. The
# colon fallback list is ONLY valid on -machine accel=..., not on -accel. TCG is
# slow but always works. NB_KVM=0 forces TCG. -smp 1 is safe under both (TCG can
# hang with -smp 2).
if [ "${NB_KVM:-1}" = "1" ]; then
  MACHINE="pc,accel=kvm:tcg"
else
  MACHINE="pc,accel=tcg"
fi
ACCEL=(-smp 1)

# GPU: virgl (GPU-accelerated 3D via the host GPU + virglrenderer) gives the
# guest real vblank/frame timing and offloads rendering off the CPU — the fix
# for both the sluggishness and the frame-clock repaint stall. It needs a host
# render node; auto-enable when one exists, override with NB_GL=0/1. swrast is
# the fallback for GPU-less hosts.
NB_GL="${NB_GL:-$([ -e /dev/dri/renderD128 ] && echo 1 || echo 0)}"
if [ "$NB_GL" = "1" ]; then
  GPU=(-device virtio-gpu-gl-pci)
  HEADLESS_DISPLAY=(-display egl-headless)
  WINDOW_DISPLAY=(-display "gtk,gl=on")
  echo "run-desktop: GPU acceleration on (virtio-gpu-gl / virgl)"
else
  GPU=(-device virtio-gpu-pci)
  HEADLESS_DISPLAY=(-display none)
  WINDOW_DISPLAY=(-display gtk)
  echo "run-desktop: software rendering (swrast)"
fi

COMMON=(
  -M "$MACHINE" -m 2048 -no-reboot -vga none
  "${ACCEL[@]}"
  -rtc base=localtime
  -kernel "$KERNEL"
  -drive "file=$WORK/rootfs.ext4,if=virtio,format=raw"
  -append "root=/dev/vda rw console=tty1 console=ttyS0 loglevel=5 random.trust_cpu=on ${NB_XAPPEND:-}"
  "${GPU[@]}"
  -device virtio-rng-pci
  -audiodev none,id=snd0
  -device intel-hda
  -device hda-output,audiodev=snd0
  -device usb-ehci -device usb-tablet -device usb-kbd
  -serial "file:$WORK/serial.log"
  -serial "unix:$WORK/ttyS1.sock,server=on,wait=off"
)

if [ "${1:-}" = "--headless" ]; then
  exec qemu-system-x86_64 "${COMMON[@]}" \
    "${HEADLESS_DISPLAY[@]}" \
    -qmp "unix:$WORK/qmp.sock,server,nowait"
else
  exec qemu-system-x86_64 "${COMMON[@]}" "${WINDOW_DISPLAY[@]}"
fi
