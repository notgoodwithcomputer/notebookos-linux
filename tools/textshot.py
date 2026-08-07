#!/usr/bin/env python3
"""
textshot — a type specimen for Notebook OS, rendered the way the guest renders.

WHY THIS EXISTS. Text rasterization is the single thing that decides whether a
Linux desktop reads as "a Linux desktop" or as something built to the standard
of an iPad. The difference is not the typeface — it is hinting, antialias mode
and stem weight, none of which were configured anywhere in this OS. Judging
those by eye needs two things that no ordinary screenshot gives you:

  1. the SAME faces and the SAME fontconfig rules the guest uses, and
  2. a PIXEL ZOOM, because the whole argument happens inside a 13px glyph and
     is invisible at 1:1 in a screenshot you look at on a different monitor.

So this renders a specimen offscreen (as tools/uishot.py does) and writes both a
1:1 image and a nearest-neighbour blow-up of one line, so the actual pixels —
the grey levels along a stem, the colour fringes if subpixel AA is on, how hard
the baseline is snapped — can be read directly.

    FONTCONFIG_FILE=tools/guest-fonts.conf DISPLAY=:0 \
        python3 tools/textshot.py /tmp/spec.png

Compare two configurations by rendering to two paths and looking at both. That
is the only honest way to tell whether a font change did anything at all: the
settings involved fail SILENTLY and identically (a bad hintstyle name is simply
ignored), so "I set it" is never evidence that it took.
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo  # noqa: E402
import cairo  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_OVERLAY = os.path.join(os.path.dirname(_HERE),
                        "buildroot", "board", "notebookos", "rootfs-overlay")

# The papertone surface and ink, so the specimen is judged on the background it
# will actually be read on. Antialiasing quality is contrast-dependent: light
# grey text on white and this warm ink on this warm paper do not look alike.
PAPER = (0xFC / 255, 0xFB / 255, 0xF8 / 255)
INK = (0x1A / 255, 0x19 / 255, 0x16 / 255)
MUTED = (0x6E / 255, 0x69 / 255, 0x5E / 255)

# The sizes the OS actually sets, taken from the theme and the apps, rather than
# a tidy ramp. A specimen at sizes nothing uses proves nothing.
#   12  .statusbar label
#   13  body text / list rows
#   15  section headings
#   17  .dlghead
#   22  app titles
SPECIMEN = [
    (22, "Notebook", INK),
    (17, "Delete this notebook?", INK),
    (15, "Yesterday", INK),
    (13, "The quick brown fox jumps over the lazy dog, 0123456789", INK),
    (13, "Handgloves — mixing, hamburgefonstiv", INK),
    (12, "1,284 words · Saved", MUTED),
]

# The line that gets blown up. 13px ink on paper is the OS's workhorse and the
# hardest case: small enough that hinting decisions dominate, large enough that
# stem weight is visible.
ZOOM_TEXT = "Handgloves hamburgefonstiv"
ZOOM_SIZE = 13
ZOOM_FACTOR = 8


def _layout(cr, family, size_px, text):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family(family)
    # Pango sizes are in points at 96dpi unless set in device units. set_size
    # with SCALE gives points; the OS's CSS numbers are PIXELS, so convert or
    # the specimen renders everything ~33% too large and the small sizes — the
    # ones the argument is about — never appear.
    desc.set_absolute_size(size_px * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    return layout


def render(path, family="Helvetica Neue", label=None):
    """Render the specimen sheet to `path`. Returns (width, height)."""
    width, height = 720, 320
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*PAPER)
    cr.paint()

    y = 18
    for size, text, colour in SPECIMEN:
        layout = _layout(cr, family, size, text)
        cr.set_source_rgb(*colour)
        cr.move_to(28, y)
        PangoCairo.show_layout(cr, layout)
        _, logical = layout.get_pixel_extents()
        y += logical.height + 14

    # --- the blow-up -------------------------------------------------------
    # Rendered at 1:1 into its own small surface, then scaled up with NEAREST so
    # every pixel is a readable square. Scaling with the default filter would
    # smooth the very artefacts this is here to show.
    zw, zh = 260, 22
    zsurf = cairo.ImageSurface(cairo.FORMAT_RGB24, zw, zh)
    zcr = cairo.Context(zsurf)
    zcr.set_source_rgb(*PAPER)
    zcr.paint()
    zlayout = _layout(zcr, family, ZOOM_SIZE, ZOOM_TEXT)
    zcr.set_source_rgb(*INK)
    zcr.move_to(2, 2)
    PangoCairo.show_layout(zcr, zlayout)
    zsurf.flush()

    cr.save()
    cr.translate(28, y + 6)
    cr.scale(ZOOM_FACTOR, ZOOM_FACTOR)
    pattern = cairo.SurfacePattern(zsurf)
    pattern.set_filter(cairo.FILTER_NEAREST)
    cr.set_source(pattern)
    cr.rectangle(0, 0, (width - 56) / ZOOM_FACTOR, (height - y - 12) / ZOOM_FACTOR)
    cr.fill()
    cr.restore()

    if label:
        layout = _layout(cr, family, 11, label)
        cr.set_source_rgb(*MUTED)
        cr.move_to(28, height - 16)
        PangoCairo.show_layout(cr, layout)

    surf.write_to_png(path)
    return width, height


def describe():
    """Report what the font stack has actually been told to do.

    These come from GtkSettings, which is where GTK reads them from — NOT from
    the files we edit. A setting written to settings.ini that GTK never reads
    back is the exact failure this function exists to expose.
    """
    settings = Gtk.Settings.get_default()
    keys = ("gtk-xft-antialias", "gtk-xft-hinting", "gtk-xft-hintstyle",
            "gtk-xft-rgba", "gtk-xft-dpi", "gtk-font-name")
    out = []
    for k in keys:
        try:
            out.append("%s = %r" % (k, settings.get_property(k)))
        except Exception as exc:                      # pragma: no cover
            out.append("%s = <unreadable: %s>" % (k, exc))
    out.append("FREETYPE_PROPERTIES = %r" % os.environ.get("FREETYPE_PROPERTIES"))
    out.append("FONTCONFIG_FILE = %r" % os.environ.get("FONTCONFIG_FILE"))
    return "\n".join(out)


def main(argv):
    path = argv[1] if len(argv) > 1 else "/tmp/nb-specimen.png"
    label = argv[2] if len(argv) > 2 else None
    print(describe())
    w, h = render(path, label=label)
    print("wrote %s (%dx%d)" % (path, w, h))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
