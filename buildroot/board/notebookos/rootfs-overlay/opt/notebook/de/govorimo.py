#!/usr/bin/env python3
"""
Govorimo — messaging over LoRa radio. Chat, group chat, and public local
boards, with no internet and no infrastructure: the radio horizon is the
whole community.

The app is a pure client. govorimod (the vendored daemon) owns the radio,
the framing, the crypto, the mesh and the message store behind a local
socket API, and this process never sees key material — it renders state and
asks for actions (govorimolib.DaemonLink). If the daemon is restarting —
a dongle was just plugged in, or provisioning finished — the link retries
once a second and the interface says so instead of pretending.

What shapes every screen (the medium, not a style):

  * The channel carries ~20 MB a day FOR EVERYONE IN RANGE, so airtime is a
    commons. The meter in the rail is always visible, the composer prices
    each message in milliseconds of shared air, and expensive operations
    (group membership) state their cost before running.
  * Delivery is honest or it is nothing. The five states are the protocol's
    real observations — queued, sent, relayed (own frame overheard being
    rebroadcast), delivered (an actual receipt), failed — and the app never
    shows a certainty it does not have. There are no typing indicators and
    no presence polling; the protocol forbids them, and the quiet is a
    feature, not an absence.
  * Ordering is by Lamport clock; the wall-clock time on a row is the LOCAL
    receipt time, advisory only.
  * Trust is made in person. Contact bundles cross by file or by retyping —
    never over the air — and each exchange ends with a safety number both
    machines must agree on, read aloud. The recovery mnemonic is shown once,
    at identity creation, full screen, and never again.

The app's own store ($NB_HOME/.config/notebook/govorimo.json) holds only
interface state — the open surface and selections. Messages, contacts and
identity live in the daemon's store; losing this file loses nothing.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import json     # noqa: E402
import os       # noqa: E402
import threading  # noqa: E402
import time     # noqa: E402

import nbapp        # noqa: E402
import nbicons      # noqa: E402
import nbpicker     # noqa: E402
import govorimolib  # noqa: E402
from nbi18n import _t  # noqa: E402

STORE = os.path.join(os.environ.get("NB_HOME", os.path.expanduser("~")),
                     ".config", "notebook", "govorimo.json")

# Delivery states, in the order they can occur. The glyph column pins the two
# non-Latin-1 marks to DejaVu Sans when rendered (Nimbus Sans has no U+2192 /
# U+2713 and would show tofu on hardware).
_STATE_GLYPHS = {
    "queued":    ("…", "Queued — waiting for the channel or the airtime budget"),
    "sent":      ("→", "Sent — transmitted by this radio"),
    "relayed":   ("»", "Relayed — this frame was overheard being passed on"),
    "delivered": ("✓", "Delivered — the other device acknowledged it"),
    "failed":    ("×", "Failed — retries exhausted, nobody acknowledged"),
}
_DEJAVU = ("→", "✓")

_SURFACES = ("chats", "boards", "people", "radio")
_SURFACE_LABELS = {"chats": "Chats", "boards": "Boards",
                   "people": "People", "radio": "Radio"}

_ROLE_WORDS = {"leaf": "Leaf", "relay": "Relay", "station": "Station"}


def _fmt_when(unix):
    """Local receipt time, advisory by contract. Day + clock once it is not
    today; clock alone while it is."""
    if not unix:
        return ""
    lt = time.localtime(unix)
    now = time.localtime()
    if lt[:3] == now[:3]:
        return time.strftime("%H:%M", lt)
    return time.strftime("%d %b %H:%M", lt)


def _fmt_ago(secs_ago):
    """Takes SECONDS AGO (the daemon reports neighbour.last_heard that way),
    not a timestamp. None means never heard on this run of the radio."""
    if secs_ago is None:
        return _t("not heard")
    d = max(0, int(secs_ago))
    if d < 90:
        return _t("heard just now")
    if d < 3600:
        return _t("heard %d min ago") % (d // 60)
    if d < 86400:
        return _t("heard %d h ago") % (d // 3600)
    return _t("heard %d d ago") % (d // 86400)


class GovorimoWindow(nbapp.AppWindow):
    app_name = "Govorimo"
    menus = ("File", "Edit", "View", "Radio")

    def __init__(self):
        super().__init__()
        self._install_css()
        self._alive = True
        self.connect("destroy", self._on_destroy)

        # Interface state (the only thing this app persists).
        self._prefs = self._load_prefs()
        self._surface = self._prefs.get("surface", "chats")
        if self._surface not in _SURFACES:
            self._surface = "chats"

        # Mirrors of daemon state.
        self._me_node = ""
        self._me_name = ""
        self._provisioned = False
        self._convs = []
        self._boards = []
        self._contacts = []
        self._neighbours = []
        self._status = {}
        self._messages = {}          # conv tag -> [entry]
        self._posts = {}             # board_id -> [entry]
        self._board_unread = {}
        self._sel_conv = self._prefs.get("conv") or None
        self._sel_board = self._prefs.get("board") or None
        self._reply_to = None        # (msgid, excerpt) when composing a reply
        self._conv_gen = 0           # guards async transcript loads
        self._board_gen = 0
        self._probe_gen = 0
        self._wizard = None
        self._card = None            # the one open overlay card, if any
        self._status_src = None

        # The link object exists before any surface renders (the Radio surface
        # reads link.state), but it is STARTED only once the window is built,
        # so every callback lands on live widgets.
        self.link = govorimolib.DaemonLink("govorimo")

        self._build()
        self._show_surface(self._surface, save=False)
        # Honest before the daemon answers: empty transcripts, dead composers.
        self._render_chat()
        self._render_board()
        self._render_foot()

        self.link.on_state(self._link_state)
        self.link.on_event("*", self._on_daemon_event)
        self.link.start()
        self._status_src = GLib.timeout_add_seconds(5, self._poll_status)
        # nbapp.run() shows the window; showing it here would map it during
        # offscreen harness runs and lay everything out at the host monitor's
        # size. The no-show-all bars stay hidden through run()'s show_all.

    # ------------------------------------------------------------ lifecycle

    def _on_destroy(self, *_):
        self._alive = False
        if self._status_src is not None:
            GLib.source_remove(self._status_src)
            self._status_src = None
        self.link.stop()
        self._save_prefs()

    def _load_prefs(self):
        try:
            with open(STORE, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                nbapp.quarantine_unrecognized(STORE)
                return {}
            return obj
        except FileNotFoundError:
            return {}
        except ValueError:
            # atomic_write_json's preserve_damaged covers the write path; a
            # store that stopped parsing is preserved before we ever rewrite.
            nbapp.preserve_damaged(STORE)
            return {}
        except OSError:
            return {}

    def _save_prefs(self):
        try:
            nbapp.atomic_write_json(STORE, {
                "surface": self._surface,
                "conv": self._sel_conv or "",
                "board": self._sel_board or "",
            })
        except OSError:
            pass  # interface state only; nothing of the user's is at risk

    # ------------------------------------------------------------ the frame

    def _build(self):
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.content.pack_start(body, True, True, 0)

        # -- rail
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rail.set_size_request(nbapp.RAIL, -1)
        rail.get_style_context().add_class("gvrail")
        body.pack_start(rail, False, False, 0)

        self._rail_rows = {}
        for sid in _SURFACES:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("gvsurf")
            row = Gtk.Box(spacing=8)
            lab = Gtk.Label(label=_t(_SURFACE_LABELS[sid]), xalign=0)
            lab.get_style_context().add_class("gvsurflabel")
            row.pack_start(lab, True, True, 0)
            badge = Gtk.Label(label="")
            badge.get_style_context().add_class("gvbadge")
            badge.set_no_show_all(True)
            row.pack_end(badge, False, False, 0)
            b.add(row)
            b.connect("clicked", lambda _b, s=sid: self._show_surface(s))
            nbapp.name_control(b, _t(_SURFACE_LABELS[sid]))
            rail.pack_start(b, False, False, 0)
            self._rail_rows[sid] = (b, badge)

        sep = Gtk.Box()
        sep.get_style_context().add_class("gvhair")
        rail.pack_start(sep, False, False, 6)

        # Context list for the active surface (conversations / boards).
        self._rail_stack = Gtk.Stack()
        self._rail_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        rail.pack_start(self._rail_stack, True, True, 0)

        self._conv_list = self._rail_list("chats")
        self._board_list = self._rail_list("boards")
        blank = Gtk.Box()
        self._rail_stack.add_named(blank, "blank")

        # Pinned bottom block: the radio, always in view.
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        foot.get_style_context().add_class("gvfoot")
        self._radio_line = Gtk.Label(xalign=0)
        self._radio_line.get_style_context().add_class("gvfootline")
        self._radio_line.set_ellipsize(Pango.EllipsizeMode.END)
        foot.pack_start(self._radio_line, False, False, 0)
        self._meter = Gtk.DrawingArea()
        self._meter.set_size_request(-1, 8)
        self._meter.connect("draw", self._draw_meter)
        foot.pack_start(self._meter, False, False, 0)
        self._meter_line = Gtk.Label(xalign=0)
        self._meter_line.get_style_context().add_class("gvfootsub")
        foot.pack_start(self._meter_line, False, False, 0)
        rail.pack_end(foot, False, False, 0)

        vsep = Gtk.Box()
        vsep.get_style_context().add_class("gvedge")
        body.pack_start(vsep, False, False, 0)

        # -- main column
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.set_hexpand(True)
        body.pack_start(self._stack, True, True, 0)

        self._stack.add_named(self._build_chats(), "chats")
        self._stack.add_named(self._build_boards(), "boards")
        self._stack.add_named(self._build_people(), "people")
        self._stack.add_named(self._build_radio(), "radio")

        self._link_banner = Gtk.Label()
        self._link_banner.get_style_context().add_class("gvbanner")
        self._link_banner.set_no_show_all(True)
        self.content.pack_start(self._link_banner, False, False, 0)
        self.content.reorder_child(self._link_banner, 0)

    def _rail_list(self, name):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sw.add(box)
        self._rail_stack.add_named(sw, name)
        return box

    # ------------------------------------------------------- chats surface

    def _build_chats(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        head = Gtk.Box(spacing=10)
        head.get_style_context().add_class("gvhead")
        self._chat_title = Gtk.Label(xalign=0)
        self._chat_title.get_style_context().add_class("gvtitle")
        self._chat_title.set_ellipsize(Pango.EllipsizeMode.END)
        head.pack_start(self._chat_title, True, True, 0)
        self._members_btn = Gtk.Button(label=_t("Members…"))
        self._members_btn.get_style_context().add_class("gvquiet")
        self._members_btn.connect("clicked", lambda *_: self._open_members())
        self._members_btn.set_no_show_all(True)
        head.pack_end(self._members_btn, False, False, 0)
        col.pack_start(head, False, False, 0)

        self._chat_sub = Gtk.Label(xalign=0)
        self._chat_sub.get_style_context().add_class("gvsub")
        self._chat_sub.set_margin_start(24)
        self._chat_sub.set_margin_bottom(4)
        col.pack_start(self._chat_sub, False, False, 0)

        self._chat_scroll = Gtk.ScrolledWindow()
        self._chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._chat_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._chat_rows.set_margin_start(24)
        self._chat_rows.set_margin_end(24)
        self._chat_rows.set_margin_top(6)
        self._chat_rows.set_margin_bottom(10)
        self._chat_scroll.add(self._chat_rows)
        col.pack_start(self._chat_scroll, True, True, 0)
        # Stick to the bottom only while the reader is already there — a
        # scrolled-back reader is never yanked down by an arriving message.
        adj = self._chat_scroll.get_vadjustment()
        adj.connect("value-changed", self._chat_scrolled)
        self._chat_stick = True
        self._chat_autoscroll = False

        self._chat_empty = Gtk.Label()
        self._chat_empty.get_style_context().add_class("gvempty")
        self._chat_empty.set_line_wrap(True)
        self._chat_empty.set_justify(Gtk.Justification.CENTER)
        self._chat_rows.pack_start(self._chat_empty, True, True, 40)

        # Reply context, shown only while composing a reply.
        self._reply_bar = Gtk.Box(spacing=8)
        self._reply_bar.get_style_context().add_class("gvreply")
        self._reply_label = Gtk.Label(xalign=0)
        self._reply_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._reply_bar.pack_start(self._reply_label, True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel reply"))
        cancel.get_style_context().add_class("gvquiet")
        cancel.connect("clicked", lambda *_: self._set_reply(None))
        self._reply_bar.pack_end(cancel, False, False, 0)
        self._reply_bar.set_no_show_all(True)
        col.pack_start(self._reply_bar, False, False, 0)

        compose = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        compose.get_style_context().add_class("gvcompose")
        row = Gtk.Box(spacing=8)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text(_t("Write a message"))
        self._entry.connect("activate", lambda *_: self._send_current())
        self._entry.connect("changed", lambda *_: self._update_price())
        row.pack_start(self._entry, True, True, 0)
        self._send_btn = Gtk.Button(label=_t("Send"))
        self._send_btn.get_style_context().add_class("gvprimary")
        self._send_btn.connect("clicked", lambda *_: self._send_current())
        row.pack_end(self._send_btn, False, False, 0)
        compose.pack_start(row, False, False, 0)
        self._price = Gtk.Label(xalign=0)
        self._price.get_style_context().add_class("gvprice")
        compose.pack_start(self._price, False, False, 0)
        col.pack_start(compose, False, False, 0)
        return col

    def _chat_scrolled(self, adj):
        if self._chat_autoscroll:
            return
        at_bottom = adj.get_value() >= (adj.get_upper()
                                        - adj.get_page_size() - 24)
        self._chat_stick = at_bottom

    def _scroll_chat_to_end(self):
        def land():
            adj = self._chat_scroll.get_vadjustment()
            self._chat_autoscroll = True
            adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
            self._chat_autoscroll = False
            return False
        GLib.idle_add(land)

    def _update_price(self):
        text = self._entry.get_text()
        n = len(text.encode("utf-8"))
        if n == 0:
            self._price.set_text("")
            self._price.get_style_context().remove_class("gvover")
            return
        ms = govorimolib.airtime_ms(min(n, govorimolib.MAX_TEXT_BYTES))
        ctx = self._price.get_style_context()
        if n > govorimolib.MAX_TEXT_BYTES:
            ctx.add_class("gvover")
            self._price.set_text(
                _t("%d bytes — over one frame; it is sent only if it "
                   "compresses to fit") % n)
        else:
            ctx.remove_class("gvover")
            self._price.set_text(
                _t("%d of %d bytes · ≈ %d ms of shared airtime")
                % (n, govorimolib.MAX_TEXT_BYTES, int(ms)))

    def _set_reply(self, entry):
        if entry is None:
            self._reply_to = None
            self._reply_bar.hide()
            return
        excerpt = (entry.get("text") or "")[:60]
        self._reply_to = entry.get("msgid")
        self._reply_label.set_text(_t("Replying to: %s") % excerpt)
        self._reply_bar.show()
        self._reply_label.show()
        self._entry.grab_focus()

    def _send_current(self):
        tag = self._sel_conv
        text = self._entry.get_text().strip()
        if not tag or not text:
            return
        params = {"conv": tag, "text": text}
        method = "send_text"
        if self._reply_to is not None:
            method = "send_reply"
            params["target_msgid"] = self._reply_to
        self._entry.set_text("")
        reply_target = self._reply_to
        self._set_reply(None)

        def done(res, err):
            if not self._alive:
                return
            if err is not None:
                # The text is the user's work; a failed send returns it.
                self._entry.set_text(text)
                self._entry.set_position(-1)
                self._update_price()
                self._say_error(err)
                return
            entry = {"t": "msg", "out": True, "from": self._me_node,
                     "msgid": res.get("msgid"), "kind": "text", "text": text,
                     "state": "queued", "at": int(time.time())}
            if reply_target is not None:
                entry["kind"] = "reply"
                entry["target"] = reply_target
            self._messages.setdefault(tag, []).append(entry)
            if self._sel_conv == tag:
                self._append_chat_row(entry)
                self._scroll_chat_to_end()
        self.link.call(method, params, done)

    def _say_error(self, err):
        code = err.get("code", "")
        msg = err.get("message", "")
        if code == "budget_exceeded":
            retry = err.get("retry_after_s")
            text = _t("This hour's share of the shared channel is spent.")
            if retry:
                text += " " + (_t("Sending resumes in about %d s.") % int(retry))
        elif code == "gone":
            text = _t("The radio service is not answering. It restarts by "
                      "itself; the message was not sent.")
        elif code == "too_large":
            text = _t("The message does not fit one radio frame and did not "
                      "compress enough. Shorten it.")
        elif code == "radio_unavailable":
            text = _t("No working radio. Plug the dongle in; sending resumes "
                      "by itself.")
        else:
            text = msg or code or _t("The action did not happen.")
        self._flash(text)

    def _flash(self, text):
        self._price.get_style_context().add_class("gvover")
        self._price.set_text(text)
        gen = getattr(self, "_flash_gen", 0) + 1
        self._flash_gen = gen

        def revert():
            if self._alive and self._flash_gen == gen:
                self._update_price()
            return False
        GLib.timeout_add_seconds(6, revert)

    # transcript rendering

    def _load_conv(self, tag):
        self._conv_gen += 1
        gen = self._conv_gen

        def done(res, err):
            if not self._alive or gen != self._conv_gen:
                return
            if err is not None:
                return
            self._messages[tag] = res if isinstance(res, list) else []
            for c in self._convs:
                if c.get("conv") == tag:
                    c["unread"] = 0
            self._render_chat()
            self._render_rail()
            self._scroll_chat_to_end()
        self.link.call("get_messages", {"conv": tag, "limit": 200}, done)

    def _render_chat(self):
        for ch in list(self._chat_rows.get_children()):
            self._chat_rows.remove(ch)
        conv = self._conv_by_tag(self._sel_conv)
        if conv is None:
            self._chat_title.set_text(_t("Chats"))
            self._chat_sub.set_text("")
            self._members_btn.hide()
            empty = Gtk.Label()
            empty.get_style_context().add_class("gvempty")
            empty.set_line_wrap(True)
            empty.set_justify(Gtk.Justification.CENTER)
            if self._contacts:
                empty.set_text(_t("No conversation is open. Choose one in "
                                  "the rail, or add a contact in People."))
            else:
                empty.set_text(_t("A conversation starts from an exchanged "
                                  "contact bundle. Add the first contact in "
                                  "People."))
            self._chat_rows.pack_start(empty, True, True, 40)
            self._chat_rows.show_all()
            self._entry.set_sensitive(False)
            self._send_btn.set_sensitive(False)
            return
        self._entry.set_sensitive(True)
        self._send_btn.set_sensitive(True)
        self._chat_title.set_text(conv.get("name") or conv.get("conv", ""))
        if conv.get("kind") == "group":
            n = len(conv.get("members", [])) + 1
            self._chat_sub.set_text(
                _t("Group of %d · delivery marks stop at sent; groups "
                   "carry no per-person receipts") % n)
            self._members_btn.show()
        else:
            self._chat_sub.set_text(_t("Direct · end-to-end encrypted"))
            self._members_btn.hide()
        entries = self._messages.get(self._sel_conv, [])
        self._chat_prev = None
        if not entries:
            empty = Gtk.Label()
            empty.get_style_context().add_class("gvempty")
            empty.set_line_wrap(True)
            empty.set_justify(Gtk.Justification.CENTER)
            empty.set_text(_t("No messages in this conversation. The first "
                              "one breaks the radio silence."))
            empty._gv_emptystate = True
            self._chat_rows.pack_start(empty, True, True, 40)
        else:
            for e in entries:
                if e.get("t") not in (None, "msg") and e.get("kind") != "system":
                    continue
                self._append_chat_row(e)
        self._chat_rows.show_all()

    def _clear_empty(self, container):
        for ch in list(container.get_children()):
            if getattr(ch, "_gv_emptystate", False):
                container.remove(ch)

    def _append_chat_row(self, e):
        self._clear_empty(self._chat_rows)
        # The ledger stays quiet: the author cell and the time cell print
        # only when they CHANGE (a new speaker, or five minutes of silence).
        prev = getattr(self, "_chat_prev", None)
        kind = e.get("kind", "text")
        if kind == "system":
            self._chat_prev = None
            row = Gtk.Label(label=e.get("text", ""), xalign=0.5)
            row.get_style_context().add_class("gvsystem")
            row.set_line_wrap(True)
            self._chat_rows.pack_start(row, False, False, 4)
            row.show_all()
            return
        if kind == "reaction":
            self._chat_prev = None
            who = self._name_of(e.get("from", ""))
            row = Gtk.Label(xalign=0.5)
            row.get_style_context().add_class("gvsystem")
            row.set_text(_t("%s reacted %s to #%s")
                         % (who, e.get("emoji", ""), e.get("target", "")))
            self._chat_rows.pack_start(row, False, False, 2)
            row.show_all()
            return

        author = e.get("from", "")
        new_author = prev is None or prev[0] != author
        new_time = (prev is None or e.get("at") is None
                    or (e.get("at", 0) - prev[1]) >= 300)
        self._chat_prev = (author, e.get("at") or (prev[1] if prev else 0))

        row = Gtk.Box(spacing=10)
        row.get_style_context().add_class("gvmsg")
        alab = Gtk.Label(xalign=1.0, yalign=0.0)
        alab.get_style_context().add_class("gvauthor")
        alab.set_size_request(96, -1)
        alab.set_ellipsize(Pango.EllipsizeMode.END)
        if new_author:
            alab.set_text(self._name_of(author))
        row.pack_start(alab, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        if kind == "reply" and e.get("target") is not None:
            ref = Gtk.Label(xalign=0)
            ref.get_style_context().add_class("gvref")
            ref.set_text("↩ #%s" % e.get("target"))
            mid.pack_start(ref, False, False, 0)
        text = Gtk.Label(label=e.get("text") or "", xalign=0)
        text.get_style_context().add_class("gvtext")
        text.set_line_wrap(True)
        text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.set_selectable(True)
        mid.pack_start(text, False, False, 0)
        row.pack_start(mid, True, True, 0)

        meta = Gtk.Box(spacing=6)
        when = Gtk.Label(xalign=1.0)
        when.get_style_context().add_class("gvwhen")
        if new_author or new_time:
            when.set_text(_fmt_when(e.get("at")))
        meta.pack_start(when, False, False, 0)
        if e.get("out"):
            glyph = Gtk.Label(xalign=1.0)
            glyph.get_style_context().add_class("gvstate")
            self._set_state_glyph(glyph, e.get("state", "queued"))
            glyph._gv_msgid = e.get("msgid")
            meta.pack_end(glyph, False, False, 0)
        row.pack_end(meta, False, False, 0)

        ebox = Gtk.EventBox()
        ebox.add(row)
        ebox.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        ebox.connect("button-press-event",
                     lambda _w, ev, entry=e: self._msg_pressed(entry, ev))
        self._chat_rows.pack_start(ebox, False, False, 0)
        ebox.show_all()

    def _msg_pressed(self, entry, ev):
        if ev.type == Gdk.EventType._2BUTTON_PRESS and entry.get("msgid"):
            self._set_reply(entry)
        return False

    def _set_state_glyph(self, label, state):
        glyph, tip = _STATE_GLYPHS.get(state, ("…", ""))
        if glyph in _DEJAVU:
            label.set_markup('<span face="DejaVu Sans">%s</span>'
                             % GLib.markup_escape_text(glyph))
        else:
            label.set_text(glyph)
        ctx = label.get_style_context()
        for cls in ("gvfailed", "gvdelivered"):
            ctx.remove_class(cls)
        if state == "failed":
            ctx.add_class("gvfailed")
        elif state == "delivered":
            ctx.add_class("gvdelivered")
        label.set_tooltip_text(_t(tip))

    def _apply_state(self, tag, msgid, state):
        for e in self._messages.get(tag, []):
            if e.get("out") and e.get("msgid") == msgid:
                e["state"] = state
        if tag != self._sel_conv:
            return
        for ebox in self._chat_rows.get_children():
            row = ebox.get_child() if isinstance(ebox, Gtk.EventBox) else None
            if row is None:
                continue
            for side in row.get_children():
                for w in (side.get_children()
                          if isinstance(side, Gtk.Box) else []):
                    if getattr(w, "_gv_msgid", None) == msgid:
                        self._set_state_glyph(w, state)

    # ------------------------------------------------------ boards surface

    def _build_boards(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        head = Gtk.Box(spacing=10)
        head.get_style_context().add_class("gvhead")
        self._board_title = Gtk.Label(xalign=0)
        self._board_title.get_style_context().add_class("gvtitle")
        self._board_title.set_ellipsize(Pango.EllipsizeMode.END)
        head.pack_start(self._board_title, True, True, 0)
        unfollow = Gtk.Button(label=_t("Unfollow"))
        unfollow.get_style_context().add_class("gvquiet")
        unfollow.connect("clicked", lambda *_: self._unfollow_current())
        self._unfollow_btn = unfollow
        self._unfollow_btn.set_no_show_all(True)
        head.pack_end(unfollow, False, False, 0)
        col.pack_start(head, False, False, 0)

        self._board_sub = Gtk.Label(xalign=0)
        self._board_sub.get_style_context().add_class("gvsub")
        self._board_sub.set_margin_start(24)
        self._board_sub.set_margin_bottom(4)
        col.pack_start(self._board_sub, False, False, 0)

        self._board_scroll = Gtk.ScrolledWindow()
        self._board_scroll.set_policy(Gtk.PolicyType.NEVER,
                                      Gtk.PolicyType.AUTOMATIC)
        self._board_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._board_rows.set_margin_start(24)
        self._board_rows.set_margin_end(24)
        self._board_rows.set_margin_top(6)
        self._board_rows.set_margin_bottom(10)
        self._board_scroll.add(self._board_rows)
        col.pack_start(self._board_scroll, True, True, 0)

        self._post_reply_bar = Gtk.Box(spacing=8)
        self._post_reply_bar.get_style_context().add_class("gvreply")
        self._post_reply_label = Gtk.Label(xalign=0)
        self._post_reply_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._post_reply_bar.pack_start(self._post_reply_label, True, True, 0)
        pc = Gtk.Button(label=_t("Cancel reply"))
        pc.get_style_context().add_class("gvquiet")
        pc.connect("clicked", lambda *_: self._set_post_reply(None))
        self._post_reply_bar.pack_end(pc, False, False, 0)
        self._post_reply_bar.set_no_show_all(True)
        col.pack_start(self._post_reply_bar, False, False, 0)
        self._post_reply_to = None

        compose = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        compose.get_style_context().add_class("gvcompose")
        row = Gtk.Box(spacing=8)
        self._post_entry = Gtk.Entry()
        self._post_entry.set_placeholder_text(_t("Write a post"))
        self._post_entry.connect("activate", lambda *_: self._post_current())
        self._post_entry.connect("changed", lambda *_: self._update_post_note())
        row.pack_start(self._post_entry, True, True, 0)
        self._post_btn = Gtk.Button(label=_t("Post"))
        self._post_btn.get_style_context().add_class("gvprimary")
        self._post_btn.connect("clicked", lambda *_: self._post_current())
        row.pack_end(self._post_btn, False, False, 0)
        compose.pack_start(row, False, False, 0)
        self._post_note = Gtk.Label(xalign=0)
        self._post_note.get_style_context().add_class("gvprice")
        compose.pack_start(self._post_note, False, False, 0)
        col.pack_start(compose, False, False, 0)
        self._update_post_note()
        return col

    def _update_post_note(self):
        n = len(self._post_entry.get_text().encode("utf-8"))
        ctx = self._post_note.get_style_context()
        if n == 0:
            ctx.remove_class("gvover")
            self._post_note.set_text(_t("Posts are public and signed."))
        elif n > 133:
            ctx.add_class("gvover")
            self._post_note.set_text(
                _t("%d bytes — over the 133-byte post limit") % n)
        else:
            ctx.remove_class("gvover")
            self._post_note.set_text(_t("%d of 133 bytes") % n)

    def _set_post_reply(self, post):
        if post is None:
            self._post_reply_to = None
            self._post_reply_bar.hide()
            return
        self._post_reply_to = post.get("msgid")
        self._post_reply_label.set_text(
            _t("Replying to: %s") % (post.get("text") or "")[:60])
        self._post_reply_bar.show()
        self._post_reply_label.show()
        self._post_entry.grab_focus()

    def _load_board(self, bid):
        self._board_gen += 1
        gen = self._board_gen

        def done(res, err):
            if not self._alive or gen != self._board_gen:
                return
            if err is not None:
                return
            self._posts[bid] = res if isinstance(res, list) else []
            self._board_unread.pop(bid, None)
            self._render_board()
            self._render_rail()
        self.link.call("get_posts", {"board_id": bid, "limit": 200}, done)

    def _render_board(self):
        for ch in list(self._board_rows.get_children()):
            self._board_rows.remove(ch)
        board = self._board_by_id(self._sel_board)
        if board is None:
            self._board_title.set_text(_t("Boards"))
            self._board_sub.set_text("")
            self._unfollow_btn.hide()
            empty = Gtk.Label()
            empty.get_style_context().add_class("gvempty")
            empty.set_line_wrap(True)
            empty.set_justify(Gtk.Justification.CENTER)
            empty.set_text(_t("No board is followed. Follow one by name — "
                              "local.general is the customary town square; "
                              "local.forsale and emergency.alerts are common."))
            self._board_rows.pack_start(empty, True, True, 40)
            self._board_rows.show_all()
            self._post_entry.set_sensitive(False)
            self._post_btn.set_sensitive(False)
            return
        self._post_entry.set_sensitive(True)
        self._post_btn.set_sensitive(True)
        self._board_title.set_text(board.get("name", ""))
        self._board_sub.set_text(
            _t("Public · the radio horizon is the boundary · "
               "%d posts held") % int(board.get("post_count", 0)))
        self._unfollow_btn.show()
        posts = self._posts.get(self._sel_board, [])
        if not posts:
            empty = Gtk.Label()
            empty.get_style_context().add_class("gvempty")
            empty.set_line_wrap(True)
            empty.set_justify(Gtk.Justification.CENTER)
            empty.set_text(_t("No posts held for this board. Posts arrive "
                              "as neighbours write them; the first post "
                              "breaks the silence."))
            empty._gv_emptystate = True
            self._board_rows.pack_start(empty, True, True, 40)
        else:
            for p in posts:
                self._append_post_row(p)
        self._board_rows.show_all()

    def _append_post_row(self, p):
        self._clear_empty(self._board_rows)
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.get_style_context().add_class("gvpost")
        top = Gtk.Box(spacing=8)
        who = Gtk.Label(xalign=0)
        who.get_style_context().add_class("gvauthor")
        who.set_text(p.get("author") or p.get("author_id", ""))
        top.pack_start(who, False, False, 0)
        if not p.get("verified"):
            chip = Gtk.Label(label=_t("unverified author"))
            chip.get_style_context().add_class("gvchip")
            chip.set_tooltip_text(
                _t("No pseudonym announcement has arrived for this pen "
                   "name; the signature is not yet checkable"))
            top.pack_start(chip, False, False, 0)
        idl = Gtk.Label(label="#%s" % p.get("msgid", ""), xalign=0)
        idl.get_style_context().add_class("gvref")
        top.pack_start(idl, False, False, 0)
        when = Gtk.Label(label=_fmt_when(p.get("at")), xalign=1.0)
        when.get_style_context().add_class("gvwhen")
        top.pack_end(when, False, False, 0)
        row.pack_start(top, False, False, 0)
        if p.get("parent"):
            ref = Gtk.Label(xalign=0)
            ref.get_style_context().add_class("gvref")
            ref.set_text("↩ #%s" % p.get("parent"))
            row.pack_start(ref, False, False, 0)
        text = Gtk.Label(label=p.get("text") or "", xalign=0)
        text.get_style_context().add_class("gvtext")
        text.set_line_wrap(True)
        text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.set_selectable(True)
        row.pack_start(text, False, False, 0)

        ebox = Gtk.EventBox()
        ebox.add(row)
        ebox.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        ebox.connect("button-press-event",
                     lambda _w, ev, post=p: self._post_pressed(post, ev))
        self._board_rows.pack_start(ebox, False, False, 0)
        ebox.show_all()

    def _post_pressed(self, post, ev):
        if ev.type == Gdk.EventType._2BUTTON_PRESS and post.get("msgid"):
            self._set_post_reply(post)
        return False

    def _post_current(self):
        bid = self._sel_board
        text = self._post_entry.get_text().strip()
        if not bid or not text:
            return
        params = {"board_id": bid, "text": text}
        if self._post_reply_to is not None:
            params["parent_msgid"] = self._post_reply_to
        self._post_entry.set_text("")
        self._set_post_reply(None)

        def done(res, err):
            if not self._alive:
                return
            if err is not None:
                self._post_entry.set_text(text)
                self._post_entry.set_position(-1)
                self._say_post_error(err)
                return
            entry = {"t": "post", "msgid": res.get("msgid"),
                     "author": self._me_name, "author_id": "",
                     "text": text, "verified": True, "own": True,
                     "at": int(time.time()),
                     "parent": params.get("parent_msgid", 0)}
            self._posts.setdefault(bid, []).append(entry)
            for b in self._boards:
                if b.get("board_id") == bid:
                    b["post_count"] = int(b.get("post_count", 0)) + 1
            if self._sel_board == bid:
                self._render_board()
        self.link.call("post", params, done)

    def _say_post_error(self, err):
        code = err.get("code", "")
        if code == "budget_exceeded":
            text = _t("This hour's board share of the channel is spent. "
                      "Try again in a minute.")
        elif code == "too_large":
            text = _t("A board post carries at most 133 bytes. Shorten it.")
        elif code == "gone":
            text = _t("The radio service is not answering. It restarts by "
                      "itself; the post was not sent.")
        else:
            text = err.get("message") or code
        ctx = self._post_note.get_style_context()
        ctx.add_class("gvover")
        self._post_note.set_text(text)

    def _unfollow_current(self):
        bid = self._sel_board
        if not bid:
            return

        def done(_res, err):
            if not self._alive or err is not None:
                return
            self._boards = [b for b in self._boards
                            if b.get("board_id") != bid]
            self._sel_board = (self._boards[0]["board_id"]
                               if self._boards else None)
            if self._sel_board:
                self._load_board(self._sel_board)
            self._render_board()
            self._render_rail()
        self.link.call("unfollow_board", {"board_id": bid}, done)

    # ------------------------------------------------------ people surface

    def _build_people(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=10)
        head.get_style_context().add_class("gvhead")
        t = Gtk.Label(label=_t("People"), xalign=0)
        t.get_style_context().add_class("gvtitle")
        head.pack_start(t, True, True, 0)
        add = Gtk.Button(label=_t("Add Contact…"))
        add.get_style_context().add_class("gvprimary")
        add.connect("clicked", lambda *_: self._open_exchange())
        head.pack_end(add, False, False, 0)
        grp = Gtk.Button(label=_t("New Group…"))
        grp.get_style_context().add_class("gvquiet")
        grp.connect("clicked", lambda *_: self._open_group())
        head.pack_end(grp, False, False, 0)
        col.pack_start(head, False, False, 0)

        body = Gtk.Box(spacing=0)
        body.set_homogeneous(True)
        col.pack_start(body, True, True, 0)

        # Contacts — people whose keys crossed by hand.
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_margin_start(24)
        left.set_margin_end(12)
        left.set_margin_top(6)
        ch = Gtk.Label(label=_t("CONTACTS"), xalign=0)
        ch.get_style_context().add_class("gveyebrow")
        left.pack_start(ch, False, False, 4)
        csub = Gtk.Label(xalign=0)
        csub.get_style_context().add_class("gvsub")
        csub.set_text(_t("Keys exchanged in person. A contact is the only "
                         "way a conversation starts."))
        csub.set_line_wrap(True)
        csub.set_width_chars(20)
        csub.set_max_width_chars(48)
        left.pack_start(csub, False, False, 2)
        csw = Gtk.ScrolledWindow()
        csw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._contact_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        csw.add(self._contact_rows)
        left.pack_start(csw, True, True, 4)
        body.pack_start(left, True, True, 0)

        edge = Gtk.Box()
        edge.get_style_context().add_class("gvedge")
        body.pack_start(edge, False, False, 0)

        # The radio horizon — whoever the antenna can currently hear.
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.set_margin_start(12)
        right.set_margin_end(24)
        right.set_margin_top(6)
        nh = Gtk.Label(label=_t("IN RADIO RANGE"), xalign=0)
        nh.get_style_context().add_class("gveyebrow")
        right.pack_start(nh, False, False, 4)
        nsub = Gtk.Label(xalign=0)
        nsub.get_style_context().add_class("gvsub")
        nsub.set_text(_t("Whoever the antenna hears. Stations announce "
                         "themselves every 15 to 30 minutes."))
        nsub.set_line_wrap(True)
        nsub.set_width_chars(20)
        nsub.set_max_width_chars(48)
        right.pack_start(nsub, False, False, 2)
        nsw = Gtk.ScrolledWindow()
        nsw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._neigh_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nsw.add(self._neigh_rows)
        right.pack_start(nsw, True, True, 4)
        body.pack_start(right, True, True, 0)
        return col

    def _render_people(self):
        for ch in list(self._contact_rows.get_children()):
            self._contact_rows.remove(ch)
        if not self._contacts:
            empty = Gtk.Label()
            empty.get_style_context().add_class("gvempty")
            empty.set_line_wrap(True)
            empty.set_width_chars(20)
            empty.set_max_width_chars(44)
            empty.set_text(_t("No contacts. Add Contact opens the exchange: "
                              "two machines, one bundle each, in person."))
            self._contact_rows.pack_start(empty, False, False, 20)
        for c in self._contacts:
            self._contact_rows.pack_start(self._contact_row(c), False, False, 0)
        self._contact_rows.show_all()

        for ch in list(self._neigh_rows.get_children()):
            self._neigh_rows.remove(ch)
        if not self._neighbours:
            empty = Gtk.Label()
            empty.get_style_context().add_class("gvempty")
            empty.set_line_wrap(True)
            empty.set_width_chars(20)
            empty.set_max_width_chars(44)
            empty.set_text(_t("Nothing heard on the radio. Silence is "
                              "normal between announcements; two towns out "
                              "of range have different neighbours."))
            self._neigh_rows.pack_start(empty, False, False, 20)
        for n in self._neighbours:
            self._neigh_rows.pack_start(self._neighbour_row(n), False, False, 0)
        self._neigh_rows.show_all()

    def _contact_row(self, c):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.get_style_context().add_class("gvpost")
        top = Gtk.Box(spacing=8)
        name = Gtk.Label(label=c.get("display_name") or c.get("node_id", ""),
                         xalign=0)
        name.get_style_context().add_class("gvname")
        top.pack_start(name, True, True, 0)
        msg = Gtk.Button(label=_t("Message"))
        msg.get_style_context().add_class("gvquiet")
        msg.connect("clicked",
                    lambda _b, nid=c.get("node_id", ""): self._message_contact(nid))
        top.pack_end(msg, False, False, 0)
        row.pack_start(top, False, False, 0)
        # The contact's own last_heard is daemon-uptime-relative and useless
        # for display; the neighbours list carries honest seconds-ago.
        ago = None
        for n in self._neighbours:
            if n.get("node_id") == c.get("node_id"):
                ago = n.get("last_heard")
        sn = Gtk.Label(xalign=0)
        sn.get_style_context().add_class("gvmono")
        sn.set_line_wrap(True)
        sn.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        sn.set_width_chars(20)
        sn.set_max_width_chars(44)
        sn.set_text(_t("safety number %s · %s")
                    % (c.get("safety_number") or "—", _fmt_ago(ago)))
        row.pack_start(sn, False, False, 0)
        return row

    def _neighbour_row(self, n):
        row = Gtk.Box(spacing=10)
        row.get_style_context().add_class("gvpost")
        bars = Gtk.DrawingArea()
        bars.set_size_request(26, 16)
        rssi = n.get("rssi")
        bars.connect("draw", self._draw_bars, rssi)
        if rssi is not None:
            bars.set_tooltip_text("%s dBm" % rssi)
        row.pack_start(bars, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Beacon name first; else the contact book may know this node.
        shown = n.get("name") or self._name_of(n.get("node_id", ""))
        name = Gtk.Label(label=shown, xalign=0)
        name.get_style_context().add_class("gvname")
        col.pack_start(name, False, False, 0)
        sub = Gtk.Label(xalign=0)
        sub.get_style_context().add_class("gvsub")
        role = _ROLE_WORDS.get(n.get("role", "leaf"), n.get("role", ""))
        sub.set_text("%s · %s" % (_t(role), _fmt_ago(n.get("last_heard"))))
        col.pack_start(sub, False, False, 0)
        row.pack_start(col, True, True, 0)
        return row

    def _draw_bars(self, _w, cr, rssi):
        strength = 0
        if rssi is not None:
            strength = (4 if rssi >= -80 else 3 if rssi >= -95
                        else 2 if rssi >= -110 else 1)
        for i in range(4):
            h = 4 + i * 4
            if i < strength:
                cr.set_source_rgb(*nbicons._hex("#1A1916"))
            else:
                cr.set_source_rgb(*nbicons._hex("#D7D2C5"))
            cr.rectangle(i * 6, 16 - h, 4, h)
            cr.fill()
        return False

    def _message_contact(self, node_id):
        for c in self._convs:
            if c.get("kind") == "direct" and node_id in c.get("members", []):
                self._sel_conv = c.get("conv")
                self._show_surface("chats")
                self._load_conv(self._sel_conv)
                self._render_chat()
                return
        # The daemon creates the pair conversation with the contact; if it is
        # not listed yet, a refresh brings it in.
        self._refresh_convs(then=lambda: self._message_contact_retry(node_id))

    def _message_contact_retry(self, node_id):
        for c in self._convs:
            if c.get("kind") == "direct" and node_id in c.get("members", []):
                self._sel_conv = c.get("conv")
                self._show_surface("chats")
                self._load_conv(self._sel_conv)
                self._render_chat()
                return

    # ------------------------------------------------------- radio surface

    def _build_radio(self):
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        col.set_margin_start(24)
        col.set_margin_end(24)
        col.set_margin_top(6)
        col.set_margin_bottom(16)
        outer.add(col)

        head = Gtk.Label(label=_t("Radio"), xalign=0)
        head.get_style_context().add_class("gvtitle")
        col.pack_start(head, False, False, 0)

        # Deterministic geometry: the content column is 1024-240-1-48 = 735px
        # on the smallest panel, so two 340px cards and a 12px gutter fit
        # with true slack (minsize measured the window minimum at 1041 with
        # 360s — 17 past the budget). Sized requests beat GTK's wrapped-label
        # width negotiation, which twice pushed the second column off-screen.
        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        col.pack_start(grid, False, False, 0)

        self._card_ident = self._radio_card(_t("THIS MACHINE"))
        grid.attach(self._card_ident[0], 0, 0, 1, 1)
        self._card_radio = self._radio_card(_t("THE RADIO"))
        grid.attach(self._card_radio[0], 1, 0, 1, 1)
        self._card_air = self._radio_card(_t("THE SHARED CHANNEL"))
        grid.attach(self._card_air[0], 0, 1, 1, 1)
        self._card_role = self._radio_card(_t("ROLE"))
        grid.attach(self._card_role[0], 1, 1, 1, 1)

        # Role consent lives inside its card.
        role_box = self._card_role[1]
        self._role_value = Gtk.Label(xalign=0)
        self._role_value.get_style_context().add_class("gvname")
        role_box.pack_start(self._role_value, False, False, 0)
        role_note = Gtk.Label(xalign=0)
        role_note.get_style_context().add_class("gvsub")
        role_note.set_line_wrap(True)
        role_note.set_max_width_chars(44)
        role_note.set_text(
            _t("A leaf never relays. A relay repeats other people's frames "
               "and spends the shared channel doing it; a battery machine "
               "at desk height mostly adds noise. Change roles only on a "
               "powered machine placed high."))
        role_box.pack_start(role_note, False, False, 0)
        self._role_btn = Gtk.Button(label=_t("Change Role…"))
        self._role_btn.get_style_context().add_class("gvquiet")
        self._role_btn.connect("clicked", lambda *_: self._open_role_card())
        role_box.pack_start(self._role_btn, False, False, 2)

        # Provisioning — the one-time hardware ceremony.
        prov = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        prov.get_style_context().add_class("gvcard")
        ph = Gtk.Label(label=_t("DONGLE PROVISIONING"), xalign=0)
        ph.get_style_context().add_class("gveyebrow")
        prov.pack_start(ph, False, False, 0)
        self._prov_text = Gtk.Label(xalign=0)
        self._prov_text.get_style_context().add_class("gvtext")
        self._prov_text.set_line_wrap(True)
        self._prov_text.set_max_width_chars(96)
        self._prov_text.set_text(
            _t("A new dongle needs one setup pass before its first use: it "
               "ships tuned to the European band. Probe reads the dongle's "
               "state without changing it."))
        prov.pack_start(self._prov_text, False, False, 0)
        pb = Gtk.Box(spacing=8)
        self._probe_btn = Gtk.Button(label=_t("Probe"))
        self._probe_btn.get_style_context().add_class("gvquiet")
        self._probe_btn.connect("clicked", lambda *_: self._run_probe())
        pb.pack_start(self._probe_btn, False, False, 0)
        self._prov_btn = Gtk.Button(label=_t("Provision"))
        self._prov_btn.get_style_context().add_class("gvprimary")
        self._prov_btn.connect("clicked", lambda *_: self._run_provision())
        self._prov_btn.set_sensitive(False)
        pb.pack_start(self._prov_btn, False, False, 0)
        prov.pack_start(pb, False, False, 0)
        col.pack_start(prov, False, False, 0)

        calm = Gtk.Label(xalign=0)
        calm.get_style_context().add_class("gvsub")
        calm.set_line_wrap(True)
        calm.set_max_width_chars(96)
        calm.set_text(
            _t("Govorimo sends no typing notices, polls nobody, and asks "
               "for no receipts in groups. The protocol forbids them; a "
               "quiet channel is the feature."))
        col.pack_start(calm, False, False, 4)
        return outer

    def _radio_card(self, eyebrow):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("gvcard")
        card.set_size_request(340, -1)
        card.set_halign(Gtk.Align.START)
        h = Gtk.Label(label=eyebrow, xalign=0)
        h.get_style_context().add_class("gveyebrow")
        card.pack_start(h, False, False, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.pack_start(inner, True, True, 0)
        return card, inner

    def _card_set_lines(self, card, lines):
        inner = card[1]
        for ch in list(inner.get_children()):
            inner.remove(ch)
        for text, cls in lines:
            lab = Gtk.Label(label=text, xalign=0)
            lab.get_style_context().add_class(cls)
            lab.set_line_wrap(True)
            lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            # Both bounds matter: width-chars floors the MINIMUM (or a long
            # mono line refuses to wrap and widens the card) and
            # max-width-chars caps the NATURAL request.
            lab.set_width_chars(16)
            lab.set_max_width_chars(40)
            inner.pack_start(lab, False, False, 0)
        inner.show_all()

    def _render_radio(self):
        st = self._status or {}
        self._card_set_lines(self._card_ident, [
            (self._me_name or _t("No identity"), "gvname"),
            (_t("node %s") % (self._me_node or "—"), "gvmono"),
        ])
        radios = st.get("radios") or [{}]
        r = radios[0] if radios else {}
        path = r.get("path", "")
        if self.link.state != govorimolib.READY:
            radio_lines = [(_t("The radio service is starting."), "gvtext")]
        elif path in ("none", "", None):
            radio_lines = [
                (_t("No radio attached"), "gvname"),
                (_t("Chats and boards read fine; nothing sends until a "
                    "dongle is plugged in."), "gvsub"),
            ]
        elif r.get("state") == "error":
            radio_lines = [
                (_t("Radio error"), "gvname"),
                (_t("The service retries by itself; unplugging and "
                    "replugging the dongle also restarts it."), "gvsub"),
            ]
        else:
            ch = r.get("channel")
            freq = ""
            if isinstance(ch, int):
                freq = " · %.3f MHz" % (850.125 + ch)
            radio_lines = [
                (_t("Listening"), "gvname"),
                ("%s · CH %s%s · %s bit/s"
                 % (path, ch if ch is not None else "?", freq,
                    r.get("air_rate", "?")), "gvmono"),
            ]
        self._card_set_lines(self._card_radio, radio_lines)

        air = st.get("airtime") or {}
        used = float(air.get("used_pct") or 0.0)
        budget = float(air.get("budget_pct") or 0.0)
        busy = st.get("channel_busy_pct")
        q = st.get("queue_depth")
        self._card_set_lines(self._card_air, [
            (_t("%.1f%% of this hour used, of a %.1f%% share")
             % (used, budget), "gvtext"),
            (_t("channel busy %s%% · %s frames queued")
             % (busy if busy is not None else "?",
                q if q is not None else "?"), "gvsub"),
            (_t("About 20 MB a day crosses this channel for everyone in "
                "range together; the share adapts to how many neighbours "
                "are heard."), "gvsub"),
        ])
        self._role_value.set_text(
            _t(_ROLE_WORDS.get(st.get("role", "leaf"), "Leaf")))

    # provisioning (serial work runs off the main thread; Article V)

    def _run_probe(self):
        self._probe_gen += 1
        gen = self._probe_gen
        self._probe_btn.set_sensitive(False)
        self._prov_text.set_text(_t("Reading the dongle…"))

        def work():
            try:
                res = govorimolib.probe()
                err = None
            except govorimolib.ProvisionError as e:
                res, err = None, str(e)
            except OSError as e:
                res, err = None, str(e)
            GLib.idle_add(land, res, err)

        def land(res, err):
            if not self._alive or gen != self._probe_gen:
                return False
            self._probe_btn.set_sensitive(True)
            if err is not None:
                self._prov_text.set_text(_t(err))
                self._prov_btn.set_sensitive(False)
                return False
            if res["mode"] == "config":
                if res["provisioned"]:
                    self._prov_text.set_text(
                        _t("The dongle is in configuration mode and already "
                           "carries the standard profile. Hold its button "
                           "until the LED turns green to return it to "
                           "service."))
                    self._prov_btn.set_sensitive(False)
                else:
                    self._prov_text.set_text(
                        _t("The dongle answered in configuration mode and "
                           "is ready to provision: address FFFF, channel 68 "
                           "(918.125 MHz), 9.6k air rate, listen-before-"
                           "talk. Written once, kept across power cycles."))
                    self._prov_btn.set_sensitive(True)
            elif res["mode"] == "transfer":
                self._prov_text.set_text(
                    _t("The dongle is provisioned and in service; nothing "
                       "to do."))
                self._prov_btn.set_sensitive(False)
            else:
                self._prov_text.set_text(
                    _t("The dongle did not answer. If the radio service "
                       "holds the port this is normal — otherwise hold the "
                       "button on the dongle for two seconds until the LED "
                       "turns red, then probe again."))
                self._prov_btn.set_sensitive(False)
            return False

        threading.Thread(target=work, daemon=True).start()

    def _run_provision(self):
        self._probe_gen += 1
        gen = self._probe_gen
        self._prov_btn.set_sensitive(False)
        self._probe_btn.set_sensitive(False)
        self._prov_text.set_text(_t("Writing the profile…"))

        def work():
            try:
                res = govorimolib.provision()
                err = None
            except govorimolib.ProvisionError as e:
                res, err = None, str(e)
            except OSError as e:
                res, err = None, str(e)
            GLib.idle_add(land, res, err)

        def land(res, err):
            if not self._alive or gen != self._probe_gen:
                return False
            self._probe_btn.set_sensitive(True)
            if err is not None:
                self._prov_text.set_text(_t(err))
                return False
            self._prov_text.set_text(
                _t("Provisioned. The radio service picks the dongle up by "
                   "itself within a few seconds."))
            return False

        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------- rail render

    def _render_rail(self):
        # A badge is UNREAD, nothing else — a count of contacts is not an
        # alarm and gets no red pill.
        unread_chats = sum(int(c.get("unread") or 0) for c in self._convs)
        unread_boards = sum(self._board_unread.values())
        badges = {
            "chats": str(unread_chats) if unread_chats else "",
            "boards": str(unread_boards) if unread_boards else "",
            "people": "",
            "radio": "",
        }
        for sid, (btn, badge) in self._rail_rows.items():
            badge.set_text(badges[sid])
            badge.set_visible(bool(badges[sid]))
            ctx = btn.get_style_context()
            if sid == self._surface:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

        # Conversations.
        for ch in list(self._conv_list.get_children()):
            self._conv_list.remove(ch)
        if not self._convs:
            hint = Gtk.Label()
            hint.get_style_context().add_class("gvempty")
            hint.set_line_wrap(True)
            hint.set_max_width_chars(24)
            hint.set_text(_t("Conversations appear once a contact bundle "
                             "is exchanged."))
            self._conv_list.pack_start(hint, False, False, 12)
        for c in self._convs:
            self._conv_list.pack_start(self._conv_row(c), False, False, 0)
        self._conv_list.show_all()

        # Boards.
        for ch in list(self._board_list.get_children()):
            self._board_list.remove(ch)
        for b in self._boards:
            self._board_list.pack_start(self._board_row(b), False, False, 0)
        follow = Gtk.Button()
        follow.set_relief(Gtk.ReliefStyle.NONE)
        follow.get_style_context().add_class("gvsurf")
        fl = Gtk.Label(label=_t("Follow a board…"), xalign=0)
        fl.get_style_context().add_class("gvsub")
        follow.add(fl)
        follow.connect("clicked", lambda *_: self._open_follow())
        self._board_list.pack_start(follow, False, False, 2)
        self._board_list.show_all()

    def _conv_row(self, c):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("gvsurf")
        if c.get("conv") == self._sel_conv and self._surface == "chats":
            b.get_style_context().add_class("selected")
        row = Gtk.Box(spacing=6)
        name = Gtk.Label(label=c.get("name") or c.get("conv", ""), xalign=0)
        name.get_style_context().add_class("gvrowname")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(name, True, True, 0)
        if c.get("kind") == "group":
            g = Gtk.Label(label=_t("group"))
            g.get_style_context().add_class("gvrowkind")
            row.pack_start(g, False, False, 0)
        unread = int(c.get("unread") or 0)
        if unread:
            u = Gtk.Label(label=str(unread))
            u.get_style_context().add_class("gvbadge")
            row.pack_end(u, False, False, 0)
        b.add(row)
        b.connect("clicked", lambda _b, tag=c.get("conv"): self._pick_conv(tag))
        return b

    def _board_row(self, brd):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("gvsurf")
        if brd.get("board_id") == self._sel_board and self._surface == "boards":
            b.get_style_context().add_class("selected")
        row = Gtk.Box(spacing=6)
        name = Gtk.Label(label=brd.get("name", ""), xalign=0)
        name.get_style_context().add_class("gvrowname")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(name, True, True, 0)
        unread = self._board_unread.get(brd.get("board_id"), 0)
        if unread:
            u = Gtk.Label(label=str(unread))
            u.get_style_context().add_class("gvbadge")
            row.pack_end(u, False, False, 0)
        b.add(row)
        b.connect("clicked",
                  lambda _b, bid=brd.get("board_id"): self._pick_board(bid))
        return b

    def _pick_conv(self, tag):
        self._sel_conv = tag
        if self._surface != "chats":
            self._show_surface("chats")
        self._load_conv(tag)
        self._render_chat()
        self._render_rail()

    def _pick_board(self, bid):
        self._sel_board = bid
        if self._surface != "boards":
            self._show_surface("boards")
        self._load_board(bid)
        self._render_board()
        self._render_rail()

    @staticmethod
    def _stack_show(stack, name):
        # A Stack refuses to switch to a child that is not itself visible,
        # and silently stays put — bitten before the window's first map.
        child = stack.get_child_by_name(name)
        if child is not None:
            child.show_all()
        stack.set_visible_child_name(name)

    def _show_surface(self, sid, save=True):
        self._surface = sid
        self._stack_show(self._stack, sid)
        self._stack_show(self._rail_stack,
                         sid if sid in ("chats", "boards") else "blank")
        self._render_rail()
        if sid == "radio":
            self._render_radio()
        if sid == "people":
            self._render_people()
        if save:
            self._save_prefs()

    # ------------------------------------------------------ the footer

    def _render_foot(self):
        st = self._status or {}
        radios = st.get("radios") or []
        r = radios[0] if radios else {}
        if self.link.state == govorimolib.MISMATCH:
            line = _t("Radio service version mismatch")
        elif self.link.state != govorimolib.READY:
            line = _t("Radio service starting…")
        elif r.get("path") in ("none", "", None):
            line = _t("No radio attached")
        elif r.get("state") == "error":
            line = _t("Radio error · retrying")
        else:
            n = st.get("neighbours", 0)
            line = _t("Listening · CH %s · %d heard") % (
                r.get("channel", "?"), n)
        self._radio_line.set_text(line)
        air = st.get("airtime") or {}
        used = float(air.get("used_pct") or 0.0)
        budget = float(air.get("budget_pct") or 0.0)
        self._meter_line.set_text(
            _t("airtime %.1f%% of a %.1f%% share") % (used, budget))
        self._meter.queue_draw()

    def _draw_meter(self, w, cr):
        air = (self._status or {}).get("airtime") or {}
        used = float(air.get("used_pct") or 0.0)
        budget = float(air.get("budget_pct") or 0.0)
        width = w.get_allocated_width()
        cr.set_source_rgb(*nbicons._hex("#DED4C2"))
        cr.rectangle(0, 2, width, 4)
        cr.fill()
        if budget > 0:
            frac = min(1.0, used / budget)
            colour = "#1A1916" if frac < 0.8 else "#C8341E"
            cr.set_source_rgb(*nbicons._hex(colour))
            cr.rectangle(0, 2, int(width * frac), 4)
            cr.fill()
        return False

    # --------------------------------------------------- daemon connection

    def _link_state(self, state, detail):
        if not self._alive:
            return
        if state == govorimolib.READY:
            self._link_banner.hide()
            hello = self.link.hello
            self._provisioned = bool(hello.get("provisioned"))
            self._me_node = hello.get("node_id") or ""
            self._me_name = hello.get("display_name") or ""
            if not self._provisioned:
                self._open_wizard()
            else:
                self._close_wizard()
                self._refresh_all()
        elif state == govorimolib.MISMATCH:
            self._link_banner.set_text(
                _t("The radio service speaks a different protocol version "
                   "than this app. Update the system.") + " " + detail)
            self._link_banner.show()
        else:
            self._link_banner.set_text(
                _t("Waiting for the radio service. It starts with the "
                   "session and restarts by itself."))
            self._link_banner.show()
        self._render_foot()
        if self._surface == "radio":
            self._render_radio()

    def _poll_status(self):
        if not self._alive:
            return False
        if self.link.state == govorimolib.READY:
            def done(res, err):
                if not self._alive or err is not None:
                    return
                self._status = res
                self._render_foot()
                if self._surface == "radio":
                    self._render_radio()
            self.link.call("get_status", None, done)

            def ndone(res, err):
                if not self._alive or err is not None:
                    return
                self._neighbours = res if isinstance(res, list) else []
                if self._surface == "people":
                    self._render_people()
            self.link.call("list_neighbours", None, ndone)
        return True

    def _refresh_all(self):
        self._refresh_convs()
        self._refresh_boards()
        self._refresh_contacts()
        self._poll_status()

    def _refresh_convs(self, then=None):
        def done(res, err):
            if not self._alive or err is not None:
                return
            self._convs = res if isinstance(res, list) else []
            tags = [c.get("conv") for c in self._convs]
            if self._sel_conv not in tags:
                self._sel_conv = tags[0] if tags else None
            if self._sel_conv:
                self._load_conv(self._sel_conv)
            self._render_rail()
            self._render_chat()
            if then is not None:
                then()
        self.link.call("list_conversations", None, done)

    def _refresh_boards(self):
        def done(res, err):
            if not self._alive or err is not None:
                return
            boards = res if isinstance(res, list) else []
            self._boards = [b for b in boards if b.get("followed")]
            ids = [b.get("board_id") for b in self._boards]
            if self._sel_board not in ids:
                self._sel_board = ids[0] if ids else None
            if self._sel_board:
                self._load_board(self._sel_board)
            self._render_rail()
            self._render_board()
        self.link.call("list_boards", None, done)

    def _refresh_contacts(self):
        def done(res, err):
            if not self._alive or err is not None:
                return
            self._contacts = res if isinstance(res, list) else []
            if self._surface == "people":
                self._render_people()
            self._render_rail()
        self.link.call("list_contacts", None, done)

    # ------------------------------------------------------- daemon events

    def _on_daemon_event(self, name, data):
        if not self._alive:
            return
        if name == "message":
            tag = data.get("conv", "")
            entry = dict(data)
            entry["out"] = False
            entry.setdefault("at", int(time.time()))
            known = any(c.get("conv") == tag for c in self._convs)
            self._messages.setdefault(tag, []).append(entry)
            viewing = (self._sel_conv == tag and self._surface == "chats")
            if viewing:
                self._append_chat_row(entry)
                if self._chat_stick:
                    self._scroll_chat_to_end()
                # Tell the daemon it is read (clears the unread counter).
                self.link.call("get_messages", {"conv": tag, "limit": 1})
            else:
                for c in self._convs:
                    if c.get("conv") == tag:
                        c["unread"] = int(c.get("unread") or 0) + 1
            if not known:
                self._refresh_convs()
                self._refresh_contacts()
            self._render_rail()
        elif name == "message_state":
            self._apply_state(data.get("conv", ""), data.get("msgid"),
                              data.get("state", ""))
        elif name == "post":
            bid = data.get("board_id", "")
            entry = dict(data)
            entry.setdefault("at", int(time.time()))
            self._posts.setdefault(bid, []).append(entry)
            known = any(b.get("board_id") == bid for b in self._boards)
            viewing = (self._sel_board == bid and self._surface == "boards")
            if viewing:
                self._append_post_row(entry)
            else:
                self._board_unread[bid] = self._board_unread.get(bid, 0) + 1
            for b in self._boards:
                if b.get("board_id") == bid:
                    b["post_count"] = int(b.get("post_count", 0)) + 1
            if not known:
                self._refresh_boards()
            self._render_rail()
            if viewing:
                self._render_board()
        elif name == "neighbour":
            nid = data.get("node_id")
            self._neighbours = [n for n in self._neighbours
                                if n.get("node_id") != nid]
            if data.get("state") != "gone":
                self._neighbours.append(data)
            if self._surface == "people":
                self._render_people()
            self._render_foot()
        elif name == "awaiting_key":
            tag = data.get("conv", "")
            who = self._name_of(data.get("from", ""))
            entry = {"kind": "system",
                     "text": _t("A message from %s is sealed under a key "
                                "that has not arrived. It opens by itself "
                                "once the key does.") % who}
            self._messages.setdefault(tag, []).append(entry)
            if self._sel_conv == tag and self._surface == "chats":
                self._append_chat_row(entry)
        elif name == "budget_warning":
            self._flash(_t("Airtime is nearly spent: %.1f%% used of a "
                           "%.1f%% share this hour.")
                        % (data.get("used_pct", 0), data.get("budget_pct", 0)))
            self._render_foot()
        elif name == "radio_state":
            self._poll_status()
            self._render_foot()

    def _name_of(self, node_id):
        if node_id == self._me_node:
            return self._me_name or _t("Me")
        for c in self._contacts:
            if c.get("node_id") == node_id:
                return c.get("display_name") or node_id
        return node_id

    def _conv_by_tag(self, tag):
        for c in self._convs:
            if c.get("conv") == tag:
                return c
        return None

    def _board_by_id(self, bid):
        for b in self._boards:
            if b.get("board_id") == bid:
                return b
        return None

    # ------------------------------------------------------------- overlays
    # One card at a time, on the window overlay, scrim behind, Esc closes.

    def _centre_card(self, layer, card_win):
        """Centre card_win in the live window, snapped to the grid, and keep
        it centred: preferred-size numbers measured before the first real
        allocation can be wrong, so the size-allocate hook re-centres with
        the true size once it exists (the mnemonic grid caught this)."""
        def place(width, height):
            # The overlay's allocation is the truth in every world this app
            # renders in — the guest window, and the offscreen harness that
            # lifts the content out of the toplevel (the window's own
            # allocation then describes a childless shell).
            alloc = self._overlay.get_allocation()
            sw, sh = nbapp.screen_size()
            W = alloc.width if alloc.width > 1 else sw
            H = alloc.height if alloc.height > 1 else sh
            x = max(0, (W - min(width, W - 48)) // 2)
            y = max(0, (H - min(height, H - 48)) // 2)
            x -= x % nbapp.GRID_UNIT
            y -= y % nbapp.GRID_UNIT
            if getattr(card_win, "_gv_pos", None) != (x, y):
                card_win._gv_pos = (x, y)
                layer.move(card_win, x, y)
        _min, nat = card_win.get_preferred_size()
        place(nat.width, nat.height)
        card_win.connect("size-allocate",
                         lambda _w, a: place(a.width, a.height))

    def _open_card(self, builder):
        self._close_card()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("gvscrim")
        alloc = self.get_allocation()
        sw, sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else sw
        H = alloc.height if alloc.height > 1 else sh
        scrim.set_size_request(W, H)
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.connect("button-press-event", lambda *_: self._close_card())
        layer.put(scrim, 0, 0)

        card_win = Gtk.EventBox()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("gvbigcard")
        card_win.add(card)
        builder(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._centre_card(layer, card_win)
        try:
            for gdkw in (layer.get_window(), card_win.get_window()):
                if gdkw is not None:
                    gdkw.raise_()
        except Exception:
            pass
        self._card = layer

    def _close_card(self):
        if self._card is not None:
            self._overlay.remove(self._card)
            self._card = None

    # -- contact exchange

    def _open_exchange(self):
        def build(card):
            title = Gtk.Label(label=_t("Exchange contact bundles"), xalign=0)
            title.get_style_context().add_class("gvcardtitle")
            card.pack_start(title, False, False, 0)
            sub = Gtk.Label(xalign=0)
            sub.get_style_context().add_class("gvsub")
            sub.set_line_wrap(True)
            sub.set_size_request(640, -1)
            sub.set_text(_t("Bundles cross by hand — a saved file on a "
                            "stick, or retyped — never over the air. Done "
                            "in person, there is nobody in the middle."))
            card.pack_start(sub, False, False, 0)

            cols = Gtk.Box(spacing=16)
            card.pack_start(cols, True, True, 0)

            mine = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            mh = Gtk.Label(label=_t("THIS MACHINE"), xalign=0)
            mh.get_style_context().add_class("gveyebrow")
            mine.pack_start(mh, False, False, 0)
            self._bundle_view = Gtk.TextView()
            self._bundle_view.set_wrap_mode(Gtk.WrapMode.CHAR)
            self._bundle_view.set_editable(False)
            self._bundle_view.get_style_context().add_class("gvbundle")
            bsw = Gtk.ScrolledWindow()
            bsw.set_size_request(300, 110)
            bsw.add(self._bundle_view)
            mine.pack_start(bsw, False, False, 0)
            save = Gtk.Button(label=_t("Save Bundle to File…"))
            save.get_style_context().add_class("gvquiet")
            save.connect("clicked", lambda *_: self._save_bundle())
            mine.pack_start(save, False, False, 0)
            cols.pack_start(mine, True, True, 0)

            theirs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            th = Gtk.Label(label=_t("THE OTHER MACHINE"), xalign=0)
            th.get_style_context().add_class("gveyebrow")
            theirs.pack_start(th, False, False, 0)
            self._paste_view = Gtk.TextView()
            self._paste_view.set_wrap_mode(Gtk.WrapMode.CHAR)
            self._paste_view.get_style_context().add_class("gvbundle")
            psw = Gtk.ScrolledWindow()
            psw.set_size_request(300, 110)
            psw.add(self._paste_view)
            theirs.pack_start(psw, False, False, 0)
            hb = Gtk.Box(spacing=8)
            load = Gtk.Button(label=_t("Open Bundle File…"))
            load.get_style_context().add_class("gvquiet")
            load.connect("clicked", lambda *_: self._load_bundle_file())
            hb.pack_start(load, False, False, 0)
            addb = Gtk.Button(label=_t("Add Contact"))
            addb.get_style_context().add_class("gvprimary")
            addb.connect("clicked", lambda *_: self._add_pasted())
            hb.pack_end(addb, False, False, 0)
            theirs.pack_start(hb, False, False, 0)
            cols.pack_start(theirs, True, True, 0)

            self._exchange_result = Gtk.Label(xalign=0)
            self._exchange_result.get_style_context().add_class("gvtext")
            self._exchange_result.set_line_wrap(True)
            self._exchange_result.set_size_request(640, -1)
            card.pack_start(self._exchange_result, False, False, 0)

            close = Gtk.Button(label=_t("Close"))
            close.get_style_context().add_class("gvquiet")
            close.connect("clicked", lambda *_: self._close_card())
            card.pack_end(close, False, False, 0)

        self._open_card(build)

        def done(res, err):
            if not self._alive or self._card is None:
                return
            buf = self._bundle_view.get_buffer()
            if err is not None:
                buf.set_text(_t("The bundle could not be read: %s")
                             % err.get("message", err.get("code", "")))
                return
            self._my_bundle = res.get("bundle", "")
            buf.set_text(self._my_bundle)
        self.link.call("get_contact_bundle", None, done)

    def _save_bundle(self):
        bundle = getattr(self, "_my_bundle", "")
        if not bundle:
            return
        name = "%s.govorimo-bundle.txt" % (self._me_name or "identity")
        path = nbpicker.save_file(self, _t("Save Bundle"),
                                  suggested_name=name, default_ext=".txt")
        if not path:
            return
        try:
            nbapp.atomic_write_text(path, bundle + "\n")
            self._exchange_result.set_text(_t("Saved to %s") % path)
        except OSError as e:
            self._exchange_result.set_text(nbapp.save_failure_reason(e, path))

    def _load_bundle_file(self):
        path = nbpicker.open_file(self, _t("Open Bundle File"))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(4096).strip()
        except OSError as e:
            self._exchange_result.set_text(str(e))
            return
        self._paste_view.get_buffer().set_text(text)

    def _add_pasted(self):
        buf = self._paste_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                            False).strip()
        if not text:
            self._exchange_result.set_text(
                _t("Paste the other machine's bundle first."))
            return

        def done(res, err):
            if not self._alive or self._card is None:
                return
            if err is not None:
                self._exchange_result.set_text(
                    err.get("message") or err.get("code", ""))
                return
            self._exchange_result.set_markup(
                GLib.markup_escape_text(
                    _t("%s added. Safety number:") % res.get("display_name", ""))
                + "\n<span size='x-large' weight='bold'>"
                + GLib.markup_escape_text(res.get("safety_number", ""))
                + "</span>\n"
                + GLib.markup_escape_text(
                    _t("Read it aloud. The other machine must show the "
                       "same number; if it does not, stop and exchange "
                       "again in person.")))
            self._refresh_contacts()
            self._refresh_convs()
        self.link.call("add_contact", {"bundle": text}, done)

    # -- follow board

    def _open_follow(self):
        def build(card):
            title = Gtk.Label(label=_t("Follow a board"), xalign=0)
            title.get_style_context().add_class("gvcardtitle")
            card.pack_start(title, False, False, 0)
            sub = Gtk.Label(xalign=0)
            sub.get_style_context().add_class("gvsub")
            sub.set_line_wrap(True)
            sub.set_size_request(430, -1)
            sub.set_text(_t("A board is named, public, and local by "
                            "physics: it reaches whoever the radio "
                            "reaches. Names are lowercase, dot-separated."))
            card.pack_start(sub, False, False, 0)
            entry = Gtk.Entry()
            entry.set_placeholder_text("local.general")
            card.pack_start(entry, False, False, 0)
            note = Gtk.Label(xalign=0)
            note.get_style_context().add_class("gvsub")
            card.pack_start(note, False, False, 0)

            def go(*_a):
                name = entry.get_text().strip().lower()
                if not name:
                    return

                def done(res, err):
                    if not self._alive or self._card is None:
                        return
                    if err is not None:
                        note.set_text(err.get("message") or err.get("code", ""))
                        return
                    self._close_card()
                    self._sel_board = res.get("board_id")
                    self._refresh_boards()
                    self._show_surface("boards")
                self.link.call("follow_board", {"name": name}, done)
            entry.connect("activate", go)
            row = Gtk.Box(spacing=8)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("gvquiet")
            cancel.connect("clicked", lambda *_: self._close_card())
            row.pack_start(cancel, False, False, 0)
            ok = Gtk.Button(label=_t("Follow"))
            ok.get_style_context().add_class("gvprimary")
            ok.connect("clicked", go)
            row.pack_end(ok, False, False, 0)
            card.pack_end(row, False, False, 0)
            GLib.idle_add(entry.grab_focus)
        self._open_card(build)

    # -- new group

    def _open_group(self):
        if not self._contacts:
            self._open_exchange()
            return

        def build(card):
            title = Gtk.Label(label=_t("New group"), xalign=0)
            title.get_style_context().add_class("gvcardtitle")
            card.pack_start(title, False, False, 0)
            entry = Gtk.Entry()
            entry.set_placeholder_text(_t("Group name"))
            card.pack_start(entry, False, False, 0)
            sub = Gtk.Label(xalign=0)
            sub.get_style_context().add_class("gvsub")
            sub.set_line_wrap(True)
            sub.set_size_request(430, -1)
            sub.set_text(_t("Each member receives the keys over the pair "
                            "conversation — a few frames of airtime per "
                            "member. Groups work best under about ten "
                            "people."))
            card.pack_start(sub, False, False, 0)
            checks = []
            for c in self._contacts:
                cb = Gtk.CheckButton(
                    label=c.get("display_name") or c.get("node_id", ""))
                checks.append((cb, c.get("node_id", "")))
                card.pack_start(cb, False, False, 0)
            note = Gtk.Label(xalign=0)
            note.get_style_context().add_class("gvsub")
            card.pack_start(note, False, False, 0)

            def go(*_a):
                name = entry.get_text().strip()
                members = [nid for cb, nid in checks if cb.get_active()]
                if not name:
                    note.set_text(_t("A group needs a name."))
                    return
                if not members:
                    note.set_text(_t("Choose at least one member."))
                    return

                def done(res, err):
                    if not self._alive or self._card is None:
                        return
                    if err is not None:
                        note.set_text(err.get("message") or err.get("code", ""))
                        return
                    self._close_card()
                    self._sel_conv = res.get("conv")
                    self._refresh_convs()
                    self._show_surface("chats")
                self.link.call("create_group",
                               {"name": name, "members": members}, done)
            row = Gtk.Box(spacing=8)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("gvquiet")
            cancel.connect("clicked", lambda *_: self._close_card())
            row.pack_start(cancel, False, False, 0)
            ok = Gtk.Button(label=_t("Create Group"))
            ok.get_style_context().add_class("gvprimary")
            ok.connect("clicked", go)
            row.pack_end(ok, False, False, 0)
            card.pack_end(row, False, False, 0)
            GLib.idle_add(entry.grab_focus)
        self._open_card(build)

    # -- members

    def _open_members(self):
        conv = self._conv_by_tag(self._sel_conv)
        if conv is None or conv.get("kind") != "group":
            return
        tag = conv.get("conv")

        def build(card):
            title = Gtk.Label(label=_t("Members of %s") % conv.get("name", ""),
                              xalign=0)
            title.get_style_context().add_class("gvcardtitle")
            card.pack_start(title, False, False, 0)
            for nid in conv.get("members", []):
                row = Gtk.Box(spacing=8)
                lab = Gtk.Label(label=self._name_of(nid), xalign=0)
                lab.get_style_context().add_class("gvtext")
                row.pack_start(lab, True, True, 0)
                rm = Gtk.Button(label=_t("Remove…"))
                rm.get_style_context().add_class("gvdanger")
                rm.connect("clicked",
                           lambda _b, n=nid: self._confirm_remove(tag, n))
                row.pack_end(rm, False, False, 0)
                card.pack_start(row, False, False, 0)
            note = Gtk.Label(xalign=0)
            note.get_style_context().add_class("gvsub")
            note.set_line_wrap(True)
            note.set_size_request(430, -1)
            note.set_text(_t("Removing a member rekeys the whole group — "
                             "every remaining member resends keys to every "
                             "other. The airtime cost is quoted before "
                             "anything is sent."))
            card.pack_start(note, False, False, 0)
            addrow = Gtk.Box(spacing=8)
            candidates = [c for c in self._contacts
                          if c.get("node_id") not in conv.get("members", [])]
            if candidates:
                combo = Gtk.ComboBoxText()
                for c in candidates:
                    combo.append(c.get("node_id", ""),
                                 c.get("display_name") or c.get("node_id", ""))
                combo.set_active(0)
                addrow.pack_start(combo, True, True, 0)
                addb = Gtk.Button(label=_t("Add Member"))
                addb.get_style_context().add_class("gvquiet")

                def add(*_a):
                    nid = combo.get_active_id()
                    if not nid:
                        return

                    def done(res, err):
                        if not self._alive:
                            return
                        if err is not None:
                            note.set_text(err.get("message")
                                          or err.get("code", ""))
                            return
                        note.set_text(
                            _t("Added. The key hand-over cost about %.1f s "
                               "of shared airtime.")
                            % float(res.get("airtime_cost_s", 0)))
                        self._refresh_convs()
                    self.link.call("add_member",
                                   {"conv": tag, "node_id": nid}, done)
                addb.connect("clicked", add)
                addrow.pack_end(addb, False, False, 0)
                card.pack_start(addrow, False, False, 0)
            close = Gtk.Button(label=_t("Close"))
            close.get_style_context().add_class("gvquiet")
            close.connect("clicked", lambda *_: self._close_card())
            card.pack_end(close, False, False, 0)
        self._open_card(build)

    def _confirm_remove(self, tag, nid):
        name = self._name_of(nid)

        def build(card):
            title = Gtk.Label(label=_t("Remove %s from the group?") % name,
                              xalign=0)
            title.get_style_context().add_class("gvcardtitle")
            card.pack_start(title, False, False, 0)
            body = Gtk.Label(xalign=0)
            body.get_style_context().add_class("gvtext")
            body.set_line_wrap(True)
            body.set_size_request(430, -1)
            body.set_text(_t("They keep what they already received. The "
                             "group rekeys so they receive nothing new; "
                             "the rekey spends everyone's airtime."))
            card.pack_start(body, False, False, 0)
            row = Gtk.Box(spacing=8)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("gvquiet")
            cancel.connect("clicked", lambda *_: self._close_card())
            row.pack_start(cancel, False, False, 0)
            ok = Gtk.Button(label=_t("Remove and Rekey"))
            ok.get_style_context().add_class("gvdanger")

            def go(*_a):
                def done(res, err):
                    if not self._alive:
                        return
                    self._close_card()
                    if err is not None:
                        self._say_error(err)
                        return
                    self._flash(_t("Removed. The rekey cost about %.1f s "
                                   "of shared airtime.")
                                % float(res.get("airtime_cost_s", 0)))
                    self._refresh_convs()
                self.link.call("remove_member",
                               {"conv": tag, "node_id": nid}, done)
            ok.connect("clicked", go)
            row.pack_end(ok, False, False, 0)
            card.pack_end(row, False, False, 0)
            GLib.idle_add(cancel.grab_focus)
        self._open_card(build)

    # -- role consent

    def _open_role_card(self):
        current = (self._status or {}).get("role", "leaf")

        def build(card):
            title = Gtk.Label(label=_t("Node role"), xalign=0)
            title.get_style_context().add_class("gvcardtitle")
            card.pack_start(title, False, False, 0)
            body = Gtk.Label(xalign=0)
            body.get_style_context().add_class("gvtext")
            body.set_line_wrap(True)
            body.set_size_request(430, -1)
            body.set_text(
                _t("A leaf talks and listens and never repeats. A relay "
                   "rebroadcasts other people's frames, which extends the "
                   "town's reach and spends this machine's share of the "
                   "channel — right for a mains-powered machine placed "
                   "high, wrong for a laptop on a desk. A station is a "
                   "relay that additionally serves history."))
            card.pack_start(body, False, False, 0)
            group = None
            btns = {}
            for role in ("leaf", "relay", "station"):
                rb = Gtk.RadioButton.new_with_label_from_widget(
                    group, _t(_ROLE_WORDS[role]))
                group = group or rb
                if role == current:
                    rb.set_active(True)
                btns[role] = rb
                card.pack_start(rb, False, False, 0)
            note = Gtk.Label(xalign=0)
            note.get_style_context().add_class("gvsub")
            card.pack_start(note, False, False, 0)
            row = Gtk.Box(spacing=8)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("gvquiet")
            cancel.connect("clicked", lambda *_: self._close_card())
            row.pack_start(cancel, False, False, 0)
            ok = Gtk.Button(label=_t("Set Role"))
            ok.get_style_context().add_class("gvprimary")

            def go(*_a):
                role = next(r for r, b in btns.items() if b.get_active())

                def done(res, err):
                    if not self._alive:
                        return
                    if err is not None:
                        note.set_text(err.get("message") or err.get("code", ""))
                        return
                    self._close_card()
                    self._poll_status()
                self.link.call("set_role", {"role": role}, done)
            ok.connect("clicked", go)
            row.pack_end(ok, False, False, 0)
            card.pack_end(row, False, False, 0)
            GLib.idle_add(cancel.grab_focus)
        self._open_card(build)

    # ------------------------------------------------------- first-run wizard

    def _open_wizard(self):
        if self._wizard is not None:
            return
        self._wizard_stage("choose")

    def _close_wizard(self):
        if self._wizard is not None:
            self._overlay.remove(self._wizard)
            self._wizard = None

    def _wizard_stage(self, stage, payload=None):
        if self._wizard is not None:
            self._overlay.remove(self._wizard)
            self._wizard = None
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("gvwizardground")
        alloc = self.get_allocation()
        sw, sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else sw
        H = alloc.height if alloc.height > 1 else sh
        scrim.set_size_request(W, H)
        layer.put(scrim, 0, 0)
        card_win = Gtk.EventBox()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("gvbigcard")
        card_win.add(card)
        getattr(self, "_wz_" + stage)(card, payload)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._centre_card(layer, card_win)
        self._wizard = layer

    def _wz_choose(self, card, _payload):
        t = Gtk.Label(label=_t("Govorimo"), xalign=0)
        t.get_style_context().add_class("gvcardtitle")
        card.pack_start(t, False, False, 0)
        body = Gtk.Label(xalign=0)
        body.get_style_context().add_class("gvtext")
        body.set_line_wrap(True)
        body.set_size_request(460, -1)
        body.set_text(
            _t("Messages travel by LoRa radio — no internet, no accounts, "
               "no servers. An identity is a key pair that lives on this "
               "machine and nowhere else."))
        card.pack_start(body, False, False, 0)
        create = Gtk.Button(label=_t("Create an Identity"))
        create.get_style_context().add_class("gvprimary")
        create.connect("clicked",
                       lambda *_: self._wizard_stage("create"))
        card.pack_start(create, False, False, 0)
        restore = Gtk.Button(label=_t("Restore from Recovery Words…"))
        restore.get_style_context().add_class("gvquiet")
        restore.connect("clicked",
                        lambda *_: self._wizard_stage("restore"))
        card.pack_start(restore, False, False, 0)

    def _wz_create(self, card, _payload):
        t = Gtk.Label(label=_t("The name on the air"), xalign=0)
        t.get_style_context().add_class("gvcardtitle")
        card.pack_start(t, False, False, 0)
        body = Gtk.Label(xalign=0)
        body.get_style_context().add_class("gvtext")
        body.set_line_wrap(True)
        body.set_size_request(460, -1)
        body.set_text(_t("The display name travels in announcements to "
                         "whoever the radio reaches. Up to 32 characters; "
                         "it can be changed later only by restoring."))
        card.pack_start(body, False, False, 0)
        entry = Gtk.Entry()
        entry.set_max_length(32)
        entry.set_placeholder_text(_t("Display name"))
        card.pack_start(entry, False, False, 0)
        note = Gtk.Label(xalign=0)
        note.get_style_context().add_class("gvsub")
        card.pack_start(note, False, False, 0)

        def go(*_a):
            name = entry.get_text().strip()
            if not name:
                note.set_text(_t("A name is needed; it is what neighbours "
                                 "see."))
                return

            def done(res, err):
                if not self._alive:
                    return
                if err is not None:
                    note.set_text(err.get("message") or err.get("code", ""))
                    return
                self._me_node = res.get("node_id", "")
                self._me_name = name
                self._wizard_stage("mnemonic",
                                   res.get("recovery_mnemonic", ""))
            self.link.call("create_identity", {"display_name": name}, done)
        entry.connect("activate", go)
        row = Gtk.Box(spacing=8)
        back = Gtk.Button(label=_t("Back"))
        back.get_style_context().add_class("gvquiet")
        back.connect("clicked", lambda *_: self._wizard_stage("choose"))
        row.pack_start(back, False, False, 0)
        ok = Gtk.Button(label=_t("Create"))
        ok.get_style_context().add_class("gvprimary")
        ok.connect("clicked", go)
        row.pack_end(ok, False, False, 0)
        card.pack_end(row, False, False, 0)
        GLib.idle_add(entry.grab_focus)

    def _wz_mnemonic(self, card, mnemonic):
        t = Gtk.Label(label=_t("The recovery words"), xalign=0)
        t.get_style_context().add_class("gvcardtitle")
        card.pack_start(t, False, False, 0)
        body = Gtk.Label(xalign=0)
        body.get_style_context().add_class("gvtext")
        body.set_line_wrap(True)
        body.set_size_request(460, -1)
        body.set_text(
            _t("These 24 words rebuild the identity if this machine is "
               "lost. They are shown once, now, and never again — there is "
               "no cloud and no reset. Write them on paper, in order, and "
               "keep the paper somewhere that survives the machine."))
        card.pack_start(body, False, False, 0)
        grid = Gtk.Grid()
        grid.set_column_spacing(18)
        grid.set_row_spacing(4)
        words = (mnemonic or "").split()
        for i, w in enumerate(words):
            lab = Gtk.Label(xalign=0)
            lab.get_style_context().add_class("gvword")
            lab.set_text("%2d  %s" % (i + 1, w))
            grid.attach(lab, i // 6, i % 6, 1, 1)
        card.pack_start(grid, False, False, 4)
        ack = Gtk.CheckButton(label=_t("The 24 words are written on paper"))
        card.pack_start(ack, False, False, 0)
        done_btn = Gtk.Button(label=_t("Open Govorimo"))
        done_btn.get_style_context().add_class("gvprimary")
        done_btn.set_sensitive(False)
        ack.connect("toggled",
                    lambda cb: done_btn.set_sensitive(cb.get_active()))

        def finish(*_a):
            self._close_wizard()
            self._provisioned = True
            self._refresh_all()
        done_btn.connect("clicked", finish)
        card.pack_end(done_btn, False, False, 0)

    def _wz_restore(self, card, _payload):
        t = Gtk.Label(label=_t("Restore from recovery words"), xalign=0)
        t.get_style_context().add_class("gvcardtitle")
        card.pack_start(t, False, False, 0)
        body = Gtk.Label(xalign=0)
        body.get_style_context().add_class("gvtext")
        body.set_line_wrap(True)
        body.set_size_request(460, -1)
        body.set_text(_t("Enter the 24 words in order, separated by "
                         "spaces."))
        card.pack_start(body, False, False, 0)
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.get_style_context().add_class("gvbundle")
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(420, 90)
        sw.add(view)
        card.pack_start(sw, False, False, 0)
        note = Gtk.Label(xalign=0)
        note.get_style_context().add_class("gvsub")
        note.set_line_wrap(True)
        note.set_max_width_chars(56)
        card.pack_start(note, False, False, 0)

        def go(*_a):
            buf = view.get_buffer()
            words = buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                                 False).strip()
            if not words:
                return

            def done(res, err):
                if not self._alive:
                    return
                if err is not None:
                    note.set_text(err.get("message") or err.get("code", ""))
                    return
                self._me_node = res.get("node_id", "")
                note.set_text("")
                self._wizard_stage("restored", res.get("warning", ""))
            self.link.call("restore_identity",
                           {"recovery_mnemonic": words}, done)
        row = Gtk.Box(spacing=8)
        back = Gtk.Button(label=_t("Back"))
        back.get_style_context().add_class("gvquiet")
        back.connect("clicked", lambda *_: self._wizard_stage("choose"))
        row.pack_start(back, False, False, 0)
        ok = Gtk.Button(label=_t("Restore"))
        ok.get_style_context().add_class("gvprimary")
        ok.connect("clicked", go)
        row.pack_end(ok, False, False, 0)
        card.pack_end(row, False, False, 0)
        GLib.idle_add(view.grab_focus)

    def _wz_restored(self, card, _warning):
        t = Gtk.Label(label=_t("Restored"), xalign=0)
        t.get_style_context().add_class("gvcardtitle")
        card.pack_start(t, False, False, 0)
        body = Gtk.Label(xalign=0)
        body.get_style_context().add_class("gvtext")
        body.set_line_wrap(True)
        body.set_size_request(460, -1)
        body.set_text(
            _t("The identity is back, and boards and groups work at once. "
               "Old one-to-one conversations are not safe to send on until "
               "contact bundles are exchanged again in person — the "
               "restore cannot know how much was already said."))
        card.pack_start(body, False, False, 0)
        done_btn = Gtk.Button(label=_t("Open Govorimo"))
        done_btn.get_style_context().add_class("gvprimary")

        def finish(*_a):
            self._close_wizard()
            self._provisioned = True
            self._refresh_all()
        done_btn.connect("clicked", finish)
        card.pack_end(done_btn, False, False, 0)

    # --------------------------------------------------------------- menus

    def menu_items(self, name):
        if name == "File":
            has_contacts = bool(self._contacts)
            return [
                ("New Group…", self._open_group if has_contacts else None),
                ("Add Contact…", self._open_exchange),
                ("Follow Board…", self._open_follow),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "View":
            # No active-mark prefix: a "•  "-prefixed label bypasses the
            # catalog's suffix transform and ships English in 16 languages
            # (measured; tasks.py has the bug today). The rail already shows
            # where you are.
            def mk(sid):
                return ("%s    Ctrl+%d"
                        % (_SURFACE_LABELS[sid], _SURFACES.index(sid) + 1),
                        lambda s=sid: self._show_surface(s))
            return [mk("chats"), mk("boards"), mk("people"), mk("radio")]
        if name == "Radio":
            return [
                ("Probe Dongle", self._menu_probe),
                ("Change Role…", self._open_role_card),
            ]
        return super().menu_items(name)

    def _menu_probe(self):
        self._show_surface("radio")
        self._run_probe()

    # ---------------------------------------------------------------- keys

    def _on_key(self, w, ev):
        state = ev.state & Gtk.accelerator_get_default_mod_mask()
        if state == Gdk.ModifierType.CONTROL_MASK:
            n = {Gdk.KEY_1: "chats", Gdk.KEY_2: "boards",
                 Gdk.KEY_3: "people", Gdk.KEY_4: "radio"}.get(ev.keyval)
            if n:
                self._show_surface(n)
                return True
        if ev.keyval == Gdk.KEY_Escape:
            if self._card is not None:
                self._close_card()
                return True
            if self._reply_to is not None:
                self._set_reply(None)
                return True
            if self._post_reply_to is not None:
                self._set_post_reply(None)
                return True
            # The wizard is not escapable — there is nothing behind it yet.
            if self._wizard is not None:
                return True
        return super()._on_key(w, ev)

    # ----------------------------------------------------------------- CSS

    def _install_css(self):
        css = b"""
        .gvrail { background: #EFEBE0; border-right: 1px solid #C9C4B6;
                  padding: 8px 0; }
        .gvsurf { padding: 8px 14px; border: none; border-radius: 0;
                  background: transparent; box-shadow: none; }
        .gvsurf:hover { background: #F1EEE6; }
        .gvsurf.selected { background: #EAE3D2;
                           box-shadow: inset 3px 0 0 #C8341E; }
        .gvsurflabel { font-size: 15px; color: #1A1916; font-weight: 600; }
        .gvrowname { font-size: 14px; color: #1A1916; }
        .gvrowkind { font-size: 11px; color: #8A857A; }
        .gvbadge { font-size: 11px; font-weight: 700; color: #FCFBF8;
                   background: #C8341E; border-radius: 8px;
                   padding: 1px 6px; }
        .gvhair { background: #D7D2C5; min-height: 1px;
                  margin-left: 12px; margin-right: 12px; }
        .gvedge { background: #C9C4B6; min-width: 1px; }
        .gvfoot { border-top: 1px solid #D7D2C5; padding: 8px 12px; }
        .gvfootline { font-size: 12px; color: #6E695E; font-weight: 600; }
        .gvfootsub { font-size: 11px; color: #8A857A; }

        .gvhead { padding: 10px 24px 0 24px; }
        .gvtitle { font-size: 24px; font-weight: 700; color: #1A1916; }
        .gvsub { font-size: 12px; color: #8A857A; }
        .gveyebrow { font-size: 11px; letter-spacing: 0.13em; color: #8A857A;
                     font-weight: 700; }
        .gvempty { font-size: 14px; color: #8A857A; }

        .gvmsg { padding: 3px 0; }
        .gvpost { padding: 8px 0; border-bottom: 1px solid #D7D2C5; }
        .gvauthor { font-size: 12px; color: #8A857A; font-weight: 700; }
        .gvname { font-size: 15px; color: #1A1916; font-weight: 600; }
        .gvtext { font-size: 15px; color: #2A2620; }
        .gvwhen { font-size: 11px; color: #9A9484; }
        .gvstate { font-size: 12px; color: #6E695E; }
        .gvstate.gvdelivered { color: #7FA98C; }
        .gvstate.gvfailed { color: #C8341E; font-weight: 700; }
        .gvref { font-size: 11px; color: #9A9484; }
        .gvsystem { font-size: 12px; color: #8A857A; font-style: italic; }
        .gvchip { font-size: 10px; color: #6E695E; background: #F1EEE6;
                  border: 1px solid #C9C4B6; border-radius: 8px;
                  padding: 0 6px; }
        .gvmono { font-size: 12px; color: #6E695E;
                  font-family: "Noto Sans Mono","DejaVu Sans Mono",monospace; }

        .gvreply { background: #F8F7F2; border-top: 1px solid #D7D2C5;
                   padding: 4px 24px; }
        .gvcompose { border-top: 1px solid #C9C4B6; padding: 10px 24px; }
        .gvprice { font-size: 11px; color: #8A857A; }
        .gvprice.gvover { color: #C8341E; font-weight: 600; }

        .gvprimary { background: #1A1916; color: #FCFBF8; border: none;
                     border-radius: 6px; padding: 6px 16px;
                     font-weight: 600; }
        .gvprimary label { color: inherit; }
        .gvprimary:hover { background: #2A2620; }
        .gvprimary:disabled { background: #B3AD9E; color: #FCFBF8; }
        .gvquiet { background: #F8F7F2; border: 1px solid #C9C4B6;
                   border-radius: 6px; padding: 5px 12px; color: #2A2620; }
        .gvquiet:hover { background: #F1EEE6; }
        .gvdanger { background: #FCFBF8; border: 1px solid #C8341E;
                    color: #C8341E; border-radius: 6px; padding: 5px 12px; }
        .gvdanger:hover { background: #FBEFEC; }
        .gvdanger label { color: inherit; }

        .gvcard { background: #FCFBF8; border: 1px solid #D7D2C5;
                  border-radius: 12px; padding: 12px 14px; }
        .gvbigcard { background: #FCFBF8; border: 1px solid #B3AD9E;
                     border-radius: 12px; padding: 20px 24px; }
        .gvcardtitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .gvscrim { background: rgba(26,25,22,0.35); }
        .gvwizardground { background: #DED4C2; }
        .gvword { font-size: 16px; color: #1A1916;
                  font-family: "Noto Sans Mono","DejaVu Sans Mono",monospace; }
        .gvbundle { font-size: 12px; border: 1px solid #C9C4B6;
                    font-family: "Noto Sans Mono","DejaVu Sans Mono",monospace; }
        .gvbundle text { background: #F8F7F2; color: #2A2620; }
        .gvbanner { background: #F1EEE6; color: #6E695E; font-size: 12px;
                    padding: 4px 12px; border-bottom: 1px solid #D7D2C5; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def main():
    nbapp.run(GovorimoWindow)


if __name__ == "__main__":
    main()
