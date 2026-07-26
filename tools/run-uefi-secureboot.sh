#!/bin/bash
# Boot the assembled UEFI image under QEMU + OVMF with **Secure Boot ENFORCED**
# (Microsoft keys pre-enrolled), to validate the shim -> grub -> kernel chain.
#
#   OVMF_CODE_4M.secboot.fd  Secure-Boot-enforcing firmware
#   OVMF_VARS_4M.ms.fd       PK/KEK/db pre-loaded with Microsoft's UEFI keys
#
# With no MOK enrolled, the expected result is: shim loads (MS-signed, trusted),
# Debian's signed grub loads (trusted via the Debian CA shim embeds), our menu
# appears, and the kernel is REJECTED at launch (its MOK isn't enrolled yet) --
# proving Secure Boot is active and enforced end to end. On real hardware the
# user enrolls MOK.cer once via MokManager and the kernel then boots.
#
#   run-uefi-secureboot.sh [seconds]     boot headless, screenshot after N s (default 40)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMG="$ROOT/boot-work/notebookos-uefi.img"
WORK="$ROOT/boot-work"; mkdir -p "$WORK"
[ -f "$IMG" ] || { echo "missing image: $IMG (run tools/mkimage-uefi.sh)"; exit 2; }

OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd
OVMF_VARS_SRC=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
cp -f "$OVMF_VARS_SRC" "$WORK/OVMF_VARS.sb.fd"
cp -f "$IMG" "$WORK/uefi-disk.sb.img"
SHOT_AT="${1:-40}"

# -vga std so the QMP screendump captures the shim/grub screen (no GL needed for
# a boot-chain text test).
QMP="$WORK/qmp-sb.sock"
rm -f "$QMP"
# Secure Boot on OVMF needs SMM (the secure pflash region is SMM-protected).
setsid qemu-system-x86_64 \
  -machine q35,smm=on,accel="${NB_KVM:+kvm:}tcg" -m 2048 -no-reboot -smp 1 \
  -global ICH9-LPC.disable_s3=1 \
  -global driver=cfi.pflash01,property=secure,value=on \
  -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
  -drive "if=pflash,format=raw,unit=1,file=$WORK/OVMF_VARS.sb.fd" \
  -drive "if=none,id=usbstick,format=raw,file=$WORK/uefi-disk.sb.img" \
  -device qemu-xhci -device usb-storage,drive=usbstick,bootindex=0 \
  -vga std -display none \
  -serial "file:$WORK/serial.sb.log" \
  -qmp "unix:$QMP,server=on,wait=off" \
  2>"$WORK/qemu-sb.err.log" &
QPID=$!
echo "qemu(secureboot) pid=$QPID  screenshot in ${SHOT_AT}s"
sleep "$SHOT_AT"

# QMP screendump (PPM) of wherever the chain got to
python3 - "$QMP" "$WORK/sb-screen.ppm" <<'PY' || true
import socket,sys,json,time
sock=sys.argv[1]; out=sys.argv[2]
s=socket.socket(socket.AF_UNIX); s.connect(sock); f=s.makefile("rw")
f.readline(); f.write(json.dumps({"execute":"qmp_capabilities"})+"\n"); f.flush(); f.readline()
f.write(json.dumps({"execute":"screendump","arguments":{"filename":out}})+"\n"); f.flush(); f.readline()
time.sleep(1); print("screenshot ->",out)
PY
kill -9 "$QPID" 2>/dev/null || true
echo "--- serial.sb.log (tail) ---"; tail -30 "$WORK/serial.sb.log" 2>/dev/null || true
