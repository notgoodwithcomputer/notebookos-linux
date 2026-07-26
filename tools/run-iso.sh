#!/bin/bash
# Boot the Notebook OS Live ISO under QEMU + OVMF, to validate the full live
# boot chain end to end:
#   UEFI firmware -> GRUB (El Torito) -> kernel + live-init initramfs ->
#   find medium -> squashfs -> overlay root -> switch_root -> GTK desktop.
# A blank scratch disk is attached as /dev/vda so the guided installer has a
# real target to partition/format/install onto.
#
#   run-iso.sh                 interactive (GTK window), boots the ISO
#   run-iso.sh --headless      QMP socket for screenshots
#   run-iso.sh --boot-installed  boot the scratch disk instead (test the
#                              system the installer wrote), no ISO
#   NB_KVM=0  force software TCG   ·   NB_GL=0  force software virtio-gpu
#   SCRATCH_GB=8  size of the install target disk
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK="$ROOT/boot-work"; mkdir -p "$WORK"
VER="${NB_VERSION:-1.0}"
ISO="${ISO:-$ROOT/release/notebookos-${VER}.iso}"
SCRATCH="${SCRATCH:-$WORK/install-target.img}"
SCRATCH_GB="${SCRATCH_GB:-8}"

MODE="iso"
HEADLESS=0
for a in "$@"; do case "$a" in
  --headless)       HEADLESS=1 ;;
  --boot-installed) MODE="installed" ;;
  *) echo "unknown arg: $a" >&2; exit 2 ;;
esac; done

OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS_SRC=/usr/share/OVMF/OVMF_VARS_4M.fd
cp -f "$OVMF_VARS_SRC" "$WORK/OVMF_VARS.iso.fd"

# scratch target disk (persists across runs so an install can be booted back)
if [ ! -f "$SCRATCH" ]; then
  echo "run-iso: creating blank ${SCRATCH_GB}G install target $SCRATCH"
  qemu-img create -f raw "$SCRATCH" "${SCRATCH_GB}G" >/dev/null
fi

NB_KVM="${NB_KVM:-1}"
if [ "$NB_KVM" = "1" ]; then MACHACCEL="kvm:tcg"; else MACHACCEL="tcg"; fi

NB_GL="${NB_GL:-$([ -e /dev/dri/renderD128 ] && echo 1 || echo 0)}"
DISPLAY_MODE="${DISPLAY_MODE:-virtio}"
if [ "$DISPLAY_MODE" = "simpledrm" ]; then
  # std VGA -> the guest drives the EFI/VESA linear framebuffer via simpledrm,
  # exactly like real hardware with no native GPU KMS driver. That framebuffer
  # is scanned out continuously (no virtio-gpu lazy dirty-fb), so this is the
  # honest "will it paint on a real laptop" test. Software rendering only.
  GPU=(-vga std); NB_GL=0
  HEADLESS_DISPLAY=(-display none); WINDOW_DISPLAY=(-display gtk)
  echo "run-iso: simpledrm (std VGA framebuffer — real-HW-like)"
elif [ "$NB_GL" = "1" ]; then
  GPU=(-vga none -device virtio-gpu-gl-pci)
  HEADLESS_DISPLAY=(-display egl-headless); WINDOW_DISPLAY=(-display "gtk,gl=on")
  echo "run-iso: GPU acceleration ON (virgl)"
else
  GPU=(-vga none -device virtio-gpu-pci)
  HEADLESS_DISPLAY=(-display none); WINDOW_DISPLAY=(-display gtk)
  echo "run-iso: software virtio-gpu"
fi

# media/boot devices differ per mode
if [ "$MODE" = "installed" ]; then
  echo "run-iso: booting the INSTALLED system from $SCRATCH (no ISO)"
  BOOTDEV=(
    -drive "file=$SCRATCH,if=none,id=hd0,format=raw"
    -device "virtio-blk-pci,drive=hd0,bootindex=0"
  )
else
  [ -f "$ISO" ] || { echo "missing ISO: $ISO (build it: tools/mkrelease.sh)"; exit 2; }
  echo "run-iso: booting Live ISO $ISO  (+ blank target /dev/vda)"
  BOOTDEV=(
    -drive "file=$ISO,if=none,id=iso0,format=raw,media=cdrom,readonly=on"
    -device "ide-cd,drive=iso0,bootindex=0"
    -drive "file=$SCRATCH,if=none,id=hd0,format=raw"
    -device "virtio-blk-pci,drive=hd0"
  )
fi

COMMON=(
  -M "q35,accel=$MACHACCEL" -m 2048 -smp 2 -no-reboot
  -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
  -drive "if=pflash,format=raw,unit=1,file=$WORK/OVMF_VARS.iso.fd"
  "${BOOTDEV[@]}"
  -device qemu-xhci -device usb-tablet -device usb-kbd
  -audiodev none,id=snd0 -device intel-hda -device hda-output,audiodev=snd0
  -rtc base=localtime
  "${GPU[@]}"
  -serial "file:$WORK/serial.log"
  -serial "unix:$WORK/ttyS1.sock,server=on,wait=off"
)

if [ "$HEADLESS" = "1" ]; then
  exec qemu-system-x86_64 "${COMMON[@]}" \
    "${HEADLESS_DISPLAY[@]}" -qmp "unix:$WORK/qmp.sock,server,nowait"
else
  exec qemu-system-x86_64 "${COMMON[@]}" "${WINDOW_DISPLAY[@]}"
fi
