#!/usr/bin/env python3
"""
Widget Settings — choose which reminder tiles the desktop board shows.

Opened from the desktop's own right-click menu, because the board is what it
configures. It is a real app window (nbapp.AppWindow: the same chrome, menu bar
and Papertone treatment as Finder and everything else) rather than a bare GTK
dialog — a settings screen that looks like a system error box is worse than no
settings screen.

It is the ONLY writer of widgets.json. The board reads that file and follows it
live through a file monitor, so a switch here takes effect the moment it is
flipped, with no app-close cycle in between. Tasks and the calendar are not
listed: they are pinned to the board's column and are the two things the
desktop is for.
"""
import json
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo     # noqa: E402

import nbapp                                              # noqa: E402
import widgets                                            # noqa: E402
from nbi18n import _t                                     # noqa: E402

STORE = os.path.join(os.environ.get("NB_HOME", os.path.expanduser("~")),
                     ".config", "notebook", "widgets.json")

# The reading column every settings screen in this OS is held to, so one short
# row does not stretch a switch to the far side of a 1920 panel.
COLUMN_W = 620

# What each tile actually tells you, in the words someone deciding whether to
# switch it on would use. Keyed by the board's tile ids.
#
# `.get`, never `[tid]`, at the point of use: a tile added to the board without
# a line here must cost that row its description, not stop Widget Settings
# opening at all. It did exactly that once.
TILE_BLURB = {
    "academics": "Today's classes, with times and rooms",
    "homework": "Assignments and their due dates",
    "meals": "Breakfast, lunch and dinner from today's meal plan",
    "workout": "Today's sets against the goal, exercise by exercise",
    "journal": "Whether today has been written",
    "bills": "What is due next, and what it comes to this month",
    "accounting": "Cash balance and recent entries",
    "birthdays": "Whose birthday is coming, and how soon",
    "reading": "The books on the shelf and how far through each one is",
    "language": "Whether today's practice goal has been met",
    "novel": "The manuscript, chapter by chapter, counted in words",
}

# The board's own colours, so the preview below is the desktop and not an
# illustration of it.
_PAPER = (0xF8 / 255.0, 0xF7 / 255.0, 0xF2 / 255.0)
_DESK = (0xDE / 255.0, 0xD4 / 255.0, 0xC2 / 255.0)
_FRAME = (0x1A / 255.0, 0x19 / 255.0, 0x16 / 255.0)
_MUTED = (0x9A / 255.0, 0x94 / 255.0, 0x84 / 255.0)


class BoardPreview(Gtk.DrawingArea):
    """A small live picture of the desktop, drawn to the board's own geometry.

    Switching a name on and off in a list says nothing about what the desktop
    will look like — which tile moves up into the gap, what the grid does when
    only two are left, where the pinned pair sits. This is the same 3x2 grid
    plus pinned column the board lays out, so the answer is on screen while the
    switch is being flipped rather than after closing the window.

    Drawn rather than built from widgets: it has to be small, it must not
    depend on a font having any particular glyph, and nothing in it is
    interactive."""

    def __init__(self, get_state):
        super().__init__()
        self._get_state = get_state
        self.set_size_request(-1, 176)
        self.connect("draw", self._draw)

    def _draw(self, _area, cr):
        try:
            self._paint(cr)
        except Exception:                                   # noqa: BLE001
            pass          # a preview must never be the thing that crashes
        return False

    def _paint(self, cr):
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        if w <= 8 or h <= 8:
            return
        on, order = self._get_state()

        cr.set_source_rgb(*_DESK)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        pad, gap = 7.0, 5.0
        # GRID_COLS counts the pinned column, so the tiles get all but one of
        # them and the pinned pair gets the last.
        cols = max(1, widgets.TILE_COLS)
        rows = max(1, widgets.TILE_ROWS)
        colw = (w - 2 * pad - gap * cols) / (cols + 1)
        rowh = (h - 2 * pad - gap * (rows - 1)) / rows

        shown = [t for t in order if on.get(t)][:cols * rows]
        for slot in range(cols * rows):
            x = pad + (slot % cols) * (colw + gap)
            y = pad + (slot // cols) * (rowh + gap)
            if slot < len(shown):
                self._card(cr, x, y, colw, rowh,
                           _t(widgets.TILE_TITLE[shown[slot]]))
            else:
                self._empty(cr, x, y, colw, rowh)

        # The pinned column: Tasks over the calendar, always. Their split is
        # the tile grid's own row split, because on the real board the two line
        # up with the rows beside them.
        px = pad + cols * (colw + gap)
        pw = w - pad - px
        self._card(cr, px, pad, pw, rowh, _t("Tasks"), pinned=True)
        self._card(cr, px, pad + rowh + gap, pw, rowh, _t("Calendar"),
                   pinned=True)

    @staticmethod
    def _show_text(cr, x, y, text, size, bold=False):
        """Draw `text` with its BASELINE at y, through Pango.

        NOT cr.show_text: the toy API binds one face and does no per-glyph
        fallback, and Nimbus Sans carries no CJK, Devanagari or Hebrew — so
        every tile title in this preview came out as .notdef (invisible, not a
        box) in ja/zh/ko/hi/yi. The card frames drew, the titles did not.
        """
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription("Nimbus Sans")
        fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
        fd.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        cr.move_to(x, y - layout.get_baseline() / Pango.SCALE)
        PangoCairo.show_layout(cr, layout)

    def _card(self, cr, x, y, w, h, title, pinned=False):
        if w < 2 or h < 2:
            return
        cr.set_source_rgb(*_PAPER)
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgb(*_FRAME)
        cr.set_line_width(1.0)
        cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
        cr.stroke()
        # the card's header rule, the one piece of a real card that reads at
        # this size
        head = min(15.0, h * 0.34)
        cr.move_to(x + 0.5, y + head + 0.5)
        cr.line_to(x + w - 0.5, y + head + 0.5)
        cr.stroke()
        cr.save()
        cr.rectangle(x + 3, y, w - 6, head)
        cr.clip()
        cr.set_source_rgb(*_FRAME)
        self._show_text(cr, x + 5, y + head - 4.5, title, 9.5, bold=pinned)
        cr.restore()

    def _empty(self, cr, x, y, w, h):
        """A slot no tile is in. Dashed, so it reads as space left on purpose
        rather than as a card that failed to draw."""
        if w < 2 or h < 2:
            return
        cr.set_source_rgb(*_MUTED)
        cr.set_line_width(1.0)
        cr.set_dash([3.0, 3.0])
        cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
        cr.stroke()
        cr.set_dash([])


class WidgetSettings(nbapp.AppWindow):
    app_name = "Widget Settings"
    menus = ("File", "View")

    def __init__(self):
        super().__init__()
        self.data, self.order = self._load()
        self._switches = {}
        self._rows = {}
        self._build()
        self._install_css()
        self._refresh_status()

    # -- store ---------------------------------------------------------------

    def _load(self):
        """Which tiles are on, and the order they sit in. Tolerates anything
        that is not the right shape: a hand-edited or truncated store must open
        this screen showing the defaults, not stop it opening."""
        try:
            with open(STORE, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = {}
        # widgets.board_state is what the DESKTOP reads this file with, so the
        # writer and the reader can never disagree about what it means -- down
        # to which tile an older store's missing entries default to.
        return widgets.board_state(raw)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STORE), exist_ok=True)
            nbapp.atomic_write_json(
                STORE, {"tiles": self.data, "order": list(self.order)},
                indent=1)
        except OSError:
            pass          # a read-only home must never stop the app working

    # -- ui ------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app.
        css = b"""
        .ws-main { background: #FCFBF8; }
        .ws-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .ws-title { font-size: 24px; font-weight: 700; color: #1A1916; }
        .ws-lede { font-size: 14px; color: #6E695E; }
        .ws-eyebrow { font-size: 11px; letter-spacing: 0.14em;
                      font-weight: 700; color: #9A9484; }
        .ws-rule { background: #D7D2C5; }
        .ws-row { padding: 12px 8px; }
        .ws-row-hot { background: #F4F2EC; }
        .ws-row-sep { border-top: 1px solid #D7D2C5; }
        .ws-name { font-size: 15px; color: #1A1916; }
        .ws-blurb { font-size: 13px; color: #6E695E; }
        /* the board's position number, in the board's own furniture colour */
        .ws-slot { font-size: 12px; color: #9A9484; }
        /* "4 / 6" beside the section name: how much of the board is spoken
           for. Tabular-ish weight so it reads as a count, not a heading. */
        .ws-count { font-size: 12px; font-weight: 700; color: #6E695E; }
        /* Why a control below is dead, or why a list is empty. Stated once per
           list rather than left to be discovered by pressing something. */
        .ws-reason { font-size: 13px; color: #8A857A; padding: 2px 0 10px 28px; }
        .ws-preview { border: 1px solid #D7D2C5; padding: 0; }
        .ws-move { min-width: 26px; min-height: 24px; padding: 0;
                   background: #FCFBF8; border: 1px solid #D7D2C5;
                   border-radius: 8px; box-shadow: none; color: #3A362E;
                   font-size: 11px; }
        .ws-move:hover { background: #EFEBE0; }
        .ws-move:disabled { color: #C9C4B6; background: #F8F7F2; }
        /* Information, not a disabled control: no fill, just a rule down the
           side. A grey panel here read as a section that had been switched
           off, which is the opposite of "always on". */
        .ws-pinned { border-left: 2px solid #D7D2C5; padding: 2px 0 2px 16px; }
        .ws-pinnedname { font-size: 14px; color: #1A1916; }
        .ws-pinnedwhy { font-size: 12px; color: #6E695E; }
        .ws-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #D7D2C5; background: #F8F7F2; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                       # noqa: BLE001
            pass          # styling is cosmetic; never block launch

    @staticmethod
    def _wrap(label):
        """Wrap a paragraph INSIDE the reading column.

        A wrapping GtkLabel still asks for its whole unwrapped line as its
        natural width, and the column is sized from what its children ask for
        — so one longer sentence in the lede stretched the reading column from
        620px to 851px and pulled every switch across the screen with it. A
        translation would have done the same. max_width_chars pins the natural
        width down; the column's own size request decides where the text
        actually wraps."""
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(1)
        return label

    def _build(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("ws-main")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        sw, _sh = nbapp.screen_size()
        inner.set_size_request(max(360, min(COLUMN_W, sw - 80)), -1)
        inner.set_margin_top(30)
        inner.set_margin_bottom(28)

        title = Gtk.Label(label=_t("Widgets"), xalign=0)
        title.get_style_context().add_class("ws-title")
        inner.pack_start(title, False, False, 0)
        lede = Gtk.Label(
            label=_t("The desktop holds six tiles beside Tasks and the "
                     "calendar. Choose which six, and the order they sit in."),
            xalign=0)
        lede.get_style_context().add_class("ws-lede")
        self._wrap(lede)
        lede.set_margin_top(4)
        inner.pack_start(lede, False, False, 0)

        # The desktop as it will look, updated on every change.
        self.preview = BoardPreview(lambda: (self.data, self.order))
        frame = Gtk.Box()
        frame.get_style_context().add_class("ws-preview")
        frame.pack_start(self.preview, True, True, 0)
        frame.set_margin_top(18)
        inner.pack_start(frame, False, False, 0)

        rule = Gtk.Box()
        rule.get_style_context().add_class("ws-rule")
        rule.set_size_request(-1, 1)
        rule.set_margin_top(24)
        rule.set_margin_bottom(6)
        inner.pack_start(rule, False, False, 0)

        self._rowbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.pack_start(self._rowbox, False, False, 0)
        self._fill_rows()

        # The two that cannot be switched off, said plainly rather than left as
        # a silent absence from the list above.
        pin_head = Gtk.Label(label=_t("ALWAYS ON THE DESKTOP"), xalign=0)
        pin_head.get_style_context().add_class("ws-eyebrow")
        pin_head.set_margin_top(26)
        pin_head.set_margin_bottom(8)
        inner.pack_start(pin_head, False, False, 0)

        pinned = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pinned.get_style_context().add_class("ws-pinned")
        for name, why in ((_t("Tasks"), _t("Down the right-hand side")),
                          (_t("Calendar"), _t("Beside Tasks, with this month "
                                              "and today's events"))):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            nm = Gtk.Label(label=name, xalign=0)
            nm.get_style_context().add_class("ws-pinnedname")
            wl = Gtk.Label(label=why, xalign=0)
            wl.get_style_context().add_class("ws-pinnedwhy")
            self._wrap(wl)
            row.pack_start(nm, False, False, 0)
            row.pack_start(wl, False, False, 0)
            pinned.pack_start(row, False, False, 0)
        inner.pack_start(pinned, False, False, 0)

        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        holder.pack_start(inner, True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(holder)
        main.pack_start(scroll, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("ws-status")
        # A fixed bottom strip must be pinned, or GTK3 propagates vexpand up
        # from the content above and the strip floats mid-window.
        self.status.set_vexpand(False)
        main.pack_start(self.status, False, False, 0)

        self.content.pack_start(main, True, True, 0)

    # -- the two lists -------------------------------------------------------
    #
    # There are eleven tiles and six slots, so a single flat list of switches
    # cannot say the thing that matters: WHICH SIX are on the desktop. Split in
    # two, the screen answers that by its shape — the top list IS the board, in
    # board order, and the bottom one is everything still to choose from.

    def _shown(self):
        """The switched-on tiles, in board order. This is what the desktop
        draws (capped at the slots there are), so it is what the top list
        shows."""
        return [t for t in self.order if self.data.get(t)]

    def _full(self):
        return len(self._shown()) >= widgets.slot_count()

    def _fill_rows(self):
        """(Re)build both lists. Wholesale, on every change: the lists ARE the
        state — which section a tile is in, what position it holds, whether its
        switch can be flipped — so a row left where it was would be the screen
        disagreeing with the desktop it is describing."""
        for child in self._rowbox.get_children():
            self._rowbox.remove(child)
        self._switches.clear()
        shown = self._shown()
        slots = widgets.slot_count()
        off = [t for t in self.order if not self.data.get(t)]

        self._rowbox.pack_start(
            self._section(_t("ON THE DESKTOP"),
                          "%d / %d" % (min(len(shown), slots), slots)),
            False, False, 0)
        if not shown:
            self._rowbox.pack_start(
                self._note(_t("Nothing is on the desktop. Switch a tile on "
                              "below.")), False, False, 0)
        for i, tid in enumerate(shown):
            self._rowbox.pack_start(
                self._tile_row(tid, first=(i == 0), pos=i,
                               last=(i == len(shown) - 1), shown=True),
                False, False, 0)

        if off:
            self._rowbox.pack_start(
                self._section(_t("NOT ON THE DESKTOP"), "", top=26),
                False, False, 0)
            # Said once, here, rather than left for someone to discover by
            # flipping a switch that does nothing. The switches below really
            # are dead while the board is full, so the screen has to say so.
            if self._full():
                self._rowbox.pack_start(
                    self._note(_t("The board is full. Switch one off above to "
                                  "make room.")), False, False, 0)
            for i, tid in enumerate(off):
                self._rowbox.pack_start(
                    self._tile_row(tid, first=(i == 0), shown=False),
                    False, False, 0)
        self._rowbox.show_all()

    @staticmethod
    def _section(title, count="", top=6):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(top)
        row.set_margin_bottom(6)
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.get_style_context().add_class("ws-eyebrow")
        row.pack_start(lbl, True, True, 0)
        if count:
            num = Gtk.Label(label=count, xalign=1)
            num.get_style_context().add_class("ws-count")
            row.pack_end(num, False, False, 0)
        return row

    def _note(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("ws-reason")
        self._wrap(lbl)
        return lbl

    def _move(self, tid, delta):
        """Move a tile one place along THE BOARD, not one place along the
        stored order.

        Those are different lists once some tiles are switched off: swapping
        with the neighbouring entry in `order` can swap with a tile that is not
        on the desktop, and the button then visibly does nothing. The shown
        tiles are reordered among themselves and written back into the
        positions they already occupied, so the switched-off tiles keep their
        places and come back where they were left."""
        shown = self._shown()
        if tid not in shown:
            return
        i = shown.index(tid)
        j = i + delta
        if not (0 <= j < len(shown)):
            return
        shown[i], shown[j] = shown[j], shown[i]
        slots = [k for k, t in enumerate(self.order) if self.data.get(t)]
        for k, t in zip(slots, shown):
            self.order[k] = t
        self._save()
        self._fill_rows()
        self.preview.queue_draw()
        self._refresh_status()

    def _tile_row(self, tid, first=False, pos=0, last=False, shown=True):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        ctx = row.get_style_context()
        ctx.add_class("ws-row")
        if not first:
            ctx.add_class("ws-row-sep")

        # Where this tile lands on the board, so the row and the picture above
        # it are talking about the same thing. A tile in the lower list has no
        # position, and a hand-edited store with more tiles on than the board
        # can draw leaves the ones past the cap marked "-" rather than
        # pretending they are somewhere.
        slot = Gtk.Label(xalign=0.5)
        slot.get_style_context().add_class("ws-slot")
        if shown:
            if pos < widgets.slot_count():
                slot.set_text("%d" % (pos + 1))
            else:
                slot.set_text("-")
                slot.set_tooltip_text(_t("The board has room for six tiles"))
        else:
            slot.set_text("")
        slot.set_size_request(20, -1)
        slot.set_valign(Gtk.Align.CENTER)
        row.pack_start(slot, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=_t(widgets.TILE_TITLE[tid]), xalign=0)
        name.get_style_context().add_class("ws-name")
        text.pack_start(name, False, False, 0)
        # .get, not [tid]: a tile added to the board without a line here
        # should cost that row its description, not stop Widget Settings
        # opening at all. It did exactly that once.
        blurb = Gtk.Label(label=_t(TILE_BLURB.get(tid, "")), xalign=0)
        blurb.get_style_context().add_class("ws-blurb")
        self._wrap(blurb)
        text.pack_start(blurb, False, False, 0)
        row.pack_start(text, True, True, 0)

        # A switch that cannot change anything is left visible and greyed, the
        # way an unavailable menu item is: removing it would make the lower
        # list shift under the pointer as tiles are switched on and off, and
        # hide what the app can do. The reason is printed once above the list.
        blocked = not shown and self._full()
        sw = nbapp.PaperSwitch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(self.data.get(tid)))
        sw.set_sensitive(not blocked)
        sw.connect("notify::active", self._on_toggle, tid)
        sw.set_tooltip_text(
            _t("The board is full. Switch one off to make room.") if blocked
            else (_t("Take %s off the desktop") if shown
                  else _t("Show %s on the desktop"))
            % _t(widgets.TILE_TITLE[tid]))
        self._switches[tid] = sw
        row.pack_end(sw, False, False, 0)

        # Up/down rather than dragging: a drag is fiddly and there is no undo
        # on this screen. Only on a tile that is ON — position is a fact about
        # the board, and a tile that is not on it has none. Insensitive at the
        # ends, so the limits are visible instead of being discovered by a
        # press that does nothing.
        if shown:
            moves = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            moves.set_valign(Gtk.Align.CENTER)
            for glyph, delta, tip, stop in (("▲", -1, "Move up", first),
                                            ("▼", 1, "Move down", last)):
                b = Gtk.Button(label=glyph)
                b.get_style_context().add_class("ws-move")
                b.set_tooltip_text(_t(tip))
                b.set_sensitive(not stop)
                b.connect("clicked",
                          lambda _b, t=tid, d=delta: self._move(t, d))
                moves.pack_start(b, False, False, 0)
            row.pack_end(moves, False, False, 0)

        # The whole row is the target, not just the 48px switch: the name and
        # the line explaining it are what someone aims at, and a click that
        # lands on the words and does nothing reads as a dead control. The
        # EventBox is windowless, so it sits UNDER the switch's own input
        # window and a click on the switch is still the switch's.
        hit = Gtk.EventBox()
        hit.set_visible_window(False)
        hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                       | Gdk.EventMask.ENTER_NOTIFY_MASK
                       | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        hit.add(row)
        hit.set_tooltip_text(sw.get_tooltip_text() or "")
        hit.connect("button-press-event", self._on_row_press, tid)
        # A row that can be clicked has to look like one, or the target is a
        # secret. Flipping a style class costs nothing to draw and needs no
        # window of its own.
        hit.connect("enter-notify-event",
                    lambda _w, _e, c=ctx: (c.add_class("ws-row-hot"), False)[1])
        hit.connect("leave-notify-event",
                    lambda _w, _e, c=ctx: (c.remove_class("ws-row-hot"),
                                           False)[1])
        return hit

    def _on_row_press(self, _w, ev, tid):
        try:
            if ev.button != 1:
                return False
        except AttributeError:
            return False
        sw = self._switches.get(tid)
        # An insensitive switch still changes when its "active" property is
        # set, so the whole-row target has to check the same condition the
        # switch does — otherwise clicking the words beside a greyed switch
        # would do what the switch itself refuses to.
        if sw is not None and sw.get_sensitive():
            sw.set_active(not sw.get_active())   # notify::active does the rest
        return True

    def _refresh_status(self):
        """How many tiles are on -- and, when more are on than the board has
        room for, how many are not being drawn.

        There are more tiles than slots, so counting the SWITCHES would be a
        plain untruth about what is on screen. Any tile past the cap already
        shows "-" where its position would be; this is the same fact said once
        for the board as a whole. It is normally unreachable -- the lower
        list's switches go dead when the board is full -- but a hand-edited
        store can still arrive with eight tiles on, and the screen must not
        misdescribe it."""
        slots = widgets.slot_count()
        n = sum(1 for tid in widgets.TILE_ORDER if self.data.get(tid))
        shown = min(n, slots)
        if not n:
            self.status.set_text(_t("No widgets on the desktop"))
            return
        text = _t("%d widget%s on the desktop") % (shown,
                                                   "" if shown == 1 else "s")
        if n > shown:
            # The separator is composed OUTSIDE the catalog keys: a key with
            # padding baked into it matches nothing and shows English.
            text += "  ·  " + _t("%d with no slot") % (n - shown)
        self.status.set_text(text)

    # -- actions -------------------------------------------------------------

    def _on_toggle(self, sw, _param, tid):
        self.data[tid] = bool(sw.get_active())
        # A tile that goes on or off changes SECTION, renumbers everything
        # under it, and can be what makes the board full -- so both lists are
        # rebuilt, not just the row that was touched.
        self._after_change()

    def _fill_board(self):
        """Switch tiles on, in board order, until the grid is full.

        Not "switch everything on": there are more tiles than the board can
        draw, so that would leave some of them switched on and undrawable --
        the exact state the lower list's dead switches exist to prevent."""
        room = widgets.slot_count() - len(self._shown())
        for tid in self.order:
            if room <= 0:
                break
            if not self.data.get(tid):
                self.data[tid] = True
                room -= 1
        self._after_change()

    def _set_all(self, on):
        for tid in widgets.TILE_ORDER:
            self.data[tid] = on
        self._after_change()

    def _reset_order(self):
        self.order = list(widgets.TILE_ORDER)
        self._after_change()

    def _after_change(self):
        """Save, then redraw everything that describes the board. One place,
        because the picture, the two lists and the status line all describe the
        same state, and any of them left stale is the screen disagreeing with
        the desktop."""
        self._save()
        self._refresh_status()
        self.preview.queue_draw()
        self._fill_rows()

    def menu_items(self, name):
        if name == "File":
            return [("Close    Esc", self.close)]
        if name == "View":
            any_on = any(self.data.get(t) for t in widgets.TILE_ORDER)
            moved = self.order != list(widgets.TILE_ORDER)
            return [
                # Each entry names what happens ON THE DESKTOP rather than the
                # kind of thing being switched. "Show All" is gone: there are
                # more tiles than the board can draw, so it was a promise the
                # grid cannot keep — filling the board is the real outcome.
                ("Fill the Board",
                 self._fill_board if not self._full() else None),
                ("Hide All from the Desktop",
                 (lambda: self._set_all(False)) if any_on else None),
                nbapp.SEP,
                ("Restore the Original Order",
                 self._reset_order if moved else None),
            ]
        return super().menu_items(name)


if __name__ == "__main__":
    nbapp.run(WidgetSettings)
