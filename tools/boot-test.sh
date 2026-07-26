#!/bin/bash
# Boot the (no-internet) kernel under QEMU with a probe initramfs and
# verify which socket families exist. No root required.
#
# usage: boot-test.sh [path-to-bzImage]
#   PASS = kernel boots, AF_BLUETOOTH works, no other family does
#   (on an unpurged kernel this prints the probe table and FAILs, which
#    is useful as a baseline)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
LINUX=$ROOT/linux
BUILD=$ROOT/kbuild
BZIMAGE=${1:-$BUILD/arch/x86/boot/bzImage}
WORK=$ROOT/boot-work
LOG=$WORK/serial.log

[ -f "$BZIMAGE" ] || { echo "no bzImage at $BZIMAGE"; exit 2; }
mkdir -p "$WORK"

# static init + probe
gcc -static -Os -o "$WORK/init" "$HERE/sockprobe-init.c"

# gen_init_cpio from the kernel tree creates device nodes without root
gcc -O2 -o "$WORK/gen_init_cpio" "$LINUX/usr/gen_init_cpio.c"
cat > "$WORK/initramfs.list" <<EOF
dir /dev 0755 0 0
nod /dev/console 0600 0 0 c 5 1
file /init $WORK/init 0755 0 0
EOF
"$WORK/gen_init_cpio" "$WORK/initramfs.list" | gzip -9 > "$WORK/initramfs.cpio.gz"

timeout 300 qemu-system-x86_64 \
    -M pc -m 512 -no-reboot -nographic \
    -kernel "$BZIMAGE" \
    -initrd "$WORK/initramfs.cpio.gz" \
    -append "console=ttyS0 panic=-1 quiet loglevel=4" \
    </dev/null | tee "$LOG" || true

echo
if grep -q 'SOCKPROBE-PASS' "$LOG"; then
    echo "BOOT-TEST: PASS"
elif grep -q 'SOCKPROBE-FAIL' "$LOG"; then
    echo "BOOT-TEST: probe ran but family set is wrong (see table above)"
    exit 1
else
    echo "BOOT-TEST: kernel never reached the probe (boot failure?)"
    exit 1
fi
