#!/usr/bin/env python3
"""
Journal — the Notebook OS dated journaling app (native GTK).

A paper writing surface with an entries sidebar grouped by month and a serif
editor showing the entry's long date, a written-at meta line, and an editable
body with a live word count / autosave indicator. Opens EMPTY (no entries) per
the no-seed rule; the + button or File -> New Entry starts one dated today.
Entries persist under $NB_HOME/.config/notebook; File -> Export to PDF renders
them into $NB_HOME/Documents.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import time

import cairo  # noqa: F401  (PDF surfaces come from nbprint.report_page)

import nbapp
import nbicons
import nbprint
from nbi18n import _t  # noqa: E402

WD_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WD_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
           "Friday", "Saturday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

# -- persistence: entries survive close/reboot under $NB_HOME/.config/notebook --
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
JOURNAL_FILE = os.path.join(CFG_DIR, "journal.json")
# File -> Export to PDF writes into Documents; the journal.json above is
# session-recovery autosave, not a user-facing file.
DOCS_DIR = os.path.join(HOME, "Documents")
# every entry dict carries exactly these keys; the UI reads them unconditionally
ENTRY_KEYS = ("day", "wd", "month_label", "date", "meta",
              "title", "preview", "text")

# The meta line's fixed half. Stored ENGLISH in the entry, like every other
# derived field here, because the interface language can be changed under a
# journal that is already written — so the translation has to happen at DISPLAY
# time, not at creation time. It cannot happen through the composed string
# either: nbi18n's auto-translate layer looks the whole label up, and
# "Written at 09:00" is not a catalog key (the catalogs carry the fragment
# "Written at " precisely because it is joined to a live value). Result before
# this: every other line of the entry page was in Spanish and this one line was
# not. See _meta_display.
META_PREFIX = "Written at "

# Writing-column geometry: the measure the design wants, the narrowest one it
# will fall back to on a small panel, and the .page CSS padding either side.
PAGE_MAX = 720
PAGE_MIN = 420
PAGE_PAD = 48

# Straight quotes and hyphen-hyphen dashes become real typography as the
# writer types (nbapp.smart_replacement, shared with Writer and Novel).


def _atomic_write_json(path, obj):
    """Write the journal crash-safely, through the SHARED nbapp writer.

    This used to be a private copy of the atomic write, and that copy is what
    destroyed journals: nbapp.atomic_write_json also quarantines a store it
    could not parse (nbapp.preserve_damaged) before replacing it, and the local
    twin had no such guard. A journal.json that failed to load — still plainly
    holding the user's diary — was read as "no entries", and the destroy-time
    flush then wrote that empty state straight over it. Opening and closing the
    app was enough to lose the lot. One implementation now, so it cannot drift
    apart again."""
    nbapp.atomic_write_json(path, obj)


class Journal(nbapp.AppWindow):
    app_name = "Journal"
    menus = ("File", "Edit", "Format", "Insert", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        # each entry: dict(day, wd, month_label, date, meta, title, preview,
        # text). Loaded from disk before the UI is built; Journal opens EMPTY
        # on first run (no seed), so the default is ([], -1).
        self.entries, self.active = self._load_entries()
        self._loading = False
        # Guards the smart-quote re-insert against re-entering its own handler.
        self._smart_busy = False
        # Set BEFORE any timer is armed or any widget is built: every deferred
        # sink below reads it, and _on_destroy can fire the moment the window
        # exists. A GLib timeout already dispatched when source_remove() runs
        # still executes, so cancelling the source is not enough on its own --
        # the sinks have to be able to see that their owner is gone.
        self._closed = False
        # A queued second activation of Delete must not consume the neighbour
        # that becomes active after the first deletion. Released on the next
        # main-loop turn so later, intentional deletes remain available.
        self._delete_pending = False
        self._save_timer = None
        # coalesces the live word recount off the keystroke hot path (see
        # _on_change / _recount_tick); None when no recount is pending
        self._count_timer = None
        # live handles to the ACTIVE row's title/preview labels, so the autosave
        # hot path can refresh them in place instead of rebuilding the sidebar.
        # Kept in sync by _refresh_list (reset here, re-captured per active row).
        self._active_title_lbl = None
        self._active_preview_lbl = None

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._sidebar(), False, False, 0)
        body.pack_start(self._editor(), True, True, 0)

        self._refresh_list()
        self._load_active()
        # Undo/redo over the whole journal, not just the open entry: a delete
        # takes the entry back out of the list, and Ctrl+Z has to put it back.
        # Built LAST so its baseline is the journal as it was restored.
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()
        # if a saved entry restored open, put the cursor in the editor so a
        # returning writer can type immediately (skipped in the empty state,
        # where the editor is hidden)
        if 0 <= self.active < len(self.entries):
            self.body.grab_focus()

        # final flush: closing the window (dot button / File->Close / Esc all
        # route through Gtk.Window.close -> "destroy") must not lose the last edit
        self.connect("destroy", self._on_destroy)

    # ---------------- persistence ----------------
    def _load_entries(self):
        """Load saved entries + active index from disk. Returns
        (entries, active).

        FORGIVING BY DESIGN. This used to be all-or-nothing: one surprise in the
        file's shape returned ([], -1), the journal opened blank, and the
        close-time flush (_on_destroy -> _persist) wrote that blankness straight
        over the diary. Opening the app and pressing Esc was enough to lose
        every entry the user had ever written, and a journal is the one file
        here that cannot be re-derived from anything. So a malformed record now
        costs ITSELF and nothing else, and anything that still plausibly holds
        entries is read as entries."""
        self._quarantine_pending = False
        try:
            with open(JOURNAL_FILE) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return [], -1        # first run: nothing to protect
        except Exception:
            # Unreadable bytes. nbapp.atomic_write_json moves the original
            # aside (preserve_damaged) immediately before its next replacing
            # write, so the diary's bytes land in journal.json.damaged-* and
            # persistence KEEPS WORKING. The earlier cure — suppressing every
            # write for the session — protected the bytes but left a journal
            # that silently never saved again, which is its own lie.
            return [], -1
        entries, active = self._parse_entries(data)
        # Top-level keys a NEWER build may have added are carried through the
        # save untouched (accounting's _extra idiom): rebuilding the file from
        # only the keys this build knows silently deletes the rest.
        self._extra = ({k: v for k, v in data.items()
                        if k not in ("entries", "active")}
                       if isinstance(data, dict) else {})
        if not entries and data and not (isinstance(data, dict)
                                         and data.get("entries") == []):
            # Parsed fine, but nothing in it reads as a journal — a shape
            # only this app can judge, because valid JSON of the wrong shape
            # sails straight through nbapp's parse check. _persist moves the
            # file aside immediately before the first replacing write — the
            # same moment nbapp picks for the files IT can detect — so there
            # is never a window in which the journal has no file at all. An
            # empty dict or an empty entries list is OUR OWN empty journal,
            # not somebody's data in a foreign shape.
            self._quarantine_pending = True
        return entries, active

    def _parse_entries(self, data):
        """The forgiving read itself, split out so _load_entries can tell
        "nothing was in there" from "nothing came back"."""
        active = data.get("active", -1) if isinstance(data, dict) else -1
        if isinstance(data, dict):
            raw = data.get("entries")
            if raw is None:
                # The wrapper key is gone or was written under another name.
                # The entries are still in there; take the first list of
                # records rather than opening blank and saving that over them.
                for v in data.values():
                    if isinstance(v, list) and any(isinstance(x, dict)
                                                   for x in v):
                        raw = v
                        break
        else:
            # A bare list IS the entry list (an older / hand-repaired file, or
            # one written by a tool that dropped the wrapper). Rejecting it
            # threw the whole journal away over a missing pair of braces.
            raw = data
        # Entries stored as an object keyed by date or id: the values are still
        # the user's writing, taken in file order.
        if isinstance(raw, dict):
            raw = list(raw.values())
        if not isinstance(raw, list):
            return [], -1
        entries = []
        for en in raw:
            if not isinstance(en, dict):
                continue
            # A null field is empty text, not the four characters "None".
            e = {}
            for k in ENTRY_KEYS:
                v = en.get(k, "")
                e[k] = "" if v is None else str(v)
            # formatting spans persist alongside the plain text; older
            # files lack this key and load as unformatted (empty list)
            tags = en.get("tags")
            e["tags"] = tags if isinstance(tags, list) else []
            entries.append(e)
        if not isinstance(active, int) or isinstance(active, bool) \
                or not (0 <= active < len(entries)):
            active = 0 if entries else -1
        return entries, active

    def _quarantine(self):
        """Move a journal file this app could not read AS A JOURNAL aside,
        under the same <name>.damaged-<stamp> name nbapp.preserve_damaged
        uses. nbapp quarantines a store that fails to PARSE on every write;
        it deliberately cannot cover this case — valid JSON of the wrong
        shape parses perfectly, and only this app knows the shape is not a
        journal. Without this, the next flush would write an empty journal
        straight over whatever the file really held."""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = "%s.damaged-%s" % (JOURNAL_FILE, stamp)
            n = 2
            while os.path.exists(dest):
                dest = "%s.damaged-%s-%d" % (JOURNAL_FILE, stamp, n)
                n += 1
            os.replace(JOURNAL_FILE, dest)
        except OSError:
            pass

    def _persist(self):
        """Write the full entries model + active index to disk. Swallows I/O
        errors so a bad write never crashes the app; returns True only when the
        write actually succeeded (used to gate the 'Saved' indicator)."""
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            if getattr(self, "_quarantine_pending", False):
                self._quarantine()
                self._quarantine_pending = False
            payload = dict(getattr(self, "_extra", None) or {})
            payload.update({"entries": self.entries, "active": self.active})
            _atomic_write_json(JOURNAL_FILE, payload)
            self._save_warned = False
            return True
        except Exception as exc:
            # See academics._save_to_disk: a silent failed write is
            # indistinguishable from the app eating the diary.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash(nbapp.save_failure_reason(exc, JOURNAL_FILE))
                except Exception:
                    pass
            return False

    # ---------------- undo / redo ----------------
    # The whole journal is the undo unit, not the open buffer: deleting an
    # entry removes it from the list entirely, and a select-all-then-type
    # replaces a day's writing — both have to be reversible, and both change
    # more than one TextBuffer's worth of state. See nbapp.UndoHistory.
    def _undo_snapshot(self):
        """The journal as it stands, with the live buffer folded in."""
        self._save_current()
        return {"entries": self._copy_entries(self.entries),
                "active": self.active,
                "_caret": self.body.get_buffer().get_iter_at_mark(
                    self.body.get_buffer().get_insert()).get_offset()}

    @staticmethod
    def _copy_entries(entries):
        """Fresh dicts (and a fresh tag list) per entry, so a snapshot can never
        be edited from under itself by the next _save_current. The strings
        inside are immutable and shared, which is what keeps a long journal's
        history small."""
        return [dict(en, tags=list(en.get("tags") or [])) for en in entries]

    def _undo_restore(self, state):
        self.entries = self._copy_entries(state["entries"])
        self.active = state["active"]
        self._clear_search()          # a filter can hide the entry we restored
        self._refresh_list()
        self._load_active()
        buf = self.body.get_buffer()
        caret = min(max(0, state.get("_caret", 0)), buf.get_char_count())
        buf.place_cursor(buf.get_iter_at_offset(caret))
        self._persist()
        if 0 <= self.active < len(self.entries):
            self.body.grab_focus()

    def _on_destroy(self, *_):
        """Flush the in-progress buffer to disk when the window closes.

        Idempotent, and closes the gate FIRST: "destroy" can arrive more than
        once, and a timeout that was already dispatched before its
        source_remove() will still run its sink. Setting _closed before
        anything else means those in-flight sinks find a closed owner and do
        nothing, so the final flush below is the last write -- no rebuilt rows,
        no second persist, no "Saved" chip painted after the app is gone."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.undo.cancel()
        for attr in ("_save_timer", "_count_timer", "_filter_timer"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self._save_current()
        except Exception:
            pass
        self._persist()

    # ---------------- sidebar ----------------
    def _sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.set_size_request(320, -1)
        side.get_style_context().add_class("side")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("sidehead")
        toprow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        kick = Gtk.Label(label=_t("JOURNAL"), xalign=0)
        kick.get_style_context().add_class("kicker")
        year = Gtk.Label(label=time.strftime("%Y"), xalign=0)
        year.get_style_context().add_class("yearlabel")
        titles.pack_start(kick, False, False, 0)
        titles.pack_start(year, False, False, 0)
        toprow.pack_start(titles, True, True, 0)

        plus = Gtk.Button()
        plus.set_relief(Gtk.ReliefStyle.NONE)
        plus.get_style_context().add_class("newbtn")
        plus.set_tooltip_text(_t("New Entry"))
        plus.add(nbicons.image("plus", 17, "#2A2620"))
        plus.connect("clicked", lambda *_: self.new_entry())
        toprow.pack_end(plus, False, False, 0)
        head.pack_start(toprow, False, False, 0)

        # Search. A journal fills up: after a term of daily writing the sidebar
        # is hundreds of rows deep and the only way back to the entry about the
        # hospital appointment was to scroll and read every one. This filters the
        # list by title AND body text, so the words you remember writing find the
        # day you wrote them. Hidden until there is something to search.
        self.search = Gtk.SearchEntry()
        nbicons.style_search_entry(self.search)
        self.search.set_placeholder_text(_t("Search entries"))
        self.search.get_style_context().add_class("jsearch")
        self.search.set_no_show_all(True)      # driven by hand (see _refresh_list)
        self.search.connect("search-changed", self._on_search)
        self.search.connect("activate", lambda *_: self._first_match())
        self._query = ""
        self._filter_timer = None
        head.pack_start(self.search, False, False, 0)
        side.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("sidescroll")
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_box.get_style_context().add_class("listbox")
        scroll.add(self.list_box)
        side.pack_start(scroll, True, True, 0)
        return side

    def _refresh_list(self):
        # rebuilding tears down the old rows, so drop their (now dead) label
        # handles; _entry_row re-captures them for the freshly built active row
        self._active_title_lbl = None
        self._active_preview_lbl = None
        for c in self.list_box.get_children():
            self.list_box.remove(c)

        # The search field only makes sense once there is something to search.
        self.search.set_visible(bool(self.entries))

        if not self.entries:
            # A bare "No entries" states the obvious and leaves the reader
            # stuck. Name the next move, in the words of the button that does
            # it (the canvas beside this list is showing "Start today's entry"),
            # and say where the result will turn up.
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            empty.get_style_context().add_class("sideempty")
            head = Gtk.Label(label=_t("No entries"), xalign=0)
            head.get_style_context().add_class("sideemptyhead")
            hint = Gtk.Label(
                label=_t("Entries are listed here by date."),
                xalign=0)
            hint.set_line_wrap(True)
            hint.set_max_width_chars(24)
            empty.pack_start(head, False, False, 0)
            empty.pack_start(hint, False, False, 0)
            self.list_box.pack_start(empty, False, False, 0)
            self.list_box.show_all()
            return

        rows = self._matches()
        if not rows:
            # A search that matches nothing hides every entry the writer has,
            # which reads like the journal emptied itself. Name the state, then
            # give her the one press that brings them all back — the same
            # wording the View menu uses for the same action, so learning one
            # teaches the other.
            # The padding + type style move to the BOX so both children sit
            # inside one inset; the label inherits colour and size from it.
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.get_style_context().add_class("sideempty")
            empty = Gtk.Label(label=_t("No entry matches “%s”") % self._query,
                              xalign=0)
            empty.set_line_wrap(True)
            empty.set_max_width_chars(24)
            box.pack_start(empty, False, False, 0)
            back = Gtk.Button(label=_t("Show All Entries"))
            back.set_relief(Gtk.ReliefStyle.NONE)
            back.set_halign(Gtk.Align.START)
            back.get_style_context().add_class("sideemptybtn")
            back.connect("clicked", lambda *_: self._clear_search())
            box.pack_start(back, False, False, 0)
            self.list_box.pack_start(box, False, False, 0)
            self.list_box.show_all()
            return

        if self._query:
            n = len(rows)
            cnt = Gtk.Label(label=(_t("1 entry found") if n == 1
                                   else _t("%d entries found") % n), xalign=0)
            cnt.get_style_context().add_class("searchcount")
            self.list_box.pack_start(cnt, False, False, 0)

        last_label = None
        for i, en in rows:
            if en["month_label"] != last_label:
                last_label = en["month_label"]
                gl = Gtk.Label(label=_t(en["month_label"]).upper(), xalign=0)
                gl.get_style_context().add_class("monthlabel")
                self.list_box.pack_start(gl, False, False, 0)
            self.list_box.pack_start(self._entry_row(i, en), False, False, 0)
        self.list_box.show_all()

    def _matches(self):
        """(index, entry) pairs to show: every entry, or those whose date,
        title or body text contains the search text."""
        if not self._query:
            return list(enumerate(self.entries))
        q = self._query.lower()
        out = []
        for i, en in enumerate(self.entries):
            hay = "%s %s %s %s" % (en.get("date", ""), en.get("month_label", ""),
                                   en.get("title", ""), en.get("text", ""))
            if q in hay.lower():
                out.append((i, en))
        return out

    def _on_search(self, _entry):
        """Filter the entries list as the search text is typed. Debounced: on a
        long journal rebuilding every row per keystroke is visible work."""
        if getattr(self, "_closed", False):
            return
        if self._filter_timer:
            GLib.source_remove(self._filter_timer)
        self._filter_timer = GLib.timeout_add(120, self._filter_tick)

    def _filter_tick(self):
        self._filter_timer = None
        if getattr(self, "_closed", False):
            return False
        # Read the field HERE rather than in the signal handler, so the filter
        # is whatever is actually in the box at the moment it is applied.
        self._query = self.search.get_text().strip()
        # The live buffer holds edits the model hasn't seen yet; flush them so
        # searching finds words typed moments ago.
        self._save_current()
        self._refresh_list()
        return False

    def _first_match(self):
        """Enter in the search field opens the first entry that matched."""
        if self._filter_timer:
            GLib.source_remove(self._filter_timer)
        self._filter_tick()
        rows = self._matches()
        if rows and rows[0][0] != self.active:
            self.select_entry(rows[0][0])
        self.body.grab_focus()

    def _focus_search(self):
        """Ctrl+F / View ▸ Find an Entry — put the caret in the search field."""
        if self.entries:
            self.search.grab_focus()

    def _clear_search(self):
        """Drop the filter and show every entry again."""
        if not self._query and not self.search.get_text():
            return False
        self.search.set_text("")
        self._query = ""
        self._refresh_list()
        return True

    def _entry_row(self, i, en):
        # A Button, not an EventBox: an EventBox is not focusable and answers to
        # nothing but the pointer, so the entries list — the only way to reach a
        # past entry — could not be operated from the keyboard at all. A button
        # is in the Tab ring, activates on Space/Enter, and reports itself to
        # assistive tech as the control it is. It also still carries the hover
        # tint the EventBox was introduced for: only the widget the pointer is
        # actually over gets GTK's PRELIGHT flag, and GTK3 does not propagate it
        # to children, so a :hover rule on the inner box never fires. The box
        # sits flush inside the button, so the tint lands in the same place; the
        # selected row paints its own fill on top of it.
        ev = Gtk.Button()
        ev.set_relief(Gtk.ReliefStyle.NONE)
        ev.get_style_context().add_class("entryrowhit")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        ctx = row.get_style_context()
        ctx.add_class("entryrow")
        if i == self.active:
            ctx.add_class("active")
        ev.add(row)

        datebox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        datebox.set_size_request(34, -1)
        dctx = datebox.get_style_context()
        dctx.add_class("datebox")
        if i == self.active:
            dctx.add_class("active")
        day = Gtk.Label(label=en["day"], xalign=0)
        day.get_style_context().add_class("dbday")
        wd = Gtk.Label(label=en["wd"].upper(), xalign=0)
        wd.get_style_context().add_class("dbwd")
        datebox.set_valign(Gtk.Align.START)
        datebox.pack_start(day, False, False, 0)
        datebox.pack_start(wd, False, False, 0)
        row.pack_start(datebox, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        meta.set_valign(Gtk.Align.START)
        t = Gtk.Label(label=en["title"] or "Untitled entry", xalign=0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.get_style_context().add_class("entrytitle")
        p = Gtk.Label(label=en["preview"] or "—", xalign=0)
        p.set_ellipsize(Pango.EllipsizeMode.END)
        p.get_style_context().add_class("entrypreview")
        meta.pack_start(t, False, False, 0)
        meta.pack_start(p, False, False, 0)
        row.pack_start(meta, True, True, 0)
        # remember the active row's title/preview labels for in-place autosave
        # refresh (see _sync_active_row); month/date labels never change on edit
        if i == self.active:
            self._active_title_lbl = t
            self._active_preview_lbl = p

        # "clicked" covers the pointer AND Space/Enter in one signal, so the
        # keyboard path cannot drift away from the mouse path. `i` is bound per
        # call, so each row opens its own entry.
        ev.connect("clicked", lambda *_: self.select_entry(i))
        return ev

    # ---------------- editor ----------------
    def _editor(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_hexpand(True)
        col.get_style_context().add_class("editorcol")

        fbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        fbar.get_style_context().add_class("formatbar")

        # format controls; refs kept in _fmt_btns so the empty state can grey
        # them out — there is nothing to format when no entry is open
        self._fmt_btns = []

        b = Gtk.Button(label=_t("B"))
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("fmtbtn")
        b.get_style_context().add_class("bold")
        b.set_tooltip_text(_t("Bold (Ctrl+B)"))
        b.connect("clicked", lambda *_: self._toggle_tag("bold"))
        fbar.pack_start(b, False, False, 0)
        self._fmt_btns.append(b)

        it = Gtk.Button(label=_t("I"))
        it.set_relief(Gtk.ReliefStyle.NONE)
        it.get_style_context().add_class("fmtbtn")
        it.get_style_context().add_class("ital")
        it.set_tooltip_text(_t("Italic (Ctrl+I)"))
        it.connect("clicked", lambda *_: self._toggle_tag("italic"))
        fbar.pack_start(it, False, False, 0)
        self._fmt_btns.append(it)

        fbar.pack_start(self._sep(), False, False, 10)
        qb = self._iconbtn("quote", lambda: self._toggle_tag("quote"))
        qb.set_tooltip_text(_t("Quote"))
        fbar.pack_start(qb, False, False, 0)
        self._fmt_btns.append(qb)
        bb = self._iconbtn("bullet", self._bullet)
        bb.set_tooltip_text(_t("Bullet"))
        fbar.pack_start(bb, False, False, 0)
        self._fmt_btns.append(bb)

        self.save = Gtk.Label()
        self.save.get_style_context().add_class("savestate")
        self.save.set_markup('<span foreground="#7FA98C">● </span>'
                             'Saved %s' % time.strftime("%H:%M"))
        fbar.pack_end(self.save, False, False, 0)
        # divider between word count and save chip; hidden with them in the
        # empty state so no orphaned hairline floats at the bar's right edge
        self._count_sep = self._sep()
        self._count_sep.set_no_show_all(True)
        fbar.pack_end(self._count_sep, False, False, 18)
        self.count = Gtk.Label(label=_t("0 words"))
        self.count.get_style_context().add_class("wordcount")
        fbar.pack_end(self.count, False, False, 0)
        col.pack_start(fbar, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("canvaswrap")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_halign(Gtk.Align.CENTER)
        # The writing column wants PAGE_MAX, but it must be able to SHRINK: a
        # hard 720 beside the 320px entries sidebar made the window's minimum
        # 1060px — wider than a 1024x768 panel, which put the right edge of the
        # canvas (and the save state) permanently off-screen, since a window can
        # never be smaller than its minimum. _fit_page widens it back to the
        # full measure whenever the canvas has room.
        page.set_size_request(PAGE_MIN, -1)
        page.get_style_context().add_class("page")
        self.page = page

        self.date_lbl = Gtk.Label(xalign=0)
        self.date_lbl.set_line_wrap(True)
        self.date_lbl.get_style_context().add_class("bigdate")
        page.pack_start(self.date_lbl, False, False, 0)
        self.meta_lbl = Gtk.Label(xalign=0)
        self.meta_lbl.get_style_context().add_class("metaline")
        # `meta` is app-generated and short in normal use, but the loader
        # deliberately survives a hand-edited or foreign journal.json — and it
        # validates TYPES, not LENGTHS. An unbounded single-line label makes its
        # whole string the page's minimum width, so a ~500-character meta drove
        # the window's minimum to 3208px: the sidebar and two thirds of the page
        # ended up permanently off a 1024px panel. Ellipsize + a capped measure
        # means the line can never widen the app (the full value still round-
        # trips through the model and the file — only the DISPLAY is clipped).
        self.meta_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta_lbl.set_max_width_chars(60)
        page.pack_start(self.meta_lbl, False, False, 0)
        # Both carry big type and big margins even when empty, which pushed the
        # "No entry open" panel a third of the way down an otherwise blank
        # canvas. Marked visible once, then driven by hand from _load_active
        # (set_no_show_all keeps a later show_all from re-revealing them —
        # the same guard empty_box and the editor already needed).
        for lbl in (self.date_lbl, self.meta_lbl):
            lbl.show()
            lbl.set_no_show_all(True)

        self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.empty_box.get_style_context().add_class("emptybox")
        e1 = Gtk.Label(label=_t("No entries"), xalign=0)
        e1.get_style_context().add_class("emptyhead")
        # The one action that matters, on the screen the writer is looking at —
        # the + that used to be the only way in sits in the far corner of the
        # other pane, so the blank canvas offered nothing to press.
        e3 = Gtk.Button(label=_t("Start today's entry"))
        e3.set_relief(Gtk.ReliefStyle.NONE)
        e3.set_halign(Gtk.Align.START)
        e3.get_style_context().add_class("emptybtn")
        e3.connect("clicked", lambda *_: self.new_entry())
        self.empty_box.pack_start(e1, False, False, 0)
        self.empty_box.pack_start(e3, False, False, 0)
        page.pack_start(self.empty_box, False, False, 0)
        # Mark the labels visible ONCE, then take the box out of show_all()'s
        # reach and drive it by hand (show()/hide()) from _load_active.
        # Without this, restoring an autosaved entry drew the "No entry open"
        # panel ON TOP of the restored text: _load_active hid the box during
        # construction, and nbapp.run()'s later show_all() dutifully un-hid it
        # again. self.body already had this guard; empty_box was missed.
        e1.show()
        e3.show()
        self.empty_box.set_no_show_all(True)
        self.empty_box.hide()

        self.body = Gtk.TextView()
        # empty state hides the editor (see _load_active); without this,
        # nbapp.run()'s show_all() would re-reveal it as a stray blank canvas.
        self.body.set_no_show_all(True)
        # WORD_CHAR, not WORD: a word longer than the column (a pasted URL, a
        # long compound) cannot break under WORD, so it runs off the page AND
        # grows the TextView's minimum width, dragging the window wider than
        # the screen.
        self.body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.body.get_style_context().add_class("docbody")
        self.body.set_pixels_below_lines(20)
        self.body.set_pixels_inside_wrap(10)
        self.body.set_size_request(PAGE_MIN, 400)
        buf = self.body.get_buffer()
        buf.create_tag("bold", weight=Pango.Weight.BOLD)
        buf.create_tag("italic", style=Pango.Style.ITALIC)
        buf.create_tag("quote", left_margin=24, style=Pango.Style.ITALIC,
                       foreground="#6E695E")
        buf.connect("changed", self._on_change)
        buf.connect("insert-text", self._on_insert_before)
        page.pack_start(self.body, True, True, 0)

        scroll.add(page)
        # Track the viewport (not the scroller — its width includes the
        # scrollbar) so the column always matches the space actually available.
        vp = scroll.get_child()
        if vp is not None:
            vp.connect("size-allocate", self._fit_page)
        col.pack_start(scroll, True, True, 0)
        return col

    def _fit_page(self, _w, alloc):
        """Size the writing column to the canvas: the design's measure where it
        fits, the canvas width where it does not. Writes only when the value
        actually changes, so it cannot loop with the resize it triggers."""
        w = max(PAGE_MIN, min(PAGE_MAX, alloc.width - PAGE_PAD))
        if w == getattr(self, "_page_w", None):
            return
        self._page_w = w
        self.page.set_size_request(w, -1)
        self.body.set_size_request(w, 400)

    def _sep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("fsep")
        return s

    def _iconbtn(self, name, cb):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("fmtbtn")
        b.add(nbicons.image(name, 19, "#2A2620"))
        b.connect("clicked", lambda *_: cb())
        return b

    # ---------------- behavior ----------------
    def _new_entry_dict(self, text=""):
        """Build a fresh entry dict dated today, its title/preview derived from
        `text`. Uses the app's static weekday/month maps only — never
        time.strptime / import calendar, which would crash the app on launch."""
        n = time.localtime()
        title, preview = self._derive(text)
        return {
            "day": str(n.tm_mday),
            "wd": WD_SHORT[(n.tm_wday + 1) % 7],
            "month_label": "%s %d" % (MONTHS[n.tm_mon - 1], n.tm_year),
            "date": "%s, %d %s" % (WD_LONG[(n.tm_wday + 1) % 7], n.tm_mday,
                                   MONTHS[n.tm_mon - 1]),
            "meta": META_PREFIX + time.strftime("%H:%M"),
            "title": title, "preview": preview, "text": text,
            "tags": [],
        }

    def new_entry(self):
        self._save_current()
        self.undo.checkpoint("New Entry")
        # A fresh entry is blank, so it cannot match an active search — clear the
        # filter first or the row you are about to type into is not in the list.
        self._clear_search()
        self.entries.insert(0, self._new_entry_dict())
        self.active = 0
        self._refresh_list()
        self._load_active()
        self._persist()          # structural change: persist the new entry
        self.undo.commit()
        self.body.grab_focus()

    def select_entry(self, i):
        if i == self.active:
            return
        self._save_current()
        self.active = i
        self._refresh_list()
        self._load_active()
        self._persist()          # persist buffered edit + new active index

    def _load_active(self):
        self._loading = True
        buf = self.body.get_buffer()
        have = 0 <= self.active < len(self.entries)
        if have:
            en = self.entries[self.active]
            self.date_lbl.set_text(en["date"])
            self.meta_lbl.set_text(self._meta_display(en["meta"]))
            self.date_lbl.show()
            self.meta_lbl.show()
            self.empty_box.hide()
            self.body.show()
            buf.set_text(en["text"])
            # restore character/paragraph formatting saved for this entry
            self._apply_tags(buf, en.get("tags"))
        else:
            self.date_lbl.set_text("")
            self.meta_lbl.set_text("")
            # hidden, not just blanked: an empty 44px date line and its 46px
            # margin would otherwise strand the empty-state panel in mid-air
            self.date_lbl.hide()
            self.meta_lbl.hide()
            self.empty_box.show()
            self.body.hide()
            buf.set_text("")
        self._loading = False
        # the format bar, word count and save chip only make sense with an entry
        # open; keep the empty state honest instead of showing "0 words / Saved"
        self._set_fmt_enabled(have)
        self._mark_saved(have)
        self._recount()

    def _set_fmt_enabled(self, on):
        """Enable the format-bar controls only when an entry is open (the editor
        is hidden and there is nothing to format in the empty state)."""
        for btn in getattr(self, "_fmt_btns", []):
            try:
                btn.set_sensitive(on)
            except Exception:
                pass

    def _mark_saved(self, have):
        """Reflect the on-disk truth in the save chip: an open entry is already
        persisted ('Saved'); the empty state has nothing to save (blank)."""
        try:
            if have:
                self.save.set_markup('<span foreground="#7FA98C">● </span>Saved')
            else:
                self.save.set_text("")
            sep = getattr(self, "_count_sep", None)
            if sep is not None:
                sep.set_visible(have)
        except Exception:
            pass

    def _save_current(self):
        if 0 <= self.active < len(self.entries):
            buf = self.body.get_buffer()
            txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            en = self.entries[self.active]
            en["text"] = txt
            # keep the formatting alive across entry-switch/restart: plain text
            # alone would silently drop every bold/italic/quote span
            en["tags"] = self._serialize_tags(buf)
            en["title"], en["preview"] = self._derive(txt)

    @staticmethod
    def _meta_display(meta):
        """A stored meta line as the reader should see it (see META_PREFIX).
        Anything that is not the app's own generated form — a hand-edited or
        foreign journal.json — is shown exactly as it is stored."""
        if isinstance(meta, str) and meta.startswith(META_PREFIX):
            return _t(META_PREFIX) + meta[len(META_PREFIX):]
        return meta or ""

    def _derive(self, txt):
        """Derive (title, preview) from an entry's plain text. The title is the
        first line; the preview is the body AFTER that line so it doesn't just
        repeat the title (single-line entries have no body -> empty preview)."""
        parts = txt.strip().split("\n", 1)
        title = parts[0].strip()[:60]
        rest = parts[1].strip() if len(parts) > 1 else ""
        preview = rest.replace("\n", " ")[:80]
        return title, preview

    def _serialize_tags(self, buf):
        """Capture the buffer's formatting as a list of {start,end,tag} char
        spans (offsets into the plain text) so it can round-trip through
        journal.json. Only the app's own named tags are recorded."""
        spans = []
        table = buf.get_tag_table()
        for name in ("bold", "italic", "quote"):
            tag = table.lookup(name)
            if tag is None:
                continue
            it = buf.get_start_iter()
            # a toggle located exactly at the start iter is not reported by
            # forward_to_tag_toggle, so seed the open offset here
            open_off = 0 if it.has_tag(tag) else None
            while it.forward_to_tag_toggle(tag):
                off = it.get_offset()
                if it.has_tag(tag):
                    open_off = off
                elif open_off is not None:
                    spans.append({"start": open_off, "end": off, "tag": name})
                    open_off = None
            if open_off is not None:
                spans.append({"start": open_off,
                              "end": buf.get_end_iter().get_offset(),
                              "tag": name})
        return spans

    def _apply_tags(self, buf, spans):
        """Re-apply serialized {start,end,tag} spans onto buf. Defensive about
        missing/older data (spans may be None) and clamps offsets to the text
        so a stale span can never raise."""
        if not isinstance(spans, list):
            return
        table = buf.get_tag_table()
        n = buf.get_char_count()
        for sp in spans:
            try:
                tag = table.lookup(sp.get("tag"))
                if tag is None:
                    continue
                s = max(0, min(int(sp.get("start")), n))
                e = max(0, min(int(sp.get("end")), n))
                if e > s:
                    buf.apply_tag(tag, buf.get_iter_at_offset(s),
                                  buf.get_iter_at_offset(e))
            except Exception:
                continue

    def _on_insert_before(self, buf, it, text, length):
        """Turn typewriter marks into real typography as they are typed: " and '
        become the matching curly quotes, -- becomes an em dash. Only ever fires
        for a single typed character, so a pasted passage is left exactly as it
        is, and so is every entry restored from disk."""
        if self._loading or self._smart_busy:
            return
        prev = ""
        probe = it.copy()
        if probe.backward_char():
            prev = probe.get_char()
        repl = nbapp.smart_replacement(prev, text)
        if repl is None:
            return
        self._smart_busy = True
        try:
            buf.stop_emission_by_name("insert-text")
            if text == "-":               # swallow the first hyphen of the pair
                buf.delete(probe, it)
                it = buf.get_iter_at_mark(buf.get_insert())
            buf.insert(it, repl)
        finally:
            self._smart_busy = False

    def _on_change(self, buf):
        if self._loading or getattr(self, "_closed", False):
            return
        # Keep the word recount OFF the keystroke hot path: recomputing the
        # whole-buffer count synchronously per keypress makes each keystroke
        # cost scale with entry length. Coalesce it onto a short idle timer so
        # it runs at most once per ~150ms of typing yet still settles on the
        # final count once the burst ends. Scheduled only when nothing is
        # already pending so continuous typing can't pile up timers.
        if self._count_timer is None:
            self._count_timer = GLib.timeout_add(150, self._recount_tick)
        # One undo step per burst of typing: this only re-arms a timer, so it
        # adds nothing measurable to the keystroke path (see nbapp.UndoHistory).
        self.undo.touch()
        self.save.set_markup('<span foreground="#C8341E">● </span>'
                             'Saving…')
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(900, self._did_save)

    def _recount_tick(self):
        """Debounce sink for the live word count; reads the current buffer."""
        self._count_timer = None
        if getattr(self, "_closed", False):
            return False
        self._recount()
        return False

    def _did_save(self):
        self._save_timer = None
        # An autosave dispatched just before close must not write again after
        # _on_destroy's final flush, nor repaint "Saved" over a closed window.
        if getattr(self, "_closed", False):
            return False
        self._save_current()
        # only report "Saved" once the bytes are actually on disk. If the entry
        # was deleted while this autosave was pending (empty state now), keep the
        # chip blank rather than repainting a dishonest "Saved" over nothing.
        if self._persist():
            if 0 <= self.active < len(self.entries):
                self.save.set_markup('<span foreground="#7FA98C">● </span>'
                                     'Saved %s' % time.strftime("%H:%M"))
            else:
                self.save.set_text("")
        # update sidebar preview/title live — only the active row's two labels,
        # in place. A full _refresh_list() here (every ~900ms of typing) would
        # tear down/rebuild the whole sidebar and reset its scroll. Structural
        # changes (new/select/delete) still go through _refresh_list.
        self._sync_active_row()
        return False

    def _sync_active_row(self):
        """Update just the active entry's sidebar title/preview labels in place.
        On a text edit only title/preview change (the date/weekday/month labels
        are fixed at entry creation), so nothing structural moves. Writes each
        label only when its text actually changed; falls back to a full rebuild
        if the cached handles are missing (e.g. active row not currently built)."""
        if not (0 <= self.active < len(self.entries)):
            return
        if self._active_title_lbl is None or self._active_preview_lbl is None:
            self._refresh_list()
            return
        en = self.entries[self.active]
        title = en["title"] or "Untitled entry"
        preview = en["preview"] or "—"
        if self._active_title_lbl.get_text() != title:
            self._active_title_lbl.set_text(title)
        if self._active_preview_lbl.get_text() != preview:
            self._active_preview_lbl.set_text(preview)

    def _recount(self):
        if not (0 <= self.active < len(self.entries)):
            self.count.set_text("")
            return
        buf = self.body.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        n = len(txt.split())
        self.count.set_text("%s word%s" % (format(n, ","), "" if n == 1 else "s"))

    def _toggle_tag(self, name):
        # nothing to format when no entry is open (the editor is hidden)
        if not (0 <= self.active < len(self.entries)):
            return
        buf = self.body.get_buffer()
        bounds = buf.get_selection_bounds()
        if not bounds:
            self.body.grab_focus()
            return
        start, end = bounds
        tag = buf.get_tag_table().lookup(name)
        # determine if fully tagged already
        it = start.copy()
        fully = True
        while it.compare(end) < 0:
            if not it.has_tag(tag):
                fully = False
                break
            it.forward_char()
        self.undo.checkpoint("Formatting")
        if fully:
            buf.remove_tag(tag, start, end)
        else:
            buf.apply_tag(tag, start, end)
        # A tag change emits no "changed" signal, so nothing here would arm the
        # autosave or the undo checkpoint: the formatting sat unwritten until
        # the next text edit, and the chip claimed "Saved" while it was.
        self._on_change(buf)
        self.undo.commit()

    def _bullet(self):
        # _insert_at_cursor no-ops (and refocuses) when no entry is open, so the
        # toolbar/menu bullet can never leak text into the hidden buffer
        self._insert_at_cursor("• ", "Bullet")

    def _on_key(self, w, ev):
        # Esc drops an active search before it reaches the base handler (which
        # would close the whole app) — the escape a filtered list needs.
        if ev.keyval == Gdk.KEY_Escape and self._clear_search():
            self.body.grab_focus()
            return True
        # Ctrl+F puts the caret in the search field wherever focus happens to be.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F)):
            self._focus_search()
            return True
        # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y. Handled at the window level so they
        # work from the sidebar and the search field too, not only the editor.
        if nbapp.undo_keys(self.undo, ev):
            return True
        # Ctrl+B / Ctrl+I inline formatting — the shortcuts a writer reaches for
        # and the ones the toolbar tooltips promise. They apply to the selection
        # (a no-op without one) and only when an entry is open. Intercepted at
        # the window level so they never fall through to the TextView; the Esc /
        # menu handling stays with the base. The delete confirm is a separate
        # modal window, so its keys are unaffected.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and 0 <= self.active < len(self.entries)):
            if ev.keyval in (Gdk.KEY_b, Gdk.KEY_B):
                self._toggle_tag("bold")
                return True
            if ev.keyval in (Gdk.KEY_i, Gdk.KEY_I):
                self._toggle_tag("italic")
                return True
        return super()._on_key(w, ev)

    # ---------------- File menu: Export to PDF under $NB_HOME/Documents -------
    # No file open/save. journal.json is the session-recovery autosave (the
    # source of truth); Export renders the entries as a read-only paginated PDF
    # into Documents, drawn with the same serif body + ink palette as the canvas.
    def _pdf_name(self):
        """A neutral PDF filename for the whole journal export."""
        return "journal-" + time.strftime("%Y-%m-%d") + ".pdf"

    def _make_pdf(self, path):
        """Write the journal PDF to `path` — the single renderer shared by both
        File ▸ Export to PDF and File ▸ Print. Flushes the live buffer into the
        active entry first so the output reflects the on-screen text, not the
        last debounced snapshot, then draws every entry via _render_pdf."""
        self._save_current()
        self._render_pdf(path)

    def _print(self, *_a):
        """Print the whole journal via the shared themed Print dialog, using the
        SAME renderer as Export to PDF. The no-printer case is handled inside
        nbprint (it points the writer at Export to PDF instead)."""
        if not self.entries:
            self._flash("No entries to print")
            return
        try:
            nbprint.print_document(self, self._make_pdf, job_name="Journal")
        except Exception:
            self._flash("Print unavailable")

    def _export_pdf(self, *_a):
        """Render every entry (date + title + body) to a paginated PDF under
        Documents. Reports a neutral status line in the save indicator; never
        crashes on a bad path/write."""
        if not self.entries:
            self._flash("No entries to export")
            return
        name = self._pdf_name()
        # The name is journal-<today>.pdf, so a second export on the same day
        # lands on the first one. It used to destroy it without a word. Ask, using the same three strings as
        # Novel's Save As -- one wording for "you are about to overwrite",
        # already carried by all seventeen catalogs.
        if os.path.exists(os.path.join(DOCS_DIR, name)):
            self._confirm(
                _t("Replace file?"),
                _t("“%s” already exists in Documents. Replace it?")
                % name,
                _t("Replace"), lambda: self._write_export_pdf(name))
            return
        self._write_export_pdf(name)

    def _write_export_pdf(self, name):
        """Render the journal to Documents/`name`. Split from _export_pdf so the
        replace-an-existing-file question can be answered before anything is
        written."""
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            # _make_pdf flushes the live buffer + formatting into the active
            # entry so the export reflects the on-screen text.
            self._make_pdf(os.path.join(DOCS_DIR, name))
        except Exception:
            self._flash("Export failed")
            return
        # Success: the autosave state is unchanged, so only update the label.
        # Name the destination folder so the file is findable in the Finder.
        try:
            self.save.set_markup('<span foreground="#7FA98C">● </span>'
                                 'Exported to Documents')
        except Exception:
            pass

    @staticmethod
    def _line_spans(tags, base, lo, length):
        """Inline runs for one body line, in nbprint.PdfText's
        (start, end, kind) shape.

        `tags` are the entry's saved {start,end,tag} spans, indexed into the
        WHOLE entry text; `base` is where the body begins in it and `lo` is the
        line's offset within the body. Returns (spans, whole_line_quote) — a
        line entirely inside a quote is drawn indented and italic, the way it
        looks on screen."""
        spans = []
        quoted = False
        abs_s = base + lo
        abs_e = abs_s + length
        for sp in tags or ():
            try:
                s = int(sp.get("start"))
                e = int(sp.get("end"))
                kind = sp.get("tag")
            except (TypeError, ValueError, AttributeError):
                continue
            if e <= abs_s or s >= abs_e:
                continue
            ls = max(0, s - abs_s)
            le = min(length, e - abs_s)
            if le <= ls:
                continue
            if kind == "quote":
                # A quote is an italic, indented block on screen. Whole-line is
                # the only case that means anything on paper.
                if ls == 0 and le == length:
                    quoted = True
                else:
                    spans.append((ls, le, "italic"))
            elif kind in ("bold", "italic"):
                spans.append((ls, le, kind))
        return spans, quoted

    def _render_pdf(self, path):
        """Draw every journal entry onto a cairo PDF at `path`, paginating when
        the cursor overflows the page. Each entry emits its long date, title and
        body text — with the bold, italic and quote formatting it was written
        with — and a hairline rule separates one entry from the next.

        Laid out with nbprint.PdfText (PangoCairo). The old cairo toy-font
        helpers printed empty boxes for every Chinese, Japanese, Korean and
        Devanagari character, and dropped all formatting: the tags were saved
        and restored on screen, then read past entirely on the way to paper."""
        surf, cr, page = nbprint.report_page(path)

        # Cover header, then each entry: date eyebrow, title, meta, body lines.
        page.emit(_t("JOURNAL"), 9.5, False, "#6E695E", gap_after=6)
        page.emit(time.strftime("%Y"), 26, True, "#1A1916", gap_after=3)
        page.rule()
        for idx, en in enumerate(self.entries):
            if idx:
                page.y += 8
                page.rule()
            page.emit(_t(en.get("date", "")).upper(), 9.5, False, "#6E695E",
                      gap_before=6, gap_after=4)
            page.emit(en.get("title", "") or _t("Untitled entry"), 22, True,
                      "#1A1916", gap_after=2)
            meta = self._meta_display(en.get("meta", ""))
            if meta:
                page.emit(meta, 10, False, "#9A9484", gap_after=6)
            body = en.get("text", "")
            # first line is the entry title (see _derive); render the remainder
            # as the body so the title is not repeated. Empty body -> nothing.
            parts = body.split("\n", 1)
            rest = parts[1] if len(parts) > 1 else ""
            base = len(parts[0]) + 1 if len(parts) > 1 else 0
            tags = en.get("tags") or []
            lo = 0
            for raw in rest.split("\n"):
                spans, quoted = self._line_spans(tags, base, lo, len(raw))
                page.emit(raw, 11, False, "#2A2620", italic=quoted,
                          indent=24.0 if quoted else 0.0, spans=spans)
                lo += len(raw) + 1

        surf.finish()

    def _flash(self, text):
        """Surface a transient status/error line in the save indicator
        (crash-safe; the next edit or successful save resets it)."""
        try:
            self.save.set_markup(
                '<span foreground="#C8341E">● </span>%s'
                % GLib.markup_escape_text(text))
        except Exception:
            pass

    # ---------------- insert ----------------
    def _insert_at_cursor(self, s, label=None):
        """Insert `s` into the body buffer at the cursor and refocus. No-op
        when no entry is open, since the editor is hidden in the empty state.
        `label` makes the insert its own undo step, named after the menu item
        that asked for it, instead of merging into the surrounding typing."""
        if not (0 <= self.active < len(self.entries)):
            return
        buf = self.body.get_buffer()
        self.undo.checkpoint(label)
        buf.insert_at_cursor(s)
        self.undo.commit()
        self.body.grab_focus()

    def _insert_date(self):
        """Insert today's long date (e.g. 'Saturday, 20 June 2026') at the
        cursor. Built from the app's static maps — never time.strptime /
        import calendar, which would crash the app on launch."""
        n = time.localtime()
        stamp = "%s, %d %s %d" % (WD_LONG[(n.tm_wday + 1) % 7], n.tm_mday,
                                  MONTHS[n.tm_mon - 1], n.tm_year)
        self._insert_at_cursor(stamp, "Insert Date")

    def _insert_time(self):
        """Insert the current wall-clock time (24h) at the cursor."""
        self._insert_at_cursor(time.strftime("%H:%M"), "Insert Time")

    def _insert_divider(self):
        """Insert a manuscript section divider on its own line."""
        self._insert_at_cursor("\n———\n", "Insert Divider")

    def _insert_bullets(self):
        """Insert a three-item bullet-list scaffold at the cursor."""
        self._insert_at_cursor("\n• \n• \n• \n", "Insert Bullet List")

    # ---------------- delete (undoable) ----------------
    def _delete_active(self):
        """Remove the current entry immediately and leave an Undo step.

        Destruction gets undo rather than a confirmation detour: the operation
        is reversible, and the menu item is disabled when no entry is open.
        """
        if (getattr(self, "_delete_pending", False)
                or not (0 <= self.active < len(self.entries))):
            return
        self._delete_pending = True
        self._remove_active()
        GLib.idle_add(self._release_delete_guard)

    def _release_delete_guard(self):
        self._delete_pending = False
        return False

    def _short_title(self, title, limit=44):
        """Shorten a long entry title for the confirm sentence. The stored
        title is the entry's whole first line (capped at 60 characters by
        _derive), which inside quotation marks read as a sentence chopped
        mid-word — 'Delete "…keeps going well pa"?'. Cut back to the last whole
        word instead and mark it with an ellipsis."""
        title = title.strip()
        if len(title) <= limit:
            return title
        cut = title[:limit].rstrip()
        if " " in cut:
            cut = cut[:cut.rindex(" ")].rstrip()
        return cut + "…"

    def _remove_active(self):
        """Remove the current entry and re-anchor the selection to a neighbour,
        or fall back to the empty state when it was the last one. Persisted
        immediately since this is a structural change."""
        if not (0 <= self.active < len(self.entries)):
            return
        self.undo.checkpoint("Delete Entry")
        del self.entries[self.active]
        if not self.entries:
            self.active = -1
        elif self.active >= len(self.entries):
            self.active = len(self.entries) - 1
        self._refresh_list()
        self._load_active()
        self._persist()
        self.undo.commit()

    def _confirm(self, title, message, ok_label, on_yes):
        """Modal confirmation for a destructive action. Runs `on_yes` only when
        the primary button is pressed; crash-safe if the dialog can't build."""
        try:
            dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
            dlg.set_decorated(False)
            dlg.get_style_context().add_class("jrdlg")
            area = dlg.get_content_area()
            area.set_spacing(0)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.get_style_context().add_class("jrdlgbox")
            hd = Gtk.Label(label=title, xalign=0)
            hd.get_style_context().add_class("jrdlgtitle")
            msg = Gtk.Label(label=message, xalign=0)
            msg.set_line_wrap(True)
            msg.set_line_wrap_mode(Pango.WrapMode.WORD)
            msg.set_max_width_chars(40)
            msg.get_style_context().add_class("jrdlgmsg")
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            btns.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("jrdlgcancel")
            ok = Gtk.Button(label=ok_label)
            ok.get_style_context().add_class("jrdlgok")
            btns.pack_start(cancel, False, False, 0)
            btns.pack_start(ok, False, False, 0)
            box.pack_start(hd, False, False, 0)
            box.pack_start(msg, False, False, 0)
            box.pack_start(btns, False, False, 0)
            area.add(box)
            cancel.connect("clicked", lambda *_: dlg.destroy())
            ok.connect("clicked", lambda *_: (dlg.destroy(), on_yes()))
            # Esc cancels the destructive action. The modal dialog is a separate
            # window with its own key focus, so the app-window Esc handler never
            # sees these events — wire it here or Esc would be dead in the dialog.
            dlg.connect(
                "key-press-event",
                lambda _w, e: (dlg.destroy() or True)
                if e.keyval == Gdk.KEY_Escape else False)
            dlg.show_all()
            # focus the safe default so a stray Space/Return cancels, not deletes
            cancel.grab_focus()
        except Exception:
            pass

    # ---------------- menus ----------------
    def menu_items(self, name):
        if name == "File":
            # New Entry is this app's purely-internal "new document" action; it
            # only appends to the in-memory model. Export to PDF renders the
            # entries into Documents. No open/save — journal.json autosave is
            # the source of truth. Delete Entry is destructive, so it is only
            # offered when an entry is open and always confirms first.
            have = 0 <= self.active < len(self.entries)
            return [
                ("New Entry", self.new_entry),
                ("Delete Entry", self._delete_active if have else None),
                nbapp.SEP,
                ("Export to PDF", self._export_pdf),
                ("Print…", self._print),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Format":
            # only meaningful with an entry open (the editor is hidden and there
            # is nothing to format in the empty state), matching the Insert menu
            have = 0 <= self.active < len(self.entries)
            return [
                ("Bold    Ctrl+B",
                 (lambda: self._toggle_tag("bold")) if have else None),
                ("Italic    Ctrl+I",
                 (lambda: self._toggle_tag("italic")) if have else None),
                nbapp.SEP,
                ("Quote", (lambda: self._toggle_tag("quote")) if have else None),
                ("Bullet", self._bullet if have else None),
                nbapp.SEP,
                ("Clear Formatting", self._clear_format if have else None),
            ]
        if name == "Insert":
            # only offered when an entry is open (the editor is hidden in the
            # empty state, so there is nowhere to insert into)
            have = 0 <= self.active < len(self.entries)
            return [
                ("Insert Date", self._insert_date if have else None),
                ("Insert Time", self._insert_time if have else None),
                nbapp.SEP,
                ("Insert Divider", self._insert_divider if have else None),
                ("Insert Bullet List", self._insert_bullets if have else None),
            ]
        if name == "Edit":
            # Undo/redo lead the menu, as they do in every editor — and they
            # have to be VISIBLE, not just bound to a key nobody can discover.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit")
        if name == "View":
            can_newer = self.active - 1 >= 0
            can_older = 0 <= self.active + 1 < len(self.entries)
            return [
                ("Search Entries    Ctrl+F",
                 self._focus_search if self.entries else None),
                ("Show All Entries",
                 (lambda: self._clear_search()) if self._query else None),
                nbapp.SEP,
                ("Show / Hide Entries", self._toggle_sidebar),
                nbapp.SEP,
                ("Newer Entry",
                 (lambda: self._go_entry(-1)) if can_newer else None),
                ("Older Entry",
                 (lambda: self._go_entry(1)) if can_older else None),
            ]
        return super().menu_items(name)

    def _clear_format(self):
        """Strip every tag from the current selection (no-op if nothing
        selected or no entry is open)."""
        if not (0 <= self.active < len(self.entries)):
            return
        try:
            buf = self.body.get_buffer()
            bounds = buf.get_selection_bounds()
            if not bounds:
                self.body.grab_focus()
                return
            start, end = bounds
            self.undo.checkpoint("Clear Formatting")
            buf.remove_all_tags(start, end)
            self._on_change(buf)        # see _toggle_tag: no "changed" signal
            self.undo.commit()
        except Exception:
            pass

    def _go_entry(self, delta):
        """Move selection by `delta` within the entries list, guarded so it
        never blanks the editor by stepping out of range."""
        if not self.entries:
            return
        i = self.active + delta
        if 0 <= i < len(self.entries) and i != self.active:
            self.select_entry(i)

    def _toggle_sidebar(self):
        """Show/hide the entries sidebar (the first child of the body row)."""
        try:
            body = self.content.get_children()[0]
            side = body.get_children()[0]
            side.set_visible(not side.get_visible())
        except Exception:
            pass

    # ---------------- css ----------------
    def _install_css(self):
        css = b"""
        .side { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        .sidehead { padding: 24px 26px 20px; border-bottom: 1px solid #D7D2C5; }
        .side * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .kicker { font-size: 11px; letter-spacing: 0.16em; color: #9A9484;
                  font-weight: 700; margin-bottom: 8px; }
        .yearlabel { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 24px; color: #1A1916; }
        .newbtn { min-width: 34px; min-height: 34px; padding: 0;
                  background: #FCFBF8; border: 1px solid #C9C4B6;
                  border-radius: 8px; box-shadow: none; }
        .newbtn:hover { background: #EFEBE0; }
        /* The Viewport that GTK inserts inside a ScrolledWindow paints its own
           background OVER .side, so the entries list came out paper-white
           below the sidebar's beige header. Name the viewport node itself, as
           .canvaswrap already does for the writing canvas. */
        .sidescroll, .sidescroll viewport { background: #F1EEE6; }
        .jsearch { margin-top: 16px; font-size: 13px; color: #1A1916;
                   background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; box-shadow: none; min-height: 30px; }
        .jsearch:focus { border: 1px solid #8A857A; }
        .searchcount { font-size: 11px; letter-spacing: 0.1em; color: #9A9484;
                       font-weight: 700; padding: 0 10px; margin: 4px 0 8px; }
        .listbox { padding: 16px 14px; }
        .sideempty { padding: 30px 12px; font-size: 13px; color: #9A9484; }
        .sideemptyhead { font-size: 13px; color: #6E695E; font-weight: 700; }
        /* the way out of a search that matched nothing: quiet, but a real
           control, so the pane is never a dead end */
        .sideemptybtn { min-height: 30px; padding: 0 10px; font-size: 13px;
                        color: #6E695E; background: #EFEBE0;
                        border: 1px solid #D7D2C5; border-radius: 8px;
                        box-shadow: none; }
        .sideemptybtn:hover { background: #EAE3D2; }
        .monthlabel { font-size: 11px; letter-spacing: 0.14em; color: #9A9484;
                      font-weight: 700; padding: 0 10px; margin: 14px 0 9px; }
        .entryrow { padding: 12px 10px; border-radius: 6px; margin-bottom: 2px;
                    border-left: 3px solid transparent; }
        /* The row hit area is a Gtk.Button now, so it arrives wearing the
           theme's button chrome: a paper fill, a hairline border, a 8px radius
           and 5px 14px of padding that would inset every row and double up on
           .entryrow's own padding. Strip it back to a bare hit area and let the
           inner row keep owning the layout. No `outline: none` here: the focus
           ring is what makes the keyboard path visible, and it is the whole
           reason these are buttons. */
        .entryrowhit { padding: 0; margin: 0; border: none;
                       background: transparent; background-image: none;
                       box-shadow: none; min-height: 0; min-width: 0;
                       border-radius: 6px; }
        .entryrowhit:hover { background: #EAE3D2; border-radius: 6px; }
        .entryrow.active { background: #EAE3D2; border-left: 3px solid #C8341E; }
        .datebox { background: transparent; margin-top: 1px; }
        .dbday { font-size: 20px; font-weight: 400; color: #6E695E; }
        .datebox.active .dbday { color: #C8341E; }
        .dbwd { font-size: 10px; letter-spacing: 0.09em; color: #9A9484;
                font-weight: 700; margin-top: 1px; }
        .datebox.active .dbwd { color: #B3AD9E; }
        .entrytitle { font-family: "Newsreader","Liberation Serif",serif;
                      font-size: 16px; color: #1A1916; }
        .entrypreview { font-size: 12px; color: #9A9484; margin-top: 3px; }

        .formatbar { background: #FCFBF8; border-bottom: 1px solid #D7D2C5;
                     padding: 10px 36px; min-height: 54px; }
        .formatbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .fmtbtn { min-width: 34px; min-height: 34px; padding: 0;
                  background: transparent; border: none; border-radius: 8px;
                  box-shadow: none; color: #2A2620; font-size: 17px; }
        .fmtbtn:hover { background: #EFEBE0; }
        /* With no entry open these are insensitive, but B and I still looked
           live: the theme's `* { color: ink }` lands on the button's own LABEL
           node, and a direct declaration beats any colour inherited from the
           button, so the label has to be named explicitly. */
        .fmtbtn:disabled, .fmtbtn:disabled label { color: #B3AD9E; }
        .fmtbtn.bold { font-weight: 700; }
        .fmtbtn.ital { font-style: italic;
                       font-family: "Newsreader","Liberation Serif",serif; }
        .fsep { color: #D7D2C5; min-width: 1px; }
        .wordcount, .savestate { font-size: 13px; color: #8A857A; }

        .editorcol { background: #FCFBF8; }
        .canvaswrap { background: #FCFBF8; }
        .canvaswrap viewport { background: #FCFBF8; }
        .page { padding: 72px 24px 160px; }
        .bigdate { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 44px; color: #1A1916; letter-spacing: -0.01em;
                   margin-bottom: 12px; }
        .metaline { font-size: 13px; letter-spacing: 0.04em; color: #9A9484;
                    margin-bottom: 46px; }
        .emptybox { margin-top: 40px; }
        .emptyhead { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 20px; color: #1A1916; }
        .emptysub { font-size: 13px; color: #9A9484; margin-bottom: 14px; }
        .emptybtn { min-height: 36px; padding: 0 18px; font-size: 14px;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; box-shadow: none; color: #2A2620; }
        .emptybtn:hover { background: #EFEBE0; }
        .docbody { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 20px; color: #2A2620; background: #FCFBF8;
                   caret-color: #C8341E; }
        .docbody text { background: #FCFBF8; }
        .docbody text selection { background-color: #EAE3D2; color: #1A1916; }

        /* confirm dialog for destructive actions (paper card, darker-beige
           border; signage-red only on the destructive primary button) */
        .jrdlg { background: #FCFBF8; border: 1px solid #C9C4B6; }
        .jrdlgbox { padding: 24px 28px 20px; }
        .jrdlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .jrdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
                      font-size: 20px; color: #1A1916; }
        .jrdlgmsg { font-size: 13px; color: #6E695E; }
        .jrdlgcancel { font-size: 13px; color: #2A2620; padding: 6px 16px;
                       background: #FCFBF8; border: 1px solid #C9C4B6;
                       border-radius: 8px; box-shadow: none; }
        .jrdlgcancel:hover { background: #EFEBE0; }
        /* The label needs its OWN colour rule: the theme's `* { color: ink }`
           lands directly on the button's label node, and a direct declaration
           beats a colour inherited from the button, so paper-on-red set here
           alone rendered as near-black text on the red button. */
        .jrdlgok, .jrdlgok label { font-size: 13px; color: #FCFBF8; }
        .jrdlgok { padding: 6px 16px; background: #C8341E;
                   border: 1px solid #C8341E;
                   border-radius: 8px; box-shadow: none; }
        .jrdlgok:hover { background: #B12D19; border-color: #B12D19; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # styling is cosmetic; a bad screen/provider must not stop launch
            pass


if __name__ == "__main__":
    nbapp.run(Journal)
