#!/bin/sh
# bt_hw_report.sh — one-shot Bluetooth report, to run ON THE LAPTOP.
#
# The build host has no Bluetooth radio at all, so nothing about a real
# adapter can be reproduced here. This collects everything needed to pin down a
# firmware or adapter problem in one paste-able block: which chip it is, which
# firmware file the driver asked for, exactly how the load failed, and whether
# the controller ever reached a configured state.
#
# Run it in the Terminal app (or over a serial console):
#
#     sh /opt/notebook/tools/bt_hw_report.sh
#
# It only reads; it changes nothing.

# --short prints ONE line, short enough to read off the screen and type out.
# There is no clipboard between this machine and anywhere else, so the full
# report below is unusable when the laptop is the only place it exists — the
# chip id alone identifies which firmware image it needs.
if [ "${1:-}" = "--short" ]; then
    ids=""
    for d in /sys/bus/usb/devices/*; do
        [ -e "$d/idVendor" ] || continue
        for i in "$d":*; do
            case "$(readlink -f "$i/driver" 2>/dev/null)" in
                */btusb) ids="$ids$(cat "$d/idVendor")":"$(cat "$d/idProduct") " ;;
            esac
        done
    done
    [ -n "$ids" ] || ids="none "
    addr="none"
    for h in /sys/class/bluetooth/hci*; do
        [ -e "$h/address" ] && addr=$(cat "$h/address" 2>/dev/null)
    done
    case "$addr" in
        00:00:00:00:00:00) state="UNCONFIGURED" ;;
        none)              state="NO-HCI" ;;
        *)                 state="OK" ;;
    esac
    fw=$(dmesg 2>/dev/null | sed -n 's/.*Intel firmware file \([^ ]*\).*/\1/p' | tail -1)
    [ -n "$fw" ] || fw=$(dmesg 2>/dev/null | sed -n 's/.*device firmware: \([^ ]*\).*/\1/p' | tail -1)
    [ -n "$fw" ] || fw="?"
    ondisk="?"
    [ "$fw" != "?" ] && { [ -e "/lib/firmware/$fw" ] && ondisk=yes || ondisk=no; }
    err=$(dmesg 2>/dev/null | grep -o "Failed to send firmware header ([-0-9]*)\|Failed to load Intel firmware file [^ ]* ([-0-9]*)\|FW download error recovery failed ([-0-9]*)" | tail -1 | grep -o "([-0-9]*)" | tr -d '()')
    [ -n "$err" ] || err="?"
    last=$(tail -1 /var/log/btfirmware.log 2>/dev/null | tr ' ' '_')
    [ -n "$last" ] || last="no-log"
    echo "BT ${ids}hci=$state fw=$fw ondisk=$ondisk err=$err recovery=$last"
    exit 0
fi

echo "=== Notebook OS Bluetooth report ==="
date 2>/dev/null
echo

echo "--- USB devices claiming to be Bluetooth ---"
if [ -d /sys/bus/usb/devices ]; then
    for d in /sys/bus/usb/devices/*; do
        [ -e "$d/idVendor" ] || continue
        cls=$(cat "$d/bDeviceClass" 2>/dev/null)
        # class e0 = Wireless Controller (where BT adapters live); also report
        # anything btusb has actually bound, whatever it calls itself.
        bound=""
        for i in "$d":*; do
            [ -e "$i/driver" ] || continue
            case "$(readlink -f "$i/driver" 2>/dev/null)" in
                */btusb) bound="btusb" ;;
            esac
        done
        if [ "$cls" = "e0" ] || [ -n "$bound" ]; then
            printf "  %s  %s:%s  %s %s  driver=%s\n" \
                "$(basename "$d")" \
                "$(cat "$d/idVendor" 2>/dev/null)" \
                "$(cat "$d/idProduct" 2>/dev/null)" \
                "$(cat "$d/manufacturer" 2>/dev/null)" \
                "$(cat "$d/product" 2>/dev/null)" \
                "${bound:-none}"
        fi
    done
else
    echo "  (no /sys/bus/usb/devices)"
fi
echo

echo "--- kernel Bluetooth controllers ---"
if [ -d /sys/class/bluetooth ] && [ -n "$(ls /sys/class/bluetooth 2>/dev/null)" ]; then
    for h in /sys/class/bluetooth/hci*; do
        [ -e "$h" ] || continue
        addr=$(cat "$h/address" 2>/dev/null)
        note=""
        [ "$addr" = "00:00:00:00:00:00" ] && note="   <-- NOT CONFIGURED (no firmware)"
        printf "  %s  address=%s%s\n" "$(basename "$h")" "$addr" "$note"
    done
else
    echo "  none — the kernel sees no Bluetooth controller at all"
fi
echo

echo "--- firmware the driver asked for, and how it went ---"
dmesg 2>/dev/null | grep -i -E "bluetooth|btusb|btintel|btrtl|btmtk|firmware" \
    | tail -40 || echo "  (dmesg unavailable)"
echo

echo "--- is the firmware actually on disk? ---"
for f in $(dmesg 2>/dev/null | sed -n 's/.*firmware file \([^ ]*\).*/\1/p' | sort -u); do
    if [ -e "/lib/firmware/$f" ]; then
        echo "  present: /lib/firmware/$f"
    else
        echo "  MISSING: /lib/firmware/$f"
    fi
done
ls /lib/firmware/intel/ibt-* >/dev/null 2>&1 \
    && echo "  (/lib/firmware/intel holds $(ls /lib/firmware/intel/ibt-*.sfi 2>/dev/null | wc -l) Intel BT images)"
echo

echo "--- firmware recovery script ---"
if [ -f /var/log/btfirmware.log ]; then
    tail -20 /var/log/btfirmware.log
else
    echo "  S39btfirmware left no log (it exits early when an adapter is already"
    echo "  configured, which is the healthy case)"
fi
echo

echo "--- bluetoothd / BlueZ view ---"
if command -v bluetoothctl >/dev/null 2>&1; then
    echo "show:";    bluetoothctl show 2>&1 | sed 's/^/    /' | head -15
    echo "list:";    bluetoothctl list 2>&1 | sed 's/^/    /' | head -5
else
    echo "  bluetoothctl not present"
fi
command -v btmgmt >/dev/null 2>&1 && { echo "btmgmt info:"; btmgmt info 2>&1 | sed 's/^/    /' | head -10; }
echo
echo "=== end of report ==="
