#!/usr/bin/env python3
"""Check that an ISO still carries everything it needs in order to boot.

Written for one specific failure. tools/mkiso.sh re-masters the finished ISO
more than once — to swap in the Secure Boot payload, and to add the map packs —
and a re-master that forgets `-isohybrid-mbr` (or `-boot_image any replay`)
produces an image that is still a perfectly valid ISO9660 filesystem, still
mounts, still burns to a DVD, and **silently will not boot from a USB stick**,
because firmware copying it byte-for-byte onto a stick finds no MBR boot code
and no 0x55AA signature and skips the device. That shipped once already.

Nothing in "the build succeeded" can see that, so this reads the bytes:

  * 0x55AA at offset 510          the boot signature firmware looks for
  * non-zero code in bytes 0..440 the isohybrid MBR boot program itself
  * an 0xEF partition entry       the EFI System partition (UEFI firmware)
  * "EFI PART" at LBA 1           the GPT the UEFI path is declared through
  * ISO9660 primary descriptor   rejects marker-shaped non-images
  * El Torito, BIOS *and* UEFI    the optical/emulated boot catalogue

Run as:  tools/iso_boot_check.py IMAGE.iso
Exits 0 when every check passes, 1 otherwise. El Torito needs xorriso on PATH;
without it the release check fails closed. A missing inspection tool is not
evidence that an image is safe to ship.
"""
import os
import struct
import subprocess
import sys

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def valid_efi_partition(part, image_sectors):
    typ, start, count = part
    return (typ == 0xEF and start > 0 and count > 0
            and start + count <= image_sectors)


def main(path):
    if not os.path.isfile(path):
        print("FAIL no such image: %s" % path)
        return 1
    print("image: %s (%.1f MB)\n" % (path, os.path.getsize(path) / 1e6))

    with open(path, "rb") as fh:
        mbr = fh.read(512)
        gpt_sig = fh.read(8)
        fh.seek(16 * 2048)
        pvd = fh.read(7)

    check("boot signature 0x55AA at offset 510",
          mbr[510:512] == b"\x55\xaa", mbr[510:512].hex() or "(image too short)")
    # The bootstrap program itself. An ISO re-mastered without -isohybrid-mbr
    # keeps the signature but zeroes this, which is the exact shape of the bug.
    check("isohybrid MBR boot code present",
          any(mbr[:440]), "bytes 0..440 are all zero — a USB stick will not boot")

    parts = []
    for i in range(4):
        ent = mbr[446 + 16 * i:446 + 16 * (i + 1)]
        if any(ent):
            parts.append((ent[4],
                          struct.unpack("<I", ent[8:12])[0],
                          struct.unpack("<I", ent[12:16])[0]))
    for typ, start, count in parts:
        print("     partition type=0x%02x start=%d sectors=%d" % (typ, start, count))
    image_sectors = os.path.getsize(path) // 512
    check("a nonempty in-image EFI System partition (type 0xEF) is declared",
          any(valid_efi_partition(part, image_sectors) for part in parts),
          "partition types found: %s" % [hex(t) for t, _s, _c in parts])
    check("GPT header at LBA 1", gpt_sig == b"EFI PART", gpt_sig)
    check("ISO9660 primary volume descriptor is present",
          len(pvd) == 7 and pvd[0] == 1 and pvd[1:6] == b"CD001",
          pvd or b"(image too short)")

    try:
        proc = subprocess.run(["xorriso", "-indev", path,
                               "-report_el_torito", "plain"],
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        check("xorriso can inspect the El Torito catalogue", False, exc)
        proc = None
    if proc is not None:
        check("xorriso can inspect the El Torito catalogue",
              proc.returncode == 0, proc.stderr.strip() or "xorriso failed")
        out = proc.stdout if proc.returncode == 0 else ""
        imgs = [ln for ln in out.splitlines() if "El Torito boot img" in ln]
        for ln in imgs:
            print("     " + ln.strip())
        check("El Torito carries a BIOS boot image",
              any(" BIOS " in ln for ln in imgs), "none found")
        check("El Torito carries a UEFI boot image",
              any(" UEFI " in ln for ln in imgs), "none found")

    print("\n%d checks, %d passed, %d failed"
          % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
    ok = all(RESULTS)
    print("RESULT: " + ("BOOTABLE" if ok else "NOT BOOTABLE — DO NOT SHIP"))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
