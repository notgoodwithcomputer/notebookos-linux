#!/usr/bin/env python3
"""
accel_selftest — prove opt/notebook/accel.sh only claims hardware rendering when
Mesa can actually deliver it.

WHY. NB_ACCEL is a single 0/1 that decides three things at once (compositor on,
compositor vsync, xflushd paint-helper off). The failure it exists to prevent is
silent and only appears on hardware the developer does not own: an AMD or NVIDIA
laptop where the kernel binds a GPU driver, Mesa has no driver for it, and the
desktop therefore runs software rendering with the software path's help removed.
Nothing logs it. The user just gets a desktop that stutters.

METHOD. accel.sh reads exactly three things — the DRM sysfs tree, the Mesa DRI
driver directory, and the kernel cmdline — and every one is overridable, so the
whole matrix can be built as fixtures on disk. No GPU required.

    python3 tools/accel_selftest.py
"""
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ACCEL_SH = os.path.join(
    os.path.dirname(_HERE), "buildroot", "board", "notebookos",
    "rootfs-overlay", "opt", "notebook", "accel.sh")
XFLUSHD = os.path.join(os.path.dirname(ACCEL_SH), "de", "xflushd.py")

# What this image ACTUALLY ships, verified against
# output/target/usr/lib/dri after the Mesa rebuild. Kept here as the realistic
# default so the cases read as "this laptop, this image".
#   crocus     Intel gen4-7 (2006-2013)
#   iris       Intel gen8+ / Xe
#   radeonsi   AMD (needs LLVM built with the AMDGPU target)
#   nouveau    NVIDIA
#   virtio_gpu VMs, and only with host GL
#   swrast     llvmpipe -- the fast software rasterizer, and the fallback for
#              anything above that is missing
SHIPPED = ["iris", "crocus", "radeonsi", "nouveau", "virtio_gpu",
           "swrast", "kms_swrast"]
# What it shipped BEFORE, which is the configuration the old detection was
# wrong about. Retained so the regression stays covered.
OLD_IMAGE = ["iris", "virtio_gpu", "swrast", "kms_swrast"]

# (label, kernel driver, installed dri drivers, cmdline, expected)
CASES = [
    # --- the machines the old kernel-name-only test got WRONG ----------------
    ("AMD laptop, no radeonsi in image",   "amdgpu",  OLD_IMAGE, "", "0"),
    ("NVIDIA laptop, no nouveau in image", "nouveau", OLD_IMAGE, "", "0"),
    ("Sandy Bridge i915, only iris",       "i915",    ["iris"],  "", "1"),

    # --- the same machines once Mesa can actually drive them -----------------
    ("AMD laptop, radeonsi present",       "amdgpu",  SHIPPED,   "", "1"),
    ("NVIDIA laptop, nouveau present",     "nouveau", SHIPPED,   "", "1"),
    ("old Intel, crocus present",          "i915",    SHIPPED,   "", "1"),

    # --- straightforwardly correct before and after --------------------------
    ("modern Intel (iris)",                "i915",    SHIPPED,   "", "1"),
    ("Intel Xe (Lunar Lake)",              "xe",      SHIPPED,   "", "1"),
    ("simpledrm firmware framebuffer",     "simple-framebuffer", SHIPPED, "", "0"),
    ("unknown GPU driver",                 "some_gpu", SHIPPED,  "", "0"),

    # --- overrides -----------------------------------------------------------
    ("nb.accel=1 forces on over simpledrm", "simple-framebuffer", SHIPPED,
     "root=/dev/sda1 nb.accel=1", "1"),
    ("nb.accel=0 forces off over Intel",   "i915", SHIPPED,
     "root=/dev/sda1 nb.accel=0", "0"),
]


def run_case(kernel_drv, dri, cmdline, tmpdir):
    drm = os.path.join(tmpdir, "drm")
    dridir = os.path.join(tmpdir, "dri")
    for d in (drm, dridir):
        # rebuild each time so cases cannot leak into one another
        if os.path.exists(d):
            for root, dirs, files in os.walk(d, topdown=False):
                for f in files:
                    os.unlink(os.path.join(root, f))
                for s in dirs:
                    p = os.path.join(root, s)
                    (os.unlink if os.path.islink(p) else os.rmdir)(p)
            os.rmdir(d)
        os.makedirs(d)

    # /sys/class/drm/card0/device/driver -> .../drivers/<name>
    card = os.path.join(drm, "card0", "device")
    os.makedirs(card)
    target = os.path.join(tmpdir, "drivers", kernel_drv)
    os.makedirs(target, exist_ok=True)
    os.symlink(target, os.path.join(card, "driver"))

    for name in dri:
        open(os.path.join(dridir, "%s_dri.so" % name), "w").close()

    cmdfile = os.path.join(tmpdir, "cmdline")
    with open(cmdfile, "w") as fh:
        fh.write(cmdline + "\n")

    env = dict(os.environ)
    env.update(NB_SYS_DRM=drm, NB_DRI_DIR=dridir, NB_CMDLINE=cmdfile)
    p = subprocess.run(["sh", ACCEL_SH], env=env, capture_output=True,
                       text=True, timeout=30)
    if p.returncode != 0:
        return "<exit %d: %s>" % (p.returncode, p.stderr.strip()[:100])
    return p.stdout.strip()


def main():
    if not os.path.exists(ACCEL_SH):
        print("accel.sh not found at %s" % ACCEL_SH)
        return 1
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for label, drv, dri, cmdline, expected in CASES:
            got = run_case(drv, dri, cmdline, tmpdir)
            ok = (got == expected)
            failures += (not ok)
            print("%s  %-40s expect %s got %s" % (
                "ok  " if ok else "FAIL", label, expected, got))
    with open(XFLUSHD, encoding="utf-8") as fh:
        flush_src = fh.read()
    fallback_ok = "renderD128" not in flush_src.split("_accel =", 1)[-1]
    failures += not fallback_ok
    print("%s  xflushd runs when acceleration is unknown" %
          ("ok  " if fallback_ok else "FAIL"))
    force_ok = ('accel == "1" and os.environ.get("NB_XFLUSHD_FORCE") '
                '!= "1"' in flush_src)
    failures += not force_ok
    print("%s  compositor failure can force xflushd under acceleration" %
          ("ok  " if force_ok else "FAIL"))
    total = len(CASES) + 2
    print("\n%d/%d passed" % (total - failures, total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
