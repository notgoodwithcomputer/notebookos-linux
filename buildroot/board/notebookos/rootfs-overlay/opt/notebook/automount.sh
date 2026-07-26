#!/bin/sh
#
# Auto-mount / unmount removable block devices to /media/<dev>. Invoked by the
# udev rule 99-notebook-automount.rules when a USB storage partition appears or
# disappears. The mounted volume then shows up in the Finder's Devices sidebar
# (read live from /proc/mounts). Kept deliberately small and defensive.
#
action="$1"
dev="$2"
[ -n "$dev" ] || exit 0
mnt="/media/$dev"

# Name the mount point after the volume's OWN label, so the user meets their
# stick as "PHOTOS" in the Finder sidebar, the window title and the file picker
# rather than as "sda1". Doing it here (rather than prettifying the name in the
# Finder) means every path the user is ever shown reads properly.
#   * only / and \ are actually unsafe in a path component; the rest of a label
#     is left alone so "My Backup" stays "My Backup"
#   * capped at 40 chars, and a label that sanitises to nothing falls back
#   * a second stick with the same label gets "LABEL (2)" rather than colliding
# On REMOVE the device is already gone, so blkid can tell us nothing — look the
# mount point up by device in /proc/mounts instead. Deriving it from the label
# here would compute the wrong path and strand the mount forever.
if [ "$action" = "remove" ]; then
	# /proc/mounts octal-escapes space, tab and backslash; a label like
	# "My Backup" appears as "My\040Backup" and umounting that literal fails.
	m=$(awk -v d="/dev/$dev" '$1 == d { print $2; exit }' /proc/mounts \
		| sed 's/\\040/ /g; s/\\011/\t/g; s/\\134/\\/g')
	[ -n "$m" ] && mnt="$m"
else
	name=$(blkid -s LABEL -o value "/dev/$dev" 2>/dev/null \
		| sed 's#[/\\]#_#g; s/^[[:space:]]*//; s/[[:space:]]*$//' | cut -c1-40)
	if [ -n "$name" ]; then
		cand="$name"; i=2
		while [ -d "/media/$cand" ] && [ "$(ls -A "/media/$cand" 2>/dev/null)" ]; do
			cand="$name ($i)"
			i=$((i + 1))
			[ "$i" -gt 20 ] && { cand="$dev"; break; }
		done
		mnt="/media/$cand"
	fi
fi

case "$action" in
	add)
		# already mounted (e.g. the system disk)? leave it alone
		grep -q "^/dev/$dev " /proc/mounts && exit 0
		mkdir -p "$mnt"
		# let the kernel autodetect the fs; try rw, fall back to ro.
		# rw uses -o sync (write-through): a USB stick has NO safe-eject step in
		# every flow, so a user who copies a file and then just pulls the stick
		# must not lose it to the page cache. Synchronous writes land on the
		# device as the copy happens (Windows' "quick removal" default). Slower
		# for huge files, but correct — losing the file is the worse outcome.
		mount -o rw,sync,noatime,nosuid,nodev "/dev/$dev" "$mnt" 2>/dev/null \
			|| mount -o ro,nosuid,nodev "/dev/$dev" "$mnt" 2>/dev/null \
			|| { rmdir "$mnt" 2>/dev/null; exit 0; }
		;;
	remove)
		umount "$mnt" 2>/dev/null
		rmdir "$mnt" 2>/dev/null
		;;
esac
exit 0
