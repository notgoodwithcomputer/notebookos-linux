#!/usr/bin/env bash
# tools/mkrelease.sh — one command to build a Notebook OS release.
#
# Orchestrates the whole pipeline into a clean release/ dir:
#   1. build the kernel  (make -C kbuild-desktop bzImage)
#   2. build userspace   (make -C buildroot)  -> rootfs.ext4 + rootfs.tar
#   3. build the installed-system GRUB EFI (grub-mkstandalone, root=PARTUUID)
#   4. build the raw UEFI disk image (tools/mkimage-uefi.sh)  [bonus artifact]
#   5. build the Live ISO (tools/mkiso.sh)  [primary artifact]
#   6. stamp release/BUILD_MANIFEST.txt with versions + checksums
#
# Flags:  --no-kernel  --no-buildroot  --no-image  --iso-only (implies both --no-*)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Buildroot invokes this file as a rootfs post-build hook.  Installing modules
# here is deliberately after packages/firmware and before rootfs images are
# generated, so PCI coldplug can find both modules and firmware on first boot.
if [ "${1:-}" = "$ROOT/buildroot/output/target" ]; then
    TARGET_DIR=$1
    KBUILD="$ROOT/kbuild-desktop"
    KREL="$(make -s -C "$KBUILD" kernelrelease)"
    make -C "$KBUILD" modules_install INSTALL_MOD_PATH="$TARGET_DIR"
    # depmod lives in /sbin on most hosts, and buildroot's hook environment
    # (and an unprivileged user shell) may not carry sbin on PATH — which made
    # this check die on a machine that has depmod perfectly well. Resolve it
    # with sbin appended rather than trusting the inherited PATH.
    DEPMOD="$(PATH="$PATH:/sbin:/usr/sbin" command -v depmod || true)"
    [ -n "$DEPMOD" ] || {
        echo "mkrelease: host depmod is required to package kernel modules" >&2
        exit 1
    }
    "$DEPMOD" -b "$TARGET_DIR" "$KREL"

    # Buildroot 2024.02 can select all AMDGPU firmware, but has no NVIDIA
    # selector.  Add the vendored Turing tu10x and Ampere ga10x GSP/ancillary
    # directories from the same pinned linux-firmware package.
    FW_SRC=$(find "$ROOT/buildroot/output/build" -maxdepth 1 -type d \
        -name 'linux-firmware-*' -print -quit)
    [ -n "$FW_SRC" ] || {
        echo "mkrelease: vendored linux-firmware build directory is missing" >&2
        exit 1
    }
    mkdir -p "$TARGET_DIR/lib/firmware/nvidia"
    for family in "$FW_SRC"/nvidia/tu10* "$FW_SRC"/nvidia/ga10*; do
        [ -e "$family" ] || continue
        cp -a "$family" "$TARGET_DIR/lib/firmware/nvidia/"
    done
    exit 0
fi

cd "$ROOT"

VER="${NB_VERSION:-1.0}"
FIXED_PARTUUID="b8e5a5f2-1a2b-4c3d-9e8f-000000000042"
OUTDIR="$ROOT/release"
STAGE="$OUTDIR/staging"
KERNEL="$ROOT/kbuild-desktop/arch/x86/boot/bzImage"
ROOTFS="$ROOT/buildroot/output/images/rootfs.ext4"
ROOTFSTAR="$ROOT/buildroot/output/images/rootfs.tar"

DO_KERNEL=1; DO_BUILDROOT=1; DO_IMAGE=1
for a in "$@"; do case "$a" in
    --no-kernel)    DO_KERNEL=0 ;;
    --no-buildroot) DO_BUILDROOT=0 ;;
    --no-image)     DO_IMAGE=0 ;;
    --iso-only)     DO_KERNEL=0; DO_BUILDROOT=0 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
esac; done

say()  { printf '\n\033[1;36m=== [mkrelease] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[mkrelease] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v grub-mkstandalone >/dev/null 2>&1 || die "grub-mkstandalone missing (grub-efi-amd64-bin)"
[ -f "$ROOT/tools/mkiso.sh" ] || die "tools/mkiso.sh missing"

mkdir -p "$OUTDIR" "$STAGE"

# ---- 1. kernel ----------------------------------------------------------
if [ "$DO_KERNEL" = 1 ]; then
    say "1/6 building kernel"
    make -C "$ROOT/kbuild-desktop" bzImage -j"$(nproc)"
else say "1/6 kernel build SKIPPED"; fi
[ -f "$KERNEL" ] || die "kernel missing: $KERNEL"
KREL="$(cat "$ROOT/kbuild-desktop/include/config/kernel.release" 2>/dev/null || echo unknown)"

# ---- 2. userspace -------------------------------------------------------
if [ "$DO_BUILDROOT" = 1 ]; then
    say "2/6 building userspace (buildroot)"
    make -C "$ROOT/buildroot"
else say "2/6 buildroot build SKIPPED"; fi
[ -f "$ROOTFS" ]    || die "rootfs image missing: $ROOTFS"
[ -f "$ROOTFSTAR" ] || die "rootfs tarball missing: $ROOTFSTAR"

# ---- 3. installed-system GRUB EFI ---------------------------------------
# The installer copies this onto a fresh ESP; its embedded grub.cfg boots the
# installed ext4 root by the FIXED PARTUUID the installer assigns.
say "3/6 building installed-system GRUB EFI (BOOTX64.EFI)"
INSTALL_GRUBCFG="$STAGE/install-grub.cfg"
cat > "$INSTALL_GRUBCFG" <<EOF
set default=0
set timeout=1
insmod all_video
insmod part_gpt
insmod ext2
menuentry "Notebook OS ${VER}" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=${FIXED_PARTUUID} rw rootwait console=tty1 loglevel=5
}
menuentry "Notebook OS ${VER} (verbose + serial console)" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=${FIXED_PARTUUID} rw rootwait console=tty1 console=ttyS0,115200 loglevel=7
}
menuentry "Notebook OS ${VER} (software rendering / safe graphics)" {
    search --no-floppy --file --set=root /bzImage
    linux /bzImage root=PARTUUID=${FIXED_PARTUUID} rw rootwait nomodeset console=tty1 loglevel=5
}
menuentry "Enroll Secure Boot key (first boot: run once, then reboot)" {
    search --no-floppy --file --set=root /EFI/BOOT/mmx64.efi
    chainloader /EFI/BOOT/mmx64.efi
}
EOF
INSTALL_EFI="$STAGE/BOOTX64.EFI"
grub-mkstandalone -O x86_64-efi -o "$INSTALL_EFI" \
    "boot/grub/grub.cfg=$INSTALL_GRUBCFG"
[ -f "$INSTALL_EFI" ] || die "grub-mkstandalone produced no EFI"

# ---- 3b. Secure Boot payload (shim -> Debian-signed grub -> MOK-signed kernel)
# The installer lays this whole chain on the target ESP when present; otherwise
# it falls back to the unsigned BOOTX64.EFI above. Set NB_SECUREBOOT=0 to skip.
SECUREBOOT="${NB_SECUREBOOT:-1}"
SB_PAYLOAD=""
SB_SHIM=/usr/lib/shim/shimx64.efi.signed
SB_MM=/usr/lib/shim/mmx64.efi.signed
SB_SIGNED_GRUB=/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed
if [ "$SECUREBOOT" = "1" ]; then
    if command -v sbsign >/dev/null 2>&1 && [ -f "$SB_SHIM" ] && [ -f "$SB_SIGNED_GRUB" ]; then
        say "3b/6 building Secure Boot payload (shim + signed grub + signed kernel)"
        "$ROOT/tools/gen-sb-keys.sh" >/dev/null
        KEYDIR="$ROOT/secureboot"
        SB_PAYLOAD="$STAGE/sb-payload"; mkdir -p "$SB_PAYLOAD"
        sbsign --key "$KEYDIR/MOK.key" --cert "$KEYDIR/MOK.crt" \
            --output "$SB_PAYLOAD/bzImage" "$KERNEL"
        cp "$SB_SHIM"         "$SB_PAYLOAD/shimx64.efi"
        cp "$SB_SIGNED_GRUB"  "$SB_PAYLOAD/grubx64.efi"
        cp "$SB_MM"           "$SB_PAYLOAD/mmx64.efi"
        cp "$KEYDIR/MOK.cer"  "$SB_PAYLOAD/MOK.cer"
        cp "$INSTALL_GRUBCFG" "$SB_PAYLOAD/grub.cfg"
        say "    kernel: $(sbverify --cert "$KEYDIR/MOK.crt" "$SB_PAYLOAD/bzImage" 2>&1 | tr '\n' ' ')"
    else
        echo "  (Secure Boot payload SKIPPED — need: shim-signed grub-efi-amd64-signed sbsigntool)"
    fi
fi

# ---- 4. raw UEFI disk image (bonus) -------------------------------------
if [ "$DO_IMAGE" = 1 ] && [ -x "$ROOT/tools/mkimage-uefi.sh" ]; then
    say "4/6 building raw UEFI disk image"
    if "$ROOT/tools/mkimage-uefi.sh"; then
        [ -f "$ROOT/boot-work/notebookos-uefi.img" ] && \
            cp "$ROOT/boot-work/notebookos-uefi.img" "$OUTDIR/notebookos-${VER}.img"
    else echo "  (disk-image step failed — continuing; ISO is the primary artifact)"; fi
else say "4/6 raw disk image SKIPPED"; fi

# ---- 5. Live ISO --------------------------------------------------------
say "5/6 building Live ISO"
NB_VERSION="$VER" KERNEL="$KERNEL" ROOTFSTAR="$ROOTFSTAR" \
    INSTALL_EFI="$INSTALL_EFI" SB_PAYLOAD="$SB_PAYLOAD" OUTDIR="$OUTDIR" \
    bash "$ROOT/tools/mkiso.sh"
ISO="$OUTDIR/notebookos-${VER}.iso"
[ -f "$ISO" ] || die "ISO not produced"

# ---- 6. manifest --------------------------------------------------------
say "6/6 stamping release manifest"
MAN="$OUTDIR/BUILD_MANIFEST.txt"
{
    echo "Notebook OS ${VER} — release manifest"
    echo "built:          $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "kernel release: ${KREL}"
    echo "root PARTUUID:  ${FIXED_PARTUUID}"
    echo "rootfs size:    $(du -hL "$ROOTFS" | cut -f1)"
    echo ""
    echo "artifacts:"
    # if-fi, not `[ -f ] &&`: under set -e the && list's status-1 on a
    # missing OPTIONAL .img (every --no-image build) killed the script right
    # after the sha line — the manifest tail, MAPS-README and the staging
    # tidy silently never ran. Found on the 1.8-composer spins, where BOTH
    # builds died here while looking complete.
    for f in "$ISO" "$OUTDIR/notebookos-${VER}.img"; do
        if [ -f "$f" ]; then
            printf '  %-40s %s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"
        fi
    done
    echo ""
    echo "sha256:"
    ( cd "$OUTDIR" && for f in notebookos-${VER}.iso notebookos-${VER}.img; do
        if [ -f "$f" ]; then sha256sum "$f"; fi
    done )
    echo ""
    echo "starting on a machine with Secure Boot switched on:"
    if [ -n "${SB_PAYLOAD:-}" ] && [ -d "${SB_PAYLOAD:-/nonexistent}" ]; then
        echo "  The USB starts and reaches the boot menu. The kernel is signed"
        echo "  with the Notebook OS key, which the machine does not trust yet,"
        echo "  so the first start stops with:"
        echo "      error: you need to load the kernel first."
        echo "  Choose \"Enroll Secure Boot key\" in the boot menu, accept the key"
        echo "  in the blue MokManager screen, and restart. This is needed once."
        echo "  Switching Secure Boot off in the firmware also works."
    else
        echo "  NOT SUPPORTED in this build -- the Secure Boot payload was not"
        echo "  present, so the boot files are unsigned and the firmware refuses"
        echo "  the USB with \"Access Denied\" / \"No bootable device\"."
        echo "  Switch Secure Boot off in the firmware to start from this medium."
    fi
} | tee "$MAN"

# ---- 6b. companion map packs -------------------------------------------
# The base ISO bundles only the small default (Monaco). Large add-on packs
# (e.g. North America, ~2 GB) ship ALONGSIDE the ISO, not inside it: bundling
# would double-copy them into both the live squashfs and the install tarball
# (~4 GB ISO). The user copies a pack onto the machine (USB) into /data/maps
# or ~/maps, which the Maps app scans. NB_BUNDLE_MAPS=1 can override later.
NA_PACK="$ROOT/mapwork/north-america.nbm2"
if [ -f "$NA_PACK" ]; then
    cp "$NA_PACK" "$OUTDIR/north-america.nbm2"
    say "companion map pack: north-america.nbm2 ($(du -h "$NA_PACK" | cut -f1)) -> $OUTDIR/"
    cat > "$OUTDIR/MAPS-README.txt" <<'MEOF'
Notebook OS — add-on map packs
==============================
The installed system ships with a small default map (Monaco). To add a larger
region, copy its .nbm2 pack onto the machine into one of:

    /data/maps/            (system-wide, all users)
    ~/maps/                (your account)

e.g. copy north-america.nbm2 from this USB to /data/maps/, then open Maps and
pick "North America" from the region menu. Packs are read-only and stream from
disk, so they do not need to be unpacked.
MEOF
fi

# tidy: drop staging, keep the shippable artifacts
rm -rf "$STAGE"

say "RELEASE COMPLETE -> $OUTDIR/"
ls -lh "$OUTDIR"/notebookos-${VER}.* "$MAN" 2>/dev/null || true
