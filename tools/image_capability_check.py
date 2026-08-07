#!/usr/bin/env python3
"""
What the SHIPPED image can actually do.

Every other gate here runs app code against the developer's libraries. This one
asks a different question: does the thing that gets burned to a stick carry the
pieces the apps depend on? A fix can be perfect and still be dead on the guest
because a package was never selected.

It exists because two ROADMAP entries were wrong in exactly that direction, both
by reading a config or a directory rather than the built artefact:

  #10  "gdk-pixbuf built with JPEG disabled." BR2_PACKAGE_JPEG=y is set, and
       the shipped libgdk_pixbuf links libjpeg.so.8 directly. What misleads is
       the loaders directory: modern gdk-pixbuf compiles PNG and JPEG INTO the
       library instead of shipping loader modules, so `ls .../loaders/` shows
       every format EXCEPT the two that always work.
  #9   "exFAT and NTFS are not built, so no modern USB stick mounts." The
       buildroot PACKAGES (exfat-utils, ntfs-3g) are indeed off — but those are
       formatting tools, not drivers. The kernel that ships has CONFIG_EXFAT_FS
       and CONFIG_NTFS3_FS built in, and automount.sh calls `mount` with no -t,
       so the kernel picks the filesystem itself.

The kernel checked is the one `mkrelease.sh` actually builds — `kbuild-desktop`,
NOT `kbuild`, whose config genuinely does disable exFAT. Checking the wrong tree
would have "confirmed" the entry.

Run:
  python3 image_capability_check.py
  python3 image_capability_check.py -v
Exit status is nonzero if the image is missing something an app relies on.
"""
import os
import re
import sys
import glob
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, "buildroot", "output", "target")
if "--target" in sys.argv:
    TARGET = os.path.abspath(sys.argv[sys.argv.index("--target") + 1])
# The kernel mkrelease.sh builds. kbuild/ is a different tree with a different
# config; reading it would answer a question nobody asked.
KCONFIG = os.path.join(ROOT, "kbuild-desktop", ".config")
if "--kconfig" in sys.argv:
    KCONFIG = os.path.abspath(sys.argv[sys.argv.index("--kconfig") + 1])

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def on_target(*rel):
    return os.path.join(TARGET, *rel)


def links_against(lib_glob, needed):
    """Is a shipped .so linked against `needed`? Answers the question the
    loaders directory cannot."""
    hits = glob.glob(on_target(*lib_glob))
    if not hits:
        return None
    out = subprocess.run(["readelf", "-d", hits[0]],
                         capture_output=True, text=True).stdout
    return needed in out


def kconfig():
    try:
        with open(KCONFIG, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def main():
    verbose = "-v" in sys.argv
    if not os.path.isdir(TARGET):
        print("no built target at %s — nothing to check" % TARGET)
        return 2

    print("target: %s" % TARGET)
    print("kernel: %s\n" % KCONFIG)

    # ---- pictures (ROADMAP #10) --------------------------------------
    jpeg = links_against(("usr", "lib", "libgdk_pixbuf-2.0.so.0"), "libjpeg")
    png = links_against(("usr", "lib", "libgdk_pixbuf-2.0.so.0"), "libpng")
    if jpeg is None:
        check("gdk-pixbuf is on the image", False)
    else:
        check("gdk-pixbuf is on the image", True)
        check("...and can open JPEG (built in, not a loader module)", jpeg)
        check("...and PNG", png)

    # ---- removable media (ROADMAP #9) --------------------------------
    kc = kconfig()
    check("the shipped kernel config is readable", bool(kc))
    if kc:
        for opt, why in (("CONFIG_VFAT_FS", "the format every stick still uses"),
                         ("CONFIG_EXFAT_FS", "anything over 4GB, and most cards"),
                         ("CONFIG_NTFS3_FS", "a stick that has been near Windows")):
            check("the kernel mounts %s — %s" % (opt.replace("CONFIG_", "")
                                                 .replace("_FS", ""), why),
                  re.search(r"^%s=[ym]$" % opt, kc, re.M) is not None)

    # ---- the clock survives a reboot (task 008) ----------------------
    check("hwclock is on the image, so Set Clock can write the RTC",
          os.path.exists(on_target("sbin", "hwclock")))
    if kc:
        check("the kernel has an RTC driver to write to",
              re.search(r"^CONFIG_RTC_DRV_CMOS=[ym]$", kc, re.M) is not None)

    # ---- video playback (task 013) -----------------------------------
    gst = on_target("usr", "lib", "gstreamer-1.0")
    have = set(os.listdir(gst)) if os.path.isdir(gst) else set()
    if verbose and have:
        print("      gstreamer plugins: %s" % ", ".join(sorted(have)))
    for so, why in (("libgstplayback.so", "playbin — the video transport"),
                    ("libgstgtk.so", "gtksink — the surface the editor packs"),
                    ("libgstlibav.so", "the decoders"),
                    ("libgstisomp4.so", "mp4/mov"),
                    ("libgstalsa.so", "sound out"),
                    ("libgstvideoconvertscale.so", "colour + scaling")):
        check("gstreamer: %s (%s)" % (so, why), so in have)

    # ---- fonts the reader needs --------------------------------------
    fonts = glob.glob(on_target("usr", "share", "fonts", "**", "*.ttf"),
                      recursive=True)
    check("fonts are on the image (%d)" % len(fonts), len(fonts) > 0)

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: THE IMAGE IS MISSING SOMETHING")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: the image carries everything the apps rely on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
