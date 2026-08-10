"""Notebook OS icons rendered from the pinned Lucide 1.31.0 SVG set."""
import io
import os
import cairo
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GObject, Gtk  # noqa: E402

from nbicons_data import FILLS, MAPPING, PATHS

ICONS = PATHS
ALIAS = {"settings": "sys", "language": "globe", "maps": "mappin",
         "gbaemu": "gamepad", "academics": "academic"}

def glyph_for(module, fallback="sys"):
    """The glyph a DE module wears: its own name, its alias, else `fallback`."""
    if module in ICONS:
        return module
    name = ALIAS.get(module)
    return name if name in ICONS else fallback

_DIRECTIONAL = {"back", "fwd", "prev", "next", "indent", "outdent"}

def _is_rtl():
    try:
        return Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL
    except Exception:
        return False

def _hex(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)

def _append(ctx, commands):
    for command in commands:
        kind = command[0]
        if kind == "m": ctx.move_to(command[1], command[2])
        elif kind == "l": ctx.line_to(command[1], command[2])
        elif kind == "c": ctx.curve_to(*command[1:])
        elif kind == "z": ctx.close_path()
        else: raise ValueError("invalid generated icon command: %r" % (command,))

def draw(ctx, name, size, color="#1A1916", width=1.6, mirror=None):
    """Stroke a compiled Lucide glyph on its native 24-unit design grid."""
    commands = PATHS.get(name, PATHS["sys"])
    ctx.new_path(); ctx.save(); ctx.scale(size / 24.0, size / 24.0)
    if mirror is None: mirror = name in _DIRECTIONAL and _is_rtl()
    if mirror: ctx.translate(24, 0); ctx.scale(-1, 1)
    ctx.set_source_rgb(*_hex(color)); ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND); ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    _append(ctx, commands); ctx.stroke()
    for subpath in FILLS.get(name, ()):
        ctx.new_path(); _append(ctx, subpath); ctx.fill()
    ctx.restore()

# A rendered icon is a pure, deterministic function of (name, size, color,
# width), and a GdkPixbuf is immutable as far as consumers go (Gtk.Image only
# refs it, never mutates), so it is safe to build once and share across every
# widget. Memoizing here collapses all repeated same-icon renders — which
# dominate list rebuilds / folder opens — to a single PNG round-trip.
_PIXBUF_CACHE = {}


def pixbuf(name, size, color="#1A1916", width=1.6):
    """cairo-drawn icon -> GdkPixbuf. We route through PNG bytes (pure pycairo
    write_to_png + a PixbufLoader) rather than Gdk.pixbuf_get_from_surface,
    which would need PyGObject's cairo foreign-type bridge (not built here).

    The result is memoized on (name, size, color, width); the returned pixbuf is
    shared and must be treated as read-only by callers (which Gtk.Image is)."""
    # Direction is part of the key: the same name draws a different glyph in a
    # right-to-left language, and a shared cache would hand back the wrong one.
    key = (name, size, color, width, _is_rtl() and name in _DIRECTIONAL)
    cached = _PIXBUF_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surf)
        draw(ctx, name, size, color, width)
        surf.flush()
        buf = io.BytesIO()
        surf.write_to_png(buf)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(buf.getvalue())
        loader.close()
        pb = loader.get_pixbuf()
        if pb is not None:
            _PIXBUF_CACHE[key] = pb
            return pb
    except Exception:
        # A missing gdk-pixbuf PNG loader (or any cairo/loader failure) must not
        # crash the app at construction time — icons degrade to blank instead.
        pass
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    pb.fill(0x00000000)
    _PIXBUF_CACHE[key] = pb
    return pb


# ---------------------------------------------------------------- HiDPI path
#
# THE PROBLEM. Every icon in this OS is a VECTOR — a list of drawing ops — so it
# can be rasterized perfectly at any resolution. `pixbuf()` above throws that
# away: it rasterizes into a `size x size` bitmap in LOGICAL pixels and hands it
# to Gtk.Image. On a panel running at scale 2 (see opt/notebook/display.sh) GTK
# then draws that bitmap into a context scaled by 2, so a 24px icon is smeared
# across 48 device pixels by the interpolator. The result is that on exactly the
# machines bought for their screen, every icon in the interface is soft — while
# the text beside it is sharp, which makes it look worse, not better, than the
# same icon on a normal panel.
#
# A GdkPixbuf cannot fix this: it is a bag of pixels with no notion of scale.
# A cairo SURFACE can — `set_device_scale(n, n)` marks it as "these pixels are n
# per logical unit", and Gtk.Image.new_from_surface honours it, drawing the icon
# at logical `size` using all n*size real pixels.
#
# So: render at size*scale, tell the surface what it is, and let GTK place it.
# The icon is then drawn from the vector at full panel resolution.

_SCALE = None


def scale_factor():
    """The integer device scale the interface is drawing at (1, 2 or 3).

    Asked of GDK first, because GDK is what will actually place the surface and
    is the only thing that knows what a window ended up on. GDK_SCALE is
    consulted as well and the LARGER wins: in an offscreen render — which is how
    every visual check in tools/ works — there is no monitor to ask and GDK
    reports 1, so trusting it alone would make the HiDPI path untestable from
    the harness that exists to test it."""
    global _SCALE
    if _SCALE is not None:
        return _SCALE
    scale = 1
    try:
        disp = Gdk.Display.get_default()
        if disp is not None:
            mon = None
            try:
                mon = disp.get_primary_monitor()
            except Exception:                                     # noqa: BLE001
                mon = None
            if mon is None:
                try:
                    mon = disp.get_monitor(0)
                except Exception:                                 # noqa: BLE001
                    mon = None
            if mon is not None:
                scale = max(scale, int(mon.get_scale_factor() or 1))
    except Exception:                                             # noqa: BLE001
        pass
    try:
        env = (os.environ.get("GDK_SCALE") or "").strip()
        if env.isdigit():
            scale = max(scale, int(env))
    except Exception:                                             # noqa: BLE001
        pass
    _SCALE = max(1, min(3, scale))
    return _SCALE


# The GType to give a Gtk.ListStore column that holds one of these surfaces.
#
# A cairo surface is a BOXED type, not a GObject, so a column declared
# GObject.TYPE_OBJECT (which is what the Finder's model used for its two icon
# columns) will not accept one, and `Gtk.ListStore(cairo.Surface, ...)` is
# rejected outright by PyGObject with "Must be GObject.GType, not type". The
# usable name is registered by the cairo bridge as "CairoSurface"; paired with
# Gtk.CellRendererPixbuf's `surface` property (GTK 3.10+, which honours a
# surface's device scale) it is what lets a TreeView or IconView show a HiDPI
# icon at all. Verified end to end at scale 2 before the Finder was changed.
#
# TYPE_PYOBJECT is the fallback: it also stores the surface, but a cell can then
# only be filled through a cell-data function rather than add_attribute.
try:
    SURFACE_GTYPE = GObject.type_from_name("CairoSurface")
except Exception:                                                 # noqa: BLE001
    SURFACE_GTYPE = GObject.TYPE_PYOBJECT


_SURFACE_CACHE = {}


def surface(name, size, color="#1A1916", width=1.6, flip_v=False):
    """cairo surface for `name`, rasterized at the panel's real resolution.

    `flip_v` mirrors the glyph top-to-bottom, which is how an "up" arrow becomes
    a "down" one without a second icon (Packages' sort indicator). It exists
    here rather than at the call site because the pixbuf equivalent — building
    the icon and calling GdkPixbuf.flip() — only works on a pixbuf, and would
    have quietly kept that one arrow on the blurry path.

    Cached on the same key as pixbuf(). A cairo surface handed to several
    Gtk.Images is safe to share: Gtk.Image only ever reads it."""
    scale = scale_factor()
    key = (name, size, color, width, scale, flip_v,
           _is_rtl() and name in _DIRECTIONAL)
    cached = _SURFACE_CACHE.get(key)
    if cached is not None:
        return cached
    dev = max(1, int(round(size * scale)))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, dev, dev)
    ctx = cairo.Context(surf)
    if flip_v:
        ctx.translate(0, dev)
        ctx.scale(1, -1)
    # Drawn at the DEVICE size, so the 1.6px stroke is scaled with everything
    # else and comes out 1.6 LOGICAL px thick — not a hairline at 2x.
    draw(ctx, name, dev, color, width)
    surf.flush()
    surf.set_device_scale(scale, scale)
    _SURFACE_CACHE[key] = surf
    return surf


def surface_from_pixbuf(pb, scale=None):
    """Wrap a PHOTO (album art, a thumbnail, a video frame) for a HiDPI screen.

    The icons above are vectors and can simply be redrawn at any resolution.
    Raster content cannot: the only thing to do is decode MORE SOURCE PIXELS and
    then tell GTK that those pixels are finer than logical units. So the caller
    scales its pixbuf to size*scale_factor() and hands it here, and this returns
    a surface carrying the device scale, which Gtk.Image places at the original
    logical size — sharp instead of interpolated.

    Returns None if the bridge or the pixbuf is unusable, so every caller can
    fall back to set_from_pixbuf and still show something."""
    if pb is None:
        return None
    try:
        return Gdk.cairo_surface_create_from_pixbuf(
            pb, int(scale or scale_factor()), None)
    except Exception:                                             # noqa: BLE001
        return None


def set_image_pixbuf(img, pb, scale=None):
    """Show an already-device-resolution pixbuf in `img` at its logical size.
    Drop-in for `img.set_from_pixbuf(pb)` on the HiDPI path."""
    surf = surface_from_pixbuf(pb, scale)
    if surf is not None:
        try:
            img.set_from_surface(surf)
            return img
        except Exception:                                         # noqa: BLE001
            pass
    img.set_from_pixbuf(pb)
    return img


def image(name, size, color="#1A1916", width=1.6):
    """A Gtk.Image showing `name`, crisp at whatever scale the panel runs at.

    Drop-in for the `Gtk.Image.new_from_pixbuf(nbicons.pixbuf(...))` this OS used
    everywhere. Falls back to that older path if the cairo bridge is missing, so
    a build without gi's cairo foreign-type support still shows icons rather
    than nothing (that bridge going missing is what once made every DrawingArea
    in the OS blank, so it is worth not depending on absolutely)."""
    try:
        return Gtk.Image.new_from_surface(surface(name, size, color, width))
    except Exception:                                             # noqa: BLE001
        return Gtk.Image.new_from_pixbuf(pixbuf(name, size, color, width))


def set_image(img, name, size, color="#1A1916", width=1.6):
    """Re-point an existing Gtk.Image at `name`. Drop-in for
    `img.set_from_pixbuf(nbicons.pixbuf(...))`."""
    try:
        img.set_from_surface(surface(name, size, color, width))
        return img
    except Exception:                                             # noqa: BLE001
        img.set_from_pixbuf(pixbuf(name, size, color, width))
        return img


def style_search_entry(entry, size=15, color="#9A9484"):
    """Give a Gtk.SearchEntry OUR magnifier instead of the icon theme's.

    Gtk.SearchEntry is the one widget in this OS that reaches outside itself for
    an image: it asks the icon theme for "edit-find-symbolic". This OS ships no
    icon theme that has it — /usr/share/icons holds hicolor (four unrelated app
    icons: cups, htop, compton) and the notebook CURSOR theme, nothing else — so
    that lookup can only ever land on GTK's internal fallback. On a developer
    machine with a full theme installed it comes back as a blue, shaded 3-D
    pixmap: the only blue and the only non-flat icon anywhere in a flat warm-paper
    OS, sitting in the Finder's toolbar. With no theme at all it resolves to
    image-missing, and a bare SearchEntry then fails to paint.

    Music, Packages and Contacts never had the problem because they build their
    own search fields out of an nbicons glyph. The seven that use SearchEntry
    (finder, journal, academics, novel, screenplay, writer, nbpicker) now draw
    the same glyph through here, so every search field in the OS matches and none
    of them depends on anything outside the image.

    Overriding the PRIMARY icon is sufficient and verified: the entry keeps
    working, including once text is typed into it. Cosmetic, so never fatal.
    """
    try:
        entry.set_icon_from_pixbuf(Gtk.EntryIconPosition.PRIMARY,
                                   pixbuf("search", size, color))
        entry.set_icon_activatable(Gtk.EntryIconPosition.PRIMARY, False)
    except Exception:                                            # noqa: BLE001
        pass
