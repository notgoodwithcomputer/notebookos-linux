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

SZ="$(du -h "$ISO" | cut -f1)"
say "done: $ISO ($SZ)"
say "boot test:  tools/run-uefi.sh (point it at the ISO) or:  qemu-system-x86_64 -cdrom '$ISO' -m 2048 -bios /usr/share/OVMF/OVMF_CODE.fd"
