#!/bin/bash
# Assemble a dd-able UEFI disk image for Notebook OS on real hardware.
#
# Layout (GPT):
#   p1  EFI System Partition (FAT32)  -> GRUB (BOOTX64.EFI) + the kernel
#   p2  Linux filesystem (ext4)       -> the Buildroot rootfs, fixed PARTUUID
#
# GRUB (removable-media path /EFI/BOOT/BOOTX64.EFI) boots the kernel with
# root=PARTUUID=<fixed>, so it finds the rootfs regardless of the device name
# (sdX vs nvme0n1 vs mmcblk0). No per-machine tweaks needed. Runs entirely
# from image files — no root, no loop mounts.
#
#   tools/mkimage-uefi.sh            -> boot-work/notebookos-uefi.img
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KERNEL="$ROOT/kbuild-desktop/arch/x86/boot/bzImage"
ROOTFS="$ROOT/buildroot/output/images/rootfs.ext4"
WORK="$ROOT/boot-work"; mkdir -p "$WORK"
OUT="$WORK/notebookos-uefi.img"

SGDISK=/usr/sbin/sgdisk
# Stable GPT partition GUID for the rootfs — baked into the GRUB cmdline.
ROOT_PARTUUID="b8e5a5f2-1a2b-4c3d-9e8f-000000000042"
ESP_MB=64

[ -f "$KERNEL" ] || { echo "missing kernel: $KERNEL"; exit 2; }
[ -f "$ROOTFS" ] || { echo "missing rootfs: $ROOTFS (build Buildroot first)"; exit 2; }

echo "== 1/5  Secure Boot keys + signed GRUB (embeds grub.cfg) + signed kernel =="
# UEFI Secure Boot chain:  shim (MS-signed) -> grub (MOK-signed) -> kernel (MOK-signed)
# Set NB_SECUREBOOT=0 to fall back to a plain unsigned GRUB (dev/BIOS only).
SECUREBOOT="${NB_SECUREBOOT:-1}"
SHIM=/usr/lib/shim/shimx64.efi.signed
MM=/usr/lib/shim/mmx64.efi.signed
GRUBCFG="$WORK/grub.cfg"
cat > "$GRUBCFG" <<EOF
set timeout=${NB_GRUB_TIMEOUT:-1}
set default=0
insmod part_gpt
insmod fat
insmod ext2
menuentry "Notebook OS" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=$ROOT_PARTUUID rw rootwait console=tty1 loglevel=5
}
menuentry "Notebook OS (verbose + serial console)" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=$ROOT_PARTUUID rw rootwait console=tty1 console=ttyS0,115200 loglevel=7
}
menuentry "Notebook OS (no Intel graphics driver)" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=$ROOT_PARTUUID rw rootwait console=tty1 loglevel=5 module_blacklist=i915
}
menuentry "Notebook OS (with compositor — menu-repaint test)" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=$ROOT_PARTUUID rw rootwait console=tty1 loglevel=5 nb.compositor=1
}
menuentry "Enroll Secure Boot key (first boot: run once, then reboot)" {
    search --no-floppy --file --set=root /EFI/BOOT/mmx64.efi
    chainloader /EFI/BOOT/mmx64.efi
}
EOF

# Chain: shim (MS-signed; trusts the Debian SB CA it embeds) -> Debian's signed
# GRUB (carries the shim_lock verifier, so it verifies the kernel under Secure
# Boot) -> our MOK-signed kernel. We sign ONLY the kernel; GRUB is Debian-signed
# and already trusted by shim. Debian's signed GRUB reads its menu from
# prefix=/EFI/debian/grub.cfg, so we place the config there.
SIGNED_GRUB=/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed
KEYDIR="$ROOT/secureboot"

KSIGNED="$WORK/bzImage"          # what lands on the ESP as /bzImage
if [ "$SECUREBOOT" = "1" ]; then
    for f in "$SHIM" "$MM" "$SIGNED_GRUB"; do
        [ -f "$f" ] || { echo "missing $f (apt install shim-signed grub-efi-amd64-signed)"; exit 2; }
    done
    "$ROOT/tools/gen-sb-keys.sh" >/dev/null
    sbsign --key "$KEYDIR/MOK.key" --cert "$KEYDIR/MOK.crt" --output "$KSIGNED" "$KERNEL"
    echo "   kernel: $(sbverify --cert "$KEYDIR/MOK.crt" "$KSIGNED" 2>&1 | tr '\n' ' ')"
else
    # dev / no-Secure-Boot fallback: self-built unsigned GRUB + unsigned kernel
    GRUBEFI="$WORK/grubx64.efi"
    grub-mkstandalone -O x86_64-efi -o "$GRUBEFI" \
        --modules="part_gpt part_msdos fat ext2 search search_fs_file normal linux echo test configfile loadenv all_video gfxterm terminal halt reboot" \
        "boot/grub/grub.cfg=$GRUBCFG" 2>/dev/null
    cp -f "$KERNEL" "$KSIGNED"
    echo "   (Secure Boot disabled: unsigned GRUB + kernel)"
fi

echo "== 2/5  ESP (FAT32): shim -> grub -> kernel =="
ESP="$WORK/esp.img"
dd if=/dev/zero of="$ESP" bs=1M count=$ESP_MB status=none
mformat -i "$ESP" -F -v NBOS_ESP ::
mmd -i "$ESP" ::/EFI ::/EFI/BOOT
if [ "$SECUREBOOT" = "1" ]; then
    mmd -i "$ESP" ::/EFI/debian
    mcopy -i "$ESP" "$SHIM"        ::/EFI/BOOT/BOOTX64.EFI    # MS-signed shim = SB entry point
    mcopy -i "$ESP" "$SIGNED_GRUB" ::/EFI/BOOT/grubx64.efi    # Debian-signed grub (shim trusts it)
    mcopy -i "$ESP" "$MM"          ::/EFI/BOOT/mmx64.efi      # MokManager (enrollment UI)
    mcopy -i "$ESP" "$KEYDIR/MOK.cer" ::/EFI/BOOT/MOK.cer     # our cert (user enrolls once)
    mcopy -i "$ESP" "$GRUBCFG"     ::/EFI/debian/grub.cfg     # signed grub's config prefix
else
    mcopy -i "$ESP" "$GRUBEFI"     ::/EFI/BOOT/BOOTX64.EFI    # plain grub
fi
mcopy -i "$ESP" "$KSIGNED" ::/bzImage
echo "   ESP contents:"; mdir -i "$ESP" -/ :: | sed 's/^/     /'

echo "== 3/5  size the disk image =="
ROOTFS_BYTES=$(stat -Lc %s "$ROOTFS")   # -L: follow the .ext4 -> .ext2 symlink
ALIGN_MB=1                                  # GPT + 1MiB alignment at front
BACKUP_MB=1                                 # backup GPT at the end
TOTAL_MB=$(( ALIGN_MB + ESP_MB + (ROOTFS_BYTES + 1048575)/1048576 + BACKUP_MB ))
echo "   rootfs=$((ROOTFS_BYTES/1048576))MiB  esp=${ESP_MB}MiB  total=${TOTAL_MB}MiB"
rm -f "$OUT"
dd if=/dev/zero of="$OUT" bs=1M count=$TOTAL_MB status=none

echo "== 4/5  GPT partition table =="
$SGDISK -Z "$OUT" >/dev/null
$SGDISK -n 1:2048:+${ESP_MB}M -t 1:EF00 -c 1:"EFI System" "$OUT" >/dev/null
$SGDISK -n 2:0:0 -t 2:8300 -c 2:"notebookos-root" \
        -u 2:"$ROOT_PARTUUID" "$OUT" >/dev/null
P1_START=$($SGDISK -i 1 "$OUT" | awk '/First sector/{print $3}')
P2_START=$($SGDISK -i 2 "$OUT" | awk '/First sector/{print $3}')
echo "   p1(ESP) @ sector $P1_START   p2(root) @ sector $P2_START"

echo "== 5/5  write partition contents =="
dd if="$ESP"    of="$OUT" bs=512 seek="$P1_START" conv=notrunc status=none
dd if="$ROOTFS" of="$OUT" bs=512 seek="$P2_START" conv=notrunc status=none
$SGDISK -v "$OUT" | sed 's/^/   /'

echo
echo "DONE -> $OUT  ($(du -h "$OUT" | cut -f1))"
echo "Flash with:  sudo dd if=$OUT of=/dev/sdX bs=4M oflag=direct status=progress && sync"
