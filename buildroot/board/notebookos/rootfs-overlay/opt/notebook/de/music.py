#!/usr/bin/env python3
"""
Music — the Notebook OS library music player (native GTK).

A sidebar of views (Songs / Albums / Artists) and user playlists, a main pane
with a search field and column header, and a fixed playback bar along the
bottom. The library is enumerated at launch from Home / Music
(.mp3/.flac/.ogg/.wav/.m4a), each track named from its file and containing
folder; nothing is fabricated, so an empty or absent folder opens on the empty
state. Playback is real: a GStreamer 'playbin' pipeline decodes the selected
file to ALSA, and the transport, progress/seek bar, volume, shuffle and repeat
all drive that pipeline. The playlists the user builds persist to
$NB_HOME/.config/notebook/music.json.

GStreamer is only guaranteed on the built guest; on a host without it the app
still constructs and opens its (empty or library) state, with the engine
controls disabled and a neutral 'Media engine unavailable' note (see GST_OK).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib, GdkPixbuf  # noqa: E402

import os
import sys
import json
import random
import subprocess
import copy
import unicodedata

# The audio engine is optional at import time: GStreamer (Gst) is only
# guaranteed on the built guest, not on the host running construct_all.py /
# selftests. Guard the require_version + import so the module always imports and
# the window always constructs; GST_OK gates every use of the pipeline below.
GST_OK = False
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
    GST_OK = True
except (ImportError, ValueError):
    Gst = None

# Track lengths come from GStreamer's Discoverer (gst-pbutils), part of the same
# media stack the player already uses. Guarded exactly like Gst above: without it
# the Time column simply stays blank, as it always was.
GstPbutils = None
DISC_OK = False
if GST_OK:
    try:
        gi.require_version("GstPbutils", "1.0")
        from gi.repository import GstPbutils  # noqa: E402
        DISC_OK = True
    except (ImportError, ValueError):
        GstPbutils = None

import nbapp
import nbstate
import nbicons
from nbi18n import _t  # noqa: E402

DE_DIR = os.path.dirname(os.path.abspath(__file__))

# where the library is sourced from + where the user's playlists are kept
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "music.json")
MUSIC_DIR = os.path.join(HOME, "Music")
AUDIO_EXTS = (".mp3", ".flac", ".ogg", ".wav", ".m4a")


def _info_tags(info):
    """(title, artist, album) from a DiscovererInfo, or ('', '', '').

    The library used to be named from FILE PATHS alone -- "01 Some Song.mp3"
    became the title and the artist read "Unknown Artist" even when the file
    carried perfectly good tags. The Discoverer that already runs over every
    file for its duration returns the tags too, so this costs nothing extra.

    Every step is guarded: a file with no tags, a GStreamer without the tag
    plugins, or a deprecated accessor must all degrade to the old filename
    behaviour rather than raise on a background callback."""
    try:
        tags = info.get_tags()
    except Exception:                                          # noqa: BLE001
        tags = None
    if tags is None:
        return "", "", ""
    out = []
    for key in ("title", "artist", "album"):
        val = ""
        try:
            ok, got = tags.get_string(key)
            if ok and got:
                val = got.strip()
        except Exception:                                      # noqa: BLE001
            val = ""
        out.append(val)
    return out[0], out[1], out[2]


def _info_image(info):
    """The bytes of an embedded cover image, or None.

    ID3 art arrives as a sample under the "image" tag; a file can carry several
    (front cover, back, artist), and the first is the one to show."""
    try:
        tags = info.get_tags()
        if tags is None or tags.get_tag_size("image") < 1:
            return None
        ok, sample = tags.get_sample_index("image", 0)
        if not ok or sample is None:
            return None
        buf = sample.get_buffer()
        ok2, mi = buf.map(Gst.MapFlags.READ)
        if not ok2:
            return None
        try:
            return bytes(mi.data)
        finally:
            buf.unmap(mi)
    except Exception:                                          # noqa: BLE001
        return None


class Music(nbapp.AppWindow):
    app_name = "Music"
    menus = ("File", "Edit", "View", "Controls")

    VIEWS = (("songs", "Songs", "music"),
             ("albums", "Albums", "album"),
             ("artists", "Artists", "artist"))

    # gutter between the Title / Artist / Album / Time columns, applied to the
    # header and to every track row so they share one grid
    COLUMN_GAP = 12

    def __init__(self):
        super().__init__()
        self._install_css()
        # Size-dependent metrics derive from the REAL panel, never a hardcoded
        # 1920 — the sidebar and the Artist/Album/Time columns narrow on a
        # small panel (1366x768, 1280x800) so nothing is cramped or clipped,
        # and cap at the roomy desktop widths on a large one. The column header
        # and every track row read the same attrs, so they stay aligned.
        self._sw, self._sh = nbapp.screen_size()
        self._side_w = max(200, min(264, int(self._sw * 0.17)))
        self._cell_w = max(150, min(220, int(self._sw * 0.13)))
        self._time_w = 80
        self.view = "songs"
        self._rows = {}
        self._playlists = []
        self._playlist_rows = []
        # per-playlist track lists, keyed by the playlist's (unique) name, plus
        # the name of the playlist currently shown in the main pane, if any.
        # This is what turns "New Playlist" into a usable feature instead of a
        # dead-end: tracks added via each song row's + button land here.
        self._playlist_tracks = {}
        self._current_playlist = None
        self._playing = False
        self._play_img = None
        self._play_ev = None       # the play/pause control (its tooltip flips)
        self._nowlbl = None
        # Music's status channel. It had none at all, so a track that would not
        # decode failed in complete silence: the play glyph went back to Play
        # and nothing anywhere said why. The now-playing line is the one label
        # a listener is already reading, so a failure borrows it for a moment.
        self._flash_serial = 0     # so a later message wins the restore race
        self._flashing = False     # while set, refreshes leave the label alone
        self.lbl_total = None
        self.lbl_elapsed = None
        # --- GStreamer engine state (all None/0 until _build_engine) ---
        self._player = None        # the playbin pipeline, or None if unavailable
        self._loaded_path = None   # file path currently loaded into the pipeline
        self._duration_ns = 0      # cached duration of the loaded track (ns)
        self._poll_id = 0          # GLib.timeout source id for progress polling
        # set once the window is gone: the pipeline's bus messages are
        # dispatched from the main loop, so one posted just before teardown is
        # still delivered afterwards. Nothing may start audio or arm a timer
        # from that point on (see _on_eos / _start_poll).
        self._closed = False
        self._user_seeking = False  # true while the user drags the seek bar
        self._seek = None          # the progress/seek Gtk.Scale
        # a drill-down "scope" (("album", name) or ("artist", name)) opened by
        # clicking an Albums/Artists row — renders that group's tracks. None in
        # the plain Songs/Albums/Artists views.
        self._scope = None
        self._scope_label = ""         # the album/artist name the scope shows
        self._scope_origin = None      # which sidebar view the scope drilled from
        # playlists restored from disk, applied once the sidebar exists
        self._loaded_playlists = []
        # What was open when the library was last closed: a library view id, or
        # a playlist NAME. Both are read by _load and applied by
        # _restore_selection once the sidebar rows actually exist — a playlist
        # is restored by its name, never by its row position, because playlists
        # are created, renamed and deleted between sessions.
        self._saved_view = "songs"
        self._saved_playlist = None
        # True only while _restore_selection is putting the sidebar back, so
        # restoring cannot be mistaken for the user changing something (see
        # _save).
        self._restoring = nbstate.RestoreScope()

        # library model — a list of track dicts: {title, artist, album, time}
        # enumerated from Home / Music at launch (see _load_library). Nothing is
        # seeded; an empty or absent folder opens on the empty state.
        self.songs = []
        self._query = ""      # current search text (filters the rendered list)
        self._current = None  # the track cued in the playback bar, if any
        # Forward shuffle is a bag, not an independent coin toss: every track
        # in the visible queue plays once before any can repeat.
        self._shuffle_remaining = []
        self._shuffle_queue_key = ()
        # track lengths: {path: [stat-key, seconds]}, read once by the background
        # scan below and cached on disk so a relaunch is instant.
        self._lengths = {}
        self._tags = {}       # path -> [key, title, artist, album] from the file
        self._art_img = None  # the playbar's artwork image, set once built
        # (path, logical size, device scale) -> Pixbuf | False (False = looked,
        # found none). The scale is in the key because the SAME cover at the
        # same layout size is a different number of real pixels on a 1x and a
        # 2x screen; keying on path alone would hand a window moved between
        # monitors the raster built for the other one.
        self._art_cache = {}
        self._by_path = {}    # path -> track dict, for the scan's callbacks
        self._time_labels = {}   # path -> the Time labels currently on screen
        self._tag_labels = {}    # path -> [(label, field)] so real tags can
                                 # replace filename guesses without a re-render
        self._disc = None     # the running GstPbutils.Discoverer, if any
        self._disc_dirty = False
        self._match_count = 0    # tracks passing the current search
        self._empty_row = None   # the "nothing matched" row inside the list
        self._empty_lbl = None
        self._load_library()
        # load the user's saved playlists now that the library exists (so saved
        # tracks can be re-linked to the live library objects)
        self._load()
        self._apply_cached_lengths()

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        body.pack_start(self._sidebar(), False, False, 0)
        body.pack_start(self._main(), True, True, 0)
        self.content.pack_start(body, True, True, 0)
        self.content.pack_start(self._playbar(), False, False, 0)

        # rebuild the saved playlist rows in the (now-built) sidebar
        self._restore_playlists()
        # persist playlists on close as well as on every mutation
        self.connect("destroy", self._on_destroy)

        # bring up the audio engine now that the playback-bar widgets exist; if
        # GStreamer is unavailable the controls are disabled and the bar reads
        # 'Media engine unavailable'
        self._build_engine()
        if not self._engine_ok():
            self._disable_engine_controls()
        with self._restoring:
            self.shuffle.set_active(bool(getattr(self, "_saved_shuffle", False)))
            self.repeat.set_active(bool(getattr(self, "_saved_repeat", False)))
        self._refresh_transport()

        self._restore_selection()
        self.undo = nbapp.UndoHistory(self._undo_snapshot,
                                      self._restore_undo_snapshot)
        self.undo.reset()
        # A song handed in as argv[1] (the Finder opens audio files this way)
        # is cued and played on launch, so double-clicking a track in the file
        # manager actually plays it rather than just opening the library.
        self._open_arg_file()
        # fill in any track length we have not read yet, in the background
        self._start_length_scan()

    def _open_arg_file(self):
        """Play a track handed in as sys.argv[1] (the Finder opens audio files
        this way). A file already in the ~/Music library is played from it; one
        from elsewhere is added to the in-memory library for this session so the
        transport has something to decode. Fully defensive — a bad or missing
        arg just opens the library normally and never crashes launch."""
        try:
            if len(sys.argv) < 2:
                return
            path = sys.argv[1]
            if not (path and os.path.isfile(path)):
                return
            if os.path.splitext(path)[1].lower() not in AUDIO_EXTS:
                return
            ap = os.path.abspath(path)
            track = None
            for s in self.songs:
                if os.path.abspath(s.get("path", "")) == ap:
                    track = s
                    break
            if track is None:
                # a file outside ~/Music: synthesize a track (honouring the
                # 'Artist - Title' filename convention) and surface it at the
                # top of the library so it can play this session.
                base = os.path.splitext(os.path.basename(ap))[0].strip()
                artist, title = "Unknown Artist", base
                if " - " in base:
                    a, t = base.split(" - ", 1)
                    if a.strip() and t.strip():
                        artist, title = a.strip(), t.strip()
                track = {"title": title or "Untitled", "artist": artist,
                         "album": "Unknown Album", "time": "", "path": ap}
                self.songs.insert(0, track)
            self._select("songs")
            self._play_track(track)
        except Exception:
            pass

    # ---------------- sidebar ----------------
    def _sidebar(self):
        sb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sb.get_style_context().add_class("sidebar")
        sb.set_size_request(self._side_w, -1)
        self._sb = sb

        sb.pack_start(self._heading("Library"), False, False, 0)
        for vid, label, glyph in self.VIEWS:
            sb.pack_start(self._viewrow(vid, label, glyph), False, False, 0)

        pl = self._heading("Playlists")
        pl.set_margin_top(18)
        sb.pack_start(pl, False, False, 0)

        # The user's playlists live in their own scroller. Packed straight into
        # the sidebar they were an unbounded stack of rows, so the WINDOW's
        # minimum height grew with the library: eight playlists already needed
        # 887px against the 740px a 768-tall panel has, which pushes the
        # playback bar off the bottom of the screen entirely. Propagating the
        # list's natural height keeps the sidebar looking exactly as it does now
        # while letting it shrink (and scroll) when there is no room.
        plscroll = Gtk.ScrolledWindow()
        plscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        plscroll.set_propagate_natural_height(True)
        plscroll.get_style_context().add_class("plscroll")
        pl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        plscroll.add(pl_box)          # auto-wraps the list in a viewport
        self._pl_box = pl_box
        sb.pack_start(plscroll, False, False, 0)

        # A bare "No playlists" told the reader only what she could already see.
        # Say what a playlist is for and point at the button right beneath this
        # note. One label, so the existing show/hide of self._none still works;
        # the width is capped for the same reason the footer below it is.
        none = Gtk.Label(
            label=_t("No playlists") + "\n"
                  + _t("Add one below."),
            xalign=0)
        none.set_line_wrap(True)
        none.set_max_width_chars(24)
        none.get_style_context().add_class("empty-mini")
        pl_box.pack_start(none, False, False, 0)
        self._none = none

        newpl = Gtk.Button()
        newpl.set_relief(Gtk.ReliefStyle.NONE)
        newpl.get_style_context().add_class("newplaylist")
        nprow = Gtk.Box(spacing=9)
        nprow.pack_start(nbicons.image("plus", 15, "#1A1916"), False, False, 0)
        nprow.pack_start(Gtk.Label(label=_t("New Playlist")), False, False, 0)
        newpl.add(nprow)
        newpl.connect("clicked", self._new_playlist)
        sb.pack_start(newpl, False, False, 0)
        self._newpl = newpl

        # _t, not a bare literal: the catalogs have carried this sentence all
        # along and nothing applied it, so the one line explaining where the
        # library comes from stayed English in all 16 other languages.
        foot = Gtk.Label(label=_t("Tracks are read from Home / Music."))
        foot.get_style_context().add_class("sidefoot")
        foot.set_line_wrap(True)
        # A wrapped label still REQUESTS its full one-line width as its natural
        # size, and a GtkBox hands a non-expanding child its natural width when
        # there is room — so this one sentence stretched the sidebar to 480px,
        # more than double the intended _side_w, and squeezed the track list's
        # Title column down to an ellipsis on a 1024/1366 panel. Cap the natural
        # width; the label still wraps to whatever width the sidebar allocates.
        foot.set_max_width_chars(24)
        foot.set_xalign(0)
        foot.set_valign(Gtk.Align.END)
        foot.set_vexpand(True)
        sb.pack_start(foot, True, True, 0)
        return sb

    def _heading(self, text):
        # section labels read small, uppercase, letter-tracked and muted
        lbl = Gtk.Label(label=text.upper(), xalign=0)
        lbl.get_style_context().add_class("sidehead")
        return lbl

    def _viewrow(self, vid, label, glyph):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("viewrow")
        row = Gtk.Box(spacing=12)
        row.pack_start(nbicons.image(glyph, 18, "#1A1916"), False, False, 0)
        name = Gtk.Label(label=label, xalign=0)
        row.pack_start(name, True, True, 0)
        count = Gtk.Label(label="0")
        count.get_style_context().add_class("viewcount")
        row.pack_start(count, False, False, 0)
        btn.add(row)
        # keep the count label so _update_counts() can reflect the live library
        btn._count_lbl = count
        btn.connect("clicked", lambda *_: self._select(vid))
        self._rows[vid] = btn
        return btn

    def _playlist_row(self, name):
        # a sidebar playlist entry — reuses the library row styling
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("viewrow")
        btn.get_style_context().add_class("playlistrow")
        row = Gtk.Box(spacing=12)
        row.pack_start(nbicons.image("viewlist", 18, "#1A1916"), False, False, 0)
        name_lbl = Gtk.Label(label=name, xalign=0)
        # a playlist the user named "Long Drive Home Through The Mountains"
        # asked for its full 325px as a MINIMUM, which dragged the whole sidebar
        # out to 375px on a 1024 panel and squeezed the track list. Truncate the
        # name instead; the full name is on the row's tooltip and in the header.
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        name_lbl.set_max_width_chars(16)
        row.pack_start(name_lbl, True, True, 0)
        btn.set_tooltip_text(name)
        btn.add(row)
        btn._name_lbl = name_lbl
        # store the name on the row so a rename stays in sync — the click must
        # read the LIVE name, not one captured in the lambda at build time
        btn._pl_name = name
        # clicking a playlist selects it in the main pane (like a library view)
        btn.connect("clicked", lambda *_: self._select_playlist(btn, btn._pl_name))
        return btn

    def _select_playlist(self, row, name):
        # highlight the chosen playlist and title the main pane with it, then
        # render its tracks; the Title/Artist/Album/Time header shows only when
        # the playlist actually has tracks so an empty one reads clean
        try:
            self.view = None
            self._scope = None
            self._scope_origin = None
            self._current_playlist = name
            for btn in self._rows.values():
                btn.get_style_context().remove_class("active")
            for prow in self._playlist_rows:
                ctx = prow.get_style_context()
                if prow is row:
                    ctx.add_class("active")
                else:
                    ctx.remove_class("active")
            self.title.set_text(name)
            # the Rename/Delete actions apply to whichever playlist is open
            self._set_playlist_actions(True)
            has = bool(self._playlist_tracks.get(name))
            self.colhead.set_visible(has)
            self.colhead.set_no_show_all(not has)
            # opening a playlist starts clean too (same as the library views)
            if self._search is not None:
                self._search.set_text("")
            self._query = ""
            # render this playlist's tracks (empty ones show a status note)
            self._populate()
        except Exception:
            # a torn-down/invalid row must not crash the click
            pass

    def _new_playlist(self, *_):
        # append an auto-numbered playlist into the sidebar's playlist section,
        # persist it, then OPEN it so the click lands the user inside the new
        # (empty) playlist — with the Rename/Delete actions and the "add tracks"
        # guidance visible — rather than dropping a mystery row into the sidebar
        # with the main pane unchanged and nothing selected.
        name = self._unique_playlist_name()
        row = self._create_playlist(name)
        self._save()
        if row is not None:
            self._select_playlist(row, name)

    def _unique_playlist_name(self):
        # "Playlist N", stepping past any name already in use
        n = len(self._playlists) + 1
        name = "Playlist %d" % n
        while name in self._playlists:
            n += 1
            name = "Playlist %d" % n
        return name

    def _create_playlist(self, name, tracks=None):
        # build a sidebar playlist row + its (possibly pre-filled) track list.
        # Shared by the New Playlist action and the restore-from-disk path.
        if name in self._playlists:
            # never duplicate a name — the parallel row/track structures are
            # keyed by it (New Playlist already picks a unique name)
            return None
        row = self._playlist_row(name)
        self._playlists.append(name)
        self._playlist_rows.append(row)
        self._playlist_tracks[name] = list(tracks) if tracks else []
        # the first playlist retires the "No playlists" placeholder
        self._none.set_no_show_all(True)
        self._none.hide()
        # the rows live INSIDE the playlist scroller (never straight in the
        # sidebar box) so a long list scrolls instead of growing the window
        self._pl_box.pack_start(row, False, False, 0)
        row.show_all()
        return row

    def _restore_selection(self):
        """Reopen on whatever was open last time.

        Runs after the library scan AND after _restore_playlists, so the row
        being selected exists: restoration by identity is only honest once the
        content it names is on screen. A saved playlist that has since been
        deleted (or a store that never held one) falls back to the saved
        library view, and that falls back to Songs — the library always opens
        on something."""
        with self._restoring:
            try:
                name = self._saved_playlist
                i = nbstate.identity_index(self._playlists, name)
                if i >= 0 and i < len(self._playlist_rows):
                    self._select_playlist(self._playlist_rows[i], name)
                    return
                self._select(nbstate.choice(
                    self._saved_view, [v[0] for v in self.VIEWS], "songs"))
            except Exception:
                # a damaged store must never keep the library from opening
                self._select("songs")

    def _restore_playlists(self):
        # recreate saved playlists in the sidebar (called after it is built)
        try:
            for name, tracks in getattr(self, "_loaded_playlists", []):
                self._create_playlist(name, tracks)
        except Exception:
            pass

    # ---------------- main ----------------
    def _main(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.get_style_context().add_class("mainpane")

        # header: title + search
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("mainhead")
        self.title = Gtk.Label(label=_t("Songs"), xalign=0)
        self.title.get_style_context().add_class("viewtitle")
        head.pack_start(self.title, False, False, 0)

        # per-playlist actions (Rename / Delete) — shown next to the title only
        # while a user playlist is open, hidden for the Songs/Albums/Artists
        # library views. This is what makes playlists a complete feature (create,
        # add, remove, rename, delete) rather than an append-only dead-end.
        self._pl_actions = Gtk.Box(spacing=4)
        self._pl_actions.set_valign(Gtk.Align.CENTER)
        self._pl_actions.set_margin_start(14)
        self._pl_actions.pack_start(
            self._icon_button("pencil", "Rename Playlist",
                              lambda *_: self._rename_current_playlist()),
            False, False, 0)
        self._pl_actions.pack_start(
            self._icon_button("trash", "Delete Playlist",
                              lambda *_: self._delete_current_playlist()),
            False, False, 0)
        # hidden until a playlist opens (never shown by the initial show_all)
        self._pl_actions.set_no_show_all(True)
        head.pack_start(self._pl_actions, False, False, 0)

        searchbox = Gtk.Box(spacing=8)
        searchbox.get_style_context().add_class("searchbox")
        searchbox.set_valign(Gtk.Align.CENTER)
        searchbox.pack_start(nbicons.image("search", 15, "#9A9484"), False, False, 0)
        entry = Gtk.Entry()
        entry.set_has_frame(False)
        entry.set_placeholder_text(_t("Search library"))
        entry.get_style_context().add_class("searchentry")
        entry.set_size_request(180, -1)
        # filter the rendered library as the user types
        entry.connect("changed", self._on_search)
        self._search = entry
        searchbox.pack_start(entry, True, True, 0)
        head.pack_end(searchbox, False, False, 0)
        main.pack_start(head, False, False, 0)

        # column header (only for songs view). COLUMN_GAP is shared with each
        # track row's box so the two stay on one grid; without it a long,
        # ellipsized title ran straight into the Artist column with no gutter.
        self.colhead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=self.COLUMN_GAP)
        self.colhead.get_style_context().add_class("colhead")
        for text, expand, xalign in (("Title", True, 0.0),
                                     ("Artist", False, 0.0),
                                     ("Album", False, 0.0),
                                     ("Time", False, 1.0)):
            c = Gtk.Label(label=text.upper(), xalign=xalign)
            if expand:
                self.colhead.pack_start(c, True, True, 0)
            else:
                c.set_size_request(
                    self._time_w if text == "Time" else self._cell_w, -1)
                self.colhead.pack_start(c, False, False, 0)
        # reserve a slim trailing column so the header lines up with each row's
        # per-track "Add to Playlist" (+) button
        spacer = Gtk.Label(label="")
        spacer.set_size_request(30, -1)
        self._colhead_spacer = spacer
        self.colhead.pack_start(spacer, False, False, 0)
        main.pack_start(self.colhead, False, False, 0)

        # populated library — a scrolling track/album/artist list. Shown when
        # the library has content; hidden in favour of the empty state below
        # when it does not (see _populate / _show_empty).
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        # the scroll owns its own GdkWindow: it MUST paint an opaque papertone
        # background or the track-list area renders solid black on the
        # no-compositor stack (the ListBox above it is deliberately transparent)
        scroll.get_style_context().add_class("songscroll")
        self._songscroll = scroll
        self.songrows = Gtk.ListBox()
        self.songrows.set_selection_mode(Gtk.SelectionMode.NONE)
        self.songrows.get_style_context().add_class("songlist")
        self.songrows.connect("row-activated", self._on_song_activated)
        # SEARCH FILTERS THE ROWS ALREADY BUILT — it does not rebuild the list.
        # Every keystroke used to tear down and reconstruct one ListBoxRow (five
        # widgets) per matching track: on a real 500-track library that measured
        # 448ms for the first letter typed, on a fast desktop, before the guest's
        # software renderer is even in the picture. The rows for a view are built
        # once; typing only re-runs this predicate over them.
        self.songrows.set_filter_func(self._row_filter)
        scroll.add(self.songrows)
        main.pack_start(scroll, True, True, 0)
        # COLUMN ALIGNMENT. The header sits OUTSIDE the scroller while the rows
        # sit inside it, so as soon as the library is long enough to scroll, the
        # vertical scrollbar eats width from the rows only — measured at 17px,
        # which pushed every row's Artist/Album/Time column left of its heading.
        # Mirror that width as a right margin on the header so the two always
        # share one grid, and keep it in step as the scrollbar comes and goes.
        self._songscroll.connect("size-allocate",
                                 lambda *_a: self._sync_colhead_margin())
        vsb = scroll.get_vscrollbar()
        if vsb is not None:
            vsb.connect("size-allocate", lambda *_a: self._sync_colhead_margin())
            vsb.connect("notify::visible", lambda *_a: self._sync_colhead_margin())

        # empty state
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_vexpand(True)
        empty.pack_start(nbicons.image("music", 52, "#C9C4B6"), False, False, 0)
        t = Gtk.Label(label=_t("Library empty"))
        t.get_style_context().add_class("empty-title")
        empty.pack_start(t, False, False, 0)
        # _t: the whole point of an empty state is that it tells the reader what
        # to do next, and this sentence — already translated in all 17 catalogs
        # — was handed to the label raw, so it said it in English under a
        # heading that WAS translated.
        d = Gtk.Label(
            label=_t("No audio files in Home / Music. Supported formats: "
                     ".mp3, .flac, .ogg, .wav, .m4a."))
        d.get_style_context().add_class("empty-desc")
        d.set_line_wrap(True)
        d.set_justify(Gtk.Justification.CENTER)
        d.set_max_width_chars(46)
        empty.pack_start(d, False, False, 0)
        openbtn = Gtk.Button(label=_t("Open Music Folder"))
        openbtn.get_style_context().add_class("openfolder")
        openbtn.set_margin_top(8)
        openbtn.connect("clicked", lambda *_: self._open_music_folder())
        empty.pack_start(openbtn, False, False, 0)
        self.empty = empty
        main.pack_start(empty, True, True, 0)
        return main

    def _open_music_folder(self):
        # the library is sourced from ~/Music — ensure the folder exists, then
        # open it in the file manager
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
        except OSError:
            pass
        try:
            subprocess.Popen(["python3", os.path.join(DE_DIR, "finder.py"), "Music"],
                             env=dict(os.environ, PYTHONPATH=DE_DIR))
        except (OSError, ValueError):
            # file manager unavailable (missing python3/finder.py) — stay put
            pass

    def _icon_button(self, glyph, tooltip, cb):
        # a small, quiet icon button used for the header playlist actions
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("plact")
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_tooltip_text(tooltip)
        btn.add(nbicons.image(glyph, 16, "#6E695E"))
        btn.connect("clicked", cb)
        return btn

    def _set_playlist_actions(self, show):
        # reveal the Rename/Delete header actions only while a playlist is open
        try:
            if show:
                self._pl_actions.set_no_show_all(False)
                self._pl_actions.show_all()
            else:
                self._pl_actions.set_no_show_all(True)
                self._pl_actions.hide()
        except Exception:
            pass

    # ---------------- playback bar ----------------
    def _playbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=22)
        bar.get_style_context().add_class("playbar")
        # The playbar is a FIXED-height transport strip pinned to the bottom.
        # GTK3 propagates vexpand up from children (the seek/volume scales and the
        # valign-centred now-playing block), which made this whole bar compute as
        # vertically expandable. As content's expanding child it then swallowed
        # the column's vertical slack: `body` (sidebar + main) stayed at natural
        # height near the top and the bar floated in the middle, leaving a blank
        # gap below the sidebar and empty space under the bar. Pin vexpand=False
        # so the slack goes to `body` and the bar sits flush at the bottom (the
        # vertical twin of the artwork's hexpand=False fix below).
        bar.set_vexpand(False)

        # transport — keep the three buttons so they can be disabled when the
        # audio engine is unavailable
        trans = Gtk.Box(spacing=12)
        trans.set_valign(Gtk.Align.CENTER)
        self._prev_ev = self._round("prev", 38)
        self._play_ev = self._round("play", 48, big=True)
        self._next_ev = self._round("next", 38)
        # icon-only controls: name them, as the Media Viewer's transport does
        self._prev_ev.set_tooltip_text(_t("Previous track"))
        self._play_ev.set_tooltip_text(_t("Play"))
        self._next_ev.set_tooltip_text(_t("Next track"))
        trans.pack_start(self._prev_ev, False, False, 0)
        trans.pack_start(self._play_ev, False, False, 0)
        trans.pack_start(self._next_ev, False, False, 0)
        bar.pack_start(trans, False, False, 0)

        # artwork placeholder
        art = Gtk.Box()
        art.get_style_context().add_class("artwork")
        art.set_size_request(52, 52)
        art.set_valign(Gtk.Align.CENTER)
        art.set_halign(Gtk.Align.CENTER)
        img = nbicons.image("music", 22, "#C9C4B6")
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        # kept so _show_cover can swap in the cued track's embedded artwork;
        # this used to be a placeholder that nothing ever replaced
        self._art_img = img
        art.pack_start(img, True, True, 0)
        # GTK3 PROPAGATES hexpand up from children, and that beats the
        # pack_start(expand=False) used here: the artwork's inner image asked to
        # expand (only to centre itself), which made the whole artwork cell
        # expandable and let it swallow the bar's slack. The result was a
        # transport group, an artwork tile and a now-playing block scattered
        # across the bar with ~400px holes between them. Centre with halign
        # instead, and pin the cell to non-expanding so the bar's slack goes
        # where it is meant to: the now-playing/progress block.
        art.set_hexpand(False)
        bar.pack_start(art, False, False, 0)

        # now-playing + progress
        now = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        now.set_valign(Gtk.Align.CENTER)
        nowlbl = Gtk.Label(label=_t("Nothing playing"), xalign=0)
        nowlbl.get_style_context().add_class("nowplaying")
        # a long "Title — Artist" must ellipsize, not force the playbar wider:
        # without this the label demands its full natural width as a minimum,
        # which at the fixed 1920 window shoves the volume/toggles off the edge
        # (or clips them) instead of truncating cleanly.
        nowlbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._nowlbl = nowlbl
        now.pack_start(nowlbl, False, False, 0)
        prog = Gtk.Box(spacing=12)
        t0 = Gtk.Label(label="0:00")
        t0.get_style_context().add_class("timecode")
        # fixed timecode width so the seek bar between the two labels does not
        # jitter/relayout each time the elapsed digit count changes (e.g. 9:59
        # -> 10:00) — a needless full-row reflow on the software renderer.
        t0.set_size_request(42, -1)
        t0.set_xalign(1.0)
        # keep the elapsed timecode so the progress poll can advance it
        self.lbl_elapsed = t0
        prog.pack_start(t0, False, False, 0)
        # the progress bar is a real seek control: 0..1000 maps to 0..duration,
        # updated by the poll and click/drag-seekable while a track is loaded
        track = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
        track.set_draw_value(False)
        track.get_style_context().add_class("seekbar")
        track.set_valign(Gtk.Align.CENTER)
        # change-value fires only on user interaction (click/keyboard/drag);
        # the poll uses set_value, which fires value-changed instead, so there
        # is no feedback loop. Press/release gate the poll while dragging.
        track.connect("change-value", self._on_seek)
        track.connect("button-press-event", self._on_seek_press)
        track.connect("button-release-event", self._on_seek_release)
        self._seek = track
        prog.pack_start(track, True, True, 0)
        t1 = Gtk.Label(label="0:00")
        t1.get_style_context().add_class("timecode")
        # matching fixed width on the total so the seek bar stays put (see t0)
        t1.set_size_request(42, -1)
        t1.set_xalign(0.0)
        # keep the total-duration label so it can track the cued track's Time
        self.lbl_total = t1
        prog.pack_start(t1, False, False, 0)
        now.pack_start(prog, False, False, 0)
        bar.pack_start(now, True, True, 0)

        # volume
        vol = Gtk.Box(spacing=10)
        vol.set_valign(Gtk.Align.CENTER)
        vol.set_size_request(190, -1)
        vol.pack_start(nbicons.image("vol", 16, "#6E695E"), False, False, 0)
        self.vol = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.vol.set_draw_value(False)
        self.vol.set_value(70)
        self.vol.get_style_context().add_class("volslider")
        # drive the playbin "volume" property (0..1) live as the slider moves
        self.vol.connect("value-changed", self._on_volume)
        vol.pack_start(self.vol, True, True, 0)
        bar.pack_start(vol, False, False, 0)

        # shuffle / repeat
        toggles = Gtk.Box(spacing=8)
        toggles.set_valign(Gtk.Align.CENTER)
        self.shuffle = self._toggle("shuffle", "Shuffle")
        self.repeat = self._toggle("repeat", "Repeat")
        toggles.pack_start(self.shuffle, False, False, 0)
        toggles.pack_start(self.repeat, False, False, 0)
        bar.pack_start(toggles, False, False, 0)
        return bar

    def _round(self, glyph, size, big=False):
        # Transport control. This was a windowless Gtk.Box wrapped in an
        # input-only Gtk.EventBox listening for button-press-event, which made
        # prev/play/next MOUSE-ONLY: no Tab stop, no Space/Enter, and nothing
        # for assistive tech to announce, because an EventBox carries no button
        # role for nbapp's tooltip-derived naming to attach to. A native button
        # supplies all of that, and "clicked" fires for pointer and keyboard
        # alike. Relief NONE plus .roundbtn keeps the existing flat, circular
        # Papertone treatment at exactly `size` (see the CSS, which strips the
        # theme's padding, minimum size, pressed background image and shadow).
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("roundbtn")
        if big:
            button.get_style_context().add_class("roundbig")
        button.set_size_request(size, size)
        # keep the control a fixed circle (not stretched to the taller play
        # button) and vertically centred within the transport row
        button.set_valign(Gtk.Align.CENTER)
        button.set_halign(Gtk.Align.CENTER)
        # transport glyphs render in active ink (not the pale #C9C4B6
        # placeholder tone) so prev/play/next read as live controls
        img = nbicons.image(glyph, 19 if big else 16, "#1A1916")
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        button.add(img)
        # see the artwork note in _playbar: hexpand here propagates out to the
        # transport group and unbalances the whole bar.
        button.set_hexpand(False)
        if glyph == "play":
            # keep the play glyph so it can flip to the pause icon while playing
            self._play_img = img
        button.connect("clicked", lambda _w: self._on_transport(glyph))
        return button

    def _refresh_transport(self):
        # reflect self._playing in the play glyph + now-playing label
        playing = getattr(self, "_playing", False)
        if self._play_img is not None:
            # keep the play/pause glyph in the same active ink as the other
            # transport controls (was pale #C9C4B6, which read as disabled).
            # It must be the PAUSE bars, not a stop square: the button pauses
            # the pipeline and keeps the position, and a square reads as "stop
            # and go back to the beginning" to anyone who isn't guessing.
            nbicons.set_image(self._play_img, "pause" if playing else "play", 19, "#1A1916")
        if self._play_ev is not None:
            self._play_ev.set_tooltip_text(_t("Pause") if playing else "Play")
        self._refresh_now_label()
        if self.lbl_total is not None:
            self.lbl_total.set_text(self._nowtotal())

    def _on_transport(self, glyph):
        # play toggles play/pause on the pipeline; prev/next move to the
        # neighbouring track in the on-screen list and load+play it. With no
        # engine the controls are disabled, so this is a no-op guard.
        try:
            if not self._engine_ok():
                return True
            if glyph == "play":
                self._toggle_play()
            elif glyph == "next":
                self._advance(auto=False, direction=1)
            else:
                self._advance(auto=False, direction=-1)
        except Exception:
            # a transport click must never crash the app
            pass
        return True

    # ---------------- audio engine (GStreamer playbin) ----------------
    def _build_engine(self):
        """Create the playbin pipeline and wire its bus, if GStreamer is
        available. On any failure the engine stays None and the app runs in its
        disabled 'Media engine unavailable' state — never crashes."""
        if not GST_OK:
            return
        try:
            Gst.init(None)
            player = Gst.ElementFactory.make("playbin", "player")
            if player is None:
                return
            bus = player.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            bus.connect("message::error", self._on_error)
            self._player = player
            # seed the pipeline volume from the slider's initial position
            self._apply_volume()
        except Exception:
            self._player = None

    def _engine_ok(self):
        """True only when a real playbin pipeline exists to drive."""
        return GST_OK and getattr(self, "_player", None) is not None

    def _disable_engine_controls(self):
        """Make the transport, seek, volume and toggle controls inert when no
        audio engine is present (host builds without GStreamer)."""
        for w in (self._prev_ev, self._play_ev, self._next_ev,
                  self._seek, self.vol, self.shuffle, self.repeat):
            try:
                w.set_sensitive(False)
            except Exception:
                pass

    def _play_track(self, track):
        """Cue `track` in the transport and start real playback. A track with
        no engine or no readable file is cued visually only (no audio)."""
        self._current = track
        self._show_cover(track)
        self._duration_ns = 0
        if self.lbl_elapsed is not None:
            self.lbl_elapsed.set_text("0:00")
        self._set_seek_value(0)
        # A track that really starts cancels whatever message was showing: the
        # last failure is over, and leaving it up would caption the wrong song.
        self._flashing = False
        self._flash_serial += 1
        path = track.get("path") if track else None
        if not (self._engine_ok() and path and os.path.isfile(path)):
            # nothing to decode — leave the row cued but not playing
            self._playing = False
            self._loaded_path = None
            if self._player is not None:
                try:
                    self._player.set_state(Gst.State.NULL)
                except Exception:
                    pass
            self._refresh_transport()
            self._mark_playing_row()
            # A file the library lists but the disk no longer has is the common
            # case here (a track deleted or a stick pulled since the last scan),
            # and it used to leave the row cued and mute with nothing said. The
            # no-engine case says its own piece through _nowtext, so it is not
            # overwritten.
            if self._engine_ok() and track:
                self._flash(_t("“%s” is no longer where the library found it")
                            % self._track_label(path))
            return
        try:
            self._player.set_state(Gst.State.NULL)
            self._player.set_property("uri", Gst.filename_to_uri(path))
            self._apply_volume()
            self._player.set_state(Gst.State.PLAYING)
            self._loaded_path = path
            self._playing = True
            self._start_poll()
        except Exception:
            # decode/pipeline failure — fall back to a cued-but-silent state
            self._playing = False
            self._loaded_path = None
            self._refresh_transport()
            self._mark_playing_row()
            self._flash(_t("“%s” can’t be played — the file may be damaged")
                        % self._track_label(path))
            return
        self._refresh_transport()
        self._mark_playing_row()

    def _toggle_play(self):
        """Play/pause the current track, cueing the first visible track when
        nothing is loaded yet."""
        if self._loaded_path is None:
            # nothing loaded (fresh, or after a stop): (re)play the cued track,
            # falling back to the first track on screen
            target = self._current
            if target is None:
                tracks = self._visible_tracks()
                target = tracks[0] if tracks else None
            if target is not None:
                self._play_track(target)
            return
        try:
            if self._playing:
                self._player.set_state(Gst.State.PAUSED)
                self._playing = False
            else:
                self._player.set_state(Gst.State.PLAYING)
                self._playing = True
                self._start_poll()
        except Exception:
            pass
        self._refresh_transport()

    def _advance(self, auto, direction):
        """Move to another track. Shuffle governs forward moves (manual Next
        and end-of-track); Prev is sequential. On end-of-track (auto), Repeat
        loops the list; without it, the last track stops playback."""
        tracks = self._visible_tracks()
        if not tracks:
            return
        cur = self._current
        idx = None
        for j, t in enumerate(tracks):
            if t is cur:
                idx = j
                break
        if idx is None:
            self._play_track(tracks[0])
            return
        shuffle = False
        try:
            shuffle = self.shuffle.get_active()
        except Exception:
            pass
        if shuffle and direction > 0:
            self._play_track(self._shuffle_next(tracks, idx))
            return
        if auto:
            # end-of-track (sequential): loop only when Repeat is on
            if idx >= len(tracks) - 1:
                repeat = False
                try:
                    repeat = self.repeat.get_active()
                except Exception:
                    pass
                if repeat:
                    self._play_track(tracks[0])
                else:
                    self._stop_playback()
            else:
                self._play_track(tracks[idx + 1])
        else:
            # manual Prev/Next wrap around the list
            self._play_track(tracks[(idx + direction) % len(tracks)])

    def _random_other(self, tracks, idx):
        """A random track other than the one at idx (or that same one when the
        list holds a single track)."""
        if len(tracks) <= 1:
            return tracks[idx]
        j = random.randrange(len(tracks) - 1)
        if j >= idx:
            j += 1
        return tracks[j]

    def _shuffle_next(self, tracks, idx):
        """Draw once from every other track before starting a new cycle.

        Identity, rather than metadata equality, defines queue membership: two
        different files are allowed to carry the same tags.
        """
        key = tuple(id(t) for t in tracks)
        remaining = getattr(self, "_shuffle_remaining", [])
        if getattr(self, "_shuffle_queue_key", ()) != key:
            remaining = []
        if not remaining:
            current = tracks[idx]
            remaining = [t for t in tracks if t is not current]
        self._shuffle_queue_key = key
        self._shuffle_remaining = remaining
        if not remaining:
            return tracks[idx]
        j = random.randrange(len(remaining))
        return remaining.pop(j)

    def _stop_playback(self):
        """Halt the pipeline and reset the transport to a cued-but-idle state
        (the current row stays highlighted)."""
        self._playing = False
        self._loaded_path = None
        self._duration_ns = 0
        if self._player is not None:
            try:
                self._player.set_state(Gst.State.NULL)
            except Exception:
                pass
        if self.lbl_elapsed is not None:
            self.lbl_elapsed.set_text("0:00")
        self._set_seek_value(0)
        self._refresh_transport()
        self._mark_playing_row()

    def _mark_playing_row(self):
        """Move the 'playing' highlight to the row of the current track without
        rebuilding the list. Cueing/skipping a track never changes the list
        CONTENT — only which row is lit — so re-running the full _populate()
        (which tears down and rebuilds every ListBoxRow, then re-lays-out the
        whole pane) is wasted work that visibly stutters when skipping tracks on
        the no-GPU framebuffer. This only re-styles the affected rows, so GTK
        repaints just those rows. The .playing class also recolors the title via
        CSS, so nothing else needs touching."""
        try:
            cur = getattr(self, "_current", None)
            for row in self.songrows.get_children():
                song = getattr(row, "_song", None)
                ctx = row.get_style_context()
                if song is not None and song is cur:
                    ctx.add_class("playing")
                else:
                    ctx.remove_class("playing")
        except Exception:
            pass

    def _on_eos(self, _bus, _msg):
        # A track finished — advance per shuffle/repeat.
        #
        # Only when playback is still live. Bus messages are queued on the main
        # loop, so an EOS posted as the last samples drained can be delivered
        # AFTER playback was already stopped (a decode error) or after the
        # window was destroyed — and advancing then puts the pipeline straight
        # back to PLAYING, so the machine keeps making noise with no Music
        # window to stop it from, and re-arms the progress timer on widgets
        # that are gone. Nothing is loaded once playback stopped, which is what
        # tells this message apart from a real end-of-track.
        if self._closed or self._loaded_path is None:
            return
        try:
            self._advance(auto=True, direction=1)
        except Exception:
            pass

    def _on_error(self, _bus, msg):
        # a decode/pipeline error — stop cleanly rather than wedge on the track
        if self._closed:
            return
        # Say so. This used to stop in silence: the play glyph flipped back and
        # the track simply never started, which is indistinguishable from a
        # broken button. The name is read BEFORE _stop_playback, which clears
        # the loaded path.
        name = self._track_label(self._loaded_path)
        try:
            self._stop_playback()
        except Exception:
            pass
        self._flash(self._play_failure(msg) % name)

    def _track_label(self, path):
        """What to call a track in a message: its title if the library knows
        one, else the filename. Never the full path — a message is not a place
        to print where the OS keeps things."""
        if path:
            for t in self.songs:
                if t.get("path") == path and t.get("title"):
                    return t["title"]
            return os.path.splitext(os.path.basename(path))[0]
        return _t("That track")

    @staticmethod
    def _play_failure(msg):
        """One plain sentence for why a track would not play, with a "%s" for
        its name.

        Never the GError's own text. That is developer English, never
        translated, and reads "Your GStreamer installation is missing a
        plug-in." or "This appears to be a text file." — the machinery talking
        about itself. The error DOMAIN separates the only two causes a listener
        can act on differently, which is all that needs to reach them:

            gst-resource-error-quark   the file is not readable where it was
            gst-stream-error-quark     it is readable but cannot be decoded
        """
        domain = ""
        try:
            err, _debug = msg.parse_error()
            domain = getattr(err, "domain", "") or ""
        except Exception:
            pass
        if "resource" in domain:
            return _t("“%s” is no longer where the library found it")
        return _t("“%s” can’t be played — the file may be damaged")

    def _flash(self, msg, restore_ms=4000):
        """Borrow the now-playing line for a transient message, then give it
        back. Finder's _flash_status pattern, on the label this app already
        keeps current."""
        if self._closed or self._nowlbl is None:
            return
        self._flash_serial += 1
        self._flashing = True
        serial = self._flash_serial
        self._nowlbl.set_text(msg)
        GLib.timeout_add(restore_ms, self._unflash, serial)

    def _unflash(self, serial):
        # A newer message (or a track that started playing) owns the label now.
        if serial != self._flash_serial:
            return False
        self._flashing = False
        self._refresh_now_label()
        return False

    def _refresh_now_label(self):
        """Put the live now-playing text back, unless a message is showing."""
        if self._closed or self._nowlbl is None or self._flashing:
            return
        self._nowlbl.set_text(self._nowtext())

    # ---------------- progress polling + seek + volume ----------------
    def _start_poll(self):
        """Begin (or keep) the ~300ms progress poll that advances the elapsed/
        total timecodes and the seek bar from the pipeline's own clock."""
        if self._closed:
            return
        if self._poll_id == 0:
            self._poll_id = GLib.timeout_add(300, self._on_poll)

    def _on_poll(self):
        # Only poll while actually playing. Paused/stopped playback needs no
        # ticks, so the timer tears itself down (return False frees the source
        # and _start_poll re-arms it on the next play) rather than waking the
        # CPU every 300ms forever — on the no-GPU framebuffer an idle timer is
        # wasted work with nothing to show for it.
        if not self._engine_ok() or not self._playing:
            self._poll_id = 0
            return False
        try:
            self._update_progress()
        except Exception:
            pass
        return True

    def _update_progress(self):
        # while the user drags the bar, leave it under their control
        if self._user_seeking:
            return
        ok_d, dur = self._player.query_duration(Gst.Format.TIME)
        if ok_d and dur and dur > 0:
            self._duration_ns = dur
            if self.lbl_total is not None:
                self.lbl_total.set_text(self._fmt_ns(dur))
        ok_p, pos = self._player.query_position(Gst.Format.TIME)
        if ok_p and pos is not None and pos >= 0:
            if self.lbl_elapsed is not None:
                self.lbl_elapsed.set_text(self._fmt_ns(pos))
            if self._duration_ns > 0:
                frac = max(0.0, min(1.0, pos / float(self._duration_ns)))
                self._set_seek_value(frac * 1000.0)

    def _set_seek_value(self, v):
        if self._seek is None:
            return
        try:
            self._seek.set_value(max(0.0, min(1000.0, v)))
        except Exception:
            pass

    def _on_seek_press(self, *_):
        self._user_seeking = True
        return False

    def _on_seek_release(self, *_):
        self._user_seeking = False
        return False

    def _on_seek(self, _scale, _scroll, value):
        # user click/drag on the bar -> seek the pipeline to that fraction
        try:
            if self._engine_ok() and self._duration_ns > 0:
                v = max(0.0, min(1000.0, value))
                ns = int(self._duration_ns * (v / 1000.0))
                self._player.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, ns)
                # reflect the new position immediately: while paused the poll is
                # stopped, so nothing else would move the elapsed timecode.
                if self.lbl_elapsed is not None:
                    self.lbl_elapsed.set_text(self._fmt_ns(ns))
        except Exception:
            pass
        return False

    def _apply_volume(self):
        if not self._engine_ok():
            return
        try:
            v = max(0.0, min(1.0, self.vol.get_value() / 100.0))
            self._player.set_property("volume", v)
        except Exception:
            pass

    def _on_volume(self, _scale):
        self._apply_volume()

    @staticmethod
    def _fmt_ns(ns):
        """A GStreamer nanosecond count as a m:ss timecode."""
        try:
            secs = int(ns // 1000000000)
        except Exception:
            return "0:00"
        if secs < 0:
            secs = 0
        return "%d:%02d" % (secs // 60, secs % 60)

    def _toggle(self, glyph, tip=None):
        btn = Gtk.ToggleButton()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("togglebtn")
        # Shuffle and repeat are pure symbols; without a name they are two
        # controls a person can only identify by pressing them.
        if tip:
            btn.set_tooltip_text(tip)
        btn._glyph = glyph
        btn._img = nbicons.image(glyph, 16, "#1A1916")
        btn.add(btn._img)
        btn.connect("toggled", self._on_toggle)
        return btn

    def _on_toggle(self, btn):
        color = "#FCFBF8" if btn.get_active() else "#1A1916"
        nbicons.set_image(btn._img, btn._glyph, 16, color)
        self._save()

    # ---------------- view switching ----------------
    def _select(self, vid):
        self.view = vid
        # leaving for a plain library view drops any album/artist drill-down
        self._scope = None
        self._scope_origin = None
        for k, btn in self._rows.items():
            ctx = btn.get_style_context()
            if k == vid:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")
        # selecting a library view clears any highlighted/open playlist
        self._current_playlist = None
        self._set_playlist_actions(False)
        for prow in self._playlist_rows:
            prow.get_style_context().remove_class("active")
        title = dict((v[0], v[1]) for v in self.VIEWS)[vid]
        self.title.set_text(title)
        self.colhead.set_visible(vid == "songs")
        self.colhead.set_no_show_all(vid != "songs")
        # a view switch starts clean: drop any search carried over from the
        # previous view so this one never appears mysteriously pre-filtered
        # (the album/artist drill-down already clears the search the same way)
        if self._search is not None:
            self._search.set_text("")
        self._query = ""
        # render the library for the chosen view (songs / albums / artists)
        self._populate()

    # ---------------- library model + rendering ----------------
    def _load_library(self):
        """Populate the library from audio files under Home / Music. The folder
        is enumerated for supported extensions and each match named from its
        file + folder; nothing is fabricated, so an empty or absent folder
        yields an empty library (and the empty state). Fully defensive — a scan
        hiccup yields an empty library, never a crash."""
        try:
            self.songs = self._scan_music_folder()
        except Exception:
            self.songs = []
        self._current = None

    def _scan_music_folder(self):
        """Enumerate Home / Music for audio files and return them as track
        dicts (title/artist/album/time). Returns [] when the folder is missing
        or holds none. Never raises — a scan hiccup just yields []."""
        found = []
        try:
            if not os.path.isdir(MUSIC_DIR):
                return []
            paths = []
            for root, _dirs, files in os.walk(MUSIC_DIR):
                for fn in files:
                    if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                        paths.append(os.path.join(root, fn))
            for path in sorted(paths, key=lambda p: p.lower()):
                found.append(self._track_from_path(path))
        except Exception:
            return []
        return found

    def _track_from_path(self, path):
        """Derive a track dict from a file path. Honours the common
        'Artist - Title.ext' file convention and an Artist/Album/track folder
        layout; anything unknown reads as Unknown Artist / Album.

        This is the FIRST guess, not the final answer: the Discoverer runs over
        every file afterwards and replaces title/artist/album with the real
        tags (see _info_tags) and fills in the duration. Until it lands the row
        shows a dash rather than a made-up length, which is why the value
        starts blank here.

        (This docstring used to say "no tag reader is available". One arrived;
        the sentence did not keep up, and a comment that describes a limitation
        the code no longer has sends the next reader to build something that
        already exists.)"""
        base = os.path.splitext(os.path.basename(path))[0].strip()
        artist, title = "", base
        if " - " in base:
            a, t = base.split(" - ", 1)
            if a.strip() and t.strip():
                artist, title = a.strip(), t.strip()
        # album/artist from the folder layout under ~/Music
        try:
            rel = os.path.relpath(os.path.dirname(path), MUSIC_DIR)
        except Exception:
            rel = ""
        parts = [p for p in rel.split(os.sep) if p not in ("", ".")]
        album = ""
        if len(parts) >= 2:
            if not artist:
                artist = parts[-2]
            album = parts[-1]
        elif len(parts) == 1:
            album = parts[0]
            if not artist:
                artist = parts[0]
        return {"title": title or base or "Untitled",
                "artist": artist or "Unknown Artist",
                "album": album or "Unknown Album",
                "time": "",
                # absolute path to the real file — what the engine plays
                "path": path}

    # ---------------- track lengths ----------------
    # The library is enumerated from file names, which carry no duration — so
    # the Time column and the playback bar's total both read blank/0:00 for
    # every track until it was actually played. On a real library that is a
    # whole dead column. GStreamer's Discoverer reads the real length from the
    # file; it runs OFF the main loop (async, one file at a time) and every
    # answer is cached in music.json, so this is paid once per file, ever.

    @staticmethod
    def _fmt_secs(secs):
        """Whole seconds as a timecode (h:mm:ss past the hour)."""
        try:
            secs = max(0, int(secs))
        except Exception:
            return ""
        if secs >= 3600:
            return "%d:%02d:%02d" % (secs // 3600, (secs % 3600) // 60,
                                     secs % 60)
        return "%d:%02d" % (secs // 60, secs % 60)

    @staticmethod
    def _length_key(path):
        """A cheap fingerprint of the file, so a replaced/re-tagged file is
        re-read instead of showing the old length forever."""
        try:
            st = os.stat(path)
            return "%d:%d" % (st.st_size, int(st.st_mtime))
        except OSError:
            return ""

    def _apply_cached_lengths(self):
        """Fill in every length already known from a previous run."""
        try:
            self._by_path = {}
            for s in self.songs:
                path = s.get("path") or ""
                self._by_path[path] = s
                ent = self._lengths.get(path)
                if (isinstance(ent, list) and len(ent) == 2
                        and ent[0] == self._length_key(path)):
                    secs = int(ent[1] or 0)
                    s["secs"] = secs
                    s["time"] = self._fmt_secs(secs) if secs > 0 else ""
                # real tags from a previous run, so the list opens with the
                # file's own names rather than the filename guess
                tag = self._tags.get(path)
                if (isinstance(tag, list) and len(tag) == 4
                        and tag[0] == self._length_key(path)):
                    if tag[1]:
                        s["title"] = tag[1]
                    if tag[2]:
                        s["artist"] = tag[2]
                    if tag[3]:
                        s["album"] = tag[3]
        except Exception:
            pass

    def _start_length_scan(self):
        """Read the length of every track we have no cached answer for. Never
        blocks: the Discoverer works on its own thread and hands each result
        back on the main loop, where it fills that one row's Time cell."""
        if not DISC_OK or not self.songs:
            return
        todo = [s for s in self.songs
                if not s.get("time") and s.get("path")
                and "secs" not in s]
        if not todo:
            return
        try:
            Gst.init(None)
            disc = GstPbutils.Discoverer.new(5 * Gst.SECOND)
            disc.connect("discovered", self._on_discovered)
            disc.connect("finished", self._on_discover_finished)
            disc.start()
        except Exception:
            self._disc = None
            return
        self._disc = disc
        for s in todo:
            try:
                disc.discover_uri_async(Gst.filename_to_uri(s["path"]))
            except Exception:
                pass

    def _on_discovered(self, _disc, info, _err):
        """One track's length came back. Record it, cache it, and update just
        that row's Time cell — never a re-render of the list."""
        try:
            uri = info.get_uri()
            path = GLib.filename_from_uri(uri)[0] if uri else ""
        except Exception:
            path = ""
        if not path:
            return
        secs = 0
        try:
            if info.get_result() == GstPbutils.DiscovererResult.OK:
                secs = int(round((info.get_duration() or 0) / 1000000000.0))
        except Exception:
            secs = 0
        self._lengths[path] = [self._length_key(path), secs]
        self._disc_dirty = True
        text = self._fmt_secs(secs) if secs > 0 else ""
        song = self._by_path.get(path)
        # The file's OWN tags beat anything guessed from its name. Only a
        # non-empty tag overrides, so a file with half its tags filled in keeps
        # the filename-derived value for the rest instead of blanking it.
        title = artist = album = ""
        try:
            if info.get_result() == GstPbutils.DiscovererResult.OK:
                title, artist, album = _info_tags(info)
                self._save_cover(path, info)
        except Exception:                                      # noqa: BLE001
            pass
        if title or artist or album:
            self._tags[path] = [self._length_key(path), title, artist, album]
        if song is not None:
            if title:
                song["title"] = title
            if artist:
                song["artist"] = artist
            if album:
                song["album"] = album
            if title or artist or album:
                self._retitle_row(path, song)
            song["secs"] = secs
            song["time"] = text
            # the cued track's total is now known even before it plays
            if song is getattr(self, "_current", None) and self.lbl_total \
                    is not None and self._duration_ns <= 0:
                self.lbl_total.set_text(self._nowtotal())
        for lbl in self._time_labels.get(path, ()):
            try:
                lbl.set_text(text)
            except Exception:
                pass

    # ---------------- cover art ----------------
    COVER_DIR = os.path.join(HOME, ".cache", "notebook", "covers")

    @staticmethod
    def _cover_file(path):
        """Where a track's extracted cover lives. Keyed by a hash of the path so
        two files with the same basename cannot collide."""
        import hashlib
        h = hashlib.md5(path.encode("utf-8", "replace")).hexdigest()
        return os.path.join(Music.COVER_DIR, h)

    # Covers are cached at this size, not at the size they were embedded. A
    # track can carry several MB of front-cover JPEG and a library can hold
    # thousands of tracks, while the cache lives under $HOME/.cache — which on
    # the live image is a RAM overlay. Storing the original would let a big
    # library on a USB stick fill memory with artwork that is only ever drawn
    # at 52px. 256 leaves room to show it larger later.
    COVER_MAX = 256

    def _save_cover(self, path, info):
        """Write this track's embedded cover to the cache, once.

        Extracted during the discovery pass that already reads every file, so
        playing a track never has to decode it again. Silent on any failure --
        a missing cover is a placeholder icon, never an error."""
        try:
            dest = self._cover_file(path)
            if os.path.exists(dest):
                return
            data = _info_image(info)
            if not data:
                return
            os.makedirs(self.COVER_DIR, exist_ok=True)
            tmp = dest + ".new"
            if not self._write_scaled(tmp, data):
                return
            os.replace(tmp, dest)
        except Exception:                                      # noqa: BLE001
            pass

    def _write_scaled(self, tmp, data):
        """Write cover bytes to tmp, shrunk to COVER_MAX on its longest side.

        Returns whether anything was written. An image that cannot be decoded
        is not stored at all: it could not have been displayed either, and
        keeping it would only cost space and be retried as a decode failure on
        every launch."""
        ldr = GdkPixbuf.PixbufLoader()
        ldr.write(data)
        ldr.close()
        pb = ldr.get_pixbuf()
        if pb is None:
            return False
        w, h = pb.get_width(), pb.get_height()
        big = max(w, h)
        if big > self.COVER_MAX:
            scale = float(self.COVER_MAX) / big
            pb = pb.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)),
                                 GdkPixbuf.InterpType.BILINEAR)
            if pb is None:
                return False
        pb.savev(tmp, "png", [], [])
        return True

    def _cover_pixbuf(self, path, size=52):
        """The track's cover, scaled for the SCREEN's real pixel density.

        `size` stays in logical units — it is a layout number and every caller
        should keep thinking in those — but the pixbuf is decoded at size*scale
        so a 2x panel gets a genuinely 104px cover rather than a 52px one that
        the compositor stretches. Album art sits right beside the track title,
        which is sharp, and a soft square next to sharp text is the exact
        contrast that reads as cheap.

        The on-disk cache is capped at COVER_MAX (256px) on its longest side, so
        there is real detail available to scale from at 2x and this costs
        nothing but the decode. Cached in memory per (path, size, scale)."""
        sf = nbicons.scale_factor()
        key = (path, size, sf)
        if key in self._art_cache:
            got = self._art_cache[key]
            return got or None
        px = max(1, size * sf)
        pb = None
        try:
            f = self._cover_file(path)
            if os.path.isfile(f):
                ldr = GdkPixbuf.PixbufLoader()
                with open(f, "rb") as fh:
                    ldr.write(fh.read())
                ldr.close()
                raw = ldr.get_pixbuf()
                if raw is not None:
                    pb = raw.scale_simple(px, px,
                                          GdkPixbuf.InterpType.BILINEAR)
        except Exception:                                      # noqa: BLE001
            pb = None
        self._art_cache[key] = pb or False
        return pb

    def _show_cover(self, track):
        """Put the cued track's cover in the playbar, or the placeholder."""
        img = getattr(self, "_art_img", None)
        if img is None:
            return
        pb = None
        try:
            path = (track or {}).get("path")
            if path:
                pb = self._cover_pixbuf(path)
        except Exception:                                      # noqa: BLE001
            pb = None
        try:
            if pb is not None:
                # The pixbuf is already at DEVICE resolution (see
                # _cover_pixbuf), so it must be handed over as a surface
                # carrying the scale -- set_from_pixbuf would place those extra
                # pixels as logical ones and draw the cover at twice its size.
                nbicons.set_image_pixbuf(img, pb)
            else:
                nbicons.set_image(img, "music", 22, "#C9C4B6")
        except Exception:                                      # noqa: BLE001
            pass

    def _retitle_row(self, path, song):
        """Refresh the visible title/artist cells for one row after its real
        tags arrived, without re-rendering the whole list."""
        try:
            for lbl, field in self._tag_labels.get(path, ()):
                lbl.set_text(song.get(field) or "")
        except Exception:                                      # noqa: BLE001
            pass
        try:
            if song is getattr(self, "_current", None):
                self._refresh_transport()
        except Exception:                                      # noqa: BLE001
            pass

    def _on_discover_finished(self, _disc):
        """Every queued track has been read — persist the answers once (not
        once per file) so the next launch shows the times immediately."""
        try:
            if self._disc_dirty:
                self._disc_dirty = False
                self._save()
            # Tags can change every library ordering/grouping, not just album
            # lengths. Rebuild the active library view once after the pass;
            # playlists retain their deliberate user order.
            if self.view in ("songs", "albums", "artists", "scope"):
                self._populate()
        except Exception:
            pass

    def _stop_length_scan(self):
        disc, self._disc = self._disc, None
        if disc is not None:
            try:
                disc.stop()
            except Exception:
                pass

    def _match(self, song, q):
        """True when a track matches the (already lower-cased) search text."""
        if not q:
            return True
        return (q in song.get("title", "").lower()
                or q in song.get("artist", "").lower()
                or q in song.get("album", "").lower())

    @staticmethod
    def _sort_key(value):
        """A reader-facing key: ignore leading articles, case and accents."""
        text = unicodedata.normalize("NFKD", str(value or "").strip())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.casefold()
        for article in ("the ", "an ", "a "):
            if text.startswith(article):
                text = text[len(article):]
                break
        return text

    def _ordered_songs(self, songs=None):
        return sorted(self.songs if songs is None else songs,
                      key=lambda s: (self._sort_key(s.get("title")),
                                     self._sort_key(s.get("artist")),
                                     self._sort_key(s.get("album"))))

    def _populate(self):
        """Build the main pane's rows for the current view — the WHOLE view, not
        the search's subset; the search is a filter over these rows (see
        _row_filter). Falls back to the empty state when the library is empty.
        Fully defensive — a render hiccup must never stop the window opening."""
        try:
            self._update_counts()
            # a completely empty library shows the hero empty state; the column
            # header is hidden so it never floats above an empty pane
            if not self.songs:
                self.colhead.set_no_show_all(True)
                self.colhead.hide()
                self._show_empty(True)
                return
            self._show_empty(False)
            for c in self.songrows.get_children():
                self.songrows.remove(c)
            self._time_labels = {}
            self._tag_labels = {}

            if self.view == "albums":
                rows = self._album_rows()
            elif self.view == "artists":
                rows = self._artist_rows()
            elif self.view == "scope":
                # drilled into one album/artist — render that group's tracks
                rows = self._scope_rows()
            elif self.view == "songs":
                rows = self._song_rows()
            else:
                # a playlist is selected — render its tracks (may be empty)
                rows = self._playlist_track_rows()

            for r in rows:
                self.songrows.add(r)
            # One placeholder row lives at the end of every list, shown by the
            # filter only when nothing is on screen — so "no matches" appears
            # and disappears as the user types without rebuilding anything.
            self._empty_row = self._placeholder_row("")
            self.songrows.add(self._empty_row)
            self._refresh_filter()
            self.songrows.show_all()

            self._refresh_now_label()
            if self.lbl_total is not None:
                self.lbl_total.set_text(self._nowtotal())
        except Exception:
            # never let rendering crash the app; leave whatever is on screen
            pass

    def _row_filter(self, row):
        """The ListBox's filter: does this row survive the current search?"""
        try:
            if row is self._empty_row:
                return self._match_count == 0
            q = (self._query or "").strip().lower()
            if not q:
                return True
            song = getattr(row, "_song", None)
            if song is not None:
                return self._match(song, q)
            return q in getattr(row, "_filter_text", "")
        except Exception:
            return True

    def _refresh_filter(self):
        """Re-count what the search matches, retitle the placeholder, and re-run
        the filter over the rows already built."""
        try:
            q = (self._query or "").strip().lower()
            n = 0
            for row in self.songrows.get_children():
                if row is self._empty_row:
                    continue
                song = getattr(row, "_song", None)
                if song is not None:
                    if self._match(song, q):
                        n += 1
                elif not q or q in getattr(row, "_filter_text", ""):
                    n += 1
            self._match_count = n
            if self._empty_lbl is not None:
                self._empty_lbl.set_text(self._empty_note(q))
            self.songrows.invalidate_filter()
        except Exception:
            pass

    def _empty_note(self, q):
        # translated here, not at the call site: this note is only ever applied
        # with a bare set_text() as the search/view changes, so returning raw
        # English left the empty state in English on a localised install
        # Every branch names the way out as well as the state. A note that only
        # reports the absence leaves the reader looking at a blank pane with
        # nothing to press — and in all three of these cases there IS something
        # to press, it just isn't obvious which.
        if q:
            return (_t("No tracks match the search.") + "\n"
                    + _t("Clear the search to see all tracks."))
        if self.view is None:
            return _t("Playlist empty. Add tracks from the library with +.")
        if self.view == "scope" and self._scope:
            return (_t("No tracks in %s.") % self._scope_label
                    + "\n" + _t("Choose another view in the sidebar."))
        return (_t("No tracks to display.") + "\n"
                + _t("Choose another view in the sidebar."))

    def _show_empty(self, show):
        """Toggle between the hero empty state and the populated list."""
        if show:
            self._songscroll.set_no_show_all(True)
            self._songscroll.hide()
            self.empty.set_no_show_all(False)
            self.empty.show_all()
        else:
            self.empty.set_no_show_all(True)
            self.empty.hide()
            self._songscroll.set_no_show_all(False)
            self._songscroll.show_all()

    def _sync_colhead_margin(self):
        """Right-margin the column header by the live scrollbar width so the
        header and the rows beneath it stay on one grid."""
        try:
            # Measure the width the rows ACTUALLY lost rather than asking the
            # scrollbar how wide it would like to be: the two differ (the
            # scrollbar carries margins of its own), and only the real geometry
            # lines the columns up.
            outer = self._songscroll.get_allocation().width
            inner = self.songrows.get_allocation().width
            w = outer - inner
            if not (0 <= w <= 60):        # not laid out yet, or absurd
                vsb = self._songscroll.get_vscrollbar()
                w = 0
                if vsb is not None and vsb.get_visible() and vsb.get_child_visible():
                    w = vsb.get_preferred_width()[1]
            # Grow the header's TRAILING SPACER by that much rather than setting
            # a margin on the header: the spacer is a fixed-width cell in the
            # same expand layout the rows use, so Title absorbs the change and
            # every following column lands on the row grid. (A margin is applied
            # by the parent and does not reliably re-run this box's own
            # expand arithmetic.)
            want = 30 + w
            if self._colhead_spacer.get_size_request()[0] != want:
                self._colhead_spacer.set_size_request(want, -1)
        except Exception:
            pass

    def _song_rows(self):
        return [self._song_row(s) for s in self._ordered_songs()]

    def _song_row(self, s, in_playlist=None):
        # a track row whose columns line up beneath the Title/Artist/Album/Time
        # header (same expand + fixed widths as _main's colhead). When rendered
        # inside an open playlist, the trailing control removes the track from
        # that playlist instead of offering to add it to one.
        row = Gtk.ListBoxRow()
        row._song = s
        if s is getattr(self, "_current", None):
            row.get_style_context().add_class("playing")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      spacing=self.COLUMN_GAP)
        box.get_style_context().add_class("songrow")

        title = Gtk.Label(label=s.get("title", ""), xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.get_style_context().add_class("s-title")
        box.pack_start(title, True, True, 0)

        artist = Gtk.Label(label=s.get("artist", ""), xalign=0)
        self._fixed_cell(artist)
        box.pack_start(artist, False, False, 0)

        album = Gtk.Label(label=s.get("album", ""), xalign=0)
        self._fixed_cell(album)
        box.pack_start(album, False, False, 0)

        dur = Gtk.Label(label=s.get("time", ""), xalign=1)
        dur.set_size_request(self._time_w, -1)
        dur.get_style_context().add_class("s-time")
        box.pack_start(dur, False, False, 0)
        # keep the Time cell so the background length scan can fill it in when
        # the real duration comes back, without rebuilding the row
        self._time_labels.setdefault(s.get("path", ""), []).append(dur)
        # same idea for the name cells: the library is first named from the
        # file path, and the discovery pass replaces that with the file's own
        # tags a moment later
        _p = s.get("path", "")
        if _p:
            self._tag_labels.setdefault(_p, []).extend(
                ((title, "title"), (artist, "artist"), (album, "album")))

        # trailing per-row control. In the library views it files the track
        # into a playlist (+); inside an open playlist it removes the track from
        # that playlist (trash) so a playlist is fully editable, not append-only.
        trail = Gtk.Button()
        trail.set_relief(Gtk.ReliefStyle.NONE)
        trail.get_style_context().add_class("addbtn")
        trail.set_valign(Gtk.Align.CENTER)
        trail.set_size_request(30, 30)
        if in_playlist is not None:
            trail.set_tooltip_text(_t("Remove from Playlist"))
            trail.add(nbicons.image("trash", 14, "#9A9484"))
            trail.connect("clicked", self._on_remove_clicked, s, in_playlist)
        else:
            trail.set_tooltip_text(_t("Add to Playlist"))
            trail.add(nbicons.image("plus", 14, "#9A9484"))
            trail.connect("clicked", self._on_add_clicked, s)
        box.pack_start(trail, False, False, 0)

        row.add(box)
        return row

    def _fixed_cell(self, label):
        """Pin an Artist/Album cell to exactly _cell_w so it lines up with its
        column heading.

        set_size_request only sets a MINIMUM, and a GtkBox hands each
        non-expanding child its NATURAL width before giving the remainder to the
        expanding one — an ellipsized label's natural width is its FULL text, so
        a long artist/album name grew its cell past the heading above it (and
        starved the Title column down to an ellipsis). Capping the natural width
        in characters, below the narrowest _cell_w, collapses natural back onto
        the pixel minimum, so every row lands on the header's grid."""
        label.set_size_request(self._cell_w, -1)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(20)
        label.get_style_context().add_class("s-cell")

    def _playlist_track_rows(self):
        # rows for the playlist currently open in the main pane — the same track
        # rows the Songs view uses, in the order the user built
        name = getattr(self, "_current_playlist", None)
        return [self._song_row(s, in_playlist=name)
                for s in self._playlist_tracks.get(name, [])]

    def _on_add_clicked(self, button, song):
        # open a small menu of playlists to drop this track into. With no
        # playlists yet, offer to make one on the spot so the flow never
        # dead-ends. (A native Gtk.Menu — no animated Stack/Revealer.)
        try:
            menu = Gtk.Menu()
            head = Gtk.MenuItem(label=_t("Add to playlist"))
            head.set_sensitive(False)
            menu.append(head)
            menu.append(Gtk.SeparatorMenuItem())
            if self._playlists:
                for pname in self._playlists:
                    item = Gtk.MenuItem(label=pname)
                    item.connect(
                        "activate",
                        lambda _mi, s=song, n=pname: self._add_to_playlist(s, n))
                    menu.append(item)
            else:
                item = Gtk.MenuItem(label=_t("New Playlist"))
                item.connect(
                    "activate",
                    lambda _mi, s=song: self._add_to_new_playlist(s))
                menu.append(item)
            menu.show_all()
            nbapp.popup_at(menu, widget=button, anchor="widget-sw")
        except Exception:
            # the add affordance must never crash a row click
            pass

    def _add_to_playlist(self, song, name):
        # append the track to the named playlist (deduped by identity) and, if
        # that playlist is the one currently on screen, reveal the new track
        try:
            tracks = self._playlist_tracks.setdefault(name, [])
            if not any(t is song for t in tracks):
                tracks.append(song)
                self._save()   # persist the new membership
            if (self.view is None
                    and getattr(self, "_current_playlist", None) == name):
                has = bool(tracks)
                self.colhead.set_visible(has)
                self.colhead.set_no_show_all(not has)
                if has:
                    self.colhead.show_all()
                self._populate()
        except Exception:
            # a failed add must never crash the app
            pass

    def _add_to_new_playlist(self, song):
        # create a fresh playlist, then file this track into it
        try:
            self._new_playlist()
            if self._playlists:
                self._add_to_playlist(song, self._playlists[-1])
        except Exception:
            pass

    def _on_remove_clicked(self, _button, song, name):
        try:
            self._remove_from_playlist(song, name)
        except Exception:
            # a failed remove must never crash a row click
            pass

    def _remove_from_playlist(self, song, name):
        # drop the track (by identity) from the named playlist and, if that
        # playlist is on screen, re-render it — hiding the column header again
        # when the removal empties the playlist
        tracks = self._playlist_tracks.get(name)
        if not tracks:
            return
        if hasattr(self, "undo"):
            self.undo.checkpoint("Remove Track")
        self._playlist_tracks[name] = [t for t in tracks if t is not song]
        self._save()   # persist the new membership
        if (self.view is None
                and getattr(self, "_current_playlist", None) == name):
            has = bool(self._playlist_tracks[name])
            self.colhead.set_visible(has)
            self.colhead.set_no_show_all(not has)
            self._populate()
        if hasattr(self, "undo"):
            self.undo.commit()

    @staticmethod
    def _album_id(s):
        """An album's identity: the ARTIST as well as the title. Keying on the
        title alone merged every artist's "Greatest Hits" (or, in a foldered
        library, every "Volume 1") into one row credited to whichever artist
        happened to be scanned first — one 8-track album read as 39 songs."""
        return (s.get("artist") or "Unknown Artist",
                s.get("album") or "Unknown Album")

    def _album_rows(self):
        order, index = [], {}
        for s in self.songs:
            key = self._album_id(s)
            if key not in index:
                index[key] = {"count": 0, "secs": 0, "whole": True}
                order.append(key)
            a = index[key]
            a["count"] += 1
            secs = int(s.get("secs", 0) or 0)
            if secs > 0:
                a["secs"] += secs
            else:
                a["whole"] = False      # a length we haven't read yet
        rows = []
        for key in sorted(order, key=lambda k: (self._sort_key(k[1]),
                                                self._sort_key(k[0]))):
            artist, alb = key
            a = index[key]
            n = a["count"]
            right = "%d song%s" % (n, "" if n == 1 else "s")
            if a["whole"] and a["secs"] > 0:
                right += "  ·  " + self._fmt_secs(a["secs"])
            rows.append(self._meta_row(alb, artist, right,
                                       scope=("album", key), label=alb))
        return rows

    def _artist_rows(self):
        order, index = [], {}
        for s in self.songs:
            art = s.get("artist") or "Unknown Artist"
            if art not in index:
                index[art] = {"albums": set(), "count": 0}
                order.append(art)
            index[art]["albums"].add(s.get("album") or "")
            index[art]["count"] += 1
        rows = []
        for art in sorted(order, key=self._sort_key):
            a = index[art]
            na, ns = len(a["albums"]), a["count"]
            sub = "%d album%s · %d song%s" % (
                na, "" if na == 1 else "s", ns, "" if ns == 1 else "s")
            rows.append(self._meta_row(art, sub, "", scope=("artist", art),
                                       label=art))
        return rows

    def _meta_row(self, title, sub, right, scope=None, label=""):
        # a two-line entry used by the Albums and Artists views. When given a
        # scope it becomes activatable and drills into that group's tracks.
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row._scope = scope
        row._scope_label = label
        # what the search matches this row on (the filter never rebuilds rows)
        row._filter_text = ("%s %s" % (title, sub)).lower()
        if scope is not None:
            row.get_style_context().add_class("metarow")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.get_style_context().add_class("songrow")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=title, xalign=0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.get_style_context().add_class("m-title")
        left.pack_start(t, False, False, 0)
        if sub:
            su = Gtk.Label(label=sub, xalign=0)
            su.set_ellipsize(Pango.EllipsizeMode.END)
            su.get_style_context().add_class("m-sub")
            left.pack_start(su, False, False, 0)
        box.pack_start(left, True, True, 0)
        if right:
            r = Gtk.Label(label=right, xalign=1)
            r.get_style_context().add_class("m-right")
            box.pack_start(r, False, False, 0)
        row.add(box)
        return row

    def _placeholder_row(self, text):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        lbl = Gtk.Label(label=text, xalign=0.5)
        lbl.set_line_wrap(True)
        lbl.get_style_context().add_class("listempty")
        row.add(lbl)
        # kept so the note can be retitled as the search changes, in place
        self._empty_lbl = lbl
        return row

    def _update_counts(self):
        # reflect the live library in the sidebar view counters
        try:
            counts = {
                "songs": len(self.songs),
                # an album is an (artist, album) pair, exactly as the Albums
                # view lists it — counting titles alone made the sidebar
                # disagree with the list it opens
                "albums": len({self._album_id(s) for s in self.songs}),
                "artists": len({s.get("artist") or "" for s in self.songs}),
            }
            for vid, btn in self._rows.items():
                lbl = getattr(btn, "_count_lbl", None)
                if lbl is not None:
                    lbl.set_text(str(counts.get(vid, 0)))
        except Exception:
            pass

    def _nowtext(self):
        # what the playback bar reads: the neutral note when no engine, else the
        # cued track or the bare state. "Media engine" is developer language —
        # say plainly that sound is unavailable (only reachable on a damaged
        # install; the audio engine ships with the system).
        # every string here is translated at the point it is BUILT: the playback
        # bar re-reads this on each track change with a bare set_text(), so an
        # untranslated return value silently snapped the bar back to English on
        # a localised install the moment anything was played.
        if not self._engine_ok():
            return _t("Sound isn’t available on this system")
        cur = getattr(self, "_current", None)
        playing = getattr(self, "_playing", False)
        idle = _t("Playing") if playing else _t("Nothing playing")
        if not cur:
            return idle
        label = "%s — %s" % (cur.get("title", ""), cur.get("artist", ""))
        return label.strip(" —") or idle

    def _nowtotal(self):
        # the total-duration timecode: the live pipeline duration while a track
        # is loaded, else the length already read for the cued track (so cueing
        # one shows how long it is before a note has been played), else 0:00
        cur = getattr(self, "_current", None)
        if self._engine_ok() and self._duration_ns > 0 and cur is not None:
            return self._fmt_ns(self._duration_ns)
        if cur is not None and int(cur.get("secs", 0) or 0) > 0:
            return self._fmt_secs(cur["secs"])
        return "0:00"

    def _on_song_activated(self, _listbox, row):
        # a track row cues + plays; an album/artist row drills into its tracks
        try:
            scope = getattr(row, "_scope", None)
            if scope is not None:
                self._open_scope(scope[0], scope[1],
                                 getattr(row, "_scope_label", ""))
                return
            s = getattr(row, "_song", None)
            if s is None:
                return
            # load + play the chosen track through the engine (a track with no
            # readable file is simply cued, not played)
            self._play_track(s)
        except Exception:
            pass

    def _open_scope(self, kind, value, label=""):
        # drill from an Albums/Artists row into that group's filtered tracks,
        # reusing the Songs-view rendering. The originating sidebar view stays
        # highlighted and the header names the album/artist.
        try:
            origin = "albums" if kind == "album" else "artists"
            self._scope = (kind, value)
            self._scope_label = label or (value if isinstance(value, str)
                                          else value[-1])
            self._scope_origin = origin
            self.view = "scope"
            self._current_playlist = None
            self._set_playlist_actions(False)
            for k, btn in self._rows.items():
                ctx = btn.get_style_context()
                if k == origin:
                    ctx.add_class("active")
                else:
                    ctx.remove_class("active")
            for prow in self._playlist_rows:
                prow.get_style_context().remove_class("active")
            self.title.set_text(self._scope_label)
            # clear any lingering search so the whole group shows
            if self._search is not None:
                self._search.set_text("")
            self._query = ""
            self.colhead.set_visible(True)
            self.colhead.set_no_show_all(False)
            self.colhead.show_all()
            self._populate()
        except Exception:
            pass

    def _scope_matches(self, s):
        # True when a track belongs to the active album/artist scope. An album
        # scope carries (artist, album) — see _album_id — so drilling into one
        # artist's "Volume 1" never pulls in another artist's.
        kind, value = self._scope or (None, None)
        if kind == "album":
            return self._album_id(s) == value
        if kind == "artist":
            return (s.get("artist") or "Unknown Artist") == value
        return False

    def _scope_rows(self):
        return [self._song_row(s) for s in self._ordered_songs(
                [s for s in self.songs if self._scope_matches(s)])]

    def _visible_tracks(self):
        # the ordered list of track dicts currently on screen, used by the
        # prev/next transport controls. Albums/Artists views (which list groups,
        # not tracks) fall back to the whole filtered library.
        q = (self._query or "").strip().lower()
        if self.view == "scope":
            return self._ordered_songs([s for s in self.songs
                                        if self._scope_matches(s)
                                        and self._match(s, q)])
        if self.view is None:
            name = getattr(self, "_current_playlist", None)
            return [s for s in self._playlist_tracks.get(name, [])
                    if self._match(s, q)]
        return self._ordered_songs([s for s in self.songs
                                    if self._match(s, q)])

    def _on_search(self, entry):
        # live-filter the rows already on screen as the search text changes —
        # no row is built or destroyed here (see _row_filter)
        try:
            self._query = entry.get_text()
            self._refresh_filter()
        except Exception:
            pass

    # ---------------- persistence ----------------
    def _track_dict(self, t):
        """The on-disk shape of a track — the rendered fields plus the file
        path so a saved playlist track can still play after a relaunch."""
        return {"title": str(t.get("title", "")),
                "artist": str(t.get("artist", "")),
                "album": str(t.get("album", "")),
                "time": str(t.get("time", "") or ""),
                "path": str(t.get("path", "") or "")}

    def _link_track(self, t):
        """Re-link a saved track to the live library object with the same
        title/artist/album (so highlight + dedupe by identity keep working);
        fall back to the saved dict when the file is no longer in the library."""
        title = str(t.get("title", ""))
        artist = str(t.get("artist", ""))
        album = str(t.get("album", ""))
        for s in self.songs:
            if (s.get("title", "") == title and s.get("artist", "") == artist
                    and s.get("album", "") == album):
                return s
        return self._track_dict(t)

    def _load(self):
        """Restore saved playlists (names + track lists) from music.json into
        self._loaded_playlists, applied to the sidebar once it is built. Must
        run after the library is populated so saved tracks can be re-linked."""
        self._loaded_playlists = []
        # Closing the window is not consent to replace a store we could not
        # parse.  Stay read-only for this run after damage; a missing file is
        # the normal first-run case and is handled separately below.
        self._store_load_ok = False
        try:
            with open(CFG_FILE) as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            damaged = False
            # be strict about the shapes: a garbage file whose "playlists" is a
            # string (or "tracks" a list) must not iterate characters or crash —
            # it just yields no saved playlists
            # What was open last time. A view id that this build no longer has
            # (or a number, or nothing at all) falls back to Songs; the saved
            # playlist is kept as a bare name and only honoured in
            # _restore_selection if a playlist by that name still exists.
            self._saved_view = nbstate.choice(
                data.get("view"), [v[0] for v in self.VIEWS], "songs")
            saved_pl = data.get("playlist")
            self._saved_playlist = (saved_pl.strip() or None
                                    if isinstance(saved_pl, str) else None)
            self._saved_shuffle = (data.get("shuffle")
                                   if isinstance(data.get("shuffle"), bool)
                                   else False)
            self._saved_repeat = (data.get("repeat")
                                  if isinstance(data.get("repeat"), bool)
                                  else False)
            names = data.get("playlists")
            tracks = data.get("tracks")
            if not isinstance(names, list):
                if "playlists" in data:
                    damaged = True
                names = []
            if not isinstance(tracks, dict):
                if "tracks" in data:
                    damaged = True
                tracks = {}
            seen = set()
            for name in names:
                if not isinstance(name, str):
                    damaged = True
                name = str(name).strip()
                if not name or name in seen:
                    continue
                seen.add(name)      # never restore a duplicate playlist name
                tlist = tracks.get(name)
                if not isinstance(tlist, list):
                    if name in tracks:
                        damaged = True
                    tlist = []
                if any(not isinstance(t, dict) for t in tlist):
                    damaged = True
                linked = [self._link_track(t) for t in tlist
                          if isinstance(t, dict)]
                self._loaded_playlists.append((name, linked))
            # cached track lengths: {path: [stat-key, seconds]}. Anything that
            # isn't that exact shape is simply dropped and re-read.
            lens = data.get("lengths")
            if isinstance(lens, dict):
                for path, ent in lens.items():
                    if (isinstance(ent, list) and len(ent) == 2
                            and isinstance(ent[0], str)):
                        try:
                            self._lengths[str(path)] = [ent[0], int(ent[1])]
                        except (TypeError, ValueError):
                            damaged = True
                            pass
                    else:
                        damaged = True
            elif "lengths" in data:
                damaged = True
            # cached tags: {path: [stat-key, title, artist, album]}. Same
            # shape-or-drop rule, so an older music.json with no "tags" key
            # simply re-reads them on the next discovery pass.
            tg = data.get("tags")
            if isinstance(tg, dict):
                for path, ent in tg.items():
                    if (isinstance(ent, list) and len(ent) == 4
                            and all(isinstance(x, str) for x in ent)):
                        self._tags[str(path)] = list(ent)
                    else:
                        damaged = True
            elif "tags" in data:
                damaged = True
            self._store_load_ok = not damaged
        except FileNotFoundError:
            self._store_load_ok = True
        except Exception:
            # no file yet / unreadable — start with no saved playlists
            self._loaded_playlists = []

    def _save(self):
        """Persist playlists + their track lists to music.json. Called on every
        mutation and on destroy. Never crashes the app on an I/O error.

        A write is skipped while _restore_selection is running: putting the
        sidebar back walks the same setters a click walks, and restoration must
        never be recorded as a change the user made."""
        if self._restoring.active or not getattr(self, "_store_load_ok", False):
            return
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            # only keep lengths for files still in the library, so the cache
            # can never grow without bound as folders come and go
            live = set(self._by_path)
            data = {
                "playlists": list(self._playlists),
                "tracks": {n: [self._track_dict(t)
                               for t in self._playlist_tracks.get(n, [])]
                           for n in self._playlists},
                "lengths": {p: v for p, v in self._lengths.items()
                            if p in live},
                # the file's own tags, cached exactly like the lengths so the
                # next launch shows real names immediately instead of the
                # filename guess until the discovery pass catches up
                "tags": {p: v for p, v in self._tags.items() if p in live},
                # Where the library was left. Only a real library view is
                # stored — an album/artist drill-down ("scope") is a transient
                # place inside one, not somewhere to reopen on.
                "view": nbstate.choice(self.view, [v[0] for v in self.VIEWS],
                                       "songs"),
                "playlist": self._current_playlist or "",
                # Read the live toggle when the transport is built; before that
                # (or on a bare test instance) fall back to the value last
                # loaded, so a save can never crash the whole write on a
                # half-constructed window.
                "shuffle": bool(self.shuffle.get_active()) if hasattr(
                    self, "shuffle") else bool(getattr(self, "_saved_shuffle",
                                                       False)),
                "repeat": bool(self.repeat.get_active()) if hasattr(
                    self, "repeat") else bool(getattr(self, "_saved_repeat",
                                                      False)),
            }
            nbapp.atomic_write_json(CFG_FILE, data)
        except Exception:
            pass

    def _undo_snapshot(self):
        return {
            "playlists": list(self._playlists),
            "tracks": {name: [self._track_dict(t) for t in tracks]
                       for name, tracks in self._playlist_tracks.items()},
            "playlist": self._current_playlist,
            "view": self.view,
        }

    def _restore_undo_snapshot(self, state):
        names = list(state.get("playlists", []))
        saved = state.get("tracks", {})
        tracks = {name: [self._link_track(copy.deepcopy(t)) for t in
                         saved.get(name, []) if isinstance(t, dict)]
                  for name in names}
        # Rebuild the sidebar rows when a real window owns them. Model-only
        # execution (the selftests) deliberately has no GTK widgets.
        if hasattr(self, "_pl_box"):
            for row in list(getattr(self, "_playlist_rows", [])):
                try:
                    self._pl_box.remove(row)
                except Exception:
                    pass
            self._playlists = []
            self._playlist_rows = []
            self._playlist_tracks = {}
            for name in names:
                self._create_playlist(name, tracks.get(name, []))
            try:
                self._none.set_no_show_all(bool(names))
                self._none.set_visible(not names)
            except Exception:
                pass
            current = state.get("playlist")
            if current in self._playlists:
                i = self._playlists.index(current)
                self._select_playlist(self._playlist_rows[i], current)
            else:
                self._select(state.get("view", "songs"))
        else:
            self._playlists = names
            self._playlist_tracks = tracks
            self._current_playlist = state.get("playlist")
            self.view = state.get("view", "songs")
        self._save()

    def _on_destroy(self, *_):
        # tear the engine down cleanly: stop the poll, the length scan and the
        # pipeline. The flag goes up FIRST, so a bus message still queued on
        # the main loop cannot restart any of them behind us.
        self._closed = True
        self._playing = False
        self._loaded_path = None
        try:
            if self._poll_id:
                GLib.source_remove(self._poll_id)
                self._poll_id = 0
        except Exception:
            pass
        self._stop_length_scan()
        try:
            if self._player is not None:
                self._player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._save()

    # ---------------- menus ----------------
    def menu_items(self, name):
        if name == "File":
            # A single-store app (the library is Home / Music, playlists are
            # autosaved): no New/Open/Save/Save As of documents, but the create
            # and delete actions for the one thing the user DOES make here — a
            # playlist — belong in File, as they do in every other single-store
            # app in the OS. They used to be reachable only from an unlabelled
            # pair of icons in the playlist header, and New Playlist sat under
            # View, which is for choosing what the pane shows.
            # Rename/Delete both raise a card before anything happens, so both
            # take an ellipsis; New Playlist makes one immediately, so it does
            # not. Both grey out when no playlist is open.
            have_pl = bool(getattr(self, "_current_playlist", None))
            return [("New Playlist", self._new_playlist),
                    ("Rename Playlist…",
                     self._rename_current_playlist if have_pl else None),
                    ("Delete Playlist…",
                     self._delete_current_playlist if have_pl else None),
                    nbapp.SEP,
                    # the library lives in Home / Music; opening it creates it
                    ("Open Music Folder", self._open_music_folder),
                    nbapp.SEP,
                    ("Close    Esc", self.close)]
        if name == "Edit":
            return nbapp.undo_menu_items(self.undo)
        if name == "View":
            # what the main pane shows — nothing else
            return [("Songs", lambda: self._select("songs")),
                    ("Albums", lambda: self._select("albums")),
                    ("Artists", lambda: self._select("artists"))]
        if name == "Controls":
            # drive the real playback-bar widgets (toggles + volume slider).
            # With no audio engine those same controls are inert and greyed out,
            # so present the menu entries disabled to match rather than offering
            # actions that would silently do nothing.
            if not self._engine_ok():
                return [("Shuffle", None), ("Repeat", None), nbapp.SEP,
                        ("Volume Up", None), ("Volume Down", None),
                        ("Mute", None)]
            muted = False
            try:
                muted = self.vol.get_value() <= 0
            except Exception:
                pass
            return [("Shuffle", lambda: self._menu_toggle(self.shuffle)),
                    ("Repeat", lambda: self._menu_toggle(self.repeat)),
                    nbapp.SEP,
                    ("Volume Up", lambda: self._menu_volume(+5)),
                    ("Volume Down", lambda: self._menu_volume(-5)),
                    # the label tracks state so a muted player offers "Unmute"
                    ("Unmute" if muted else "Mute",
                     lambda: self._menu_volume(None))]
        return super().menu_items(name)

    def _menu_toggle(self, btn):
        # flip a playback-bar ToggleButton (recolors its glyph via _on_toggle)
        try:
            btn.set_active(not btn.get_active())
        except Exception:
            pass

    def _menu_volume(self, delta):
        # nudge the volume slider (clamped 0..100), or with delta=None TOGGLE
        # mute: silence to 0 and, on a second call, restore the level the user
        # was last at, so Mute is a reversible switch rather than a one-way trip
        # to silence with no way back but dragging the slider up by hand.
        try:
            if delta is None:
                cur = self.vol.get_value()
                if cur > 0:
                    self._premute_vol = cur
                    self.vol.set_value(0)
                else:
                    self.vol.set_value(getattr(self, "_premute_vol", 70) or 70)
                return
            v = max(0, min(100, self.vol.get_value() + delta))
            self.vol.set_value(v)
        except Exception:
            pass

    # ---------------- playlist rename / delete ----------------
    def _rename_current_playlist(self):
        name = getattr(self, "_current_playlist", None)
        if not name:
            return
        self._prompt_name(
            "Rename Playlist", name, "Rename",
            lambda new: self._apply_rename(name, new),
            validate=lambda new: self._rename_problem(name, new))

    def _rename_problem(self, old, new):
        # inline validation for the Rename dialog: a blank name is rejected, an
        # unchanged name simply closes, and a name already taken by ANOTHER
        # playlist is refused with a clear reason (it would collide the by-name
        # track map) instead of the dialog closing on a silent no-op.
        new = (new or "").strip()
        if not new:
            return "Enter a name for the playlist."
        if new == old:
            return None
        if new in self._playlists:
            return "A playlist named “%s” already exists." % new
        return None

    def _apply_rename(self, old, new):
        # apply a validated rename across the parallel playlist structures
        try:
            new = (new or "").strip()
            if not new or new == old or old not in self._playlists:
                return
            if new in self._playlists:
                # a name already in use would collide the track map — ignore
                return
            idx = self._playlists.index(old)
            self._playlists[idx] = new
            self._playlist_tracks[new] = self._playlist_tracks.pop(old, [])
            try:
                row = self._playlist_rows[idx]
                row._name_lbl.set_text(new)
                row._pl_name = new     # keep the row's live name in sync
                row.set_tooltip_text(new)   # the row truncates, so does its hint
            except Exception:
                pass
            if getattr(self, "_current_playlist", None) == old:
                self._current_playlist = new
                self.title.set_text(new)
                # re-render so the open playlist's rows carry the new name (their
                # per-row remove control is keyed by it)
                self._populate()
            self._save()
        except Exception:
            pass

    def _delete_current_playlist(self):
        name = getattr(self, "_current_playlist", None)
        if not name:
            return
        if hasattr(self, "undo"):
            self.undo.checkpoint("Delete Playlist")
        self._remove_playlist(name)
        if hasattr(self, "undo"):
            self.undo.commit()
        self._flash(_t('Playlist “%s” deleted; tracks remain in the music library.')
                    % name)

    def _remove_playlist(self, name):
        # remove the playlist row + its data, restore the empty placeholder when
        # none remain, and fall back to the Songs view if it was the open one
        try:
            if name not in self._playlists:
                return
            idx = self._playlists.index(name)
            row = self._playlist_rows[idx]
            try:
                self._pl_box.remove(row)
            except Exception:
                pass
            del self._playlists[idx]
            del self._playlist_rows[idx]
            self._playlist_tracks.pop(name, None)
            if not self._playlists:
                try:
                    self._none.set_no_show_all(False)
                    self._none.show()
                except Exception:
                    pass
            self._save()
            if getattr(self, "_current_playlist", None) == name:
                self._current_playlist = None
                self._select("songs")
        except Exception:
            pass

    # ---------------- in-window dialogs (reliable on the no-compositor stack) --
    def _open_dialog(self, card):
        # centre a card over a full-window scrim, drawn inside the app's overlay
        # (no separate popup window). Mirrors nbapp's About/menu approach.
        self._close_dialog()
        try:
            self._close_menu()
        except Exception:
            pass
        alloc = self.get_allocation()
        # size the scrim + centre the card on the LIVE window, falling back to
        # the real primary-monitor size — NEVER a hardcoded 1920x1080. On a
        # smaller panel a 1920x1080 scrim overflows the window and the card,
        # centred on that oversized area, lands off-centre / off-screen.
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_dialog(), True)[1])
        layer.put(scrim, 0, 0)
        holder = Gtk.EventBox()   # own GdkWindow so the card blits on top
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        _min, nat = holder.get_preferred_size()
        cw = nat.width if nat.width > 1 else 360
        ch = nat.height if nat.height > 1 else 160
        layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            hw = holder.get_window()
            if hw is not None:
                hw.raise_()
        except Exception:
            pass
        self._dialog_layer = layer
        return holder

    def _close_dialog(self, *_):
        layer = getattr(self, "_dialog_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._dialog_layer = None
            return True
        return False

    def _prompt_name(self, title, initial, ok_label, on_ok, validate=None):
        # a single-field name prompt (Rename playlist). An optional `validate`
        # callback returns an error string to show inline — keeping the dialog
        # OPEN so the user can correct it — or None to accept, so a blank or
        # clashing name gives a clear reason rather than closing on a no-op.
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("mdlg")
        card.set_size_request(340, -1)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("mdlg-title")
        card.pack_start(t, False, False, 0)
        entry = Gtk.Entry()
        entry.get_style_context().add_class("mdlg-entry")
        entry.set_text(initial or "")
        card.pack_start(entry, False, False, 0)
        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("mdlg-error")
        err.set_line_wrap(True)
        err.set_max_width_chars(40)
        err.set_no_show_all(True)   # only appears once there is something to say
        card.pack_start(err, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("mdlg-btn")
        cancel.connect("clicked", lambda *_: self._close_dialog())
        ok = Gtk.Button(label=ok_label)
        ok.get_style_context().add_class("mdlg-btn")
        # naming a playlist destroys nothing -> the ink primary, not the
        # signage red reserved for the destructive confirm below
        ok.get_style_context().add_class("mdlg-ink")

        def _do(*_a):
            val = entry.get_text()
            if validate is not None:
                try:
                    problem = validate(val)
                except Exception:
                    problem = None
                if problem:
                    err.set_text(problem)
                    err.set_no_show_all(False)
                    err.show()
                    try:
                        entry.grab_focus()
                    except Exception:
                        pass
                    return
            self._close_dialog()
            on_ok(val)
        ok.connect("clicked", _do)
        entry.connect("activate", _do)   # Enter confirms
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        card.pack_start(btns, False, False, 0)
        self._open_dialog(card)
        try:
            entry.grab_focus()
            entry.select_region(0, -1)
        except Exception:
            pass

    def _confirm(self, title, message, ok_label, on_yes):
        # house-style destructive confirmation; the primary action is the one
        # signage red (an alert, per the design language)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("mdlg")
        card.set_size_request(360, -1)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("mdlg-title")
        card.pack_start(t, False, False, 0)
        m = Gtk.Label(label=message, xalign=0)
        m.get_style_context().add_class("mdlg-body")
        m.set_line_wrap(True)
        m.set_max_width_chars(40)
        card.pack_start(m, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("mdlg-btn")
        cancel.connect("clicked", lambda *_: self._close_dialog())
        ok = Gtk.Button(label=ok_label)
        ok.get_style_context().add_class("mdlg-btn")
        ok.get_style_context().add_class("mdlg-primary")
        ok.connect("clicked", lambda *_: (self._close_dialog(), on_yes()))
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        card.pack_start(btns, False, False, 0)
        self._open_dialog(card)
        # land keyboard focus on the SAFE choice for a destructive prompt, so a
        # reflexive Enter/Space cancels rather than deletes
        try:
            cancel.grab_focus()
        except Exception:
            pass

    def _on_key(self, w, ev):
        # Esc dismisses an open dialog first, then clears an active search so a
        # user typing in the search field can back out of the filter WITHOUT
        # closing the whole app (the toplevel key handler would otherwise catch
        # Esc before the entry does and quit to the Finder). Only then does it
        # defer to the base chrome (close the About card / an open menu, quit).
        if ev.keyval == Gdk.KEY_Escape:
            if self._close_dialog():
                return True
            if (self._search is not None and self.get_focus() is self._search
                    and self._search.get_text()):
                self._search.set_text("")
                return True
        if hasattr(self, "undo") and nbapp.undo_keys(self.undo, ev):
            return True
        # Ctrl+F puts the cursor in the search field — with a library of any
        # size, searching is how you find a song, and it was mouse-only.
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            if ev.keyval in (Gdk.KEY_f, Gdk.KEY_F) and self._search is not None:
                try:
                    self._search.grab_focus()
                    self._search.select_region(0, -1)
                except Exception:
                    pass
                return True
        # Space plays/pauses, as it does in every music player — but only when
        # the user is not typing into a text field, where it is a space.
        elif (ev.keyval == Gdk.KEY_space
                and self._menu_open is None
                and getattr(self, "_dialog_layer", None) is None
                and getattr(self, "_about_layer", None) is None
                and not isinstance(self.get_focus(),
                                   (Gtk.Editable, Gtk.TextView))):
            if self._engine_ok():
                self._toggle_play()
            return True
        return super()._on_key(w, ev)

    # ---------------- css ----------------
    def _install_css(self):
        css = b"""
        /* ---- sidebar: a papertone panel with a hairline divider ---- */
        .sidebar { background: #F1EEE6; border-right: 1px solid #C9C4B6;
                   padding: 24px 14px; }
        .sidebar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .sidehead { font-size: 11px; letter-spacing: 0.14em; color: #9A9484;
                    font-weight: 700; padding: 0 12px; margin-bottom: 10px; }
        .viewrow { padding: 9px 12px; border-radius: 6px; font-size: 15px;
                   color: #1A1916; font-weight: 500; margin-bottom: 2px;
                   background: transparent; border: none; box-shadow: none; }
        .viewrow:hover { background: #EFEBE0; }
        /* THE ONE SIGNAGE RED on this window means SELECTED: the view or
           playlist whose tracks the main pane is showing. It is the same 3px
           accent edge Tasks/Academics/Journal/Cookbook/Contacts/Packages use
           for a selected row, so a person who has learned it once reads it
           here. Nothing else on this screen may borrow it: now-playing and an
           engaged shuffle/repeat are INK (see below). */
        .viewrow.active { background: #EAE3D2;
                          box-shadow: inset 3px 0 0 #C8341E; }
        .viewcount { font-size: 13px; color: #9A9484; }
        .empty-mini { padding: 6px 12px; font-size: 13px; color: #9A9484; }
        /* the playlist scroller must be INVISIBLE: the theme paints every
           scrolledwindow/viewport in page paper (#FCFBF8), which inside the
           darker sidebar panel drew a pale rounded slab that read like an empty
           text field. Repaint it in the sidebar's own tone (opaque, never
           transparent: an unpainted viewport renders black here). */
        .plscroll, .plscroll viewport { background-color: #F1EEE6;
                                        border: none; box-shadow: none; }
        .newplaylist { padding: 8px 12px; border-radius: 6px; font-size: 14px;
                       color: #1A1916; background: transparent; border: none;
                       box-shadow: none; }
        .newplaylist:hover { background: #EFEBE0; }
        .sidefoot { font-size: 12px; color: #9A9484; padding: 0 12px; }

        /* ---- main pane ---- */
        .mainpane { background: #FCFBF8; }
        .mainpane * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .mainhead { padding: 30px 36px 18px 36px; }
        .viewtitle { font-size: 24px; font-weight: 700; color: #1A1916; }
        .searchbox { background: #FCFBF8; border: 1px solid #C9C4B6;
                     border-radius: 8px; padding: 0 11px; min-height: 34px; }
        .searchentry { background: transparent; border: none; box-shadow: none;
                       font-size: 13px; color: #1A1916; padding: 0; }
        .colhead { padding: 0 36px; min-height: 34px;
                   border-bottom: 1px solid #D7D2C5; background: transparent; }
        .colhead label { font-size: 11px; letter-spacing: 0.12em; color: #9A9484;
                         font-weight: 700; }
        .empty-title { font-size: 16px; font-weight: 600; color: #6E695E; }
        .empty-desc { font-size: 13px; color: #9A9484; }

        /* ---- populated library list ---- */
        /* the scroll (and any viewport) must paint an OPAQUE page background so
           the list area is never a black rectangle on the no-compositor stack */
        .songscroll, .songscroll viewport { background: #FCFBF8; }
        .songlist { background: transparent; }
        .songlist row { padding: 0; background: transparent; border: none; }
        .songlist row:hover { background: #F4F2EC; }
        /* NOW PLAYING is INK, never the accent: the accent already means
           "selected" in the sidebar of this same window, and an identical red
           edge in two panes made one colour say two things. Ink reads as
           emphasis (the track the transport is driving) and cannot be confused
           with the selection it sits beside. */
        .songlist row.playing { background: #EAE3D2;
                                box-shadow: inset 3px 0 0 #1A1916; }
        .songrow { padding: 11px 36px; border-bottom: 1px solid #EFEBE0; }
        .s-title { font-size: 14px; color: #1A1916; }
        .songlist row.playing .s-title { color: #1A1916; font-weight: 700; }
        .songlist row.playing .s-cell,
        .songlist row.playing .s-time { color: #3A362E; }
        .s-cell { font-size: 13px; color: #6E695E; }
        .s-time { font-size: 13px; color: #9A9484; }
        /* per-row add-to-playlist button - quiet until hovered */
        .addbtn { min-width: 30px; min-height: 30px; padding: 0; border: none;
                  background: transparent; box-shadow: none; border-radius: 50%; }
        .addbtn:hover { background: #F1EEE6; }
        .m-title { font-size: 15px; font-weight: 600; color: #1A1916; }
        .m-sub { font-size: 12px; color: #9A9484; }
        .m-right { font-size: 13px; color: #9A9484; }
        /* drillable album/artist rows: a touch stronger on hover to read as
           tappable (they open a filtered track list) */
        .songlist row.metarow:hover { background: #EFEBE0; }
        .listempty { padding: 40px 12px; font-size: 13px; color: #9A9484; }
        /* Empty-state CTA -> the OS paper-outline create/CTA treatment (matching
           GBA SDK's "Open the example game", Calendar's New Event, Novel's New
           Chapter, Cookbook's New Recipe). Opening the Music folder is a mild
           setup action, not an alert, so it stays on paper; the one signage red
           on this window is reserved for the SELECTED sidebar view/playlist. */
        .openfolder { background: #F8F7F2; border: 1px solid #C9C4B6;
                      border-radius: 8px; color: #2A2620; font-size: 14px;
                      font-weight: 600; padding: 9px 20px; box-shadow: none; }
        .openfolder:hover { background: #F1EEE6; border-color: #C9C4B6; }

        /* ---- playback bar: seated on a hairline, not a black rule ---- */
        .playbar { background: #F4F2EC; border-top: 1px solid #C9C4B6;
                   padding: 0 26px; min-height: 84px; }
        .playbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        /* transport buttons + art frame share the toolbar's own surface
           (#F4F2EC) and border token (#C9C4B6) so nothing on the bar reads as a
           slightly-off swatch (prev/next were a lighter #D7D2C5 border than the
           bar + the big play button) */
        /* These are real buttons now (keyboard-activatable, named for
           assistive tech), so the theme would otherwise stack its own padding,
           minimum size, pressed background IMAGE and shadow on top of the
           38/48px circle and the control would grow and stop being flat. A
           colour-only override loses to the pressed state because that state is
           painted as an image, so `background-image: none` is load-bearing on
           every state, not tidiness. Zeroing the chrome leaves the border, fill
           and radius as the entire appearance. The focus ring is deliberately
           NOT touched: it is what shows Tab has landed on the control. */
        .roundbtn { border: 1px solid #C9C4B6; background: #F4F2EC;
                    background-image: none; box-shadow: none; padding: 0;
                    margin: 0; min-width: 0; min-height: 0;
                    border-radius: 50%; }
        .roundbtn:hover { background: #EAE3D2; border-color: #B3AD9E;
                          background-image: none; box-shadow: none; }
        .roundbtn:active, .roundbtn:checked {
                          background: #EAE3D2; border-color: #B3AD9E;
                          background-image: none; box-shadow: none; }
        /* disabled = the "Media engine unavailable" state: same flat circle on
           a fainter edge, never the theme's shaded button face */
        .roundbtn:disabled { background: #F4F2EC; border-color: #D7D2C5;
                             background-image: none; box-shadow: none; }
        .roundbig { border: 1px solid #C9C4B6; border-radius: 50%; }
        .artwork { border: 1px solid #C9C4B6; background: #EFEBE0; }
        .nowplaying { font-size: 14px; color: #6E695E; }
        .timecode { font-size: 11px; color: #9A9484; }
        /* seek bar: a thin papertone track with an ink fill + small knob,
           matching the volume slider's language (no signage red) */
        .seekbar { padding: 0; }
        .seekbar trough { background: #D7D2C5; min-height: 4px;
                          border-radius: 100px; border: none; }
        .seekbar highlight { background: #1A1916; border-radius: 100px; }
        .seekbar slider { background: #1A1916; border: none;
                          border-radius: 50%; min-width: 12px;
                          min-height: 12px; margin: -5px; }
        .seekbar:disabled trough { background: #EAE3D2; }
        .seekbar:disabled slider { background: #C9C4B6; }
        /* shuffle/repeat sit on the same toolbar face (#F4F2EC) + border
           (#C9C4B6) as the transport buttons; the near-white #FCFBF8 face made
           them read as a different swatch from the bar and the round controls */
        .togglebtn { border: 1px solid #C9C4B6; background: #F4F2EC;
                     border-radius: 8px; min-width: 34px; min-height: 34px;
                     padding: 0; box-shadow: none; }
        .togglebtn:hover { background: #EFEBE0; }
        /* an engaged shuffle/repeat is an ENGAGED CONTROL, not a selection and
           not an alert -> an ink chip. Two solid red chips on the playbar were
           the loudest thing on the window while meaning the least, and made a
           third thing out of the one signage red. */
        .togglebtn:checked { background: #1A1916; border-color: #1A1916; }
        .togglebtn:checked:hover { background: #2A2620; border-color: #2A2620; }
        .volslider trough { background: #D7D2C5; min-height: 4px;
                            border-radius: 100px; border: none; }
        .volslider highlight { background: #1A1916; border-radius: 100px; }
        .volslider slider { background: #1A1916; border: none;
                            border-radius: 50%; min-width: 14px;
                            min-height: 14px; margin: -6px; }

        /* ---- header playlist actions (rename / delete) ---- */
        .plact { min-width: 30px; min-height: 30px; padding: 0; border: none;
                 background: transparent; box-shadow: none; border-radius: 8px; }
        .plact:hover { background: #F1EEE6; }

        /* ---- in-window dialogs (New/Rename/Delete playlist) ---- */
        .mdlg { background: #F8F7F2; border: 1px solid #C9C4B6;
                padding: 24px 28px; }
        .mdlg * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .mdlg-title { font-size: 17px; font-weight: 700; color: #1A1916; }
        .mdlg-body { font-size: 13px; color: #6E695E; }
        /* inline validation note -> the one signage red (an alert) */
        .mdlg-error { font-size: 12px; color: #C8341E; }
        .mdlg-entry { background: #FCFBF8; border: 1px solid #C9C4B6;
                      border-radius: 8px; padding: 7px 10px; font-size: 14px;
                      color: #1A1916; box-shadow: none; }
        .mdlg-entry:focus { border-color: #B3AD9E; }
        .mdlg-btn { min-height: 34px; padding: 0 18px; border-radius: 8px;
                    border: 1px solid #C9C4B6; background: #FCFBF8;
                    color: #1A1916; font-size: 14px; box-shadow: none; }
        .mdlg-btn:hover { background: #F1EEE6; }
        /* a DESTRUCTIVE confirm (Delete playlist) is the one signage red, as
           Papertone legislates for button.destructive-action */
        .mdlg-primary { background: #C8341E; border-color: #C8341E;
                        color: #FCFBF8; font-weight: 600; }
        .mdlg-primary:hover { background: #B12D19; border-color: #B12D19; }
        /* a NON-destructive primary (Create / Rename a playlist) is dark ink,
           exactly as Papertone paints button.suggested-action and as Academics
           and Cookbook paint theirs. Red on a "name your playlist" prompt read
           as a warning about nothing. */
        .mdlg-ink { background: #1A1916; border-color: #1A1916;
                    color: #FCFBF8; font-weight: 600; }
        .mdlg-ink label { color: #FCFBF8; }
        .mdlg-ink:hover { background: #2A2620; border-color: #2A2620; }
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
    nbapp.run(Music)
