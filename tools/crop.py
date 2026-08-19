#!/usr/bin/env python3
"""crop — cut a region out of a render and optionally zoom it, so a detail can
actually be inspected.  Also prints the colour at a point, for verifying a fix
changed the pixels it was meant to.

    crop.py IN.png OUT.png X Y W H [ZOOM]
    crop.py IN.png --px X Y            # print the RGB at one pixel
"""
import os
import sys
import tempfile
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402


def px(path, x, y):
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    n = pb.get_n_channels()
    data = pb.get_pixels()
    off = y * pb.get_rowstride() + x * n
    print("(%d,%d) = rgb(%d,%d,%d)  #%02X%02X%02X"
          % (x, y, data[off], data[off + 1], data[off + 2],
             data[off], data[off + 1], data[off + 2]))


def _save_png(pb, out):
    directory = os.path.dirname(os.path.abspath(out))
    mode = os.stat(out).st_mode & 0o7777 if os.path.exists(out) else 0o644
    fd, tmp = tempfile.mkstemp(prefix=".crop-", suffix=".png", dir=directory)
    os.close(fd)
    try:
        pb.savev(tmp, "png", [], [])
        if not os.path.getsize(tmp):
            raise OSError("PNG encoder produced an empty file")
        os.chmod(tmp, mode)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, out)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def main():
    src = sys.argv[1]
    if sys.argv[2] == "--px":
        px(src, int(sys.argv[3]), int(sys.argv[4]))
        return 0
    out = sys.argv[2]
    x, y, w, h = (int(v) for v in sys.argv[3:7])
    zoom = float(sys.argv[7]) if len(sys.argv) > 7 else 1.0
    pb = GdkPixbuf.Pixbuf.new_from_file(src)
    w = min(w, pb.get_width() - x)
    h = min(h, pb.get_height() - y)
    sub = pb.new_subpixbuf(x, y, w, h)
    if zoom != 1.0:
        sub = sub.scale_simple(int(w * zoom), int(h * zoom),
                               GdkPixbuf.InterpType.NEAREST)
    _save_png(sub, out)
    print("%s  %dx%d -> %s" % (src, sub.get_width(), sub.get_height(), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
