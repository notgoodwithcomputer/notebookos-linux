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
from gi.repository import Gdk, Gtk, Pango                 # noqa: E402

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
    "accounting": "Cash balance and recent entries",
}


class WidgetSettings(nbapp.AppWindow):
    app_name = "Widget Settings"
    menus = ("File", "View")

    def __init__(self):
        super().__init__()
        self.data = self._load()
        self._switches = {}
        self._build()
        self._install_css()
        self._refresh_status()

    # -- store ---------------------------------------------------------------

    def _load(self):
        """Which tiles are on. Tolerates anything that is not the right shape:
        a hand-edited or truncated store must open this screen showing the
        defaults, not stop it opening."""
        on = dict(widgets.TILE_DEFAULT_ON)
        try:
            with open(STORE, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return on
        tiles = raw.get("tiles") if isinstance(raw, dict) else None
        if isinstance(tiles, dict):
            for tid in widgets.TILE_ORDER:
                if tid in tiles:
                    on[tid] = bool(tiles[tid])
        return on

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STORE), exist_ok=True)
            nbapp.atomic_write_json(STORE, {"tiles": self.data}, indent=1)
        except OSError:
            pass          # a read-only home must never stop the app working

    # -- ui ------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app.
        css = b"""
        .ws-main { background: #FCFBF8; }
        .ws-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .ws-title { font-size: 26px; font-weight: 700; color: #1A1916; }
        .ws-lede { font-size: 14px; color: #6E695E; }
        .ws-eyebrow { font-size: 11px; letter-spacing: 0.14em;
                      font-weight: 700; color: #9A9484; }
        .ws-rule { background: #E4DFD2; }
        .ws-row { padding: 14px 8px; }
        .ws-row-hot { background: #F4F2EC; }
        .ws-row-sep { border-top: 1px solid #EDE9DE; }
        .ws-name { font-size: 15px; color: #1A1916; }
        .ws-blurb { font-size: 13px; color: #6E695E; }
        .ws-pinned { background: #F4F2EC; border: 1px solid #E2DCCE;
                     border-radius: 3px; padding: 14px 16px; }
        .ws-pinnedname { font-size: 14px; color: #1A1916; }
        .ws-pinnedwhy { font-size: 12px; color: #6E695E; }
        .ws-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #E4DFD2; background: #F8F7F2; }
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
        # This is a FULLSCREEN app window, so the desktop it configures is not
        # on screen while the switches are being set: the one line says how to
        # get back to it.
        lede = Gtk.Label(
            label=_t("Close this window to see the desktop."),
            xalign=0)
        lede.get_style_context().add_class("ws-lede")
        self._wrap(lede)
        lede.set_margin_top(4)
        inner.pack_start(lede, False, False, 0)

        rule = Gtk.Box()
        rule.get_style_context().add_class("ws-rule")
        rule.set_size_request(-1, 1)
        rule.set_margin_top(20)
        rule.set_margin_bottom(6)
        inner.pack_start(rule, False, False, 0)

        for i, tid in enumerate(widgets.TILE_ORDER):
            inner.pack_start(self._tile_row(tid, first=(i == 0)),
                             False, False, 0)

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

    def _tile_row(self, tid, first=False):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        ctx = row.get_style_context()
        ctx.add_class("ws-row")
        if not first:
            ctx.add_class("ws-row-sep")

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

        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(self.data.get(tid)))
        sw.connect("notify::active", self._on_toggle, tid)
        sw.set_tooltip_text(_t("Show %s on the desktop")
                            % _t(widgets.TILE_TITLE[tid]))
        self._switches[tid] = sw
        row.pack_end(sw, False, False, 0)

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
        hit.set_tooltip_text(_t("Show %s on the desktop")
                             % _t(widgets.TILE_TITLE[tid]))
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
        if sw is not None:
            sw.set_active(not sw.get_active())   # notify::active does the rest
        return True

    def _refresh_status(self):
        n = sum(1 for tid in widgets.TILE_ORDER if self.data.get(tid))
        if not n:
            self.status.set_text(
                _t("No widgets on the desktop"))
        else:
            self.status.set_text(
                _t("%d widget%s on the desktop") % (n, "" if n == 1 else "s"))

    # -- actions -------------------------------------------------------------

    def _on_toggle(self, sw, _param, tid):
        self.data[tid] = bool(sw.get_active())
        self._save()
        self._refresh_status()

    def _set_all(self, on):
        for tid in widgets.TILE_ORDER:
            self.data[tid] = on
            self._switches[tid].set_active(on)
        self._save()
        self._refresh_status()

    def menu_items(self, name):
        if name == "File":
            return [("Close    Esc", self.close)]
        if name == "View":
            all_on = all(self.data.get(t) for t in widgets.TILE_ORDER)
            any_on = any(self.data.get(t) for t in widgets.TILE_ORDER)
            return [
                # Each entry names what happens on the desktop rather than
                # the kind of thing being switched — the same sentence the
                # rows' own tooltips use ("Show %s on the desktop").
                ("Show All on the Desktop",
                 (lambda: self._set_all(True)) if not all_on else None),
                ("Hide All from the Desktop",
                 (lambda: self._set_all(False)) if any_on else None),
            ]
        return super().menu_items(name)


if __name__ == "__main__":
    nbapp.run(WidgetSettings)
