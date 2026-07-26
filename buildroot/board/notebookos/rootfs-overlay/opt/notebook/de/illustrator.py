#!/usr/bin/env python3
"""
Illustrator — the Notebook OS raster paint app (native GTK).

A tool ribbon (tools / brush size / color palette + custom color chooser) sits
above a fixed 1180x640 canvas on a slate mat, with a Layers panel on the right
and a status bar below. Drawing is real: pencil / brush / eraser paint freehand,
line / rectangle / ellipse draw shapes with a live preview, fill flood-fills and
the picker samples a color. Color selection is the preset palette, the eyedropper,
or the full color chooser (any RGB value). The File menu operates on PNG files
under $NB_HOME/Pictures: New (blank canvas), Open (load a PNG), Save / Save As
(flatten the visible layers to a PNG). Opens with a single empty white Background
layer per the no-seed rule.

Hot paths are kept partial: freehand strokes and shape previews invalidate only a
tight bounding rectangle (queue_draw_area) so motion events never repaint the full
surface, and the flood fill is scanline (span) based.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango  # noqa: E402

import cairo
import io
import math
import os
import time

import nbapp
import nbpicker
import nbicons
from nbi18n import _t  # noqa: E402

CW, CH = 1180, 640
PANEL_W = 300     # layers-panel width; a matching left mat rail keeps the
                  # canvas centred on the real screen instead of shoved left
# Bound the Undo/Redo history. A frame stores only the RECTANGLE an edit
# actually touched (see _begin_edit / _commit_edit), so an ordinary brush stroke
# costs a few kilobytes instead of a whole 1180x640 canvas copy — which is what
# lets the history be this deep. At the old whole-canvas-per-frame cost, twenty
# frames alone were 60 MB.
UNDO_DEPTH = 80

# User files (File ▸ Open / Save / Save As) are PNGs under $NB_HOME/Pictures.
NB_HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
PICS_DIR = os.path.join(NB_HOME, "Pictures")
# Colours the user mixed themselves, kept between sessions. A mixed colour was
# previously unrecoverable the moment another swatch was clicked — the exact
# blue someone spent a minute finding could only be found again by eye.
CFG_FILE = os.path.join(NB_HOME, ".config", "notebook", "illustrator.json")
MIXES_MAX = 8

TOOLS = [
    ("pencil", "Pencil"), ("brush", "Brush"), ("eraser", "Eraser"),
    ("fill", "Fill"), ("picker", "Colour Picker"), ("line", "Line"),
    ("rect", "Rectangle"), ("ellipse", "Ellipse"),
]
SIZES = [2, 4, 8, 14]
PALETTE = ["#1A1916", "#3A362E", "#8A857A", "#C9C4B6", "#FCFBF8", "#A6564A",
           "#9A7B4F", "#B98A4E", "#DCCBA2", "#EFE7D5", "#4A5E73", "#566E86",
           "#6E7B57", "#7E8A66", "#8A6D5B", "#A8895A"]

TOOL_NAMES = dict(TOOLS)

# Single-key tool shortcuts. The letter is surfaced in each tool's tooltip so
# it is discoverable, and [ / ] step the brush size.
TOOL_KEYS = {
    "pencil": "P", "brush": "B", "eraser": "E", "fill": "F",
    "picker": "I", "line": "L", "rect": "R", "ellipse": "O",
}
_KEY_TOOLS = {
    Gdk.KEY_p: "pencil", Gdk.KEY_b: "brush", Gdk.KEY_e: "eraser",
    Gdk.KEY_f: "fill", Gdk.KEY_i: "picker", Gdk.KEY_l: "line",
    Gdk.KEY_r: "rect", Gdk.KEY_o: "ellipse",
}


def _rgb(hex_):
    h = hex_.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


class Layer:
    def __init__(self, name, fill_white=False):
        self.name = name
        self.visible = True
        self.opacity = 100
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, CW, CH)
        if fill_white:
            cr = cairo.Context(self.surface)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()


class Illustrator(nbapp.AppWindow):
    app_name = "Illustrator"
    menus = ("File", "Edit", "View", "Layer")

    def __init__(self):
        super().__init__()
        self._install_css()
        self._build_checker_pattern()

        self.tool = "brush"
        self.size = 4
        self.color = "#1A1916"
        self.layers = [Layer("Background", fill_white=True)]
        self.active = 0
        self.next_id = 2
        self._drawing = False
        self._start = None
        self._last = None
        self._preview = None      # (tool, (x0,y0), (x1,y1))
        self._cursor = None
        self._dirty = False       # True once the canvas differs from the last save
        self._path = None         # current PNG file (File ▸ Save writes here)
        self._undo_stack = []     # history frames (see _apply_frame), newest last
        self._redo_stack = []
        self._pending = None      # pixels held while an edit is in progress
        self._stroke_track = None  # union of everything the live gesture touched
        self._saveprompt_layer = None
        self._mixes = self._load_mixes()   # colours mixed in earlier sessions
        self._chip_state = "empty"   # ribbon save chip: empty | saved | unsaved
        self._saved_time = ""
        self._flash_token = 0        # guards transient _flash_save auto-restores

        # Guard both exit routes (Esc and the red logo dot both call
        # self.close(), which emits delete-event): when there are unsaved
        # changes, offer Save / Discard / Cancel before the window is destroyed.
        self.connect("delete-event", self._on_delete)

        self._tool_btns = {}
        self._size_btns = {}
        self._swatches = {}

        self.content.pack_start(self._ribbon(), False, False, 0)

        # --- workspace: canvas mat + layers panel ---
        work = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        work.set_vexpand(True)

        # The canvas is a fixed-size CWxCH document. On a panel with room it sits
        # centred in the papertone field; on a smaller real panel (1366x768,
        # 1280x800, ...) the field SCROLLS instead of clipping the canvas off the
        # edges — never assume a 1920x1080 screen. Same halign/valign-in-a-Viewport
        # pattern the media image viewer uses.
        mat = Gtk.ScrolledWindow()
        mat.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        mat.get_style_context().add_class("mat")
        mat.set_hexpand(True)
        mat.set_vexpand(True)
        # CRITICAL: a GtkScrolledWindow installs a capture-phase pan / kinetic-
        # scroll gesture that intercepts a pointer drag BEFORE it reaches the
        # canvas — so dragging to draw would also pan the viewport and the canvas
        # would visibly move out from under the stroke. Disable both so a drag on
        # the canvas ONLY paints; the scrollbars still scroll a large canvas.
        mat.set_kinetic_scrolling(False)
        mat.set_capture_button_press(False)

        canvas_wrap = Gtk.Box()
        canvas_wrap.get_style_context().add_class("canvasframe")
        canvas_wrap.set_halign(Gtk.Align.CENTER)
        canvas_wrap.set_valign(Gtk.Align.CENTER)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(CW, CH)
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.canvas.connect("draw", self._on_draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("leave-notify-event", self._on_leave)
        # coalesce motion: GDK compresses queued motion events so a fast drag
        # produces one segment per frame, not one per raw device sample
        self.canvas.connect(
            "realize",
            lambda w: w.get_window().set_event_compression(True))
        canvas_wrap.add(self.canvas)
        mat.add(canvas_wrap)          # ScrolledWindow auto-wraps it in a Viewport

        # Centre the canvas on the REAL screen, not just within the field left of
        # the panel: the fixed-width layers panel on the right otherwise pulls the
        # centred canvas visibly off to the left. When the panel *and* a matching
        # left rail both still leave room for the whole canvas (a roomy panel),
        # add that rail so the mat is symmetric and the canvas lands on the true
        # screen centre. On a small panel where the canvas already has to scroll,
        # skip the rail so drawing keeps the full mat width. The rail is an
        # EventBox with a visible window painting the OPAQUE mat field — a plain
        # windowless Box background would blit black on the no-compositor stack.
        sw, _sh = nbapp.screen_size()
        if sw - 2 * PANEL_W >= CW:
            left_rail = Gtk.EventBox()
            left_rail.get_style_context().add_class("matrail")
            left_rail.set_size_request(PANEL_W, -1)
            work.pack_start(left_rail, False, False, 0)
        work.pack_start(mat, True, True, 0)

        self.panel = self._layers_panel()
        work.pack_start(self.panel, False, False, 0)
        self.content.pack_start(work, True, True, 0)

        # --- status bar ---
        self.content.pack_start(self._statusbar(), False, False, 0)
        self._refresh_status()

    # ---------------- ribbon ----------------
    def _ribbon(self):
        # Tools + Size + Colors have to fit the NARROWEST panel we support
        # (1024): the groups are fixed-size controls, so the only give is the
        # air between them. Keep the groups generous and the gaps modest.
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        bar.get_style_context().add_class("ribbon")

        # Tools
        tcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tcol.pack_start(self._caption("Tools"), False, False, 0)
        trow = Gtk.Box(spacing=4)
        for tid, name in TOOLS:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text("%s  (%s)" % (name, TOOL_KEYS[tid]))
            b.get_style_context().add_class("toolbtn")
            img = Gtk.Image.new_from_pixbuf(nbicons.pixbuf(tid, 18, "#6E695E"))
            b.add(img)
            b._img = img
            b._tid = tid
            b.connect("clicked", self._pick_tool, tid)
            self._tool_btns[tid] = b
            trow.pack_start(b, False, False, 0)
        tcol.pack_start(trow, False, False, 0)
        bar.pack_start(tcol, False, False, 0)
        bar.pack_start(self._vsep(), False, False, 0)

        # Size
        scol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scol.pack_start(self._caption("Size"), False, False, 0)
        srow = Gtk.Box(spacing=4)
        for sz in SIZES:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("sizebtn")
            b.set_tooltip_text(_t("Brush size %d px  ·  [ and ] to change") % sz)
            dot = Gtk.DrawingArea()
            d = min(sz + 2, 18)
            dot.set_size_request(d, d)
            dot.connect("draw", self._draw_dot)
            box = Gtk.Box()
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.add(dot)
            b.add(box)
            b._sz = sz
            b.connect("clicked", self._pick_size, sz)
            self._size_btns[sz] = b
            srow.pack_start(b, False, False, 0)
        scol.pack_start(srow, False, False, 0)
        bar.pack_start(scol, False, False, 0)
        bar.pack_start(self._vsep(), False, False, 0)

        # Colors
        ccol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ccol.pack_start(self._caption("Colours"), False, False, 0)
        crow = Gtk.Box(spacing=10)
        crow.set_valign(Gtk.Align.CENTER)
        self.chip = Gtk.DrawingArea()
        self.chip.set_size_request(44, 44)
        self.chip.get_style_context().add_class("chip")
        self.chip.set_tooltip_text(_t("Active colour — click to sample from canvas (eyedropper)"))
        self.chip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.chip.connect("draw", self._draw_chip)
        self.chip.connect("button-press-event", self._on_chip_press)
        crow.pack_start(self.chip, False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(4)
        for i, c in enumerate(PALETTE):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("swatch")
            b.set_size_request(22, 22)
            da = Gtk.DrawingArea()
            da.set_size_request(22, 22)
            da._col = c
            da.connect("draw", self._draw_swatch)
            b.add(da)
            b._col = c
            b._da = da
            b.connect("clicked", self._pick_color, c)
            self._swatches[c] = b
            grid.attach(b, i % 8, i // 8, 1, 1)

        # swatch grid, then a full color-chooser button INLINE at the end of the
        # palette row (any RGB value, beyond the preset palette and the
        # eyedropper) — to the RIGHT of the swatches, not stacked beneath them.
        grid.set_valign(Gtk.Align.CENTER)
        crow.pack_start(grid, False, False, 0)
        custom = Gtk.Button(label=_t("Custom Colour…"))
        custom.set_relief(Gtk.ReliefStyle.NONE)
        custom.get_style_context().add_class("custombtn")
        custom.set_valign(Gtk.Align.CENTER)
        custom.set_tooltip_text(_t("Mix any colour beyond the palette"))
        custom.connect("clicked", self._open_color_chooser)
        crow.pack_start(custom, False, False, 0)
        ccol.pack_start(crow, False, False, 0)
        bar.pack_start(ccol, False, False, 0)

        self._sync_ribbon()
        return bar

    def _caption(self, text):
        lbl = Gtk.Label(label=text.upper(), xalign=0)
        lbl.get_style_context().add_class("caption")
        return lbl

    def _vsep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("vsep")
        s.set_valign(Gtk.Align.CENTER)
        s.set_size_request(1, 56)
        return s

    # ---------------- layers panel ----------------
    def _layers_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.get_style_context().add_class("lpanel")
        panel.set_size_request(PANEL_W, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("lhead")
        title = Gtk.Label(label=_t("LAYERS"), xalign=0)
        title.get_style_context().add_class("ltitle")
        head.pack_start(title, True, True, 0)

        # Raise / lower the active layer. Without these, whatever order the user
        # happened to draw in is the order they are stuck with — a sky painted
        # last can never go behind the house, which is most of what layers are
        # for.
        self.up_btn = Gtk.Button()
        self.up_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.up_btn.get_style_context().add_class("liconbtn")
        self.up_btn.set_tooltip_text(_t("Bring layer forward"))
        self.up_btn.add(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf("up", 15, "#1A1916")))
        self.up_btn.connect("clicked", lambda *_: self._move_layer(1))
        head.pack_start(self.up_btn, False, False, 0)

        self.down_btn = Gtk.Button()
        self.down_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.down_btn.get_style_context().add_class("liconbtn")
        self.down_btn.set_tooltip_text(_t("Send layer back"))
        self.down_btn.add(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf("down", 15, "#1A1916")))
        self.down_btn.connect("clicked", lambda *_: self._move_layer(-1))
        head.pack_start(self.down_btn, False, False, 0)

        add = Gtk.Button()
        add.set_relief(Gtk.ReliefStyle.NONE)
        add.get_style_context().add_class("liconbtn")
        add.set_tooltip_text(_t("New layer"))
        add.add(Gtk.Image.new_from_pixbuf(nbicons.pixbuf("plus", 15, "#1A1916")))
        add.connect("clicked", lambda *_: self._add_layer())
        head.pack_start(add, False, False, 0)

        self.del_btn = Gtk.Button()
        self.del_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.del_btn.get_style_context().add_class("liconbtn")
        self.del_btn.set_tooltip_text(_t("Delete layer"))
        self.del_btn.add(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf("trash", 15, "#1A1916")))
        self.del_btn.connect("clicked", lambda *_: self._delete_layer())
        head.pack_start(self.del_btn, False, False, 0)
        panel.pack_start(head, False, False, 0)

        self.layer_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.layer_list.get_style_context().add_class("llist")
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.layer_list)
        panel.pack_start(scroll, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        foot.get_style_context().add_class("lfoot")
        oprow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        opcap = Gtk.Label(label=_t("OPACITY"), xalign=0)
        opcap.get_style_context().add_class("caption")
        oprow.pack_start(opcap, True, True, 0)
        self.op_val = Gtk.Label(label="100%", xalign=1)
        self.op_val.get_style_context().add_class("caption")
        oprow.pack_start(self.op_val, False, False, 0)
        foot.pack_start(oprow, False, False, 0)

        self.op_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.op_scale.set_draw_value(False)
        self.op_scale.set_value(100)
        self.op_scale.get_style_context().add_class("opacity")
        self._op_handler = self.op_scale.connect("value-changed", self._on_opacity)
        foot.pack_start(self.op_scale, False, False, 0)
        panel.pack_start(foot, False, False, 0)

        self._rebuild_layers()
        return panel

    def _rebuild_layers(self):
        for ch in self.layer_list.get_children():
            self.layer_list.remove(ch)
        # idx -> that row's opacity label, so a live opacity drag can update the
        # number in place without rebuilding this whole widget tree per tick
        self._op_labels = {}
        # top layer first
        for idx in range(len(self.layers) - 1, -1, -1):
            ly = self.layers[idx]
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("lrow")
            if idx == self.active:
                row.get_style_context().add_class("active")
            row.connect("clicked", self._select_layer, idx)

            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            eye = Gtk.Button()
            eye.set_relief(Gtk.ReliefStyle.NONE)
            eye.get_style_context().add_class("eyebtn")
            col = "#1A1916" if ly.visible else "#9A9484"
            try:
                eye.add(Gtk.Image.new_from_pixbuf(
                    nbicons.pixbuf("eye" if ly.visible else "eyeoff", 15, col)))
            except GLib.Error:
                eye.add(Gtk.Image())
            eye.connect("clicked", self._toggle_visible, idx)
            inner.pack_start(eye, False, False, 0)

            name = Gtk.Label(label=ly.name, xalign=0)
            name.get_style_context().add_class("lname")
            # the panel is a fixed 300px column; a long name must ellipsize
            # rather than widen it and squeeze the canvas
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(16)
            inner.pack_start(name, True, True, 0)

            op = Gtk.Label(label="%d%%" % ly.opacity, xalign=1)
            op.get_style_context().add_class("lopacity")
            inner.pack_start(op, False, False, 0)
            self._op_labels[idx] = op
            row.add(inner)
            self.layer_list.pack_start(row, False, False, 0)

        # the bottom layer is the one the document always keeps, so it cannot be
        # deleted or sent further back; the top one has nowhere forward to go
        for btn, on in ((self.del_btn, self.active != 0),
                        (self.down_btn, self.active > 0),
                        (self.up_btn, self.active < len(self.layers) - 1)):
            btn.set_sensitive(on)
            sc = btn.get_style_context()
            if on:
                sc.remove_class("disabled")
            else:
                sc.add_class("disabled")

        ly = self.layers[self.active]
        # Block the value-changed handler while syncing the slider to the active
        # layer: otherwise selecting a layer (or an opacity drag, which rebuilds
        # this list per tick) re-enters _on_opacity and rebuilds the list twice.
        self.op_scale.handler_block(self._op_handler)
        self.op_scale.set_value(ly.opacity)
        self.op_scale.handler_unblock(self._op_handler)
        self.op_val.set_text("%d%%" % ly.opacity)
        self.layer_list.show_all()

    # ---------------- status bar ----------------
    def _statusbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        bar.get_style_context().add_class("statusbar")
        self.st_tool = Gtk.Label(xalign=0)
        self.st_tool.get_style_context().add_class("stlabel")
        bar.pack_start(self.st_tool, False, False, 0)
        self.st_pos = Gtk.Label(xalign=0.5)
        self.st_pos.get_style_context().add_class("stlabel")
        bar.set_center_widget(self.st_pos)
        self.st_size = Gtk.Label(xalign=1)
        self.st_size.get_style_context().add_class("stlabel")
        bar.pack_end(self.st_size, False, False, 0)
        # Save state lives here, beside the document's own facts, the way
        # Writer does it. In the ribbon it cost 120px of width that a 1024
        # panel does not have — the tools were pushed off the screen edge.
        self.save_lbl = Gtk.Label()
        self.save_lbl.get_style_context().add_class("savestate")
        self._render_chip()
        bar.pack_end(self.save_lbl, False, False, 0)
        return bar

    def _refresh_status(self):
        tname = TOOL_NAMES.get(self.tool, "")
        ly = self.layers[self.active]
        name = ly.name if ly.visible else ly.name + " (hidden)"
        self.st_tool.set_text("%s · %s" % (tname, name))
        if self._cursor:
            self.st_pos.set_text("%d, %d" % self._cursor)
        else:
            self.st_pos.set_text("")
        n = len(self.layers)
        self.st_size.set_text(
            "%d × %d px · %d layer%s" % (CW, CH, n, "" if n == 1 else "s"))

    # ---------------- drawing surface ----------------
    def _draw_dot(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        r = min(w, h) / 2
        cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb("#1A1916"))
        cr.fill()

    def _draw_chip(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        if w <= 0 or h <= 0:
            return                       # not yet allocated — nothing to paint
        cr.rectangle(0, 0, w, h)
        cr.set_source_rgb(*_rgb(self.color))
        cr.fill()
        # Inset the 1px frame by half a pixel so it lands on the pixel grid and
        # renders as a crisp hairline instead of a clipped half-pixel at the edge
        # (visible on the real framebuffer, masked by virtio-gpu resampling).
        cr.set_source_rgb(*_rgb("#C9C4B6"))
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()

    def _draw_swatch(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        c = area._col
        cr.rectangle(0, 0, w, h)
        cr.set_source_rgb(*_rgb(c))
        cr.fill()
        sel = (c == self.color)
        if sel:
            cr.set_source_rgb(*_rgb("#FCFBF8"))
            cr.set_line_width(2)
            cr.rectangle(1.5, 1.5, w - 3, h - 3)
            cr.stroke()
            cr.set_source_rgb(*_rgb("#C8341E"))
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, w - 1, h - 1)
            cr.stroke()
        else:
            cr.set_source_rgb(*_rgb("#C9C4B6"))
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, w - 1, h - 1)
            cr.stroke()

    def _build_checker_pattern(self):
        """Build the transparency checkerboard once as a repeating cairo pattern.
        A 2x2 (40x40) tile is painted a single time and wrapped in an
        EXTEND_REPEAT SurfacePattern; _on_draw then fills the damaged region with
        one native pattern paint instead of looping hundreds of Python-level tile
        fills on every repaint (the real, GPU-less framebuffer has nothing to
        hide that cost behind). Anchored at user-space (0,0) so the checker never
        shifts phase between a partial and a full repaint."""
        t = 20
        light = _rgb("#F8F6F0")
        dark = _rgb("#E9E4D6")
        tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, t * 2, t * 2)
        tc = cairo.Context(tile)
        tc.set_source_rgb(*light)
        tc.paint()
        tc.set_source_rgb(*dark)
        tc.rectangle(t, 0, t, t)   # top-right
        tc.rectangle(0, t, t, t)   # bottom-left
        tc.fill()
        tile.flush()
        pat = cairo.SurfacePattern(tile)
        pat.set_extend(cairo.EXTEND_REPEAT)
        self._bg_tile = tile          # keep the tile surface alive for the pattern
        self._bg_pattern = pat

    def _on_draw(self, area, cr):
        # Repaint only the exposed region. Freehand strokes and shape previews
        # invalidate a tight rectangle (queue_draw_area); cairo clips every op
        # below to that damaged rect, so a motion event never repaints the whole
        # 1180x640 surface. The transparency checkerboard is a cached repeating
        # pattern (built once) rather than a per-draw Python tile loop — on the
        # GPU-less framebuffer that turns ~1900 rectangle fills per full repaint
        # into a single native pattern paint.
        cr.set_source(self._bg_pattern)
        cr.paint()
        # composite layers (cairo clips the blit to the exposed region)
        for ly in self.layers:
            if not ly.visible:
                continue
            cr.set_source_surface(ly.surface, 0, 0)
            cr.paint_with_alpha(ly.opacity / 100.0)
        # live preview of shape tools
        if self._preview:
            tool, a, b = self._preview
            cr.save()
            cr.set_source_rgb(*_rgb(self.color))
            cr.set_line_width(self.size)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            self._path_shape(cr, tool, a, b)
            cr.stroke()
            cr.restore()
        return False

    def _path_shape(self, cr, tool, a, b):
        if tool == "line":
            cr.move_to(*a)
            cr.line_to(*b)
        elif tool == "rect":
            x, y = min(a[0], b[0]), min(a[1], b[1])
            w, h = abs(b[0] - a[0]), abs(b[1] - a[1])
            cr.rectangle(x, y, w, h)
        elif tool == "ellipse":
            cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            rx, ry = abs(b[0] - a[0]) / 2, abs(b[1] - a[1]) / 2
            if rx < 0.5 or ry < 0.5:
                return
            cr.save()
            cr.translate(cx, cy)
            cr.scale(rx, ry)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()

    # ---------------- interaction ----------------
    def _pick_tool(self, _b, tid):
        self.tool = tid
        self._sync_ribbon()
        self._refresh_status()

    def _pick_size(self, _b, sz):
        self.size = sz
        self._sync_ribbon()

    def _step_size(self, delta):
        """Move the brush size to the previous/next preset ([ and ] keys)."""
        try:
            i = SIZES.index(self.size)
        except ValueError:
            i = 0
        i = max(0, min(len(SIZES) - 1, i + delta))
        if SIZES[i] != self.size:
            self._pick_size(None, SIZES[i])

    def _pick_color(self, _b, c):
        self.color = c
        self._sync_ribbon()
        self.chip.queue_draw()

    def _open_color_chooser(self, *_):
        """Mix any colour, beyond the preset palette and the eyedropper.

        A papertone card on the app's own prompt overlay rather than the stock
        Gtk.ColorChooserDialog: that dialog is a separate window carrying the
        toolkit's own saturated palette, rounded chips and 'Custom +' editor —
        a different product's look dropped into the middle of this one, and the
        only place in the OS that was not the papertone card everything else
        uses. Three named sliders and a live well say the same thing in plain
        terms, and mix colours the fixed palette cannot."""
        try:
            mix = {"rgb": [int(round(c * 255)) for c in _rgb(self.color)]}
        except (ValueError, IndexError):
            mix = {"rgb": [0, 0, 0]}

        well = Gtk.DrawingArea()
        well.set_size_request(-1, 44)

        def _draw_well(_w, cr):
            a = _w.get_allocation()
            r, g, b = mix["rgb"]
            cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0)
            cr.rectangle(0, 0, a.width, a.height)
            cr.fill()
            cr.set_source_rgb(0xC9 / 255, 0xC4 / 255, 0xB6 / 255)
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, a.width - 1, a.height - 1)
            cr.stroke()
            return False

        well.connect("draw", _draw_well)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.pack_start(well, False, False, 0)

        sliders = []          # filled below; a recent chip drives them

        def _load_mix(hex_):
            """Put a previously mixed colour back on the sliders."""
            mix["rgb"] = [int(round(c * 255)) for c in _rgb(hex_)]
            for i, sc in enumerate(sliders):
                sc.set_value(mix["rgb"][i])
            well.queue_draw()

        # Colours mixed before, newest first, so a blue found once never has to
        # be found again. Only shown when there is something to show.
        if self._mixes:
            recent = Gtk.Box(spacing=6)
            cap = Gtk.Label(label=_t("MIXED BEFORE"), xalign=0)
            cap.get_style_context().add_class("caption")
            body.pack_start(cap, False, False, 0)
            for hex_ in self._mixes:
                b = Gtk.Button()
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.get_style_context().add_class("swatch")
                b.set_size_request(26, 26)
                b.set_tooltip_text(hex_)
                da = Gtk.DrawingArea()
                da.set_size_request(26, 26)
                da._col = hex_
                da.connect("draw", self._draw_swatch)
                b.add(da)
                b.connect("clicked", lambda _w, h=hex_: _load_mix(h))
                recent.pack_start(b, False, False, 0)
            body.pack_start(recent, False, False, 0)

        for idx, name in enumerate(("Red", "Green", "Blue")):
            row = Gtk.Box(spacing=12)
            lbl = Gtk.Label(label=_t(name), xalign=0)
            lbl.get_style_context().add_class("mixname")
            lbl.set_size_request(52, -1)
            row.pack_start(lbl, False, False, 0)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            sc.set_draw_value(False)
            sc.set_value(mix["rgb"][idx])
            sc.set_size_request(230, -1)
            sc.get_style_context().add_class("opacity")   # the app's one slider

            def _moved(scale, i=idx):
                mix["rgb"][i] = int(round(scale.get_value()))
                well.queue_draw()

            sc.connect("value-changed", _moved)
            sliders.append(sc)
            row.pack_start(sc, True, True, 0)
            body.pack_start(row, False, False, 0)

        def _use():
            self.color = "#%02X%02X%02X" % tuple(mix["rgb"])
            self._remember_mix(self.color)
            self._sync_ribbon()
            self.chip.queue_draw()
            self._refresh_status()

        self._overlay_prompt(
            _t("Mix a colour"),
            _t("Drag the sliders to mix any colour you like."),
            [("Cancel", "ilpromptcancel", None),
             (_t("Use this colour"), "ilpromptok", _use)],
            content=body)

    # ---------------- mixed colours, kept ----------------
    def _load_mixes(self):
        """Colours mixed in earlier sessions, newest first. Never raises: a
        missing, empty or hand-mangled file just means no history yet."""
        try:
            import json
            with open(CFG_FILE, encoding="utf-8") as fh:
                got = json.load(fh).get("mixes")
        except Exception:
            return []
        out = []
        for c in (got if isinstance(got, list) else []):
            if isinstance(c, str) and len(c) == 7 and c.startswith("#"):
                try:
                    _rgb(c)
                except (ValueError, IndexError):
                    continue
                if c.upper() not in out:
                    out.append(c.upper())
        return out[:MIXES_MAX]

    def _remember_mix(self, hex_):
        """Keep a colour the user mixed, newest first, and write it out so it
        is still there next time the app opens."""
        hex_ = hex_.upper()
        self._mixes = [hex_] + [c for c in self._mixes if c != hex_]
        del self._mixes[MIXES_MAX:]
        try:
            nbapp.atomic_write_json(CFG_FILE, {"mixes": self._mixes})
        except Exception:
            pass          # a colour history is never worth an error on screen

    def _on_chip_press(self, _w, _ev):
        # The active-colour well doubles as an eyedropper shortcut: clicking it
        # arms the Colour Picker so the next canvas click samples a new colour.
        self._pick_tool(None, "picker")
        return True

    def _sync_ribbon(self):
        for tid, b in self._tool_btns.items():
            sc = b.get_style_context()
            if tid == self.tool:
                sc.add_class("sel")
                try:
                    b._img.set_from_pixbuf(nbicons.pixbuf(tid, 18, "#FCFBF8"))
                except GLib.Error:
                    pass
            else:
                sc.remove_class("sel")
                try:
                    b._img.set_from_pixbuf(nbicons.pixbuf(tid, 18, "#6E695E"))
                except GLib.Error:
                    pass
        for sz, b in self._size_btns.items():
            sc = b.get_style_context()
            if sz == self.size:
                sc.add_class("sel")
            else:
                sc.remove_class("sel")
        for c, b in self._swatches.items():
            b._da.queue_draw()

    def _pos(self, ev):
        x = max(0, min(CW - 1, int(ev.x)))
        y = max(0, min(CH - 1, int(ev.y)))
        return (x, y)

    def _on_press(self, _w, ev):
        if ev.button != 1:
            return False
        p = self._pos(ev)
        # The eyedropper samples the composited canvas, so it works no matter
        # which layer is active or whether it is hidden — handle it before the
        # active-layer visibility guard.
        if self.tool == "picker":
            self._pick_from_canvas(p)
            return True
        ly = self.layers[self.active]
        if not ly.visible:
            # Painting a hidden layer changes nothing on screen; say why the
            # click did nothing instead of silently swallowing it.
            self._flash_save("Layer hidden — click its eye to show it")
            return True
        self._drawing = True
        self._start = p
        self._last = p
        if self.tool == "fill":
            if self._flood_fill(ly, p):   # snapshots internally, only on a real fill
                self._end_stroke()
            else:
                self._drawing = False     # nothing to fill — not an edit
        elif self.tool in ("pencil", "brush", "eraser"):
            self._begin_edit()        # hold pre-stroke pixels for Undo
            # seed the gesture's damage record; _stroke_seg grows it from here
            self._stroke_track = self._seg_rect(p, p, max(self.size, 8))
            self._stroke_seg(ly, p, p)
        # line / rect / ellipse only touch the surface on release, so the Undo
        # snapshot is taken there — and only for a shape that was actually
        # dragged — so a stray single click never dirties the doc or wipes Redo.
        return True

    def _on_motion(self, _w, ev):
        p = self._pos(ev)
        self._cursor = p
        self._refresh_status()
        if not self._drawing:
            return False
        ly = self.layers[self.active]
        if self.tool in ("pencil", "brush", "eraser"):
            self._stroke_seg(ly, self._last, p)
            self._last = p
        elif self.tool in ("line", "rect", "ellipse"):
            # repaint only the old and new preview rects, not the whole surface
            old = self._preview
            self._preview = (self.tool, self._start, p)
            if old:
                self._invalidate_seg(old[1], old[2], self.size)
            self._invalidate_seg(self._start, p, self.size)
        return True

    def _on_release(self, _w, ev):
        if not self._drawing:
            return False
        p = self._pos(ev)
        ly = self.layers[self.active]
        if self.tool in ("line", "rect", "ellipse"):
            old = self._preview        # last live guide: (tool, start, lastpoint)
            self._preview = None
            if p == self._start:
                # a click with no drag draws nothing: keep it a true no-op so it
                # neither dirties the doc nor clears the Redo history. Repaint
                # just the last guide's rect to clear any leftover preview line.
                self._drawing = False
                if old:
                    self.canvas.queue_draw_area(
                        *self._seg_rect(old[1], old[2], self.size))
                return True
            self._begin_edit()        # hold pre-shape pixels for Undo
            cr = cairo.Context(ly.surface)
            cr.set_source_rgb(*_rgb(self.color))
            cr.set_line_width(self.size)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            self._path_shape(cr, self.tool, self._start, p)
            cr.stroke()
            # Commit repaints only the shape's bounding box, unioned with the
            # last guide's rect so no preview pixels are left behind — never the
            # whole 1180x640 surface.
            region = self._seg_rect(self._start, p, self.size)
            if old:
                region = self._union_rect(
                    region, self._seg_rect(old[1], old[2], self.size))
            self._end_stroke(region)
            return True
        # Freehand: paint the final segment up to the release point (event
        # compression can drop the last motion sample, so the stroke would
        # otherwise stop short), then finalize with a tight repaint.
        width = max(self.size, 8) if self.tool == "eraser" else self.size
        region = self._seg_rect(self._last, p, width)
        if p != self._last:
            self._stroke_seg(ly, self._last, p)
            self._last = p
        self._end_stroke(region)
        return True

    def _on_leave(self, _w, _ev):
        self._cursor = None
        self._refresh_status()
        return False

    def _end_stroke(self, region=None):
        """Finalize a committed stroke / shape / fill: drop the drawing flag,
        repaint, bank the Undo frame and re-arm the Unsaved chip. `region` is a
        tight (x, y, w, h) box for a local edit (freehand stroke, one shape) so
        only the damaged rect recomposites; None means repaint the whole canvas
        (a flood fill can touch anywhere). Never a full 1180x640 redraw for an
        ordinary stroke."""
        self._drawing = False
        if region is None:
            self.canvas.queue_draw()
        else:
            self.canvas.queue_draw_area(*region)
        # The Undo frame has to cover every pixel the gesture touched, which for
        # a freehand drag is the accumulated _stroke_track, not the one segment
        # `region` repaints.
        track, self._stroke_track = self._stroke_track, None
        self._commit_edit(track if track is not None else region)
        self._mark_unsaved()

    # ---------------- undo / redo ----------------
    # The history is one stack of frames covering BOTH kinds of edit a drawing
    # can suffer, so every destructive action in the app is reversible:
    #
    #   ("px", layer, x, y, snapshot)   pixels — the rectangle an edit touched
    #   ("st", layers, active)          structure — the layer list itself, so
    #                                   adding and deleting a layer undo too
    #
    # A structural frame holds the old list, which still references a deleted
    # Layer object — that is what keeps its pixels alive to be restored. Frames
    # name their layer by identity, never by index, so history stays correct
    # after layers are added or removed.
    @staticmethod
    def _clamp_rect(region):
        """`region` intersected with the canvas, as (x, y, w, h) of ints."""
        x, y, w, h = region
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1 = min(CW, int(x) + int(w))
        y1 = min(CH, int(y) + int(h))
        return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))

    def _crop_surface(self, surf, region):
        """An independent ARGB32 copy of just `region` of `surf`, or None when
        the region falls outside the canvas."""
        x, y, w, h = self._clamp_rect(region)
        if w <= 0 or h <= 0:
            return None
        surf.flush()
        cp = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(cp)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(surf, -x, -y)
        cr.paint()
        cp.flush()
        return cp

    def _blit(self, ly, x, y, snap):
        """Put `snap` back into layer `ly` at (x, y), replacing those pixels."""
        cr = cairo.Context(ly.surface)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(snap, x, y)
        cr.rectangle(x, y, snap.get_width(), snap.get_height())
        cr.fill()
        ly.surface.mark_dirty()

    def _push(self, frame):
        """Add a frame to the history and drop the Redo trail (any fresh edit
        makes the redone future unreachable). Oldest frames fall off the front
        at UNDO_DEPTH so memory stays bounded."""
        self._undo_stack.append(frame)
        if len(self._undo_stack) > UNDO_DEPTH:
            self._undo_stack.pop(0)
        self._redo_stack = []

    def _begin_edit(self):
        """Hold the active layer's current pixels while an edit is in progress.
        Kept whole only until _commit_edit crops it to the rectangle the edit
        actually touched, so nothing canvas-sized is retained per stroke."""
        ly = self.layers[self.active]
        self._pending = (ly, self._crop_surface(ly.surface, (0, 0, CW, CH)))

    def _commit_edit(self, region=None):
        """Turn the held pixels into a history frame. `region` is the (x, y, w,
        h) an edit touched — the frame keeps only that; None means the edit
        could have touched anywhere (a flood fill, Clear Layer) and the whole
        layer is kept."""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        ly, before = pending
        if before is None:
            return
        if region is not None:
            x, y, w, h = self._clamp_rect(region)
            if w <= 0 or h <= 0:
                return           # the edit fell off the canvas — nothing to undo
            before = self._crop_surface(before, (x, y, w, h))
            if before is None:
                return
        else:
            x, y = 0, 0
        self._push(("px", ly, x, y, before))

    def _apply_frame(self, frame):
        """Apply one history frame and return the inverse frame for the other
        stack. None means the frame no longer applies (its layer is gone) and
        the caller should move on to the next one rather than appear to do
        nothing."""
        if frame[0] == "st":
            _kind, layers, active = frame
            inverse = ("st", list(self.layers), self.active)
            self.layers = list(layers)
            self.active = max(0, min(len(self.layers) - 1, active))
            return inverse
        _kind, ly, x, y, snap = frame
        if ly not in self.layers:
            return None
        inverse = ("px", ly, x, y, self._crop_surface(
            ly.surface, (x, y, snap.get_width(), snap.get_height())))
        self._blit(ly, x, y, snap)
        self.active = self.layers.index(ly)
        return inverse

    def _step_history(self, take, give):
        """Move one frame from `take` to `give`, applying it. Shared by Undo and
        Redo — they are the same operation with the stacks swapped."""
        while take:
            inverse = self._apply_frame(take.pop())
            if inverse is None:
                continue                      # stale frame — try the next
            give.append(inverse)
            if len(give) > UNDO_DEPTH:
                give.pop(0)
            self._rebuild_layers()
            self._refresh_status()
            self.canvas.queue_draw()
            self._mark_unsaved()
            return True
        return False

    def _undo(self):
        self._step_history(self._undo_stack, self._redo_stack)

    def _redo(self):
        self._step_history(self._redo_stack, self._undo_stack)

    def _seg_rect(self, a, b, width):
        """Bounding box (x, y, w, h) of the a→b segment, padded by half the brush
        width (+ a hair) for the round cap. Shared by the partial-repaint
        invalidation and the stroke/shape commit so the damaged region is
        computed exactly one way."""
        pad = int(width / 2) + 3
        x0 = min(a[0], b[0]) - pad
        y0 = min(a[1], b[1]) - pad
        w = abs(a[0] - b[0]) + 2 * pad
        h = abs(a[1] - b[1]) + 2 * pad
        return (int(x0), int(y0), int(w) + 1, int(h) + 1)

    @staticmethod
    def _union_rect(r1, r2):
        """Smallest (x, y, w, h) rect covering both r1 and r2."""
        x0 = min(r1[0], r2[0])
        y0 = min(r1[1], r2[1])
        x1 = max(r1[0] + r1[2], r2[0] + r2[2])
        y1 = max(r1[1] + r1[3], r2[1] + r2[3])
        return (x0, y0, x1 - x0, y1 - y0)

    def _invalidate_seg(self, a, b, width):
        """Queue a repaint of just the a→b segment's bounding box. Keeps
        freehand/shape motion events off the full-surface repaint path."""
        self.canvas.queue_draw_area(*self._seg_rect(a, b, width))

    def _stroke_seg(self, ly, a, b):
        # Grow the running record of everything this gesture has touched, so the
        # Undo frame committed at release covers the WHOLE stroke and not just
        # its last repainted segment.
        if self._stroke_track is not None:
            self._stroke_track = self._union_rect(
                self._stroke_track, self._seg_rect(a, b, max(self.size, 8)))
        cr = cairo.Context(ly.surface)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if self.tool == "eraser":
            cr.set_operator(cairo.OPERATOR_CLEAR)
            width = max(self.size, 8)
        else:
            # pencil / brush both honor the selected brush size
            width = self.size
            cr.set_source_rgb(*_rgb(self.color))
        cr.set_line_width(width)
        cr.move_to(*a)
        cr.line_to(*b)
        cr.stroke()
        self._invalidate_seg(a, b, width)

    def _flood_fill(self, ly, p):
        # Scanline (span) flood fill. The old 4-neighbour per-pixel version
        # pushed four seeds for every pixel it touched — up to ~3M list ops and
        # a huge stack for a full-canvas fill, which froze the UI. Here each
        # stack entry seeds a whole horizontal run: we expand the run left/right,
        # blit it in one C-level slice write, then scan only the rows directly
        # above and below for the next seeds. Stack depth stays tiny.
        surf = ly.surface
        surf.flush()
        stride = surf.get_stride()
        data = surf.get_data()
        px, py = p
        idx = py * stride + px * 4
        # cairo ARGB32 little-endian byte order: B, G, R, A
        target = bytes(data[idx:idx + 4])
        r, g, b = _rgb(self.color)
        newpx = bytes((int(b * 255), int(g * 255), int(r * 255), 255))
        if target == newpx:
            return False   # nothing to do — don't dirty the doc or push an Undo frame
        # Only now that a genuine fill will happen do we hold pixels for Undo.
        self._begin_edit()

        def _match(i):
            return data[i:i + 4] == target

        stack = [(px, py)]
        while stack:
            x, y = stack.pop()
            row = y * stride
            if not _match(row + x * 4):
                continue
            # expand the run left and right to its solid extent
            x1 = x
            while x1 > 0 and _match(row + (x1 - 1) * 4):
                x1 -= 1
            x2 = x
            while x2 < CW - 1 and _match(row + (x2 + 1) * 4):
                x2 += 1
            # blit the whole span in one slice write (fast C memcpy)
            i0 = row + x1 * 4
            data[i0:i0 + (x2 - x1 + 1) * 4] = newpx * (x2 - x1 + 1)
            # scan the rows above and below across [x1, x2] for new runs
            for ny in (y - 1, y + 1):
                if ny < 0 or ny >= CH:
                    continue
                nrow = ny * stride
                xx = x1
                while xx <= x2:
                    if _match(nrow + xx * 4):
                        # walk to the end of this contiguous run, seed its tail
                        while xx <= x2 and _match(nrow + xx * 4):
                            xx += 1
                        stack.append((xx - 1, ny))
                    else:
                        xx += 1
        surf.mark_dirty()
        # the caller (_on_press) runs _end_stroke() on a successful fill, which
        # repaints the canvas and re-arms the chip — no redundant full redraw here
        return True

    def _pick_from_canvas(self, p):
        tmp = cairo.ImageSurface(cairo.FORMAT_ARGB32, CW, CH)
        cr = cairo.Context(tmp)
        for ly in self.layers:
            if ly.visible:
                cr.set_source_surface(ly.surface, 0, 0)
                cr.paint_with_alpha(ly.opacity / 100.0)
        tmp.flush()
        stride = tmp.get_stride()
        data = tmp.get_data()
        i = p[1] * stride + p[0] * 4
        b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
        if a == 0:
            return
        # cairo ARGB32 stores premultiplied alpha; un-premultiply (divide RGB by
        # alpha) so partially transparent pixels sample their true, undarkened colour
        if a < 255:
            r = min(255, (r * 255 + a // 2) // a)
            g = min(255, (g * 255 + a // 2) // a)
            b = min(255, (b * 255 + a // 2) // a)
        self.color = "#%02X%02X%02X" % (r, g, b)
        self.tool = "brush"
        self._sync_ribbon()
        self.chip.queue_draw()
        self._refresh_status()

    # ---------------- layer ops ----------------
    def _select_layer(self, _b, idx):
        self.active = idx
        self._rebuild_layers()
        self._refresh_status()

    def _toggle_visible(self, btn, idx):
        self.layers[idx].visible = not self.layers[idx].visible
        self._rebuild_layers()
        self.canvas.queue_draw()
        self._mark_unsaved()  # visibility changes the saved PNG -> re-arm Unsaved

    def _add_layer(self):
        self._push(("st", list(self.layers), self.active))   # undoable
        ly = Layer("Layer %d" % self.next_id)
        self.next_id += 1
        self.layers.append(ly)
        self.active = len(self.layers) - 1
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_unsaved()  # adding a layer alters the document -> re-arm Unsaved

    def _move_layer(self, delta):
        """Move the active layer one step up (+1, towards the front) or down
        (-1) the stack, keeping it selected. A structural history frame, so a
        reorder undoes like everything else."""
        i = self.active
        j = i + delta
        if j < 0 or j >= len(self.layers):
            return
        self._push(("st", list(self.layers), self.active))
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        self.active = j
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_unsaved()   # the stacking order changes the saved PNG

    def _delete_layer(self):
        # Deleting a layer is now a history frame like any other edit, so it
        # takes one press of Ctrl+Z to get the layer and its artwork back. That
        # is a better answer than the modal that used to stand here warning the
        # user it could not be undone: the safety is real instead of a question,
        # and the chip says how to use it.
        self._do_delete_layer()

    def _do_delete_layer(self):
        if self.active == 0:
            return
        # the old list still references the layer being removed, so the frame
        # itself is what keeps its pixels alive for Undo to bring back
        self._push(("st", list(self.layers), self.active))
        name = self.layers[self.active].name
        del self.layers[self.active]
        self.active = max(0, self.active - 1)
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_unsaved()  # deleting a layer alters the document -> re-arm Unsaved
        # after _mark_unsaved, which re-renders the chip and would otherwise
        # wipe this notice off it
        self._flash_save(_t('Deleted "%s" — press Ctrl+Z to bring it back')
                         % name)

    def _on_opacity(self, scale):
        v = int(scale.get_value())
        ly = self.layers[self.active]
        # Only a real opacity change is a doc edit; a drag that rounds to the same
        # integer (or the blocked set_value on layer select) is a pure no-op — do
        # nothing, sparing a needless canvas recomposite.
        if v == ly.opacity:
            return
        ly.opacity = v
        self.op_val.set_text("%d%%" % v)
        # Live opacity drags fire value-changed many times a second. Update just
        # the active row's number label in place rather than tearing down and
        # rebuilding the whole layer list per tick (widget churn the GPU-less
        # framebuffer can't absorb); GDK coalesces the canvas repaints below to
        # the display refresh.
        lbl = self._op_labels.get(self.active)
        if lbl is not None:
            lbl.set_text("%d%%" % v)
        self.canvas.queue_draw()
        self._mark_unsaved()  # opacity change alters the saved PNG

    def _show_all_layers(self):
        for ly in self.layers:
            ly.visible = True
        self._rebuild_layers()
        self.canvas.queue_draw()
        self._mark_unsaved()  # restoring visibility alters the saved PNG

    def _clear_active_layer(self):
        if not self.layers:
            return
        self._begin_edit()   # hold the layer's pixels so Clear Layer is undoable
        ly = self.layers[self.active]
        cr = cairo.Context(ly.surface)
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        ly.surface.mark_dirty()
        self._commit_edit()   # cleared everywhere — keep the whole layer
        self.canvas.queue_draw()
        self._mark_unsaved()

    # ---------------- file: New / Open / Save / Save As (PNG) ----------------
    def _flatten_surface(self):
        """Composite the visible layers (honoring per-layer opacity) into one
        ARGB32 surface. Transparency is preserved — the exported PNG matches
        exactly what the canvas shows through the checkerboard."""
        flat = cairo.ImageSurface(cairo.FORMAT_ARGB32, CW, CH)
        cr = cairo.Context(flat)
        for ly in self.layers:
            if ly.visible:
                cr.set_source_surface(ly.surface, 0, 0)
                cr.paint_with_alpha(ly.opacity / 100.0)
        flat.flush()
        return flat

    def _write_png(self, path):
        """Flatten the visible layers and write the PNG to `path`. Returns True
        on success; never raises so a bad path can't crash the app."""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._flatten_surface().write_to_png(path)
            return True
        except Exception:
            return False

    def _file_new(self):
        """Blank canvas. Confirms first when there is unsaved work to lose
        (as Writer / Novel do), then resets to a single empty white
        Background."""
        self._confirm_discard(
            "Starting a new canvas will discard them.", self._do_file_new)

    def _do_file_new(self):
        self.layers = [Layer("Background", fill_white=True)]
        self.active = 0
        self.next_id = 2
        self._undo_stack = []
        self._redo_stack = []
        self._pending = None
        self._stroke_track = None
        self._path = None
        self._preview = None
        self._drawing = False
        self.op_scale.set_value(100)
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_empty()

    def _open_file(self, path):
        """Load a PNG as a new document: a single Background with the image
        composited on top, shrunk to fit the fixed canvas and centered. Returns
        True on success. The Background starts transparent (not white) so a PNG
        with an alpha channel keeps its transparency — painting it over a white
        fill would flatten transparent pixels to white and break round-tripping
        a transparent image back through Save."""
        try:
            img = cairo.ImageSurface.create_from_png(path)
        except Exception:
            self._flash_save("Could not open image")
            return False
        iw, ih = img.get_width(), img.get_height()
        if iw <= 0 or ih <= 0:
            self._flash_save("Could not open image")
            return False
        base = Layer("Background")   # transparent — preserve the PNG's alpha
        cr = cairo.Context(base.surface)
        scale = min(CW / iw, CH / ih, 1.0)   # shrink to fit; never enlarge
        dw, dh = iw * scale, ih * scale
        cr.translate((CW - dw) / 2, (CH - dh) / 2)
        cr.scale(scale, scale)
        cr.set_source_surface(img, 0, 0)
        try:
            cr.get_source().set_filter(cairo.FILTER_GOOD)
        except Exception:
            pass
        cr.paint()
        base.surface.flush()
        self.layers = [base]
        self.active = 0
        self.next_id = 2
        self._undo_stack = []
        self._redo_stack = []
        self._pending = None
        self._stroke_track = None
        self._path = path
        self._preview = None
        self._drawing = False
        self.op_scale.set_value(100)
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_saved()
        return True

    def _file_open(self):
        """Open a PNG. Confirms first when there is unsaved work to lose, then
        shows the chooser under $NB_HOME/Pictures."""
        self._confirm_discard(
            "Opening another image will discard them.", self._do_file_open)

    def _do_file_open(self):
        path = self._choose_file(save=False)
        if path and os.path.isfile(path):
            self._open_file(path)

    def _file_save(self):
        """Write to the current file; prompt via Save As if there is none.
        Returns True once the PNG is on disk."""
        if not self._path:
            return self._file_save_as()
        if self._write_png(self._path):
            self._mark_saved()
            return True
        self._flash_save("Could not save image")
        return False

    def _file_save_as(self):
        """Pick a path, adopt it, and write the PNG there. Returns True on a
        successful write (False if the chooser was cancelled)."""
        path = self._choose_file(save=True)
        if not path:
            return False
        if not path.lower().endswith(".png"):
            path += ".png"          # the document is always a PNG on disk
        self._path = path
        return self._file_save()

    def _choose_file(self, save):
        """Finder-style in-app picker under $NB_HOME/Pictures; path or None."""
        try:
            os.makedirs(PICS_DIR, exist_ok=True)
        except Exception:
            pass
        base = os.path.dirname(self._path) if self._path else PICS_DIR
        start = base if os.path.isdir(base) else PICS_DIR
        if save:
            suggested = (os.path.basename(self._path) if self._path
                         else "illustration.png")
            return nbpicker.save_file(self, title="Save Image As",
                                      start_dir=start, suggested_name=suggested,
                                      patterns=("*.png",), default_ext=".png")
        return nbpicker.open_file(self, title="Open Image",
                                  start_dir=start, patterns=("*.png",))

    def _flatten_pixbuf(self):
        """Composite the visible layers over a white matte and return a
        GdkPixbuf. Routes cairo -> PNG bytes -> PixbufLoader (the same safe path
        nbicons uses) rather than Gdk.pixbuf_get_from_surface, whose cairo
        foreign-type bridge isn't guaranteed on this build."""
        flat = cairo.ImageSurface(cairo.FORMAT_ARGB32, CW, CH)
        cr = cairo.Context(flat)
        cr.set_source_rgb(1, 1, 1)   # white matte so transparency isn't black
        cr.paint()
        for ly in self.layers:
            if ly.visible:
                cr.set_source_surface(ly.surface, 0, 0)
                cr.paint_with_alpha(ly.opacity / 100.0)
        flat.flush()
        buf = io.BytesIO()
        flat.write_to_png(buf)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(buf.getvalue())
        loader.close()
        return loader.get_pixbuf()

    def _copy_image(self):
        """Copy the flattened canvas to the system clipboard as an image, so it
        can be pasted into another app."""
        try:
            pb = self._flatten_pixbuf()
            if pb is None:
                return
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clip.set_image(pb)
            clip.store()
        except Exception:
            pass

    def _render_chip(self):
        """Paint the ribbon save-state chip from _chip_state. One renderer so the
        dot colour and wording can never drift apart between the three states."""
        if self._chip_state == "saved":
            markup = ('<span foreground="#7FA98C">●</span>  '
                      '<span foreground="#6E695E">Saved %s</span>'
                      % self._saved_time)
        elif self._chip_state == "unsaved":
            markup = ('<span foreground="#C8341E">●</span>  '
                      '<span foreground="#6E695E">Unsaved changes</span>')
        else:
            markup = ('<span foreground="#9A9484">●</span>  '
                      '<span foreground="#6E695E">Empty canvas</span>')
        try:
            self.save_lbl.set_markup(markup)
        except Exception:
            pass

    def _mark_saved(self):
        """Green 'Saved HH:MM' chip — shown only once the PNG is on disk. The
        next edit re-arms 'Unsaved changes' via _mark_unsaved()."""
        self._dirty = False
        self._chip_state = "saved"
        self._saved_time = time.strftime("%H:%M")
        self._flash_token += 1     # cancel any pending flash auto-restore
        self._render_chip()

    def _mark_unsaved(self):
        """Red 'Unsaved changes' chip. Single source of truth for the dirty
        state so every edit that affects the saved PNG — pixel strokes AND
        layer ops (add/delete/visibility/opacity) — flips the ribbon honestly."""
        self._dirty = True
        self._chip_state = "unsaved"
        self._flash_token += 1
        self._render_chip()

    def _mark_empty(self):
        """Grey 'Empty canvas' chip — the first-run / File ▸ New empty state."""
        self._dirty = False
        self._chip_state = "empty"
        self._flash_token += 1
        self._render_chip()

    def _flash_save(self, text):
        """Surface a transient file-op notice/error in the ribbon chip, then
        restore the real save state after a moment so the chip never keeps
        showing a stale message (crash-safe)."""
        self._flash_token += 1
        token = self._flash_token
        try:
            self.save_lbl.set_markup(
                '<span foreground="#C8341E">●</span>  '
                '<span foreground="#6E695E">%s</span>'
                % GLib.markup_escape_text(text))
        except Exception:
            return
        GLib.timeout_add(2600, self._restore_chip, token)

    def _restore_chip(self, token):
        # Only restore if no newer flash or state change happened meanwhile.
        if token == self._flash_token:
            self._render_chip()
        return False

    # ---------------- menus ----------------
    def menu_items(self, name):
        if name == "File":
            # real PNG file I/O over $NB_HOME/Pictures, plus the base Close
            return [
                ("New    Ctrl+N", self._file_new),
                ("Open…    Ctrl+O", self._file_open),
                nbapp.SEP,
                ("Save    Ctrl+S", self._file_save),
                ("Save As…    Ctrl+Shift+S", self._file_save_as),
                nbapp.SEP,
            ] + super().menu_items(name)
        if name == "Edit":
            # The base Cut/Copy/Paste/Select All act on a focused text widget,
            # of which this paint app has none — they'd be dead. Replace them
            # with real raster actions: history + copy the canvas to the clipboard.
            return [
                ("Undo    Ctrl+Z", self._undo if self._undo_stack else None),
                ("Redo    Ctrl+Y", self._redo if self._redo_stack else None),
                nbapp.SEP,
                ("Copy Image", self._copy_image),
            ]
        if name == "View":
            # Dynamic label so the action reads honestly for the current state
            # (menu_items is rebuilt every time the menu opens).
            vis = self.layers[self.active].visible
            return [
                ("Hide Active Layer" if vis else "Show Active Layer",
                 lambda: self._toggle_visible(None, self.active)),
                ("Show All Layers", self._show_all_layers),
            ]
        if name == "Layer":
            return [
                ("New Layer", self._add_layer),
                ("Delete Layer",
                 self._delete_layer if self.active != 0 else None),
                nbapp.SEP,
                ("Bring Forward",
                 (lambda: self._move_layer(1))
                 if self.active < len(self.layers) - 1 else None),
                ("Send Back",
                 (lambda: self._move_layer(-1)) if self.active > 0 else None),
                nbapp.SEP,
                ("Clear Layer", self._clear_active_layer),
                nbapp.SEP,
                ("Opacity 100%", lambda: self.op_scale.set_value(100)),
                ("Opacity  50%", lambda: self.op_scale.set_value(50)),
                ("Opacity  25%", lambda: self.op_scale.set_value(25)),
            ]
        return super().menu_items(name)

    # ---------------- keyboard ----------------
    def _on_key(self, w, ev):
        # Esc cancels an open save-prompt first (so it never re-triggers a
        # close); then Ctrl+N/O/S and Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y drive the
        # File menu and Undo/Redo; unmodified letter keys pick tools and [ / ]
        # step the brush size; anything else falls through to the base.
        if ev.keyval == Gdk.KEY_Escape and self._close_saveprompt():
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            shift = ev.state & Gdk.ModifierType.SHIFT_MASK
            if ev.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self._redo() if shift else self._undo()
                return True
            if ev.keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._redo()
                return True
            if ev.keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self._file_save_as() if shift else self._file_save()
                return True
            if ev.keyval in (Gdk.KEY_o, Gdk.KEY_O):
                self._file_open()
                return True
            if ev.keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._file_new()
                return True
        elif (not (ev.state & Gdk.ModifierType.MOD1_MASK) and not self._drawing
                and self._saveprompt_layer is None and self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            # Single-key tool + brush-size shortcuts, only when nothing else is
            # capturing keys (no overlay/menu open) and we're not mid-stroke.
            kl = Gdk.keyval_to_lower(ev.keyval)
            tool = _KEY_TOOLS.get(kl)
            if tool is not None:
                self._pick_tool(None, tool)
                return True
            if kl == Gdk.KEY_bracketleft:
                self._step_size(-1)
                return True
            if kl == Gdk.KEY_bracketright:
                self._step_size(1)
                return True
        return super()._on_key(w, ev)

    # ---------------- close guard ----------------
    def _on_delete(self, *_):
        # Both Esc and the red logo dot reach here via self.close(). When there
        # are unsaved changes, veto the destroy and show the save-prompt; the
        # prompt's buttons call self.destroy() directly, bypassing this guard.
        if not self._dirty:
            return False
        if self._saveprompt_layer is not None:
            return True   # a prompt is already up — don't stack another
        self._prompt_close()
        return True

    def _overlay_prompt(self, title, body, buttons, content=None):
        """Modal in-window prompt: a scrim over a warm-paper card with a serif
        title, a body line, and right-aligned buttons. `buttons` is a list of
        (label, style_class, callback) in display order; the callback runs after
        the prompt is dismissed, and a None callback (e.g. Cancel) just closes
        it. `content` is an optional widget shown under the body line (the
        colour mixer's well and sliders). Only one prompt shows at a time; Esc
        or a scrim click dismisses it. Shared by the close guard, the New/Open
        discard confirm, the delete-layer confirm and the colour mixer."""
        self._close_saveprompt()
        self._close_menu()
        self._close_about()
        alloc = self.get_allocation()
        # Size the scrim to the LIVE window, falling back to the real primary
        # monitor size — never a hardcoded 1920x1080. On a smaller real panel a
        # 1920-wide scrim overflows and the centred card lands off-centre / off
        # the edge; matching the live allocation keeps the card on-screen.
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh

        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.get_style_context().add_class("ilscrim")
        scrim.connect("button-press-event",
                      lambda *a: (self._close_saveprompt(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("ilprompt")
        tl = Gtk.Label(label=title, xalign=0)
        tl.get_style_context().add_class("ilprompttitle")
        card.pack_start(tl, False, False, 0)
        bd = Gtk.Label(label=body, xalign=0)
        bd.get_style_context().add_class("ilpromptbody")
        bd.set_line_wrap(True)
        bd.set_max_width_chars(34)
        card.pack_start(bd, False, False, 0)
        if content is not None:
            card.pack_start(content, False, False, 0)

        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        focus_btn = None
        for label, style, cb in buttons:
            btn = Gtk.Button(label=label)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class(style)
            btn.connect(
                "clicked",
                lambda _w, fn=cb: (self._close_saveprompt(), fn and fn())[1])
            btnrow.pack_start(btn, False, False, 0)
            # Rest keyboard focus on the safe (Cancel) button so a stray
            # Space/Enter can never fire a destructive action by default;
            # fall back to the first button if there is no Cancel.
            if focus_btn is None or style == "ilpromptcancel":
                focus_btn = btn
        card.pack_start(btnrow, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # centre on the real window using the card's measured size (correct at
        # any resolution, not a fixed 1920x1080)
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 420
        ch = nat.height if nat.height > 1 else 200
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        self._saveprompt_layer = layer
        if focus_btn is not None:
            focus_btn.grab_focus()

    def _confirm_discard(self, consequence, on_confirm):
        """Run `on_confirm` immediately when there is nothing to lose; otherwise
        ask first (Cancel / Discard) so New / Open never silently drop unsaved
        work. `consequence` completes the sentence shown to the user."""
        if not self._dirty:
            on_confirm()
            return
        self._overlay_prompt(
            "Discard changes?",
            "The current image has unsaved changes. " + consequence,
            [("Cancel", "ilpromptcancel", None),
             ("Discard", "ilpromptdiscard", on_confirm)])

    def _prompt_close(self):
        # Unsaved-work guard on close (Esc / logo). Discard drops the work,
        # Cancel keeps the window open, Save writes the PNG first then closes.
        self._overlay_prompt(
            "Unsaved changes",
            "This image has unsaved changes. Save it before closing?",
            [("Discard", "ilpromptdiscard",
              lambda: self._close_and_destroy(False)),
             ("Cancel", "ilpromptcancel", None),
             ("Save", "ilpromptok",
              lambda: self._close_and_destroy(True))])

    def _close_saveprompt(self):
        layer = self._saveprompt_layer
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._saveprompt_layer = None
            return True
        return False

    def _close_and_destroy(self, save):
        self._close_saveprompt()
        if save and not self._file_save():
            # Save As was cancelled or the write failed — abort the close so
            # the user doesn't lose unsaved work to a dismissed chooser
            return
        self.destroy()           # destroy skips delete-event, so no re-prompt

    # ---------------- css ----------------
    def _install_css(self):
        css = b"""
        .ribbon *, .lpanel *, .statusbar * {
            font-family: "Nimbus Sans","Helvetica",sans-serif; }

        /* ---- ribbon (top tool bar) ---- */
        .ribbon { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                  padding: 14px 20px; min-height: 88px; }
        .caption { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                   font-weight: 700; }
        .vsep { background: #D7D2C5; }

        .toolbtn { min-width: 36px; min-height: 36px; padding: 0;
                   background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 2px; box-shadow: none; }
        .toolbtn:hover { background: #F4F2EC; }
        .toolbtn.sel { background: #C8341E; border-color: #C8341E; }
        .toolbtn.sel:hover { background: #B12C18; border-color: #B12C18; }

        /* Size buttons share the tool/custom/layer buttons' border swatch
           (#C9C4B6), not a one-off lighter beige, so the ribbon controls read
           as one set. Selected = the darker-beige, per the design language
           (signage-red is reserved for the active-tool / alert state). */
        .sizebtn { min-width: 32px; min-height: 36px; padding: 0;
                   background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 2px; box-shadow: none; }
        .sizebtn:hover { background: #F4F2EC; }
        .sizebtn.sel { background: #E6DFCE; border-color: #C4BFB1; }

        .chip { border: none; }
        .swatch { padding: 0; margin: 0; min-width: 22px; min-height: 22px;
                  background: transparent; border: none; box-shadow: none; }
        .custombtn { min-height: 24px; padding: 2px 8px; font-size: 12px;
                     color: #1A1916; background: #FCFBF8;
                     border: 1px solid #C9C4B6; border-radius: 2px;
                     box-shadow: none; }
        .custombtn:hover { background: #F4F2EC; }
        .savestate { font-size: 12px; color: #6E695E; }

        /* ---- canvas mat ---- */
        /* Papertone field on the scroll AND its viewport, so the field fills the
           area whether the canvas is centred (large panel) or scrolled (small). */
        .mat, .mat viewport { background: #DED4C2; }
        /* balancing left rail - same opaque field so it reads as one mat */
        .matrail { background: #DED4C2; }
        .canvasframe { background: #FCFBF8; padding: 1px;
                       border: 1px solid #C9C4B6;
                       box-shadow: 4px 4px 0 rgba(26,25,22,0.10); }

        /* ---- layers panel ---- */
        .lpanel { background: #F1EEE6; border-left: 1px solid #C9C4B6; }
        .lhead { padding: 18px 20px; border-bottom: 1px solid #D7D2C5; }
        .ltitle { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                  font-weight: 700; }
        .liconbtn { min-width: 28px; min-height: 28px; padding: 0; margin-left: 6px;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 2px; box-shadow: none; }
        .liconbtn:hover { background: #F4F2EC; }
        .liconbtn.disabled { background: #F1EEE6; border-color: #D7D2C5; }
        .llist { padding: 8px 10px; }
        .lrow { padding: 8px 10px; border-radius: 2px; box-shadow: none;
                background: transparent; border: none; }
        .lrow:hover { background: #F4F2EC; }
        .lrow.active { background: #FCFBF8; box-shadow: inset 3px 0 0 #C8341E; }
        .lname { font-size: 14px; color: #1A1916; }
        .lrow.active .lname { font-weight: 600; }
        .lopacity { font-size: 11px; color: #9A9484; }
        .eyebtn { min-width: 26px; min-height: 26px; padding: 0;
                  background: transparent; border: none; box-shadow: none; }
        .lfoot { padding: 18px 20px; border-top: 1px solid #D7D2C5; }
        .opacity { padding: 0; }
        .opacity trough { min-height: 4px; background: #D7D2C5;
                          border: none; border-radius: 2px; }
        .opacity highlight { background: #1A1916; border-radius: 2px; }
        .opacity slider { min-width: 16px; min-height: 16px; margin: -7px;
                          background: #1A1916; border: none; border-radius: 50%; }

        /* ---- status bar ---- */
        .statusbar { background: #F1EEE6; border-top: 1px solid #C9C4B6;
                     min-height: 34px; padding: 0 20px; }
        .stlabel { font-size: 12px; color: #6E695E; }

        /* ---- unsaved-changes close prompt ---- */
        .ilscrim { background: rgba(26,25,22,0.28); }
        /* min-width sets the card's measure: a wrapping label's natural width
           is computed from the font's AVERAGE character, which for this face
           is far narrower than the real text, so the body was breaking into a
           ragged four-line column half the width of its own title. */
        .ilprompt { background: #FCFBF8; border: 1px solid #1A1916;
                    padding: 26px 30px; min-width: 330px; }
        .ilprompt * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .ilprompttitle { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 21px; color: #1A1916; }
        .ilpromptbody { font-size: 14px; color: #6E695E; }
        .mixname { font-size: 13px; color: #1A1916; }
        .ilpromptok { min-height: 34px; padding: 0 18px; border: 1px solid #1A1916;
                      border-radius: 2px; background: #1A1916; color: #FCFBF8;
                      box-shadow: none; font-size: 14px; font-weight: 600; }
        .ilpromptok:hover { background: #2A2620; }
        .ilpromptcancel { min-height: 34px; padding: 0 16px; color: #2A2620;
                          border: 1px solid #C4BFB1; border-radius: 2px;
                          background: #FCFBF8; box-shadow: none; font-size: 14px; }
        .ilpromptcancel:hover { background: #ECE8DD; }
        .ilpromptdiscard { min-height: 34px; padding: 0 16px; color: #C8341E;
                           border: 1px solid #E0B3AA; border-radius: 2px;
                           background: #FCFBF8; box-shadow: none; font-size: 14px; }
        .ilpromptdiscard:hover { background: #F6E7E3; }

        /* The system theme sets `* { color: ink }`, which matches a button's
           LABEL node directly, so a colour set on the button itself never
           reaches its text: the ink-filled primary button came up as a blank
           black slab with its "Save" invisible, and Discard lost its red. */
        .ilpromptok label { color: #FCFBF8; }
        .ilpromptdiscard label { color: #C8341E; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(Illustrator)
