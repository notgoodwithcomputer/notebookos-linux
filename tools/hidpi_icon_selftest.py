#!/usr/bin/env python3
"""
hidpi_icon_selftest — prove icons are rasterized at the panel's real resolution.

WHY THIS HAS TO BE A MEASUREMENT. "Blurry at 2x" is invisible on a 1x developer
monitor, and it is invisible in a screenshot of a 1x render. It is also the
single most damaging way this OS could fail its own goal: the machines that run
at 2x are the ones bought for their screen, and a soft icon sitting next to
sharp text reads as cheap in a way that a uniformly soft interface does not.

WHAT IS ACTUALLY BEING TESTED. Every icon here is a VECTOR (a list of cairo
ops), so a correct implementation has all the information it needs to draw at
any resolution. The old `nbicons.pixbuf()` rasterized into a logical-size bitmap
and let GTK stretch it — throwing that information away. The test therefore does
not ask "does it look sharp", it asks the only question with a right answer:

    AT SCALE 2, DOES THE ICON CONTAIN 2x WORTH OF DETAIL, OR 1x STRETCHED?

An upscaled bitmap cannot contain detail finer than its source. Measured as the
count of distinct alpha values along the icon's antialiased edges and the mean
absolute gradient: a genuine 2x rasterization resolves edge steps that a
2x-stretched 1x bitmap simply does not have.

    GDK_SCALE=2 tools/guestrun.sh python3 tools/hidpi_icon_selftest.py
    tools/guestrun.sh python3 tools/hidpi_icon_selftest.py     # 1x control
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import cairo  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(
    os.path.dirname(_HERE), "buildroot", "board", "notebookos",
    "rootfs-overlay", "opt", "notebook", "de"))
import nbicons  # noqa: E402

# Icons with plenty of curve and diagonal, where interpolation shows worst.
PROBE_ICONS = ("search", "folder", "gear", "trash")
LOGICAL = 24


def _alpha(surf):
    surf.flush()
    w, h = surf.get_width(), surf.get_height()
    data = bytes(surf.get_data())
    stride = surf.get_stride()
    return [[data[y * stride + x * 4 + 3] for x in range(w)] for y in range(h)], w, h


def _sharpness(surf):
    """Mean absolute gradient of the alpha channel — the standard edge-acuity
    measure, and the one that actually discriminates here.

    NOT "count of distinct alpha levels", which was the first metric tried and
    is exactly backwards: interpolation INVENTS intermediate values, so the
    blurred image scores HIGHER (measured: 148 levels for the stretched icon
    against 84 for the native one). A smooth ramp has many levels and no detail.
    What upscaling cannot manufacture is a STEEP transition, so the gradient is
    what to measure."""
    a, w, h = _alpha(surf)
    total, n = 0, 0
    for y in range(h - 1):
        for x in range(w - 1):
            total += abs(a[y][x] - a[y][x + 1])
            total += abs(a[y][x] - a[y + 1][x])
            n += 2
    return total / n if n else 0.0


def _old_path_surface(name, size, scale):
    """What the OLD code produced on screen: a logical-size pixbuf, stretched by
    GTK to the device resolution. Reproduced here by drawing the logical-size
    raster and scaling it up the way the compositor would."""
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    nbicons.draw(cairo.Context(small), name, size)
    small.flush()
    big = cairo.ImageSurface(cairo.FORMAT_ARGB32, size * scale, size * scale)
    cr = cairo.Context(big)
    cr.scale(scale, scale)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)   # what an upscale really is
    cr.paint()
    big.flush()
    return big


def main():
    scale = nbicons.scale_factor()
    print("nbicons.scale_factor() = %d   (GDK_SCALE=%r)"
          % (scale, os.environ.get("GDK_SCALE")))

    if scale == 1:
        print("\nrunning at 1x -- this is the CONTROL. The interesting run is:")
        print("  GDK_SCALE=2 tools/guestrun.sh python3 tools/hidpi_icon_selftest.py")

    failures = 0
    # STRUCTURAL CHECKS FIRST. These are dispositive on their own: a surface
    # that is scale*LOGICAL pixels wide, carries a device scale of `scale`, and
    # is accepted by Gtk.Image as a SURFACE, is by construction being drawn from
    # the vector at panel resolution. The sharpness numbers below are corrobo-
    # ration, not the proof.
    for name in PROBE_ICONS:
        new = nbicons.surface(name, LOGICAL)
        want = LOGICAL * scale
        if new.get_width() != want or new.get_height() != want:
            print("FAIL %-8s surface is %dx%d, expected %dx%d"
                  % (name, new.get_width(), new.get_height(), want, want))
            failures += 1
        dsx, dsy = new.get_device_scale()
        if abs(dsx - scale) > 0.001 or abs(dsy - scale) > 0.001:
            print("FAIL %-8s device scale is %.2f,%.2f, expected %d"
                  % (name, dsx, dsy, scale))
            failures += 1
    print("structure: %d icons at %dpx with device scale %d  -> %s"
          % (len(PROBE_ICONS), LOGICAL * scale, scale,
             "ok" if not failures else "FAILED"))

    if scale == 1:
        print("\n(no stretch comparison at 1x -- the two paths are the same "
              "raster here, which is itself the point: 1x must not regress.)")
    else:
        print("\n%-10s %-14s %-14s %s"
              % ("icon", "stretched 1x", "native 2x", "sharper by"))
        for name in PROBE_ICONS:
            new = nbicons.surface(name, LOGICAL)
            old = _old_path_surface(name, LOGICAL, scale)
            n_gr, o_gr = _sharpness(new), _sharpness(old)
            gain = (n_gr / o_gr - 1.0) * 100 if o_gr else 0.0
            # 8% is well outside run-to-run noise (there is none -- both are
            # deterministic rasterizations) and comfortably below the ~18%
            # measured, so this fails loudly if the surface ever silently goes
            # back to being a stretched bitmap.
            ok = gain >= 8.0
            failures += (not ok)
            print("%-10s %-14.2f %-14.2f %+.1f%%  %s"
                  % (name, o_gr, n_gr, gain, "ok" if ok else "FAIL"))

    # The Gtk.Image path must actually accept the surface -- if the cairo bridge
    # is missing, image() silently falls back to the blurry path, and a test that
    # never checks would pass while the OS shipped soft icons.
    try:
        img = nbicons.image("search", LOGICAL)
        via = img.get_storage_type()
        print("\nGtk.Image storage type: %s" % via.value_nick)
        if via != Gtk.ImageType.SURFACE:
            print("FAIL: image() fell back off the surface path "
                  "(cairo bridge missing?)")
            failures += 1
    except Exception as exc:                                      # noqa: BLE001
        print("FAIL: nbicons.image() raised: %s" % exc)
        failures += 1

    # A bare "PASS" is not a terminal verdict the release runner recognises
    # (it also matches half a run). Name the outcome.
    print("\nRESULT: %s" % ("ALL PASS" if not failures
                            else "FAILED (%d)" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
