#!/usr/bin/env bash
# tools/mkiso.sh — build the Notebook OS Live ISO.
#
# Produces a hybrid BIOS+UEFI bootable ISO that boots to the live desktop and
# carries the payload the installer needs. Structure inside the ISO:
#   /boot/grub/grub.cfg    live GRUB menu (grub-mkrescue reads this)
#   /live/bzImage          the kernel
#   /live/initrd.img       the live-init initramfs (busybox: find medium ->
#                          squashfs -> overlay -> switch_root)
#   /live/rootfs.squashfs  the read-only squashed root filesystem
#   /install/rootfs.tar    pristine root tarball the installer extracts
#   /install/BOOTX64.EFI   prebuilt GRUB EFI for the INSTALLED system
#   /install/bzImage       kernel for the installed system
#
# The live root is mounted read-only (squashfs) with a tmpfs overlay, so the
# live session is fully writable but never touches the medium. Everything is
# built from host tools (mksquashfs, grub-mkrescue, xorriso) + the target
# busybox — no root, no loop mounts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER="${NB_VERSION:-1.0}"
LABEL="NOTEBOOKOS"

KERNEL="${KERNEL:-$ROOT/kbuild-desktop/arch/x86/boot/bzImage}"
ROOTFSTAR="${ROOTFSTAR:-$ROOT/buildroot/output/images/rootfs.tar}"
BUSYBOX="${BUSYBOX:-$ROOT/buildroot/output/target/bin/busybox}"
TARGET="${TARGET:-$ROOT/buildroot/output/target}"
INSTALL_EFI="${INSTALL_EFI:-$ROOT/boot-work/BOOTX64.EFI}"
INIT_SRC="${INIT_SRC:-$ROOT/tools/live/init.sh}"
OUTDIR="${OUTDIR:-$ROOT/release}"
ISO="${ISO:-$OUTDIR/notebookos-${VER}.iso}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/nbiso.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

say() { printf '\033[1;33m[mkiso]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[mkiso] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "host tool '$1' not found (need: $2)"; }

# ---- preflight ----------------------------------------------------------
need mksquashfs   "squashfs-tools"
need grub-mkrescue "grub2 + grub-pc-bin/grub-efi-amd64-bin"
need xorriso      "xorriso"
need cpio         "cpio"
need gzip         "gzip"
[ -f "$KERNEL" ]     || die "kernel not found: $KERNEL (build it: make -C kbuild-desktop bzImage)"
[ -f "$ROOTFSTAR" ]  || die "rootfs tarball not found: $ROOTFSTAR (build it: make -C buildroot)"
[ -x "$BUSYBOX" ]    || die "target busybox not found: $BUSYBOX (build buildroot first)"
[ -f "$INIT_SRC" ]   || die "live init not found: $INIT_SRC"
[ -f "$INSTALL_EFI" ] || die "installed-system EFI not found: $INSTALL_EFI (mkrelease builds it)"
mkdir -p "$OUTDIR"

GRAFT="$WORK/graft"
mkdir -p "$GRAFT/live" "$GRAFT/install" "$GRAFT/boot/grub"

# ---- 1. squashfs the root -----------------------------------------------
# Source is rootfs.tar (buildroot's fakeroot-finalized tree: correct root
# ownership, permissions and /dev). Prefer mksquashfs' native tar mode; fall
# back to a fakeroot extract when the host mksquashfs is too old for -tar.
# The result is cached at boot-work/rootfs.squashfs and reused when it is newer
# than rootfs.tar, so iterating on the initramfs/ISO doesn't re-squash (~min).
SQUASH_CACHE="$ROOT/boot-work/rootfs.squashfs"
if [ "${NB_SQUASH_REBUILD:-0}" != 1 ] && [ -f "$SQUASH_CACHE" ] && [ "$SQUASH_CACHE" -nt "$ROOTFSTAR" ]; then
    say "reusing cached squashfs ($(du -h "$SQUASH_CACHE" | cut -f1)) — set NB_SQUASH_REBUILD=1 to force"
    cp "$SQUASH_CACHE" "$GRAFT/live/rootfs.squashfs"
else
    # zstd (not xz): the live squashfs is the read-only root, so EVERY app launch
    # and library read decompresses a block on the CPU. zstd decompresses ~3-5x
    # faster than xz for ~10% larger size — a real boot + app-launch speedup on the
    # GPU-less software-rendered target. The kernel has CONFIG_SQUASHFS_ZSTD=y.
    say "building live/rootfs.squashfs (zstd) from $(basename "$ROOTFSTAR")"
    if mksquashfs -help 2>&1 | grep -q -- '-tar'; then
        mksquashfs - "$GRAFT/live/rootfs.squashfs" -tar -comp zstd -Xcompression-level 19 -b 1M -noappend \
            -no-progress < "$ROOTFSTAR"
    elif command -v fakeroot >/dev/null 2>&1; then
        say "  (host mksquashfs lacks -tar; using fakeroot extract)"
        EXT="$WORK/rootfs"
        mkdir -p "$EXT"
        fakeroot -- sh -c "tar xpf '$ROOTFSTAR' -C '$EXT' && mksquashfs '$EXT' '$GRAFT/live/rootfs.squashfs' -comp zstd -Xcompression-level 19 -b 1M -noappend -no-progress"
    else
        die "need either a mksquashfs with -tar support or 'fakeroot' installed"
    fi
    # cache for fast re-iteration; never let a cache-write problem (e.g. a
    # read-only stale cache) abort the release build.
    rm -f "$SQUASH_CACHE" 2>/dev/null || true
    if cp "$GRAFT/live/rootfs.squashfs" "$SQUASH_CACHE" 2>/dev/null; then
        chmod u+w "$SQUASH_CACHE" 2>/dev/null || true
    else
        echo "  (note: squashfs cache not updated — non-fatal)" >&2
    fi
fi

# ---- 2. live-init initramfs ---------------------------------------------
say "assembling live/initrd.img (busybox live-init)"
IRD="$WORK/initrd"
mkdir -p "$IRD"/{bin,sbin,proc,sys,dev,run/live/medium,run/live/ro,run/live/rw,run/live/root}
cp "$BUSYBOX" "$IRD/bin/busybox"
chmod 0755 "$IRD/bin/busybox"

# busybox applet symlinks the init script relies on
for a in sh mount umount mkdir sleep cat ls echo mknod; do
    ln -sf busybox "$IRD/bin/$a"
done
for a in switch_root findfs blkid mdev; do
    ln -sf ../bin/busybox "$IRD/sbin/$a"
done

# copy the dynamic loader + libraries busybox needs (target is same arch as
# host: x86_64/glibc). Resolve NEEDED + PT_INTERP from the ELF headers.
copy_lib() {
    # $1 = library soname or absolute path; search the target tree first.
    local name="$1" src=""
    case "$name" in
        /*) src="$TARGET$name"; [ -e "$src" ] || src="$name" ;;
        *)  for d in "$TARGET/lib" "$TARGET/usr/lib" "$TARGET/lib64" "$TARGET/usr/lib64"; do
                [ -e "$d/$name" ] && { src="$d/$name"; break; }
            done ;;
    esac
    [ -n "$src" ] && [ -e "$src" ] || return 0
    mkdir -p "$IRD/lib" "$IRD/lib64"
    cp -aL "$src" "$IRD/lib/$(basename "$name")" 2>/dev/null || true
    cp -aL "$src" "$IRD/lib64/$(basename "$name")" 2>/dev/null || true
}
if command -v readelf >/dev/null 2>&1; then
    INTERP="$(readelf -l "$BUSYBOX" 2>/dev/null | sed -n 's/.*interpreter: \(.*\)\]/\1/p' | tr -d ' ')"
    [ -n "$INTERP" ] && copy_lib "$INTERP"
    for so in $(readelf -d "$BUSYBOX" 2>/dev/null | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
        copy_lib "$so"
    done
fi
# belt-and-suspenders: the usual glibc set for a static-ish busybox
for so in ld-linux-x86-64.so.2 libc.so.6 libm.so.6 libresolv.so.2 libcrypt.so.1 libpthread.so.0; do
    copy_lib "$so"
done

# (Bluetooth firmware was staged into the initramfs here for an early btusb
# probe; removed 2026-08 with Bluetooth — the kernel has no btusb driver to
# request it, so there is nothing to stage. See docs/SECURITY-MODEL.md.)

install -m 0755 "$INIT_SRC" "$IRD/init"
( cd "$IRD" && find . -print0 | cpio --null -o -H newc --quiet | gzip -9 ) > "$GRAFT/live/initrd.img"

# ---- 3. kernel + install payload ----------------------------------------
say "staging kernel + install payload"
cp "$KERNEL" "$GRAFT/live/bzImage"
cp "$KERNEL" "$GRAFT/install/bzImage"
cp "$ROOTFSTAR" "$GRAFT/install/rootfs.tar"
cp "$INSTALL_EFI" "$GRAFT/install/BOOTX64.EFI"

# Secure Boot payload: shim + Debian-signed grub + MokManager + our cert + the
# signed kernel (overrides the plain /install/bzImage). The installer writes
# this whole chain onto the target ESP when it finds shimx64.efi here.
if [ -n "${SB_PAYLOAD:-}" ] && [ -d "$SB_PAYLOAD" ]; then
    say "staging Secure Boot payload"
    cp "$SB_PAYLOAD/shimx64.efi" "$GRAFT/install/shimx64.efi"
    cp "$SB_PAYLOAD/grubx64.efi" "$GRAFT/install/grubx64.efi"
    cp "$SB_PAYLOAD/mmx64.efi"   "$GRAFT/install/mmx64.efi"
    cp "$SB_PAYLOAD/MOK.cer"     "$GRAFT/install/MOK.cer"
    cp "$SB_PAYLOAD/grub.cfg"    "$GRAFT/install/grub.cfg"
    cp "$SB_PAYLOAD/bzImage"     "$GRAFT/install/bzImage"
fi

# ---- 4. live GRUB menu --------------------------------------------------
cat > "$GRAFT/boot/grub/grub.cfg" <<EOF
set default=0
set timeout=3
insmod all_video
insmod gfxterm
insmod part_gpt
insmod iso9660

menuentry "Notebook OS (live)" {
    search --no-floppy --set=root --label ${LABEL}
    linux /live/bzImage boot=live nb.live=1 console=tty1 loglevel=5
    initrd /live/initrd.img
}
menuentry "Notebook OS (live — verbose + serial console)" {
    search --no-floppy --set=root --label ${LABEL}
    linux /live/bzImage boot=live nb.live=1 console=tty1 console=ttyS0,115200 loglevel=7
    initrd /live/initrd.img
}
menuentry "Notebook OS (live — no Intel graphics driver)" {
    search --no-floppy --set=root --label ${LABEL}
    linux /live/bzImage boot=live nb.live=1 module_blacklist=i915 console=tty1 loglevel=5
    initrd /live/initrd.img
}
menuentry "Notebook OS (live — software rendering / safe graphics)" {
    search --no-floppy --set=root --label ${LABEL}
    linux /live/bzImage boot=live nb.live=1 nomodeset console=tty1 loglevel=5
    initrd /live/initrd.img
}
EOF

# ---- 5. build the hybrid ISO --------------------------------------------
say "running grub-mkrescue -> $ISO"
grub-mkrescue --compress=xz -o "$ISO" "$GRAFT" -- -volid "$LABEL" >/dev/null 2>"$WORK/grub.log" \
    || { cat "$WORK/grub.log" >&2; die "grub-mkrescue failed"; }

# ---- 5b. make the LIVE medium Secure Boot bootable -----------------------
# grub-mkrescue writes its own UNSIGNED EFI image (/efi.img in the ISO), and a
# Secure Boot machine refuses it outright -- the firmware never even reaches
# GRUB, it just says "Access Denied" and falls through to "No bootable device".
# Verified against OVMF_CODE_4M.secboot.fd with the Microsoft keys enrolled.
#
# So when a Secure Boot payload exists, swap two things inside the finished ISO:
#   * /efi.img       -> a FAT16 ESP holding shim (MS-signed) + Debian-signed
#                       GRUB (it carries the shim_lock verifier) + MokManager
#   * /live/bzImage  -> the MOK-signed kernel
# and add /EFI/debian/grub.cfg, which is where Debian's signed GRUB looks.
#
# TRAPS, both of which cost a full test cycle:
#   * The ESP must be FAT16. An 8 MB volume formatted FAT32 (mformat -F) is not
#     a valid FAT32 filesystem, and the firmware reports "Not Found" -- which
#     reads like a missing file, not a broken filesystem.
#   * xorriso -extract restores files READ-ONLY. Without chmod -R u+w the
#     replacement copies fail and you re-master the ORIGINAL image, which then
#     "proves" the fix does not work.
#
# The kernel is signed with our own MOK, which no machine trusts yet, so the
# first Secure Boot start stops at GRUB with "you need to load the kernel
# first". That is expected: the user runs "Enroll Secure Boot key" once, which
# chainloads MokManager, and the OS boots from then on. Every third-party
# distribution requires this same one-time enrolment.
if [ -n "${SB_PAYLOAD:-}" ] && [ -d "$SB_PAYLOAD" ] && command -v mformat >/dev/null 2>&1; then
    say "5b/6 making the live ISO Secure Boot bootable (shim + signed grub)"
  # The whole step is an enhancement on top of an ISO that already boots.
  # Run it in a subshell so no failure inside can abort the release.
  if ! (
    SBW="$WORK/sb"; rm -rf "$SBW"; mkdir -p "$SBW"

    # Do NOT hand-copy the menu here. The Secure Boot path must run the SAME
    # entries as the normal one, and a duplicated copy silently drifted once:
    # it omitted nb.live=1, so GRUB said "Booting Notebook OS (live)" and the
    # kernel then stalled with no display. Source the real menu instead, and
    # only ADD the enrolment entry.
    cat > "$SBW/grub.cfg" <<SBCFG
search --no-floppy --set=root --label ${LABEL}
source /boot/grub/grub.cfg
menuentry "Enroll Secure Boot key" {
    search --no-floppy --set=root --file /EFI/BOOT/mmx64.efi
    chainloader /EFI/BOOT/mmx64.efi
}
SBCFG

    dd if=/dev/zero of="$SBW/efi.img" bs=1M count=16 status=none
    mformat -i "$SBW/efi.img" -v NBOSEFI ::
    mmd -i "$SBW/efi.img" ::/EFI ::/EFI/BOOT ::/EFI/debian
    mcopy -i "$SBW/efi.img" "$SB_PAYLOAD/shimx64.efi" ::/EFI/BOOT/BOOTX64.EFI
    mcopy -i "$SBW/efi.img" "$SB_PAYLOAD/grubx64.efi" ::/EFI/BOOT/grubx64.efi
    mcopy -i "$SBW/efi.img" "$SB_PAYLOAD/mmx64.efi"   ::/EFI/BOOT/mmx64.efi
    mcopy -i "$SBW/efi.img" "$SB_PAYLOAD/MOK.cer"     ::/EFI/BOOT/MOK.cer
    mcopy -i "$SBW/efi.img" "$SBW/grub.cfg"           ::/EFI/debian/grub.cfg
    mcopy -i "$SBW/efi.img" "$SBW/grub.cfg"           ::/EFI/BOOT/grub.cfg

    xorriso -osirrox on -indev "$ISO" -extract / "$SBW/graft" >/dev/null 2>&1
    # -extract restores READ-ONLY files; without this the copies below fail
    # silently and the ISO is re-mastered unchanged.
    chmod -R u+w "$SBW/graft"
    cp "$SBW/efi.img" "$SBW/graft/efi.img"
    # `[ -f x ] && cp` would return non-zero when absent and, under
    # `set -e`, abort the whole release build. Keep it an if.
    if [ -f "$SB_PAYLOAD/bzImage" ]; then
        cp "$SB_PAYLOAD/bzImage" "$SBW/graft/live/bzImage"
    else
        say "     WARNING: no signed kernel in the payload; Secure Boot will stop at GRUB"
    fi
    mkdir -p "$SBW/graft/EFI/debian"
    cp "$SBW/grub.cfg" "$SBW/graft/EFI/debian/grub.cfg"

    # -isohybrid-mbr is what makes the ISO WRITABLE TO A USB STICK. Without it
    # the image is pure ISO9660: no MBR boot code, no signature at offset 510,
    # so firmware copying it byte-for-byte onto a stick sees nothing bootable
    # and silently skips the device. grub-mkrescue puts that MBR in for us
    # above — and this re-master, which exists only to swap in the signed EFI
    # payload, was throwing it away again. (-isohybrid-gpt-basdat alone only
    # declares the EFI image as a GPT partition; it writes no MBR.)
    #
    # Measured on the shipped ISO before this line existed: bytes 510-511 were
    # not 55 AA and there was no partition table, while a Debian ISO of the
    # same era has both. Optical media and the .img were unaffected — this only
    # ever broke the USB path, which is the one everybody actually uses.
    HYBRID_MBR=
    for c in /usr/lib/grub/i386-pc/boot_hybrid.img \
             /usr/share/grub/i386-pc/boot_hybrid.img; do
        [ -f "$c" ] && { HYBRID_MBR="$c"; break; }
    done
    [ -n "$HYBRID_MBR" ] || say "     WARNING: no boot_hybrid.img; the ISO will not boot from a USB stick"

    xorriso -as mkisofs -o "$SBW/sb.iso" -V "$LABEL" -J -r \
        ${HYBRID_MBR:+-isohybrid-mbr "$HYBRID_MBR"} \
        -b boot/grub/i386-pc/eltorito.img -no-emul-boot \
        -boot-load-size 4 -boot-info-table --grub2-boot-info \
        -eltorito-alt-boot -e efi.img -no-emul-boot -isohybrid-gpt-basdat \
        "$SBW/graft" >/dev/null 2>"$WORK/sbiso.log" \
        && mv -f "$SBW/sb.iso" "$ISO" \
        && say "     Secure Boot chain in place (enrol the key once on first start)" \
        || { cat "$WORK/sbiso.log" >&2; say "     WARNING: Secure Boot re-master failed, keeping the unsigned ISO"; }
    rm -rf "$SBW/graft"
  ); then
      say "     WARNING: Secure Boot step failed; the ISO is unchanged and still boots"
  fi
fi

SZ="$(du -h "$ISO" | cut -f1)"
say "done: $ISO ($SZ)"
say "boot test:  tools/run-uefi.sh (point it at the ISO) or:  qemu-system-x86_64 -cdrom '$ISO' -m 2048 -bios /usr/share/OVMF/OVMF_CODE.fd"
