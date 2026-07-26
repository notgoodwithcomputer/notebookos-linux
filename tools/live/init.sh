#!/bin/sh
# Notebook OS live-ISO init (PID 1 inside the live initramfs).
#
# The kernel unpacks this initramfs and runs /init. We:
#   1. bring up /proc /sys /dev (devtmpfs auto-mounts /dev — DEVTMPFS_MOUNT=y),
#   2. find the boot medium by SCANNING block devices for the live payload
#      (busybox has no findfs and its blkid can't resolve a label, so we don't
#      rely on labels: we just try mounting each device and look for the
#      squashfs). Works for an optical disc, a USB stick the ISO was dd'd onto,
#      or a QEMU cdrom.
#   3. loop-mount the read-only squashfs root,
#   4. stack a tmpfs over it with overlayfs so the live session is writable,
#   5. keep the medium mounted at /run/live/medium (the installer reads the
#      /install payload from there),
#   6. switch_root into the overlay and hand off to the real /sbin/init.
SQUASH="/live/rootfs.squashfs"

/bin/mount -t proc     proc /proc 2>/dev/null
/bin/mount -t sysfs    sys  /sys  2>/dev/null
/bin/mount -t devtmpfs dev  /dev  2>/dev/null

echo "Notebook OS live: locating boot medium..." > /dev/console

rescue() {
    echo "" > /dev/console
    echo "!! live boot failed: $1" > /dev/console
    echo "!! dropping to an emergency shell." > /dev/console
    exec /bin/sh
}

# Try to mount $1 read-only at /run/live/medium and confirm it carries the live
# payload. On success the medium is left mounted and we return 0.
try_medium() {
    dev="$1"
    [ -b "$dev" ] || return 1
    if /bin/mount -t iso9660 -o ro "$dev" /run/live/medium 2>/dev/null \
       || /bin/mount -o ro "$dev" /run/live/medium 2>/dev/null; then
        if [ -f "/run/live/medium$SQUASH" ]; then
            return 0
        fi
        /bin/umount /run/live/medium 2>/dev/null
    fi
    return 1
}

/bin/mkdir -p /run/live/medium /run/live/ro /run/live/rw /run/live/root

# Scan every block device (and its first partitions) for the live payload. USB
# and optical devices enumerate a few seconds after the kernel starts, so retry.
FOUND=""
i=0
while [ $i -lt 30 ]; do
    for base in $(/bin/ls /sys/block 2>/dev/null); do
        case "$base" in
            sr*|sd*|vd*|hd*|mmcblk*|nvme*) ;;
            *) continue ;;
        esac
        for cand in "/dev/$base" "/dev/${base}1" "/dev/${base}2" \
                    "/dev/${base}p1" "/dev/${base}p2"; do
            if try_medium "$cand"; then FOUND="$cand"; break; fi
        done
        [ -n "$FOUND" ] && break
    done
    [ -n "$FOUND" ] && break
    [ -x /sbin/mdev ] && /sbin/mdev -s 2>/dev/null
    /bin/sleep 1
    i=$((i + 1))
done
[ -n "$FOUND" ] || rescue "no medium carrying $SQUASH found"
echo "Notebook OS live: booting from $FOUND" > /dev/console

# squashfs (read-only lower layer) via busybox auto-loop
/bin/mount -t squashfs -o ro,loop "/run/live/medium$SQUASH" /run/live/ro \
    || rescue "cannot mount squashfs root"

# tmpfs upper layer -> a writable live session that never touches the medium
/bin/mount -t tmpfs -o "size=75%,mode=0755" tmpfs /run/live/rw \
    || rescue "cannot mount tmpfs upper"
/bin/mkdir -p /run/live/rw/upper /run/live/rw/work
/bin/mount -t overlay overlay \
    -o "lowerdir=/run/live/ro,upperdir=/run/live/rw/upper,workdir=/run/live/rw/work" \
    /run/live/root \
    || rescue "cannot stack overlay root"

# Pre-mount the NEW root's /run as tmpfs and relocate the medium under it. The
# real init later runs `mount -a`, and fstab has `tmpfs /run tmpfs`; by mounting
# /run ourselves first, mount -a finds it already mounted and does NOT shadow
# our medium (the installer reads /run/live/medium/install/*). The squashfs (ro)
# and tmpfs (rw) overlay backing stay referenced by the live overlay itself, so
# they need not be relocated.
NEWROOT=/run/live/root
/bin/mkdir -p "$NEWROOT/run"
/bin/mount -t tmpfs -o "mode=0755,nosuid,nodev" tmpfs "$NEWROOT/run" \
    || rescue "cannot mount new /run"
/bin/mkdir -p "$NEWROOT/run/live/medium"
/bin/mount --move /run/live/medium "$NEWROOT/run/live/medium" \
    || rescue "cannot relocate boot medium into the new root"

echo "Notebook OS live: starting session..." > /dev/console

# Carry the kernel-mounted devtmpfs into the new root. The real init's inittab
# does NOT mount /dev — it relies on the kernel's one-shot DEVTMPFS_MOUNT, which
# already fired on the initramfs and won't fire again after switch_root. Without
# this the tty1 / ttyS1 getty processes find no device node and spin. Do this
# last, after our final /dev/console write.
/bin/mount --move /dev "$NEWROOT/dev" 2>/dev/null \
    || /bin/mount -t devtmpfs dev "$NEWROOT/dev" 2>/dev/null

exec /sbin/switch_root "$NEWROOT" /sbin/init
exec /bin/sh
