#!/usr/bin/env python3
"""appshot — render Notebook OS apps at chosen sizes, for the UI audit.

    appshot.py OUTDIR WxH[,WxH...] app[:Class] [app[:Class] ...]

Writes OUTDIR/<app>_<W>x<H>.png for every app x size, and prints the size the
PNG actually came out at. **If the PNG is bigger than you asked for, that app's
minimum size exceeds the budget** — on real hardware the excess is clipped and
unreachable. See tools/AUDIT_BRIEF.md.

Run it with the guest theme+fonts:

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf python3 tools/appshot.py ...

Sizes worth using: 1024x740 (smallest panel we support), 1366x740 (common
laptop), 1920x1052 (the res the previous audits used). Heights already have the
28px desktop panel subtracted.
"""
import os
import sys
import importlib
import inspect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import uishot  # noqa: E402


def app_class(mod, name=""):
    """The app's toplevel: the named class, else the module's first Gtk.Window."""
    if name:
        return getattr(mod, name)
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    return None


def pin_screen_size(w, h):
    """Make nbapp.screen_size() report the size we are RENDERING at.

    Apps legitimately ask the screen how big it is, to size a scrim, centre a
    modal, or choose a tile size. Unpinned, they get the developer's 1080p
    monitor while being drawn into a 1024x740 image, and lay themselves out for
    a screen that isn't there — g2048 picked 128px tiles and clipped its board,
    illustrator grew a balancing mat rail, video sized its preview stage wrong,
    and a confirm card centred itself off the edge. Three separate audits have
    now reported one of those as a bug in the app. It was the harness."""
    import nbapp
    nbapp.screen_size = lambda: (w, h)


def render(modname, cls, w, h, path, setup=None, after_show=None):
    """Construct modname's app, optionally drive it with setup(app), render."""
    if modname in sys.modules:
        del sys.modules[modname]
    pin_screen_size(w, h)
    m = importlib.import_module(modname)
    c = app_class(m, cls)
    if c is None:
        raise RuntimeError("no Gtk.Window class in " + modname)
    app = c()
    if setup is not None:
        setup(app)
    got = uishot.shot_window(app, w, h, path, after_show=after_show)
    try:
        app.destroy()
    except Exception:
        pass
    return got


def _device_scale():
    """The scale the renders are being captured at (1 or 2).

    Asked of GDK rather than of GDK_SCALE alone so it stays right if the scale
    ever comes from a monitor instead of the environment."""
    scale = 1
    try:
        from gi.repository import Gdk
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0) if disp else None
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
    return max(1, scale)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    outdir = sys.argv[1]
    sizes = [tuple(int(x) for x in s.split("x")) for s in sys.argv[2].split(",")]
    os.makedirs(outdir, exist_ok=True)
    os.environ.setdefault("NB_HOME", os.path.join(outdir, "nbhome"))
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    uishot.load_theme()
    for spec in sys.argv[3:]:
        mod, _, cls = spec.partition(":")
        for (w, h) in sizes:
            path = os.path.join(outdir, "%s_%dx%d.png" % (mod, w, h))
            try:
                gw, gh = render(mod, cls, w, h, path)
                # THE OVERFLOW TEST IS IN LOGICAL UNITS, THE PNG IS IN REAL
                # PIXELS. Under GDK_SCALE=2 the capture is legitimately twice
                # the requested size (uishot now saves the full device-resolution
                # surface rather than a downsampled pixbuf), so comparing raw
                # PNG dimensions against the layout budget flagged EVERY app as
                # overflowing by exactly one screen -- turning this tool's one
                # important signal into noise that would have been learned to be
                # ignored, which is how a real overflow gets shipped.
                scale = _device_scale()
                lw, lh = gw / scale, gh / scale
                flag = "" if (lw <= w and lh <= h) else \
                    "   <<< OVERFLOWS by %dx%d" % (max(0, int(lw - w)),
                                                   max(0, int(lh - h)))
            except Exception as e:
                print("ERR  %-12s %dx%d  %s" % (mod, w, h, str(e)[:80]))
                continue
            extra = "" if scale == 1 else "  (%dx%d logical @%dx)" % (lw, lh, scale)
            print("ok   %-12s asked %dx%d  got %dx%d%s  %s%s"
                  % (mod, w, h, gw, gh, extra, path, flag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
