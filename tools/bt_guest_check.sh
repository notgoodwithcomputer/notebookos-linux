#!/bin/bash
# bt_guest_check.sh — prove the BLE stack works inside the running guest.
#
# Boots NotebookOS headless, creates an emulated LE controller with btvirt,
# and checks that the kernel, bluetoothd and D-Bus all agree it is usable.
# This is the on-guest counterpart to tools/bt_stack_check.sh (which only
# inspects the built tree).
#
# Why btvirt: the build host has no Bluetooth radio, so there is nothing to
# pass through to QEMU. btvirt attaches an emulated controller to /dev/vhci
# (CONFIG_BT_HCIVHCI), which exercises the same path a real adapter uses.
#
# NOTE: btvirt needs AF_ALG. BlueZ builds its LE crypto on the kernel crypto
# socket API (ecb(aes) + cmac(aes)); with AF_ALG absent, btdev_create() fails
# and btvirt dies with the misleading "Failed to open Virtual HCI device".
# See tools/sockprobe-init.c for why AF_ALG is not a networking hole.
#
# Usage: tools/bt_guest_check.sh          (boots a guest, leaves it running)
#        tools/bt_guest_check.sh --running  (use an already-booted guest)

set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
G="python3 $R/tools/gsh.py"
fail=0; pass=0

ok()  { printf '  \033[32mOK\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

if [ "${1:-}" != "--running" ]; then
    hdr "booting guest (headless, TCG)"
    pkill -f qemu-system >/dev/null 2>&1
    rm -f "$R/boot-work/ttyS1.sock"
    NB_KVM=0 "$R/tools/run-desktop.sh" --headless >/tmp/bt_guest_qemu.log 2>&1 &
    for _ in $(seq 1 60); do [ -S "$R/boot-work/ttyS1.sock" ] && break; sleep 2; done
    for _ in $(seq 1 60); do
        $G 'echo READY' 2>/dev/null | grep -q READY && break
        sleep 3
    done
    $G 'echo READY' 2>/dev/null | grep -q READY \
        && echo "  guest shell up" || { echo "  guest never came up"; exit 1; }
fi

hdr "1. kernel bluetooth core"
$G 'dmesg | grep -c "Bluetooth: Core ver"' 2>/dev/null | grep -q '^[1-9]' \
    && ok "Bluetooth core initialised" || bad "Bluetooth core missing"
$G 'dmesg | grep -c "L2CAP socket layer initialized"' 2>/dev/null | grep -q '^[1-9]' \
    && ok "L2CAP registered (ATT/GATT ride on this)" || bad "L2CAP missing"

hdr "2. daemon"
$G pidof bluetoothd 2>/dev/null | grep -qE '[0-9]' \
    && ok "bluetoothd running" || bad "bluetoothd not running"

hdr "3. emulated LE controller"
$G 'pidof btvirt >/dev/null || setsid btvirt -l1 >/tmp/btv.log 2>&1 & true' >/dev/null 2>&1
sleep 4
$G pidof btvirt 2>/dev/null | grep -qE '[0-9]' \
    && ok "btvirt running" || { bad "btvirt died -- check AF_ALG"; $G cat /tmp/btv.log; }
$G ls /sys/class/bluetooth 2>/dev/null | grep -q hci0 \
    && ok "hci0 present" || bad "no hci0"

hdr "4. management API sees an LE controller"
info=$($G btmgmt info 2>/dev/null)
echo "$info" | grep -q "Index list with 1 item" && ok "controller registered" \
                                                || bad "no controller in index list"
echo "$info" | grep -q "supported settings:.* le "  && ok "LE supported" || bad "LE not supported"
echo "$info" | grep -q "supported settings:.*advertising" \
    && ok "advertising supported" || bad "advertising unsupported"

hdr "5. power on + advertise"
$G btmgmt --index 0 power on >/dev/null 2>&1
$G btmgmt --index 0 advertising on 2>/dev/null | grep -q "advertising" \
    && ok "LE advertising enabled" || bad "could not enable advertising"

hdr "6. bluetoothd over D-Bus (the path an app uses)"
show=$($G bluetoothctl show 2>/dev/null)
echo "$show" | grep -q "Powered: yes"        && ok "adapter powered"      || bad "adapter not powered"
echo "$show" | grep -q "Generic Attribute"   && ok "GATT registered"      || bad "GATT missing"
echo "$show" | grep -q "Roles: central"      && ok "central role"         || bad "no central role"
echo "$show" | grep -q "Roles: peripheral"   && ok "peripheral role"      || bad "no peripheral role"

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
