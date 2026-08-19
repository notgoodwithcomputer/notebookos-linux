#!/usr/bin/env python3
"""illustrator_secondpass_selftest — what a second drive-through found.

    tools/guestrun.sh python3 tools/illustrator_secondpass_selftest.py

The suites beside this one were all green (300+ checks) while every defect
below was live in the shipped file. Each check here stands for one of them,
found by driving the REAL window with real keys and real drags:

* a drawing saved with its Background at 55% came back at 100% — File ▸ Open
  set the opacity slider to 100 with the slider's own handler live, and that
  handler writes what it is set to straight into the layer. The chip said
  "Saved" over a document the file on disk disagreed with, and the next
  Ctrl+S wrote the wrong opacity over the right one;
* moving the Opacity slider banked no history, so Ctrl+Z after it did not put
  the opacity back — it took back the BRUSH STROKE before it;
* Image ▸ Canvas Size: a size typed in and confirmed with Return did nothing
  at all, because nothing was listening for Return in either field;
* while a rectangle was dragged, the size readout was one motion event stale:
  a rectangle dragged to 16 x 16 said 11 x 11, and the first motion of every
  drag printed the pointer's coordinates instead of a size;
* View ▸ Show All Layers was offered when every layer was already showing,
  where it returns without doing anything.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

HOME = tempfile.mkdtemp(prefix="illustrator-secondpass-")
os.environ["NB_HOME"] = HOME
os.environ["NB_DRIVE_HOME_ROOT"] = HOME

import appdrive                                                 # noqa: E402
from gi.repository import Gtk                                   # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         ("   [%s]" % (detail,)) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(name, fn):
    """One named check. A check that raises has FAILED — by its name, so the
    report reads the same whether the app gave the wrong answer or blew up
    trying to give one."""
    try:
        ok, detail = fn()
    except Exception as exc:                                    # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    check(name, ok, detail)


DRIVE = appdrive.Drive("illustrator", home=os.path.join(HOME, "drive"))
APP = DRIVE.app
PICS = os.path.join(HOME, "drive", "Pictures")
os.makedirs(PICS, exist_ok=True)


def pt(x, y):
    """The centre of image pixel (x, y) in canvas coordinates."""
    return ((x + 0.5) * APP.zoom, (y + 0.5) * APP.zoom)


def pixel(x, y, layer=0):
    surf = APP.layers[layer].surface
    surf.flush()
    i = y * surf.get_stride() + x * 4
    return bytes(surf.get_data()[i:i + 4])


def fresh():
    """An untitled document, as File ▸ New leaves one."""
    APP._do_file_new()
    DRIVE.pump(0.05)


def entries():
    return [w for w in DRIVE.walk() if isinstance(w, Gtk.Entry)]


def stroke(y=10, x0=5, x1=20):
    DRIVE.drag(APP.canvas, [pt(x, y) for x in range(x0, x1)])
    DRIVE.pump(0.05)


# ---- IL5-1: a saved layer opacity is the one that comes back ---------------
OPACITY_PNG = os.path.join(PICS, "opacity.png")


def _save_a_faint_background():
    fresh()
    APP.layers[0].opacity = 55
    APP._add_layer()
    APP.layers[1].opacity = 40
    APP.active = 1
    APP._rebuild_layers()      # the slider now reads 40 — the state that bites
    APP._path = OPACITY_PNG
    ok = APP._file_save()
    return ok, "save returned %r" % (ok,)


def _opacity_survives_a_reopen():
    APP._open_file(OPACITY_PNG)
    DRIVE.pump(0.05)
    got = [ly.opacity for ly in APP.layers]
    return got == [55, 40], "layers came back at %r" % (got,)


def _the_next_save_writes_what_was_opened():
    """The half that is DATA LOSS: whatever the open put in memory is what the
    next Ctrl+S writes over the file."""
    APP._file_save()
    APP._open_file(OPACITY_PNG)
    DRIVE.pump(0.05)
    got = [ly.opacity for ly in APP.layers]
    return got == [55, 40], "after a re-save the file holds %r" % (got,)


def _the_chip_agrees_with_the_file():
    """A green chip is a claim that what is on screen is what is on disk. It
    was being made over a document whose layer document said something else."""
    import json
    digest = APP._file_digest(OPACITY_PNG)
    with open(APP._layer_sidecar(OPACITY_PNG, digest), encoding="utf-8") as fh:
        saved = [row["opacity"] for row in json.load(fh)["layers"]]
    live = [ly.opacity for ly in APP.layers]
    return (APP._chip_state == "saved" and not APP._dirty and saved == live,
            "chip %r dirty %r; on disk %r, on screen %r"
            % (APP._chip_state, APP._dirty, saved, live))


run("a drawing saves with its layer opacities", _save_a_faint_background)
run("File > Open brings every layer back at the opacity it was saved at",
    _opacity_survives_a_reopen)
# BEFORE the re-save below, not after it: once the wrong opacity has been
# written over the right one the screen and the file agree again, and a chip
# check standing after that point can never go red.
run("the chip says Saved only over a document the file agrees with",
    _the_chip_agrees_with_the_file)
run("the next Save writes the opacity the file was opened with",
    _the_next_save_writes_what_was_opened)


# ---- IL5-2: a layer opacity change is one step of Undo ---------------------
def _opacity_is_undoable():
    fresh()
    stroke()
    ink = pixel(10, 10)
    APP.op_scale.set_value(30)
    DRIVE.pump(0.05)
    APP._undo()
    return (APP.layers[0].opacity == 100 and pixel(10, 10) == ink,
            "opacity %d, the stroke's pixel is %s"
            % (APP.layers[0].opacity, tuple(pixel(10, 10))))


def _undo_names_the_opacity_change():
    APP.op_scale.set_value(70)
    DRIVE.pump(0.05)
    label = APP.history.undo_label()
    return label == "Layer Opacity", "Edit menu would read Undo %r" % (label,)


def _redo_puts_the_opacity_back():
    """Undo has to move it before Redo can put it back — a Redo that "passes"
    because nothing ever changed is not a check."""
    APP._undo()
    if APP.layers[0].opacity != 100:
        return False, "Undo left the opacity at %d" % APP.layers[0].opacity
    APP._redo()
    return APP.layers[0].opacity == 70, "Redo left it at %d" % APP.layers[0].opacity


def _a_whole_drag_is_one_step():
    before = len(APP._undo_stack)
    APP._undo()                      # back to 100, and the run is over
    DRIVE.pump(0.02)
    before = len(APP._undo_stack)
    for v in range(100, 40, -1):     # a drag from one end of the slider
        APP.op_scale.set_value(v)
    DRIVE.pump(0.05)
    added = len(APP._undo_stack) - before
    if added != 1:
        return False, "%d frames for one drag" % added
    APP._undo()
    return (APP.layers[0].opacity == 100,
            "one Undo gave back %d" % APP.layers[0].opacity)


def _an_edit_between_keeps_them_separate():
    fresh()
    APP.op_scale.set_value(60)
    stroke(y=20)
    APP.op_scale.set_value(20)
    DRIVE.pump(0.05)
    APP._undo()                       # the second opacity change
    first = APP.layers[0].opacity
    APP._undo()                       # the stroke
    APP._undo()                       # the first opacity change
    return (first == 60 and APP.layers[0].opacity == 100,
            "after one Undo %d, after three %d"
            % (first, APP.layers[0].opacity))


def _the_history_can_measure_itself_with_one_on_it():
    """_history_bytes walks every frame's surfaces on every push; a frame
    shape it does not know is an IndexError that takes the next edit down."""
    fresh()
    APP.op_scale.set_value(45)
    DRIVE.pump(0.02)
    total = APP._history_bytes()
    stroke(y=30)                      # the next edit must still be able to push
    return isinstance(total, int), "history_bytes returned %r" % (total,)


run("Ctrl+Z after the Opacity slider puts the opacity back, not the stroke",
    _opacity_is_undoable)
run("the Edit menu names the opacity change it would take back",
    _undo_names_the_opacity_change)
run("Redo puts an undone opacity back", _redo_puts_the_opacity_back)
run("one drag of the Opacity slider is one step of Undo", _a_whole_drag_is_one_step)
run("an edit between two opacity changes keeps them separate steps",
    _an_edit_between_keeps_them_separate)
run("the history can measure itself with an opacity frame on it",
    _the_history_can_measure_itself_with_one_on_it)


# ---- IL5-3: Return commits the Canvas Size card ----------------------------
def _return_resizes_the_canvas():
    fresh()
    APP._resize_canvas(64, 64)
    APP._canvas_size_prompt()
    DRIVE.pump(0.1)
    ents = entries()
    if len(ents) != 2:
        return False, "the card holds %d fields" % len(ents)
    ents[0].grab_focus()
    ents[0].select_region(0, -1)
    DRIVE.type("128")
    ents[1].grab_focus()
    ents[1].select_region(0, -1)
    DRIVE.type("96")
    DRIVE.key("Return")
    DRIVE.pump(0.2)
    return ((APP.cw, APP.ch) == (128, 96)
            and APP._saveprompt_layer is None,
            "canvas is %dx%d, card %s"
            % (APP.cw, APP.ch,
               "still up" if APP._saveprompt_layer else "closed"))


def _return_on_a_rejected_number_keeps_the_card():
    APP._canvas_size_prompt()
    DRIVE.pump(0.1)
    ents = entries()
    ents[0].grab_focus()
    ents[0].select_region(0, -1)
    DRIVE.type("9999")
    DRIVE.key("Return")
    DRIVE.pump(0.2)
    up = APP._saveprompt_layer is not None
    said = [w.get_text() for w in DRIVE.walk()
            if isinstance(w, Gtk.Label) and "must be a number" in (w.get_text() or "")]
    kept = [e.get_text() for e in entries()]
    APP._close_saveprompt()
    return (up and said and kept[:1] == ["9999"] and (APP.cw, APP.ch) == (128, 96),
            "card %s, said %r, fields %r, canvas %dx%d"
            % ("up" if up else "gone", said, kept, APP.cw, APP.ch))


run("Return in a Canvas Size field resizes the canvas", _return_resizes_the_canvas)
run("Return on a size that cannot be used keeps the card and the numbers",
    _return_on_a_rejected_number_keeps_the_card)


# ---- IL5-4: the readout during a shape drag is that shape ------------------
def _the_size_readout_is_the_shape_being_dragged():
    fresh()
    APP._resize_canvas(64, 64)
    DRIVE.pump(0.1)
    APP._pick_tool(None, "rect")
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    canvas = APP.canvas
    gw = canvas.get_window()
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    ev.window, ev.button, ev.state = gw, 1, Gdk.ModifierType(0)
    ev.x, ev.y = pt(5, 5)
    canvas.emit("button-press-event", ev)
    DRIVE.pump(0.02)
    seen = []
    for corner in (12, 20):
        mv = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
        mv.window, mv.state = gw, Gdk.ModifierType.BUTTON1_MASK
        mv.x, mv.y = pt(corner, corner)
        canvas.emit("motion-notify-event", mv)
        DRIVE.pump(0.02)
        side = corner - 5 + 1
        seen.append((APP.st_pos.get_text(), APP._dims(side, side)))
    rl = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    rl.window, rl.button = gw, 1
    rl.state = Gdk.ModifierType.BUTTON1_MASK
    rl.x, rl.y = pt(20, 20)
    canvas.emit("button-release-event", rl)
    DRIVE.pump(0.05)
    wrong = [(got, want) for got, want in seen if got != want]
    return not wrong, "readout/truth mismatch %r" % (wrong,)


run("the size readout during a shape drag is the shape on screen",
    _the_size_readout_is_the_shape_being_dragged)


# ---- IL5-5: Show All Layers is offered only when it can do something -------
def _show_all_is_offered_only_when_something_is_hidden():
    fresh()
    APP._add_layer()
    DRIVE.pump(0.05)
    items = {i[0]: i[1] for i in APP.menu_items("View") if isinstance(i, tuple)}
    if "Show All Layers" not in items:
        return False, "the View menu has no Show All Layers"
    live_when_all_shown = items["Show All Layers"] is not None
    APP._toggle_visible(None, 0)
    DRIVE.pump(0.05)
    items = {i[0]: i[1] for i in APP.menu_items("View") if isinstance(i, tuple)}
    live_when_hidden = items["Show All Layers"] is not None
    return (not live_when_all_shown and live_when_hidden,
            "enabled with all shown: %r, with one hidden: %r"
            % (live_when_all_shown, live_when_hidden))


run("View > Show All Layers is offered only when a layer is hidden",
    _show_all_is_offered_only_when_something_is_hidden)


DRIVE.close()
print()
print("%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
if FAILS:
    for name in FAILS:
        print("  FAILED: %s" % name)
print("RESULT: %s" % ("ALL PASS" if not FAILS else "FAILED"))
raise SystemExit(len(FAILS))
