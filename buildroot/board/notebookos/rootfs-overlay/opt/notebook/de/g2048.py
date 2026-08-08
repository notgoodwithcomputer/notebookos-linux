#!/usr/bin/env python3
"""
2048 — sliding-tile board (native GTK).

A 4x4 grid: merge equal tiles to reach 2048. Controlled with the arrow keys or
W A S D. Score and best-score readouts, a target-reached / no-moves overlay,
and a New Game control. The board in play, its score and the best score all
persist to $NB_HOME/.config/notebook/g2048.json, so leaving for the Finder and
coming back resumes the same game rather than discarding it.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import json
import os
import random

import nbapp
import nbmotion  # the shared motion engine; degrades to instant headless
from nbi18n import _t  # noqa: E402

# Best score persists across launches in this app's private JSON file, under
# the shared per-app config directory (NB_HOME defaults to /root, as elsewhere).
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STATE_FILE = os.path.join(CFG_DIR, "g2048.json")

# tile value -> (background, foreground)
TILE_COLORS = {
    2: ("#EFE7D5", "#1A1916"), 4: ("#E8DCC0", "#1A1916"),
    8: ("#DCCBA2", "#1A1916"), 16: ("#CBB892", "#1A1916"),
    32: ("#B89E6E", "#FCFBF8"), 64: ("#9E8252", "#FCFBF8"),
    128: ("#8A6D43", "#FCFBF8"), 256: ("#6E5836", "#FCFBF8"),
    512: ("#4A3F2E", "#FCFBF8"), 1024: ("#2A2620", "#FCFBF8"),
    2048: ("#1A1916", "#FCFBF8"),
}
# Board chrome, shared by the CSS block AND the motion overlay's cairo
# painter, so the animated frame and the resting frame are one picture.
# (The same single-source arrangement TILE_COLORS already has with tile_css.)
BOARD_BG = "#BCAE93"
CELL_BG = "#CCBF9F"
SUPER_COLORS = ("#1A1916", "#FCFBF8")     # any tile past 2048
TILE_RADIUS = 4


def _hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0)


def _font_size(v):
    if v < 100:
        return 50
    if v < 1000:
        return 42
    return 32


class Game2048(nbapp.AppWindow):
    app_name = "2048"
    # A game has no documents, so no File menu (the app-name menu already
    # offers About / Close, and Esc / the logo return to the Finder). Every
    # game action lives under Game; there are no display toggles, so no View.
    menus = ("Game",)

    def __init__(self):
        super().__init__()
        self._install_css()

        # Size the board to the REAL panel, never a fixed 1920x1080. On short
        # panels (1366x768, 1280x800) full-size 128px tiles push the footer off
        # the bottom, so the tiles and the game column shrink to fit; a scroller
        # (added below) is the final safety net.
        sw, sh = nbapp.screen_size()
        tile_px = 128 if sh >= 860 else 104
        col_w = 600 if sw >= 720 else max(360, sw - 60)

        self.board = [[0] * 4 for _ in range(4)]
        self.score = 0
        self._quarantine_unrecognized_store()
        self.best = self._load_best()
        self._save_timer = None
        self.status = "play"
        # What each tile label is currently showing, as (text, style class).
        # _refresh compares against this and touches only the cells that
        # actually changed; see the note there.
        self._cell_state = [[None] * 4 for _ in range(4)]
        self._score_shown = None
        self._best_shown = None
        # win overlay must fire only ONCE; "Continue Past 2048" clears the
        # overlay and this flag keeps later moves from re-raising it.
        self._won_shown = False

        # centered game column ---------------------------------------------
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_valign(Gtk.Align.CENTER)
        outer.set_size_request(col_w, -1)

        # --- header ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.set_valign(Gtk.Align.END)
        header.set_margin_bottom(24)

        titlecol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label="2048", xalign=0)
        title.get_style_context().add_class("g-title")
        sub = Gtk.Label(xalign=0)
        sub.set_markup('Merge equal tiles to reach <b>2048</b>.')
        sub.get_style_context().add_class("g-sub")
        sub.set_margin_top(12)
        titlecol.pack_start(title, False, False, 0)
        titlecol.pack_start(sub, False, False, 0)
        header.pack_start(titlecol, True, True, 0)

        scores = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        scores.set_valign(Gtk.Align.END)
        self.score_lbl = self._score_box("Score", scores)
        self.best_lbl = self._score_box("Best", scores)
        header.pack_end(scores, False, False, 0)
        outer.pack_start(header, False, False, 0)

        # --- board (with overlay) ---
        self.overlay = Gtk.Overlay()

        boardbg = Gtk.Box()
        boardbg.get_style_context().add_class("boardbg")
        grid = Gtk.Grid()
        grid.set_row_spacing(14)
        grid.set_column_spacing(14)
        grid.set_row_homogeneous(True)
        grid.set_column_homogeneous(True)

        self.tiles = []
        for r in range(4):
            row = []
            for c in range(4):
                cell = Gtk.Box()
                cell.get_style_context().add_class("cell")
                lbl = Gtk.Label(label="")
                lbl.set_hexpand(True)
                lbl.set_vexpand(True)
                lbl.get_style_context().add_class("tile")
                cell.pack_start(lbl, True, True, 0)
                cell.set_size_request(tile_px, tile_px)
                grid.attach(cell, c, r, 1, 1)
                row.append(lbl)
            self.tiles.append(row)
        boardbg.add(grid)
        self.overlay.add(boardbg)

        # win / lose overlay
        self.ov_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.ov_box.get_style_context().add_class("gameover")
        self.ov_box.set_halign(Gtk.Align.FILL)
        self.ov_box.set_valign(Gtk.Align.FILL)
        self.ov_box.set_hexpand(True)
        self.ov_box.set_vexpand(True)
        self.ov_box.set_no_show_all(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        self.ov_text = Gtk.Label(label="")
        # Wrap + centre so the banner text never overflows a narrow board on a
        # small panel (where the board column is scaled down).
        self.ov_text.set_line_wrap(True)
        self.ov_text.set_justify(Gtk.Justification.CENTER)
        self.ov_text.set_max_width_chars(18)
        self.ov_text.get_style_context().add_class("ov-text")
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btnrow.set_halign(Gtk.Align.CENTER)
        # "Keep Going" resumes play past 2048, so reaching the target does not
        # force the player to throw away a winning board. It is shown only on
        # the win banner (hidden on no-moves, where nothing can continue).
        self.keep_btn = Gtk.Button(label=_t("Continue"))
        self.keep_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.keep_btn.get_style_context().add_class("dark-btn")
        self.keep_btn.set_no_show_all(True)
        self.keep_btn.connect("clicked", lambda *_: self._continue_play())
        again = Gtk.Button(label=_t("New Game"))
        again.set_relief(Gtk.ReliefStyle.NONE)
        again.get_style_context().add_class("dark-btn")
        again.connect("clicked", lambda *_: self.new_game())
        btnrow.pack_start(self.keep_btn, False, False, 0)
        btnrow.pack_start(again, False, False, 0)
        inner.pack_start(self.ov_text, False, False, 0)
        inner.pack_start(btnrow, False, False, 0)
        self.ov_box.pack_start(inner, True, True, 0)
        # Mark the overlay's contents visible now (ov_box itself stays hidden
        # via no-show-all). Without this, ov_box.show() reveals the translucent
        # box but its text/button — never shown — stay invisible: an empty
        # overlay on win / game over.
        inner.show_all()
        self.overlay.add_overlay(self.ov_box)
        self.overlay.set_overlay_pass_through(self.ov_box, False)

        # nbmotion-inventory: content.2048
        # The motion layer: a pass-through DrawingArea covering the board.
        # During the two 90ms phases of a move (slide, then merge/spawn
        # settle) it paints the WHOLE board — wells from a LayerCache,
        # travelling tiles at interpolated positions — and then hides,
        # revealing the identical resting labels beneath. Tiles are never
        # widgets in motion, so nothing here animates an allocation (F2).
        self.anim_layer = Gtk.DrawingArea()
        self.anim_layer.set_no_show_all(True)
        self.anim_layer.connect("draw", self._anim_draw)
        self.anim_layer.connect(
            "size-allocate", lambda *_: setattr(self, "_geom", None))
        self.overlay.add_overlay(self.anim_layer)
        self.overlay.set_overlay_pass_through(self.anim_layer, True)
        self._anim = None            # the one Scalar driving both phases
        self._anim_v = 0.0
        self._anim_phase = None      # None | "slide" | "settle"
        self._anim_data = None
        self._geom = None            # cached (r, c) -> rect in layer coords
        self._wells = (nbmotion.LayerCache(self._draw_wells)
                       if nbmotion is not None else None)

        outer.pack_start(self.overlay, False, False, 0)

        # --- footer ---
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.set_margin_top(22)
        hint = Gtk.Label(xalign=0)
        # Translate the WHOLE sentence, not the runs it is built from. Markup
        # splits this into "Use the", "arrow keys", " or ", " to move tiles." —
        # only the first was ever a catalog key, so a translated system showed
        # half-English gibberish. The full sentence is a key in every catalog.
        hint.set_markup(_t("Use the <b>arrow keys</b> or <b>W A S D</b> "
                           "to move tiles."))
        hint.get_style_context().add_class("g-hint")
        footer.pack_start(hint, True, True, 0)
        newbtn = Gtk.Button(label=_t("New Game"))
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("dark-btn")
        newbtn.connect("clicked", lambda *_: self.new_game())
        footer.pack_end(newbtn, False, False, 0)
        outer.pack_start(footer, False, False, 0)

        # Scroller keeps the whole board reachable if it still can't fit (very
        # short panel); GtkViewport gives `outer` the full visible area when it
        # DOES fit, so the column stays centred. Opaque "gameroot" fill is
        # mandatory — a ScrolledWindow/Viewport with no opaque paint renders
        # black on the no-compositor framebuffer.
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("gameroot")
        scroller.add(outer)
        self.content.pack_start(scroller, True, True, 0)

        self.connect("key-press-event", self._on_game_key)
        # Flush the best score on close so a session's high mark survives even
        # if it was reached without any later save trigger.
        self.connect("destroy", self._on_destroy)
        # Pick up the game that was in play last time rather than throwing it
        # away; only a board that fails validation starts fresh.
        saved = self._load_saved_game()
        if saved:
            self.board, self.score = saved
            self.status = "play"
            self._won_shown = any(v >= 2048 for row in self.board for v in row)
            self._refresh()
        else:
            self.new_game()

    # -- persistence ------------------------------------------------------
    def _quarantine_unrecognized_store(self):
        """Move a store that parses but is NOT this app's shape aside at load,
        once, before any save can replace it.

        A scalar or a list (`5`, `"..."`, `[...]`) holds no board or best we
        can read, so the app opens blank and the close-time save writes that
        blankness over it. The .bak does not save it: a fresh new-game board
        carries two spawned tiles, so it OUTWEIGHS a scalar marker, and on the
        second open _bak_would_shrink judges the blank game the fuller copy and
        refreshes the .bak over the user's bytes — gone on the second
        open+close, no user action. quarantine_unrecognized keeps them at
        <store>.damaged-<stamp> the way journal and calculator do.

        A wrong-shape DICT is deliberately left alone: its unknown keys ride
        through _extra and are written back, so only a non-object is a loss.
        Missing, empty and unparseable stores are handled on the write path
        (preserve_damaged) and are not this method's business — a first run is
        not damage."""
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return
        if not isinstance(data, dict):
            nbapp.quarantine_unrecognized(STATE_FILE)

    def _load_best(self):
        """Return the stored best score, or 0 when the file is missing or
        malformed. Reading must never crash the launch."""
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            known = {"best", "board", "score", "_extra"}
            self._extra = (dict(data.get("_extra"))
                           if isinstance(data.get("_extra"), dict) else {})
            self._extra.update((k, v) for k, v in data.items() if k not in known)
            best = data.get("best")
            if isinstance(best, (int, float)) and not isinstance(best, bool) \
                    and best >= 0:
                return int(best)
        except Exception:
            pass
        if not hasattr(self, "_extra"):
            self._extra = {}
        return 0

    def _load_saved_game(self):
        """The board left mid-play last time, as (board, score), or None.

        This OS runs one app at a time, so opening the Finder for a moment used
        to throw away a game in progress with no warning. Anything that isn't a
        clean 4x4 of powers of two is ignored, so a hand-edited or truncated
        file just starts a fresh game."""
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            board = data.get("board")
            score = data.get("score")
            if not (isinstance(board, list) and len(board) == 4):
                return None
            for row in board:
                if not (isinstance(row, list) and len(row) == 4):
                    return None
                for v in row:
                    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                        return None
                    if v and (v < 2 or v & (v - 1)):
                        return None
            if not any(v for row in board for v in row):
                return None            # an empty board is not a game
            if isinstance(score, bool) or not isinstance(score, int) \
                    or score < 0:
                return None
            return [list(r) for r in board], score
        except Exception:
            return None

    def _save_best(self):
        """Write the best score and the board in play to this app's private
        JSON file. Never crash on I/O — a read-only or missing config dir just
        skips the save."""
        try:
            state = dict(getattr(self, "_extra", {}) or {})
            state["best"] = self.best
            # A finished game is not worth resuming into its own end banner.
            if self.status == "play":
                state["board"] = self.board
                state["score"] = self.score
            state["_extra"] = getattr(self, "_extra", {})
            nbapp.atomic_write_json(STATE_FILE, state)
        except Exception as exc:
            nbapp.save_failure_reason = str(exc)

    def _queue_save(self):
        """Persist shortly after a move. Debounced, so mashing the arrow keys
        costs one write a second rather than an fsync per keypress."""
        if self._save_timer is not None:
            return
        self._save_timer = GLib.timeout_add(1200, self._flush_save)

    def _flush_save(self):
        self._save_timer = None
        self._save_best()
        return False

    def _on_destroy(self, *_):
        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
            self._save_timer = None
        self._save_best()
        return False

    # -- widgets ----------------------------------------------------------
    def _score_box(self, name, parent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("scorebox")
        cap = Gtk.Label(label=name.upper())
        cap.get_style_context().add_class("score-cap")
        val = Gtk.Label(label="0")
        val.get_style_context().add_class("score-val")
        box.pack_start(cap, False, False, 0)
        box.pack_start(val, False, False, 0)
        parent.pack_start(box, False, False, 0)
        return val

    # -- game logic -------------------------------------------------------
    def _empty_cells(self):
        return [(r, c) for r in range(4) for c in range(4)
                if not self.board[r][c]]

    def _add_random(self):
        """Place a spawn tile; returns (r, c, value) so the motion overlay
        can grow it in, or None when the board is full."""
        cells = self._empty_cells()
        if not cells:
            return None
        r, c = random.choice(cells)
        v = 2 if random.random() < 0.9 else 4
        self.board[r][c] = v
        return (r, c, v)

    def new_game(self):
        self._finish_anim_now()      # a fresh board owes nothing to the old
        if getattr(self, "board", None) and any(v for row in self.board for v in row):
            self._new_game_undo = ([list(row) for row in self.board], self.score,
                                   self.status, self._won_shown)
        self.board = [[0] * 4 for _ in range(4)]
        self.score = 0
        self.status = "play"
        self._won_shown = False  # allow a fresh game to raise the win once
        self._add_random()
        self._add_random()
        self._refresh()
        self._save_best()

    def undo_new_game(self):
        """Restore the game replaced by New Game, once, without a warning card."""
        saved = getattr(self, "_new_game_undo", None)
        if not saved:
            return False
        self._finish_anim_now()
        self.board, self.score, self.status, self._won_shown = saved
        self._new_game_undo = None
        self._refresh()
        self._save_best()
        return True

    @staticmethod
    def _slide(line):
        arr = [v for v in line if v]
        res = []
        gained = 0
        i = 0
        while i < len(arr):
            if i + 1 < len(arr) and arr[i] == arr[i + 1]:
                res.append(arr[i] * 2)
                gained += arr[i] * 2
                i += 2
            else:
                res.append(arr[i])
                i += 1
        while len(res) < 4:
            res.append(0)
        return res, gained

    @staticmethod
    def _slide_traced(line):
        """_slide plus provenance — the data the motion overlay draws.

        Returns (res, gained, journeys, merges): `journeys` holds one
        (src_slot, dst_slot, shown_value) per nonzero source, where
        shown_value is what the TRAVELLING tile displays (its pre-merge
        value — two 2s travel as 2s and become a 4 on arrival); `merges`
        lists the slots where a doubled tile forms. The suite asserts this
        agrees with _slide on res and gained for every line, so the game's
        arithmetic cannot drift from what the animation shows."""
        src = [(j, v) for j, v in enumerate(line) if v]
        res, journeys, merges = [], [], []
        gained = 0
        i = 0
        while i < len(src):
            j, v = src[i]
            dst = len(res)
            if i + 1 < len(src) and src[i + 1][1] == v:
                res.append(v * 2)
                gained += v * 2
                journeys.append((j, dst, v))
                journeys.append((src[i + 1][0], dst, v))
                merges.append(dst)
                i += 2
            else:
                res.append(v)
                journeys.append((j, dst, v))
                i += 1
        while len(res) < 4:
            res.append(0)
        return res, gained, journeys, merges

    @staticmethod
    def _rc(direction, i, j):
        if direction == "left":
            return i, j
        if direction == "right":
            return i, 3 - j
        if direction == "up":
            return j, i
        return 3 - j, i  # down

    def move(self, direction):
        # Block tile moves while the win banner is up (status "win") so keys
        # don't silently mutate the board behind the overlay; "Continue Past
        # 2048" sets status back to "play" and re-enables moving.
        if self.status in ("lose", "win"):
            return
        # A key during the ~180ms of tile motion is a real move, not noise:
        # the previous journey lands instantly and the new one begins from
        # the resting board (the retarget rule, at the game's granularity).
        self._finish_anim_now()
        moved = False
        gained = 0
        journeys, statics, merges = [], [], []
        for i in range(4):
            idx = [self._rc(direction, i, j) for j in range(4)]
            line = [self.board[r][c] for (r, c) in idx]
            res, g, tj, tm = self._slide_traced(line)
            gained += g
            for (sj, dj, v) in tj:
                if sj != dj:
                    journeys.append((v, idx[sj], idx[dj]))
                else:
                    statics.append((v, idx[sj]))
            for dj in tm:
                merges.append(idx[dj])
            for j in range(4):
                r, c = idx[j]
                if self.board[r][c] != res[j]:
                    moved = True
                self.board[r][c] = res[j]
        if not moved:
            return
        spawn = self._add_random()
        self.score += gained
        # A new high mark is persisted immediately, so it survives a crash or
        # power loss without waiting for a clean close.
        if self.score > self.best:
            self.best = self.score
            self._save_best()
        self._queue_save()
        # Trigger the win overlay only the first time 2048 appears. Without the
        # _won_shown gate, every move with a 2048 tile still on the board would
        # re-set "win", defeating the "Continue Past 2048" View action.
        if self.status == "play" and not self._won_shown and any(
                v == 2048 for row in self.board for v in row):
            self.status = "win"
            self._won_shown = True
        if not self._can_move():
            self.status = "lose"
        # Labels take the final state NOW, under the cover of the motion
        # overlay; when the journey lands, hiding the overlay reveals an
        # identical resting frame. Instant paths never show the overlay.
        self._refresh()
        self._animate_move(journeys, statics, merges, spawn)

    def _can_move(self):
        for r in range(4):
            for c in range(4):
                if not self.board[r][c]:
                    return True
                if c < 3 and self.board[r][c] == self.board[r][c + 1]:
                    return True
                if r < 3 and self.board[r][c] == self.board[r + 1][c]:
                    return True
        return False

    # -- motion (PAPER-PHYSICS G4: content.2048, the reference) -----------
    # Two 90ms FEEDBACK phases, each a damped arrival: the SLIDE carries
    # every travelling tile to its destination on one shared clock, then the
    # SETTLE grows the spawn in and marks each merge with a single downward
    # scale settle (1.06 -> 1.0 - one direction, no bounce, Article H).
    def _animate_move(self, journeys, statics, merges, spawn):
        if nbmotion is None or self._wells is None:
            return                     # resting labels are already final
        self._anim_data = {"journeys": journeys, "statics": statics,
                           "merges": set(merges),
                           "spawn": spawn}
        if self._anim is None:
            self._anim = nbmotion.Scalar(
                widget=self.anim_layer, value=0.0,
                on_frame=self._anim_frame,
                duration=nbmotion.FEEDBACK, easing=nbmotion.ARRIVE)
        self._anim_phase = "slide"
        self._anim_v = 0.0
        self.anim_layer.show()
        self._anim.jump_to(0.0)
        self._anim.animate_to(1.0, on_done=self._begin_settle)

    def _anim_frame(self, v):
        self._anim_v = v
        # The layer covers exactly the board: invalidating it IS the
        # smallest-changed-rectangle rule at this animation's scale (F1) —
        # the window around it is never repainted.
        self.anim_layer.queue_draw()

    def _begin_settle(self, completed):
        if not completed or self._anim_phase != "slide":
            return
        self._anim_phase = "settle"
        self._anim_v = 0.0
        self._anim.jump_to(0.0)
        self._anim.animate_to(1.0, on_done=self._anim_end)

    def _anim_end(self, _completed):
        self._anim_phase = None
        self._anim_data = None
        self.anim_layer.hide()

    def _finish_anim_now(self):
        """Land any in-flight journey instantly: the labels beneath already
        show the final board, so hiding the overlay IS the end state."""
        if self._anim is not None:
            self._anim.cancel()
        self._anim_phase = None
        self._anim_data = None
        if self.anim_layer is not None:
            self.anim_layer.hide()

    def _ensure_geom(self):
        if self._geom is not None:
            return self._geom
        geom = {}
        for r in range(4):
            for c in range(4):
                lbl = self.tiles[r][c]
                at = lbl.translate_coordinates(self.overlay, 0, 0)
                if at is None:
                    return None        # not realized yet: skip this frame
                alloc = lbl.get_allocation()
                geom[(r, c)] = (at[0], at[1], alloc.width, alloc.height)
        self._geom = geom
        return geom

    @staticmethod
    def _rounded(cr, x, y, w, h, rad):
        import math
        cr.new_sub_path()
        cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
        cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
        cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
        cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw_wells(self, cr, w, h):
        cr.set_source_rgb(*_hex_rgb(BOARD_BG))
        self._rounded(cr, 0, 0, w, h, TILE_RADIUS)
        cr.fill()
        geom = self._ensure_geom()
        if geom is None:
            return
        cr.set_source_rgb(*_hex_rgb(CELL_BG))
        for rect in geom.values():
            self._rounded(cr, rect[0], rect[1], rect[2], rect[3], TILE_RADIUS)
            cr.fill()

    def _paint_tile(self, cr, rect, value, scale=1.0):
        x, y, w, h = rect
        if scale != 1.0:
            dx, dy = w * (1.0 - scale) / 2.0, h * (1.0 - scale) / 2.0
            x, y, w, h = x + dx, y + dy, w * scale, h * scale
        bg, fg = TILE_COLORS.get(value, SUPER_COLORS)
        cr.set_source_rgb(*_hex_rgb(bg))
        self._rounded(cr, x, y, w, h, TILE_RADIUS)
        cr.fill()
        from gi.repository import Pango, PangoCairo
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription("Nimbus Sans Bold")
        # Absolute (device-pixel) size, matching the CSS block's px sizes —
        # a point size here would re-introduce the DPI dependence the tile
        # CSS deliberately avoids.
        desc.set_absolute_size(_font_size(value) * scale * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_text(str(value), -1)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgb(*_hex_rgb(fg))
        cr.move_to(x + (w - tw) / 2.0, y + (h - th) / 2.0)
        PangoCairo.show_layout(cr, layout)

    def _anim_draw(self, _area, cr):
        data, phase = self._anim_data, self._anim_phase
        if data is None or phase is None:
            return False
        geom = self._ensure_geom()
        if geom is None:
            return False
        if self._wells is not None:
            self._wells.paint(self.anim_layer, cr)
        v = self._anim_v
        if phase == "slide":
            for (val, cell) in data["statics"]:
                self._paint_tile(cr, geom[cell], val)
            for (val, src, dst) in data["journeys"]:
                a, b = geom[src], geom[dst]
                rect = (a[0] + (b[0] - a[0]) * v, a[1] + (b[1] - a[1]) * v,
                        a[2], a[3])
                self._paint_tile(cr, rect, val)
        else:
            spawn = data["spawn"]
            for r in range(4):
                for c in range(4):
                    val = self.board[r][c]
                    if not val:
                        continue
                    if spawn is not None and (r, c) == spawn[:2]:
                        if v > 0.0:
                            self._paint_tile(cr, geom[(r, c)], val, scale=v)
                    elif (r, c) in data["merges"]:
                        self._paint_tile(cr, geom[(r, c)], val,
                                         scale=1.06 - 0.06 * v)
                    else:
                        self._paint_tile(cr, geom[(r, c)], val)
        return True

    # -- menus ------------------------------------------------------------
    def menu_items(self, name):
        if name == "Game":
            # Directional moves only do something while play is live, so they
            # are greyed (never dead) behind the win / no-moves overlay.
            # "Continue Past 2048" mirrors the win banner's Keep Going button
            # and is live only on a win. Reset Best Score wipes the persisted
            # record, so it always confirms — and is itself greyed when there
            # is no record to clear.
            playing = self.status == "play"
            return [
                ("New Game", self.new_game),
                # Compose the undo label from the OS-wide "Undo %s" pattern and
                # the action's own already-translated name, exactly as
                # nbapp.UndoHistory does for the editor apps — so this custom
                # one-level undo needs no new catalog keys and reads right in
                # all 17 languages.
                (_t("Undo %s") % _t("New Game"), self.undo_new_game
                 if getattr(self, "_new_game_undo", None) else None),
                nbapp.SEP,
                ("Move Up", (lambda: self.move("up")) if playing else None),
                ("Move Down", (lambda: self.move("down")) if playing else None),
                ("Move Left", (lambda: self.move("left")) if playing else None),
                ("Move Right", (lambda: self.move("right")) if playing else None),
                nbapp.SEP,
                ("Continue Past 2048",
                 self._continue_play if self.status == "win" else None),
                nbapp.SEP,
                ("Reset Best Score…",
                 self._reset_best if self.best > 0 else None),
                (_t("Undo %s") % _t("Reset Best Score"), self.undo_reset_best
                 if getattr(self, "_best_undo", None) else None),
            ]
        return super().menu_items(name)

    def _reset_best(self):
        if self.best <= 0:
            return
        self._do_reset_best()

    def _do_reset_best(self):
        # Persist the cleared value too, or the old best would return on the
        # next launch and the reset would look like it did nothing.
        self._best_undo = self.best
        self.best = 0
        self._save_best()
        self._refresh()

    def undo_reset_best(self):
        if not getattr(self, "_best_undo", None):
            return False
        self.best, self._best_undo = self._best_undo, None
        self._save_best()
        self._refresh()
        return True

    def _confirm(self, title, message, ok_label, on_yes):
        """Modal confirmation for a destructive action. Runs `on_yes` only when
        the primary button is pressed; crash-safe if the dialog can't build."""
        try:
            dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
            dlg.set_decorated(False)
            dlg.get_style_context().add_class("g2dlg")
            area = dlg.get_content_area()
            area.set_spacing(0)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.get_style_context().add_class("g2dlgbox")
            hd = Gtk.Label(label=title, xalign=0)
            hd.get_style_context().add_class("g2dlgtitle")
            msg = Gtk.Label(label=message, xalign=0)
            msg.set_line_wrap(True)
            msg.set_max_width_chars(40)
            msg.get_style_context().add_class("g2dlgmsg")
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            btns.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.get_style_context().add_class("g2dlgcancel")
            ok = Gtk.Button(label=ok_label)
            ok.get_style_context().add_class("g2dlgok")
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
            # Focus the safe default so a stray Space / Return cancels, not
            # resets the best score.
            cancel.grab_focus()
        except Exception:
            pass

    def _continue_play(self):
        # after winning, dismiss the overlay and keep playing past 2048
        if getattr(self, "status", "play") == "win":
            self.status = "play"
            self._refresh()

    def _refresh(self):
        # Repaint only the cells that changed. _refresh runs on every keypress,
        # and setting a label's text or swapping its style class invalidates
        # that widget's style and queues a resize of the whole board — so the
        # old unconditional pass paid 16 tile restyles per move even though a
        # move changes at most a handful of cells. Held arrow keys repeat at
        # the keyboard's rate, and on this software-rendered, compositor-less
        # stack that wasted relayout is what makes the board feel like it lags
        # behind the key. _cell_state mirrors what each label is showing, and
        # nothing outside this method touches a tile's text or "t-" classes.
        for r in range(4):
            for c in range(4):
                v = self.board[r][c]
                cls = None
                if v:
                    cls = "t-%d" % v if v in TILE_COLORS else "t-super"
                want = (str(v) if v else "", cls)
                if self._cell_state[r][c] == want:
                    continue
                self._cell_state[r][c] = want
                lbl = self.tiles[r][c]
                ctx = lbl.get_style_context()
                for old in list(ctx.list_classes()):
                    if old.startswith("t-"):
                        ctx.remove_class(old)
                lbl.set_text(want[0])
                if cls:
                    ctx.add_class(cls)
        score = "{:,}".format(self.score)
        if score != self._score_shown:
            self._score_shown = score
            self.score_lbl.set_text(score)
        best = "{:,}".format(self.best)
        if best != self._best_shown:
            self._best_shown = best
            self.best_lbl.set_text(best)
        if self.status == "play":
            self.ov_box.hide()
        elif self.status == "win":
            # the player's moment, phrased as one: "Target 2048 reached" read
            # like a status readout, and mirrors the goal line in the header
            self.ov_text.set_text(_t("2048 reached"))
            self.keep_btn.show()   # offer to keep playing past 2048
            self.ov_box.show()
        else:
            self.ov_text.set_text(_t("No moves left"))
            self.keep_btn.hide()   # nothing left to continue
            self.ov_box.show()

    _KEYMAP = {
        Gdk.KEY_Left: "left", Gdk.KEY_Right: "right",
        Gdk.KEY_Up: "up", Gdk.KEY_Down: "down",
        Gdk.KEY_a: "left", Gdk.KEY_d: "right",
        Gdk.KEY_w: "up", Gdk.KEY_s: "down",
        Gdk.KEY_A: "left", Gdk.KEY_D: "right",
        Gdk.KEY_W: "up", Gdk.KEY_S: "down",
    }

    def _on_game_key(self, _w, ev):
        # While a dropdown menu or the About card is open, let the base window
        # own the keyboard (Esc closes the overlay) instead of sliding tiles
        # invisibly behind the popup.
        if self._menu_open is not None or getattr(self, "_about_layer", None):
            return False
        direction = self._KEYMAP.get(ev.keyval)
        if direction:
            self.move(direction)
            return True
        return False

    # -- style ------------------------------------------------------------
    def _install_css(self):
        tile_css = ""
        for v, (bg, fg) in TILE_COLORS.items():
            tile_css += (
                ".tile.t-%d { background:%s; color:%s; font-size:%dpx; }\n"
                % (v, bg, fg, _font_size(v)))
        css = ("""
        /* Opaque fill on the game surface AND the scroller's viewport — a
           transparent viewport paints black without a compositor. */
        .gameroot, .gameroot viewport { background: #DED4C2; }
        /* one sans face for the whole game surface (chrome + tiles) */
        .gameroot * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .g-title { font-size: 60px; font-weight: 700; color: #1A1916;
                   letter-spacing: -2px; }
        .g-sub { font-size: 14px; color: #6E695E; }
        .g-hint { font-size: 13px; color: #6E695E; }
        .scorebox { background: #BCAE93; border-radius: 4px;
                    padding: 9px 18px; min-width: 88px; }
        .score-cap { font-size: 11px; letter-spacing: 1px; color: #EFEBE0;
                     font-weight: 700; }
        .score-val { font-size: 24px; font-weight: 700; color: #FCFBF8; }
        .boardbg { background: %(board_bg)s; border-radius: 4px; padding: 14px; }
        .cell { background: %(cell_bg)s; border-radius: 4px; }
        .tile { border-radius: 4px; font-weight: 700;
                background: transparent; }
        .tile.t-super { background: #1A1916; color: #FCFBF8; font-size: 32px; }
        /* The `label` selector is NOT redundant: a colour set on the button
           node alone never reaches the label inside it, because the theme's
           universal `* { color: ink }` matches that label node directly and
           beats the inherited value. Without it every New Game / Keep Going
           button rendered as a black slab with ink text -- an unreadable,
           unlabelled rectangle, including the two on the win banner. */
        .dark-btn { background: #1A1916; border: none;
                    box-shadow: none; border-radius: 8px; font-size: 14px;
                    font-weight: 600; padding: 11px 22px; }
        .dark-btn, .dark-btn label { color: #FCFBF8; }
        .dark-btn:hover { background: #3A362E; }
        .gameover { background: rgba(222,212,194,0.85); border-radius: 4px; }
        .ov-text { font-size: 44px; font-weight: 700; color: #1A1916;
                   letter-spacing: -1px; }
        /* confirm dialog for destructive actions (paper card, darker-beige
           border; signage-red only on the destructive primary button) */
        .g2dlg { background: #FCFBF8; border: 1px solid #C9C4B6; }
        .g2dlgbox { padding: 24px 28px 20px; }
        .g2dlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .g2dlgtitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .g2dlgmsg { font-size: 13px; color: #6E695E; }
        .g2dlgcancel { font-size: 13px; color: #1A1916; padding: 6px 16px;
                       background: #FCFBF8; border: 1px solid #C9C4B6;
                       border-radius: 8px; box-shadow: none; }
        .g2dlgcancel:hover { background: #F1EEE6; }
        .g2dlgok { font-size: 13px; padding: 6px 16px;
                   background: #C8341E; border: 1px solid #C8341E;
                   border-radius: 8px; box-shadow: none; }
        .g2dlgok, .g2dlgok label { color: #FCFBF8; }   /* see .dark-btn */
        .g2dlgok:hover { background: #B12D19; border-color: #B12D19; }
        """ % {"board_bg": BOARD_BG, "cell_bg": CELL_BG} + tile_css
        ).encode("utf-8")
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # Styling is cosmetic: a CSS parse error or a missing default
            # screen must not stop the app window from constructing.
            pass


if __name__ == "__main__":
    nbapp.run(Game2048)
