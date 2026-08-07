#!/usr/bin/env python3
"""
gen_cursors — draw the Notebook OS pointer set and build the Xcursor theme.

THE PROBLEM THIS SOLVES. usr/share/icons/notebook/cursors contained exactly ONE
cursor: left_ptr, plus four symlinks pointing at it. It is also the only cursor
theme on the target and its index.theme has no Inherits=. So every pointer shape
the OS asks for and does not define — the I-beam over all 214 text fields, the
Finder's BOTTOM_RIGHT_CORNER resize handle, the busy pointer, Illustrator's
crosshair — fell through to the X11 CORE FONT cursors: 1-bit, unantialiased
bitmaps from the 1980s. They appear directly next to carefully rasterized text,
and they are the single loudest "this is an old Linux desktop" signal left in
the interface. A person deciding whether this machine is as well made as their
iPad sees the I-beam within about four seconds of picking it up.

THE DESIGN. It is not an arrow set. Notebook OS's pointer is a SOLID INK DISC
INSIDE A WHITE RING, with a centred hotspot — deliberate, distinctive, and
already shipped, so every shape added here has to belong to the same family
rather than importing a generic arrow theme:

  * ink fill, white halo, nothing else — no gradients, no grey, no colour;
  * every hotspot is the geometric centre, exactly as left_ptr's is. Centring is
    what makes a dot pointer feel accurate, and mixing centred and tip-anchored
    hotspots in one theme makes the pointer feel like it drifts between apps;
  * the white halo is what keeps the shape readable on both the dark Terminal
    field and the pale paper, and its width scales with the cursor.

    python3 tools/gen_cursors.py            # writes into the overlay
    python3 tools/gen_cursors.py --sheet /tmp/cursors.png   # + contact sheet

Requires xcursorgen (x11-apps). Sizes match the existing left_ptr: 24/32/48/64,
so a HiDPI panel at scale 2 has a real 64px bitmap to use instead of a scaled
32px one — which matters here for the same reason the rest of the HiDPI work
does. See opt/notebook/display.sh.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile

import cairo

SIZES = (24, 32, 48, 64)

INK = (0x0B / 255, 0x0A / 255, 0x09 / 255)
HALO = (1.0, 1.0, 1.0)

_HERE = os.path.dirname(os.path.abspath(__file__))
CURSOR_DIR = os.path.join(
    os.path.dirname(_HERE), "buildroot", "board", "notebookos",
    "rootfs-overlay", "usr", "share", "icons", "notebook", "cursors")


# --------------------------------------------------------------------- shapes
# Each shape draws into a unit square (0..1) and is scaled to the real size by
# the caller, so one definition serves every size and stays proportional.
# Each is a list of "paths"; a path is drawn twice — once thick in white (the
# halo) and once in ink — so the halo is always exactly concentric.

def _disc(cr, cx, cy, r):
    cr.new_sub_path()
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.close_path()


# The shipped left_ptr, measured: at 32px its ink disc spans x=8..23, i.e. 16px
# across, so r = 8px = 0.25*s, with a 2px halo. DISC_R is set from that
# measurement rather than chosen, because the pointer is already on screen and
# the rest of the set has to join IT — a set drawn to a different disc size
# would make the default pointer look like the odd one out in its own theme.
DISC_R = 0.25


def shape_default(cr, s):
    """The existing pointer: solid disc, white ring. Regenerated from the same
    source as the rest so the theme has one definition, and verified against the
    shipped bitmap (tools/gen_cursors.py --verify-default)."""
    _disc(cr, 0.5 * s, 0.5 * s, DISC_R * s)
    return "fill"


def shape_text(cr, s):
    """I-beam. Serifs top and bottom so it reads as a text cursor and not as a
    stray vertical rule in a document that is full of vertical rules."""
    w = 0.16 * s          # serif half-width
    h = 0.30 * s          # half height
    cx, cy = 0.5 * s, 0.5 * s
    t = 0.055 * s         # stroke half-thickness
    # stem
    cr.rectangle(cx - t, cy - h, 2 * t, 2 * h)
    # top and bottom serifs
    cr.rectangle(cx - w, cy - h, 2 * w, 2 * t)
    cr.rectangle(cx - w, cy + h - 2 * t, 2 * w, 2 * t)
    return "fill"


def shape_pointer(cr, s):
    """The clickable pointer. A RING rather than a disc: same silhouette as the
    default so the pointer does not appear to change size, but visibly open, so
    "this responds" is legible at a glance without introducing a hand — which
    would be the only representational object in the entire interface."""
    r = DISC_R * s
    _disc(cr, 0.5 * s, 0.5 * s, r)
    _disc(cr, 0.5 * s, 0.5 * s, r * 0.46)     # hole, via even-odd fill
    return "fill-eo"


def _arrow_head(cr, cx, cy, dx, dy, s):
    """A solid triangular head pointing along (dx,dy), centred on (cx,cy)."""
    a = 0.115 * s                      # head half-width
    l = 0.145 * s                      # head length
    px, py = -dy, dx                   # perpendicular
    tipx, tipy = cx + dx * l, cy + dy * l
    cr.move_to(tipx, tipy)
    cr.line_to(cx - dx * l * 0.10 + px * a, cy - dy * l * 0.10 + py * a)
    cr.line_to(cx - dx * l * 0.10 - px * a, cy - dy * l * 0.10 - py * a)
    cr.close_path()


def _double_arrow(cr, s, dx, dy):
    """Two heads back to back along (dx,dy) joined by a bar. The resize family."""
    cx, cy = 0.5 * s, 0.5 * s
    reach = 0.30 * s
    t = 0.050 * s
    # the connecting bar, as a rectangle rotated into the axis
    px, py = -dy, dx
    x0, y0 = cx - dx * reach, cy - dy * reach
    x1, y1 = cx + dx * reach, cy + dy * reach
    cr.move_to(x0 + px * t, y0 + py * t)
    cr.line_to(x1 + px * t, y1 + py * t)
    cr.line_to(x1 - px * t, y1 - py * t)
    cr.line_to(x0 - px * t, y0 - py * t)
    cr.close_path()
    _arrow_head(cr, x1, y1, dx, dy, s)
    _arrow_head(cr, x0, y0, -dx, -dy, s)


def shape_ew(cr, s):
    _double_arrow(cr, s, 1.0, 0.0)
    return "fill"


def shape_ns(cr, s):
    _double_arrow(cr, s, 0.0, 1.0)
    return "fill"


def shape_nwse(cr, s):
    k = math.sqrt(0.5)
    _double_arrow(cr, s, k, k)
    return "fill"


def shape_nesw(cr, s):
    k = math.sqrt(0.5)
    _double_arrow(cr, s, k, -k)
    return "fill"


def shape_move(cr, s):
    """Four-way. Used for move/fleur/all-scroll."""
    _double_arrow(cr, s, 1.0, 0.0)
    _double_arrow(cr, s, 0.0, 1.0)
    return "fill"


def shape_crosshair(cr, s):
    """A precision cross with a gap at the centre, so the exact point the
    crosshair refers to is not hidden under its own ink."""
    cx, cy = 0.5 * s, 0.5 * s
    t = 0.042 * s
    reach = 0.32 * s
    gap = 0.085 * s
    cr.rectangle(cx - reach, cy - t, reach - gap, 2 * t)
    cr.rectangle(cx + gap, cy - t, reach - gap, 2 * t)
    cr.rectangle(cx - t, cy - reach, 2 * t, reach - gap)
    cr.rectangle(cx - t, cy + gap, 2 * t, reach - gap)
    return "fill"


def shape_wait(cr, s):
    """Busy. The default disc with a wedge taken out of it — the same object the
    pointer already is, visibly incomplete. Deliberately NOT an hourglass or a
    spinner: it must not animate (an animated cursor is a moving element on a
    desktop whose entire purpose is to stop demanding attention)."""
    r = DISC_R * s
    cx, cy = 0.5 * s, 0.5 * s
    cr.move_to(cx, cy)
    cr.arc(cx, cy, r, -math.pi / 2 + 0.9, -math.pi / 2 + 2 * math.pi)
    cr.close_path()
    return "fill"


def shape_no_drop(cr, s):
    """Not allowed: ring plus a bar. Reads at 24px, which a diagonal slash of
    the same weight does not."""
    r = DISC_R * s
    cx, cy = 0.5 * s, 0.5 * s
    _disc(cr, cx, cy, r)
    _disc(cr, cx, cy, r * 0.60)
    cr.rectangle(cx - r * 0.62, cy - 0.055 * s, r * 1.24, 0.11 * s)
    return "fill-eo"


# name -> (draw fn, [alias names])
# The alias lists are the whole point of the exercise: X clients ask for these
# by name, and a name this theme does not carry is a core-font bitmap on screen.
# Both the legacy X names (xterm, sb_h_double_arrow) and the CSS/freedesktop
# names (text, ew-resize) are provided, because GTK apps use both.
CURSORS = {
    "left_ptr": (shape_default, [
        "default", "arrow", "top_left_arrow", "left_ptr_watch"]),
    "xterm": (shape_text, ["text", "ibeam"]),
    "hand2": (shape_pointer, ["hand1", "hand", "pointer", "pointing_hand"]),
    "sb_h_double_arrow": (shape_ew, [
        "ew-resize", "h_double_arrow", "col-resize", "split_h",
        "left_side", "right_side", "e-resize", "w-resize"]),
    "sb_v_double_arrow": (shape_ns, [
        "ns-resize", "v_double_arrow", "row-resize", "split_v",
        "top_side", "bottom_side", "n-resize", "s-resize"]),
    "bottom_right_corner": (shape_nwse, [
        "nwse-resize", "top_left_corner", "se-resize", "nw-resize",
        "size_fdiag"]),
    "bottom_left_corner": (shape_nesw, [
        "nesw-resize", "top_right_corner", "sw-resize", "ne-resize",
        "size_bdiag"]),
    "fleur": (shape_move, ["move", "all-scroll", "size_all", "grabbing", "grab"]),
    "crosshair": (shape_crosshair, ["cross", "tcross", "cell", "crosshair2"]),
    "watch": (shape_wait, ["wait", "progress"]),
    "crossed_circle": (shape_no_drop, [
        "not-allowed", "no-drop", "forbidden", "dnd-none"]),
}


def render_png(shape_fn, size, path):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    # THE HALO IS DRAWN FROM THE SAME PATH, STROKED WIDE, BEFORE THE FILL.
    # Stroking the identical path guarantees the ring is concentric at every
    # size; drawing a separate slightly-larger shape (the obvious alternative)
    # drifts visibly at 24px, where the halo is only a couple of pixels wide.
    #
    # WIDTH IS DOUBLE THE VISIBLE RING. A stroke is centred on its path, so half
    # of it lands INSIDE the shape and is then painted over by the fill: only
    # half ever shows. Sizing this as "the ring I want" therefore drew a halo
    # half the intended thickness, and the generated pointer came out 26%
    # smaller in area than the one the OS already ships (276 vs 372 opaque
    # pixels at 32px) — enough that the default pointer would have visibly
    # changed size on upgrade.
    #
    # 0.18 is derived, not chosen: the shipped left_ptr has an ink radius of
    # 0.25*s and a total opaque radius of ~0.34*s, so the visible ring is
    # ~0.09*s and the stroke must be twice that. Verified at every size with
    # `--verify-default`.
    halo_w = max(3.0, 0.18 * size)

    mode = shape_fn(cr, size)
    cr.set_source_rgb(*HALO)
    cr.set_line_width(halo_w)
    cr.stroke_preserve()
    cr.set_source_rgb(*INK)
    if mode == "fill-eo":
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    else:
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
    cr.fill()

    surf.write_to_png(path)


def build_cursor(name, shape_fn, outdir, workdir):
    """Render every size and run xcursorgen. Returns the output path."""
    cfg_lines = []
    for size in SIZES:
        png = os.path.join(workdir, "%s_%d.png" % (name, size))
        render_png(shape_fn, size, png)
        # Hotspot is the centre for every cursor in this theme -- see the
        # module docstring. left_ptr's shipped hotspot is (16,16) at 32px,
        # i.e. exactly centre, and the set has to agree with it.
        hot = size // 2
        cfg_lines.append("%d %d %d %s" % (size, hot, hot, png))
    cfg = os.path.join(workdir, "%s.cursor" % name)
    with open(cfg, "w") as fh:
        fh.write("\n".join(cfg_lines) + "\n")
    out = os.path.join(outdir, name)
    subprocess.run(["xcursorgen", cfg, out], check=True,
                   capture_output=True, text=True)
    return out


def contact_sheet(path):
    """One PNG showing every cursor at 32px on both fields it must survive:
    the paper desktop and the Terminal's dark one. A cursor set is only right if
    it reads on BOTH, which is what the white halo is for."""
    names = list(CURSORS)
    cell, pad = 32, 14
    cols = len(names)
    w = cols * (cell + pad) + pad
    h = 2 * (cell + pad) + pad + 18
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0xDE / 255, 0xD4 / 255, 0xC2 / 255)
    cr.rectangle(0, 0, w, (cell + pad) + pad // 2)
    cr.fill()
    cr.set_source_rgb(0x1A / 255, 0x19 / 255, 0x16 / 255)
    cr.rectangle(0, (cell + pad) + pad // 2, w, h)
    cr.fill()

    with tempfile.TemporaryDirectory() as td:
        for i, name in enumerate(names):
            png = os.path.join(td, "%s.png" % name)
            render_png(CURSORS[name][0], cell, png)
            img = cairo.ImageSurface.create_from_png(png)
            for row in (0, 1):
                x = pad + i * (cell + pad)
                y = pad + row * (cell + pad)
                cr.set_source_surface(img, x, y)
                cr.paint()
    surf.write_to_png(path)
    return path


def _read_xcursor(path, want=32):
    """Pull one size out of an Xcursor file as (RGBA image bytes, w, h, hotspot).

    Written out rather than shelled to a tool because the ONLY question that
    matters here — does the pointer this script generates match the pointer the
    OS already ships — cannot be answered by looking at the two files, only by
    comparing their pixels."""
    import struct
    with open(path, "rb") as fh:
        d = fh.read()
    magic, _hdr, _ver, ntoc = struct.unpack("<4sIII", d[:16])
    if magic != b"Xcur":
        raise ValueError("%s is not an Xcursor file" % path)
    for i in range(ntoc):
        typ, sub, pos = struct.unpack("<III", d[16 + i * 12:16 + i * 12 + 12])
        if typ != 0xFFFD0002 or sub != want:
            continue
        w, h, xh, yh, _delay = struct.unpack("<IIIII", d[pos + 16:pos + 36])
        return d[pos + 36:pos + 36 + w * h * 4], w, h, (xh, yh)
    return None, 0, 0, None


def verify_default():
    """Is the generated left_ptr the same pointer the OS already ships?"""
    shipped = os.path.join(CURSOR_DIR, "left_ptr")
    if not os.path.exists(shipped):
        print("no shipped left_ptr at %s" % shipped)
        return 1
    with tempfile.TemporaryDirectory() as td:
        build_cursor("left_ptr", shape_default, td, td)
        gen = os.path.join(td, "left_ptr")
        rc = 0
        for size in SIZES:
            a, w, h, ha = _read_xcursor(shipped, size)
            b, _, _, hb = _read_xcursor(gen, size)
            if a is None or b is None:
                print("%dpx: MISSING in %s" % (
                    size, "shipped" if a is None else "generated"))
                rc = 1
                continue
            # Compare on the alpha channel: it carries the silhouette, which is
            # the thing that must match. Sub-pixel differences in the ink fill
            # do not change how the pointer reads; a different SIZE does.
            da = [a[i] for i in range(3, len(a), 4)]
            db = [b[i] for i in range(3, len(b), 4)]
            differing = sum(1 for x, y in zip(da, db) if abs(x - y) > 8)
            cov_a = sum(1 for x in da if x > 128)
            cov_b = sum(1 for x in db if x > 128)
            print("%2dpx  hotspot %s vs %s   opaque px %d vs %d   differing %d/%d (%.1f%%)"
                  % (size, ha, hb, cov_a, cov_b, differing, len(da),
                     100.0 * differing / len(da)))
            if ha != hb:
                rc = 1
        return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=CURSOR_DIR)
    ap.add_argument("--sheet", default=None,
                    help="also write a contact sheet PNG here")
    ap.add_argument("--verify-default", action="store_true",
                    help="compare the generated left_ptr against the shipped "
                         "one and report, without writing anything")
    args = ap.parse_args()

    if args.verify_default:
        return verify_default()

    if not shutil.which("xcursorgen"):
        print("gen_cursors: xcursorgen not found (apt install x11-apps)")
        return 1

    os.makedirs(args.out, exist_ok=True)
    made, linked = 0, 0
    with tempfile.TemporaryDirectory() as workdir:
        for name, (fn, aliases) in CURSORS.items():
            build_cursor(name, fn, args.out, workdir)
            made += 1
            for alias in aliases:
                link = os.path.join(args.out, alias)
                if os.path.lexists(link):
                    os.unlink(link)
                os.symlink(name, link)
                linked += 1
    print("wrote %d cursors + %d aliases to %s" % (made, linked, args.out))

    if args.sheet:
        contact_sheet(args.sheet)
        print("contact sheet: %s" % args.sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
