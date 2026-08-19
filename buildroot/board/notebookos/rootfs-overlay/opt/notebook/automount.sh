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
media_root="${NB_MEDIA_ROOT:-/media}"
mnt="$media_root/$dev"
reserved=0

# Duplicate add events are legal. Check before label lookup/reservation so an
# already-mounted device cannot leave an unused "LABEL (2)" directory behind.
if [ "$action" = "add" ] && grep -q "^/dev/$dev " /proc/mounts; then
	exit 0
fi

# Name the mount point after the volume's OWN label, so the user meets their
# stick as "PHOTOS" in the Finder sidebar, the window title and the file picker
# rather than as "sda1". Doing it here (rather than prettifying the name in the
# Finder) means every path the user is ever shown reads properly.
#   * / and \ are replaced, so a label can never build a path of its own
#   * "." and ".." are NOT names, they are this directory and its PARENT, and
#     the label on a stick somebody handed you is attacker-controlled by
#     definition (e2label and mkfs.vfat both write either one happily).
#     Measured, with the old rule that replaced only / and \:
#        label "."  -> "/media/." IS /media, and the dedup loop below skips an
#                      EMPTY /media, so the stick was mounted ON TOP OF /media
#                      and every other volume under it vanished. This is the
#                      shipped configuration: /media exists and starts empty.
#        label ".." -> "/media/.." IS "/". Today the dedup loop deflects it
#                      (because / is never empty) into a junk directory called
#                      ".. (2)" -- but only while /media itself exists. With no
#                      /media the mount lands on the RUNNING ROOT FILESYSTEM.
#     Neither outcome is one line of shell away from the other, so any all-dots
#     label falls back to the device name.
#   * control characters are stripped: they cannot be typed, cannot be read
#     back, and a newline would split the value this script reasons about
#   * everything else is left alone, so "My Backup" stays "My Backup"
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
	mkdir -p "$media_root" 2>/dev/null || exit 0
	name=$(blkid -s LABEL -o value "/dev/$dev" 2>/dev/null \
		| tr -d '\000-\037' \
		| sed 's#[/\\]#_#g; s/^[[:space:]]*//; s/[[:space:]]*$//' | cut -c1-40)
	# Reject a label made of nothing but dots — see the note above: "/media/.."
	# is "/", and mounting a stranger's stick there takes the machine down.
	case "$name" in
		"")     ;;      # already empty
		*[!.]*) ;;      # has a real character in it: keep
		*)      name="" ;;
	esac
	if [ -n "$name" ]; then
		cand="$name"; i=2
		# An empty directory may already be an empty mounted filesystem.  Its
		# contents cannot distinguish it from an unused directory, so every
		# existing path is reserved; otherwise equal labels stack mounts and
		# make the first USB drive disappear.
		# mkdir is the reservation, not a later consequence of the choice.
		# udev runs add jobs concurrently; check-then-mkdir lets equal labels
		# both choose the same path and stack mounts, hiding the first drive.
		while ! mkdir "$media_root/$cand" 2>/dev/null; do
			# mkdir fails for two different reasons and they must not be
			# treated the same. "It is already there" means try the next
			# candidate. Anything else -- a read-only or full /media -- will
			# not change on the next twenty tries, and spinning through them
			# to exit silently means a plugged-in stick simply never appears.
			# Fall back to the device name, which is what shipped before mount
			# points were named after labels at all.
			if [ ! -d "$media_root/$cand" ]; then
				cand="$dev"
				mkdir -p "$media_root/$cand" 2>/dev/null || exit 0
				break
			fi
			[ "$i" -gt 20 ] && exit 0
			cand="$name ($i)"
			i=$((i + 1))
		done
		mnt="$media_root/$cand"
		reserved=1
	fi
fi

case "$action" in
	add)
		[ "$reserved" -eq 1 ] || mkdir -p "$mnt"
		# let the kernel autodetect the fs; try rw, fall back to ro.
		# noexec: nothing on a stick somebody handed you may be RUN. The
		# machine already auto-mounts any partition that appears, with no
		# prompt, so the contents are attacker-controlled by definition.
		# nosuid,nodev were already set; noexec closes the third of the trio.
		#
		# rw uses -o sync (write-through): a USB stick has NO safe-eject step in
		# every flow, so a user who copies a file and then just pulls the stick
		# must not lose it to the page cache. Synchronous writes land on the
		# device as the copy happens (Windows' "quick removal" default). Slower
		# for huge files, but correct — losing the file is the worse outcome.
		mount -o rw,sync,noatime,nosuid,nodev,noexec "/dev/$dev" "$mnt" 2>/dev/null \
			|| mount -o ro,nosuid,nodev,noexec "/dev/$dev" "$mnt" 2>/dev/null \
			|| { rmdir "$mnt" 2>/dev/null; exit 0; }
		;;
	remove)
		umount "$mnt" 2>/dev/null
		rmdir "$mnt" 2>/dev/null
		;;
esac
exit 0
