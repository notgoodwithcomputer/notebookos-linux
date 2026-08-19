#!/usr/bin/env python3
"""illustrator_realuse_selftest — what a person doing real work meets.

    tools/guestrun.sh python3 tools/illustrator_realuse_selftest.py

illustrator_selftest proves the drawing engine is pixel-exact. This file
proves the things AROUND the pixels: what is on screen after an ordinary
sequence of actions, what the next Ctrl+S writes, what comes back when a
drawing is reopened, and whether the window answers a click. Each check below
stands for a defect a drive-through found:

* adding an empty layer BLANKED the artwork — the canvas was painted with the
  cairo operator a container's draw handler had left on the shared context, so
  every layer REPLACED what was under it instead of compositing over it;
* File > New kept the previous drawing's filename, so the next Ctrl+S wrote a
  blank canvas over the file that was open before, with no Save As;
* one click of the fill tool on the largest canvas the size dialog offers
  froze the window for four and a half seconds;
* a drawing could be given more layers than reopening it could restore: it
  saved with "Saved" on the chip and came back flattened to one layer;
* a canvas size that could not be used dismissed the card and threw away both
  typed numbers to flash the reason in the far corner of the status bar;
* hiding the active layer — the state where every stroke goes nowhere — did
  not reach the status bar until the pointer next moved;
* the chip after File > Open said "Saved" with the time of the OPEN.

Exit status is the number of failures.
"""
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
HOME = tempfile.mkdtemp(prefix="illustrator-realuse-")
os.environ["NB_HOME"] = HOME

import gi                                                       # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                   # noqa: E402
import cairo                                                    # noqa: E402
import illustrator                                              # noqa: E402

FAILS = []
CHECKS = [0]
INK = illustrator.px4("#1A1916")
# The layer ceiling the app publishes. A build that has none is the defect
# this file's IL-4 section is about, so fall back to the figure the sidecar
# reader has always enforced rather than ending the run in a traceback.
LAYER_CEILING = getattr(illustrator, "MAX_LAYERS", 64)
CHECKER_LIGHT = illustrator.px4("#F8F7F2")


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         ("   [%s]" % (detail,)) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(name, fn):
    """One named check. A check that raises has FAILED — by its name, so the
    report reads the same whether the app returned the wrong answer or blew
    up trying to give one."""
    try:
        ok, detail = fn()
    except Exception as exc:                                    # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    check(name, ok, detail)


def new_app(w=32, h=32):
    a = illustrator.Illustrator()
    a.cw, a.ch = w, h
    a.layers = [illustrator.Layer("Background", w, h, fill_white=True)]
    a.active = 0
    a.zoom = 1
    a.color = "#1A1916"
    a._new_scratch()
    a._rebuild_layers()
    return a


def put(a, x, y, px, layer=0):
    """Write one pixel of a layer, the way the brush does."""
    surf = a.layers[layer].surface
    surf.flush()
    i = y * surf.get_stride() + x * 4
    surf.get_data()[i:i + 4] = px
    surf.mark_dirty()


def shot(a, operator=cairo.OPERATOR_SOURCE):
    """Render the canvas onto a context left on `operator` — which is what a
    Python draw handler on an ancestor leaves behind (see paint_field)."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                              int(a.cw * a.zoom), int(a.ch * a.zoom))
    cr = cairo.Context(surf)
    cr.set_operator(operator)
    a._on_draw(a.canvas, cr)
    surf.flush()
    return surf, cr


def at(surf, x, y):
    i = y * surf.get_stride() + x * 4
    return bytes(surf.get_data()[i:i + 4])


def widgets(root):
    out = [root]
    i = 0
    while i < len(out):
        w = out[i]
        i += 1
        if isinstance(w, Gtk.Container):
            out.extend(w.get_children())
    return out


# ---- IL-1: layers composite OVER, whatever the window left on the context --
def _field_restores_operator():
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    cr = cairo.Context(surf)
    cr.set_operator(cairo.OPERATOR_OVER)
    illustrator.paint_field(cr, 8, 8)
    return (cr.get_operator() == cairo.OPERATOR_OVER,
            "operator left as %s" % cr.get_operator())


run("the canvas field hands the window's context back as it found it",
    _field_restores_operator)

_a = new_app()
put(_a, 4, 4, INK)
put(_a, 20, 20, illustrator.CLEAR4)   # what the eraser leaves behind
_a._add_layer()                      # a second, empty layer over the artwork


def _artwork_survives_a_new_layer():
    surf, _cr = shot(_a)
    return (at(surf, 4, 4) == INK,
            "the drawn pixel renders as %s" % (tuple(at(surf, 4, 4)),))


def _transparency_is_the_checker():
    # an erased pixel, a grown canvas and an alpha PNG all show through here
    surf, _cr = shot(_a)
    return (at(surf, 20, 20) == CHECKER_LIGHT,
            "the erased pixel renders as %s" % (tuple(at(surf, 20, 20)),))


def _canvas_leaves_the_context_alone():
    _surf, cr = shot(_a)
    return (cr.get_operator() == cairo.OPERATOR_SOURCE,
            "operator left as %s" % cr.get_operator())


def _a_faint_layer_does_not_dim_the_rest():
    _a.layers[1].opacity = 25
    surf, _cr = shot(_a)
    _a.layers[1].opacity = 100
    return (at(surf, 4, 4) == INK,
            "under a 25%% layer the ink renders as %s" % (tuple(at(surf, 4, 4)),))


run("adding an empty layer leaves the artwork on screen",
    _artwork_survives_a_new_layer)
run("a transparent pixel shows the checkerboard, not a void",
    _transparency_is_the_checker)
run("the canvas leaves the shared context's operator as it found it",
    _canvas_leaves_the_context_alone)
run("a layer at 25% does not dim the layers under it",
    _a_faint_layer_does_not_dim_the_rest)
_a.destroy()


# ---- IL-2: File > New starts an untitled document --------------------------
_n = new_app()
_n._path = os.path.join(HOME, "an earlier drawing.png")
_asked = []
_n._file_save_as = lambda: (_asked.append(1), True)[1]
_n._do_file_new()


def _new_forgets_the_filename():
    return _n._path is None, "still %r" % (_n._path,)


def _save_after_new_asks_for_a_name():
    _n._file_save()
    return _asked == [1], "Save As called %d times" % len(_asked)


run("File > New starts a document with no file behind it",
    _new_forgets_the_filename)
run("the first save after File > New asks for a name",
    _save_after_new_asks_for_a_name)
_n.destroy()


# ---- IL-3: the fill answers a click ----------------------------------------
def reference_fill(buf, stride, w, h, seed, target, newpx):
    """A plain per-pixel 4-connected fill, to say what the answer IS."""
    if target == newpx:
        return buf
    stack = [seed]
    while stack:
        x, y = stack.pop()
        if not (0 <= x < w and 0 <= y < h):
            continue
        i = y * stride + x * 4
        if bytes(buf[i:i + 4]) != target:
            continue
        buf[i:i + 4] = newpx
        stack.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    return buf


def _fill_matches_a_per_pixel_fill():
    import random
    random.seed(11)
    detail = []
    for trial in range(24):
        w = random.randrange(1, 30)
        h = random.randrange(1, 30)
        a = new_app(w, h)
        ly = a.layers[0]
        surf = ly.surface
        surf.flush()
        stride = surf.get_stride()
        palette = [illustrator.px4("#FFFFFF"), INK, illustrator.CLEAR4,
                   illustrator.px4("#C71818")]
        for y in range(h):
            for x in range(w):
                i = y * stride + x * 4
                surf.get_data()[i:i + 4] = random.choice(palette)
        surf.mark_dirty()
        surf.flush()
        before = bytearray(surf.get_data())
        seed = (random.randrange(w), random.randrange(h))
        a.color = random.choice(["#C71818", "#1A1916", "#FFFFFF", "#2E8B57"])
        i = seed[1] * stride + seed[0] * 4
        target = bytes(before[i:i + 4])
        want = reference_fill(bytearray(before), stride, w, h, seed, target,
                              illustrator.px4(a.color))
        a._flood_fill(ly, seed)
        surf.flush()
        got = bytearray(surf.get_data())
        a.destroy()
        if got != want:
            detail.append("%dx%d seed=%s" % (w, h, seed))
    return not detail, "; ".join(detail)


def _fill_of_the_largest_canvas_is_not_a_freeze():
    a = illustrator.Illustrator()
    a.cw = a.ch = illustrator.MAX_DIM
    a.layers = [illustrator.Layer("Background", a.cw, a.ch, fill_white=True)]
    a.active = 0
    a.zoom = 1
    a.color = "#C71818"
    a._new_scratch()
    start = time.monotonic()
    a._flood_fill(a.layers[0], (100, 100))
    spent = time.monotonic() - start
    surf = a.layers[0].surface
    surf.flush()
    i = 900 * surf.get_stride() + 1500 * 4
    filled = bytes(surf.get_data()[i:i + 4]) == illustrator.px4("#C71818")
    a.destroy()
    return (filled and spent < 1.0,
            "%dx%d filled in %.2fs (filled=%s)"
            % (illustrator.MAX_DIM, illustrator.MAX_DIM, spent, filled))


run("the fill paints exactly the pixels a per-pixel fill would",
    _fill_matches_a_per_pixel_fill)
run("filling the largest canvas the app offers takes under a second",
    _fill_of_the_largest_canvas_is_not_a_freeze)


# ---- IL-4: a drawing never holds more layers than reopening restores -------
_l = new_app(8, 8)
_png = os.path.join(HOME, "many layers.png")


def _layers_stop_at_the_ceiling():
    for _i in range(LAYER_CEILING + 12):
        _l._add_layer()
    return (len(_l.layers) == LAYER_CEILING, "%d layers" % len(_l.layers))


def _the_new_layer_button_says_why():
    return (not _l.add_btn.get_sensitive()
            and _l.add_btn.get_tooltip_text()
            == "This drawing holds as many layers as it can.",
            "sensitive=%s tooltip=%r" % (_l.add_btn.get_sensitive(),
                                         _l.add_btn.get_tooltip_text()))


def _the_new_layer_menu_item_is_greyed():
    item = [it for it in _l.menu_items("Layer")
            if it and it[0].startswith("New Layer")][0]
    return item[1] is None, "callback %r" % (item[1],)


def _every_layer_comes_back():
    assert _l._write_png(_png)
    restored = _l._read_layer_sidecar(_png, _l.cw, _l.ch)
    return (restored is not None and len(restored[0]) == len(_l.layers),
            "wrote %d layers, read back %s"
            % (len(_l.layers),
               "nothing" if restored is None else len(restored[0])))


run("a drawing stops at the layer count it can be reopened with",
    _layers_stop_at_the_ceiling)
run("the + button is disabled at the layer ceiling and says why",
    _the_new_layer_button_says_why)
run("the New Layer menu item is greyed at the layer ceiling",
    _the_new_layer_menu_item_is_greyed)
run("every layer a saved drawing holds comes back when it is reopened",
    _every_layer_comes_back)
_l.destroy()


# ---- IL-5: a rejected canvas size keeps the card and the typing ------------
_c = new_app(64, 64)
_MESSAGE = "Width must be a number from 1 to 2048"


def _press(label):
    [w for w in widgets(_c._saveprompt_layer)
     if isinstance(w, Gtk.Button) and w.get_label() == label][0].clicked()


def _the_card_stays_up():
    _c._canvas_size_prompt()
    entries = [w for w in widgets(_c._saveprompt_layer)
               if isinstance(w, Gtk.Entry)]
    entries[0].set_text("abc")      # a width that is not a number
    entries[1].set_text("77")       # and a height that is
    _press("Resize")
    return (_c._saveprompt_layer is not None,
            "card was dismissed; canvas is %dx%d" % (_c.cw, _c.ch))


def _the_typing_survives():
    live = [w.get_text() for w in widgets(_c._saveprompt_layer)
            if isinstance(w, Gtk.Entry)]
    return live == ["abc", "77"], "entries hold %s" % (live,)


def _the_reason_is_on_the_card():
    said = [w.get_text() for w in widgets(_c._saveprompt_layer)
            if isinstance(w, Gtk.Label) and w.get_visible()]
    return _MESSAGE in said, "card says %s" % (said,)


def _a_good_size_still_resizes():
    live = [w for w in widgets(_c._saveprompt_layer) if isinstance(w, Gtk.Entry)]
    live[0].set_text("96")
    _press("Resize")
    return ((_c.cw, _c.ch) == (96, 77) and _c._saveprompt_layer is None,
            "canvas %dx%d, card open=%s"
            % (_c.cw, _c.ch, _c._saveprompt_layer is not None))


run("a canvas size that cannot be used keeps its card open", _the_card_stays_up)
run("a rejected canvas size keeps both numbers as they were typed",
    _the_typing_survives)
run("a rejected canvas size says why on the card, beside the fields",
    _the_reason_is_on_the_card)
run("a corrected canvas size still resizes and closes the card",
    _a_good_size_still_resizes)
_c.destroy()


# ---- IL-6: the hidden active layer is announced at once --------------------
_v = new_app()
_v._add_layer()


def _hiding_says_so_at_once():
    _v._toggle_visible(None, _v.active)
    return ('is hidden' in _v.st_tool.get_text(),
            "status reads %r" % _v.st_tool.get_text())


def _showing_all_takes_the_warning_back():
    # Put the warning up by the route that cannot be broken by the thing
    # under test, so this check can only pass because Show All took it down.
    _v.layers[_v.active].visible = False
    _v._refresh_status()
    if 'is hidden' not in _v.st_tool.get_text():
        return False, "the hidden-layer warning never went up"
    _v._show_all_layers()
    return ('is hidden' not in _v.st_tool.get_text(),
            "status reads %r" % _v.st_tool.get_text())


run("hiding the active layer says so in the status bar at once",
    _hiding_says_so_at_once)
run("Show All Layers takes the hidden-layer warning back at once",
    _showing_all_takes_the_warning_back)
_v.destroy()


# ---- IL-7: the chip says when the FILE was saved ---------------------------
_o = new_app(16, 16)
_saved = os.path.join(HOME, "yesterday.png")
_o._write_png(_saved)
_then = time.time() - 5 * 3600
os.utime(_saved, (_then, _then))


def _open_stamps_the_files_own_time():
    _o._open_file(_saved)
    want = time.strftime("%H:%M", time.localtime(_then))
    return (_o._saved_time == want,
            "chip says %r, the file was saved at %r" % (_o._saved_time, want))


run("the chip after File > Open shows when the file was saved",
    _open_stamps_the_files_own_time)
_o.destroy()

print("")
print("%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
print("RESULT: %s" % ("ALL PASS" if not FAILS
                      else "FAILED: %s" % ", ".join(FAILS)))
sys.exit(len(FAILS))
