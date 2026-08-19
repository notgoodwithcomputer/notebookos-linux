#!/bin/sh
#
# mkstick — build a FAT "USB stick" image on the host, no root needed, so a
# guest boot can be handed FILES: a big photo for the decode ceilings, a film
# for the export-cancel row, a wrong-shape store to copy into place, an .nbpkg.
#
#   tools/mkstick.sh OUT.img LABEL SIZE_MB file [file...]
#
# Attach it to a guest through the hook run-desktop.sh / run-iso.sh already
# have:
#
#   NB_QEMU_EXTRA="-drive if=none,id=stick,file=OUT.img,format=raw \
#                  -device usb-storage,drive=stick" tools/run-desktop.sh --headless
#
# The guest's udev automount (99-notebook-automount.rules + automount.sh) mounts
# it at /media/LABEL, where the Finder lists it as a removable drive and every
# app's file picker can reach it. This is the route the HANDOFF of 2026-08-14
# was missing: every other way of getting a file INTO the guest (gsh over the
# debug serial, debugfs, a root mount) was closed or flaky.
#
# mtools only (mformat/mcopy); the label is upper-cased by FAT and must be
# 11 characters or fewer.
set -eu
[ $# -ge 4 ] || { sed -n 2,20p "$0"; exit 2; }
OUT=$1; LABEL=$2; MB=$3; shift 3
command -v mformat >/dev/null || { echo "mkstick: mtools (mformat) not installed" >&2; exit 1; }
rm -f "$OUT"
# a plain FAT filesystem image (no partition table): the guest kernel sees a
# whole-disk filesystem, which the automount rule handles like a bare stick
dd if=/dev/zero of="$OUT" bs=1M count="$MB" status=none
mformat -i "$OUT" -v "$LABEL" ::
for f in "$@"; do
	[ -e "$f" ] || { echo "mkstick: no such file: $f" >&2; exit 1; }
	mcopy -i "$OUT" -s "$f" ::
done
echo "mkstick: $OUT ($MB MB, label $LABEL):"
mdir -i "$OUT" :: | sed 's/^/  /'
