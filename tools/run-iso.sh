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
#   run-iso.sh --debug-shell   same live ISO, but booted with `nbdebug` so the
#                              serial debug shell runs and tools/gsh.py works
#
# WHY --debug-shell EXISTS. /opt/notebook/debugshell.sh only starts a shell
# when /proc/cmdline carries `nbdebug`; otherwise it sleeps, deliberately, so
# an image somebody owns does not hand a root shell to anyone with a serial
# cable. That gate is right, but it silently blocks every on-target check that
# needs to place a file on the guest — and a shell-less tty still ECHOES, so
# gsh appears to HANG rather than refuse. The ISO's cmdline lives in its own
# grub.cfg and cannot be appended to from the QEMU side, so this mode boots
# the kernel and initrd OUT of the ISO directly (-kernel/-initrd/-append) with
# the ISO still attached as the live medium the initrd goes looking for.
#   NB_KVM=0  force software TCG   ·   NB_GL=0  force software virtio-gpu
#   SCRATCH_GB=8  size of the install target disk
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
# NB_WORK gives this run a PRIVATE work dir, the way run-desktop.sh already
# does. boot-work/ is otherwise a shared battleground: the OVMF vars, the
# serial log, the ttyS1 socket and the extracted directboot kernel all live
# there under fixed names, so a second lane booting a guest silently overwrites
# the first one's — and a Live-ISO GRUB menu turning up in "your" serial.log is
# what that looks like from the outside.
#
# Two constraints on the path, both learned the hard way: keep it SHORT (QEMU's
# UNIX sockets hit the kernel's 108-byte limit, which a session scratchpad path
# blows past and only reports at launch), and pass NB_GL=0 with it (screendump
# returns "no surface" forever on the GL path). /tmp/<label> is the right shape.
WORK="${NB_WORK:-$ROOT/boot-work}"; mkdir -p "$WORK"
VER="${NB_VERSION:-1.0}"
ISO="${ISO:-$ROOT/release/notebookos-${VER}.iso}"
SCRATCH="${SCRATCH:-$WORK/install-target.img}"
SCRATCH_GB="${SCRATCH_GB:-8}"

MODE="iso"
HEADLESS=0
DEBUGSHELL=0
# -kernel takes bootindex 0 implicitly, so the CD must not also claim it.
CD_BOOTIDX=",bootindex=0"
for a in "$@"; do case "$a" in
  --headless)       HEADLESS=1 ;;
  --boot-installed) MODE="installed" ;;
  --debug-shell)    DEBUGSHELL=1; CD_BOOTIDX="" ;;
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
    -device "ide-cd,drive=iso0${CD_BOOTIDX}"
    -drive "file=$SCRATCH,if=none,id=hd0,format=raw"
    -device "virtio-blk-pci,drive=hd0"
  )
fi

# --debug-shell: boot the ISO's OWN kernel with `nbdebug` appended. The cmdline
# below is copied from the ISO's grub.cfg live entry; if that entry ever
# changes, this must follow it, so it is read from the ISO rather than guessed.
DIRECTBOOT=()
if [ "$DEBUGSHELL" = "1" ]; then
  [ "$MODE" = "iso" ] || { echo "--debug-shell needs the live ISO" >&2; exit 2; }
  command -v 7z >/dev/null || { echo "--debug-shell needs 7z to read the ISO" >&2; exit 2; }
  KDIR="$WORK/directboot"
  rm -rf "$KDIR"; mkdir -p "$KDIR"
  7z e -y -o"$KDIR" "$ISO" live/bzImage live/initrd.img >/dev/null 2>&1
  7z e -y -o"$KDIR" "$ISO" boot/grub/grub.cfg >/dev/null 2>&1
  [ -f "$KDIR/bzImage" ] && [ -f "$KDIR/initrd.img" ] || {
    echo "--debug-shell: could not extract live/bzImage + live/initrd.img from $ISO" >&2
    exit 2; }
  # Take the FIRST live entry's arguments verbatim, then add nbdebug. Guessing
  # them would be the kind of near-miss that boots to an initrd prompt and
  # looks like a broken image.
  CMDLINE=$(sed -n 's|^ *linux /live/bzImage *||p' "$KDIR/grub.cfg" | head -1)
  [ -n "$CMDLINE" ] || CMDLINE="boot=live nb.live=1 console=tty1 loglevel=5"
  echo "run-iso: --debug-shell, cmdline from the ISO: $CMDLINE nbdebug"
  DIRECTBOOT=(
    -kernel "$KDIR/bzImage" -initrd "$KDIR/initrd.img"
    -append "$CMDLINE nbdebug"
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
  "${DIRECTBOOT[@]}"
)

if [ "$HEADLESS" = "1" ]; then
  exec qemu-system-x86_64 "${COMMON[@]}" \
    "${HEADLESS_DISPLAY[@]}" -qmp "unix:$WORK/qmp.sock,server,nowait"
else
  exec qemu-system-x86_64 "${COMMON[@]}" "${WINDOW_DISPLAY[@]}"
fi
