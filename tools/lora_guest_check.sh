#!/bin/bash
# lora_guest_check.sh — prove the LoRa dongle's USB-serial support inside the
# running guest.
#
# The Ebyte E22-900T22U presents as a USB-UART bridge (CH340 on some batches,
# CP2102 on others) in front of a serial LoRa modem. What the OS must supply,
# and what this gate asserts against a booted image, is the full chain that
# fires when the stick is plugged in:
#
#   USB uevent -> 80-drivers.rules -> udevd kmod builtin -> modules.alias
#   (1a86:7523 / 10c4:ea60) -> ch341.ko / cp210x.ko insert -> /dev/ttyUSB*
#   -> 99-notebook-serial.rules (dialout 0660, /dev/lora symlink)
#
# Every link the OS controls is checked live in the guest: the modules ship
# for the running kernel and INSERT into it, both bridge identities resolve
# through modules.alias, the autoload rule + kmod builtin are wired, and the
# udev rule ships with the /dev/lora contract. The one link the rig cannot
# supply is the uevent itself: QEMU's only USB-UART model (FTDI FT232) never
# completes cold-attach enumeration on this TCG setup — uhci root-hub polling
# misses connects that predate the driver, xhci's MSI-X vector never fires
# (0 interrupts; virtio MSI-X on the same guest works), and hot-adds behind
# QEMU's auto-inserted hub need the very interrupts that never arrive. That
# leg runs only with --with-ftdi (useful on a KVM host or a fixed QEMU); the
# authoritative end-to-end is the physical stick — recipe in
# docs/LORA-DONGLE.md.
#
# Usage: tools/lora_guest_check.sh                 boot, test, shut down
#        tools/lora_guest_check.sh --running       reuse this gate's guest
#        tools/lora_guest_check.sh --with-ftdi     also run the live-plug leg
#
# Two hard-won details of the guest wiring (see also gsh.py):
#   * the guest must boot with `nbdebug` or debugshell.sh never execs a
#     shell on ttyS1 — and a shell-less tty still ECHOES, so probes that
#     grep for their own text false-match. gsh's BEGIN/DONE markers plus the
#     nbdebug boot flag together make the transcript trustworthy.
#   * everything lives in a PRIVATE work dir (boot-work-lora/) with private
#     sockets, and teardown kills only the qemu whose command line references
#     that dir — two harnesses sharing boot-work/ shoot each other's guests.

set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$R/boot-work-lora"
export NB_TTYS1="$WORK/ttyS1.sock"
G="python3 $R/tools/gsh.py"
USBSOCK="$WORK/usbser.sock"
WITH_FTDI=0; RUNNING=0
for a in "$@"; do case "$a" in
    --with-ftdi) WITH_FTDI=1 ;;
    --running)   RUNNING=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
esac; done
fail=0; pass=0

ok()  { printf '  \033[32mOK\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }
kill_own_guest() { pkill -f "boot-work-lor[a]/" >/dev/null 2>&1; }

BOOTED=0
if [ "$RUNNING" != 1 ]; then
    hdr "booting guest (headless, TCG, emulated USB-serial adapter attached)"
    kill_own_guest
    mkdir -p "$WORK"
    rm -f "$NB_TTYS1" "$USBSOCK"
    NB_WORK="$WORK" NB_KVM=0 NB_XAPPEND="${NB_XAPPEND:-} nbdebug" \
    NB_QEMU_EXTRA="-usb -chardev socket,id=lora0,path=$USBSOCK,server=on,wait=off -device usb-serial,bus=usb-bus.0,chardev=lora0" \
        "$R/tools/run-desktop.sh" --headless >"$WORK/qemu.log" 2>&1 &
    BOOTED=1
    # generous budgets: TCG on a loaded host can take many minutes to boot
    for _ in $(seq 1 150); do [ -S "$NB_TTYS1" ] && break; sleep 2; done
    for _ in $(seq 1 100); do
        $G 'echo READY' 2>/dev/null | grep -q READY && break
        sleep 3
    done
    if ! $G 'echo READY' 2>/dev/null | grep -q READY; then
        echo "  guest never came up (see $WORK/qemu.log and $WORK/serial.log)"
        kill_own_guest
        exit 1
    fi
    echo "  guest shell up"
fi

hdr "1. drivers shipped for the running kernel"
MISS=$($G 'K=/lib/modules/$(uname -r)/kernel; for m in serial/usbserial serial/ch341 serial/cp210x serial/ftdi_sio serial/pl2303 class/cdc-acm; do [ -f $K/drivers/usb/$m.ko ] || echo ABSENT:$m; done' 2>/dev/null)
if echo "$MISS" | grep -q ABSENT; then
    bad "modules absent: $(echo "$MISS" | tr '\n' ' ')"
else
    ok "usbserial ch341 cp210x ftdi_sio pl2303 cdc-acm all present"
fi

hdr "2. plug-in autoload plumbing (uevent -> modprobe)"
$G 'grep -q "kmod load" /lib/udev/rules.d/80-drivers.rules' >/dev/null 2>&1 \
    && ok "80-drivers.rules wires MODALIAS -> kmod load" \
    || bad "80-drivers.rules missing — nothing autoloads drivers"
$G 'grep -qa kmod /sbin/udevd && [ -f /usr/lib/libkmod.so.2 ]' >/dev/null 2>&1 \
    && ok "udevd carries the kmod builtin (libkmod shipped)" \
    || bad "udevd/libkmod missing — kmod builtin unavailable"

hdr "3. the real dongle's identities resolve to the right drivers"
$G 'grep -qi v1A86p7523 /lib/modules/$(uname -r)/modules.alias' >/dev/null 2>&1 \
    && ok "CH340 1a86:7523 -> ch341 in modules.alias" \
    || bad "no modules.alias entry for CH340 — depmod stale?"
$G 'grep -qi v10C4pEA60 /lib/modules/$(uname -r)/modules.alias' >/dev/null 2>&1 \
    && ok "CP2102 10c4:ea60 -> cp210x in modules.alias" \
    || bad "no modules.alias entry for CP2102 — depmod stale?"
$G 'modprobe ch341 && modprobe cp210x && modprobe cdc_acm && lsmod | grep -q "^ch341" && lsmod | grep -q "^cp210x" && lsmod | grep -q "^cdc_acm"' 2>/dev/null \
    && ok "ch341 + cp210x + cdc_acm insert into the running kernel" \
    || bad "modprobe failed (vermagic/depmod?)"

hdr "4. udev rule ships with the /dev/lora contract"
$G 'R=/etc/udev/rules.d/99-notebook-serial.rules; grep -q 1a86 $R && grep -q 10c4 $R && grep -q "SYMLINK+=\"lora lora%n\"" $R && grep -q dialout $R' 2>/dev/null \
    && ok "both bridge ids -> dialout 0660 + /dev/lora symlink" \
    || bad "99-notebook-serial.rules missing or incomplete in image"

if [ "$WITH_FTDI" = 1 ]; then
    hdr "5. live plug (emulated FTDI — needs a rig where it enumerates)"
    $G 'lsmod | grep -q "^ftdi_sio"' 2>/dev/null \
        && ok "ftdi_sio autoloaded for the attached adapter" \
        || bad "ftdi_sio not loaded — adapter never enumerated"
    $G 'test -c /dev/ttyUSB0' 2>/dev/null \
        && ok "/dev/ttyUSB0 exists" || bad "/dev/ttyUSB0 missing"
    PERM=$($G 'ls -l /dev/ttyUSB0' 2>/dev/null | tr -d '\r')
    echo "$PERM" | grep -q "^crw-rw----.*root.*dialout" \
        && ok "root:dialout 0660 applied" || bad "wrong owner/mode: '$PERM'"
    $G 'stty -F /dev/ttyUSB0 raw -echo && rm -f /tmp/lora_rx && { cat /dev/ttyUSB0 > /tmp/lora_rx & }' >/dev/null 2>&1
    HOSTLOG=$(mktemp)
    python3 - "$USBSOCK" > "$HOSTLOG" 2>&1 <<'PY' &
import socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
s.settimeout(2)
s.sendall(b"PING-FROM-HOST\n")
buf, deadline = b"", time.time() + 45
while time.time() < deadline and b"PONG-FROM-GUEST" not in buf:
    try:
        buf += s.recv(4096)
    except socket.timeout:
        pass
print("HOSTRX-OK" if b"PONG-FROM-GUEST" in buf else "HOSTRX-MISS")
PY
    PUMP=$!
    sleep 2
    $G 'echo PONG-FROM-GUEST > /dev/ttyUSB0' >/dev/null 2>&1
    $G 'sleep 1; grep -q PING-FROM-HOST /tmp/lora_rx' 2>/dev/null \
        && ok "guest received host bytes" || bad "guest never saw host bytes"
    wait "$PUMP" 2>/dev/null
    grep -q HOSTRX-OK "$HOSTLOG" \
        && ok "host received guest bytes" || bad "host never saw guest bytes"
    rm -f "$HOSTLOG"
else
    hdr "5. live plug — DEFERRED"
    echo "  Not run: QEMU's FTDI never enumerates on this TCG rig (see header)."
    echo "  The physical-stick smoke test in docs/LORA-DONGLE.md is the"
    echo "  authoritative end-to-end; --with-ftdi runs this leg anyway."
fi

hdr "result"
printf '  %d ok, %d failed\n' "$pass" "$fail"
if [ "$BOOTED" = 1 ]; then
    kill_own_guest
    echo "  (guest shut down)"
fi
[ "$fail" = 0 ] || exit 1
echo "LORA-GUEST-CHECK: PASS"
